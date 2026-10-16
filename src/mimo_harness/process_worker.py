"""Gate target startup until its process tree has been registered by the parent."""
import json
import os
import subprocess
import sys


if __name__ == "__main__":
    argv = json.loads(sys.argv[1])
    if sys.stdin.buffer.read(1) != b"G":
        raise SystemExit(125)
    try:
        if os.name == "nt":
            raise SystemExit(subprocess.call(argv, stdin=subprocess.DEVNULL))
        os.execvpe(argv[0], argv, os.environ)
    except OSError as exc:
        print(f"Could not start target command: {exc}", file=sys.stderr)
        raise SystemExit(125)
