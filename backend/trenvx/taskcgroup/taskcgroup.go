// Package taskcgroup tracks tool descendants, not arbitrary VM process state.
package taskcgroup

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// Default is configured once before envd accepts tools. A nil manager preserves
// the upstream launch path; checkpoint requests must reject that disabled mode.
var Default *Manager

type filesystem interface {
	Mkdir(string, os.FileMode) error
	Open(string) (*os.File, error)
	ReadFile(string) ([]byte, error)
	WriteFile(string, []byte, os.FileMode) error
	Remove(string) error
}
type realFS struct{}

func (realFS) Mkdir(p string, m os.FileMode) error               { return os.Mkdir(p, m) }
func (realFS) Open(p string) (*os.File, error)                   { return os.Open(p) }
func (realFS) ReadFile(p string) ([]byte, error)                 { return os.ReadFile(p) }
func (realFS) WriteFile(p string, b []byte, m os.FileMode) error { return os.WriteFile(p, b, m) }
func (realFS) Remove(p string) error                             { return os.Remove(p) }

type group struct {
	id  string
	dir *os.File
	pid int
}
type Task struct {
	ID     string `json:"id"`
	PID    int    `json:"pid"`
	Events string `json:"events"`
}
type Manager struct {
	mu     sync.Mutex
	root   string
	groups map[string]*group
	token  string
	frozen bool
	fs     filesystem
}

func randomID() (string, error) {
	b := make([]byte, 16)
	if _, e := rand.Read(b); e != nil {
		return "", e
	}
	return hex.EncodeToString(b), nil
}

// New requires an existing delegated cgroup v2 parent. It never adopts old groups.
func New(parent string) (*Manager, error) {
	if !filepath.IsAbs(parent) {
		return nil, errors.New("cgroup parent must be absolute")
	}
	var st syscall.Statfs_t
	if e := syscall.Statfs(parent, &st); e != nil {
		return nil, e
	}
	if st.Type != 0x63677270 {
		return nil, errors.New("parent is not cgroup v2")
	}
	id, e := randomID()
	if e != nil {
		return nil, e
	}
	root := filepath.Join(parent, "asb-"+id)
	if e = os.Mkdir(root, 0700); e != nil {
		return nil, e
	}
	return &Manager{root: root, groups: map[string]*group{}, fs: realFS{}}, nil
}
func (m *Manager) path(g *group, name string) string { return filepath.Join(m.root, g.id, name) }

// An open directory FD pins ownership against deletion/recreation at the same path.
func (m *Manager) owned(g *group) error {
	a, e := g.dir.Stat()
	if e != nil {
		return e
	}
	f, e := m.fs.Open(m.path(g, "."))
	if e != nil {
		return e
	}
	defer f.Close()
	b, e := f.Stat()
	if e != nil {
		return e
	}
	if !os.SameFile(a, b) {
		return errors.New("cgroup identity changed")
	}
	return nil
}
func (m *Manager) write(g *group, name, value string) error {
	if e := m.owned(g); e != nil {
		return e
	}
	return m.fs.WriteFile(m.path(g, name), []byte(value), 0600)
}
func (m *Manager) wait(ctx context.Context, g *group, key, value string) error {
	for {
		if e := ctx.Err(); e != nil {
			return e
		}
		if e := m.owned(g); e != nil {
			return e
		}
		b, e := m.fs.ReadFile(m.path(g, "cgroup.events"))
		if e != nil {
			return e
		}
		if hasEvent(string(b), key, value) {
			return nil
		}
		t := time.NewTimer(5 * time.Millisecond)
		select {
		case <-ctx.Done():
			t.Stop()
			return ctx.Err()
		case <-t.C:
		}
	}
}
func hasEvent(s, key, value string) bool {
	for _, line := range splitLines(s) {
		if line == key+" "+value {
			return true
		}
	}
	return false
}

