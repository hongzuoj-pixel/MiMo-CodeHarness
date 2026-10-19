# 把同一份可验证安装包交给三位新用户

v0.12 beta 提供 `walkthrough-pack`，把当前引擎对应的 wheel、固定依赖、跨平台安装脚本、中文指南和三份空白反馈放在同一个目录。三位使用者安装完全相同的引擎文件，避免各自重建 wheel 带来身份差异。生成材料或自动演练不计为真人试用。

## 维护者准备

在准备交付的干净 checkout 中构建并安装 wheel：

```bash
python -m pip install build==1.3.0
python -m build --outdir .mimo/dist
python -m pip install --force-reinstall --no-deps .mimo/dist/mimo_codeharness-0.12.0-py3-none-any.whl
python -m pip download --only-binary=:all: --platform any --implementation py --abi none --dest .mimo/kit-wheels pip==25.0.1 pytest==8.4.2 iniconfig==2.1.0 packaging==25.0 pluggy==1.6.0 pygments==2.19.2 exceptiongroup==1.3.0 tomli==2.2.1 colorama==0.4.6 typing_extensions==4.15.0
python -c "import shutil; shutil.copy2('.mimo/dist/mimo_codeharness-0.12.0-py3-none-any.whl', '.mimo/kit-wheels')"
mimo walkthrough-pack --wheelhouse .mimo/kit-wheels --output .mimo/user-kit
```

准备依赖需要联网；输出目录必须是新目录。wheel 必须与当前 CLI 的版本和引擎字节一致；缺依赖、重复版本、平台专用 wheel 和不同引擎都会被拒绝。安装时使用这些确切 wheel，不从在线索引解析新依赖。

CI 的 Linux/Windows `cli-install` 任务从真实构建产物生成试用包，并演练新环境安装、拒绝覆盖、半成品虚拟环境恢复、保留反馈、真实 boltons 对照验收、手动恢复和迁移后的 ZIP 门禁。选择一份可信 CI 的 `independent-user-kit-ubuntu-latest` 或 `independent-user-kit-windows-latest` artifact 下载；解压后即可看到 bootstrap.py 和 GUIDE_CN.md。原样分发同一份包给三位使用者，不要混合不同任务中重建的 wheel。技术演练记录 rehearsal.json 另保留在 `cli-pytest-*` artifact 中。

## 新用户操作

使用者只需预装 Python 3.10+ 和 Git，在试用包目录运行：

```text
python bootstrap.py --participant user-01 --output my-study
```

另外两人选择 user-02、user-03。这些名称只是材料分配槽位，真实匿名 tester_id 由使用者自己填写。

安装脚本先校验传输文件，在新目录创建 `.venv`，仅离线安装包内依赖并核对实际安装的引擎；不修改系统 Python，不需要激活环境。随后进入 my-study，按照 GUIDE_CN.md 使用 `python mimo.py ...` 完成流程。Git 下载固定上游源码需要访问 GitHub，离线安装不等于整个真实案例都不需要网络。

安装失败或中断后，使用相同参数加 `--resume`。每次尝试保留独立安装日志，技术安装状态写入 setup.json。已有 feedback.json 和报告不会被自动填写或覆盖；无法恢复时保留原目录并使用新输出目录。

试用者提交自己填写的 feedback.json、自己运行生成的 evidence.zip 及必要日志。反馈仍要经过 [readiness](PRODUCT_ACCEPTANCE_CN.md) 检查和人工独立性复核。包中哈希和 CI JSON 均未签名，用于检查版本/传输一致性；请从可信维护者和 CI 取得材料。

汇总验收的维护者也应安装这份包中的同一 wheel，再运行 readiness。仅版本号相同、分别重建的代码文件未必具有相同的引擎身份。

本流程不调用模型、不设置模型额度，也不替维护者选项目许可证。[真实模型和最终发布验收](PRODUCT_ACCEPTANCE_CN.md) 仍单独进行。
