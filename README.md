# Codex Reviewer Skill

透過 OpenAI Codex CLI 啟動獨立、唯讀的 second-opinion reviewer。支援 Git branch/commit/uncommitted review、自訂 criteria、structured findings、模型 preset 與可稽核的 JSONL 執行結果。

## Minimum Requirements

- Python 3.10+
- Git
- 首次 bootstrap／定期更新時可連線至 OpenAI 官方 installer 或 npm registry
- 已完成 Codex CLI 登入，且帳號可使用至少一個支援模型

若尚未安裝 Codex CLI，skill 會預設 bootstrap 最新 standalone；若已安裝 global npm `@openai/codex`，則保留 npm 安裝來源。最終執行版本必須是 `0.144.1` 或更新的 stable version。

自動更新只更新 Codex CLI，不會修改被審查 repo，也不會覆寫 `$CODEX_HOME` config。

## Install

`~/.agents/skills` 是建議的單一來源：

```bash
git clone https://github.com/BIGWOO/codex-reviewer.git \
  ~/.agents/skills/codex-reviewer
```

驗證 skill 與 runtime：

```bash
SKILL_DIR="$HOME/.agents/skills/codex-reviewer"
python3 "$SKILL_DIR/scripts/codex_review.py" doctor \
  --result-json /tmp/codex-review-doctor.json
```

## Binary Diagnostic

### 自動選擇與更新

預設政策：

1. `--codex-bin`／`CODEX_REVIEWER_CODEX_BIN` 明確 pin，不自動更新。
2. 偵測到 global npm Codex 時，優先使用並透過 `codex update` 更新 npm 版本。
3. 沒有 npm 版本時，使用或 bootstrap 官方 standalone，再透過 `codex update` 更新。
4. 成功檢查會快取 24 小時；失敗會退避 15 分鐘。現有版本仍相容時會警告後繼續。

機器上可能同時存在 npm、Homebrew、App 內嵌或舊版 binary。可用以下命令確認：

```bash
type -a codex
command -v codex
codex --version
codex exec review --help
```

指定 binary：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" doctor \
  --codex-bin /absolute/path/to/codex \
  --result-json /tmp/codex-review-doctor.json
```

也可設定：

```bash
export CODEX_REVIEWER_CODEX_BIN=/absolute/path/to/codex
```

`doctor` 不呼叫模型；它檢查 binary version、model catalog、Git 與 reviewer 所需能力。遇到 config 問題時再加 `--strict-config`。

更新控制：

```bash
# 本次略過自動更新
python3 "$SKILL_DIR/scripts/codex_review.py" --no-update-check doctor

# 忽略 24 小時快取，立即檢查
python3 "$SKILL_DIR/scripts/codex_review.py" --force-update-check doctor

# CI／離線環境全域停用
export CODEX_REVIEWER_AUTO_UPDATE=0
```

可用 `CODEX_REVIEWER_UPDATE_TTL_SECONDS` 調整快取秒數，或用 `CODEX_REVIEWER_UPDATE_CACHE` 指定 cache file。

## Quick Start

標準 branch review，使用 native Codex rubric：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" native-review \
  --cd /path/to/repo \
  --base main \
  --preset standard
```

Structured deep review，使用 v2 schema：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" structured-review \
  --cd /path/to/repo \
  --base main \
  --preset deep \
  --result-json /tmp/codex-review-result.json
```

先檢查實際 command、不呼叫模型：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" native-review \
  --cd /path/to/repo \
  --uncommitted \
  --preset quick \
  --dry-run
```

## Modes

| Mode | Use when |
|---|---|
| `native-review` | 精確的 `--base`、`--commit` 或 `--uncommitted` review |
| `structured-review` | 需要 native-compatible structured findings |
| `custom` / `focused` / `diff` | 自訂 criteria、任意 range 或特定檔案 |
| `security` / `performance` / `architecture` / `quality` | Generic 專項 review |
| `doctor` | Binary、version、catalog 或 Git diagnostic |

Native review 在 Codex CLI 0.144.1 不會套用 output schema、images 或 live search，也不使用 Ultra subagents。Helper 會對不相容組合 fail fast；需要這些能力時使用 generic mode。

## Presets

| Preset | Primary | Fallback | Typical use |
|---|---|---|---|
| `quick` | GPT-5.6 Terra medium | Sol medium，再 GPT-5.5 medium | 快速找阻塞問題 |
| `standard` | GPT-5.6 Sol high | GPT-5.5 high | 預設日常 review |
| `deep` | GPT-5.6 Sol max | GPT-5.5 xhigh | 複雜、高價值變更 |
| `ultra` | GPT-5.6 Sol ultra | 無 | Generic、可平行拆解的明確 opt-in |

Helper 會用 `codex debug models` 驗證模型與 reasoning support，不假設帳號已開放 GPT-5.6。`--quick` 是 `--preset quick` 的 alias。

## Useful Options

- `--instructions <TEXT>`：加入 repo-specific review criteria。
- `--profile <NAME>`：載入 `$CODEX_HOME/<NAME>.config.toml` V2 profile。
- `--fast`：使用 catalog 提供的 Fast tier；增加 usage，只能 opt-in。
- `--strict-config`：未知 config field 直接失敗，適合 diagnostic/CI。
- `--no-update-check`：本次停用 Codex CLI 自動安裝／更新檢查。
- `--force-update-check`：忽略快取，立即依既有安裝來源執行更新。
- `--result-json <FILE>`：額外寫入 `schema_version: 2` wrapper result envelope；不取代 stdout final text。
- `--output <FILE>`：保存 raw stdout / JSONL。
- `--last-message-output <FILE>`：保存 final reviewer message。
- `--isolated`：只忽略 user config/rules；不會停用 skill discovery、skills 或 plugins。
- `--allow-large-diff`：越過大型 diff guard；應先拆 task 或 module。

Structured review 預設使用 [references/review_output_schema.json](references/review_output_schema.json)。Schema 採用 Codex native field names，但 enforcement 由 generic `codex exec --output-schema` 提供。

同一 repo 與 scope 一次只能執行一個 review。`agent_message`、skills context budget 警告與 heartbeat 都是進度訊號；必須等待 `turn.completed` 與有效 final result，不要在原 process 尚未結束時啟動 fallback。

## Review Contract

- Read-only：不修改、commit、push、merge 或 deploy。
- Findings-first：只回報 discrete、actionable、evidence-backed issues。
- Scope-bound：避免 pre-existing、無關 refactor 與純風格噪音。
- Defensive security：描述風險與修法，不產生 exploit walkthrough。
- Independent verification：主 agent 必須重新核對高風險 finding，不能把 reviewer 當成最終裁決者。
- Quick 只做 triage，不代表 quality gate 完成；交付前至少跑 `standard`，高風險變更跑 `deep`。
- P0/P1 阻擋交付；P2 必須修正，或記錄理由後針對該範圍重跑 reviewer。

## Files

- `SKILL.md`：agent workflow、trigger 與 quality gate
- `scripts/codex_review.py`：CLI wrapper
- `scripts/codex_reviewer/updates.py`：npm 優先、standalone bootstrap 與 update cache
- `references/codex_cli_reference.md`：0.144.1 capability matrix、V2 profile 與診斷
- `references/example_prompts.md`：parameterized generic prompts
- `references/review_output_schema.json`：v2 native-compatible schema
- `agents/openai.yaml`：Codex UI metadata

更完整的 CLI 行為與官方來源見 [references/codex_cli_reference.md](references/codex_cli_reference.md)。
