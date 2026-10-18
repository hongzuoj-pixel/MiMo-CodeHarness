"""Release records are checked; blank templates and reference fixes are not live/user trials."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mimo_harness.cli import main
from mimo_harness.codex_adapter import CODEX_VERSION
from mimo_harness.manifests import atomic_json, engine_identity, file_digest
from mimo_harness.product_acceptance import REQUIRED_JOBS, STEPS
from mimo_harness.real_cases import CASES, _git
from test_cli_repair import ORIGINAL, diff
from test_real_cases import prepared


def profile(path, **updates):
    atomic_json(path, {"schema_version":1,"adapter":"codex","argv":[sys.executable],"model":"test-fixture",
        "cli_version":CODEX_VERSION,"execution":{"mode":"local","env_allowlist":["CODEX_API_KEY"]},**updates})


@pytest.mark.parametrize("amount", [None, 0, -1, True, float('nan')])
def test_trial_rejects_missing_or_invalid_allowance_before_dispatch(tmp_path, monkeypatch, amount):
    import mimo_harness.product_acceptance as acceptance
    agent=tmp_path/'agent.json'; profile(agent)
    plan=tmp_path/'plan.json'
    assert main(['trial-init','--agent-config',str(agent),'--output',str(plan)]) == 0
    data=json.loads(plan.read_text());data.update(spending_allowance=amount,operator='fixture',spending_cap_location='test-only cap')
    atomic_json(plan,data)
    monkeypatch.setattr(acceptance,'preflight_agent',lambda *args: pytest.fail('Dispatch/preflight before valid allowance'))
    assert main(['trial-run','--plan',str(plan),'--output',str(tmp_path/'trial')]) == 4
    assert not (tmp_path/'trial').exists()


def test_loopback_cannot_be_labelled_as_live_trial(tmp_path, monkeypatch):
    import mimo_harness.product_acceptance as acceptance
    agent=tmp_path/'agent.json'; profile(agent,base_url='http://127.0.0.1:1/v1')
    plan=tmp_path/'plan.json'
    assert main(['trial-init','--agent-config',str(agent),'--output',str(plan)]) == 0
    data=json.loads(plan.read_text());data.update(spending_allowance=1,operator='fixture',spending_cap_location='test-only cap')
    atomic_json(plan,data)
    monkeypatch.setattr(acceptance,'preflight_agent',lambda *args: pytest.fail('Loopback trial dispatched'))
    assert main(['trial-run','--plan',str(plan),'--output',str(tmp_path/'trial')]) == 4


def test_blank_walkthrough_is_not_a_completed_user_or_release(tmp_path, capsys):
    folder=tmp_path/'walkthrough'
    assert main(['walkthrough-init','--output',str(folder)]) == 0
    feedback=json.loads((folder/'feedback.json').read_text())
    assert feedback['independence_confirmed'] is False and not any(feedback['steps'].values())
    saved=(folder/'GUIDE_CN.md').read_bytes()
    assert main(['walkthrough-init','--output',str(folder)]) == 4
    assert (folder/'GUIDE_CN.md').read_bytes()==saved
    capsys.readouterr()
    assert main(['readiness','--repository',str(tmp_path)]) == 3
    record=json.loads(capsys.readouterr().out)
    assert len(record['pending'])==4 and record['accepted']['independent_users']==0
    assert main(['readiness','--feedback',str(folder/'feedback.json')]) == 4


@pytest.fixture
def synthetic_acceptance(prepared, tmp_path, monkeypatch):
    """All declarations are synthetic fixture data, never real independent people/model observations."""
    import mimo_harness.product_acceptance as acceptance
    monkeypatch.setattr(acceptance,'TRIAL_CASES',('local-fixture','second-fixture'))
    monkeypatch.setitem(CASES,'second-fixture',dict(prepared.spec))
    program=tmp_path/'fixture_codex.py'
    patch=diff(ORIGINAL,ORIGINAL.replace('a - b','a + b'))
    program.write_text(f'''import json,sys
if sys.argv[-1]=='--version':
    print('codex-cli {CODEX_VERSION}');raise SystemExit(0)
response={{'schema_version':1,'status':'completed','patch':{patch!r}}}
for e in [{{'type':'turn.started'}},{{'type':'item.completed','item':{{'type':'agent_message','text':json.dumps(response)}}}},{{'type':'turn.completed','usage':{{'input_tokens':0,'output_tokens':0}}}}]:print(json.dumps(e))
''',encoding='utf-8')
    monkeypatch.setenv('CODEX_API_KEY','fake-unit-fixture-key')
    agent=tmp_path/'agent.json';profile(agent,argv=[sys.executable,str(program)],timeout_seconds=10)
    plan=tmp_path/'plan.json'
    assert main(['trial-init','--agent-config',str(agent),'--output',str(plan)]) == 0
    data=json.loads(plan.read_text());data.update(spending_allowance=1,operator='synthetic unit fixture',spending_cap_location='synthetic unit fixture only')
    atomic_json(plan,data)
    trial=tmp_path/'trial'
    assert main(['trial-run','--plan',str(plan),'--output',str(trial)]) == 0
    record=json.loads((trial/'trial.json').read_text())
    assert record['status']=='PASS' and 'operator_declared' in record['origin_evidence']
    assert all(row['ledger']['invocations_reserved']==1 for row in record['cases'])
    assert all(row['usage']['input_tokens']==0 for row in record['cases'])
    assert main(['case-check','--directory',str(prepared.root),'--name','third-fixture-run']) == 0
    bundles=[trial/row['bundle'] for row in record['cases']]+[prepared.root/'results/third-fixture-run/reference.zip']
    feedback=[]
    for i,bundle in enumerate(bundles):
        folder=tmp_path/f'user-fixture-{i}'
        assert main(['walkthrough-init','--output',str(folder)]) == 0
        path=folder/'feedback.json'; item=json.loads(path.read_text())
        item.update(tester_id=f'fixture-{i}',independence_confirmed=True,observed_at='2026-10-18',environment='synthetic unit fixture',
                    steps={step:True for step in STEPS},evidence_bundle=str(bundle))
        atomic_json(path,item);feedback.append(path)
    repo=Path(prepared.spec['repository'])
    (repo/'LICENSE').write_text('Test fixture declaration; not a release license choice.\n')
    _git(['add','LICENSE'],repo)
    _git(['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','Fixture notice'],repo)
    ci=tmp_path/'ci.json';atomic_json(ci,{'schema_version':1,'commit':_git(['rev-parse','HEAD'],repo),'tree':_git(['rev-parse','HEAD^{tree}'],repo),
        'jobs':[{'name':name,'conclusion':'success'} for name in sorted(REQUIRED_JOBS)]})
    args=['readiness','--repository',str(repo),'--license-id','TEST-FIXTURE','--ci-record',str(ci),'--trial',str(trial/'trial.json')]
    for path in feedback:args+=['--feedback',str(path)]
    return args,trial,feedback,ci,bundles


def test_source_bound_acceptance_and_duplicate_user_evidence(synthetic_acceptance,capsys):
    args,trial,feedback,ci,bundles=synthetic_acceptance
    capsys.readouterr()
    assert main(args)==0
    report=json.loads(capsys.readouterr().out)
    assert report['status']=='READY_FOR_MAINTAINER_REVIEW' and report['accepted']['independent_users']==3
    assert 'declared' in report['attestation_boundary']
    item=json.loads(feedback[2].read_text());item['evidence_bundle']=str(bundles[0]);atomic_json(feedback[2],item)
    assert main(args)==4


def test_reference_patch_bundle_cannot_be_relabelled_live_model(synthetic_acceptance):
    args,trial,feedback,ci,bundles=synthetic_acceptance
    path=trial/'trial.json';record=json.loads(path.read_text())
    reference=trial/'reference.zip';reference.write_bytes(bundles[-1].read_bytes())
    record['cases'][0].update(bundle='reference.zip',bundle_sha256=file_digest(reference))
    atomic_json(path,record)
    assert main(args)==4


def test_missing_required_ci_jobs_cannot_produce_release_acceptance(synthetic_acceptance):
    args,trial,feedback,ci,bundles=synthetic_acceptance
    record=json.loads(ci.read_text());record['jobs']=record['jobs'][:2];atomic_json(ci,record)
    assert main(args)==4
