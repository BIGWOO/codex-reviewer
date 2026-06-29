---
name: codex-reviewer
description: |
  Use OpenAI's Codex CLI as an independent code reviewer to provide second opinions on code implementations, architectural decisions, code specifications, and pull requests. Provides unbiased analysis using GPT-5.5 with high reasoning effort through the codex exec command for non-interactive reviews.

  觸發時機（Trigger when）：
  - 用戶要求「code review」、「程式碼審查」、「審查程式碼」「檢查程式碼」
  - 用戶要求「第二意見」、「second opinion」、「獨立審查」
  - 用戶要求「Codex 審查」、「用 Codex review」、「請 Codex 看一下」
  - 用戶要求「架構驗證」、「architecture validation」、「設計審查」
  - 用戶要求「PR review」、「pull request 審查」「MR review」、「merge request 審查」「合併請求審查」
  - 完成複雜的程式碼實作後，需要主動使用此 Skill 進行獨立審查
  - 用戶問「另一個 AI 怎麼看這段程式碼？」
---

# Codex 獨立程式碼審查器

用 Codex CLI 啟動一個獨立、唯讀的 GPT-5.5 reviewer，審查程式碼、PR/MR diff、架構決策或規格實作落差。這個 Skill 的輸出是審查意見，不負責直接修改檔案。

根據 OpenAI 於 2026 年 4 月 23 日發布的 GPT-5.5 官方文章，GPT-5.5 是 Codex 中最強的 agentic coding 模型，擅長跨大型系統維持脈絡、使用工具驗證假設、除錯與審查。GPT-5.5 在 Codex 中提供長上下文能力；實際可用上限以本機 `codex debug models` 的 model catalog 為準，helper script 會自動 clamp。來源：<https://openai.com/index/introducing-gpt-5-5/>

## 固定設定

- 模型：一律使用 `gpt-5.5`，除非用戶明確指定其他模型。
- 推理：預設 `--config reasoning_effort=high`。
- 沙箱：審查一律使用 `--sandbox read-only`。
- 長上下文：只在大型 PR、跨模組變更、規格對照實作時啟用 `--long-context`；helper 會依 `codex debug models` 自動設定 `model_context_window` 與 `model_auto_compact_token_limit`。
- Session：預設使用 `--ephemeral`，避免 reviewer session 汙染歷史；需要後續 `resume` 時才用 `--persist-session`。
- 進度：helper 會把 Codex JSONL 事件轉成 stderr 進度訊息，並每 30 秒輸出 heartbeat；不要用會吞 stderr 的呼叫方式。
- Timeout：helper timeout 時會保留已收到的 partial stdout 到 `--output`，避免長時間 review 完全白跑。
- 快審：只想先抓阻塞 bug 時可用 `--quick`，預設改用 medium reasoning 與 240 秒 timeout；深審再用 high。
- 大型 diff guard：helper 預設超過 40 個檔案或 3000 changed lines 會 fail fast，避免 high reasoning 審查整包變更後 timeout；優先改用單一 task／commit range、`--quick` 或 focused prompt。確定要審整包時才加 `--allow-large-diff`。
- 安全：不要用 `--full-auto`、`workspace-write` 或 `danger-full-access` 做 reviewer；安全審查只要求防禦式風險、影響範圍與修復建議，不要求可操作攻擊步驟。

## 參考檔案

- `references/codex_cli_reference.md`：Codex CLI 旗標、profile、JSONL 事件與疑難排解。需要查 CLI 細節時再讀。
- `references/example_prompts.md`：安全、效能、架構、PR、規格驗證等提示範例。需要特定審查情境時再讀。
- `references/review_output_schema.json`：標準 findings schema。需要穩定 JSON 輸出或後處理時使用。
- `scripts/codex_review.py`：常用 review wrapper，支援 `native-review`、`--cd`、`--add-dir`、`--output`、`--last-message-output`、`--schema`、`--timeout`、`--quick`、`--long-context`、`--review-range`、`--allow-large-diff`、`--image`、`--search`、`--isolated`。

## 核心流程

1. 先自己閱讀目標 diff、檔案或規格，形成初步風險假設。
2. 明確界定 Codex 的審查範圍：檔案、分支、關注面向、輸出格式。
3. 若專案採任務制或分批 commit，優先 review 單一 task 的 commit range；不要把多個 task 的 `origin/main..HEAD` 一次送進深審。
4. 若是 `--uncommitted` 且 dirty 檔案很多，先用 `git diff --name-status` 分辨是否有不相關變更；超過約 10 個檔案或含多個主題時，優先改用 `focused` / `custom` 並列明檔案清單。
5. `native-review --uncommitted` 不能搭配自訂 prompt；若需要自訂審查重點，改用 `custom` / `focused`，並在 prompt 內要求 Codex 先檢查 `git diff` 與 untracked files。
6. 優先用 helper script 執行唯讀審查；需要特殊 CLI 旗標時才直接呼叫 `codex exec`。
7. 對照自己的判斷與 Codex 回饋，分辨共識、分歧與 Codex 獨有發現。
8. 回覆用戶時以 findings 為主，包含嚴重度、檔案/行號、風險與建議修法；若 Codex 無發現，說明剩餘測試缺口或風險。

