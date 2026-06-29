# Codex Reviewer Skill

這是一個 Codex skill，用來透過 Codex CLI 啟動獨立、唯讀的程式碼審查器，適合做 PR/MR review、架構驗證、安全檢查、效能檢查，以及複雜實作完成後的第二意見。

## 需求

- Codex CLI
- Python 3
- 已完成 Codex CLI 登入與本機設定

## 安裝

將 repo clone 到全域 skills 目錄：

```bash
git clone https://github.com/BIGWOO/codex-reviewer.git ~/.agents/skills/codex-reviewer
```

確認檔案存在：

```bash
ls ~/.agents/skills/codex-reviewer
```

## 使用情境

當你需要獨立 code review 或第二意見時，可以要求 Codex 使用 `codex-reviewer` skill。這個 skill 預設使用唯讀沙箱，不會修改專案檔案。

常見情境：

- 審查目前 PR/MR diff
- 審查未提交變更
- 針對單一 commit range 做 review
- 檢查安全、效能、架構風險
- 對照規格與實作是否一致

## Helper Script 範例

審查目前分支相對於 `main` 的變更：

```bash
python3 ~/.agents/skills/codex-reviewer/scripts/codex_review.py native-review \
  --cd /path/to/repo \
  --base main \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

快速審查未提交變更：

```bash
python3 ~/.agents/skills/codex-reviewer/scripts/codex_review.py native-review \
  --cd /path/to/repo \
  --uncommitted \
  --quick \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

指定 commit range 做 focused review：

```bash
python3 ~/.agents/skills/codex-reviewer/scripts/codex_review.py custom \
  "Review only the changes in commit range <base>..<head>. Focus on correctness, security, performance, compatibility, and missing tests. Return actionable findings with file paths and line numbers." \
  --cd /path/to/repo \
  --review-range <base>..<head> \
  --quick \
  --output /tmp/codex-review.jsonl \
  --last-message-output /tmp/codex-review.md
```

## 主要內容

- `SKILL.md`：skill 入口與工作流程
- `scripts/codex_review.py`：Codex CLI review wrapper
- `references/codex_cli_reference.md`：Codex CLI 審查參考
- `references/example_prompts.md`：審查 prompt 範例
- `references/review_output_schema.json`：結構化輸出 schema

## 注意事項

- 這個 skill 的 review 流程預設使用 read-only sandbox。
- 大型 diff 建議先切成單一 task 或 commit range，避免 review timeout。
- 安全審查只應描述防禦性風險與修復建議，不要要求攻擊步驟或 payload。
