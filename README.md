# AI Model Service & MLOps｜AI 模型服務與維運治理

A configurable local model-service console with evaluation gates, independent approvals, service-configuration release and rollback, operational visibility, and human review.

**作品一：先判斷，再讓人能確認。** 用循環水泵異常紀錄展示如何管理 AI 模型服務、保留原始判讀、分開揭露安全政策修正，避免把單次模型回答直接當作可發布的產品。

## 版本與責任

核心產品 **0.4.1**；獨立介紹站 **1.1.0**。核心是一個需登入的本機控制台；showcase 是不用登入、只操作合成資料的互動介紹站。這是可交付的本機產品流程，不是品質已合格的生產 AI。

## 可操作功能

- 模型產物驗證、按需啟停、真實 Q8 推論、有限佇列、Trace 與原始／政策後結果分層。
- 版本化客製設定：品牌、術語、必填欄位、生成參數、評測門檻、排隊規則及告警；預覽與差異確認後保存新版本，不改寫歷史。
- 固定評測、逐筆錯例、版本比較、發布檢查、獨立 reviewer 核准、管理者套用及回滾；候選未過門檻不發布。
- 單一既有 Q8 的服務設定 A→B→A 實測；不把設定版本回滾宣稱為不同模型權重切換。
- 請求與資源監控、Trace 鑽取、有界負載／安全停止／比較、告警觸發與恢復、本機 MLflow 實驗同步。
- 人工複核收件匣：接受、修正、退回、資訊不足、版本衝突與雙人高風險複核；原模型輸出不覆寫。
- 明確同意保存、到期清理、去原始內容的業務備份與隔離唯讀還原；還原不能恢復有效發布權威。

## 循環水泵情境與客製差異

輸入為合成設備／維修事件與選定設定；流程為模型生成→Schema 驗證→安全政策→人工複核／品質閘門；輸出為判讀、理由、需複核狀態及可追查版本。高亮位置只是待查線索，不是維修或安全決策。

| 設定 | 維修服務團隊 | 設備管理團隊 |
| --- | --- | --- |
| 建議生成 | 320 tokens／seed 42 | 384 tokens／seed 84 |
| 原始任務門檻 | 80% | 85% |
| 發布核准 | 一名獨立 reviewer | 兩名不同 reviewer＋維護確認 |
| 人工複核 | 8 字理由、三欄位 | 16 字理由、四欄位及證據參照 |
| 延遲告警 | 2000 ms | 1500 ms |

## 品質結果：改善與失敗一起公開

0.4.1 的三組資料**都已觀測**，不是新的未見保留集：

| 資料 | Raw 任務正確 | 政策後正確 |
| --- | --- | --- |
| 原回歸集 | 16／20 | 18／20 |
| 已觀測 holdout-v1 | 14／14 | 14／14 |
| 已觀測 holdout-v2 | 14／22 | 17／22 |

政策後安全違規為零，但第三組有退步；政策後不必要人工介入率為 50%、66.7%、75%。triage-v5／v5.1 **未發布**，triage-v4 僅是既有回歸基線。不能由合成回歸推論工廠泛化；人工與 AI 標註也未經獨立業務專家驗證。完整定義見 [品質契約](docs/判讀品質驗證契約.md)及 [本機收尾驗收](docs/本機產品收尾驗收.md)。

## 本機啟動

Windows、Python 3.11、Node.js 24、pnpm 11；從儲存庫根目錄執行：

```powershell
.\scripts\bootstrap.ps1
.\scripts\start.ps1
```

控制台：<http://127.0.0.1:8120/>。首次建立自己的管理者帳號，無展示帳號／預設密碼。啟動不自動載入模型。公開來源**沒有 Q8 權重及引擎 binary**；Fixture 只能作工程測試，不能正式發布。真實模式需自備授權且符合產物契約的模型與引擎，見 [模型來源契約](docs/模型來源與產物契約.md)。

不需要模型即可查看介紹站：

```powershell
cd showcase
pnpm install --frozen-lockfile
pnpm test
pnpm build
pnpm preview
```

開啟 <http://127.0.0.1:4178/>；準備好依賴與建置後可離線展示，不呼叫 8120 API。

## 驗證、架構與限制

`scripts/verify.ps1` 驗證一般工程路徑，不下載權重；`-RealModel` 僅適用於已自備授權產物的機器。`backend` 是服務／評測／治理，`frontend` 是控制台，`profiles`／`schemas` 是客製契約，`tests` 是隔離測試，`showcase` 是獨立介紹。

歷史本機收尾為後端 97／97、前端 17／17、主產品 E2E 19／19。公開副本當次驗證另見 [PUBLIC_VERIFICATION](docs/PUBLIC_VERIFICATION.md)；沒有重新做 GPU 品質評測。某些證據產生器需要不公開的歷史 reports；已審核的展示摘要隨 showcase 提供，不承諾能從公開副本重建全部私有報告。

尚未實跑 Docker／Linux 引擎／Prometheus 服務端；Model Registry、真正串流、相容端點、完整帳號管理與不同權重部署仍有缺口。SQLite、loopback HTTP 與單控制程序不適合直接暴露網路。程式保留既有 MIT，原創合成案例 CC BY 4.0；第三方與模型按原授權，見 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。

## 公開範圍與資料安全

此儲存庫是 2026-10-10 整理的公開來源快照，不包含舊 Git 歷史、帳號／Session、正式 SQLite、上傳文件、原始複核證據、工單內文、備份、模型權重、私有引擎或完整本機 reports。測試內固定密碼／角色是**合成測試資料**，不會植入產品帳號。執行後建立的私人狀態由 .gitignore 排除，請勿強制加入 Git。

GitHub 上公開的是原始碼與文件；本次沒有啟用 GitHub Pages，也沒有公開核心後端服務。README 中的 127.0.0.1 只適用於執行者自己的電腦。既有鎖版依賴與授權摘要保留，不宣稱完整供應鏈、資安或生產部署認證。

控制台截圖來自合成端對端測試，可能含 e2eadmin、測試 ID、相對暫存路徑及測試 hash；它們不是正式帳號或有效核准憑證。公開副本補齊測試暫存目錄初始化及最小歷史來源摘要，未改業務邏輯或評分斷言，見 [當次驗證](docs/PUBLIC_VERIFICATION.md)。

[公開交付契約](docs/PUBLIC_RELEASE.md) · [原產品操作資料](LOCAL_PRODUCT_GUIDE.md)

## 系列作品 / Portfolio

同一個合成情境：`EQ-PUMP-001` 循環水泵。三個產品**各自運作**，不是已串接的自動維修系統，也不控制真實設備。

| 儲存庫 | 英文｜中文名稱 | 責任 |
| --- | --- | --- |
| [pump-portfolio-showcase](https://github.com/bdf10019/pump-portfolio-showcase) | Pump Portfolio Showcase｜循環水泵互動展示 | 影片式入口與捲動式 3D 說明 |
| [ai-model-service-mlops](https://github.com/bdf10019/ai-model-service-mlops) | AI Model Service & MLOps｜AI 模型服務與維運治理 | 判斷資訊與管理模型 |
| [mcp-tool-governance](https://github.com/bdf10019/mcp-tool-governance) | MCP Tool Governance｜MCP 工具整合與授權治理 | 經授權及核准執行工具 |
| [multimodal-document-review](https://github.com/bdf10019/multimodal-document-review) | Multimodal Document Review｜多模態文件理解與人工複核 | 對照來源並整理文件 |
