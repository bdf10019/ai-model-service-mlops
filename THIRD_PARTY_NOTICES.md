# Third-party notices

本產品程式使用 MIT。原創合成樣本 `data/samples.json`、回歸集 `data/evaluation-v1.json` 及保留集 `data/holdout-v1.json` 採 CC BY 4.0，建議引用「AI 模型服務與 MLOps 原創合成案例，2026」。新保留集由AI協助創作／標註，不是獨立專家或真實事故資料。權重與工具 binary 不提交 Git。

| 項目 | 用途 | 授權／來源 |
|---|---|---|
| Qwen3-1.7B | 已適配 Q8 的 base | Apache-2.0；固定版本文字保存於 docs/licenses/Qwen3-Apache-2.0.txt |
| 既有 ModelForge-TW Q8 產物 | 外部本機匯入，非新訓練 | Base 授權及原來源限制；原程式 MIT 文字在 docs/licenses/ModelForge-MIT.txt |
| llama.cpp | Windows 本機引擎 | MIT，b10665；docs/licenses/llama.cpp-MIT.txt |
| LLVM OpenMP | 既有 binary 相依 | 原 binary 附帶授權，docs/licenses/LLVM-OpenMP.txt |
| CUDA runtime 相依 | 既有 NVIDIA runtime binary | NVIDIA CUDA 相依授權；不隨原始碼散布 binary，不宣稱完整 binary 再散布包 |
| FastAPI／Pydantic／httpx | API 與契約 | MIT／MIT／BSD-3-Clause |
| Uvicorn／psutil | 程序與資源 | BSD-3-Clause |
| MLflow skinny 3.16.0 | 可選本機實驗追蹤SDK | Apache-2.0；https://github.com/mlflow/mlflow |
| SQLAlchemy 2.0.52／Alembic 1.20.0 | 可選MLflow SQLite backend | MIT；https://www.sqlalchemy.org/ ／ https://alembic.sqlalchemy.org/ |
| React／React DOM | 前端 | MIT |
| Vite／TypeScript | 前端建置 | MIT／Apache-2.0 |
| Vitest／Testing Library／jsdom | 前端測試 | MIT |
| Playwright | 瀏覽器驗證 | Apache-2.0；使用既有 Edge，不下載瀏覽器 |
| pnpm | 套件管理 | MIT |

所有直接與傳遞依賴實際版本由 Python lock、可選 `requirements.mlflow.lock.txt` 及 pnpm lock 保存。可選追蹤環境不改核心venv；目前lock為本機Windows驗證，不代表Linux容器已相容。此清單是主要直接依賴摘要，完整第三方 SBOM、CVE 稽核與 binary 再散布授權審查尚未完成。日後正式交付須補齊，不能把此摘要當完整供應鏈認證。

原創 Prompt／Schema／安全規則由本產品實作；既有作品只作唯讀參考，沒有直接複製其服務程式。


第四切片新增holdout-v2與逐筆分析為原創AI撰寫合成資料，CC-BY-4.0；沒有獨立人工／工廠專家驗證。沿用既有授權模型與依賴，未下載新權重、未訓練。legacy_domain.py是本作品0.3.0原始程式的原bytes版本保留。
