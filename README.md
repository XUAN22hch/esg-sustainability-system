# ESG 永續相關財務資訊專章工作台（雲端部署版）

## Render 部署（無須本機安裝 Python）

1. 建立 GitHub 帳號，新增一個 **Private** repository，例如 `esg-workspace`。
2. 上傳本資料夾內的 `app.py`、`engine.py`、`index.html`、`requirements.txt`、`render.yaml`（上傳檔案，不要把外層 ZIP 直接上傳）。
3. 到 Render.com 用 GitHub 登入，選 **New > Blueprint**，連接該 repository，Render 會讀取 `render.yaml`。
4. 設定 `APP_PASSWORD` 為自訂的強密碼並部署。系統登入帳號可填任何非空文字，密碼必須為所設值。
5. 等待 Deploy live，點 Render 提供的 `https://...onrender.com` 網址使用。

## 限制與重要提醒

- Render 免費服務的檔案系統**不持久**，重啟、重新部署可能使 SQLite 專案資料遺失；正式使用需改為託管 PostgreSQL／持久化磁碟與定期備份。
- 本版使用 HTTP Basic 密碼保護，適合單人內部測試，**不適合直接上傳客戶機密、個資、未公開財報**。正式系統需登入權限、加密、資料保留與刪除政策及安全審查。
- 本版分類和草稿是規則式，不是 AI 模型。掃描 PDF 及圖表自動生成尚未支援；IFRS 符合性不會自動宣稱通過。
- PDF 中文字型在部分雲端環境可能不完整，建議優先匯出 Word。
