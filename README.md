# Codex Reviewer Skill

透過 OpenAI Codex CLI 啟動獨立、唯讀的 second-opinion reviewer。支援 Git branch/commit/uncommitted review、可驗證 bounded packet、自訂 criteria、structured findings、模型 preset 與可稽核的 JSONL 執行結果。

## Minimum Requirements

- Python 3.10+
- Git
- Stable Codex CLI `0.159.3` 以上；缺少或過舊時，預設需要連線至官方 installer 或 npm registry 升級
- 已完成 Codex CLI 登入，且帳號可使用至少一個支援模型

每次審查或 `doctor` 都先確認 stable CLI 至少為 `0.159.3`。缺少或過舊時，預設依原安裝來源升級，缺少 CLI 則安裝 standalone；升級後重新讀取版本，未達門檻就停止。符合門檻即可繼續；定期查詢新版另外由更新旗標或環境設定啟用。

升級只處理 Codex CLI，不會修改被審查 repo，也不會覆寫 `$CODEX_HOME` config。

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

預設自動修復缺少或低於 `0.159.3` 的 CLI。符合門檻後，可用 `--update-check` 或 `CODEX_REVIEWER_AUTO_UPDATE=1` 啟用定期更新；`CODEX_REVIEWER_AUTO_UPDATE=0` 只停用定期更新，不阻止必要升級。`--no-update-check` 完全停用安裝／更新，`--dry-run` 也不更新；兩者仍要求已安裝版本符合門檻。

明確 binary pin 由 caller 自行管理，版本不足就停止；npm 優先且保留來源，否則選 standalone。成功快取 24 小時，但不能阻止低於最低版本的必要升級；失敗退避 15 分鐘。只有已達門檻的 CLI 可在定期更新失敗時帶警告繼續。

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
  --no-update-check \
  --codex-bin /absolute/path/to/codex \
  --result-json /tmp/codex-review-doctor.json
```

也可設定：

```bash
export CODEX_REVIEWER_CODEX_BIN=/absolute/path/to/codex
```

`doctor` 不呼叫模型；預設先確保最低 CLI 版本，再快速檢查設定能否載入、已儲存登入狀態、model catalog、schema、Git 與 read-only Git 能力。登入狀態不保證遠端服務或模型權限可用。遇到 config 問題時再加 `--strict-config`。`--dry-run` 不更新，但診斷仍可能查詢模型清單，不代表完全離線。

完整的網路、代理、防護軟體、桌面與更新診斷另用：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" doctor \
  --full-diagnostics --diagnostic-timeout 90 \
  --result-json /tmp/codex-review-full-doctor.json
```

`--diagnostic-timeout` 預設 90 秒，只限制完整診斷；快速登入／設定探測各限 10 秒。完整診斷逾時記為 `full_diagnostics: incomplete`，不覆蓋快速 `auth_config` 結果，也不算診斷成功。完整報告中的 warning 保留為 warning，fail 仍失敗；未啟用完整診斷時明確記為 skip。

更新控制：

```bash
# 本次不安裝／更新；版本不足時失敗
python3 "$SKILL_DIR/scripts/codex_review.py" --no-update-check doctor

# 明確啟用本次安裝／更新，忽略 24 小時快取
python3 "$SKILL_DIR/scripts/codex_review.py" --force-update-check doctor

# 可選：審查與 doctor 在符合門檻後仍定期查詢新版
export CODEX_REVIEWER_AUTO_UPDATE=1
```

可用 `CODEX_REVIEWER_UPDATE_TTL_SECONDS` 調整快取秒數，或用 `CODEX_REVIEWER_UPDATE_CACHE` 指定 cache file。

## Quick Start

