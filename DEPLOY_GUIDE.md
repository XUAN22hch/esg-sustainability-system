# V6.2 Gemini 免費版部署說明

1. 先備份 Supabase。此版不刪除或清空既有資料。
2. 將 ZIP 解壓縮後的 `app.py`、`ai_review.py`、`gemini_client.py`、`requirements.txt` 上傳到 GitHub 專案根目錄（覆蓋同名檔案）。其餘檔案沿用 V6，不需更動。
3. Render → Environment 確認 `GEMINI_API_KEY` 已設定；不要分享金鑰。
4. 等 Render 顯示 Live，重新整理網站。使用非機密測試資料先測試。
5. 按一次「一鍵 AI 生成整本永續專章」。已完成的非待補充小節不會被重寫；已確認的草稿仍受保護。

注意：Gemini 免費額度有速率與每日限制，並非無限量。若 429，保留已生成內容，稍後重試。免費服務的資料處理條款不適合未經授權的機密客戶資料。背景執行緒在 Render Free 重新部署時仍可能中斷，須待舊工作失效後再啟動。此版為程式修補，已做靜態語法檢查，未在真實 Render/Gemini 環境端對端測試。預設模型 `gemini-2.5-flash`，若帳戶不支援，須依 AI Studio 可用模型調整 `GEMINI_MODEL`。
