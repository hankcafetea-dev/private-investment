#!/bin/bash
# 把你的自選股清單存進 GitHub Secret（WATCHLIST_CSV），不會進公開的程式碼
# 用法：bash tools/set_watchlist.sh 你的檔案.csv   （預設讀 repo 根目錄的 watchlist.csv）
set -euo pipefail
cd "$(dirname "$0")/.."
F="${1:-watchlist.csv}"
if [ ! -f "$F" ]; then
  echo "找不到 $F。先複製 watchlist.example.csv 為 watchlist.csv，用 Excel 改好（存成 CSV UTF-8）再執行。"
  exit 1
fi
gh secret set WATCHLIST_CSV < "$F"
echo "已更新 WATCHLIST_CSV。要讓網頁馬上換成新清單，可以執行："
echo "  gh workflow run auto.yml -f task=intraday"
