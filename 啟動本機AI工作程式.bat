@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ESG 本機 Ollama 工作程式
echo 請先在 Render Environment 設定 LOCAL_OLLAMA_TOKEN。
echo.
set /p LOCAL_OLLAMA_TOKEN=請貼上與 Render 相同的 LOCAL_OLLAMA_TOKEN：
set "ESG_SERVER_URL=https://esg-sustainability-workspace.onrender.com"
set "OLLAMA_MODEL=qwen2.5:3b"
echo.
echo 正在連接本機 AI，請保持視窗開啟。
python local_ollama_worker.py
echo.
echo 程式已停止。請確認 Python、Ollama 及網路連線。
pause
