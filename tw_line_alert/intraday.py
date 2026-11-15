#!/usr/bin/env python3
"""
台股盤中警示 → LINE 推播（個人使用）
=====================================
行情：永豐金 Shioaji API（只拿行情，不下單；交易照樣在國泰樹精靈）
推播：LINE 官方帳號 Messaging API（LINE Notify 已停止服務）

功能
  1. 到價提醒：買進提醒價、突破價、停損價、停利價（寫在 watchlist.csv）
  2. 漲跌幅警示：±3%、±5%、±7%、漲停、跌停（每檔每個級距一天只提醒一次）
  3. 預估爆量：依目前時間推估全日量，超過昨量 2 倍提醒
  4. 重大訊息：證交所公開資料中，自選股的重大訊息
  5. 新聞摘要：自選股新聞，固定時間彙整成一則
  6. 收盤摘要：13:35 送出自選股收盤、漲跌、持股損益

同一輪的所有警示合併成「一則」LINE 訊息，並有每日則數上限，避免用完免費額度。

使用
  python intraday.py --test-line   # 送一則測試訊息到 LINE
  python intraday.py --demo        # 假資料模擬一整天，訊息印在螢幕上，不送 LINE
  python intraday.py               # 正式執行：開盤前啟動，收盤摘要送出後自動結束
  python intraday.py --dry-run     # 真實行情，但訊息只印在螢幕上

本程式只做資訊整理與提醒，不構成投資建議，也不會下單。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Taipei")
STATE_DIR = BASE / "state"
LOG_DIR = BASE / "logs"

DEFAULT_SETTINGS = {
    "poll_seconds": 30,                 # 每幾秒檢查一次行情
    "change_levels_pct": [3, 5, 7],     # 漲跌幅警示級距
    "volume_alert_ratio": 2.0,          # 預估全日量 ÷ 昨量 ≥ 此值就提醒
    "volume_alert_after": "09:30",      # 開盤前 30 分鐘量太少，不估算
    "news_enabled": True,
    "news_digest_times": ["09:05", "12:00"],
    "news_per_stock": 3,
    "announce_enabled": True,
    "announce_poll_minutes": 5,
    "announce_urls": ["https://openapi.twse.com.tw/v1/opendata/t187ap04_L"],
    "start_time": "08:50",
    "close_summary_time": "13:35",
    "digest_minutes": 15,               # 一般警示（漲跌幅、爆量、重大訊息）每幾分鐘合併送一次
    "max_messages_per_day": 12,         # 一般警示與新聞每日上限；關鍵警示與收盤摘要不受限
}


# ----------------------------------------------------------------------------
# 設定讀取
# ----------------------------------------------------------------------------
def load_env(path: Path) -> None:
    """讀 .env（KEY=VALUE 格式），不覆蓋已存在的環境變數。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    p = BASE / "settings.json"
    if p.exists():
        s.update(json.loads(p.read_text(encoding="utf-8")))
    return s


def fnum(x):
    try:
        x = str(x).replace(",", "").strip()
        return float(x) if x else None
    except ValueError:
        return None


def load_watchlist() -> list[dict]:
    p = BASE / "watchlist.csv"
    if not p.exists():
        sys.exit("找不到 watchlist.csv，請先依 README 建立自選股清單。")
    return parse_watchlist(p.read_text(encoding="utf-8-sig"))


def parse_watchlist(text: str) -> list[dict]:
    """watchlist.csv 的內容（本機讀檔，或 GitHub Secret WATCHLIST_CSV）→ 自選股清單。"""
    import io

    rows = []
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        code = (r.get("代號") or "").strip()
        if not code:
            continue
        rows.append({
            "code": code,
            "name": (r.get("名稱") or "").strip(),
            "shares": fnum(r.get("持有股數")) or 0,
            "cost": fnum(r.get("成本")),
            "buy_below": fnum(r.get("買進提醒價")),
            "break_above": fnum(r.get("突破提醒價")),
            "stop": fnum(r.get("停損價")),
            "target": fnum(r.get("停利價")),
            "note": (r.get("備註") or "").strip(),
        })
    return rows


def hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


