# MiMo-CodeHarness v0.2 Upgrade Log

## 已完成

1. 新增 `src/run_mimo_codeharness_v02.py`，实现完整 Harness 主流程。
2. 新增 `config/api_models_mimo.json`，预留 Xiaomi MiMo Pro / OpenAI-compatible API 接入。
3. 新增 `config/api_models_mimo_dryrun.json`，用于离线 mock 验证。
4. 新增 `src/validate_mimo_codeharness_v02.py`，一键验证 v0.2 流程是否能跑通。
5. 支持更多语言：ArkTS/TypeScript/JavaScript/Python/C/C++/Java/Kotlin/Go/Rust/Shell/Config/Docs/Web。
6. 支持真实 patch：提取 diff，执行 `git apply --check`，可选在隔离 worktree 中真实应用。
7. 支持 build/test：内置安全检查，并预留真实 pytest/npm/go/cargo 执行入口。
8. 支持 dashboard：自动输出 `dashboard.html`。
9. 支持技术报告和论文雏形：自动输出 `TECHNICAL_REPORT.md` 与 `PAPER_DRAFT.md`。

## 本地验证结果

已运行：

```bash
python src/validate_mimo_codeharness_v02.py
```

验证结果：

```text
validated: true
run_count: 6
build_all_critical_ok: true
```

## 下一步

1. 在用户本机设置 `MIMO_API_KEY` 与 `MIMO_BASE_URL`。
2. 用 `--execute` 跑真实 MiMo Pro API。
3. 将 STM32 RFID、SlideNotes、HarmonyOS、DreamZero-Libero 作为真实 case studies。
4. 将输出报告整理为 GitHub README、技术报告和找老师邮件附件。
