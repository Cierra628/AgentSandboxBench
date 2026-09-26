package taskcgroup

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fakeFS struct {
	realFS
	neverFrozen, failFreeze, failThaw bool
	writes                            []string
}

func (f *fakeFS) Mkdir(p string, m os.FileMode) error {
	if e := os.Mkdir(p, m); e != nil {
		return e
	}
	return os.WriteFile(filepath.Join(p, "cgroup.events"), []byte("populated 0\nfrozen 0\n"), 0600)
}
func (f *fakeFS) WriteFile(p string, b []byte, m os.FileMode) error {
	name, value := filepath.Base(p), string(b)
	f.writes = append(f.writes, p+"="+value)
	if name == "cgroup.freeze" {
		if value == "1" && f.failFreeze || value == "0" && f.failThaw {
			return errors.New("injected freezer IO error")
		}
		if value == "1" && f.neverFrozen {
			return nil
		}
		return os.WriteFile(filepath.Join(filepath.Dir(p), "cgroup.events"), []byte("populated 0\nfrozen "+value+"\n"), 0600)
	}
	return nil
}
func (f *fakeFS) Remove(p string) error {
	if e := os.Remove(filepath.Join(p, "cgroup.events")); e != nil {
		return e
	}
	return os.Remove(p)
}
func fixture(t *testing.T) (*Manager, *fakeFS, string) {
	t.Helper()
	f := &fakeFS{}
	m := &Manager{root: t.TempDir(), groups: map[string]*group{}, fs: f}
	c := exec.Command("true")
	id, e := m.Launch(c, func() error {
		if !c.SysProcAttr.UseCgroupFD {
			t.Fatal("launch outside cgroup")
		}
		c.Process, _ = os.FindProcess(os.Getpid())
		return nil
	})
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		for _, g := range m.groups {
			g.dir.Close()
		}
	})
	return m, f, id
}

const token = "11111111111111111111111111111111"

func TestFreezeTimeoutThawsAndReadmits(t *testing.T) {
	m, f, _ := fixture(t)
	f.neverFrozen = true
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	if !errors.Is(m.Freeze(ctx, token), context.DeadlineExceeded) {
		t.Fatal("expected deadline")
	}
	if m.token != "" || !strings.HasSuffix(f.writes[len(f.writes)-1], "=0") {
		t.Fatal("failure left task frozen")
	}
}
func TestFreezeWriteFailureThaws(t *testing.T) {
	m, f, _ := fixture(t)
	f.failFreeze = true
	if m.Freeze(context.Background(), token) == nil {
		t.Fatal("expected IO error")
	}
	if m.token != "" {
		t.Fatal("admission remains closed")
	}
}
func TestThawFailureReportedAndBlocksAdmission(t *testing.T) {
	m, f, _ := fixture(t)
	if e := m.Freeze(context.Background(), token); e != nil {
		t.Fatal(e)
	}
	f.failThaw = true
	if m.Thaw(token) == nil || m.token == "" {
		t.Fatal("lost thaw failure")
	}
	f.failThaw = false
	if e := m.Thaw(token); e != nil {
		t.Fatal(e)
	}
}
func TestFreezeOwnershipAndLaunchGate(t *testing.T) {
	m, _, id := fixture(t)
	if e := m.Freeze(context.Background(), token); e != nil {
		t.Fatal(e)
	}
	called := false
	_, e := m.Launch(exec.Command("true"), func() error { called = true; return nil })
	if e == nil || called {
		t.Fatal("launch admitted during checkpoint")
	}
	if m.Thaw("22222222222222222222222222222222") == nil {
		t.Fatal("foreign thaw")
	}
	if m.Cleanup(context.Background(), id) == nil {
		t.Fatal("cleanup during freeze")
	}
	if e := m.Thaw(token); e != nil {
		t.Fatal(e)
	}
	if e := m.Thaw(token); e != nil {
		t.Fatal(e)
	}
}
func TestCleanupOnlyOwnedGroup(t *testing.T) {
	m, f, id := fixture(t)
	before := len(f.writes)
	if m.Cleanup(context.Background(), "../foreign") == nil || len(f.writes) != before {
		t.Fatal("foreign cleanup touched filesystem")
	}
	if e := m.Cleanup(context.Background(), id); e != nil {
		t.Fatal(e)
	}
	if len(m.groups) != 0 {
		t.Fatal("not removed")
	}
}
func TestChangedIdentityRefused(t *testing.T) {
	m, _, id := fixture(t)
	p := filepath.Join(m.root, id)
	if e := os.Rename(p, p+"-old"); e != nil {
		t.Fatal(e)
	}
	os.Mkdir(p, 0700)
	if m.Cleanup(context.Background(), id) == nil {
		t.Fatal("adopted replacement cgroup")
	}
}
func TestStartFailureCleanupAndNoFallback(t *testing.T) {
	f := &fakeFS{}
	m := &Manager{root: t.TempDir(), groups: map[string]*group{}, fs: f}
	_, e := m.Launch(exec.Command("missing"), func() error { return errors.New("clone3 denied") })
	if e == nil || len(m.groups) != 0 {
		t.Fatal("failed start leaked or was swallowed")
	}
}

func TestIncompleteThawCannotBeReportedAsFrozen(t *testing.T) {
	m, f, _ := fixture(t)
	if e := m.Freeze(context.Background(), token); e != nil {
		t.Fatal(e)
	}
	f.failThaw = true
	if m.Thaw(token) == nil {
		t.Fatal("expected thaw failure")
	}
	if m.Freeze(context.Background(), token) == nil {
		t.Fatal("accepted token without confirmed frozen state")
	}
	f.failThaw = false
	if e := m.Thaw(token); e != nil {
		t.Fatal(e)
	}
}
