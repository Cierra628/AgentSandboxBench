package taskcgroup

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"
)

func splitLines(s string) []string { return strings.Split(strings.TrimSpace(s), "\n") }

// ServeHTTP is a guest control-plane endpoint; envd must stay outside task groups.
// Network access is subject to the same private-guest boundary as existing envd.
func (m *Manager) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fail := func(e error) { http.Error(w, e.Error(), http.StatusConflict) }
	if m == nil {
		http.Error(w, "task cgroups disabled", http.StatusServiceUnavailable)
		return
	}
	if r.Method == http.MethodGet {
		v, e := m.Tasks()
		if e != nil {
			fail(e)
			return
		}
		json.NewEncoder(w).Encode(v)
		return
	}
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	var req struct {
		Op        string `json:"op"`
		Token     string `json:"token"`
		ID        string `json:"id"`
		TimeoutMS int    `json:"timeout_ms"`
	}
	if e := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096)).Decode(&req); e != nil {
		fail(e)
		return
	}
	if req.TimeoutMS <= 0 || req.TimeoutMS > 30000 {
		req.TimeoutMS = 5000
	}
	ctx, cancel := context.WithTimeout(r.Context(), time.Duration(req.TimeoutMS)*time.Millisecond)
	defer cancel()
	var e error
	var result any = map[string]bool{"ok": true}
	switch req.Op {
	case "freeze":
		e = m.Freeze(ctx, req.Token)
	case "thaw":
		e = m.Thaw(req.Token)
	case "cleanup":
		e = m.Cleanup(ctx, req.ID)
	case "prepare":
		// Fixed maintenance action outside tasks: no arbitrary tool escape hatch.
		// Preserve upstream /tmp exclusions and finish sync before CH pause/copy.
		m.mu.Lock()
		if !validToken(req.Token) || m.token != req.Token || !m.frozen {
			e = errors.New("prepare requires freeze ownership")
		} else {
			path := "/dev/shm/asb-runtime-" + req.Token + ".tar"
			cmd := exec.CommandContext(ctx, "tar", "--xattrs", "--acls", "--numeric-owner", "--exclude=./mixfs-exp5*", "--exclude=./mixfs-probe*", "-C", "/tmp", "-cf", path, ".")
			if b, err := cmd.CombinedOutput(); err != nil {
				e = errors.New(err.Error() + ": " + string(b))
			} else {
				syscall.Sync()
				result = map[string]string{"runtime_path": path}
			}
		}
		m.mu.Unlock()
	case "discard-runtime":
		if !validToken(req.Token) {
			e = errors.New("invalid token")
		} else {
			e = os.Remove("/dev/shm/asb-runtime-" + req.Token + ".tar")
			if os.IsNotExist(e) {
				e = nil
			}
		}
	default:
		e = errors.New("unknown task operation")
	}
	if e != nil {
		fail(e)
		return
	}
	json.NewEncoder(w).Encode(result)
}
