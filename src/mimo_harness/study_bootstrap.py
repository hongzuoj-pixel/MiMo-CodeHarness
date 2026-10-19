"""Standalone offline installer copied into a user kit; no harness import required."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath


LAUNCHER = '''from pathlib import Path
import subprocess, sys
root = Path(__file__).resolve().parent
python = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
raise SystemExit(subprocess.call([str(python), "-X", "utf8", "-I", "-m", "mimo_harness", *sys.argv[1:]], cwd=root))
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError("Expected an object: " + str(path))
    return value


def write(path, value):
    fd, temp = tempfile.mkstemp(prefix='.setup-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.write('\n')
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def regular(root, name):
    if (not isinstance(name, str) or not name or '\\' in name or Path(name).is_absolute()
            or PureWindowsPath(name).drive or any(p in {'', '.', '..'} for p in name.split('/'))):
        raise ValueError("Unsafe kit path.")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent) or not path.is_file():
        raise ValueError("Missing or linked kit file: " + name)
    path.resolve().relative_to(root.resolve())
    return path


def validate_kit(root):
    manifest = regular(root, 'kit.json')
    kit = read(manifest)
    if (type(kit.get('schema_version')) is not int or kit['schema_version'] != 1
            or kit.get('kind') != 'independent_user_kit' or not isinstance(kit.get('files'), dict)
            or kit.get('participants') != ['user-01', 'user-02', 'user-03']
            or not isinstance(kit.get('wheels'), list) or not kit['wheels']
            or not isinstance(kit.get('version'), str) or not isinstance(kit.get('engine_sha256'), str)):
        raise ValueError("Invalid kit manifest.")
    files = kit['files']
    required = {'bootstrap.py', 'GUIDE_CN.md'} | {'participants/' + p + '/feedback.json' for p in kit['participants']}
    if not required.issubset(files):
        raise ValueError("Incomplete kit inventory.")
    for name, expected in files.items():
        if sha(regular(root, name)) != expected:
            raise ValueError("Kit file changed: " + name)
    wheels, names = [], set()
    for row in kit['wheels']:
        name = row['file']
        if name != Path(name).name or name in names or not name.endswith('.whl'):
            raise ValueError("Invalid wheel entry.")
        names.add(name)
        path = regular(root, 'wheels/' + name)
        if files.get('wheels/' + name) != row['sha256'] or sha(path) != row['sha256']:
            raise ValueError("Wheel inventory changed.")
        wheels.append(path)
    if sum(row.get('package') == 'pip' for row in kit['wheels']) != 1:
        raise ValueError("The kit must contain one reviewed offline pip installer wheel.")
    return kit, sha(manifest), wheels


def child_environment():
    keys = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'TMPDIR',
            'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'}
    return {k: v for k, v in os.environ.items() if k.upper() in keys}


def install(kit_root, output, participant, resume=False):
    kit, kit_sha, wheels = validate_kit(kit_root)
    if participant not in kit['participants']:
        raise ValueError("Choose an assigned slot: user-01, user-02 or user-03.")
    if sys.version_info < (3, 10) or not shutil.which('git'):
        raise ValueError("Install Python 3.10+ and Git first; no model key is required.")
    if output.is_symlink():
        raise ValueError("Study output cannot be a symlink.")
    output = output.expanduser().resolve()
    state_path = output / 'setup.json'
    identity = {'schema_version': 1, 'kind': 'technical_installation', 'kit_sha256': kit_sha,
                'participant_slot': participant, 'engine_sha256': kit['engine_sha256'],
                'python': str(Path(sys.executable).resolve()), 'python_version': list(sys.version_info[:2])}
    if output.exists():
        if not resume:
            raise ValueError("Study directory already exists; preserve it and add --resume, or choose a new --output.")
        state = read(regular(output, 'setup.json'))
        if any(state.get(k) != v for k, v in identity.items()):
            raise ValueError("Resume kit, slot or Python changed; preserve this study and choose a new --output.")
    else:
        if resume:
            raise ValueError("No existing setup to resume; omit --resume.")
        output.mkdir(parents=True, exist_ok=False)
        state = {**identity, 'attempts': 0}
    if type(state.get('attempts')) is not int or not 0 <= state['attempts'] < 100:
        raise ValueError("Invalid setup attempt history.")
    state.update(attempts=state['attempts'] + 1, status='INSTALLING', model_called=False,
                 note='Technical setup only. Human feedback remains independent and is never completed by this script.')
    write(state_path, state)
    log_path = output / f"install-{state['attempts']:03d}.log"
    env_root = output / '.venv'
    try:
        if env_root.is_symlink():
            raise ValueError("Study environment cannot be a symlink.")
        env = child_environment()
        with log_path.open('x', encoding='utf-8', newline='\n') as log:
            # Re-running venv without --clear repairs an interrupted creation.
            # Only this setup's owned environment is used; study files survive.
            subprocess.run([sys.executable, '-X', 'utf8', '-I', '-m', 'venv', '--without-pip', str(env_root)], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            python = env_root / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            pip_wheel = next(path for path, row in zip(wheels, kit['wheels']) if row['package'] == 'pip')
            # A half-installed pip can retain metadata while lacking __main__.
            # Load the verified wheel directly, never the damaged environment's
            # pip, and reinstall exact files even when metadata claims success.
            loader = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('pip',run_name='__main__',alter_sys=True)"
            installer = [str(python), '-X', 'utf8', '-I', '-c', loader, str(pip_wheel), '--isolated']
            subprocess.run([*installer, 'install', '--no-index', '--no-deps', '--force-reinstall',
                            *map(str, wheels)], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
            subprocess.run([*installer, 'check'], env=env,
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        code = "import json;from mimo_harness import __version__;from mimo_harness.manifests import engine_identity;print(json.dumps([__version__,engine_identity()]))"
        actual = subprocess.check_output([str(python), '-X', 'utf8', '-I', '-c', code], env=env, text=True, encoding='utf-8', timeout=30)
        if json.loads(actual) != [kit['version'], kit['engine_sha256']]:
            raise ValueError("Installed version/engine differs from the kit.")
        for name, content in [('mimo.py', LAUNCHER)]:
            path = output / name
            if path.exists():
                if path.is_symlink() or path.read_bytes() != content.encode('utf-8'):
                    raise ValueError("Existing study launcher changed; it was preserved.")
            else:
                with path.open('x', encoding='utf-8', newline='\n') as f: f.write(content)
        for name, source in [('feedback.json', kit_root / 'participants' / participant / 'feedback.json'),
                             ('GUIDE_CN.md', kit_root / 'GUIDE_CN.md')]:
            path = output / name
            if path.is_symlink(): raise ValueError("Study material cannot be a symlink.")
            if not path.exists():
                with path.open('xb') as f: f.write(source.read_bytes())
        state['status'] = 'INSTALLED'
        write(state_path, state)
    except KeyboardInterrupt:
        state['status'] = 'CANCELLED'; write(state_path, state)
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        state['status'] = 'ERROR'; write(state_path, state)
        print(f"Setup interrupted/failed. Retained log: {log_path}; retry with the same arguments and --resume.", file=sys.stderr)
        raise
    print(f"[INSTALLED] {output}\n[NEXT] Enter that directory, read GUIDE_CN.md, then run: python mimo.py --version\n[NOTE] No model call or human acceptance was recorded.")
    return 0


def main(argv=None):
    # Windows redirected output can otherwise use a code page that cannot
    # represent a Chinese study path. Child Python processes also request UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Install an exact user kit in a fresh private environment; no model calls.')
    parser.add_argument('--participant', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    try:
        return install(Path(__file__).resolve().parent, args.output, args.participant, args.resume)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print('[SETUP ERROR] ' + str(exc), file=sys.stderr)
        return 4


if __name__ == '__main__':
    raise SystemExit(main())
