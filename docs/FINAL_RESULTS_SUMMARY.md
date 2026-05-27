# Final Clean Results Summary

This file summarizes the final clean evaluation results used in the GitHub README.

## Final clean case studies

| Case Study | Repository Type | Files | Dependency Edges | Tasks | Avg. Score | Estimated Tokens | Status |
|---|---:|---:|---:|---:|---:|---:|---|
| STM32 RFID Access Control | Embedded C / IoT | 16 | 86 | 6 | 78.76 | 41,520 | OK |
| SlideNotes GUI Tool | Python GUI / Document Export Tool | 91 | 505 | 6 | 66.71 | 38,675 | OK |

## Recommended directories

```text
outputs/mimo_codeharness_v02/final_stm32_v031_clean__stm32_rfid/
outputs/mimo_codeharness_v02/final_slidenotes_v031_clean__slidenotes/
```

## Scoring note

The reported final clean scores use the evidence-aware automatic scoring system from v0.3/v0.3.1.

LLM Judge and human review scripts were explored experimentally, but they are not used as the final reported scoring basis in these clean results.

## Current limitation

Build/test checks are lightweight. For STM32, the current check does not replace a full Keil/ARM firmware build.
