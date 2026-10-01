# Codex CLI Reviewer v2 參考

本文件以 `codex-cli 0.159.3` 官方原始碼核對為基準，整理 reviewer 所需的模型、命令與已知邊界。執行前仍要以本機 `codex --version`、`codex exec review --help` 與 `codex debug models` 為準。

最低版本為 0.159.3。歷史實際執行與本次驗證的範圍見 [README 相容性與保護措施](../README.md#相容性與保護措施)；程式保留保守的版本提示，不把單一模式的執行或原始碼核對視為全面驗收。

## 目錄

- [模式選擇矩陣](#模式選擇矩陣)
- [安裝來源與自動更新](#安裝來源與自動更新)
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
| Packet-only 精確檔案／range，禁止 child tools | 否 | 是 | Bounded generic |
| Codex 內建 bug rubric 與 P0-P3 findings | 是 | 需自行提供 | Native |
| 自訂審查 criteria 或檔案集合 | scope 不能再帶 custom prompt | 是 | Generic |
| 任意 commit range | 無原生 range flag | 是 | Generic |
| `--output-schema` 強制 final JSON | 0.159.3 原始碼仍忽略 | 是 | Generic |
| 圖片輸入 | 0.159.3 原始碼仍忽略 | 是 | Generic |
| Live web search | reviewer child 強制停用 | 是 | Generic |
| Ultra / subagents | reviewer child 關閉 collaboration | 可依模型能力使用 | Generic only |
| JSONL 進度與 usage | 是 | 是 | 兩者皆可 |
| Ephemeral session | 是 | 是 | 預設啟用 |

Native review 適合標準變更審查。Generic review 適合規格對照、架構、安全、圖片、搜尋或需要穩定 schema 的流程。

## 安裝來源與自動更新

Helper 先依以下優先序選取 CLI，再檢查最低版本並在必要時安裝／升級：

1. `--codex-bin` 或 `CODEX_REVIEWER_CODEX_BIN`：使用者明確 pin，不自動更新。
2. Global npm `@openai/codex`：只要偵測到就優先沿用，不因 standalone 較新而切換來源。
3. 官方 standalone：從 PATH、`CODEX_INSTALL_DIR`、`$CODEX_HOME/packages/standalone/current` 或平台預設位置尋找。
4. 其他來源：只有停用更新或 standalone bootstrap 失敗時才作為相容 fallback。

審查與 doctor 預設先確保 stable CLI 至少 `0.159.3`；缺少或過舊就安裝／升級。沒有 npm 或 standalone 時，macOS/Linux 透過 `https://chatgpt.com/codex/install.sh`、Windows 透過 `https://chatgpt.com/codex/install.ps1` bootstrap 最新 standalone。既有 npm／standalone 呼叫選定 binary 的 `codex update`，保留原安裝管理器；完成後重新查版本，未達門檻就停止。

成功 update check 會以 binary path、版本及安裝來源快取 24 小時，但低於最低版本時不採用成功快取。失敗退避 15 分鐘，且不把 npm 使用者切換到 standalone；只有現有 stable CLI 已達門檻，才可帶 warning 繼續審查。

控制面：

```bash
# 符合最低版本後仍查更新，尊重快取
python3 scripts/codex_review.py --update-check doctor

# 單次略過安裝／更新，版本不足仍失敗
python3 scripts/codex_review.py --no-update-check doctor

# 立即重查
python3 scripts/codex_review.py --force-update-check doctor

# CI / offline：事先安裝符合門檻的 CLI
python3 scripts/codex_review.py --no-update-check doctor
```

必要升級預設啟用。`CODEX_REVIEWER_AUTO_UPDATE=1` 可在符合門檻後繼續定期更新；設為 `0` 只停用定期更新，不阻止必要升級。`--update-check`、`--force-update-check` 覆蓋環境設定；`--no-update-check` 完全停用更新，dry-run 與明確 binary pin 也不更新，但都不能繞過最低版本。三個更新旗標互斥。Python API 的 `check_updates=None` 使用上述政策，True 明確啟用，False 關閉（含 force）；審查仍在推理前檢查版本。

`CODEX_REVIEWER_UPDATE_TTL_SECONDS` 調整快取秒數；`CODEX_REVIEWER_UPDATE_CACHE` 指定 cache file。結果 envelope 的 `install_method` 與 `update`、doctor 的 `update` diagnostic 會記錄最終選擇與檢查狀態。

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

模型、推理等級與 `max` 限制統一維護於 [README Presets](../README.md#presets)。2026-10-01 本機 `codex-cli 0.159.3` 的 catalog 已列出 `gpt-6.1-sol`，支援 `low`、`medium`、`high`、`xhigh`、`max`、`ultra`；這只驗證模型清單，未執行該模型的真實審查。實際 context 與可用能力仍以每次 catalog 為準。

推理設定的正確 key 是 `model_reasoning_effort`：

```bash
-c 'model_reasoning_effort="high"'
```

不要使用舊的 `reasoning_effort`。不要依賴 model default；官方頁面與特定 CLI catalog 可能有 rollout 差異。

## 命令形狀

### Native branch review

```bash
codex --ask-for-approval never \
  --model gpt-6.1-sol \
  --sandbox read-only \
  -c 'model_reasoning_effort="high"' \
  -c 'review_model="gpt-6.1-sol"' \
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
  --model gpt-6.1-sol \
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

### Bounded packet review

```bash
python3 scripts/codex_review.py bounded-review \
  --cd /path/to/repo \
  --bounded-scope /tmp/scope.json \
  --evidence-json /tmp/evidence.json \
  --preset standard \
  --enforce-gate
```

主程序先以 Git object／diff 建立 canonical JSON packet；child stdin 只有該 packet，不再自行讀 Git或執行 tests。Bounded 固定 generic `--output-schema`、`--ignore-user-config`、minimal context、零工具與預設 262144-byte JSONL 上限。`deep` 只能由 caller 明確選擇，不會因風險標籤自動升級。

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

依 `codex-cli 0.159.3` 原始碼，native reviewer 會建立 child review session，套用內建 rubric、強制 `approval_policy=never`，並關閉 web search、Collab 與 MultiAgentV2。

CLI help 會在 `codex exec review` 顯示 `--output-schema`，exec parser 也會接受 image flag；但 0.159.3 的 Review branch 不載入 `output_schema_path`，也不把 images 組進 review input。文件與 wrapper 應以實作行為為準，而不是只看 parser 是否接受。

內部 reviewer 會產生 `ReviewOutputEvent`，但 exec JSONL 的簡化 mapper 不暴露 `ExitedReviewMode.review_output`。CLI 使用者拿到的是渲染後的 agent message，不是 raw native struct。

因此：

- Native review 不要宣稱支援 schema、image、search 或 Ultra。
- 需要上述能力時切換 generic review。
- Native deep review 使用 `gpt-6.1-sol` + `xhigh`；不要用 Ultra。`max` 只供明確指定的窄 scope。

Native child 優先讀取 `review_model`；helper 會明確覆寫為本次選定模型，不只設定外層 `--model`。

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

Helper 的 `--result-json <FILE>` 另外寫入精簡的 v2 execution envelope，不取代 stdout final message。`success` 保留執行完成語意；`execution_status`、`review_verdict`、`gate_status` 分開記錄 process、structured verdict 與 delivery gate。Envelope 另包含 duration、`terminal_event`、最後解析的 `last_event`、timeout 當下的 `silence_duration_ms`、event counts、raw bytes、policy violation，以及 bounded scope／packet／prompt hashes。Raw JSONL 只由 `--output` 保存；只有明確使用 `--include-events` 才會在 envelope 加入已遮蔽 events。Timeout 或 policy violation 的 `partial_progress` 永遠是未驗證進度，不是 final result。

`--enforce-gate` 的 exit contract：`passed`／`passed_with_warnings` 為 0、`blocked` 為 2、執行失敗／`inconclusive`／`not_evaluated` 為 1。P0–P2 都會 block；只有 P3 是 warning。取消例外：Ctrl+C 為 130、SIGTERM 為 143，結果為 `interrupted`／`inconclusive`。

自訂 `--schema` 需選填 `requirements-schema.txt`，以本機 jsonschema 驗證規則及結果；內建格式維持既有零依賴驗證。缺套件、未知格式版本及外部參照在推理前失敗，格式錯誤只回報欄位位置。安裝方式見 README。

## V2 profile

目前 `--profile reviewer` 讀取的是 V2 profile file：

```text
$CODEX_HOME/reviewer.config.toml
```

不是舊式 `[profiles.reviewer]` table。範例：

```toml
model = "gpt-6.1-sol"
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

Windows 的 helper 文字交換固定 UTF-8，輸出檔沿用 Windows 目錄存取權限。單次審查鎖使用 `msvcrt.locking`；Codex 啟動前必須成功加入 Job Object，再放行等待中的啟動器。取消／逾時終止作業物件內的子程序，主代理意外結束則由 `KILL_ON_JOB_CLOSE` 清理。這些控制不取代 Codex 唯讀沙箱；本次 Windows 原生測試已加入 CI，但尚未取得實機通過證據。

Bounded 啟動前驗證必要功能停用能力，另傳入 `--disable shell_tool` 及 `-c 'web_search="disabled"'`。JSONL 僅接受已知訊息、推理、狀態及錯誤事件；`file_change` 與未知事件不得判定通過。事後偵測不等於所有副作用都能在執行前攔截。輸入／輸出路徑衝突時不得寫任何輸出；取消後先回收子程序再釋放鎖。

- Reviewer 固定 `read-only`，只產生意見，不套 patch。
- Non-interactive review 明確傳入 `--ask-for-approval never`，避免無人值守時卡在 prompt。
- `--ignore-user-config` 只忽略 base user config；`--isolated` 另外忽略 user/project rules。兩者都不保證停用 skill discovery。
- `item.completed` 內的 `agent_message` 與 `error` 是進度事件，不是 terminal result。skills context budget 訊息應記為 warning。
- JSONL review 只有收到 `turn.completed` 才能成功；structured review 還必須通過 final JSON schema 驗證。
- Helper 對 `cwd`、每個 `--add-dir` 與 scope manifest repo 都使用 single-flight lock。任一 root 重疊時，等待或終止原 process 後再重試，不要平行啟動 fallback。
- 預設 `--ephemeral`，避免一次性 second opinion 汙染 session history。
- 所有 review 明確停用 hooks；一般／bounded review 停用 V1、V2 並設定 `agents.enabled=false`，避免 model catalog 的 V2 預設繞過 feature 開關。Generic ultra 才允許委派，由目前模型／provider 選擇 V1 或 V2，代理並行上限為 2。Minimal context 另停用 plugins／apps，full context 只恢復這兩者，不改變 hooks／委派限制。Bounded 另停用 `code_mode`、`code_mode_only`、`code_mode_host`，preflight 驗證開關，任何工具或未知 JSONL item 都是 policy violation。
- `--ignore-user-config` 可做 deterministic run，auth 仍使用 `CODEX_HOME`；但可能移除必要 provider 或 MCP 設定。
- `--ignore-rules` 會略過 user/project execpolicy，除非受控 CI 明確需要，否則不要預設啟用。
- 禁止 `--dangerously-bypass-approvals-and-sandbox`、`--full-auto`、`workspace-write` 與 `danger-full-access`。

## Context 與大型 diff

Reviewer 以選定 CLI 與帳號的即時 catalog 為準；不要把 API 上限或過去 catalog 的 context 數字硬寫進 `model_context_window`。大型 review 應先：

1. 固定 merge base 或 commit range。
2. 計算 staged、unstaged、untracked 的檔案與行數。
3. 按 task、模組或風險面拆分。
4. 需要低成本 triage 時先 quick；窄 tracer 正式 gate 先用 bounded standard，只有終態證據不足才明確升級 deep。

Generic 跨 repo review 使用 version 1 scope manifest：

```json
{
  "version": 1,
  "scopes": [
    {"repo": "/path/to/api", "kind": "base", "value": "main"},
    {"repo": "/path/to/web", "kind": "uncommitted"}
  ]
}
```

支援的 `kind` 是 `uncommitted`、`base`、`commit`、`range`。每個 scope 都會獨立以 NUL-safe Git 命令 sizing，再聚合檔案數與 changed lines。Deep custom review 沒有 `--scope-manifest` 或 `--review-range` 時直接失敗。

執行時間使用兩個界線：`--idle-timeout` 偵測 Codex 無輸出停滯，`--hard-timeout` 是絕對上限；既有 `--timeout` 保留為 hard timeout 相容參數。Bounded 預設 hard 600 秒、idle 0（停用），因 packet-only／零工具推理可能長時間沒有 JSONL event；native 與其他 generic mode 維持 hard 300／idle 180。任何顯式 timeout 都優先於 mode default。

`--max-tool-calls` 與 `--max-jsonl-bytes` 對既有模式預設 unlimited。Bounded 固定 max tools 0，JSONL 預設 262144 bytes；超限時 helper 立即終止 process group、保存 raw/partial evidence，且永遠不產生成功 final。Timeout 後不得在 scope 不變時只替換 mode、preset、`--ignore-user-config` 或 `--isolated` 重試。

Helper 的 `doctor` 預設修復缺少或過舊的 CLI；`--dry-run` 不更新，但仍要求 CLI 符合最低版本。兩者仍會讀取版本與模型清單，不保證離線。

## 診斷

Helper 的 `doctor` 預設只做快速 readiness 檢查；設定以 `features list` 驗證能否載入，登入以 `login status` 檢查儲存狀態，兩者各限 10 秒，不宣稱遠端驗證成功。完整 CLI health report 使用 `doctor --full-diagnostics`，可用 `--diagnostic-timeout` 調整預設 90 秒 deadline；兩個旗標只適用 helper doctor。完整診斷的失敗、warning 與逾時分別記在 `full_diagnostics`，不覆寫快速 `auth_config`；逾時為 incomplete，整次診斷不算成功。

```bash
command -v codex
codex --version
codex exec review --help
codex doctor --json
codex debug models
codex debug models --bundled
codex features list
codex update --help
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
- [Standalone installer for macOS/Linux](https://chatgpt.com/codex/install.sh)
- [Standalone installer for Windows](https://chatgpt.com/codex/install.ps1)
- [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [Python Windows 檔案鎖](https://docs.python.org/3/library/msvcrt.html#msvcrt.locking)
- [0.159.3 release](https://github.com/openai/codex/releases/tag/rust-v0.159.3)
- [0.159.3 review task source](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/tasks/review.rs)
- [0.159.3 exec routing source](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/exec/src/lib.rs)
- [0.159.3 agent configuration source](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/config/mod.rs)
- [0.159.3 feature controls source](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/features/src/lib.rs)
- [0.159.3 JSONL event contract](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/exec/src/exec_events.rs)
