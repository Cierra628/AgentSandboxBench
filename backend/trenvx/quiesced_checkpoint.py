"""Bounded, file-state-only capture for the pinned TrEnv-X controller.

Run the whole capture in one worker. Cancellation must join it before allowing
parent deletion or image sealing; a canceled to_thread call cannot stop cp.
"""
from __future__ import annotations
import asyncio
import http.client
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import urllib.parse
import urllib.request


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(self.path))


class CaptureIO:
    def __init__(self, guest_url: str, ch_socket: Path, timeout: float = 30):
        if not 0 < timeout <= 30:
            raise ValueError("capture timeout must be in (0, 30]")
        self.guest_url = guest_url.rstrip("/")
        self.ch_socket = ch_socket
        self.timeout = timeout
        # Private guest access should never go through a host HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def task(self, op: str, token: str, *, id: str | None = None):
        data = json.dumps(dict(op=op, token=token, id=id or "", timeout_ms=int(self.timeout * 1000))).encode()
        req = urllib.request.Request(self.guest_url + "/tasks", data,
                                     {"Content-Type": "application/json"})
        with self.http.open(req, timeout=self.timeout + 3) as response:
            return json.load(response)

    def tasks(self):
        with self.http.open(self.guest_url + "/tasks", timeout=self.timeout) as response:
            return json.load(response)

    def download(self, guest_path: str, output: Path):
        url = self.guest_url + "/file?" + urllib.parse.urlencode({"path": guest_path})
        with self.http.open(url, timeout=self.timeout) as response, output.open("xb") as target:
            shutil.copyfileobj(response, target)

    def vm(self, op: str):
        conn = UnixHTTP(self.ch_socket, self.timeout)
        try:
            conn.request("GET" if op == "info" else "PUT", "/api/v1/vm." + op)
            response = conn.getresponse()
            data = response.read()
            if response.status not in (200, 204):
                raise RuntimeError(f"CH {op}: {response.status} {data!r}")
            return json.loads(data) if data else None
        finally:
            conn.close()

    def copy(self, source: Path, target: Path):
        # subprocess.run kills and waits on timeout before returning.
        if target.exists():
            raise FileExistsError(target)
        subprocess.run(["cp", "--reflink=never", "--sparse=always", str(source), str(target)],
                       check=True, timeout=self.timeout)


def capture_upper(io: CaptureIO, upper: Path, copy: Path, runtime: Path, audit: dict):
    """Freeze/prepare/sync -> CH pause/confirm/copy -> resume/confirm -> thaw.

    Recovery uses independent timeouts and runs even if a response was lost.
    Never return a usable capture when resume or thaw confirmation fails.
    """
    token = secrets.token_hex(16)
    audit.update(token=token, restore_semantics="file-state-only", complete=False)
    failure = None
    recovery = []
    pause_attempted = False
    try:
        io.task("freeze", token)
        audit["frozen"] = True
        prepared = io.task("prepare", token)
        io.download(prepared["runtime_path"], runtime)
        pause_attempted = True
        io.vm("pause")
        if io.vm("info")["state"] != "Paused":
            raise RuntimeError("CH did not confirm Paused")
        audit["paused"] = True
        io.copy(upper, copy)
        audit["copied"] = True
    except BaseException as exc:
        failure = exc
    finally:
        if pause_attempted:
            try:
                # Always attempt resume after an attempted pause, including a
                # lost pause/info reply. A failed status query must not prevent
                # the recovery action itself.
                resume_error = None
                try:
                    io.vm("resume")
                except BaseException as exc:
                    resume_error = exc
                if io.vm("info")["state"] != "Running":
                    raise RuntimeError("CH did not confirm Running") from resume_error
                if resume_error is not None:
                    audit["resume_response_error"] = repr(resume_error)
                audit["resumed"] = True
            except BaseException as exc:
                recovery.append(exc)
                audit["resume_error"] = repr(exc)
        try:
            io.task("discard-runtime", token)
            audit["runtime_removed"] = True
        except BaseException as exc:
            recovery.append(exc)
            audit["runtime_remove_error"] = repr(exc)
        # Attempt even if freeze failed/timed out, prepare failed, or CH recovery
        # failed. Guest Freeze itself also thaws on failed acquisition.
        try:
            io.task("thaw", token)
            audit["thawed"] = True
        except BaseException as exc:
            recovery.append(exc)
            audit["thaw_error"] = repr(exc)
    if failure is not None:
        audit["error"] = repr(failure)
    if failure or recovery:
        raise BaseExceptionGroup("checkpoint capture/recovery failed",
                                 ([failure] if failure else []) + recovery)
    audit["complete"] = True


async def capture_joined(*args):
    job = asyncio.create_task(asyncio.to_thread(capture_upper, *args))
    canceled = False
    while True:
        try:
            await asyncio.shield(job)
            break
        except asyncio.CancelledError:
            # Do not release the caller while a thread can still copy or freeze.
            canceled = True
            if job.done():
                job.result()  # Preserve worker failures, including recovery.
                break
    if canceled:
        raise asyncio.CancelledError


def cleanup_tasks(io: CaptureIO, audit: dict):
    """Only IDs inventoried by this guest's owning manager; never PID scans."""
    audit.update(complete=False, tasks=[])
    failures = []
    for task in io.tasks():
        row = {"id": task["id"], "cleaned": False}
        audit["tasks"].append(row)
        try:
            io.task("cleanup", "", id=task["id"])
            row["cleaned"] = True
        except Exception as exc:
            row["error"] = repr(exc)
            failures.append(exc)
    if failures:
        raise ExceptionGroup("task cleanup failed", failures)
    audit["complete"] = True
