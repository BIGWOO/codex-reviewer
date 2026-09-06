# Codex Reviewer 操作細節

只讀選定模式需要的章節。一般使用先看 [SKILL.md](../SKILL.md)。

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

使用 `--dry-run` 檢查最後命令，絕不安裝／更新 CLI，即使傳入更新旗標。`doctor` 預設只診斷；單次明確傳入 `--update-check` 或 `--force-update-check` 才允許更新。使用 `--codex-bin /absolute/path/codex` 或 `CODEX_REVIEWER_CODEX_BIN` 選定並自行管理固定 binary。需要附加 repo-specific criteria 時用 `--instructions`，不要把 scope 與 prompt 偷混進 native positional argument。

跨 repo 的 generic review 必須使用 `--scope-manifest <JSON>` 宣告每個 repo 的 `uncommitted`、`base`、`commit` 或 `range` scope。Deep custom review 必須提供 manifest 或 `--review-range`，避免未 sizing 的廣域審查。

預設 `--minimal-context` 會停用 plugins、apps 與 multi-agent，但不代表停用一般 skill discovery 或所有 MCP。Bounded mode 另以 JSONL policy 強制零 command／MCP／web／browser／collaboration tools。只有非 bounded review 確定需要完整 context 時才用 `--full-context`。

Bounded 啟動前另確認 CLI 能停用命令工具與設定搜尋停用；檔案修改及未知事件均不得判定通過。這是啟動控制加事後偵測，不代表所有工具在發生副作用前都能被攔截。Ctrl+C／SIGTERM 會先清除子程序再釋放鎖，結果為 `interrupted`／`inconclusive`，退出碼分別是 130／143。

自訂 `--schema` 需在使用者選定的 Python 環境安裝 `requirements-schema.txt`；不得在執行中自動安裝。缺套件、規則無效或外部參照都要在模型啟動前失敗；回覆需通過本機格式驗證。內建 schema 保持零額外依賴。輸出路徑與輸入衝突時不得寫入任何結果檔。

Windows 使用 UTF-8、原生檔案鎖與 Job Object 程序管理；納管失敗不得繞過控制重試。Windows 檔案沿用目錄存取權限，不以 `chmod` 宣稱私人權限。Ctrl+Break 視為取消；強制結束時不保證有結果檔。Windows 驗證狀態與無模型測試方式見 [README](../README.md#windows-執行相容性)，macOS 測試不代表 Windows 實機通過。

`--ignore-user-config` 只忽略 base user config，仍保留 project rules；`--isolated` 則等同 `--ignore-user-config --ignore-rules`，兩者都不保證停用 skill discovery。Bounded mode固定使用 `--ignore-user-config` 並拒絕 `--isolated`。

`--max-tool-calls` 與 `--max-jsonl-bytes` 對既有模式預設 unlimited；bounded 固定零工具並預設最多 262144 JSONL bytes。Policy violation、timeout 或缺少終態都不得視為 final success。Scope 不變時禁止只替換 mode／preset／隔離旗標重試 timeout；先縮小 packet 或由 caller 明確決定下一步。

Bounded review 預設 `--hard-timeout 600` 並停用 idle timeout（effective `--idle-timeout 0`）；零工具推理可能長時間沒有 JSONL event，不能把靜默本身當成 hang。Native／其他 generic mode 維持 hard 300／idle 180。Caller 顯式傳入的 timeout 仍優先。