直接呼叫 skill 即會套用[預設審查重點](SKILL.md#預設審查重點)，不必再貼通用提示詞：副作用、相容性、邊界、效能及安全優先，命名／測試／維護問題須有具體影響，按嚴重程度排序並限制在本次範圍。主代理核實後依既有授權採最小修復。

Skill 一般選 bounded 或 structured 模式；這些及其他 generic 模式會自動將重點傳入模型。明確選用以下 native 模式時，仍使用 Codex 內建規則，無法追加同一份自訂提示。

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

窄 tracer 建議先用 bounded standard。主 agent 先執行 tests，再把結果放進 evidence JSON：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" bounded-review \
  --cd /path/to/repo \
  --bounded-scope /tmp/review-scope.json \
  --evidence-json /tmp/review-evidence.json \
  --preset standard \
  --enforce-gate \
  --result-json /tmp/codex-bounded-result.json
```

先檢查實際 command、不呼叫模型：

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" native-review \
  --cd /path/to/repo \
  --uncommitted \
  --preset quick \
  --dry-run --no-update-check
```

## 與 Codex 子代理的差異

| | Codex 子代理審查 | `$codex-reviewer` |
|---|---|---|
| 定位 | 主任務內平行分工 | 交付前的獨立品質閘門 |
| 執行 | 多個子代理執行緒，由主代理彙整 | 獨立、唯讀的 Codex CLI 程序 |
| 控制 | 通常繼承主任務的權限與設定 | 固定範圍、模型、推理強度、逾時與輸出契約 |
| 成本 | 每個子代理各自消耗 token | 預設單一 reviewer，成本較可預測 |
| 適用 | 規格、標準、測試等過程檢查 | 最終差異、正式驗收與可稽核結果 |

兩者可以互補，但不要重複審查相同面向。若主任務已用子代理檢查規格或標準，最後只讓 `$codex-reviewer` 審查最終差異或尚未覆蓋的風險。此 skill 預設啟用 `--minimal-context`，不展開子代理；只有明確選用 generic `ultra` 才啟用模型的自動分工。

## Modes

| Mode | Use when |
|---|---|
| `native-review` | 精確的 `--base`、`--commit` 或 `--uncommitted` review |
| `structured-review` | 需要 native-compatible structured findings |
| `bounded-review` | 主程序封裝精確 code/range/diff，child 僅讀 packet 且禁止 tools |
| `custom` / `focused` / `diff` | 自訂 criteria、任意 range 或特定檔案 |
| `security` / `performance` / `architecture` / `quality` | Generic 專項 review |
| `doctor` | Binary、version、catalog 或 Git diagnostic |

依 0.159.3 原始碼核對，Native review 不會套用 output schema、images 或 live search，也不使用 Ultra subagents。Helper 會對不相容組合 fail fast；需要這些能力時使用 generic mode。

## Presets

| Preset | Model / reasoning | Typical use |
|---|---|---|
| `quick` | `gpt-6.1-sol` medium | 快速找阻塞問題 |
| `standard` | `gpt-6.1-sol` high | 預設日常 review |
| `deep` | `gpt-6.1-sol` xhigh | 複雜、高價值變更 |
| `ultra` | `gpt-6.1-sol` ultra | Generic、可平行拆解的明確 opt-in |

Helper 會用 `codex debug models` 驗證模型與 reasoning support，不假設帳號已開放指定模型。`--quick` 是 `--preset quick` 的 alias。

所有 preset 在模型或所需 reasoning 不可用、catalog 完全不可取得時直接回報原因，沒有自動模型備援。其他模型必須明確用 `--model` 選定並通過 catalog 驗證；已退役模型即使被舊 catalog 列出也會拒絕。Catalog 優先刷新，失敗可用 bundled catalog 驗證客戶端能力並警告，但不保證帳號模型權限。內建審查同步指定 `review_model`，避免使用者或 profile 設定蓋過選定模型。

所有 review 都明確停用 hooks。一般 preset 同時停用 `multi_agent`、`multi_agent_v2` 並設定 `agents.enabled=false`，即使使用者／profile 或 model catalog 預設 V2，也禁止委派。Generic `ultra` 才允許委派，由目前模型／provider 選擇 V1 或 V2，代理並行上限為 2；native 與 bounded 禁止 ultra。`--full-context` 不會改變這些限制。

`max` 不屬於 preset。只有明確傳入 `--reasoning-effort max` 才會使用，且必須是完整 sizing 的單一 repo scope，最多 15 個 changed files／1200 changed lines，不能用 `--allow-large-diff` 繞過。

## Bounded Review Contract

`--bounded-scope` 使用 version 1 JSON file，v1 只支援單一 repo：

```json
{
  "version": 1,
  "kind": "commit_snapshot",
  "commit": "HEAD",
  "files": [
    {"path": "src/aes.ts", "ranges": [{"start": 40, "end": 96}]}
  ]
}
```

其他 kind：

- `commit_diff`：`commit` 加上 `files` path allowlist。
- `uncommitted_diff`：只封裝 `files` 指定的 staged、unstaged、untracked layers。

選填 evidence JSON：

```json
{
  "version": 1,
  "checks": [
    {"name": "targeted unittest", "status": "passed", "detail": "23/23"},
    {"name": "integration", "status": "not_run"}
  ]
}
```

Status 只接受 `passed`、`failed`、`not_run`。Scope／evidence 都拒絕未知欄位；scope 另拒絕絕對路徑、`..`、重複／重疊／越界 range、binary 與無效 ref。Git 抽取在主程序完成，Codex stdin 只收到 canonical JSON packet。Result envelope 會記錄實際 files／lines／bytes、scope fingerprint、packet hash 與 prompt hash。

Bounded 固定 bundled schema、`standard` 預設、`--ignore-user-config`、minimal context、零工具與 262144-byte JSONL 預算；預設 absolute hard timeout 為 600 秒並停用 idle timeout，避免無工具的靜默推理被誤判為停滯。可顯式選 `deep`，但不會自動升級。它拒絕 search、images、full-context、profile、isolated、add-dir、scope-manifest 與自訂 schema/prompt。

## Useful Options

- `--instructions <TEXT>`：加入 repo-specific review criteria。
- `--profile <NAME>`：載入 `$CODEX_HOME/<NAME>.config.toml` V2 profile。
- `--fast`：使用 catalog 提供的 Fast tier；增加 usage，只能 opt-in。
- `--strict-config`：未知 config field 直接失敗，適合 diagnostic/CI。
- `--update-check`：符合最低版本後仍檢查更新，尊重快取。
- `--no-update-check`：本次停用 CLI 安裝／更新，覆蓋環境設定；版本不足仍失敗。
- `--force-update-check`：忽略快取，立即依既有安裝來源執行更新。
- `--result-json <FILE>`：額外寫入精簡的 `schema_version: 2` result envelope；不取代 stdout final text，也不重複 raw JSONL。
- `--include-events`：明確要求把已遮蔽的 events 放入 `--result-json`；一般情況使用 `--output` 保存 raw JSONL。
- `--output <FILE>`：保存 raw stdout / JSONL。
- `--last-message-output <FILE>`：保存 final reviewer message。
- `--scope-manifest <FILE>`：宣告跨 repo generic review 的所有 Git scope，並聚合 preflight sizing。
- `--bounded-scope <FILE>`：單 repo strict bounded scope；只供 `bounded-review`。
- `--evidence-json <FILE>`：主 agent 已執行的 check evidence；reviewer 不重跑 tests。
- `--enforce-gate`：passed／warnings exit 0、blocked exit 2、failure／inconclusive exit 1。
- `--max-tool-calls <N>`：既有模式預設 unlimited；bounded 固定 0。
- `--max-jsonl-bytes <N>`：既有模式預設 unlimited；bounded 預設 262144。
- `--idle-timeout <SECONDS>`：無 stdout/stderr 活動的停滯上限；bounded 預設 `0`（停用），既有模式預設 `180`。
- `--hard-timeout <SECONDS>`：整次執行的絕對上限；bounded 預設 `600`，既有模式預設 `300`；`--timeout` 保留為相容 hard timeout。
- `--minimal-context`／`--full-context`：只控制 plugins／apps 載入；預設停用。兩者都停用 hooks，一般 preset 禁止委派，generic ultra 才允許委派；不代表停用一般 skills 或所有 MCP。Bounded 另停用 Code Mode，拒絕任何工具事件。
- `--ignore-user-config`：忽略 base user config；`--isolated` 另外忽略 rules，兩者用途不同。
- `--allow-large-diff`：越過一般大型 diff guard；應先拆 task 或 module，且不能用於 `max`。

跨 repo manifest 範例：

```json
{
  "version": 1,
  "scopes": [
    {"repo": "/path/to/api", "kind": "base", "value": "main"},
    {"repo": "/path/to/web", "kind": "uncommitted"}
  ]
}
```

```bash
python3 "$SKILL_DIR/scripts/codex_review.py" custom \
  --cd /path/to/api \
  --scope-manifest /tmp/review-scope.json \
  --preset standard \
  "Review the declared cross-repository change"
```

`deep` custom review 必須有 `--scope-manifest` 或 `--review-range`。Scope manifest 只供 generic mode；native／structured 仍使用自身精確 scope flags。`--review-range` 只做 sizing，不會改寫 prompt 或真正縮小 child scope。

Structured review 預設使用 [references/review_output_schema.json](references/review_output_schema.json)。Schema 採用 Codex native field names，但 enforcement 由 generic `codex exec --output-schema` 提供。

只要兩個 review 共用任一 `cwd`、`--add-dir` 或 manifest repo，就不能同時執行。`agent_message`、skills context budget 警告與 heartbeat 都是進度訊號；必須等待 `turn.completed` 與有效 final result，不要在原 process 尚未結束時啟動 fallback。Timeout 的 `partial_progress` 明確是未驗證進度，不得視為通過；scope 不變時也禁止只換 mode、preset 或 isolation 旗標重試。

## Review Contract

- Read-only：不修改、commit、push、merge 或 deploy。
- Findings-first：只回報 discrete、actionable、evidence-backed issues。
- Scope-bound：避免 pre-existing、無關 refactor 與純風格噪音。
- Defensive security：描述風險與修法，不產生 exploit walkthrough。
- Independent verification：主 agent 必須重新核對高風險 finding，不能把 reviewer 當成最終裁決者。
- Quick 只做 triage，不代表 quality gate 完成；窄 tracer 先跑 bounded `standard`，證據不足時才明確升級 `deep`。
- P0–P2 為 `blocked`；僅 P3 為 `passed_with_warnings`。原始結果保留不改寫。一般第二意見由主代理附證據記錄採納／不採納，未變更程式或關鍵證據不必重跑；正式品質關卡依專案要求修正、複查或取得例外，不把 blocked 說成 passed。


## 自訂格式與驗證依賴

只有自訂 `--schema` 需要 `jsonschema==4.26.0`；一般及內建格式審查仍不需要額外套件。請使用獨立環境安裝，不修改系統 Python：

```bash
python3 -m venv "$HOME/.venvs/codex-reviewer"
"$HOME/.venvs/codex-reviewer/bin/python" -m pip install -r "$SKILL_DIR/requirements-schema.txt"
"$HOME/.venvs/codex-reviewer/bin/python" "$SKILL_DIR/scripts/codex_review.py" custom \
  --no-update-check --schema /absolute/path/schema.json "Review the specified scope"
```

啟動前確認套件與規則有效，完成後再驗證回覆。未指定 `$schema` 時採 Draft 2020-12；已知版本依宣告驗證，未知版本拒絕。參照只能指向同一份文件，不讀取網路或外部檔案；`format` 保持註記語意，不額外檢查 Email 等格式。格式不符會回報欄位位置，`success=false`；格式通過並不代表品質閘門通過。使用者提供的 schema 若剛好與內建 schema 相同，仍按自訂 schema 處理。

## 相容性與保護措施

- 2026-10-01：本機 CLI 0.159.3 catalog 已確認 `gpt-6.1-sol` 支援所有 preset 的 reasoning 等級；native、structured、bounded、custom 與 ultra 的命令預覽，以及 bounded 九項工具停用開關均通過實機檢查。快速 doctor 完成；完整 doctor 完成並保留本機設定、網路與歷史資料等 warning。這些檢查未執行真實模型審查。
- JSONL 相容性測試依官方 0.159.3 event schema 建立離線案例，驗證命令啟動失敗、提早輸出、未完成進度、完整 usage 與 bounded 零工具限制；案例不是模型實跑紀錄。
- 歷史驗證（2026-09-05）：0.153.3 已核對官方原始碼與版本說明；0.153.2 已完成參數、模型清單、停用功能檢查與 Astra standard 限定範圍審查及複查。此紀錄保留舊模型的流程證據，其他模式的真實執行尚未驗證。
- 輸出路徑與範圍、格式、證據或圖片輸入衝突時，退出且不寫入任何輸出；包含符號連結及硬連結別名。
- 限定範圍的 Git 差異按檔名字面比對，`[id].tsx` 不會展開成其他檔名。
- 限定範圍模式額外停用命令工具與搜尋，必要停用功能無法確認時提前失敗。檔案修改及未知輸出事件都會阻止通過；事件偵測不代表副作用發生前的完整攔截。
- Ctrl+C／SIGTERM 先清除子程序再釋放鎖，`execution_status=interrupted`、`success=false`、品質無法確認；退出碼分別為 130／143。Windows Ctrl+Break 同樣視為取消並回傳 130；強制結束程序無法保證產生取消結果檔。

本機驗證使用模擬 CLI 與暫存 Git 專案。完整測試可在上述虛擬環境執行 `python -m unittest discover -s tests -v`；自訂格式測試需要先安裝選填依賴。

2026-09-05 首輪真實審查涵蓋 18 個檔案，約 110 秒完成，發現一個已在本機重現的格式參照問題。修正後只複查格式驗證程式與測試，約 41 秒完成且無發現；原 120 項完整測試之外，另通過新增案例後的 6 項格式驗證測試。這些是單次執行證據，不代表 Astra 審查品質、典型耗時或用量的基準測試。

## Windows 執行相容性

- 與 Codex／Git 的文字交換明確使用 UTF-8，Windows 終端輸出也設定為 UTF-8；無效的模型輸出編碼會回報失敗。
- 輸出檔不再要求 Windows 不提供的 `os.fchmod`。Windows 沿用目錄的存取權限，不宣稱 POSIX `0600` 等同於 Windows 的私人檔案權限；請將審查結果放在僅本人可存取的目錄。
- 使用 Windows 原生檔案鎖限制同一專案的並行審查。程序退出後由系統釋放鎖；沒有可用鎖機制時提前失敗。
- 使用 Windows Job Object 管理啟動器、Codex 與正常建立的子程序。啟動器須先納入管理才能啟動 Codex，取消或逾時會終止整組程序；主代理程序意外退出時由系統清理。若系統限制阻止加入作業物件，提前失敗，不退回無管理的執行模式。
- 已加入 `windows-latest`、Python 3.10／3.13 的無模型測試：`python -m unittest tests.test_windows_compat -v`。本次開發在 macOS；Windows 原生鎖、作業物件、Ctrl+Break 與 Codex 真實執行仍待 Windows CI／實機驗證，不能以 macOS 測試取代。

## Files

- `SKILL.md`：agent workflow、trigger 與 quality gate
- `scripts/codex_review.py`：CLI wrapper
- `scripts/codex_reviewer/updates.py`：npm 優先、standalone bootstrap 與 update cache
- `references/codex_cli_reference.md`：0.159.3 原始碼能力核對、V2 profile 與診斷
- `references/example_prompts.md`：parameterized generic prompts
- `references/review_output_schema.json`：v2 native-compatible schema
- `agents/openai.yaml`：Codex UI metadata

更完整的 CLI 行為與官方來源見 [references/codex_cli_reference.md](references/codex_cli_reference.md)。