## 建議指令

標準 PR review：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py native-review \
  --cd /path/to/repo \
  --base main \
  --output /tmp/codex-review.jsonl
```

大型 PR 或規格對照 review：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py diff HEAD main \
  --cd /path/to/repo \
  --long-context \
  --schema /Users/bigwoo/.agents/skills/codex-reviewer/references/review_output_schema.json \
  --output /tmp/codex-review.jsonl
```

單一 task／commit range review：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py custom \
  "Review only the changes in commit range <base>..<head>. Focus on correctness, hidden side effects, compatibility, security, performance, and missing tests. Ignore unrelated earlier tasks unless needed for integration context. Return only actionable findings with file paths and line numbers." \
  --cd /path/to/repo \
  --review-range <base>..<head> \
  --quick \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

審查未提交變更：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py native-review \
  --cd /path/to/repo \
  --uncommitted \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

審查未提交變更並指定自訂重點：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py custom \
  "Review the uncommitted changes in this repository. First inspect git diff and untracked files. Focus on correctness, security regressions, and missing tests. Return findings with file paths and line numbers." \
  --cd /path/to/repo \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

快速抓 P1/P2：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py native-review \
  --cd /path/to/repo \
  --uncommitted \
  --quick \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

明確允許大型 diff 深審：

```bash
python3 /Users/bigwoo/.agents/skills/codex-reviewer/scripts/codex_review.py diff HEAD main \
  --cd /path/to/repo \
  --long-context \
  --allow-large-diff \
  --timeout 900 \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

直接使用 Codex CLI：

```bash
codex exec --model gpt-5.5 \
  --sandbox read-only \
  --config reasoning_effort=high \
  "Review the current pull request diff against main. Focus on correctness, security, performance regressions, and missing tests. Return findings with file paths and line numbers."
```

長上下文 CLI：

```bash
codex exec --model gpt-5.5 \
  --sandbox read-only \
  --config reasoning_effort=high \
  --config model_context_window=<from codex debug models> \
  --config model_auto_compact_token_limit=<about 80% of context window> \
  "Review the current pull request diff against main, then inspect related tests, schemas, API contracts, and docs touched by the change."
```

## 提示要求

有效 prompt 應包含：

- 審查範圍：檔案、目錄、分支或規格檔。
- 優先順序：correctness、security、performance、tests、compatibility 等。
- 專案脈絡：語言、框架、業務規則或 API contract。
- 輸出格式：要求嚴重度、檔案、行號、風險、修復建議與信心度。
- 限制：明確排除風格問題或非本次變更範圍，避免噪音。

安全審查提示應採用防禦式措辭，例如「描述風險與修復方式」，不要要求 exploitation walkthrough、payload 或逐步攻擊流程。

## 結果整合

回覆用戶時不要直接貼整份 Codex 輸出。請整理為：

1. Codex 發現的高風險問題。
2. 你與 Codex 一致同意的問題。
3. 分歧或需要人工判斷的取捨。
4. 可執行修復建議與測試建議。
5. Codex 審查限制，例如未跑測試、找不到基準分支、schema 輸出失敗。

## 疑難排解

- `codex: command not found`：Codex CLI 未安裝或不在 PATH。
- `Authentication required`：先互動執行 `codex` 完成登入，或設定 `CODEX_API_KEY`。
- `native-review --uncommitted cannot be combined with a prompt`：目前 Codex CLI 不允許 `--uncommitted` 同時帶自訂 prompt；純 native review 不帶 prompt，或改用 `custom` / `focused` 並要求 Codex 自行讀 `git diff` 與 untracked files。
- `Review scope ... is large`：helper 的大型 diff guard 擋下過大的審查範圍。請改用 `--review-range <base>..<head>`、`--quick`、focused/custom prompt，或確認後加 `--allow-large-diff`。
- `Codex review timed out`：不要只拉長 timeout；先縮小為單一 task／commit range，或先跑 `--quick` 找 P0/P1/P2，再對高風險檔案做 high reasoning 深審。
- 審查過淺：縮小範圍、增加專案脈絡，或改用 `references/example_prompts.md` 的焦點範例。
- 大型 PR 被 compact：改用 `--long-context`，並要求先建立變更索引再深入檢查高風險模組。
