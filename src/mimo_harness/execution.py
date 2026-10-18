"""Controlled subprocesses and per-workspace execution environments."""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ProcessOutcome:
    returncode: int | None
    status: str
    stdout: str
    stderr: str
    stdout_bytes: bytes = b""


class CheckCancelled(KeyboardInterrupt):
    def __init__(self, row):
        self.row = row


class WindowsJob:
    """Kill-on-close job; the gated child cannot spawn outside it before assignment."""
    def __init__(self, process):
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                        ("LimitFlags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD),
                        ("SchedulingClass", w.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io),
                        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                        ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        self.api.CreateJobObjectW.restype = w.HANDLE
        self.api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetInformationJobObject.restype = w.BOOL
        self.api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.api.AssignProcessToJobObject.restype = w.BOOL
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.CloseHandle.restype = w.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.api.AssignProcessToJobObject(self.handle, w.HANDLE(int(process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def run_process(argv, cwd: Path, *, timeout=180, env=None,
                stdout_path: Path | None = None, stderr_path: Path | None = None,
                max_output_bytes: int | None = None) -> ProcessOutcome:
    """Stream logs to disk; terminate the whole managed tree on exit/timeout/interrupt."""
    stdout_path = Path(stdout_path) if stdout_path else None
    stderr_path = Path(stderr_path) if stderr_path else None
    out = stdout_path.open("w+b") if stdout_path else tempfile.TemporaryFile()
    err = stderr_path.open("w+b") if stderr_path else tempfile.TemporaryFile()
    process = job = None
    readers = []
    overflow = threading.Event()
    status, code = "completed", None
    try:
        worker = str(Path(__file__).with_name("process_worker.py"))
        process = subprocess.Popen(
            [sys.executable, "-I", worker, json.dumps(list(argv))], cwd=cwd, env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if max_output_bytes else out,
            stderr=subprocess.PIPE if max_output_bytes else err,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        if os.name == "nt":
            job = WindowsJob(process)
        if max_output_bytes:
            def drain(pipe, destination):
                retained = 0
                try:
                    while True:
                        chunk = pipe.read(65536)
                        if not chunk:
                            break
                        remaining = max(0, max_output_bytes - retained)
                        destination.write(chunk[:remaining])
                        retained += len(chunk[:remaining])
                        if len(chunk) > remaining:
                            overflow.set()
                finally:
                    pipe.close()
            for pipe, destination in ((process.stdout, out), (process.stderr, err)):
                thread = threading.Thread(target=drain, args=(pipe, destination), daemon=True)
                thread.start()
                readers.append(thread)
        process.stdin.write(b"G")
        process.stdin.close()
        try:
            if max_output_bytes:
                deadline = time.monotonic() + timeout
                while process.poll() is None:
                    if overflow.is_set():
                        status = "output_limit"
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        status = "timeout"
                        break
                    try:
                        process.wait(timeout=min(0.02, remaining))
                    except subprocess.TimeoutExpired:
                        pass
                code = process.poll()
            else:
                code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            status = "timeout"
        except KeyboardInterrupt:
            status = "cancelled"
    finally:
        if job is not None:
            job.close()
        if process is not None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        for thread in readers:
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Could not finish process output collection.")
        if overflow.is_set() and status == "completed":
            status = "output_limit"
        def tail(handle):
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 1024 * 1024))
            return handle.read()
        stdout_bytes, stderr_bytes = tail(out), tail(err)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        out.close()
        err.close()
    if code == 125:
        status = "error"
    return ProcessOutcome(code, status, stdout, stderr, stdout_bytes)


def validate_execution(value: Any) -> dict:
    cfg = {"mode": "local", "env_allowlist": [], "prepare": [], **(value or {})} if isinstance(value, (dict, type(None))) else None
    if cfg is None:
        raise ValueError("execution must be an object.")
    extra = set(cfg) - {"mode", "env_allowlist", "prepare", "image", "network", "prepare_network", "cpus", "memory_mb", "pids_limit"}
    if extra:
        raise ValueError(f"Unknown execution fields: {sorted(extra)}")
    if cfg["mode"] not in {"local", "venv", "docker"}:
        raise ValueError("execution.mode must be local, venv or docker.")
    if not isinstance(cfg["env_allowlist"], list) or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) for k in cfg["env_allowlist"]):
        raise ValueError("execution.env_allowlist must contain environment variable names.")
    reserved = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    if reserved & {k.upper() for k in cfg["env_allowlist"]}:
        raise ValueError("env_allowlist cannot override isolated home/temp/Python paths.")
    from mimo_harness.checks import validate_checks
    if not isinstance(cfg["prepare"], list):
        raise ValueError("execution.prepare must be an array.")
    for index, step in enumerate(cfg["prepare"]):
        if not isinstance(step, dict) or set(step) - {"argv", "cwd", "timeout_seconds"}:
            raise ValueError("Prepare steps support argv, cwd and timeout_seconds only.")
        validate_checks([{"id": f"prepare-{index}", "adapter": "command", **step}])
    if cfg["mode"] == "local" and cfg["prepare"]:
        raise ValueError("Dependency preparation requires venv or docker mode to avoid global installation.")
    if cfg["mode"] == "docker":
        if not isinstance(cfg.get("image"), str) or not cfg["image"].strip() or cfg["image"].startswith("-"):
            raise ValueError("Docker mode requires a locally available image.")
        for key in ("network", "prepare_network"):
            if cfg.get(key, "none") not in {"none", "bridge"}:
                raise ValueError(f"execution.{key} must be none or bridge.")
        for key, default, maximum in (("cpus", 1, 128), ("memory_mb", 512, 1048576), ("pids_limit", 128, 65536)):
            n = cfg.get(key, default)
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 < n <= maximum:
                raise ValueError(f"execution.{key} must be positive and bounded.")
            if key != "cpus" and not isinstance(n, int):
                raise ValueError(f"execution.{key} must be an integer.")
    return cfg


def clean_environment(home: Path, allowlist=()) -> dict[str, str]:
    home.mkdir(parents=True, exist_ok=True)
    temp = home / "tmp"
    temp.mkdir(exist_ok=True)
    keys = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE", "LANG", "LC_ALL", "TZ"}
    allowed = {k.upper() for k in allowlist}
    env = {k: v for k, v in os.environ.items() if k.upper() in keys | allowed}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), PYTHONNOUSERSITE="1",
               PYTHONUTF8="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_CONFIG_FILE=os.devnull)
    return env