# ----------------------------------------------------------------------------
# LINE 推播
# ----------------------------------------------------------------------------
class Sender:
    def __init__(self, dry: bool, cap: int, state: "State"):
        self.dry, self.cap, self.state = dry, cap, state
        self.token = os.getenv("LINE_CHANNEL_TOKEN", "")
        self.user = os.getenv("LINE_USER_ID", "")
        if not dry and not (self.token and self.user):
            sys.exit("請在 .env 填入 LINE_CHANNEL_TOKEN 與 LINE_USER_ID（見 README 第 2 步）。")

    def send(self, text: str, force: bool = False) -> None:
        text = text.strip()
        if not text:
            return
        if len(text) > 4900:  # LINE 單則文字上限 5000 字
            text = text[:4880] + "\n…（內容過長已截斷）"
        if not force and self.state.sent >= self.cap:
            log(f"[已達每日上限，未送出]\n{text}")
            return
        if not force and self.state.sent == self.cap - 1:
            text += "\n\n（今日推播已達上限，之後的警示只寫入 logs）"
        self.state.sent += 1
        self.state.save()
        if self.dry:
            print("\n" + "=" * 12 + f" LINE 第 {self.state.sent} 則 " + "=" * 12 + f"\n{text}\n")
            log(text)
            return
        import requests
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            json={"to": self.user, "messages": [{"type": "text", "text": text}]},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"LINE 推播失敗 {r.status_code}: {r.text}")
            print(f"LINE 推播失敗 {r.status_code}: {r.text}")
        else:
            log(text)


