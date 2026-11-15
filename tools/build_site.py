#!/usr/bin/env python3
"""
組出 GitHub Pages 網站（_site/）
  公開：site/ 的網頁＋盤後籌碼報告 JSON（證交所、集保公開資料整理，不含任何個人資料）
  加密：自選股、持股成本、提醒價、盤中快照、警示與 LINE 紀錄 → data/private.enc.json
        （沒有 SITE_PASSWORD 就不產生，網頁會顯示「私人區尚未設定」）

用法：python tools/build_site.py --data-dir _data --out _site
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tw_line_alert"))

from secure import decrypt_json, encrypt_json, site_password  # noqa: E402

TZ8 = dt.timezone(dt.timedelta(hours=8))
KEEP_REPORTS = 40  # 網頁保留最近約兩個月的盤後報告


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="_data")
    ap.add_argument("--out", default="_site")
    a = ap.parse_args()
    data, out = Path(a.data_dir), Path(a.out)

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    (out / ".nojekyll").write_text("", encoding="utf-8")
    (out / "data" / "postmarket").mkdir(parents=True, exist_ok=True)

    # ---- 公開：盤後報告 ----
    reports = sorted((data / "postmarket").glob("[0-9]" * 8 + ".json"), reverse=True)[:KEEP_REPORTS]
    for p in reports:
        shutil.copy2(p, out / "data" / "postmarket" / p.name)
    dates = [p.stem for p in reports]
    (out / "data" / "postmarket" / "index.json").write_text(json.dumps({"dates": dates}), encoding="utf-8")

    # ---- 加密：私人區 ----
    pw = site_password()
    private_ok = False
    last_tick = None
    if pw:
        from tick import load_watch  # 與盤中檢查同一份自選股來源

        watch, source = load_watch()
        sp = data / "private" / "state.enc.json"
        state = {}
        if sp.exists():
            try:
                state = decrypt_json(json.loads(sp.read_text(encoding="utf-8")), pw)
            except Exception:  # noqa: BLE001
                print("::warning::狀態檔無法用目前的 SITE_PASSWORD 解開，私人區先只放自選股設定")
        last_tick = state.get("last_tick")
        days = {}
        for k, D in sorted(state.get("days", {}).items(), reverse=True):
            days[k] = {kk: D.get(kk) for kk in ("alerts", "messages", "sent", "closed", "trading_day", "ticks")}
        pm_watch = None
        if reports:
            js = json.loads(reports[0].read_text(encoding="utf-8"))
            cols = js["stocks"]["cols"]
            codes = {w["code"] for w in watch}
            pm_watch = {"date": js["date"], "cols": cols,
                        "rows": [r for r in js["stocks"]["rows"] if str(r[0]) in codes]}
        payload = {
            "generated_at": dt.datetime.now(TZ8).isoformat(timespec="seconds"),
            "watch": watch, "watch_source": source,
            "snapshot": state.get("snapshot"), "last_tick": last_tick,
            "days": days, "errors": state.get("errors", [])[-20:],
            "line_configured": state.get("line_configured", False),
            "max_messages_per_day": state.get("max_messages_per_day"),
            "postmarket_watch": pm_watch,
        }
        (out / "data" / "private.enc.json").write_text(json.dumps(encrypt_json(payload, pw)), encoding="utf-8")
        private_ok = True

    status = {
        "built_at": dt.datetime.now(TZ8).isoformat(timespec="seconds"),
        "postmarket_latest": dates[0] if dates else None,
        "intraday_last_tick": last_tick,
        "private_available": private_ok,
    }
    (out / "data" / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    print(f"網站完成：盤後報告 {len(dates)} 天｜私人區 {'已加密' if private_ok else '未設定 SITE_PASSWORD'}")


if __name__ == "__main__":
    main()
