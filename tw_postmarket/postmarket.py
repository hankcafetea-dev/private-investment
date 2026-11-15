#!/usr/bin/env python3
"""
台股盤後籌碼分析（上市股票）
================================
每天盤後（建議 21:30 後，融資融券與當沖資料都已公布）執行一次：

    pip install requests pandas
    python postmarket.py                 # 分析今天
    python postmarket.py --date 20261113 # 分析指定日期
    python postmarket.py --watch 2330,2317,2454   # 另外列出自選股
    python postmarket.py --demo          # 不連網，用假資料測試流程

資料來源（全部是證交所 / 集保官方免費公開資料）：
  - 個股收盤行情        證交所 OpenAPI  STOCK_DAY_ALL
  - 三大法人買賣超      證交所 T86
  - 融資融券餘額        證交所 MI_MARGN
  - 當日沖銷交易        證交所 TWTB4U
  - 集保股權分散表(週)  集保結算所開放資料 1-5（每週五更新）

產出（在 ./output/ 下）：
  report_YYYYMMDD.md    給人看的盤後報告
  stocks_YYYYMMDD.csv   每檔股票的完整指標，可以用 Excel 開
集保分散表會存到 ./data/ 下，下週再跑時自動算「大戶/散戶」週變化。

注意：本程式只做資料整理與訊號標記，不構成任何投資建議。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
from pathlib import Path

import pandas as pd

import os

BASE = Path(__file__).resolve().parent
# GitHub Actions 版：用環境變數把輸出／歷史資料指到資料分支（見 .github/workflows/auto.yml）
OUT = Path(os.getenv("PM_OUT_DIR") or BASE / "output")
DATA = Path(os.getenv("PM_DATA_DIR") or BASE / "data")

UA = {"User-Agent": "Mozilla/5.0 (postmarket-report)", "Accept": "application/json"}
REQUEST_GAP = 3.0  # 證交所對頻繁請求會暫時封鎖 IP，每次請求間隔 3 秒

URL_QUOTES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
URL_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86?date={d}&selectType=ALLBUT0999&response=json"
URL_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=ALL&response=json"
URL_DAYTRADE = "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U?date={d}&response=json"
URL_TDCC = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def num(x) -> float:
    """把 '1,234' / '-5,678' / '--' / '' 轉成數字。"""
    if x is None:
        return float("nan")
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace(",", "").replace("+", "").strip()
    if s in ("", "--", "---", "X", "N/A"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


_last_call = 0.0


def fetch(url: str, as_text: bool = False):
    import requests

    global _last_call
    wait = REQUEST_GAP - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            _last_call = time.time()
            r.raise_for_status()
            if as_text:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            return r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                raise RuntimeError(f"抓取失敗：{url}\n  {e}") from e
            time.sleep(5 * (attempt + 1))


def find_col(fields: list[str], *keywords: str, start: int = 0) -> int:
    """在欄位名稱中找第一個同時包含所有關鍵字的欄位（從 start 開始）。"""
    for i in range(start, len(fields)):
        if all(k in fields[i] for k in keywords):
            return i
    raise KeyError(f"找不到欄位 {keywords}，證交所格式可能改了。欄位：{fields}")


def pick_table(js: dict, must_have: str) -> tuple[list[str], list[list]]:
    """證交所 rwd 回傳可能是 fields/data，也可能是 tables[]，挑出含有指定欄位的那張表。"""
    if js.get("stat") not in (None, "OK", "ok"):
        raise LookupError(js.get("stat"))
    cands = []
    if "fields" in js and "data" in js:
        cands.append((js["fields"], js["data"]))
    for t in js.get("tables", []) or []:
        if t.get("fields") and t.get("data"):
            cands.append((t["fields"], t["data"]))
    for f, d in cands:
        if any(must_have in x for x in f):
            return f, d
    raise LookupError(f"回傳資料中沒有含「{must_have}」的表（可能是休市日或資料尚未公布）")


# ----------------------------------------------------------------------------
# 各資料源解析
# ----------------------------------------------------------------------------
def parse_quotes(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    out = pd.DataFrame({
        "代號": df["Code"].astype(str).str.strip(),
        "名稱": df["Name"].astype(str).str.strip(),
        "成交股數": df["TradeVolume"].map(num),
        "成交金額": df["TradeValue"].map(num),
        "收盤": df["ClosingPrice"].map(num),
        "漲跌": df["Change"].map(num),
    })
    out["漲跌幅%"] = out["漲跌"] / (out["收盤"] - out["漲跌"]) * 100
    # STOCK_DAY_ALL 只給「最近一個交易日」，日期是民國年（1151113）；記下來給呼叫端比對
    if "Date" in df.columns and len(df):
        roc = str(df["Date"].iloc[0]).strip()
        if roc.isdigit() and len(roc) >= 7:
            out.attrs["資料日期"] = f"{int(roc[:-4]) + 1911}{roc[-4:]}"
    return out


def parse_t86(js: dict) -> pd.DataFrame:
    f, d = pick_table(js, "三大法人")
    i_code = find_col(f, "證券代號")
    i_for = find_col(f, "外", "買賣超")       # 外陸資買賣超股數(不含外資自營商)
    i_it = find_col(f, "投信", "買賣超")
    i_dl = next(i for i, x in enumerate(f) if x.startswith("自營商買賣超"))  # 避開「外資自營商…」
    i_all = find_col(f, "三大法人買賣超")
    return pd.DataFrame({
        "代號": [str(r[i_code]).strip() for r in d],
        "外資買賣超": [num(r[i_for]) for r in d],
        "投信買賣超": [num(r[i_it]) for r in d],
        "自營商買賣超": [num(r[i_dl]) for r in d],
        "三大法人買賣超": [num(r[i_all]) for r in d],
    })


def parse_margin(js: dict) -> tuple[pd.DataFrame, dict]:
    """個股融資融券。欄位名稱融資、融券重複（買進/賣出/今日餘額…），依位置分前後兩段。"""
    f, d = pick_table(js, "代號")
    i_code = find_col(f, "代號")
    i_m_prev = find_col(f, "前日餘額")
    i_m_today = find_col(f, "今日餘額")
    i_m_limit = find_col(f, "限額")
    i_s_prev = find_col(f, "前日餘額", start=i_m_limit + 1)
    i_s_today = find_col(f, "今日餘額", start=i_m_limit + 1)
    df = pd.DataFrame({
        "代號": [str(r[i_code]).strip() for r in d],
        "融資前日": [num(r[i_m_prev]) for r in d],
        "融資餘額": [num(r[i_m_today]) for r in d],
        "融資剩餘限額": [num(r[i_m_limit]) for r in d],
        "融券前日": [num(r[i_s_prev]) for r in d],
        "融券餘額": [num(r[i_s_today]) for r in d],
    })
    # 大盤融資：summary 表中的「融資金額(仟元)」
    market = {}
    for t in js.get("tables", []) or []:
        for row in t.get("data", []) or []:
            if row and "融資金額" in str(row[0]):
                vals = [num(x) for x in row[1:]]
                # 欄位：買進、賣出、現金(券)償還、前日餘額、今日餘額
                if len(vals) >= 5:
                    market = {"融資金額前日_仟元": vals[3], "融資金額今日_仟元": vals[4]}
    return df, market


def parse_daytrade(js: dict) -> pd.DataFrame:
    f, d = pick_table(js, "當日沖銷交易成交股數")
    i_code = find_col(f, "證券代號")
    i_vol = find_col(f, "當日沖銷交易成交股數")
    return pd.DataFrame({
        "代號": [str(r[i_code]).strip() for r in d],
        "當沖股數": [num(r[i_vol]) for r in d],
    })


TDCC_RETAIL = {1, 2, 3}          # 1–999 股、1–5 張、5–10 張  → 小散戶（≤10 張）
TDCC_BIG = {12, 13, 14, 15}      # 400 張以上 → 大戶
TDCC_WHALE = {15}                # 1000 張以上 → 千張大戶


def parse_tdcc(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), dtype=str)
    raw.columns = [c.strip() for c in raw.columns]
    c_date = [c for c in raw.columns if "日期" in c][0]
    c_code = [c for c in raw.columns if "代號" in c][0]
    c_lvl = [c for c in raw.columns if "分級" in c][0]
    c_ppl = [c for c in raw.columns if "人數" in c][0]
    c_pct = [c for c in raw.columns if "比例" in c][0]
    raw["lvl"] = raw[c_lvl].map(num)
    raw["pct"] = raw[c_pct].map(num)
    raw["ppl"] = raw[c_ppl].map(num)
    raw["code"] = raw[c_code].str.strip()
    g = raw.groupby("code")
    out = pd.DataFrame({
        "散戶持股%": g.apply(lambda x: x.loc[x["lvl"].isin(TDCC_RETAIL), "pct"].sum()),
        "散戶人數": g.apply(lambda x: x.loc[x["lvl"].isin(TDCC_RETAIL), "ppl"].sum()),
        "400張大戶%": g.apply(lambda x: x.loc[x["lvl"].isin(TDCC_BIG), "pct"].sum()),
        "千張大戶%": g.apply(lambda x: x.loc[x["lvl"].isin(TDCC_WHALE), "pct"].sum()),
    }).reset_index().rename(columns={"code": "代號"})
    out.attrs["資料日期"] = str(raw[c_date].iloc[0]).strip()
    return out


# ----------------------------------------------------------------------------
# 取得資料（真實或示範）
# ----------------------------------------------------------------------------
def load_live(day: str):
    print(f"[1/5] 個股行情 …"); q = parse_quotes(fetch(URL_QUOTES))
    qd = q.attrs.get("資料日期")
    if qd and qd != day:
        # 行情只有最新一天；日期對不上時其他資料會跟行情錯開，寧可不出報告
        raise LookupError(f"證交所行情資料日期是 {qd}，與要分析的 {day} 不同（休市或尚未更新）")
    print(f"[2/5] 三大法人 …"); t = parse_t86(fetch(URL_T86.format(d=day)))
    print(f"[3/5] 融資融券 …"); m, mk = parse_margin(fetch(URL_MARGIN.format(d=day)))
    print(f"[4/5] 當日沖銷 …")
    try:
        dtr = parse_daytrade(fetch(URL_DAYTRADE.format(d=day)))
    except Exception as e:  # noqa: BLE001
        print(f"   當沖資料略過：{e}"); dtr = pd.DataFrame(columns=["代號", "當沖股數"])
    print(f"[5/5] 集保股權分散表 …")
    try:
        txt = fetch(URL_TDCC, as_text=True)
        tdcc = parse_tdcc(txt)
        save_tdcc_summary(tdcc)
    except Exception as e:  # noqa: BLE001
        print(f"   集保資料略過：{e}"); tdcc = None
    return q, t, m, mk, dtr, tdcc


def load_demo(day: str):
    """產生結構與證交所相同的假資料，用來測試整個流程。"""
    import random

    random.seed(int(day))
    names = [("2330", "台積電", 1050), ("2317", "鴻海", 210), ("2454", "聯發科", 1350),
             ("2603", "長榮", 190), ("3481", "群創", 18), ("2409", "友達", 15),
             ("2303", "聯電", 48), ("2881", "富邦金", 88), ("1301", "台塑", 45),
             ("3231", "緯創", 110), ("2382", "廣達", 290), ("6669", "緯穎", 2600)]
    quotes, t86, mg, dtr_rows = [], [], [], []
    for code, name, px in names:
        vol = random.randint(5_000, 80_000) * 1000
        chg = round(px * random.uniform(-0.05, 0.05), 2)
        quotes.append({"Code": code, "Name": name, "TradeVolume": f"{vol:,}",
                       "TradeValue": f"{int(vol * px):,}", "ClosingPrice": f"{px:.2f}",
                       "Change": f"{chg:+.2f}"})
        f_, i_, d_ = (random.randint(-8000, 8000) * 1000 for _ in range(3))
        t86.append([code, name, "0", "0", f"{f_:,}", "0", "0", f"{i_:,}", "0", "0", "0",
                    f"{d_:,}", f"{f_ + i_ + d_:,}"])
        prev = random.randint(2_000, 60_000)
        today = int(prev * random.uniform(0.9, 1.15))
        sprev = random.randint(0, 5_000); stoday = int(sprev * random.uniform(0.8, 1.3))
        mg.append([code, name, "0", "0", "0", f"{prev:,}", f"{today:,}",
                   f"{random.randint(5_000, 200_000):,}", "0", "0", "0", f"{sprev:,}",
                   f"{stoday:,}", "0", "0", ""])
        dtr_rows.append([code, name, "", f"{int(vol * random.uniform(0.05, 0.45)):,}", "0", "0"])
    t86_js = {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)",
              "外陸資賣出股數(不含外資自營商)", "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數",
              "外資自營商賣出股數", "投信買賣超股數", "x", "y", "z", "自營商買賣超股數", "三大法人買賣超股數"],
              "data": t86}
    mg_js = {"stat": "OK", "tables": [
        {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"],
         "data": [["融資金額(仟元)", "1", "1", "1", "330,000,000", "333,500,000"]]},
        {"fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額",
                    "買進", "賣出", "現券償還", "前日餘額", "今日餘額", "次一營業日限額", "資券互抵", "註記"],
         "data": mg}]}
    dt_js = {"stat": "OK", "tables": [{"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記",
             "當日沖銷交易成交股數", "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"], "data": dtr_rows}]}
    lines = ["資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%"]
    for code, _, _ in names:
        w = [random.uniform(1, 10) for _ in range(15)]
        s = sum(w)
        for lvl in range(1, 16):
            lines.append(f"{day},{code},{lvl},{random.randint(10, 90000)},0,{w[lvl - 1] / s * 100:.2f}")
    tdcc = parse_tdcc("\n".join(lines))
    save_tdcc_summary(tdcc)
    m, mk = parse_margin(mg_js)
    return parse_quotes(quotes), parse_t86(t86_js), m, mk, parse_daytrade(dt_js), tdcc


# ----------------------------------------------------------------------------
# 指標與訊號
# ----------------------------------------------------------------------------
def build(q, t, m, dtr, tdcc) -> pd.DataFrame:
    df = q.merge(t, on="代號", how="left").merge(m, on="代號", how="left").merge(dtr, on="代號", how="left")
    df = df[df["代號"].str.fullmatch(r"\d{4}")]  # 只留一般股票（排除 ETF、權證等）
    lot = 1000
    df["外資買賣超(張)"] = df["外資買賣超"] / lot
    df["投信買賣超(張)"] = df["投信買賣超"] / lot
    df["三大法人(張)"] = df["三大法人買賣超"] / lot
    df["融資增減(張)"] = df["融資餘額"] - df["融資前日"]
    df["融資增減%"] = df["融資增減(張)"] / df["融資前日"].where(df["融資前日"] > 0) * 100
    df["融資使用率%(估)"] = df["融資餘額"] / (df["融資餘額"] + df["融資剩餘限額"]) * 100
    df["券資比%"] = df["融券餘額"] / df["融資餘額"].where(df["融資餘額"] > 0) * 100
    df["當沖比%"] = df["當沖股數"] / df["成交股數"].where(df["成交股數"] > 0) * 100

    if tdcc is not None:
        df = df.merge(tdcc, on="代號", how="left")
        prev = previous_tdcc(tdcc.attrs.get("資料日期"))
        if prev is not None:
            p = prev.rename(columns={"散戶持股%": "p散", "400張大戶%": "p大", "千張大戶%": "p千", "散戶人數": "p人"})
            df = df.merge(p[["代號", "p散", "p大", "p千", "p人"]], on="代號", how="left")
            df["散戶持股週變化"] = df["散戶持股%"] - df["p散"]
            df["400張大戶週變化"] = df["400張大戶%"] - df["p大"]
            df["千張大戶週變化"] = df["千張大戶%"] - df["p千"]
            df = df.drop(columns=["p散", "p大", "p千", "p人"])

    df["訊號"] = df.apply(signals, axis=1)
    return df


def signals(r) -> str:
    s = []
    big_money = (r.get("成交金額", 0) or 0) >= 3e8  # 成交值 3 億以上才標記，避免冷門股雜訊
    inst = r.get("三大法人(張)")
    mchg = r.get("融資增減(張)")
    if big_money and pd.notna(inst) and pd.notna(mchg):
        if inst < 0 and mchg > 0:
            s.append("⚠法人賣、融資買（散戶接刀）")
        elif inst > 0 and mchg < 0:
            s.append("✓法人買、融資退（籌碼轉穩）")
    if pd.notna(r.get("投信買賣超(張)")) and r["投信買賣超(張)"] > 0 and big_money:
        s.append("投信買超")
    if pd.notna(r.get("融資使用率%(估)")) and r["融資使用率%(估)"] >= 60:
        s.append("⚠融資使用率偏高")
    if pd.notna(r.get("當沖比%")) and r["當沖比%"] >= 40:
        s.append("⚠當沖比過高（短線投機）")
    if pd.notna(r.get("券資比%")) and r["券資比%"] >= 30:
        s.append("券資比≥30%（留意軋空/偏空）")
    if pd.notna(r.get("散戶持股週變化")) and pd.notna(r.get("400張大戶週變化")):
        if r["散戶持股週變化"] < 0 and r["400張大戶週變化"] > 0:
            s.append("✓集保：籌碼往大戶集中")
        elif r["散戶持股週變化"] > 0 and r["400張大戶週變化"] < 0:
            s.append("⚠集保：籌碼往散戶分散")
    return "；".join(s)


def save_tdcc_summary(tdcc: pd.DataFrame) -> None:
    """集保原始檔有好幾 MB；只存每檔的散戶／大戶比例摘要（幾十 KB），下週比對用。"""
    DATA.mkdir(parents=True, exist_ok=True)
    tdcc.to_csv(DATA / f"tdcc_summary_{tdcc.attrs['資料日期']}.csv", index=False, encoding="utf-8")


def previous_tdcc(cur_date: str | None):
    if not DATA.exists() or not cur_date:
        return None
    files = sorted(p for p in DATA.glob("tdcc_summary_*.csv") if p.stem.rsplit("_", 1)[1] < cur_date)
    if files:
        return pd.read_csv(files[-1], dtype={"代號": str})
    # 相容本機舊版存下的集保原始檔
    raw = sorted(p for p in DATA.glob("tdcc_[0-9]*.csv") if p.stem.split("_")[1] < cur_date)
    return parse_tdcc(raw[-1].read_text(encoding="utf-8")) if raw else None


# ----------------------------------------------------------------------------
# 報告
# ----------------------------------------------------------------------------
def fmt_table(df: pd.DataFrame, cols: list[str], n: int = 10) -> str:
    sub = df[cols].head(n).copy()
    for c in sub.columns:
        if sub[c].dtype.kind == "f":
            sub[c] = sub[c].map(lambda v: "" if pd.isna(v) else f"{v:,.1f}")
    head = "| " + " | ".join(cols) + " |\n| " + " | ".join("---" for _ in cols) + " |\n"
    body = "".join("| " + " | ".join(str(v) for v in row) + " |\n" for row in sub.values)
    return head + body


def report(df: pd.DataFrame, market: dict, day: str, watch: list[str], tdcc_date: str | None) -> str:
    L = [f"# 台股盤後籌碼報告 {day[:4]}-{day[4:6]}-{day[6:]}", ""]
    L.append("> 自動產生，僅供研究參考，不構成投資建議。資料來源：臺灣證券交易所、臺灣集中保管結算所。\n")

    L.append("## 一、大盤散戶溫度")
    if market:
        a, b = market["融資金額前日_仟元"], market["融資金額今日_仟元"]
        L.append(f"- 上市融資餘額：{b / 1e5:,.1f} 億元（前日 {a / 1e5:,.1f} 億，變化 {(b - a) / 1e5:+,.1f} 億）")
    inst_total = df["三大法人(張)"].sum()
    marg_total = df["融資增減(張)"].sum()
    L.append(f"- 個股合計：三大法人 {inst_total:+,.0f} 張，融資 {marg_total:+,.0f} 張")
    if inst_total < 0 < marg_total:
        L.append("- 判讀：**法人賣、散戶融資買**，籌碼由法人流向散戶，短線風險升高。")
    elif marg_total < 0 < inst_total:
        L.append("- 判讀：**法人買、散戶融資退**，籌碼趨於穩定。")
    else:
        L.append("- 判讀：法人與散戶同方向，觀察明日量價確認。")
    dtr = (df["當沖股數"].sum() / df["成交股數"].sum() * 100) if df["當沖股數"].notna().any() else float("nan")
    if pd.notna(dtr):
        L.append(f"- 全市場當沖比（個股加總）：{dtr:.1f}%")
    L.append("")

    liquid = df[df["成交金額"] >= 3e8]
    L.append("## 二、法人動向")
    L.append("### 外資買超前 10（張）")
    L.append(fmt_table(liquid[liquid["外資買賣超(張)"] > 0].sort_values("外資買賣超(張)", ascending=False),
                       ["代號", "名稱", "收盤", "漲跌幅%", "外資買賣超(張)", "投信買賣超(張)", "融資增減(張)"]))
    L.append("### 外資賣超前 10（張）")
    L.append(fmt_table(liquid[liquid["外資買賣超(張)"] < 0].sort_values("外資買賣超(張)"),
                       ["代號", "名稱", "收盤", "漲跌幅%", "外資買賣超(張)", "投信買賣超(張)", "融資增減(張)"]))
    L.append("### 投信買超前 10（張）")
    L.append(fmt_table(liquid[liquid["投信買賣超(張)"] > 0].sort_values("投信買賣超(張)", ascending=False),
                       ["代號", "名稱", "收盤", "漲跌幅%", "投信買賣超(張)", "外資買賣超(張)"]))

    L.append("## 三、散戶動作")
    knife = liquid[(liquid["三大法人(張)"] < 0) & (liquid["融資增減(張)"] > 0)]
    knife = knife.assign(背離=knife["融資增減(張)"] - knife["三大法人(張)"]).sort_values("背離", ascending=False)
    L.append("### ⚠ 法人賣、融資買（散戶接手）前 10")
    L.append(fmt_table(knife, ["代號", "名稱", "收盤", "漲跌幅%", "三大法人(張)", "融資增減(張)", "融資使用率%(估)"]))
    good = liquid[(liquid["三大法人(張)"] > 0) & (liquid["融資增減(張)"] < 0)]
    good = good.assign(背離=good["三大法人(張)"] - good["融資增減(張)"]).sort_values("背離", ascending=False)
    L.append("### ✓ 法人買、融資退（籌碼轉穩）前 10")
    L.append(fmt_table(good, ["代號", "名稱", "收盤", "漲跌幅%", "三大法人(張)", "融資增減(張)"]))
    L.append("### 當沖比最高（投機熱度）前 10")
    L.append(fmt_table(liquid.sort_values("當沖比%", ascending=False),
                       ["代號", "名稱", "收盤", "漲跌幅%", "當沖比%", "成交金額"]))
    L.append("### 券資比最高前 10")
    L.append(fmt_table(liquid.sort_values("券資比%", ascending=False),
                       ["代號", "名稱", "收盤", "券資比%", "融資餘額", "融券餘額"]))

    if "400張大戶週變化" in df.columns:
        L.append(f"## 四、集保週籌碼（資料日 {tdcc_date}）")
        L.append("### 籌碼集中：大戶增、散戶減 前 10")
        conc = liquid.assign(集中度=liquid["400張大戶週變化"] - liquid["散戶持股週變化"]).sort_values("集中度", ascending=False)
        L.append(fmt_table(conc, ["代號", "名稱", "收盤", "400張大戶%", "400張大戶週變化", "散戶持股%", "散戶持股週變化"]))
        L.append("### 籌碼分散：散戶增、大戶減 前 10")
        L.append(fmt_table(conc.iloc[::-1], ["代號", "名稱", "收盤", "400張大戶%", "400張大戶週變化", "散戶持股%", "散戶持股週變化"]))
    elif tdcc_date:
        L.append(f"## 四、集保週籌碼（資料日 {tdcc_date}）")
        L.append("已存下本週分散表；下週再執行就會出現大戶／散戶週變化排行。\n")

    if watch:
        L.append("## 五、自選股")
        w = df[df["代號"].isin(watch)]
        cols = ["代號", "名稱", "收盤", "漲跌幅%", "外資買賣超(張)", "投信買賣超(張)", "融資增減(張)",
                "融資使用率%(估)", "券資比%", "當沖比%"]
        cols += [c for c in ["散戶持股%", "400張大戶%", "千張大戶%"] if c in w.columns]
        L.append(fmt_table(w, cols, n=len(w)))
        for _, r in w.iterrows():
            if r["訊號"]:
                L.append(f"- **{r['代號']} {r['名稱']}**：{r['訊號']}")
        L.append("")

    L.append("## 指標說明")
    L.append("- 融資增減：融資餘額較前日變化，融資族群以散戶為主，常用來代表散戶動向。")
    L.append("- 融資使用率(估)：融資餘額 ÷（融資餘額＋剩餘可融資額度），60% 以上代表散戶槓桿擁擠。")
    L.append("- 券資比：融券餘額 ÷ 融資餘額，市場慣例以 30% 為分水嶺；高券資比不代表必漲。")
    L.append("- 當沖比：當沖成交股數 ÷ 總成交股數，過高代表短線投機資金為主。")
    L.append("- 集保散戶 = 持股 10 張以下；大戶 = 400 張以上；千張大戶 = 1000 張以上。看連續 4 週趨勢比看單週可靠。")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 網頁用 JSON（GitHub Pages 讀這份；排行與 report() 同一套條件）
# ----------------------------------------------------------------------------
STOCK_COLS = ["代號", "名稱", "收盤", "漲跌幅%", "成交金額", "外資買賣超(張)", "投信買賣超(張)", "三大法人(張)",
              "融資增減(張)", "融資使用率%(估)", "券資比%", "當沖比%", "散戶持股%", "400張大戶%", "千張大戶%",
              "散戶持股週變化", "400張大戶週變化", "訊號"]


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float):
        return None if pd.isna(v) else round(v, 2)
    if hasattr(v, "item"):  # numpy 數字
        v = v.item()
        return None if isinstance(v, float) and pd.isna(v) else (round(v, 2) if isinstance(v, float) else v)
    return v


def _rows(df: pd.DataFrame, cols: list[str], n: int | None = 10) -> list[list]:
    cols = [c for c in cols if c in df.columns]
    sub = df[cols] if n is None else df[cols].head(n)
    return [[_clean(v) for v in row] for row in sub.itertuples(index=False, name=None)]


def export_json(df: pd.DataFrame, market: dict, day: str, tdcc_date: str | None) -> dict:
    liquid = df[df["成交金額"] >= 3e8]
    inst_total = float(df["三大法人(張)"].sum())
    marg_total = float(df["融資增減(張)"].sum())
    if inst_total < 0 < marg_total:
        verdict = ("warn", "法人賣、散戶融資買，籌碼由法人流向散戶，短線風險升高。")
    elif marg_total < 0 < inst_total:
        verdict = ("good", "法人買、散戶融資退，籌碼趨於穩定。")
    else:
        verdict = ("neutral", "法人與散戶同方向，觀察明日量價確認。")
    dtr = (df["當沖股數"].sum() / df["成交股數"].sum() * 100) if df["當沖股數"].notna().any() else None

    base = ["代號", "名稱", "收盤", "漲跌幅%"]
    sec = []

    def add(group, sid, title, frame, cols, note=""):
        cols = [c for c in cols if c in frame.columns]
        sec.append({"group": group, "id": sid, "title": title, "note": note, "cols": cols, "rows": _rows(frame, cols)})

    flow = base + ["外資買賣超(張)", "投信買賣超(張)", "融資增減(張)"]
    add("法人動向", "foreign_buy", "外資買超前 10（張）",
        liquid[liquid["外資買賣超(張)"] > 0].sort_values("外資買賣超(張)", ascending=False), flow)
    add("法人動向", "foreign_sell", "外資賣超前 10（張）",
        liquid[liquid["外資買賣超(張)"] < 0].sort_values("外資買賣超(張)"), flow)
    add("法人動向", "trust_buy", "投信買超前 10（張）",
        liquid[liquid["投信買賣超(張)"] > 0].sort_values("投信買賣超(張)", ascending=False),
        base + ["投信買賣超(張)", "外資買賣超(張)"])
    knife = liquid[(liquid["三大法人(張)"] < 0) & (liquid["融資增減(張)"] > 0)]
    knife = knife.assign(背離=knife["融資增減(張)"] - knife["三大法人(張)"]).sort_values("背離", ascending=False)
    add("散戶動作", "knife", "法人賣、融資買（散戶接手）前 10", knife,
        base + ["三大法人(張)", "融資增減(張)", "融資使用率%(估)"], "warn")
    good = liquid[(liquid["三大法人(張)"] > 0) & (liquid["融資增減(張)"] < 0)]
    good = good.assign(背離=good["三大法人(張)"] - good["融資增減(張)"]).sort_values("背離", ascending=False)
    add("散戶動作", "steady", "法人買、融資退（籌碼轉穩）前 10", good, base + ["三大法人(張)", "融資增減(張)"], "good")
    add("散戶動作", "daytrade", "當沖比最高（投機熱度）前 10", liquid.sort_values("當沖比%", ascending=False),
        base + ["當沖比%", "成交金額"])
    add("散戶動作", "short_ratio", "券資比最高前 10", liquid.sort_values("券資比%", ascending=False),
        ["代號", "名稱", "收盤", "券資比%", "融資餘額", "融券餘額"])
    if "400張大戶週變化" in df.columns:
        conc = liquid.assign(集中度=liquid["400張大戶週變化"] - liquid["散戶持股週變化"]).sort_values("集中度", ascending=False)
        cols = ["代號", "名稱", "收盤", "400張大戶%", "400張大戶週變化", "散戶持股%", "散戶持股週變化"]
        add("集保週籌碼", "concentrate", "籌碼集中：大戶增、散戶減 前 10", conc, cols, "good")
        add("集保週籌碼", "disperse", "籌碼分散：散戶增、大戶減 前 10", conc.iloc[::-1], cols, "warn")

    stock_cols = [c for c in STOCK_COLS if c in df.columns]
    return {
        "date": day,
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
        "tdcc_date": tdcc_date,
        "tdcc_has_weekly_change": "400張大戶週變化" in df.columns,
        "market": {
            "margin_prev_k": _clean(market.get("融資金額前日_仟元")),
            "margin_today_k": _clean(market.get("融資金額今日_仟元")),
            "inst_total_lots": round(inst_total),
            "margin_total_lots": round(marg_total),
            "daytrade_pct": None if dtr is None or pd.isna(dtr) else round(float(dtr), 2),
            "verdict": verdict[0], "verdict_text": verdict[1],
            "stock_count": int(len(df)), "liquid_count": int(len(liquid)),
        },
        "sections": sec,
        "stocks": {"cols": stock_cols, "rows": _rows(df.sort_values("成交金額", ascending=False), stock_cols, None)},
    }


def main():
    ap = argparse.ArgumentParser(description="台股盤後籌碼分析")
    taipei_today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")  # Actions 主機是 UTC
    ap.add_argument("--date", default=taipei_today, help="YYYYMMDD，預設今天（台北時間）")
    ap.add_argument("--watch", default="", help="自選股代號，以逗號分隔，例如 2330,2317")
    ap.add_argument("--demo", action="store_true", help="使用假資料測試，不連網")
    ap.add_argument("--ci", action="store_true",
                    help="GitHub Actions 用：休市／資料未公布時正常結束（exit 0），另輸出網頁用 JSON、不輸出 CSV")
    a = ap.parse_args()
    day = a.date
    if a.demo and not os.getenv("PM_OUT_DIR"):  # 示範資料另外存，不汙染真實集保歷史
        global DATA, OUT
        DATA, OUT = BASE / "demo_data", BASE / "demo_output"
    watch = [x.strip() for x in a.watch.split(",") if x.strip()]

    try:
        q, t, m, mk, dtr, tdcc = (load_demo if a.demo else load_live)(day)
    except LookupError as e:
        print(f"{day} 沒有資料：{e}\n（休市日，或盤後資料尚未公布；融資融券約 21:30 後公布）")
        if a.ci:
            print(f"::notice::{day} 沒有盤後資料，這次不更新報告")
            sys.exit(0)
        sys.exit(1)

    df = build(q, t, m, dtr, tdcc)
    tdcc_date = tdcc.attrs.get("資料日期") if tdcc is not None else None
    OUT.mkdir(parents=True, exist_ok=True)
    md = report(df, mk, day, watch, tdcc_date)
    (OUT / f"report_{day}.md").write_text(md, encoding="utf-8")
    if a.ci:
        js = export_json(df, mk, day, tdcc_date)
        (OUT / f"{day}.json").write_text(json.dumps(js, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"\n完成：{OUT / f'{day}.json'}（{len(js['stocks']['rows'])} 檔）")
        return
    df.to_csv(OUT / f"stocks_{day}.csv", index=False, encoding="utf-8-sig")
    print(f"\n完成：{OUT / f'report_{day}.md'}\n      {OUT / f'stocks_{day}.csv'}")


if __name__ == "__main__":
    main()