// Launch creates one task and atomically places the child in it before exec,
// including before credentials, shell startup files, or user code can run.
// start may be cmd.Start or PTY StartWithSize. There is no PID migration fallback.
func (m *Manager) Launch(cmd *exec.Cmd, start func() error) (string, error) {
	if m == nil {
		return "", start()
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token != "" {
		return "", errors.New("checkpoint blocks new tool launches")
	}
	id, e := randomID()
	if e != nil {
		return "", e
	}
	p := filepath.Join(m.root, id)
	if e = m.fs.Mkdir(p, 0700); e != nil {
		return "", e
	}
	dir, e := m.fs.Open(p)
	if e != nil {
		return "", errors.Join(e, m.fs.Remove(p))
	}
	g := &group{id: id, dir: dir}
	m.groups[id] = g
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	oldUse, oldFD := cmd.SysProcAttr.UseCgroupFD, cmd.SysProcAttr.CgroupFD
	cmd.SysProcAttr.UseCgroupFD = true
	cmd.SysProcAttr.CgroupFD = int(dir.Fd())
	e = start()
	cmd.SysProcAttr.UseCgroupFD = oldUse
	cmd.SysProcAttr.CgroupFD = oldFD
	if e != nil {
		return "", errors.Join(e, m.cleanupLocked(context.Background(), g))
	}
	g.pid = cmd.Process.Pid
	return id, nil
}
func Start(cmd *exec.Cmd) error { _, e := Default.Launch(cmd, cmd.Start); return e }

func validToken(token string) bool { b, e := hex.DecodeString(token); return e == nil && len(b) == 16 }

// Freeze closes admission and waits for every retained task, including tasks whose
// shell has exited. Failed or canceled acquisition always attempts thaw itself.
func (m *Manager) Freeze(ctx context.Context, token string) (err error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !validToken(token) {
		return errors.New("token must be 32 hex characters")
	}
	if m.token != "" {
		if m.token == token && m.frozen {
			return nil
		}
		return errors.New("another checkpoint owns the freeze")
	}
	m.token = token
	defer func() {
		if err != nil {
			err = errors.Join(err, m.thawLocked())
		}
	}()
	for _, g := range m.groups {
		if err = m.write(g, "cgroup.freeze", "1"); err != nil {
			return err
		}
	}
	for _, g := range m.groups {
		if err = m.wait(ctx, g, "frozen", "1"); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err == nil {
		m.frozen = true
	}
	return err
}
func (m *Manager) thawLocked() error {
	m.frozen = false
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	var errs []error
	for _, g := range m.groups {
		e := m.write(g, "cgroup.freeze", "0")
		if e == nil {
			e = m.wait(ctx, g, "frozen", "0")
		}
		errs = append(errs, e)
	}
	e := errors.Join(errs...)
	if e == nil {
		m.token = ""
	}
	return e
}
func (m *Manager) Thaw(token string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token == "" {
		return nil
	}
	if m.token != token {
		return errors.New("freeze ownership mismatch")
	}
	return m.thawLocked()
}
func (m *Manager) Tasks() ([]Task, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Task{}
	for _, g := range m.groups {
		if e := m.owned(g); e != nil {
			return nil, e
		}
		b, e := m.fs.ReadFile(m.path(g, "cgroup.events"))
		if e != nil {
			return nil, e
		}
		out = append(out, Task{g.id, g.pid, string(b)})
	}
	return out, nil
}
func (m *Manager) cleanupLocked(ctx context.Context, g *group) error {
	// cgroup.kill is atomic for descendants. Refuse unsupported kernels rather
	// than signal remembered PIDs, which may have been reused outside our task.
	if e := m.write(g, "cgroup.freeze", "0"); e != nil {
		return e
	}
	if e := m.write(g, "cgroup.kill", "1"); e != nil {
		return e
	}
	bounded, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	if e := m.wait(bounded, g, "populated", "0"); e != nil {
		return e
	}
	if e := m.owned(g); e != nil {
		return e
	}
	if e := m.fs.Remove(m.path(g, ".")); e != nil {
		return e
	}
	delete(m.groups, g.id)
	return g.dir.Close()
}

// Cleanup accepts only IDs created by this manager, never filesystem discovery.
func (m *Manager) Cleanup(ctx context.Context, id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.token != "" {
		return errors.New("thaw before cleanup")
	}
	g, ok := m.groups[id]
	if !ok {
		return fmt.Errorf("task %q is not owned", id)
	}
	return m.cleanupLocked(ctx, g)
}
