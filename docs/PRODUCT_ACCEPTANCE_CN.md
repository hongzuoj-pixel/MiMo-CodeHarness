# 从当前版本走到最终产品验收

以下步骤对应真实模型试跑、独立新用户试用和最终发布。自动化检查完成后，仍需如实收集外部证据。空模板、参考修复、模拟用户和本地协议服务器都不计为真实模型或真人试用。

## 1. 真实模型试跑

先按照 [Codex 指南](CODEX_AGENT.md) 安装固定版本 CLI，选择自己的真实模型并生成配置。密钥只放入执行环境的 `CODEX_API_KEY`，不要发送到聊天或提交到仓库。

```bash
mimo agent-init --model YOUR_MODEL_ID --output .mimo/codex-agent.json
mimo trial-init --agent-config .mimo/codex-agent.json --output .mimo/live-trial-plan.json
```

上述命令不调用模型。打开计划文件，填写：

- `spending_allowance`：明确允许的正数美元额度，默认 `null` 不可执行；
- `operator`：试跑负责人；
- `spending_cap_location`：实际设置供应商/账户限额的位置与说明。

CLI 可能发出多次内部模型请求，Harness 的两次调用预留和超时不是账单上限。请实际核对供应商的限额能力，不能把预算提醒当作强制限额；没有可接受的费用控制时先保留计划。该限额说明由操作者确认，软件不会声称已经核验账单或远端限额。

配置、密钥和限额准备好后，明确执行：

```bash
mimo trial-run --plan .mimo/live-trial-plan.json --output .mimo/live-trial-001
```

这会调用真实 Agent。它准备 boltons 和 ms 的固定源码与独立测试，每个案例最多预留一次 CLI 调用、只跑一轮修复；验证完整累计补丁并输出 ZIP 和 `trial.json`。原始 Agent 配置不修改。失败或中断保留证据；已预留调用不退款。重复运行需新的输出目录，是新的独立预算，不自动继承旧试跑限额。

记录包含源码/测试身份、实际 CLI/模型、每个案例结果、调用预留和可用的 CLI 用量。用量缺失仍标记 unavailable，不填零；无法据此证明真实账单。远端提供方身份属于操作者声明，完成状态和独立测试证据由软件核验。回环协议服务器、普通参考补丁 ZIP 和未完成结果不能被重新标记为真实模型通过。

输入/引擎发生变化时应重新生成并审阅计划。旧模型结果不能充当修改后版本的最终验收。

## 2. 至少三位独立新用户试用

推荐先按[独立用户试用包指南](INDEPENDENT_USER_KIT_CN.md)准备同一份 wheel 和离线依赖，让三位使用者获得一致的引擎。下面的 `walkthrough-init` 仍可用于已经安装好相同版本的环境。

为每位未参与实现的使用者生成一份独立材料：

```bash
mimo walkthrough-init --output .mimo/user-01
```

目录包含中文 `GUIDE_CN.md` 和空白 `feedback.json`。真实使用者独立完成安装、配置、执行、理解失败、恢复、导出与离线门禁。记录匿名标识、观察时间、系统环境、卡点及问题是否解决，并提交自己运行产生的参考证据 ZIP。默认步骤全部未完成，独立性未确认，因此生成材料不等于完成一次试用。

三位使用者应分别运行，不能复制同一个 ZIP 当作三份证据。`readiness` 会拒绝重复标识/运行、未完成步骤、未解决问题、损坏证据和旧引擎记录。人的独立性仍需实际组织和人工核对，JSON 格式检查不能认证真人身份。

## 3. 发布检查与最终复核

[支持范围与升级说明](SUPPORT_AND_UPGRADES.md) 定义当前边界。维护者选定项目许可证后，将正式文本放入根目录 `LICENSE`；第三方许可通知已经单独保留，不能替代项目许可决定。

CI 最后的 `release-automation` job 汇集同一源码提交/树上的全部自动化结果，输出 `source-bound-release-automation` artifact 中的 `ci.json`。它只证明自动化检查通过；不会宣布真人或付费模型验收完成。

在相同版本的干净 checkout 中，下载所需证据并执行：

```bash
mimo readiness --repository . --ci-record CI_JSON --trial TRIAL_JSON --feedback USER_01_JSON --feedback USER_02_JSON --feedback USER_03_JSON --license-id MAINTAINER_SELECTED_ID
```

返回码 `0` 表示证据齐备，可交维护者最终审阅；`3` 表示仍有未完成项；`4` 表示输入损坏、不一致或不合格。运行 `mimo readiness` 可先查看当前缺项。检查会重算 ZIP 门禁、对照引擎、CI 提交/树和必需 job 集合。CI JSON 与人工声明均未签名，必须从可信来源取得；它不是第三方签名证明或许可证法律审核。

最终签收还需人工审阅真实问题是否解决、独立性和许可权利，确认支持边界，并在最终功能修改后重新收集有效证据。创建 GitHub Release、发布 PyPI、组织级权限和硬件/Keil 验收均不由此命令自动执行。
