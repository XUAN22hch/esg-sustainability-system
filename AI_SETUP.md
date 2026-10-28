# ESG V4 — AI 條文候選比對

1. 解壓縮 ZIP，將程式檔案放到 GitHub 專案根目錄，覆蓋舊版檔案。不要上傳本地 SQLite 資料庫。
2. Render → Web Service → Environment → Add Environment Variable：`OPENAI_API_KEY`（請自行填入 OpenAI 平台建立的金鑰）。
3. 可選：`OPENAI_MODEL=gpt-4o-mini`。模型可能隨 API 供應情況變動。
4. 設定 `APP_PASSWORD` 並確認 Render 部署成功。不要把金鑰寫進 GitHub 或前端 HTML。
5. 開啟網站 → 建立專案 → 上傳可解析 PDF/DOCX/XLSX 或輸入文字 → S1/S2 條文檢核 →「AI 分析文件並提出條文缺漏」。
6. 點「帶入人工檢核表」，核對來源後再自行選擇檢核狀態與儲存。

## 重要限制
- 本版使用現有的 20 組條文摘要，**不是完整逐條 IFRS S1/S2 符合性驗證**。
- 目前最多送出前 55 個證據片段，每段最多 1300 字；超過的資料未納入該次 AI 分析。
- 只傳客戶解析文字，不傳 IFRS 準則全文；準則正文涉及授權。
- AI 證據 ID 與片段文字可回溯，但仍需人工核對原文件。
- 不支援掃描 PDF OCR；也沒有建立正式版控、角色權限、資料庫備份或法規版本更新機制。
- SQLite 在 Render 免費服務上可能隨重啟/部署遺失。請勿用於客戶機密資料或正式生產環境。
- API 依使用量收費，與 ChatGPT 訂閱分開；可先用非機密測試資料驗證。
