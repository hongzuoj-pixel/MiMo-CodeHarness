"""Synthetic wheel fixtures test kit integrity; actual installation runs in CI."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from mimo_harness import __version__
from mimo_harness.cli import main
from mimo_harness.manifests import engine_identity, atomic_json
from mimo_harness.study_bootstrap import validate_kit, install
from mimo_harness.user_kit import REQUIRED_PACKAGES


@pytest.fixture
def wheelhouse(tmp_path):
    import importlib.util
    import mimo_harness.user_kit as kit
    folder = tmp_path / 'wheels'; folder.mkdir()
    source = Path(kit.__file__).parent
    for name in REQUIRED_PACKAGES:
        stem = name.replace('-', '_')
        version = __version__ if name == 'mimo-codeharness' else '1.0'
        with zipfile.ZipFile(folder / f'{stem}-{version}-py3-none-any.whl', 'w') as wheel:
            wheel.writestr(f'{stem}-{version}.dist-info/METADATA', f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
            wheel.writestr(f'{stem}-{version}.dist-info/WHEEL', 'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            if name == 'mimo-codeharness':
                for path in source.iterdir():
                    if path.suffix in {'.py', '.mjs'}: wheel.writestr('mimo_harness/' + path.name, path.read_bytes())
                for module in ('run_mimo_codeharness_v02', 'scoring_v03', 'verification_v04'):
                    wheel.writestr(module + '.py', Path(importlib.util.find_spec(module).origin).read_bytes())
    return folder


def make_kit(tmp_path, wheelhouse):
    root = tmp_path / 'kit'
    assert main(['walkthrough-pack', '--wheelhouse', str(wheelhouse), '--output', str(root)]) == 0
    return root


def test_three_blank_slots_share_exact_wheel_and_engine(tmp_path, wheelhouse):
    root = make_kit(tmp_path, wheelhouse)
    kit, _, wheels = validate_kit(root)
    assert kit['engine_sha256'] == engine_identity() and kit['human_trials_completed'] == 0 and kit['model_called'] is False
    assert len(wheels) == len(REQUIRED_PACKAGES)
    for slot in kit['participants']:
        feedback = json.loads((root / 'participants' / slot / 'feedback.json').read_text())
        assert feedback['engine_sha256'] == kit['engine_sha256']
        assert feedback['tester_id'] == '' and feedback['independence_confirmed'] is False and not any(feedback['steps'].values())
    saved = (root / 'kit.json').read_bytes()
    assert main(['walkthrough-pack', '--wheelhouse', str(wheelhouse), '--output', str(root)]) == 4
    assert (root / 'kit.json').read_bytes() == saved


@pytest.mark.parametrize('kind', ['wheel', 'feedback', 'bootstrap'])
def test_corrupt_transfer_is_rejected_before_any_install_or_directory_creation(tmp_path, wheelhouse, kind, monkeypatch):
    root = make_kit(tmp_path, wheelhouse)
    path = {'wheel': next((root / 'wheels').iterdir()), 'feedback': root / 'participants/user-01/feedback.json',
            'bootstrap': root / 'bootstrap.py'}[kind]
    path.write_bytes(path.read_bytes() + b'changed')
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('Corrupt kit reached execution'))
    output = tmp_path / 'study'
    with pytest.raises(ValueError, match='changed'):
        install(root, output, 'user-01')
    assert not output.exists()


@pytest.mark.parametrize('kind', ['missing-dependency', 'wrong-engine', 'duplicate-wheel', 'platform-wheel'])
def test_incompatible_wheelhouse_is_rejected_without_partial_kit(tmp_path, wheelhouse, kind):
    if kind == 'missing-dependency':
        next(wheelhouse.glob('colorama*.whl')).unlink()
    elif kind == 'wrong-engine':
        p = next(wheelhouse.glob('mimo_*.whl'))
        with zipfile.ZipFile(p, 'a') as w: w.writestr('mimo_harness/new_code.py', 'changed=True\n')
    elif kind == 'duplicate-wheel':
        p = next(wheelhouse.glob('pytest*.whl'));(wheelhouse / ('copy_' + p.name)).write_bytes(p.read_bytes())
    else:
        p = next(wheelhouse.glob('pytest*.whl'))
        with zipfile.ZipFile(p) as w: content = {n:w.read(n) for n in w.namelist()}
        with zipfile.ZipFile(p, 'w') as w:
            for n,v in content.items():w.writestr(n, v.replace(b'py3-none-any', b'cp312-cp312-win_amd64') if n.endswith('/WHEEL') else v)
    output = tmp_path / 'kit'
    assert main(['walkthrough-pack', '--wheelhouse', str(wheelhouse), '--output', str(output)]) == 4
    assert not output.exists()


def test_resume_rejects_other_kit_or_slot_before_install(tmp_path, wheelhouse, monkeypatch):
    root = make_kit(tmp_path, wheelhouse)
    output = tmp_path / 'study'; output.mkdir()
    atomic_json(output / 'setup.json', {'schema_version':1,'kind':'technical_installation','kit_sha256':'wrong','attempts':1})
    import subprocess
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: pytest.fail('Mismatched setup reached execution'))
    with pytest.raises(ValueError, match='changed'):
        install(root, output, 'user-01', resume=True)
    with pytest.raises(ValueError, match='already exists'):
        install(root, output, 'user-01')


def test_unsafe_manifest_path_does_not_read_outside_kit(tmp_path, wheelhouse):
    root = make_kit(tmp_path, wheelhouse)
    outside = tmp_path / 'outside.txt'; outside.write_text('fixture')
    manifest = json.loads((root / 'kit.json').read_text())
    manifest['files']['../outside.txt'] = hashlib.sha256(outside.read_bytes()).hexdigest()
    atomic_json(root / 'kit.json', manifest)
    with pytest.raises(ValueError, match='Unsafe'):
        validate_kit(root)
