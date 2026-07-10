# Codex CLI Reviewer v2 參考

本文件以 `codex-cli 0.144.1` 為基準，整理 reviewer 所需的模型、命令與已知邊界。執行前仍要以本機 `codex --version`、`codex exec review --help` 與 `codex debug models` 為準。

## 目錄

- [模式選擇矩陣](#模式選擇矩陣)
- [模型與推理層級](#模型與推理層級)
- [命令形狀](#命令形狀)
- [Native review 邊界](#native-review-邊界)
- [JSONL 與 structured output](#jsonl-與-structured-output)
- [V2 profile](#v2-profile)
- [安全與隔離](#安全與隔離)
- [Context 與大型 diff](#context-與大型-diff)
- [診斷](#診斷)
- [官方來源](#官方來源)

## 模式選擇矩陣

| 需求 | Native `codex exec review` | Generic `codex exec` | 選擇 |
|---|---:|---:|---|
| 精確審查 base branch、commit、未提交變更 | 是 | 需在 prompt 定義 | Native |
| Codex 內建 bug rubric 與 P0-P3 findings | 是 | 需自行提供 | Native |
| 自訂審查 criteria 或檔案集合 | scope 不能再帶 custom prompt | 是 | Generic |
| 任意 commit range | 無原生 range flag | 是 | Generic |
| `--output-schema` 強制 final JSON | 0.144.1 會接受但忽略 | 是 | Generic |
| 圖片輸入 | 0.144.1 會接受但忽略 | 是 | Generic |
| Live web search | reviewer child 強制停用 | 是 | Generic |
| Ultra / subagents | reviewer child 關閉 collaboration | 可依模型能力使用 | Generic only |
| JSONL 進度與 usage | 是 | 是 | 兩者皆可 |
| Ephemeral session | 是 | 是 | 預設啟用 |

Native review 適合標準變更審查。Generic review 適合規格對照、架構、安全、圖片、搜尋或需要穩定 schema 的流程。

## 模型與推理層級

先查目前帳號與 CLI 的實際 catalog：

```bash
codex debug models | jq '.models[] | {
  slug,
  default_reasoning_level,
  supported_reasoning_levels,
  context_window,
  max_context_window,
  effective_context_window_percent,
  input_modalities,
  supports_search_tool,
  additional_speed_tiers
}'
```

`codex-cli 0.144.1` 的 catalog：

| Model | Reviewer 定位 | Reasoning | CLI context |
|---|---|---|---:|
| `gpt-5.6-sol` | 複雜、高價值 review | `low` 到 `max`，另有 `ultra` | 372,000 |
| `gpt-5.6-terra` | 日常或 quick review | `low` 到 `max`，另有 `ultra` | 372,000 |
| `gpt-5.6-luna` | 明確、重複、低成本工作 | `low` 到 `max` | 372,000 |
| `gpt-5.5` | 5.6 不可用時 fallback | `low` 到 `xhigh` | 依 catalog |

Reviewer 建議：

- Quick：`gpt-5.6-terra` + `medium`。
- Standard：`gpt-5.6-sol` + `high`。
- Deep：`gpt-5.6-sol` + `max`。
- Ultra：只在 generic review、明確可平行拆解且使用者接受額外 usage 時啟用。

推理設定的正確 key 是 `model_reasoning_effort`：

```bash
-c 'model_reasoning_effort="high"'
```

不要使用舊的 `reasoning_effort`。不要依賴 model default；官方頁面與特定 CLI catalog 可能有 rollout 差異。

## 命令形狀

### Native branch review

```bash
codex --ask-for-approval never \
  --model gpt-5.6-sol \
  --sandbox read-only \
  -c 'model_reasoning_effort="high"' \
  exec review \
  --ephemeral \
  --json \
  --base main
```

Native scope 必須四選一：

- `--base <BRANCH>`
- `--commit <SHA>`，可搭配 `--title <TITLE>`
- `--uncommitted`
- custom positional prompt

`--base`、`--commit`、`--uncommitted` 與 custom prompt 彼此互斥。`--uncommitted` 包含 staged、unstaged 與 untracked files。

### Generic structured review

```bash
codex --ask-for-approval never \
  --model gpt-5.6-sol \
  --sandbox read-only \
  -c 'model_reasoning_effort="high"' \
  exec \
  --ephemeral \
  --json \
  --output-schema "$HOME/.agents/skills/codex-reviewer/references/review_output_schema.json" \
  --output-last-message /tmp/codex-review.json \
  - < /tmp/review-prompt.md
```

把大型或含敏感內容的 prompt 走 stdin，不要把完整 prompt 放入 process list 或 diagnostic command output。

### Live search 與圖片

Live search 是 root-level flag：

```bash
codex --search exec ...
```

圖片使用 generic exec：

```bash
codex exec --image=/absolute/path/evidence.png ...
```

Search 與圖片都不得用來繞過 scope；只有當 review 真正需要現行外部事實或視覺證據時才啟用。

## Native review 邊界

`codex-cli 0.144.1` 的 native reviewer 會建立 child review session，套用內建 rubric、強制 `approval_policy=never`，並關閉 web search、Collab 與 MultiAgentV2。

CLI help 會在 `codex exec review` 顯示 `--output-schema`，exec parser 也會接受 image flag；但 0.144.1 的 Review branch 不載入 `output_schema_path`，也不把 images 組進 review input。文件與 wrapper 應以實作行為為準，而不是只看 parser 是否接受。

內部 reviewer 會產生 `ReviewOutputEvent`，但 exec JSONL 的簡化 mapper 不暴露 `ExitedReviewMode.review_output`。CLI 使用者拿到的是渲染後的 agent message，不是 raw native struct。

因此：

- Native review 不要宣稱支援 schema、image、search 或 Ultra。
- 需要上述能力時切換 generic review。
- Native deep review 使用 Sol + `max`；不要用 Ultra。

## JSONL 與 structured output

`--json` 會把 stdout 轉為 JSONL event stream。常見事件：

- `thread.started`
- `turn.started`
- `item.started`
- `item.updated`
- `item.completed`
- `turn.completed`
- `turn.failed`
- `error`

`turn.completed.usage` 使用以下欄位：

```json
{
  "input_tokens": 24763,
  "cached_input_tokens": 24448,
  "output_tokens": 122,
  "reasoning_output_tokens": 0
}
```

只需要 final message 時使用 `-o` / `--output-last-message`。需要穩定 JSON 時，generic exec 同時使用：

- `--json`：保留事件、錯誤與 usage。
- `--output-schema <FILE>`：限制 final response shape。
- `--output-last-message <FILE>`：直接取得 final JSON。

`references/review_output_schema.json` 採用 native-compatible field names，但只保證 generic `codex exec` 的 schema enforcement。

Helper 的 `--result-json <FILE>` 另外寫入 v2 execution envelope，不取代 stdout final message。Envelope 包含選定的 absolute binary/version、scope 與 sizing metrics、model/effort/Fast tier、usage、timeout/timed-out、warnings、sanitized command、final result、exit code 與 error；stdin prompt 不會寫入 envelope。`--output` 仍只保存 raw Codex stdout/JSONL。

## V2 profile

目前 `--profile reviewer` 讀取的是 V2 profile file：

```text
$CODEX_HOME/reviewer.config.toml
```

不是舊式 `[profiles.reviewer]` table。範例：

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
model_verbosity = "low"
sandbox_mode = "read-only"
approval_policy = "never"
web_search = "disabled"
```

使用：

```bash
codex --profile reviewer exec "Review the current changes"
```

設定優先序由高到低：

1. CLI flags 與 `-c` overrides
2. Trusted project 的 `.codex/config.toml`
3. `$CODEX_HOME/<name>.config.toml`
4. `$CODEX_HOME/config.toml`
5. System config
6. Built-in defaults

Profile 適合個人預設；公開 skill 不應擅自建立或覆寫使用者的 `$CODEX_HOME/*.config.toml`。

## 安全與隔離

- Reviewer 固定 `read-only`，只產生意見，不套 patch。
- Non-interactive review 明確傳入 `--ask-for-approval never`，避免無人值守時卡在 prompt。
- 預設 `--ephemeral`，避免一次性 second opinion 汙染 session history。
- `--ignore-user-config` 可做 deterministic run，auth 仍使用 `CODEX_HOME`；但可能移除必要 provider 或 MCP 設定。
- `--ignore-rules` 會略過 user/project execpolicy，除非受控 CI 明確需要，否則不要預設啟用。
- 禁止 `--dangerously-bypass-approvals-and-sandbox`、`--full-auto`、`workspace-write` 與 `danger-full-access`。

## Context 與大型 diff

GPT-5.6 API model page列出 1.05M context，但本機 Codex catalog 對 5.6 models 配置 372K、effective 95%。Reviewer 以 CLI catalog 為 source of truth；不要把 API 上限硬寫進 `model_context_window`。

GPT-5.6 request 超過 272K input tokens 會套用 long-context 計價。大型 review 應先：

1. 固定 merge base 或 commit range。
2. 計算 staged、unstaged、untracked 的檔案與行數。
3. 按 task、模組或風險面拆分。
4. 先 quick review，再對高風險範圍做 deep review。

## 診斷

```bash
command -v codex
codex --version
codex exec review --help
codex doctor --json
codex debug models
codex debug models --bundled
codex features list
```

- `--strict-config`：遇到設定漂移時用來找出未知欄位；不必每次強制，否則較新 project config 可能阻斷 review。
- `codex doctor --json`：輸出已遮蔽的 installation、auth、config 與 runtime health report。
- `codex debug models`：refresh account-aware catalog；`--bundled` 只看 binary 內建 catalog。
- `codex debug prompt-input`：experimental，適合檢查 model-visible instruction layers。
- `-c 'service_tier="fast"'`：catalog 有 Fast tier 時降低 latency，但增加 usage，只能 opt-in。

## 官方來源

- [Codex Models](https://developers.openai.com/codex/models/)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference/)
- [Non-interactive Mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Configuration Reference](https://developers.openai.com/codex/config-reference/)
- [Config Basics](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Code Review](https://learn.chatgpt.com/docs/code-review)
- [GPT-5.6 Sol model](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [0.144.1 review task source](https://github.com/openai/codex/blob/rust-v0.144.1/codex-rs/core/src/tasks/review.rs)
- [0.144.1 exec routing source](https://github.com/openai/codex/blob/rust-v0.144.1/codex-rs/exec/src/lib.rs)
- [0.144.1 native rubric](https://github.com/openai/codex/blob/rust-v0.144.1/codex-rs/prompts/templates/review/rubric.md)
