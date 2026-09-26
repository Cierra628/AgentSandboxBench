package process

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/e2b-dev/infra/packages/envd/internal/clock"
	"github.com/e2b-dev/infra/packages/envd/internal/env"
	"github.com/e2b-dev/infra/packages/envd/internal/taskcgroup"
	"github.com/e2b-dev/infra/packages/envd/internal/terminal"
	"go.uber.org/zap"
)

// Runs real simple HTTP handlers, Service.Start (RPC implementation), and PTY
// launch code in a disposable kernel guest, without main/envd or host services.
func TestIsolatedEnvdLaunchers(t *testing.T) {
	parent := os.Getenv("ASB_CGROUP_SMOKE_PARENT")
	if parent == "" {
		t.Skip("isolated cgroup guest required")
	}
	m, e := taskcgroup.New(parent)
	if e != nil {
		t.Fatal(e)
	}
	taskcgroup.Default = m
	defer func() {
		taskcgroup.Default = nil
		tasks, e := m.Tasks()
		if e != nil {
			t.Error(e)
		}
		for _, task := range tasks {
			if e = m.Cleanup(context.Background(), task.ID); e != nil {
				t.Error(e)
			}
		}
	}()
	logger := zap.NewNop().Sugar()
	simple := NewSimpleProcessManager(logger)
	call := func(handler http.HandlerFunc, body any) *httptest.ResponseRecorder {
		b, _ := json.Marshal(body)
		w := httptest.NewRecorder()
		handler(w, httptest.NewRequest("POST", "/", bytes.NewReader(b)))
		if w.Code != 200 {
			t.Fatalf("handler failed: %d %s", w.Code, w.Body)
		}
		return w
	}
	created := call(simple.Create, SimpleProcessCreateRequest{Cmd: "printf frozen-action; exit 127", User: "root", Cwd: "/tmp"})
	var response SimpleProcessCreateResponse
	if e = json.Unmarshal(created.Body.Bytes(), &response); e != nil {
		t.Fatal(e)
	}
	if response.TaskID == "" {
		t.Fatal("simple launcher did not return task_id")
	}
	waited := call(simple.Wait, SimpleProcessWaitRequest{Pid: response.Pid})
	var result SimpleProcessWaitResponse
	if e = json.Unmarshal(waited.Body.Bytes(), &result); e != nil {
		t.Fatal(e)
	}
	if result.ExitCode != 127 || result.Stdout != "frozen-action" || result.Stderr != "" {
		t.Fatalf("action semantics changed: %+v", result)
	}
	t.Log("PASS actual simple HTTP create/wait preserves stdout and expected 127")
	// Each real launcher writes its membership before shell startup returns.
	paths := []string{"/tmp/simple-worker", "/tmp/rpc-worker", "/tmp/pty-worker"}
	detached := func(path string) string {
		return fmt.Sprintf("setsid /bin/sh -c 'echo $$ > %s; while :; do sleep 1; done' </dev/null >/dev/null 2>&1 &", path)
	}
	created = call(simple.Create, SimpleProcessCreateRequest{Cmd: detached(paths[0]), User: "root", Cwd: "/tmp"})
	json.Unmarshal(created.Body.Bytes(), &response)
	call(simple.Wait, SimpleProcessWaitRequest{Pid: response.Pid})
	service := NewService(logger, &env.EnvConfig{Shell: "/bin/bash"}, clock.NewService(logger))
	vars := map[string]string{}
	if _, e = service.Start("probe", detached(paths[1]), &vars, "/tmp"); e != nil {
		t.Fatal(e)
	}
	command := detached(paths[2])
	cwd := "/tmp"
	term, e := terminal.New("probe", "/bin/bash", &cwd, 80, 24, &vars, &command, logger)
	if e != nil {
		t.Fatal(e)
	}
	defer term.Destroy()
	deadline := time.Now().Add(3 * time.Second)
	for _, path := range paths {
		for {
			if _, e = os.Stat(path); e == nil {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("launcher writer not ready: " + path)
			}
			time.Sleep(10 * time.Millisecond)
		}
		pid, e := os.ReadFile(path)
		if e != nil {
			t.Fatal(e)
		}
		membership, e := os.ReadFile("/proc/" + strings.TrimSpace(string(pid)) + "/cgroup")
		if e != nil {
			t.Fatal(e)
		}
		tasks, e := m.Tasks()
		if e != nil {
			t.Fatal(e)
		}
		found := false
		for _, task := range tasks {
			if filepath.Base(strings.TrimSpace(string(membership))) == task.ID {
				found = true
			}
		}
		if !found {
			t.Fatalf("launcher escaped task groups: %s", membership)
		}
	}
	tasks, e := m.Tasks()
	if e != nil {
		t.Fatal(e)
	}
	if len(tasks) != 4 {
		t.Fatalf("expected one group per launcher call, got %d", len(tasks))
	}
	token := "22222222222222222222222222222222"
	request := func(op string) *httptest.ResponseRecorder {
		body := map[string]any{"op": op, "token": token, "timeout_ms": 3000}
		return call(m.ServeHTTP, body)
	}
	request("freeze")
	request("thaw")
	t.Log("PASS actual simple, RPC Service.Start, PTY launchers retain setsid descendants; HTTP freeze/thaw confirmed")
}
