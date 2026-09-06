---
name: codex-reviewer
description: Independent, read-only Codex CLI second opinions. Use when the user asks for Codex as reviewer, an independent AI review (獨立審查、第二意見), or an authorized workflow requires this skill. Not for ordinary code or PR review, implementation, explanation, or recursive reviewer calls.
---

# Codex Reviewer

以獨立 Codex CLI 程序提供有證據的第二意見。Reviewer 只讀；主代理核實結果，依使用者既有授權決定後續動作。

## 呼叫邊界

- 一般局部修改由主代理自行檢查。使用者要求獨立第二意見，或已授權流程明確指定此關卡，才使用本技能。
- 維護本技能時用本地測試與靜態檢查；只有 caller 明確要求單次限定範圍驗證才啟動真實 reviewer。Reviewer 內不得再呼叫任何 reviewer。
- 使用 helper 保持 read-only、ephemeral、approval never；不繞過 sandbox，也不由 reviewer 提交、推送或部署。
- helper 的 single-flight lock 保護所有 scope repo；看到仍在執行就等原程序，不另開 fallback 或重試。

## 選擇模式與範圍

讀專案規則、Git 狀態與目標差異，固定 base/head 或確切檔案範圍。

| 需求 | 模式 |
|---|---|
| 窄範圍、主代理可提供足夠程式與驗證證據 | `bounded-review`，預設 `standard` |
| 完整 Git 差異與穩定 JSON 結果 | `structured-review` |
| caller 明確指定 Codex 內建審查規則 | `native-review` |
| 自訂規格、架構或其他條件 | 對應 generic 模式，按需讀操作細節 |

Bounded 禁止 child tools；若內容不足，就列證據缺口，不臆測通過。Native 無法把共用提示加入 Git scope，不宣稱與 generic 相同。模型依 catalog 驗證，保留 `standard`；只有明確要求才升級或啟用額外代理。

## 預設審查重點

Bounded 與 generic 模式已注入共用提示，不必再貼同一份清單：優先檢查本次變更的副作用、相容性、邊界、效能及安全。命名、測試與維護問題須連到具體行為缺陷；每項附觸發條件、影響、最小檔案／行號證據及最小修法。忽略純風格、推測與無關既有問題，不擴大範圍填滿清單。

## 執行

```bash
SKILL_DIR="${CODEX_REVIEWER_SKILL_DIR:-$HOME/.agents/skills/codex-reviewer}"
python3 "$SKILL_DIR/scripts/codex_review.py" bounded-review \
  --cd /path/to/repo \
  --bounded-scope /tmp/review-scope.json \
  --evidence-json /tmp/review-evidence.json \
  --preset standard \
  --result-json /tmp/review-result.json
```

先由 caller 完成必要測試，再將實際結果放進 evidence。需要 packet 格式時讀 [README 的 bounded 範例](README.md#bounded-review-contract)；其他模式、跨 repo、自訂 schema、模型與平台限制見 [操作細節](references/operation-details.md)，只讀所需章節。

CLI 預設只沿用現有相容版本；缺少或過舊時回報，不自動安裝／更新。`doctor` 預設不更新；`--dry-run` 一律不更新。只有已授權 CLI 維護才加 `--update-check`（保留快取）或 `--force-update-check`（忽略快取），詳見 [更新政策](references/codex_cli_reference.md#安裝來源與自動更新)。

## 完成、採納與複查

- 等待同一程序終態；中途訊息、JSONL error item 或 skills budget 警告本身不能判定完成／失敗。Structured 結果須通過 schema 驗證；逾時、policy violation、取消或缺少終態都不是通過。
- 主代理逐項核對觸發條件、影響與 source，排除範圍外、既有、純風格及無證據問題。一般第二意見可記錄採納／不採納及具體理由後收斂；未改程式或關鍵證據，不因不採納 P2 而重跑相同內容。
- 原始 gate 不因主代理意見而改寫：P0–P2 仍為 `blocked`，僅 P3 為 `passed_with_warnings`。正式品質關卡用 `--enforce-gate`，依專案要求修正、複查或取得例外；主代理不能把原始 blocked 回報成 passed。
- 修正重要問題後只複查受影響範圍；只有證據不足且 caller 明確同意才升級 `deep`。Timeout 不用換 mode／preset 重試，先診斷並縮小範圍。
- 簡短回報已確認問題、主代理處置、實際驗證與限制。沒有 finding 不代表已跑測試；partial progress 不當成驗收。Quick 只供初步篩查，不當正式關卡。

需要 CLI 相容性查證時讀 [CLI 參考](references/codex_cli_reference.md)；需要 generic prompt 或 JSON 格式時分別讀 [提示範例](references/example_prompts.md)、[內建 schema](references/review_output_schema.json)。
