#!/bin/bash
cd "$(dirname "$0")"
mkdir -p logs
python3 intraday.py >> logs/console.log 2>&1
