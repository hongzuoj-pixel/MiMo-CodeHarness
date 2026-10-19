"""Build a portable, exact-engine user-study kit; no model or user is simulated."""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace

from mimo_harness import __version__
from mimo_harness.manifests import atomic_json, engine_identity, engine_inventory, file_digest

ALLOWED_PACKAGES = {"mimo-codeharness", "pip", "pytest", "iniconfig", "packaging", "pluggy", "pygments",
                    "exceptiongroup", "tomli", "colorama", "typing-extensions"}
REQUIRED_PACKAGES = ALLOWED_PACKAGES - {"typing-extensions"}

GUIDE = """# MiMoHarness 独立用户试用

本包用于 v0.12 beta 的独立试用，不代表正式发布验收完成。安装、参考补丁和自动化演练均不算真人反馈，不会调用模型。

## 开始

先安装 Python 3.10+ 和 Git。三位使用者使用同一份试用包，各选一个不同的 participant 名称。不要混用不同平台重新构建的 wheel。

在试用包所在目录运行（把 user-01 换成分配给你的 user-02 或 user-03）：

```text
python bootstrap.py --participant user-01 --output my-study
```

脚本校验文件后，在新目录创建独立 Python 环境，仅从包内 wheels 安装依赖；不会修改系统 Python，也不需要激活虚拟环境。安装不联网。真实源码准备阶段需要 Git 能访问 GitHub。

中断或安装失败后，保留目录并添加 `--resume` 重试；setup.json 和 install-NNN.log 用于诊断。已有 feedback.json 不会被覆盖。不需要模型密钥，不要把密钥写入反馈。

## 实际操作

进入刚生成的 my-study 目录，依次执行：

```text
python mimo.py --version
python mimo.py case-init --case boltons-write --output case
python mimo.py --config case/mimo-harness.json doctor
python mimo.py case-check --directory case --name first
```

查看 case/mimo-harness.json 和目标测试。最后一条命令验证参考修复 PASS、故意回归 FAIL、自动恢复及导出；这是参考对照，不是模型修复。

再运行一次相同的 case-check 命令，预期退出码 4 并拒绝覆盖。确认原有证据仍存在。新验收使用新名称。

独立练习恢复和导出：

```text
python mimo.py resume --run case/runs/first-reference --name manual-recovery
python mimo.py export --run case/runs/manual-recovery --output evidence.zip
python mimo.py gate --bundle evidence.zip --json
```

恢复会重新运行基线和候选检查。把 evidence.zip 复制到另一个目录，再用 gate 检查；解压后打开 report.html。原始参考证据也位于 case/results/first/reference.zip。

## 如实反馈

实际完成后编辑本目录 feedback.json，填写匿名 tester_id、observed_at、environment 和遇到的问题，逐项记录步骤。只有独立操作后才填写 independence_confirmed=true。evidence_bundle 填 evidence.zip；每位使用者提交自己运行的 ZIP。

尚未完成或未解决的问题保持 false。不要把自动安装的 setup.json 当作反馈完成证明。将 feedback.json、evidence.zip 和必要的安装日志交还维护者；卡住时也应记录并反馈。

文件哈希用于检查传输损坏和版本一致性；包和反馈没有数字签名，请从可信维护者取得。代码、真实模型效果和真人独立性分别验收。
"""


def inspect_wheel(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Kit wheels must be regular files smaller than 64 MiB.")
    try:
        with zipfile.ZipFile(path) as wheel:
            names = wheel.namelist()
            if len(names) != len(set(names)) or sum(i.file_size for i in wheel.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Invalid/oversized wheel inventory.")
            metadata = [n for n in names if n.endswith('.dist-info/METADATA')]
            headers = [n for n in names if n.endswith('.dist-info/WHEEL')]
            if len(metadata) != 1 or len(headers) != 1:
                raise ValueError("Wheel metadata missing or ambiguous.")
            info = BytesParser().parsebytes(wheel.read(metadata[0]))
            tags = BytesParser().parsebytes(wheel.read(headers[0]))
            if tags.get('Root-Is-Purelib', '').lower() != 'true' or not any(
                    tag == 'py3-none-any' for tag in tags.get_all('Tag', [])):
                raise ValueError("User kits require portable py3-none-any wheels.")
            name = info.get('Name', '').lower().replace('_', '-')
            if name not in ALLOWED_PACKAGES or not info.get('Version'):
                raise ValueError("Unsupported kit dependency: " + name)
            if name == 'mimo-codeharness':
                actual = {n: hashlib.sha256(wheel.read(n)).hexdigest() for n in names
                          if (n.startswith('mimo_harness/') and n.count('/') == 1 and Path(n).suffix in {'.py', '.mjs'})
                          or n in {'run_mimo_codeharness_v02.py', 'scoring_v03.py', 'verification_v04.py'}}
                if info['Version'] != __version__ or actual != engine_inventory():
                    raise ValueError("Harness wheel does not match the current version/engine; rebuild or use its matching installed CLI.")
            return {"package": name, "version": info['Version'], "file": path.name, "sha256": file_digest(path)}
    except (zipfile.BadZipFile, KeyError, RuntimeError, EOFError) as exc:
        raise ValueError("Invalid wheel: " + path.name) from exc


def command_walkthrough_pack(args):
    from mimo_harness.product_acceptance import command_walkthrough_init
    wheelhouse = Path(args.wheelhouse).expanduser().resolve()
    if not wheelhouse.is_dir():
        raise ValueError("Wheelhouse directory missing.")
    paths = sorted(wheelhouse.iterdir())
    if not paths or any(p.suffix != '.whl' for p in paths):
        raise ValueError("Wheelhouse must contain only the reviewed portable wheels.")
    wheels = [inspect_wheel(p) for p in paths]
    names = [row['package'] for row in wheels]
    if len(names) != len(set(names)) or not REQUIRED_PACKAGES.issubset(names):
        raise ValueError("Wheelhouse needs one harness/pytest wheel and complete Linux/Windows/Python 3.10 dependencies; see the user-kit guide.")
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'wheels').mkdir()
    for path in paths:
        shutil.copy2(path, output / 'wheels' / path.name)
    shutil.copyfile(Path(__file__).with_name('study_bootstrap.py'), output / 'bootstrap.py')
    (output / 'GUIDE_CN.md').write_text(GUIDE, encoding='utf-8', newline='\n')
    participants = ['user-01', 'user-02', 'user-03']
    for slot in participants:
        folder = output / 'participants' / slot
        command_walkthrough_init(SimpleNamespace(output=str(folder)))
        (folder / 'GUIDE_CN.md').write_text(GUIDE, encoding='utf-8', newline='\n')
    files = {p.relative_to(output).as_posix(): file_digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
    atomic_json(output / 'kit.json', {"schema_version": 1, "kind": "independent_user_kit", "version": __version__,
        "engine_sha256": engine_identity(), "participants": participants, "wheels": wheels, "files": files,
        "human_trials_completed": 0, "model_called": False,
        "integrity_boundary": "Unsigned transfer/version integrity; participant labels are slots, not observed people."})
    print(f"[KIT] {output}\n[NEXT] Read GUIDE_CN.md; distribute this same kit to three independent users. No human/model acceptance recorded.")
    return 0
