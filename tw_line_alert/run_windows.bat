@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
python intraday.py >> logs\console.log 2>&1
