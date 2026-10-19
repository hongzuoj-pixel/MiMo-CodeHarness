"""Actual wheel/venv rehearsal; never counted as an independent human trial."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--wheelhouse', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    root = args.output.resolve(); root.mkdir(parents=True, exist_ok=False)
    kit, study = root / 'kit', root / '试用 study with spaces'

    def call(argv, expect=0):
        result = subprocess.run(list(map(str, argv)), capture_output=True, text=True, encoding='utf-8', timeout=240)
        if result.returncode != expect:
            raise AssertionError(f"Exit {result.returncode}, expected {expect}: {argv}\n{result.stdout}\n{result.stderr}")
        return result

    call([sys.executable, '-I', '-m', 'mimo_harness', 'walkthrough-pack', '--wheelhouse', args.wheelhouse.resolve(), '--output', kit])
    command = [sys.executable, kit / 'bootstrap.py', '--participant', 'user-01', '--output', study]
    call(command)
    feedback = study / 'feedback.json'
    data = json.loads(feedback.read_text(encoding='utf-8'))
    assert data['tester_id'] == '' and data['independence_confirmed'] is False and not any(data['steps'].values())
    data['notes'] = 'AUTOMATED_REHEARSAL: preserve this diagnostic note; no human observation.'
    feedback.write_text(json.dumps(data), encoding='utf-8')
    saved = feedback.read_bytes()
    call(command, expect=4)
    # Keep metadata but remove entry files, reproducing a half-installed pip
    # and harness that a normal "already satisfied" install cannot repair.
    site = next((study / '.venv').glob('Lib/site-packages')) if sys.platform == 'win32' else next((study / '.venv').glob('lib/python*/site-packages'))
    (site / 'pip/__main__.py').unlink()
    (site / 'mimo_harness/cli.py').unlink()
    call(command + ['--resume'])
    assert feedback.read_bytes() == saved
    assert len(list(study.glob('install-*.log'))) == 2
    assert json.loads((study / 'setup.json').read_text())['status'] == 'INSTALLED'

    cli = [sys.executable, study / 'mimo.py']
    call(cli + ['--version'])
    call(cli + ['case-init', '--case', 'boltons-write', '--output', 'case'])
    call(cli + ['--config', 'case/mimo-harness.json', 'doctor'])
    call(cli + ['case-check', '--directory', 'case', '--name', 'first'])
    reference = study / 'case/results/first/reference.zip'
    original = hashlib.sha256(reference.read_bytes()).hexdigest()
    call(cli + ['case-check', '--directory', 'case', '--name', 'first'], expect=4)
    assert hashlib.sha256(reference.read_bytes()).hexdigest() == original
    call(cli + ['resume', '--run', 'case/runs/first-reference', '--name', 'manual-recovery'])
    call(cli + ['export', '--run', 'case/runs/manual-recovery', '--output', 'evidence.zip'])
    moved = root / 'relocated.zip'; shutil.copyfile(study / 'evidence.zip', moved)
    gate = json.loads(call(cli + ['gate', '--bundle', moved, '--json']).stdout)
    assert gate['allowed'] is True
    acceptance = json.loads((study / 'case/results/first/acceptance.json').read_text())
    assert acceptance['status'] == 'PASS' and acceptance['model_called'] is False
    (root / 'rehearsal.json').write_text(json.dumps({'schema_version':1,'mode':'automated_installation_rehearsal',
        'status':'PASS','human_trials_completed':0,'model_called':False,'fresh_install':True,'partial_venv_recovery':True,
        'feedback_preserved':True,'reference_regression_recovery_gate':True,'relocated_bundle_gate':gate}, indent=2), encoding='utf-8')
    print(f"User-kit installation/recovery/evidence rehearsal PASS: {root}; human trials 0; model calls 0.")


if __name__ == '__main__':
    main()
