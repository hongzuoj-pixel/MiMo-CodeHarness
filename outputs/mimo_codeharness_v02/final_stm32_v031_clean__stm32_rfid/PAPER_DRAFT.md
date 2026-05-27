# Paper Draft: MiMo-CodeHarness

## Title
MiMo-CodeHarness: A Multi-Agent Evaluation Harness for Repository-Level Code Understanding in IoT and Embedded Software Projects

## Abstract
Large language models are increasingly used as coding agents, but many evaluations still focus on algorithmic questions or short code snippets. This draft proposes MiMo-CodeHarness, a lightweight multi-agent harness for evaluating repository-level code understanding on real-world projects. The system scans a repository, extracts multi-language dependency evidence, generates benchmark tasks, runs OpenAI-compatible models such as Xiaomi MiMo Pro, applies patch-style outputs inside isolated worktrees, executes build/test checks, and produces reproducible score reports with token usage logs. The current prototype supports HarmonyOS/ArkTS, Python, C/C++ embedded projects, JavaScript/TypeScript, configuration files, and documentation. Future experiments will use STM32 RFID, SlideNotes, HarmonyOS, and robotics research repositories as case studies.

## Proposed Contributions
1. A reproducible multi-agent harness for repository-level code evaluation.
2. A task-generation pipeline covering dependency reasoning, bug localization, test generation, patch proposal, and risk analysis.
3. A combined scoring method using model output quality, patch applicability, build/test status, and token cost.
4. Case studies across IoT/embedded software and AI research repositories.

## Experimental Plan
- Models: MiMo Pro, DeepSeek Coder, GPT-compatible baselines.
- Repositories: STM32 RFID Access Control, SlideNotes GUI, HarmonyOS demo, DreamZero-Libero light analysis.
- Metrics: correctness, dependency understanding, patch applicability, build/test pass rate, maintainability score, token cost, latency.

## Current Prototype Status
This v0.2 implementation has completed the offline harness pipeline and is ready for real MiMo API execution after setting environment variables.
