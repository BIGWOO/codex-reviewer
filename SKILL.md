---
name: codex-reviewer
description: Use OpenAI Codex CLI as an independent, read-only second-opinion reviewer for local code changes, commits, branch diffs, PR/MR implementations, architecture decisions, security or performance risks, and spec-to-code gaps. Trigger only when the user explicitly asks to use Codex or Codex CLI as a reviewer, asks for an independent AI second opinion (獨立審查、第二意見), or a workflow explicitly mandates this skill as a quality gate after a complex high-risk implementation. Do not trigger for ordinary code review or PR review, implementation, code explanation, or from inside an existing codex-reviewer run.
---

# Codex Reviewer

啟動獨立 Codex CLI process，唯讀檢查目標變更並回傳 second opinion。這個 skill 只產生審查意見；不要直接修檔、commit、push、merge 或 deploy。

## Guardrails

- 保持 `read-only`、CLI flag `--ask-for-approval never` 與 ephemeral session。
- 若目前任務已是此 skill 派出的 reviewer，立即停止遞迴；不要再呼叫 `codex-reviewer`、`codex exec review` 或其他 reviewer agent。
- 維護 reviewer 本身或執行純 review 任務時，以本地測試與靜態驗證完成；只有 caller 明確要求的單次 bounded forward-test 才啟動此 helper。
- 只審查 caller 指定的 scope。不要把 reviewer 輸出本身當成下一輪 review target。
- 同一 repo 與 scope 一次只執行一個 helper。看到 `still running` 時持續等待原 session；不要啟動 fallback 或重試。若 helper 回報已有 review 執行中，先等待或明確終止原 process。
- JSONL 的 `agent_message` 與 `item.type=error` 都不是終態；只有 `turn.completed`、`turn.failed`、timeout 或 process exit 才能決定結果。skills context budget 警告不代表 review 失敗。
- 安全審查只描述觸發條件、影響與防禦式修法；不要要求 exploit payload 或攻擊步驟。
- 不使用 `--dangerously-bypass-approvals-and-sandbox`、`--full-auto`、`workspace-write` 或 `danger-full-access`。

## CLI Install & Update Policy

每次 helper 啟動時先執行安裝來源感知的 update preflight；更新只作用於 Codex CLI，不得修改被審查 repo：

1. `--codex-bin` 或 `CODEX_REVIEWER_CODEX_BIN` 是明確 pin，完全照用且不自動更新。
2. 若偵測到 global npm `@openai/codex`，優先使用 npm 版本，即使 standalone 也存在。
3. 沒有 npm 版本時，使用官方 standalone；若尚未安裝則透過 OpenAI 官方 installer bootstrap。
4. 由選定 binary 的 `codex update` 判斷並更新原安裝來源；舊版不支援 update 時才用同來源 repair。
5. 成功檢查後快取 24 小時；失敗則退避 15 分鐘。離線時若既有 stable CLI 符合最低版本，警告後繼續。

`--no-update-check` 可單次停用；`--force-update-check` 可忽略快取。CI／離線環境可設 `CODEX_REVIEWER_AUTO_UPDATE=0`；TTL 可用 `CODEX_REVIEWER_UPDATE_TTL_SECONDS` 調整。

## Workflow

1. 先讀 `git status --short --branch`、目標 diff、相關規格與 repo instructions，固定 base/head 或 commit scope。
2. 選擇 native 或 generic mode；不要用 parser 接受旗標推論 native 真正支援能力。
3. 大型 diff 先按 task、module 或風險面拆分；先 quick，再對高風險範圍 deep review。
4. 使用 helper 執行並等待同一個 process 完成。只有在診斷 helper/CLI contract 時才直接組 raw `codex` command；不得根據中途訊息另開一輪。
5. 驗證每個 finding：必須有可重現條件、具體影響、最小檔案/行號證據，且確實落在本次 scope。
6. 整合成 findings-first 回覆；分開標示已確認問題、分歧、限制與未執行的測試。不要原樣貼整份 reviewer transcript。

## Mode Selection

| Need | Mode |
|---|---|
| Base branch、單一 commit、未提交變更，使用內建 rubric | `native-review` |
| 穩定 JSON schema | `structured-review` |
| 自訂 criteria、任意 range、規格、架構、安全或效能 | `custom`、`diff`、`focused` 或專用 generic type |
| 圖片或 live search | Generic only |
| Ultra / subagents | Generic only，且必須明確 opt-in |

`codex-cli 0.144.1` 的 native review 會忽略 output schema 與 images，並停用 web search、Collab 與 MultiAgentV2。Native scope 的 `--base`、`--commit`、`--uncommitted`、custom prompt 四者互斥；`--title` 只能搭配 `--commit`。

## Presets

| Preset | Selection |
|---|---|
| `quick` | Terra medium，fallback Sol medium，再 fallback GPT-5.5 medium |
| `standard` | Sol high，fallback GPT-5.5 high；預設 |
| `deep` | Sol max，fallback GPT-5.5 xhigh |
| `ultra` | Sol ultra；generic only，無 fallback |

Helper 會用 `codex debug models` 驗證 catalog。不要硬設 API context 上限，也不要依賴 model default。`--quick` 是 `--preset quick` 的 alias。

## Run

先解析 skill path：

```bash
SKILL_DIR="${CODEX_REVIEWER_SKILL_DIR:-$HOME/.agents/skills/codex-reviewer}"
```

Native branch review：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" native-review \
  --cd /path/to/repo \
  --base main \
  --preset standard
```

Structured deep review：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" structured-review \
  --cd /path/to/repo \
  --base main \
  --preset deep \
  --result-json /tmp/codex-review-result.json
```

Binary 或 auth 不確定時，先跑不呼叫模型的診斷：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" doctor \
  --result-json /tmp/codex-review-doctor.json
```

使用 `--dry-run` 檢查最後命令；使用 `--codex-bin /absolute/path/codex` 或 `CODEX_REVIEWER_CODEX_BIN` 選定並自行管理固定 binary。需要附加 repo-specific criteria 時用 `--instructions`，不要把 scope 與 prompt 偷混進 native positional argument。

`--isolated` 只等同 `--ignore-user-config --ignore-rules`；它不會停用 skill discovery、skills 或 plugins，也不可作為 review 失敗後的自動重試策略。

## Quality Gate

把 reviewer 當成獨立證據來源，不是裁決者：

- 對每個高風險 finding 重新讀 source 與 diff。
- 排除 pre-existing、scope 外、純風格與無法證明 downstream impact 的項目。
- 檢查 file path、line range、priority 與 confidence 是否合理。
- Quick pass 只供 triage，不算 quality gate 完成；交付前至少重跑 `standard`，高風險變更使用 `deep`。
- P0/P1 finding 阻擋交付。P2 必須修正，或記錄不修理由後針對該範圍重跑 reviewer。
- Reviewer 無 finding 時，仍回報未跑測試、環境限制與 residual risk。
- 若 structured output parse/schema validation 失敗，不要默默降級成「審查通過」。
- 只有 `turn.completed` 且 structured schema 驗證成功才算完成；中途符合 schema 的進度訊息仍不是 final result。

## References

- 需要 CLI、model、profile、native/generic matrix 或 diagnostic 時，讀 [references/codex_cli_reference.md](references/codex_cli_reference.md)。
- 需要 generic review prompt 時，讀 [references/example_prompts.md](references/example_prompts.md)，只載入對應 template。
- 需要 structured output 時，使用 [references/review_output_schema.json](references/review_output_schema.json)；它只供 generic exec enforcement。
