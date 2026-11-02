@echo off
cd /d "%~dp0"
echo ESG Local Ollama Worker
echo.
set /p "LOCAL_OLLAMA_TOKEN=Enter LOCAL_OLLAMA_TOKEN (same as Render): "
set "ESG_SERVER_URL=https://esg-sustainability-workspace.onrender.com"
set "OLLAMA_MODEL=qwen2.5:3b"
echo.
echo Starting local worker. Keep this window open.
py -3 local_ollama_worker.py
if errorlevel 1 (
  echo.
  echo Python launcher failed. Trying python...
  python local_ollama_worker.py
)
echo.
echo Worker stopped. Review the error above.
pause
