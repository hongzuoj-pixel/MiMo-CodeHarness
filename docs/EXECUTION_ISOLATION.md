# Execution isolation (v0.5.2)

`execution` is an optional extension of `mimo-harness.json`. Older configs use `local`. Baseline and each candidate get their own context; an existing venv is never silently reused.

## Local, venv and Docker

| Mode | Dependencies | Permissions/network |
|---|---|---|
| `local` | Existing harness interpreter | Host permissions/network; clean environment |
| `venv` | Fresh environment, no host site-packages | Host permissions/network; clean environment |
| `docker` | Fresh workspace venv over an immutable resolved image | Non-root Linux container; bounded memory/CPU/PIDs; default network disabled |

Local/venv are for trusted code. They isolate dependencies/configuration, not OS access. On POSIX, process cleanup covers the managed process group; deliberately detached sessions are outside that local boundary. Windows uses a kill-on-close Job Object. A startup gate prevents the child from spawning before Job assignment. Docker is the OS/container boundary, not a guarantee against every possible kernel or application flaw.

## Independent venvs

Add this block to your project configuration and replace the requirements path with your actual dependency file:

```json
{
  "execution": {
    "mode": "venv",
    "env_allowlist": [],
    "prepare": [
      {
        "argv": ["{python}", "-m", "pip", "install", "-r", "requirements-dev.txt"],
        "cwd": ".",
        "timeout_seconds": 180
      }
    ]
  }
}
```

Include pytest in those dependencies when using the pytest adapter. `{python}` is the environment interpreter; use it in Python preparation/build/test commands. Bare `pip` should be avoided. Preparation is prohibited in local mode to avoid accidental global installation. Network-dependent preparation is subject to host networking in venv mode; use locks/hashes and offline wheels for repeatability.

Preparation runs independently against each version's copied files. Failure stops that workspace's checks and produces `ERROR`. No fallback imports dependencies from the harness's venv. `pip freeze --all`, preparation argv/cwd/results, stdout/stderr, and execution metadata are retained next to the check logs. A new run name is required to retry; checkpoint/resume is not implemented yet.

## Linux Docker

Build or pull a trusted image before running. Docker mode currently requires a non-root Linux host user with access to a local Docker daemon. The image must provide Python and venv support; pytest and other stable dependencies may be baked into it. The acceptance Dockerfile in `tests/docker/` is an example.

From the harness checkout, a minimal Python/pytest image can be built with:

```bash
python -m pip download --dest tests/docker/wheels pytest==8.4.2
docker build -t my-project-test-image:reviewed tests/docker
```

```json
{
  "execution": {
    "mode": "docker",
    "image": "my-project-test-image:reviewed",
    "network": "none",
    "prepare_network": "none",
    "cpus": 1,
    "memory_mb": 512,
    "pids_limit": 128,
    "env_allowlist": [],
    "prepare": []
  }
}
```

The tag resolves to a concrete image ID before execution. Baseline/candidate image changes invalidate comparison. Each workspace has a separate venv with read-only access to image-provided site-packages; installs go into its own writable venv. Preparation may explicitly use `prepare_network: "bridge"`; test networking is controlled separately. `host` networking, privileged mode, arbitrary extra mounts and the Docker socket are not exposed by this configuration.

Every command starts a named disposable container with non-root UID/GID, read-only root filesystem, dropped capabilities, no-new-privileges, tmpfs `/tmp`, CPU/memory/PID limits, and explicit mounts for the copied workspace, evidence directory, and read-only test recorder. The original source checkout is not mounted. A timed-out/interrupted Docker client is followed by forced container removal. Cleanup failures are reported rather than ignored. Mount source paths containing commas are currently rejected.

Docker missing, daemon unavailable, or image not present yields an error. The harness does not pull images or silently run the code locally. Windows Docker/WSL, remote Docker daemons, GPU/device access and independent kernel/VM isolation are outside the certified scope.

## Environment and lifecycle

Only minimal OS/path/locale variables and explicit `env_allowlist` names are inherited. Model/cloud keys, parent PYTHONPATH/PYTHONHOME, user-site configuration and user home/temp are not inherited by default. Reserved home/temp/Python path keys cannot override isolation. Names, not values, are recorded. An explicit allowlist is an intentional disclosure to project code; review it.

Commands use `shell=False` and closed stdin. Logs stream to disk rather than an unbounded in-memory pipe. The caller retains up to the last MiB for diagnostics while full logs remain on disk. On timeout, normal exit or interruption, the managed process group/Job is closed so background descendants do not linger. Target-start failure uses reserved internal exit 125. CLI interruption saves `CANCELLED` with exit 130, run metadata and any completed/partial logs; it can still be summarized by `mimo report`.

## Design references

The environment-per-session approach was informed by [Nox](https://nox.thea.codes/en/stable/config.html). Resolving immutable container environments and retaining evaluation evidence was informed by [SWE-bench's harness](https://github.com/SWE-bench/SWE-bench/blob/main/docs/reference/harness.md). These are architectural references; this implementation does not copy their execution code or inherit their certification claims. Automatic result caching is intentionally deferred until input/environment fingerprint rules are implemented.

Mechanism references: [Python subprocess](https://docs.python.org/3/library/subprocess.html), [venv](https://docs.python.org/3/library/venv.html), [Docker run](https://docs.docker.com/reference/cli/docker/container/run/).