class ExecutionContext:
    def __init__(self, root: Path, logs: Path, config=None):
        self.root, self.logs = root.resolve(), logs.resolve()
        self.config = validate_execution(config)
        self.mode = self.config["mode"]
        self.runtime = self.logs / "runtime"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.env = clean_environment(self.runtime / "home", self.config["env_allowlist"])
        self.python = sys.executable
        self.image_id = None
        self.preparation = []
        self.containers = []

    def metadata(self):
        return {"mode": self.mode, "python": self.python, "image_id": self.image_id,
                "env_allowlist": self.config["env_allowlist"], "runtime_dir": str(self.runtime),
                "network": self.config.get("network", "none") if self.mode == "docker" else "host-unrestricted",
                "container_names": list(self.containers), "host_python_version": sys.version.split()[0],
                "docker_policy": {"image": self.config["image"], "prepare_network": self.config.get("prepare_network", "none"),
                                  "cpus": self.config.get("cpus", 1), "memory_mb": self.config.get("memory_mb", 512),
                                  "pids_limit": self.config.get("pids_limit", 128)} if self.mode == "docker" else None}

    def path(self, path: Path) -> str:
        path = path.resolve()
        if self.mode != "docker":
            return str(path)
        for host, container in ((self.root, "/workspace"), (self.logs, "/evidence")):
            try:
                return container + "/" + path.relative_to(host).as_posix()
            except ValueError:
                pass
        if path.parent == Path(__file__).parent.resolve() and path.suffix in {".py", ".mjs"}:
            return "/mimo/" + path.name
        raise ValueError(f"Path is outside Docker mounts: {path}")

    def setup(self):
        if self.mode == "venv":
            destination = self.runtime / "venv"
            if destination.exists():
                raise ValueError("Refusing to reuse an existing dependency environment.")
            self._prepare_command([sys.executable, "-m", "venv", str(destination)], "create-venv")
            self.python = str(destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
            self.env["VIRTUAL_ENV"] = str(destination)
            self.env["PATH"] = str(Path(self.python).parent) + os.pathsep + self.env.get("PATH", "")
        elif self.mode == "docker":
            if sys.platform != "linux" or os.getuid() == 0:
                raise ValueError("Docker mode currently requires a non-root Linux host user.")
            if not shutil.which("docker"):
                raise ValueError("Docker was requested but docker is not on PATH; no local fallback.")
            if (self.root / ".mimo").is_symlink() or (self.root / ".mimo/venv").exists():
                raise ValueError("Refusing to reuse an existing Docker dependency environment.")
            inspect = run_process(["docker", "image", "inspect", "--format", "{{.Id}}", self.config["image"]], self.root, timeout=30)
            if inspect.returncode != 0 or not inspect.stdout.strip().startswith("sha256:"):
                raise ValueError("Docker daemon/image unavailable. Pull or build the configured image first.")
            self.image_id = inspect.stdout.strip()
            self._prepare_command(["python", "-m", "venv", "--system-site-packages", "/workspace/.mimo/venv"], "create-venv")
            self.python = "/workspace/.mimo/venv/bin/python"
        for index, step in enumerate(self.config["prepare"]):
            argv = [self.python if part == "{python}" else part for part in step["argv"]]
            self._prepare_command(argv, f"prepare-{index}", step)
        if self.mode != "local":
            self._prepare_command([self.python, "-m", "pip", "freeze", "--all"], "installed-packages")
        (self.logs / "execution_environment.json").write_text(json.dumps(self.metadata(), indent=2), encoding="utf-8")

    def _prepare_command(self, argv, name, spec=None):
        spec = spec or {}
        cwd = (self.root / spec.get("cwd", ".")).resolve()
        cwd.relative_to(self.root)
        outcome = self.run(argv, cwd, spec.get("timeout_seconds", 180),
                           self.logs / f"{name}_stdout.log", self.logs / f"{name}_stderr.log", prepare=True)
        row = {"name": name, "argv": argv, "cwd": str(cwd), "status": outcome.status,
               "returncode": outcome.returncode}
        self.preparation.append(row)
        (self.logs / "preparation.json").write_text(json.dumps(self.preparation, indent=2), encoding="utf-8")
        if outcome.status == "cancelled":
            raise KeyboardInterrupt
        if outcome.status != "completed" or outcome.returncode != 0:
            raise ValueError(f"Environment preparation failed at {name}; see preparation logs.")

    def run(self, argv, cwd, timeout, stdout_path, stderr_path, *, prepare=False, max_output_bytes=None):
        if self.mode != "docker":
            return run_process(argv, cwd, timeout=timeout, env=self.env,
                               stdout_path=stdout_path, stderr_path=stderr_path, max_output_bytes=max_output_bytes)
        name = "mimo-" + uuid.uuid4().hex
        self.containers.append(name)
        worker = Path(__file__).parent.resolve()
        if any("," in str(p) for p in (self.root, self.logs, worker)):
            raise ValueError("Docker bind-mount paths cannot contain commas.")
        network = self.config.get("prepare_network" if prepare else "network", "none")
        command = ["docker", "run", "--rm", "--name", name, "--init", "--pull=never",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--network", network,
                   "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                   "--cpus", str(self.config.get("cpus", 1)),
                   "--memory", f"{self.config.get('memory_mb', 512)}m",
                   "--pids-limit", str(self.config.get("pids_limit", 128)),
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=67108864,mode=1777",
                   "--mount", f"type=bind,source={self.root},target=/workspace",
                   "--mount", f"type=bind,source={self.logs},target=/evidence",
                   "--mount", f"type=bind,source={worker},target=/mimo,readonly",
                   "--workdir", self.path(cwd), "--env", "HOME=/tmp", "--env", "PYTHONNOUSERSITE=1",
                   "--env", "PYTHONUTF8=1", "--env", "PIP_CONFIG_FILE=/dev/null"]
        for key in self.config["env_allowlist"]:
            command.extend(["--env", key])
        command.extend(["--entrypoint", argv[0], self.image_id, *argv[1:]])
        self.env.setdefault("PATH", os.defpath)
        try:
            return run_process(command, self.root, timeout=timeout, env=self.env,
                               stdout_path=stdout_path, stderr_path=stderr_path, max_output_bytes=max_output_bytes)
        finally:
            cleanup = subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)
            # A successful --rm already removed it; other failures must be visible.
            if cleanup.returncode and b"No such container" not in cleanup.stderr:
                raise RuntimeError("Could not confirm container cleanup; check Docker before retrying.")
