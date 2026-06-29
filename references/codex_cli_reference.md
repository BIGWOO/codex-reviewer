# Codex CLI 程式碼審查參考手冊

本參考手冊提供使用 Codex CLI 進行程式碼審查的詳細資訊。

## 指令參考

### 基本執行

```bash
codex exec "prompt text"
```

非互動模式，用於單次審查和分析。

### 模型選擇

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "prompt"
```

**程式碼審查建議使用的模型：**
- `gpt-5.5`：此 Skill 的預設模型，適合大型 PR、跨檔案 review、規格驗證
- 除非用戶明確指定其他模型，本 Skill 的所有 Codex CLI 範例與腳本預設都應固定使用 `gpt-5.5`

OpenAI 在 2026 年 4 月 23 日發布的 GPT-5.5 官方文章指出，GPT-5.5 在 Codex 中提供長上下文能力。實際可用上限以本機 `codex debug models` 的 model catalog 為準；此 Skill 的 helper script 會自動 clamp `model_context_window`，避免使用本機 CLI 不支援的值。來源：<https://openai.com/index/introducing-gpt-5-5/>

**建議模式：**
- 標準 review：`gpt-5.5` + `reasoning_effort=high`
- 長上下文 review：使用 helper 的 `--long-context`，由 `codex debug models` 動態決定 `model_context_window`，`model_auto_compact_token_limit` 預設約為 context window 的 80%
- 只有大型 PR、跨模組變更、規格對照實作時才建議開長上下文；小型 review 使用標準模式即可

### 沙箱模式

控制檔案和網路存取權限：

```bash
codex exec --sandbox read-only "prompt"          # 本 Skill 的審查預設：僅讀取檔案
```

`workspace-write`、`danger-full-access`、`--full-auto` 只適合實作或修復任務，不適合此 reviewer skill。審查流程應保持唯讀，避免 Codex 在第二意見階段修改工作區。

### 輸出控制

```bash
codex exec "prompt"                               # 串流至 stderr，最終訊息至 stdout
codex exec -o output.txt "prompt"                 # 將最終訊息寫入檔案
codex exec --json "prompt"                        # 輸出 JSONL 事件串流
codex exec --json "prompt" > review.jsonl         # 儲存 JSONL 至檔案
```

### 核准策略

```bash
codex exec --config approval_policy=untrusted "prompt"  # 對不信任的指令提示確認
codex exec --config approval_policy=on-request "prompt" # 需要時提示確認
codex exec --config approval_policy=never "prompt"      # 永不提示（唯讀安全）
```

### 推理設定

```bash
codex exec --config reasoning_effort=low "prompt"     # 快速，較不深入
codex exec --config reasoning_effort=medium "prompt"  # 平衡模式
codex exec --config reasoning_effort=high "prompt"    # 深度分析，較慢
```

### 長上下文設定

```bash
codex exec --model gpt-5.5 \
  --sandbox read-only \
  --config reasoning_effort=high \
  --config model_context_window=<from codex debug models> \
  --config model_auto_compact_token_limit=<about 80% of context window> \
  "prompt"
```

- `model_context_window`：以 `codex debug models` 中該模型的 `max_context_window` 或 `context_window` 為上限
- `model_auto_compact_token_limit`：接近上限前讓 Codex 自動 compact 對話脈絡，helper 預設使用約 80%
- 適合大型 PR、跨模組 review、規格對照實作

### 原生 code review 指令

Codex CLI 0.124.0 提供內建 review flow，適合一般 PR、commit、未提交變更審查：

```bash
codex --model gpt-5.5 --sandbox read-only --config reasoning_effort=high \
  exec review --base main --json --ephemeral
```

常用選項：
- `--base <BRANCH>`：審查目前分支相對於指定 base branch 的變更
- `--commit <SHA>`：審查單一 commit
- `--uncommitted`：審查 staged、unstaged、untracked changes
- `--title <TITLE>`：在 review 摘要顯示標題
- `--output-last-message <FILE>`：將最終 review 訊息寫入檔案
- `--ephemeral`：不保存 session，適合一次性 reviewer
- `--ignore-user-config` / `--ignore-rules`：隔離本機設定與 exec rules，適合可重現審查

需要 `--output-schema`、圖片、跨 repo 額外目錄或自訂 structured findings 時，使用 generic `codex exec` prompt 或 `scripts/codex_review.py diff/custom`。

## 審查專用模式

### 安全審查

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Perform a security audit of [file/directory]. Check for:
- Authentication and authorization issues
- Input validation vulnerabilities (SQL injection, XSS, etc.)
- Cryptographic weaknesses
- Sensitive data exposure
- Rate limiting and DoS concerns
Provide severity ratings and specific line numbers."
```

### 效能審查

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Analyze [file/directory] for performance issues:
- Inefficient algorithms and data structures
- N+1 query problems
- Memory leaks or excessive allocations
- Blocking operations that should be async
- Database query optimization opportunities
Suggest specific improvements with code examples."
```

### 架構審查

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Review the architecture in [directory]:
- Evaluate separation of concerns
- Identify tight coupling issues
- Check adherence to design patterns
- Assess scalability concerns
- Suggest refactoring opportunities
Compare current design with best practices for [framework/technology]."
```

