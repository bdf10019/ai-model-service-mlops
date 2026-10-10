# 公開副本驗證 / Public-source verification

日期：2026-10-10。核心 0.4.1／showcase 1.1.0 的公開來源快照。

## 當次實跑

| 範圍 | 結果 |
| --- | --- |
| Python 後端 | 97／97 通過，無 xfail、跳過 |
| 控制台元件 | 17／17 通過 |
| 獨立介紹站元件 | 8／8 通過 |
| 控制台與介紹站 | TypeScript／正式建置通過 |

公開副本獨立執行來源／測試；Python 使用既有 Python 3.11 虛擬環境，前端使用 Node.js 24、pnpm 11，以原鎖檔安裝。Python 環境、node_modules、tmp、state 均不提交。既有 FastAPI/httpx 與 anyio 棄用提示仍保留。

## 乾淨來源修補

未改後端業務程式或評分斷言；只修公開副本的兩個測試準備問題：

1. tests/conftest.py 建立已忽略的 tmp 父目錄，配合原測試 basetemp。
2. test_reviews_quality.py 改讀 tests/fixtures/holdout-first-report-provenance.json；該最小來源摘要僅包含歷史合成報告 SHA、資料集名稱與公開範圍，不帶整份私有 reports、逐筆原文或執行識別碼。

本機原專案未因這兩項公開副本修補而改寫。

## 歷史與當次分開

README 中真實 Q8 品質、發布／回滾、MLflow、主產品 E2E 是既有本機驗收結果。本次沒有重做真實 GPU 品質、瀏覽器 E2E、Docker 或 Linux 部署，不由一般測試推論品質候選已通過；triage-v5／v5.1 仍未發布。

隨附控制台截圖來自合成端對端測試，可能顯示 e2eadmin、測試 ID、相對暫存路徑、測試 hash 及合成事件；不是正式帳號、私人文件或可復活的登入／核准憑證。逐張檢視未見密碼、Token、Email 或私人使用者目錄。截圖中的測試身分不會隨來源建立成產品帳號。

## 發布邊界

只公開來源與文件，不啟用 Pages 或公開本機 API；不帶模型權重、私人引擎、實際帳號資料庫、上傳／複核原始證據或完整 reports。首次啟動須建立自己的帳號；真實模型須自備授權產物。規則掃描與測試不代表完整資安、生產部署或業務驗證認證。

