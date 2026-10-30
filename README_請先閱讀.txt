ESG Groq 連線測試補丁

這次只需要上傳 3 個程式檔：app.py、index.html、ai_connection_diagnostic.py。
不要上傳 README，也不要刪除 Supabase 或既有專案。

1. 先備份目前 GitHub 的 app.py 和 index.html，並從網站備份專案。
2. 在 GitHub 專案根目錄上傳上述三個檔案，覆蓋同名檔案。
3. Commit changes，等待 Render Live。
4. 打開網站，在「專案總覽」點「測試 Groq 連線（不傳企業資料）」。
5. 將結果文字貼回 ChatGPT。請勿提供 API Key。

此補丁基於使用者提供的 app(3).py 和 index(4).html，沒有修改資料庫結構，也不會執行整本生成。
此診斷只測試固定短文字，不保證正式生成成功。
