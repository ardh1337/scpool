@echo off
cd /d "%~dp0"
echo ====================================================
echo  Menjalankan SCPool (Foxcom 32-Port)
echo  Coded By Ardh1337
echo  Dashboard: http://127.0.0.1:8000
echo ====================================================
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause

