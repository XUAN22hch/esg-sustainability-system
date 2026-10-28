# ESG V6.3 斷點續寫與逾時修正版

1. 先備份 Supabase；此版不清空、不遷移資料庫。
2. 把 app.py、gemini_client.py、index.html 上傳 GitHub 專案根目錄覆蓋同名檔案。
3. Render 部署 Live 後 Ctrl+Shift+R 重新整理。
4. 等先前 report 工作確定為 error/done 後，按一次「一鍵 AI 生成整本永續專章」。
5. 每次只生成一小節，完成後立即寫入 Supabase；已有非待補正文或人工確認內容會跳過。
6. Gemini 模型清單在每個 Render 程序中快取，避免每小節重查；連線逾時最多重試 2 次。
7. 進度以每小節更新，前端最多等待 15 分鐘。免費 Render 重啟仍可能中斷背景執行緒；7 分鐘未更新的舊工作可在下次啟動時標為失敗。
8. 無法保證免費 API 不會限流或逾時。未完成的小節可以下次接續。資料不足標示待補，不保證整本報告頁數。
9. 免費 Gemini API 不宜直接處理未獲授權的客戶機密資料。

僅做語法與 ZIP 完整性檢查，尚未在實際 Render/Supabase/Gemini 端到端測試。
