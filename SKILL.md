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
- 任一審查涉及的 `cwd`、`--add-dir` 或 scope manifest repo 都套用 single-flight lock。看到 `still running` 時持續等待原 session；不要啟動 fallback 或重試。
- JSONL 的 `agent_message` 與 `item.type=error` 都不是終態；只有 `turn.completed`、`turn.failed`、timeout 或 process exit 才能決定結果。skills context budget 警告不代表 review 失敗。
- 安全審查只描述觸發條件、影響與防禦式修法；不要要求 exploit payload 或攻擊步驟。
- 不使用 `--dangerously-bypass-approvals-and-sandbox`、`--full-auto`、`workspace-write` 或 `danger-full-access`。

## CLI Install & Update Policy

預設只沿用現有相容 CLI，缺少或過舊就回報，不自動安裝／更新。
一般審查可用 `--update-check` 或 `CODEX_REVIEWER_AUTO_UPDATE=1` 明確啟用；`--no-update-check` 覆蓋環境設定。`--force-update-check` 啟用更新並忽略快取。
`doctor` 只有單次更新旗標才會更新；`--dry-run` 一律不更新。明確 binary pin 永不自動更新。
啟用後保留安裝來源、成功快取與失敗退避，詳見 [更新政策](references/codex_cli_reference.md#安裝來源與自動更新)。

## Workflow

1. 先讀 `git status --short --branch`、目標 diff、相關規格與 repo instructions，固定 base/head 或 commit scope。
2. 一般呼叫預設用 bounded-review；完整 Git 差異不適合窄 packet 時用 structured-review，讓下列預設審查重點確實傳入模型。只有 caller 指定 native-review 或明確要求內建 rubric 時才選 native；它不能在 Git scope 之外附加自訂提示，不宣稱已注入同一份重點。
3. 窄 tracer 優先使用 `bounded-review` + `standard`。只有 standard 已有終態但證據仍不足，才由 caller 明確升級同一窄 packet 為 `deep`；不要自動升級。
4. 使用 helper 執行並等待同一個 process 完成。只有在診斷 helper/CLI contract 時才直接組 raw `codex` command；不得根據中途訊息另開一輪。
5. 驗證每個 finding：必須有可重現條件、具體影響、最小檔案/行號證據，且確實落在本次 scope。
6. 整合成 findings-first 回覆；分開標示已確認問題、分歧、限制與未執行的測試。不要原樣貼整份 reviewer transcript。

## 預設審查重點

使用者不必額外貼檢查清單。Bounded、structured 與其他 generic 模式會自動傳入共用審查重點：

- 不只檢查語法與明顯錯誤；在宣告範圍內優先檢查隱藏副作用、相容性、邊界情況、效能及安全風險。
- 命名誤導、測試不足與維護成本，只有連到本次變更新增的具體行為缺陷或可證明的誤用風險時才回報；略過純風格與推測性建議。
- 每項問題附觸發條件、影響、最小檔案／行號證據及最小修法，按嚴重程度排序。證據不足就說明限制，不為填滿清單擴大範圍或反覆審查。
- 主代理核實後自行判斷哪些需要修復、哪些不採納，必要時簡述理由；已授權實作時採最小修改，純審查不自動改檔。Reviewer 本身始終唯讀；提交、推送及部署仍依既有授權邊界。

## Mode Selection

| Need | Mode |
|---|---|
| Base branch、單一 commit、未提交變更，使用內建 rubric | `native-review` |
| 穩定 JSON schema | `structured-review` |
| 精確檔案／range packet，禁止 child tools | `bounded-review` |
| 自訂 criteria、任意 range、規格、架構、安全或效能 | `custom`、`diff`、`focused` 或專用 generic type |
| 圖片或 live search | Generic only |
| Ultra / subagents | Generic only，且必須明確 opt-in |

已核對 `codex-cli 0.153.3` 原始碼：native review 仍忽略 output schema 與 images，並停用 web search、Collab 與 MultiAgentV2。Native scope 的 `--base`、`--commit`、`--uncommitted`、custom prompt 四者互斥；`--title` 只能搭配 `--commit`。Helper 同時指定 `model` 與 `review_model`，避免設定檔改變實際審查模型。

本機 CLI 0.153.2 已完成參數、模型清單檢查及 Astra standard 的限定範圍審查與問題修正複查；僅驗證此模式的執行流程，不代表所有模式或審查品質已驗收。0.153.3 仍只有原始碼核對，最低 stable CLI 維持 0.144.1。

## Presets

| Preset | Selection |
|---|---|
| `quick` | Astra medium，fallback Sol → GPT-5.5 medium |
| `standard` | Astra high，fallback Sol → GPT-5.5 high；預設 |
| `deep` | Astra xhigh，fallback Sol → GPT-5.5 xhigh |
| `ultra` | Astra ultra；generic only，無 fallback |

Helper 會用 `codex debug models` 驗證 catalog。`max` 不屬於任何 preset，只能明確指定，且限單一 repo、完整 sizing、最多 15 檔／1200 changed lines；不能搭配 `--allow-large-diff`。不要硬設 API context 上限，也不要依賴 model default。`--quick` 是 `--preset quick` 的 alias。

一般自動 preset 可依序備援並警告；明確指定 `--model` 時不得替換模型。Catalog 完全不可取得時，一般 preset 保留 GPT-5.5 保守備援；ultra 直接失敗。

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

Bounded standard review（scope JSON 由 caller 建立，tests 由主 agent 先執行）：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" bounded-review \
  --cd /path/to/repo \
  --bounded-scope /tmp/review-scope.json \
  --evidence-json /tmp/review-evidence.json \
  --preset standard \
  --enforce-gate
```

Binary 或 auth 不確定時，先跑不呼叫模型的診斷：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" doctor \
  --no-update-check \
  --result-json /tmp/codex-review-doctor.json
```

使用 `--dry-run` 檢查最後命令，不執行 CLI 更新；`doctor` 預設不更新，只有單次更新旗標才會更新。使用 `--codex-bin /absolute/path/codex` 或 `CODEX_REVIEWER_CODEX_BIN` 選定並自行管理固定 binary。需要附加 repo-specific criteria 時用 `--instructions`，不要把 scope 與 prompt 偷混進 native positional argument。

跨 repo 的 generic review 必須使用 `--scope-manifest <JSON>` 宣告每個 repo 的 `uncommitted`、`base`、`commit` 或 `range` scope。Deep custom review 必須提供 manifest 或 `--review-range`，避免未 sizing 的廣域審查。

預設 `--minimal-context` 會停用 plugins、apps 與 multi-agent，但不代表停用一般 skill discovery 或所有 MCP。Bounded mode 另以 JSONL policy 強制零 command／MCP／web／browser／collaboration tools。只有非 bounded review 確定需要完整 context 時才用 `--full-context`。

Bounded 啟動前另確認 CLI 能停用命令工具與設定搜尋停用；檔案修改及未知事件均不得判定通過。這是啟動控制加事後偵測，不代表所有工具在發生副作用前都能被攔截。Ctrl+C／SIGTERM 會先清除子程序再釋放鎖，結果為 `interrupted`／`inconclusive`，退出碼分別是 130／143。

自訂 `--schema` 需在使用者選定的 Python 環境安裝 `requirements-schema.txt`；不得在執行中自動安裝。缺套件、規則無效或外部參照都要在模型啟動前失敗；回覆需通過本機格式驗證。內建 schema 保持零額外依賴。輸出路徑與輸入衝突時不得寫入任何結果檔。

Windows 使用 UTF-8、原生檔案鎖與 Job Object 程序管理；納管失敗不得繞過控制重試。Windows 檔案沿用目錄存取權限，不以 `chmod` 宣稱私人權限。Ctrl+Break 視為取消；強制結束時不保證有結果檔。Windows 驗證狀態與無模型測試方式見 [README](README.md#windows-執行相容性)，macOS 測試不代表 Windows 實機通過。

`--ignore-user-config` 只忽略 base user config，仍保留 project rules；`--isolated` 則等同 `--ignore-user-config --ignore-rules`，兩者都不保證停用 skill discovery。Bounded mode固定使用 `--ignore-user-config` 並拒絕 `--isolated`。

`--max-tool-calls` 與 `--max-jsonl-bytes` 對既有模式預設 unlimited；bounded 固定零工具並預設最多 262144 JSONL bytes。Policy violation、timeout 或缺少終態都不得視為 final success。Scope 不變時禁止只替換 mode／preset／隔離旗標重試 timeout；先縮小 packet 或由 caller 明確決定下一步。

Bounded review 預設 `--hard-timeout 600` 並停用 idle timeout（effective `--idle-timeout 0`）；零工具推理可能長時間沒有 JSONL event，不能把靜默本身當成 hang。Native／其他 generic mode 維持 hard 300／idle 180。Caller 顯式傳入的 timeout 仍優先。

## Quality Gate

把 reviewer 當成獨立證據來源，不是裁決者：

- 對每個高風險 finding 重新讀 source 與 diff。
- 排除 pre-existing、scope 外、純風格與無法證明 downstream impact 的項目。
- 檢查 file path、line range、priority 與 confidence 是否合理。
- Quick pass 只供 triage，不算 quality gate 完成；窄 tracer 先跑 bounded `standard`，只有證據不足才明確升級 `deep`。
- P0–P2 finding 都是 `blocked`；僅 P3 為 `passed_with_warnings`。P2 必須修正，或記錄不修理由後針對該範圍重跑 reviewer。
- Reviewer 無 finding 時，仍回報未跑測試、環境限制與 residual risk。
- 若 structured output parse/schema validation 失敗，不要默默降級成「審查通過」。
- 只有 `turn.completed` 且 structured schema 驗證成功才算完成；中途符合 schema 的進度訊息仍不是 final result。
- Timeout envelope 的 `partial_progress` 只代表未驗證進度，不是完成結果；raw JSONL 只從 `--output` 取得。

## References

- 需要 CLI、model、profile、native/generic matrix 或 diagnostic 時，讀 [references/codex_cli_reference.md](references/codex_cli_reference.md)。
- 需要 generic review prompt 時，讀 [references/example_prompts.md](references/example_prompts.md)，只載入對應 template。
- 需要 structured output 時，使用 [references/review_output_schema.json](references/review_output_schema.json)；它只供 generic exec enforcement。
