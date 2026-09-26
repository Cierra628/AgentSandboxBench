package taskcgroup

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

// Executed only in a disposable guest or an explicitly delegated cgroup parent.
func TestKernelDescendantsFreezeCleanup(t *testing.T) {
	parent := os.Getenv("ASB_CGROUP_SMOKE_PARENT")
	if parent == "" {
		t.Skip("real cgroup smoke requires an isolated guest/delegated parent")
	}
	m, e := New(parent)
	if e != nil {
		t.Fatal(e)
	}
	dir := t.TempDir()
	counter := filepath.Join(dir, "counter")
	pidfile := filepath.Join(dir, "pid")
	// Redirection closes inherited output FDs; shell Wait must not wait for worker.
	worker := fmt.Sprintf("echo $$ > %s; while :; do echo x >> %s; sleep 0.01; done", pidfile, counter)
	cmd := exec.Command("/bin/sh", "-c", fmt.Sprintf("setsid /bin/sh -c '%s' </dev/null >/dev/null 2>&1 & exit 0", worker))
	id, e := m.Launch(cmd, cmd.Start)
	if e != nil {
		t.Fatal(e)
	}
	defer func() {
		if e := m.Thaw(token); e != nil {
			t.Error(e)
		}
		for id := range m.groups {
			if e := m.Cleanup(context.Background(), id); e != nil {
				t.Error(e)
			}
		}
		if e := os.Remove(m.root); e != nil {
			t.Error(e)
		}
	}()
	if e = cmd.Wait(); e != nil {
		t.Fatal(e)
	}
	size := func() int64 {
		st, e := os.Stat(counter)
		if e != nil {
			return 0
		}
		return st.Size()
	}
	waitGrowth := func(before int64) {
		deadline := time.Now().Add(3 * time.Second)
		for size() <= before {
			if time.Now().After(deadline) {
				t.Fatal("writer did not grow")
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	waitGrowth(0)
	pid, e := os.ReadFile(pidfile)
	if e != nil {
		t.Fatal(e)
	}
	cg, e := os.ReadFile("/proc/" + strings.TrimSpace(string(pid)) + "/cgroup")
	if e != nil {
		t.Fatal(e)
	}
	if !strings.Contains(string(cg), filepath.Base(m.root)+"/"+id) {
		t.Fatalf("setsid descendant escaped: %s", cg)
	}
	t.Log("PASS parent shell exited; setsid background descendant remains in task cgroup")
	// A second manager represents an unrelated run; targeted cleanup must not kill it.
	other, e := New(parent)
	if e != nil {
		t.Fatal(e)
	}
	sentinel := exec.Command("/bin/sleep", "60")
	otherID, e := other.Launch(sentinel, sentinel.Start)
	if e != nil {
		t.Fatal(e)
	}
	defer func() {
		if e := other.Cleanup(context.Background(), otherID); e != nil {
			t.Error(e)
		}
		sentinel.Wait()
		if e := os.Remove(other.root); e != nil {
			t.Error(e)
		}
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if e = m.Freeze(ctx, token); e != nil {
		t.Fatal(e)
	}
	before := size()
	time.Sleep(200 * time.Millisecond)
	if size() != before {
		t.Fatal("frozen writer changed file")
	}
	t.Log("PASS frozen=1 confirmed; writes stopped over 200ms guest window")
	if e = m.Thaw(token); e != nil {
		t.Fatal(e)
	}
	waitGrowth(before)
	t.Log("PASS thaw confirmed and writer resumed")
	// Canceled acquisition models freeze timeout; it must thaw a live writer.
	canceled, stop := context.WithCancel(context.Background())
	stop()
	if e = m.Freeze(canceled, token); e == nil {
		t.Fatal("expected canceled freeze")
	}
	before = size()
	waitGrowth(before)
	t.Log("PASS canceled freeze thaws and writes resume")
	if e = m.Cleanup(context.Background(), "foreign"); e == nil {
		t.Fatal("foreign cleanup accepted")
	}
	if e = m.Cleanup(context.Background(), id); e != nil {
		t.Fatal(e)
	}
	if _, e = os.Stat(filepath.Join(m.root, id)); !os.IsNotExist(e) {
		t.Fatal("owned group remains")
	}
	if e = sentinel.Process.Signal(syscall.Signal(0)); e != nil {
		t.Fatalf("unrelated sentinel killed: %v", e)
	}
	t.Log("PASS task killed/removed; unrelated task and process remain")
}