### 程式碼品質審查

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Review [files] for code quality:
- Complexity metrics (functions that are too long/complex)
- Code duplication and DRY violations
- Naming conventions and clarity
- Error handling completeness
- Test coverage gaps
Focus on maintainability and readability."
```

### Diff/PR 審查

```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Review the git diff between [branch1] and [branch2]:
- Identify breaking changes
- Check for regression risks
- Evaluate test coverage for changes
- Verify documentation updates
- Assess backward compatibility
Provide feedback organized by file and severity."
```

## JSONL 事件格式

使用 `--json` 參數時，Codex 會輸出 JSONL 事件：

### 事件類型

**thread.started**
```json
{"type":"thread.started","thread_id":"0199a213-81c0-7800-8aa1-bbab2a035a53"}
```

**turn.started**
```json
{"type":"turn.started"}
```

**item.completed**（推理）
```json
{
  "type":"item.completed",
  "item":{
    "id":"item_0",
    "type":"reasoning",
    "text":"**Analysis of authentication flow**"
  }
}
```

**item.completed**（指令執行）
```json
{
  "type":"item.completed",
  "item":{
    "id":"item_1",
    "type":"command_execution",
    "command":"bash -lc 'grep -r TODO src/'",
    "aggregated_output":"...",
    "exit_code":0,
    "status":"completed"
  }
}
```

**item.completed**（代理訊息）
```json
{
  "type":"item.completed",
  "item":{
    "id":"item_2",
    "type":"agent_message",
    "text":"Review complete. Found 3 critical issues..."
  }
}
```

**turn.completed**
```json
{
  "type":"turn.completed",
  "usage":{
    "prompt_tokens":1250,
    "completion_tokens":850,
    "total_tokens":2100
  }
}
```

## 結構化 findings schema

需要穩定後處理時，使用此 Skill 內建 schema：

```bash
codex exec --model gpt-5.5 \
  --sandbox read-only \
  --config reasoning_effort=high \
  --output-schema /Users/bigwoo/.agents/skills/codex-reviewer/references/review_output_schema.json \
  "Review the current pull request diff against main. Return concise findings."
```

schema 會要求 Codex 回傳 `summary`、`overall_risk`、`findings`、`test_gaps`、`limitations`。每個 finding 需包含嚴重度、類別、檔案、行號、風險、修復建議與信心度。

## 環境變數

```bash
CODEX_API_KEY=sk-...        # 覆寫 API key（僅限 codex exec）
RUST_LOG=info               # 控制日誌層級
```

## 設定檔

Codex 從 `~/.config/codex/config.toml` 或專案專屬的 `.codex/config.toml` 讀取設定

**程式碼審查設定範例：**

```toml
model = "gpt-5.5"
reasoning_effort = "high"

[profiles.security_review]
model = "gpt-5.5"
reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "read-only"

[profiles.pr_review]
model = "gpt-5.5"
reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "read-only"

[profiles.long_context_review]
model = "gpt-5.5"
reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "read-only"
# 以 `codex debug models` 回報的本機 model catalog 為準；不要硬套超出上限的值。
# model_context_window = 272000
# model_auto_compact_token_limit = 217600
```

使用設定檔：
```bash
codex exec -c profile=security_review "Audit authentication code"
codex exec -c profile=long_context_review "Review the current pull request diff against main"
```

## 認證方式

### ChatGPT 帳號（建議）
```bash
codex  # 首次執行會提示認證
```
選擇「Sign in with ChatGPT」並完成瀏覽器流程。

### API Key
```bash
export CODEX_API_KEY=your-api-key
codex exec "review code"
```

或在設定檔中設定：
```toml
[auth]
api_key = "your-api-key"
```

## 程式碼審查最佳實踐

### 1. 適當限定審查範圍
- 單一檔案：`codex exec "Review auth.py for security issues"`
- 目錄：`codex exec "Review src/api/ for REST API best practices"`
- 特定問題：`codex exec "Check database.py for SQL injection vulnerabilities"`

### 2. 提供脈絡
```bash
codex exec "Review payment.py. This is a Django app using Stripe API.
Focus on: PCI compliance, error handling, idempotency, and webhook security."
```

### 3. 要求結構化輸出
```bash
codex exec "Review code and format findings as:
## Critical Issues
- [Issue with line numbers and explanation]

## Medium Priority
- [Issue with line numbers and explanation]

## Suggestions
- [Improvement ideas]"
```

### 4. 使用多次焦點審查
與其進行一次廣泛審查，不如執行多次針對性審查：

```bash
codex exec "Security audit of authentication system"
codex exec "Performance analysis of database queries"
codex exec "Test coverage assessment"
```

### 5. 評估測試
```bash
codex exec --model gpt-5.5 --sandbox read-only --config reasoning_effort=high "Review auth.py and the related tests. Identify missing security test cases, brittle assertions, and untested edge cases. Do not modify files."
```

## 限制與解決方案

### 大型程式碼庫
**問題**：大型檔案/目錄受限於上下文視窗
**解決方案**：分段審查、聚焦於變更的檔案，或使用目錄層級審查

### 無法互動澄清
**問題**：codex exec 是非互動式的
**解決方案**：預先考慮問題並提供詳細的提示

### 網路存取
**問題**：預設沙箱會封鎖網路存取
**解決方案**：此 reviewer skill 不應為了審查開啟寫入或完整系統存取。若需要外部文件，先由主代理取得必要摘錄或連結，再把脈絡放進 prompt。

### 狀態持久化
**問題**：每次 `codex exec` 呼叫都是獨立的
**解決方案**：使用 `resume --last` 對相同脈絡進行後續追問

## codex exec 與互動式 codex 比較

| 功能 | `codex exec` | 互動式 `codex` |
|------|-------------|----------------|
| 使用情境 | 自動化審查、CI/CD | 配對程式設計 |
| 互動方式 | 單次執行 | 多輪對話 |
| 核准機制 | 可設定 | 互動式提示 |
| 輸出格式 | 結構化（JSONL） | TUI 顯示 |
| 腳本整合 | 容易 | 困難 |

進行程式碼審查時，通常建議使用 `codex exec`，因為它具有自動化能力和結構化輸出。