def log(text: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    now = dt.datetime.now(TZ)
    with (LOG_DIR / f"{now:%Y%m%d}.log").open("a", encoding="utf-8") as f:
        f.write(f"[{now:%H:%M:%S}] {text}\n")


# ----------------------------------------------------------------------------
# 當日狀態（避免重複提醒）
# ----------------------------------------------------------------------------
class State:
    def __init__(self, day: str, demo: bool):
        STATE_DIR.mkdir(exist_ok=True)
        self.path = STATE_DIR / f"{'demo_' if demo else ''}{day}.json"
        d = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.fired: set[str] = set(d.get("fired", []))
        self.seen_ann: set[str] = set(d.get("seen_ann", []))
        self.seen_news: set[str] = set(d.get("seen_news", []))
        self.digests_done: set[str] = set(d.get("digests_done", []))
        self.sent: int = d.get("sent", 0)
        self.closed: bool = d.get("closed", False)

    def save(self) -> None:
        self.path.write_text(json.dumps({
            "fired": sorted(self.fired), "seen_ann": sorted(self.seen_ann),
            "seen_news": sorted(self.seen_news), "digests_done": sorted(self.digests_done),
            "sent": self.sent, "closed": self.closed,
        }, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------------------
# 行情來源
# ----------------------------------------------------------------------------
class SinopacQuotes:
    def __init__(self, codes: list[str]):
        import shioaji as sj

        key, sec = os.getenv("SJ_API_KEY"), os.getenv("SJ_SEC_KEY")
        if not (key and sec):
            sys.exit("請在 .env 填入 SJ_API_KEY 與 SJ_SEC_KEY（見 README 第 1 步）。")
        self.api = sj.Shioaji(simulation=False)
        self.api.login(api_key=key, secret_key=sec)
        self.contracts, self.info = [], {}
        for c in codes:
            k = self.api.Contracts.Stocks[c]
            if k is None:
                print(f"警告：找不到股票代號 {c}，已略過")
                continue
            self.contracts.append(k)
            self.info[c] = {"name": k.name, "limit_up": k.limit_up, "limit_down": k.limit_down,
                            "reference": k.reference}

    def snapshot(self) -> dict[str, dict]:
        out = {}
        for i in range(0, len(self.contracts), 500):
            for s in self.api.snapshots(self.contracts[i:i + 500]):
                out[s.code] = {"close": s.close, "change_rate": s.change_rate, "high": s.high,
                               "low": s.low, "total_volume": s.total_volume,
                               "yesterday_volume": s.yesterday_volume}
        return out

    def close(self):
        try:
            self.api.logout()
        except Exception:  # noqa: BLE001
            pass


class DemoQuotes:
    """模擬行情：隨機漫步，讓各種警示都有機會觸發。"""

    def __init__(self, codes, watch):
        rnd = random.Random(7)
        self.px, self.vol, self.info = {}, {}, {}
        for w in watch:
            ref = w["cost"] or w["buy_below"] or w["stop"] or 100.0
            ref = round(ref * rnd.uniform(0.97, 1.03), 1)
            self.px[w["code"]] = ref
            self.vol[w["code"]] = 0
            self.info[w["code"]] = {"name": w["name"] or w["code"], "reference": ref,
                                    "limit_up": round(ref * 1.1, 1), "limit_down": round(ref * 0.9, 1)}
        self.rnd = rnd

    def snapshot(self):
        out = {}
        for c, p in self.px.items():
            ref = self.info[c]["reference"]
            p = min(max(p * (1 + self.rnd.gauss(0.0005, 0.007)), self.info[c]["limit_down"]), self.info[c]["limit_up"])
            self.px[c] = round(p, 1)
            self.vol[c] += self.rnd.randint(100, 900)
            out[c] = {"close": self.px[c], "change_rate": (self.px[c] / ref - 1) * 100, "high": None,
                      "low": None, "total_volume": self.vol[c], "yesterday_volume": 20000}
        return out

    def close(self):
        pass


# ----------------------------------------------------------------------------
# 警示規則
# ----------------------------------------------------------------------------
def price_alerts(w: dict, q: dict, info: dict, st: State, s: dict, now: dt.datetime) -> list[tuple[bool, str]]:
    c, name, px = w["code"], w["name"] or info.get("name", ""), q["close"]
    chg = q["change_rate"]
    tag = f"{c} {name} {px:g}（{chg:+.2f}%）"
    out = []

    def fire(key: str, msg: str, urgent: bool = False):
        if key not in st.fired:
            st.fired.add(key)
            out.append((urgent, msg))

    if w["stop"] and w["shares"] and px <= w["stop"]:
        fire(f"{c}:stop", f"🔴 停損 {tag}　跌破停損價 {w['stop']:g}", True)
    if w["target"] and px >= w["target"]:
        fire(f"{c}:target", f"🟢 停利 {tag}　達停利價 {w['target']:g}", True)
    if w["buy_below"] and px <= w["buy_below"]:
        fire(f"{c}:buy", f"🔵 到價 {tag}　跌至買進提醒價 {w['buy_below']:g}", True)
    if w["break_above"] and px >= w["break_above"]:
        fire(f"{c}:break", f"🟠 突破 {tag}　突破 {w['break_above']:g}", True)

    lu, ld = info.get("limit_up"), info.get("limit_down")
    if lu and px >= lu:
        fire(f"{c}:limitup", f"🔺 漲停 {tag}", True)
    elif ld and px <= ld:
        fire(f"{c}:limitdown", f"🔻 跌停 {tag}", True)
    else:
        for lv in sorted(s["change_levels_pct"], reverse=True):
            side = "up" if chg >= lv else "dn" if chg <= -lv else None
            if side:
                fire(f"{c}:{side}{lv}", f"{'📈 大漲' if side == 'up' else '📉 大跌'} {tag}　{'漲' if side == 'up' else '跌'}幅達 {lv}%")
                for lower in s["change_levels_pct"]:  # 回落後不再重複提醒較低級距
                    if lower < lv:
                        st.fired.add(f"{c}:{side}{lower}")
                break

    if now.time() >= hm(s["volume_alert_after"]) and q.get("yesterday_volume"):
        open_ = now.replace(hour=9, minute=0, second=0, microsecond=0)
        frac = min(max((now - open_).total_seconds() / (270 * 60), 0.05), 1.0)
        ratio = q["total_volume"] / frac / q["yesterday_volume"]
        if ratio >= s["volume_alert_ratio"]:
            fire(f"{c}:vol", f"🔥 爆量 {tag}　預估全日量為昨量 {ratio:.1f} 倍")
    return out


# ----------------------------------------------------------------------------
# 重大訊息 / 新聞
# ----------------------------------------------------------------------------
def http_get(url: str):
    import requests
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (personal-alert)"}, timeout=20)
    r.raise_for_status()
    return r


def pick(d: dict, *kws: str) -> str:
    for k, v in d.items():
        if all(x in k for x in kws):
            return str(v).strip()
    return ""


def fetch_announcements(s: dict, codes: set[str], st: State, demo: bool) -> list[str]:
    rows = []
    if demo:
        if random.random() < 0.04:
            c = random.choice(sorted(codes))
            rows = [{"公司代號": c, "公司名稱": "示範公司", "發言日期": "1151117", "發言時間": f"{random.randint(90000, 133000)}",
                     "主旨": "公告本公司董事會決議發放現金股利（示範資料）"}]
    else:
        for url in s["announce_urls"]:
            try:
                data = http_get(url).json()
                rows += data if isinstance(data, list) else []
            except Exception as e:  # noqa: BLE001
                log(f"重大訊息抓取失敗 {url}: {e}")
    out = []
    for r in rows:
        code = pick(r, "公司代號") or pick(r, "代號")
        if code not in codes:
            continue
        subj = pick(r, "主旨")
        key = f"{code}|{pick(r, '發言日期')}|{pick(r, '發言時間')}|{subj[:40]}"
        if key in st.seen_ann:
            continue
        st.seen_ann.add(key)
        out.append(f"📢 重大訊息 {code} {pick(r, '公司名稱')}\n　{subj[:120]}")
    return out


def fetch_news(watch: list[dict], st: State, s: dict, since: dt.datetime, demo: bool) -> list[str]:
    lines = []
    for w in watch:
        name = w["name"] or w["code"]
        items = []
        if demo:
            if random.random() < 0.5:
                items = [(f"{name} 示範新聞標題 {random.randint(1, 99)}", f"https://example.com/{w['code']}/{random.randint(1, 9999)}")]
        else:
            q = urllib.parse.quote(f"{name} {w['code']}")
            url = f"https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
            try:
                root = ET.fromstring(http_get(url).content)
                for it in root.iter("item"):
                    title, link = it.findtext("title") or "", it.findtext("link") or ""
                    try:
                        pub = parsedate_to_datetime(it.findtext("pubDate"))
                    except Exception:  # noqa: BLE001
                        continue
                    if pub >= since:
                        items.append((title, link))
            except Exception as e:  # noqa: BLE001
                log(f"新聞抓取失敗 {name}: {e}")
        fresh = [(t, l) for t, l in items if l not in st.seen_news][: s["news_per_stock"]]
        for _, l in fresh:
            st.seen_news.add(l)
        if fresh:
            lines.append(f"【{w['code']} {name}】")
            lines += [f"・{t}\n  {l}" for t, l in fresh]
    return lines


# ----------------------------------------------------------------------------
# 收盤摘要
# ----------------------------------------------------------------------------
def close_summary(watch, quotes: dict, info: dict, day: dt.date) -> str:
    L = [f"📊 收盤摘要 {day:%m/%d}"]
    total_pl, total_cost = 0.0, 0.0
    for w in watch:
        q = quotes.get(w["code"])
        if not q:
            continue
        name = w["name"] or info.get(w["code"], {}).get("name", "")
        line = f"{w['code']} {name} {q['close']:g}（{q['change_rate']:+.2f}%）"
        if q.get("yesterday_volume"):
            line += f" 量比 {q['total_volume'] / q['yesterday_volume']:.1f}"
        if w["shares"] and w["cost"]:
            pl = (q["close"] - w["cost"]) * w["shares"]
            total_pl += pl
            total_cost += w["cost"] * w["shares"]
            line += f"\n　持股 {w['shares']:,.0f} 股 未實現 {pl:+,.0f} 元（{(q['close'] / w['cost'] - 1) * 100:+.1f}%）"
        L.append(line)
    if total_cost:
        L.append(f"\n持股合計未實現損益 {total_pl:+,.0f} 元（{total_pl / total_cost * 100:+.1f}%，未扣手續費與稅）")
    L.append("\n※ " + os.getenv("CLOSE_SUMMARY_FOOTER", "今晚 21:30 後可執行盤後籌碼報告"))
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
class Clock:
    """正式模式用真實時間；示範模式從 08:50 起每輪快轉。"""

    def __init__(self, demo: bool, poll: int):
        self.demo = demo
        self.t = dt.datetime.now(TZ).replace(hour=8, minute=50, second=0, microsecond=0)
        self.step = dt.timedelta(minutes=5) if demo else None
        self.poll = poll

    def now(self) -> dt.datetime:
        return self.t if self.demo else dt.datetime.now(TZ)

    def sleep(self):
        if self.demo:
            self.t += self.step
        else:
            time.sleep(self.poll)


def run(args):
    load_env(BASE / ".env")
    s = load_settings()
    watch = load_watchlist()
    codes = [w["code"] for w in watch]
    clock = Clock(args.demo, s["poll_seconds"])
    today = clock.now().date()
    if today.weekday() >= 5 and not args.demo:
        print("今天是週末，不執行。")
        return
    st = State(f"{today:%Y%m%d}", args.demo)
    sender = Sender(args.dry_run or args.demo, s["max_messages_per_day"], st)
    if st.closed and not args.demo:
        print("今天的收盤摘要已送出，不再執行。")
        return

    feed = DemoQuotes(codes, watch) if args.demo else SinopacQuotes(codes)
    info = feed.info
    print(f"開始盯盤：{len(codes)} 檔，每 {s['poll_seconds']} 秒檢查一次。Ctrl+C 可結束。")
    last_ann = dt.datetime.min.replace(tzinfo=TZ)
    last_digest = clock.now() - dt.timedelta(hours=16)  # 第一次摘要涵蓋前一晚到今早的新聞
    last_q: dict = {}
    pending: list[str] = []
    last_flush = dt.datetime.min.replace(tzinfo=TZ)
    try:
        while True:
            now = clock.now()
            if now.time() < hm(s["start_time"]):
                time.sleep(30)
                continue

            urgent: list[str] = []
            in_session = hm("09:00") <= now.time() <= hm("13:31")
            if in_session:
                try:
                    last_q = feed.snapshot()
                except Exception as e:  # noqa: BLE001
                    log(f"行情讀取失敗：{e}")
                    print(f"行情讀取失敗：{e}")
                for w in watch:
                    if w["code"] in last_q:
                        for is_urgent, m in price_alerts(w, last_q[w["code"]], info.get(w["code"], {}), st, s, now):
                            (urgent if is_urgent else pending).append(m)

            if s["announce_enabled"] and (now - last_ann).total_seconds() >= s["announce_poll_minutes"] * 60:
                pending.extend(fetch_announcements(s, set(codes), st, args.demo))
                last_ann = now

            if s["news_enabled"]:
                for t in s["news_digest_times"]:
                    if now.time() >= hm(t) and t not in st.digests_done:
                        st.digests_done.add(t)
                        news = fetch_news(watch, st, s, last_digest, args.demo)
                        last_digest = now
                        if news:
                            pending.append(f"📰 新聞摘要 {t}\n" + "\n".join(news))
                            last_flush = dt.datetime.min.replace(tzinfo=TZ)  # 新聞摘要到點就送

            closing = now.time() >= hm(s["close_summary_time"])
            if urgent:  # 關鍵警示立即送，順便帶上累積中的一般警示
                sender.send(f"⏰ {now:%H:%M} 關鍵警示\n" + "\n\n".join(urgent + pending), force=True)
                pending.clear()
                last_flush = now
            elif pending and ((now - last_flush).total_seconds() >= s["digest_minutes"] * 60 or closing):
                sender.send(f"⏰ {now:%H:%M}\n" + "\n\n".join(pending))
                pending.clear()
                last_flush = now
            st.save()

            if closing:
                if not last_q:
                    try:
                        last_q = feed.snapshot()
                    except Exception as e:  # noqa: BLE001
                        log(f"收盤行情讀取失敗：{e}")
                if last_q:
                    sender.send(close_summary(watch, last_q, info, today), force=True)
                st.closed = True
                st.save()
                print("收盤摘要已送出，結束。")
                return
            clock.sleep()
    except KeyboardInterrupt:
        print("\n已手動結束。")
    finally:
        feed.close()


def main():
    ap = argparse.ArgumentParser(description="台股盤中警示 → LINE")
    ap.add_argument("--demo", action="store_true", help="假資料模擬一整天，訊息只印在螢幕上")
    ap.add_argument("--dry-run", action="store_true", help="真實行情，但訊息只印在螢幕上")
    ap.add_argument("--test-line", action="store_true", help="送一則測試訊息到 LINE")
    a = ap.parse_args()
    if a.test_line:
        load_env(BASE / ".env")
        st = State("test", False)
        Sender(False, 99, st).send("✅ 台股盤中警示：LINE 連線測試成功", force=True)
        print("已送出測試訊息，請看手機 LINE。")
        return
    run(a)


if __name__ == "__main__":
    main()
