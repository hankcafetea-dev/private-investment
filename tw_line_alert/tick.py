#!/usr/bin/env python3
"""
台股盤中警示（GitHub Actions 版）
=================================
本機版 intraday.py 是一支從開盤跑到收盤的長程式（每 30 秒看一次）。
GitHub Actions 版改成「每 5 分鐘被叫醒一次、只檢查一輪就結束」：

  - 警示規則（到價、停損停利、漲跌幅級距、漲跌停、預估爆量、重大訊息、新聞、收盤摘要）
    直接沿用 intraday.py 的函式，判斷邏輯完全一樣。
  - 行情改用證交所「基本市況報導」公開資料（mis.twse.com.tw，上市＋上櫃，不用金鑰），
    抓不到的個股再用 Yahoo 奇摩股市備援。
  - 當天已提醒過哪些項目、待合併送出的一般警示、推播則數…存成加密檔
    （資料分支 private/state.enc.json），下一輪接著用。
  - 沒有設定 LINE 金鑰時照樣判斷警示，只是不推播，警示會出現在網頁「我的盯盤」。

因為 repo 與 Actions 執行紀錄是公開的，本程式不在螢幕上印出任何股票代號、價格或持股，
錯誤訊息也只寫進加密狀態檔。

用法
  python tw_line_alert/tick.py --data-dir _data
  python tw_line_alert/tick.py --data-dir _data --now "2026-11-13 10:30"   # 指定時間（測試用）
  python tw_line_alert/tick.py --data-dir _data --dry-run                  # 不送 LINE
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools"))

import intraday as core  # noqa: E402
from secure import decrypt_json, encrypt_json, site_password  # noqa: E402

TZ = core.TZ
KEEP_DAYS = 10
UA = {"User-Agent": "Mozilla/5.0 (personal-alert)"}
MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TWSE_DAY_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_DAY_ALL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"

_errors: list[str] = []


def _log(text: str) -> None:
    """取代 intraday.log：錯誤只進加密狀態檔，不印到公開的 Actions 紀錄。"""
    _errors.append(f"{dt.datetime.now(TZ):%m/%d %H:%M} {str(text)[:300]}")


core.log = _log  # intraday.fetch_news / fetch_announcements 內部呼叫的 log 也一起改


def roc(d: dt.date) -> str:
    return f"{d.year - 1911}{d:%m%d}"


def fnum(x):
    if x in (None, "", "-", "--"):
        return None
    v = core.fnum(x)
    return v if v else None


# ----------------------------------------------------------------------------
# 自選股
# ----------------------------------------------------------------------------
def load_watch() -> tuple[list[dict], str]:
    text = os.getenv("WATCHLIST_CSV", "").strip()
    if text:
        return core.parse_watchlist(text), "secret"
    return core.parse_watchlist((ROOT / "watchlist.example.csv").read_text(encoding="utf-8-sig")), "example"


# ----------------------------------------------------------------------------
# 行情（證交所 MIS 公開資料 → Yahoo 備援）
# ----------------------------------------------------------------------------
def fetch_mis(codes: list[str]) -> dict[str, dict]:
    import requests

    s = requests.Session()
    s.headers.update(UA)
    try:
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, dict] = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        ex = "|".join(f"tse_{c}.tw|otc_{c}.tw" for c in chunk)
        try:
            r = s.get(MIS_URL, params={"ex_ch": ex, "json": "1", "delay": "0", "_": int(time.time() * 1000)},
                      timeout=15)
            r.raise_for_status()
            arr = r.json().get("msgArray", [])
        except Exception as e:  # noqa: BLE001
            _log(f"證交所即時行情讀取失敗：{type(e).__name__}")
            continue
        for m in arr:
            c = str(m.get("c", "")).strip()
            y = fnum(m.get("y"))
            z = fnum(m.get("z"))
            if z is None:  # 最近一筆沒成交：用最佳買價，再不行用昨收
                for k in ("b", "a"):
                    first = str(m.get(k) or "").split("_")[0]
                    if fnum(first):
                        z = fnum(first)
                        break
            if z is None:
                z = fnum(m.get("pz")) or y
            if not c or z is None:
                continue
            out[c] = {
                "name": m.get("n") or "", "close": z, "ref": y,
                "change_rate": (z / y - 1) * 100 if y else 0.0,
                "open": fnum(m.get("o")), "high": fnum(m.get("h")), "low": fnum(m.get("l")),
                "total_volume": fnum(m.get("v")) or 0,  # 張
                "limit_up": fnum(m.get("u")), "limit_down": fnum(m.get("w")),
                "date": str(m.get("d") or ""), "time": str(m.get("t") or m.get("%") or ""),
                "market": m.get("ex") or "", "source": "證交所",
            }
    return out


def fetch_yahoo(codes: list[str]) -> dict[str, dict]:
    import requests

    out = {}
    for c in codes:
        for suf, mk in ((".TW", "tse"), (".TWO", "otc")):
            try:
                r = requests.get(YAHOO_URL.format(sym=c + suf), headers=UA, timeout=15)
                if r.status_code != 200:
                    continue
                meta = r.json()["chart"]["result"][0]["meta"]
            except Exception:  # noqa: BLE001
                continue
            px, ref = meta.get("regularMarketPrice"), meta.get("chartPreviousClose") or meta.get("previousClose")
            if not px:
                continue
            t = dt.datetime.fromtimestamp(meta.get("regularMarketTime", time.time()), TZ)
            out[c] = {
                "name": meta.get("shortName") or "", "close": float(px), "ref": ref,
                "change_rate": (px / ref - 1) * 100 if ref else 0.0,
                "open": None, "high": meta.get("regularMarketDayHigh"), "low": meta.get("regularMarketDayLow"),
                "total_volume": (meta.get("regularMarketVolume") or 0) / 1000,
                "limit_up": round(ref * 1.1, 2) if ref else None, "limit_down": round(ref * 0.9, 2) if ref else None,
                "date": f"{t:%Y%m%d}", "time": f"{t:%H:%M:%S}", "market": mk, "source": "Yahoo",
            }
            break
    return out


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    q = fetch_mis(codes)
    missing = [c for c in codes if c not in q]
    if missing:
        q.update(fetch_yahoo(missing))
    return q


def fetch_prev_volume(today: dt.date) -> dict[str, float]:
    """昨量（張）：證交所／櫃買中心「最近一個交易日」收盤資料；若已經是今天的就不用。"""
    out: dict[str, float] = {}
    for url, code_k, vol_k in ((TWSE_DAY_ALL, "Code", "TradeVolume"), (TPEX_DAY_ALL, "SecuritiesCompanyCode", "TradingShares")):
        try:
            rows = core.http_get(url).json()
        except Exception as e:  # noqa: BLE001
            _log(f"昨量資料讀取失敗：{type(e).__name__}")
            continue
        for r in rows:
            if str(r.get("Date", "")).strip() == roc(today):
                break  # 已經是今天收盤後的資料，不能當昨量
            v = fnum(r.get(vol_k))
            if v:
                out[str(r.get(code_k, "")).strip()] = v / 1000
    return out


# ----------------------------------------------------------------------------
# 推播（沒有 LINE 金鑰就只記錄）
# ----------------------------------------------------------------------------
def send(D: dict, text: str, now: dt.datetime, cap: int, dry: bool, force: bool = False) -> None:
    text = text.strip()
    if not text:
        return
    if len(text) > 4900:
        text = text[:4880] + "\n…（內容過長已截斷）"
    rec = {"t": f"{now:%H:%M}", "text": text, "status": "sent"}
    token, user = os.getenv("LINE_CHANNEL_TOKEN", "").strip(), os.getenv("LINE_USER_ID", "").strip()
    if not force and D["sent"] >= cap:
        rec["status"] = "over_cap"
    elif not (token and user):
        rec["status"] = "no_line"
    elif dry:
        rec["status"] = "dry_run"
    else:
        if not force and D["sent"] == cap - 1:
            text += "\n\n（今日推播已達上限，之後的警示只顯示在網頁）"
            rec["text"] = text
        try:
            import requests

            r = requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"to": user, "messages": [{"type": "text", "text": text}]}, timeout=15)
            if r.status_code != 200:
                rec["status"] = f"failed_{r.status_code}"
                _log(f"LINE 推播失敗 {r.status_code}: {r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            rec["status"] = "failed"
            _log(f"LINE 推播失敗：{type(e).__name__}")
        if rec["status"] == "sent" and not force:
            D["sent"] += 1
    D["messages"].append(rec)


# ----------------------------------------------------------------------------
# 狀態檔
# ----------------------------------------------------------------------------
def new_day() -> dict:
    return {"fired": [], "seen_ann": [], "seen_news": [], "digests_done": [], "sent": 0, "closed": False,
            "pending": [], "last_flush": None, "last_digest": None, "prev_volume": None,
            "alerts": [], "messages": [], "ticks": 0, "trading_day": None}


def load_state(path: Path, pw: str) -> dict:
    if path.exists():
        try:
            return decrypt_json(json.loads(path.read_text(encoding="utf-8")), pw)
        except Exception:  # noqa: BLE001
            print("::warning::加密狀態檔無法用目前的 SITE_PASSWORD 解開（換過密碼？），改從空白狀態開始")
    return {"days": {}}


def tick(now: dt.datetime, state: dict, dry: bool) -> dict:
    s = core.load_settings()
    watch, source = load_watch()
    codes = [w["code"] for w in watch]
    day_key = f"{now:%Y%m%d}"
    days = state.setdefault("days", {})
    D = days.setdefault(day_key, new_day())
    D["ticks"] += 1
    st = SimpleNamespace(fired=set(D["fired"]), seen_ann=set(D["seen_ann"]), seen_news=set(D["seen_news"]))

    quotes = fetch_quotes(codes) if codes else {}
    if codes and not quotes:
        _log("這一輪所有行情來源都讀不到")
    trading_day = any(q.get("date") == day_key for q in quotes.values())
    if quotes:
        D["trading_day"] = trading_day
    t = now.time()
    in_session = trading_day and core.hm("09:00") <= t <= core.hm("13:31")

    if in_session and D["prev_volume"] is None:
        pv = fetch_prev_volume(now.date())
        D["prev_volume"] = {c: pv[c] for c in codes if c in pv}
    for c, q in quotes.items():
        q["yesterday_volume"] = (D["prev_volume"] or {}).get(c)

    info = {c: {"name": q["name"], "limit_up": q["limit_up"], "limit_down": q["limit_down"],
                "reference": q["ref"]} for c, q in quotes.items()}
    urgent: list[str] = []
    pending: list[str] = D["pending"]

    def note(kind: str, msg: str, is_urgent: bool):
        D["alerts"].append({"t": f"{now:%H:%M}", "kind": kind, "urgent": is_urgent, "msg": msg})

    if in_session and not D["closed"]:
        for w in watch:
            q = quotes.get(w["code"])
            if not q:
                continue
            for is_urgent, m in core.price_alerts(w, q, info.get(w["code"], {}), st, s, now):
                (urgent if is_urgent else pending).append(m)
                note("price", m, is_urgent)

    if trading_day and not D["closed"] and t >= core.hm(s["start_time"]):
        if s["announce_enabled"]:
            for m in core.fetch_announcements(s, set(codes), st, False):
                pending.append(m)
                note("announce", m, False)
        if s["news_enabled"]:
            for tt in s["news_digest_times"]:
                if t >= core.hm(tt) and tt not in D["digests_done"]:
                    D["digests_done"].append(tt)
                    since = dt.datetime.fromisoformat(D["last_digest"]) if D["last_digest"] else now - dt.timedelta(hours=16)
                    news = core.fetch_news(watch, st, s, since, False)
                    D["last_digest"] = now.isoformat()
                    if news:
                        pending.append(f"📰 新聞摘要 {tt}\n" + "\n".join(news))
                        note("news", f"新聞摘要 {tt}：{sum(1 for x in news if x.startswith('【'))} 檔有新消息", False)
                        D["last_flush"] = None  # 新聞到點就送

    cap = int(s["max_messages_per_day"])
    closing = trading_day and t >= core.hm(s["close_summary_time"]) and not D["closed"]
    last_flush = dt.datetime.fromisoformat(D["last_flush"]) if D["last_flush"] else None
    if urgent:
        send(D, f"⏰ {now:%H:%M} 關鍵警示\n" + "\n\n".join(urgent + pending), now, cap, dry, force=True)
        pending.clear()
        D["last_flush"] = now.isoformat()
    elif pending and (last_flush is None or (now - last_flush).total_seconds() >= s["digest_minutes"] * 60 or closing):
        send(D, f"⏰ {now:%H:%M}\n" + "\n\n".join(pending), now, cap, dry)
        pending.clear()
        D["last_flush"] = now.isoformat()

    if closing and quotes:
        send(D, core.close_summary(watch, quotes, info, now.date()), now, cap, dry, force=True)
        D["closed"] = True
        note("close", "已送出收盤摘要", False)

    D["fired"], D["seen_ann"], D["seen_news"] = sorted(st.fired), sorted(st.seen_ann), sorted(st.seen_news)
    for k in sorted(days)[:-KEEP_DAYS]:
        del days[k]
    if quotes:  # 休市日照樣更新快照（顯示最近一個交易日收盤）
        state["snapshot"] = {"at": now.isoformat(timespec="seconds"), "trading_day": trading_day, "quotes": quotes}
    state["last_tick"] = now.isoformat(timespec="seconds")
    state["watch_source"] = source
    state["errors"] = (state.get("errors", []) + _errors)[-50:]
    state["line_configured"] = bool(os.getenv("LINE_CHANNEL_TOKEN", "").strip() and os.getenv("LINE_USER_ID", "").strip())
    state["max_messages_per_day"] = cap
    return state


def main():
    ap = argparse.ArgumentParser(description="台股盤中警示（GitHub Actions 版，每次只檢查一輪）")
    ap.add_argument("--data-dir", default="_data")
    ap.add_argument("--now", default="", help="指定台北時間，例 2026-11-13 10:30（測試用）")
    ap.add_argument("--dry-run", action="store_true", help="不送 LINE（訊息只記錄在網頁）")
    a = ap.parse_args()

    pw = site_password()
    if not pw:
        print("::warning::尚未設定 SITE_PASSWORD，私人資料無法加密保存，盤中監控這次略過。")
        return
    os.environ.setdefault("CLOSE_SUMMARY_FOOTER", "盤後籌碼報告約 21:40 自動更新到網頁")
    now = dt.datetime.strptime(a.now, "%Y-%m-%d %H:%M").replace(tzinfo=TZ) if a.now else dt.datetime.now(TZ)
    path = Path(a.data_dir) / "private" / "state.enc.json"
    state = tick(now, load_state(path, pw), a.dry_run)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(encrypt_json(state, pw)), encoding="utf-8")
    D = state["days"].get(f"{now:%Y%m%d}", {})
    # 公開紀錄只印筆數，不印內容
    print(f"盤中檢查完成 {now:%Y-%m-%d %H:%M}｜交易日：{D.get('trading_day')}｜"
          f"今日警示 {len(D.get('alerts', []))} 筆｜訊息 {len(D.get('messages', []))} 則｜錯誤 {len(_errors)} 筆")


if __name__ == "__main__":
    main()
