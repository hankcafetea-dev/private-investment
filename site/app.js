/* 台股籌碼盯盤：純前端，資料都是 GitHub Actions 產生的靜態 JSON */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (d = 0) => new Intl.NumberFormat("zh-TW", { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtN = (v, d = 0) => (v == null || Number.isNaN(v) ? "—" : nf(d).format(v));
const signed = (v, d = 0) => (v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + nf(d).format(Math.abs(v)));
const dir = (v) => (v == null || v === 0 ? "flat" : v > 0 ? "up" : "down");
const ymd = (s) => (s ? `${s.slice(0, 4)}/${s.slice(4, 6)}/${s.slice(6, 8)}` : "");
const md = (s) => (s ? `${+s.slice(4, 6)}/${+s.slice(6, 8)}` : "");
const WEEK = "日一二三四五六";

async function getJSON(path) {
  const r = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}
function taipeiNow() {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Taipei" }));
}
function ago(iso) {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "剛剛";
  if (m < 60) return `${m} 分鐘前`;
  if (m < 60 * 24) return `${Math.round(m / 60)} 小時前`;
  return `${Math.round(m / 1440)} 天前`;
}
function hhmm(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

/* ---------------- 欄位顯示 ---------------- */
const LABEL = {
  "外資買賣超(張)": "外資(張)", "投信買賣超(張)": "投信(張)", "三大法人(張)": "三大法人(張)",
  "融資使用率%(估)": "融資使用率%", "成交金額": "成交值", "400張大戶週變化": "大戶週變化",
  "散戶持股週變化": "散戶週變化", "400張大戶%": "400張大戶%", "散戶持股%": "散戶持股%",
};
const label = (c) => LABEL[c] || c;
function cell(col, v) {
  if (col === "代號") return `<td class="l code">${esc(v)}</td>`;
  if (col === "名稱") return `<td class="l">${esc(v)}</td>`;
  if (col === "訊號") return `<td class="sig">${sigTags(v)}</td>`;
  if (v == null) return `<td class="flat">—</td>`;
  if (col === "成交金額") return `<td>${fmtN(v / 1e8, 1)} 億</td>`;
  if (col === "漲跌幅%") return `<td class="${dir(v)}">${signed(v, 2)}%</td>`;
  if (col.endsWith("週變化")) return `<td class="${dir(v)}">${signed(v, 2)}</td>`;
  if (col.includes("(張)")) return `<td class="${dir(v)}">${signed(v, 0)}</td>`;
  if (col.includes("%")) return `<td>${fmtN(v, 1)}</td>`;
  if (col === "收盤") return `<td>${fmtN(v, v < 100 ? 2 : 1)}</td>`;
  return `<td>${fmtN(v, 0)}</td>`;
}
const SIGNALS = [
  { k: "散戶接刀", t: "法人賣、融資買", c: "warn" },
  { k: "籌碼轉穩", t: "法人買、融資退", c: "good" },
  { k: "投信買超", t: "投信買超", c: "info" },
  { k: "融資使用率偏高", t: "融資使用率偏高", c: "warn" },
  { k: "當沖比過高", t: "當沖比過高", c: "warn" },
  { k: "券資比", t: "券資比≥30%", c: "info" },
  { k: "往大戶集中", t: "集保：大戶集中", c: "good" },
  { k: "往散戶分散", t: "集保：散戶分散", c: "warn" },
];
function sigTags(s) {
  if (!s) return "";
  return String(s).split("；").map((x) => {
    const m = SIGNALS.find((g) => x.includes(g.k));
    return `<span class="tag ${m ? m.c : ""}">${esc(m ? m.t : x.replace(/^[⚠✓]/, ""))}</span>`;
  }).join(" ");
}
function table(el, cols, rows, opts = {}) {
  const head = cols.map((c, i) => {
    const l = c === "代號" || c === "名稱" || c === "訊號" ? "l" : "";
    const sort = opts.sortable ? ` sortable${opts.sortCol === i ? " sorted" + (opts.asc ? " asc" : "") : ""}` : "";
    return `<th class="${l}${sort}" data-i="${i}">${esc(label(c))}</th>`;
  }).join("");
  const body = rows.length
    ? rows.map((r) => `<tr>${r.map((v, i) => cell(cols[i], v)).join("")}</tr>`).join("")
    : `<tr><td class="none" colspan="${cols.length}">今天沒有符合條件的個股</td></tr>`;
  el.innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body}</tbody>`;
}

/* ---------------- 頁籤 ---------------- */
function showTab(which) {
  const me = which === "me";
  $("#tab-pm").setAttribute("aria-selected", String(!me));
  $("#tab-me").setAttribute("aria-selected", String(me));
  $("#view-pm").hidden = me;
  $("#view-me").hidden = !me;
  history.replaceState(null, "", me ? "#me" : "#");
  if (me && !PRIV.data) tryRemembered();
}
$("#tab-pm").onclick = () => showTab("pm");
$("#tab-me").onclick = () => showTab("me");
window.addEventListener("hashchange", () => showTab(location.hash === "#me" ? "me" : "pm"));

/* ---------------- 狀態列 ---------------- */
const STATE = { status: null, dates: [], report: null };
function renderStatus() {
  const s = STATE.status;
  if (!s) return;
  const parts = [];
  if (s.postmarket_latest) parts.push(`盤後報告 ${md(s.postmarket_latest)}`);
  if (s.intraday_last_tick) parts.push(`盯盤檢查 ${ago(s.intraday_last_tick)}`);
  parts.push(`網頁更新 ${ago(s.built_at)}`);
  $("#status-line").textContent = parts.join(" · ");
}

/* ---------------- 盤後籌碼 ---------------- */
async function loadIndex() {
  try {
    STATE.dates = (await getJSON("data/postmarket/index.json")).dates || [];
  } catch { STATE.dates = []; }
  const sel = $("#pm-date");
  if (!STATE.dates.length) {
    $("#pm-empty").hidden = false; $("#pm-body").hidden = true; sel.parentElement.hidden = true;
    return;
  }
  const cur = sel.value;
  sel.innerHTML = STATE.dates.map((d) => {
    const w = WEEK[new Date(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8)).getDay()];
    return `<option value="${d}">${ymd(d)}（${w}）</option>`;
  }).join("");
  sel.value = STATE.dates.includes(cur) ? cur : STATE.dates[0];
  await loadReport(sel.value);
}
$("#pm-date").onchange = (e) => loadReport(e.target.value);

async function loadReport(date) {
  const r = await getJSON(`data/postmarket/${date}.json`);
  STATE.report = r;
  $("#pm-empty").hidden = true; $("#pm-body").hidden = false;
  const m = r.market;
  $("#pm-meta").textContent = `上市普通股 ${m.stock_count} 檔，成交值 3 億以上 ${m.liquid_count} 檔｜產生於 ${hhmm(r.generated_at)}`;

  const k = [];
  if (m.margin_today_k != null) {
    const d = (m.margin_today_k - m.margin_prev_k) / 1e5;
    k.push(kpi("上市融資餘額", `${fmtN(m.margin_today_k / 1e5, 0)}<small>億</small>`, `<span class="${dir(d)}">${signed(d, 1)} 億</span> 較前日`));
  }
  k.push(kpi("三大法人合計", `<span class="${dir(m.inst_total_lots)}">${signed(m.inst_total_lots)}</span><small>張</small>`, "個股加總買賣超"));
  k.push(kpi("融資增減合計", `<span class="${dir(m.margin_total_lots)}">${signed(m.margin_total_lots)}</span><small>張</small>`, "散戶槓桿動向"));
  k.push(kpi("全市場當沖比", `${fmtN(m.daytrade_pct, 1)}<small>%</small>`, m.daytrade_pct >= 40 ? '<span class="tag warn">投機熱度高</span>' : "當沖股數 ÷ 成交股數"));
  $("#pm-kpis").innerHTML = k.join("");
  const v = $("#pm-verdict");
  v.className = `verdict ${m.verdict}`;
  v.innerHTML = `<span>${m.verdict === "warn" ? "⚠" : m.verdict === "good" ? "✓" : "ⓘ"}</span><span>判讀：${esc(m.verdict_text)}</span>`;

  const groups = {};
  r.sections.forEach((s) => (groups[s.group] = groups[s.group] || []).push(s));
  let html = "";
  for (const [g, secs] of Object.entries(groups)) {
    html += `<h2 class="group-title">${esc(g)}${g === "集保週籌碼" && r.tdcc_date ? `（資料日 ${md(r.tdcc_date)}）` : ""}</h2><div class="sec-grid">`;
    secs.forEach((s) => {
      html += `<section class="card"><div class="card-head"><h3>${s.note ? `<span class="tag ${s.note}">${s.note === "warn" ? "留意" : "偏多"}</span>` : ""}${esc(s.title)}</h3></div>
        <div class="table-wrap"><table class="grid" id="sec-${s.id}"></table></div></section>`;
    });
    html += "</div>";
  }
  if (!r.tdcc_has_weekly_change && r.tdcc_date) {
    html += `<div class="banner info">集保股權分散表每週更新一次。已存下 ${md(r.tdcc_date)} 這週的資料，下週起會出現「大戶增、散戶減」的週變化排行。</div>`;
  }
  $("#pm-sections").innerHTML = html;
  r.sections.forEach((s) => table($(`#sec-${s.id}`), s.cols, s.rows));
  ALL.limit = 50;
  renderAll();
}
function kpi(l, v, d) {
  return `<div class="kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="delta muted">${d}</div></div>`;
}

/* 全部個股：搜尋、訊號篩選、排序 */
const ALL = { q: "", sig: null, sortCol: 4, asc: false, limit: 50 };
$("#sig-chips").innerHTML = SIGNALS.map((g, i) => `<button class="chip" aria-pressed="false" data-i="${i}">${g.t}</button>`).join("");
$("#sig-chips").onclick = (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  const i = +b.dataset.i;
  ALL.sig = ALL.sig === i ? null : i;
  document.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(+c.dataset.i === ALL.sig)));
  ALL.limit = 50; renderAll();
};
$("#q").oninput = (e) => { ALL.q = e.target.value.trim(); ALL.limit = 50; renderAll(); };
$("#all-more").onclick = () => { ALL.limit += 100; renderAll(); };
$("#all-table").onclick = (e) => {
  const th = e.target.closest("th.sortable"); if (!th) return;
  const i = +th.dataset.i;
  if (ALL.sortCol === i) ALL.asc = !ALL.asc; else { ALL.sortCol = i; ALL.asc = false; }
  renderAll();
};
function renderAll() {
  const r = STATE.report; if (!r) return;
  const { cols, rows } = r.stocks;
  const iSig = cols.indexOf("訊號");
  let list = rows;
  if (ALL.q) {
    const q = ALL.q.toLowerCase();
    list = list.filter((x) => String(x[0]).includes(q) || String(x[1]).toLowerCase().includes(q));
  }
  if (ALL.sig != null) list = list.filter((x) => String(x[iSig] || "").includes(SIGNALS[ALL.sig].k));
  const c = ALL.sortCol;
  list = [...list].sort((a, b) => {
    const x = a[c], y = b[c];
    if (x == null) return 1; if (y == null) return -1;
    const v = typeof x === "number" ? x - y : String(x).localeCompare(String(y), "zh-Hant");
    return ALL.asc ? v : -v;
  });
  $("#all-count").textContent = `${list.length} 檔${ALL.sig != null || ALL.q ? `（共 ${rows.length}）` : ""}`;
  table($("#all-table"), cols, list.slice(0, ALL.limit), { sortable: true, sortCol: c, asc: ALL.asc });
  $("#all-more").hidden = list.length <= ALL.limit;
}

/* ---------------- 我的盯盤（解密） ---------------- */
const PRIV = { pw: null, data: null, day: null };
const LS_KEY = "twpm.pw";
const ls = {
  get() { try { return localStorage.getItem(LS_KEY); } catch { return null; } },
  set(v) { try { localStorage.setItem(LS_KEY, v); } catch { /* 無痕模式 */ } },
  del() { try { localStorage.removeItem(LS_KEY); } catch { /* 無痕模式 */ } },
};
const b64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
async function decrypt(env, pw) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: b64(env.salt), iterations: env.iter, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(env.iv) }, key, b64(env.ct));
  return JSON.parse(new TextDecoder().decode(pt));
}
async function unlock(pw, remember) {
  pw = pw.trim(); // 與後端一致：密碼前後空白不算
  const env = await getJSON("data/private.enc.json");
  let data;
  try { data = await decrypt(env, pw); } catch { throw new Error("BAD_PASSWORD"); }
  PRIV.pw = pw; PRIV.data = data;
  if (remember) ls.set(pw);
  $("#me-locked").hidden = true; $("#me-body").hidden = false; $("#lock-ico").textContent = "";
  renderPrivate();
}
async function tryRemembered() {
  const pw = ls.get();
  if (!pw || !STATE.status?.private_available) return;
  try { await unlock(pw, false); } catch (e) { if (e.message === "BAD_PASSWORD") ls.del(); } // 網路失敗不刪
}
$("#unlock-form").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("#unlock-btn"); btn.disabled = true; btn.textContent = "解鎖中…"; $("#unlock-err").textContent = "";
  try { await unlock($("#pw").value, $("#remember").checked); $("#pw").value = ""; }
  catch (err) {
    $("#unlock-err").textContent = err.message === "BAD_PASSWORD" ? "密碼不對，請再試一次。" : "讀不到私人區資料，請稍後再試。";
  } finally { btn.disabled = false; btn.textContent = "解鎖"; }
};
$("#lock-btn").onclick = () => {
  PRIV.pw = null; PRIV.data = null; ls.del();
  $("#me-body").hidden = true; $("#me-locked").hidden = false; $("#lock-ico").textContent = "🔒";
};
$("#day-pick").onchange = (e) => { PRIV.day = e.target.value; renderDay(); };

function marketState(d) {
  const snap = d.snapshot;
  if (!snap) return { cls: "", text: "尚未取得行情" };
  const n = taipeiNow();
  const mins = n.getHours() * 60 + n.getMinutes();
  const today = `${n.getFullYear()}${String(n.getMonth() + 1).padStart(2, "0")}${String(n.getDate()).padStart(2, "0")}`;
  const qd = Object.values(snap.quotes || {})[0]?.date;
  if (qd === today && mins >= 540 && mins <= 815) {
    const fresh = Date.now() - new Date(d.last_tick).getTime() < 20 * 60000;
    return fresh ? { cls: "live", text: "盤中監控中" } : { cls: "warn", text: "盤中，但排程超過 20 分鐘沒更新" };
  }
  if (qd === today && mins > 815) return { cls: "ok", text: "今日已收盤" };
  if (qd === today) return { cls: "", text: "等待開盤" };
  return { cls: "", text: `非交易時段（顯示 ${md(qd)} 收盤）` };
}

function renderPrivate() {
  const d = PRIV.data; if (!d) return;
  const ms = marketState(d);
  const dayKeys = Object.keys(d.days || {}).sort().reverse();
  const today = dayKeys[0] ? d.days[dayKeys[0]] : null;
  const pills = [
    `<span class="pill ${ms.cls}"><span class="dot"></span>${esc(ms.text)}</span>`,
    `<span class="pill">最後檢查 ${esc(hhmm(d.last_tick))}${d.last_tick ? `（${ago(d.last_tick)}）` : ""}</span>`,
    d.line_configured
      ? `<span class="pill ok"><span class="dot"></span>LINE 推播已連線</span>`
      : `<span class="pill warn"><span class="dot"></span>LINE 未設定：警示只顯示在這裡</span>`,
  ];
  if (today && d.max_messages_per_day) pills.push(`<span class="pill">今日一般推播 ${today.sent || 0} / ${d.max_messages_per_day}</span>`);
  if (d.line_configured && d.max_messages_per_month) pills.push(`<span class="pill">本月 LINE ${d.month_sent || 0} / ${d.max_messages_per_month} 則</span>`);
  let banner = "";
  if (d.watch_source === "example") banner = `<div class="banner warn">目前用的是<b>範例自選股</b>（watchlist.example.csv）。把你自己的清單設到 GitHub Secret <code>WATCHLIST_CSV</code> 後，這裡就會換成你的持股與提醒價（做法見 README）。</div>`;
  $("#me-status").innerHTML = pills.join("") + (banner ? `<div style="flex-basis:100%">${banner}</div>` : "");

  const quotes = d.snapshot?.quotes || {};
  let mv = 0, cost = 0, held = 0;
  d.watch.forEach((w) => {
    const q = quotes[w.code];
    if (q && w.shares && w.cost) { mv += q.close * w.shares; cost += w.cost * w.shares; held++; }
  });
  const pl = mv - cost;
  const todayAlerts = today?.alerts?.length || 0;
  $("#me-kpis").innerHTML = [
    kpi("自選股", `${d.watch.length}<small>檔</small>`, held ? `其中持股 ${held} 檔` : "沒有填持股"),
    kpi("持股市值", held ? `${fmtN(mv)}<small>元</small>` : "—", held ? `成本 ${fmtN(cost)} 元` : ""),
    kpi("未實現損益", held ? `<span class="${dir(pl)}">${signed(pl)}</span><small>元</small>` : "—",
      held ? `<span class="${dir(pl)}">${signed((pl / cost) * 100, 1)}%</span>，未扣手續費與稅` : ""),
    kpi("今日警示", `${todayAlerts}<small>筆</small>`, today?.closed ? "已送出收盤摘要" : dayKeys[0] ? md(dayKeys[0]) : "尚無紀錄"),
  ].join("");

  const pmw = d.postmarket_watch;
  const pmMap = {};
  if (pmw) pmw.rows.forEach((r) => (pmMap[r[0]] = Object.fromEntries(pmw.cols.map((c, i) => [c, r[i]]))));
  const hits = new Set((today?.alerts || []).map((a) => (a.msg.match(/\b(\d{4,6})\b/) || [])[1]).filter(Boolean));
  $("#watch-grid").innerHTML = d.watch.map((w) => watchCard(w, quotes[w.code], pmMap[w.code], hits.has(w.code))).join("");

  const sel = $("#day-pick");
  sel.innerHTML = dayKeys.map((k) => `<option value="${k}">${ymd(k)}</option>`).join("") || `<option>尚無紀錄</option>`;
  if (!PRIV.day || !dayKeys.includes(PRIV.day)) PRIV.day = dayKeys[0];
  sel.value = PRIV.day || "";
  renderDay();

  if (pmw && pmw.rows.length) {
    $("#pmw-card").hidden = false;
    $("#pmw-date").textContent = `${ymd(pmw.date)} 盤後`;
    table($("#pmw-table"), pmw.cols, pmw.rows);
  } else $("#pmw-card").hidden = true;

  const errs = d.errors || [];
  $("#err-card").hidden = !errs.length;
  $("#errors").innerHTML = errs.slice().reverse().map((e) => `<li>${esc(e)}</li>`).join("");
}

function watchCard(w, q, pm, hit) {
  const name = w.name || q?.name || "";
  if (!q) {
    return `<article class="wcard"><div class="w-top"><div><div class="w-name">${esc(w.code)}<span>${esc(name)}</span></div>
      <div class="w-note">${esc(w.note)}</div></div></div><p class="muted small">還沒有行情資料（下一次盤中檢查後出現）。</p></article>`;
  }
  const px = q.close;
  const lv = [
    ["stop", "停損", w.shares ? w.stop : null], ["buy", "買進", w.buy_below], ["cost", "成本", w.cost],
    ["brk", "突破", w.break_above], ["tgt", "停利", w.target],
  ].filter((x) => x[2]);
  // 價格梯：把各提醒價與現價放在同一條線上
  let ladder = "";
  if (lv.length) {
    const vals = lv.map((x) => x[2]).concat(px);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.08 || px * 0.05; lo -= pad; hi += pad;
    const pos = (v) => ((v - lo) / (hi - lo)) * 100;
    const sorted = [...lv].sort((a, b) => a[2] - b[2]);
    let lastPos = -99, lift = 0;
    const marks = sorted.map(([k, t, v]) => {
      const p = pos(v);
      lift = p - lastPos < 14 ? (lift ? 0 : 12) : 0; lastPos = p;
      return `<span class="mk ${k}" style="left:${p}%"><b style="top:${18 + lift}px">${t} ${fmtN(v, v < 100 ? 1 : 0)}</b></span>`;
    }).join("");
    ladder = `<div class="ladder" style="height:${58}px"><div class="track"></div>${marks}
      <span class="now" style="left:${pos(px)}%"><i>${fmtN(px, px < 100 ? 2 : 1)}</i></span></div>`;
  }
  const levels = lv.map(([k, t, v]) => {
    const dist = (v / px - 1) * 100;
    return `<div><span class="k">${t} ${fmtN(v, v < 100 ? 2 : 0)}</span><span class="${dir(dist)}">${signed(dist, 1)}%</span></div>`;
  }).join("");
  let pos = "";
  if (w.shares && w.cost) {
    const p = (px - w.cost) * w.shares;
    pos = `<div class="pos"><span>持股 ${fmtN(w.shares)} 股 · 成本 ${fmtN(w.cost, w.cost < 100 ? 2 : 0)}</span>
      <span class="${dir(p)}"><b>${signed(p)} 元</b>（${signed((px / w.cost - 1) * 100, 1)}%）</span></div>`;
  }
  const tags = [];
  if (pm) {
    if (pm["外資買賣超(張)"] != null) tags.push(`<span class="tag ${dir(pm["外資買賣超(張)"])}">外資 ${signed(pm["外資買賣超(張)"])} 張</span>`);
    if (pm["投信買賣超(張)"] != null) tags.push(`<span class="tag ${dir(pm["投信買賣超(張)"])}">投信 ${signed(pm["投信買賣超(張)"])} 張</span>`);
    if (pm["融資增減(張)"] != null) tags.push(`<span class="tag">融資 ${signed(pm["融資增減(張)"])} 張</span>`);
    if (pm["訊號"]) tags.push(sigTags(pm["訊號"]));
  }
  const range = q.high && q.low ? ` · 高 ${fmtN(q.high, 1)} 低 ${fmtN(q.low, 1)}` : "";
  const vol = q.total_volume ? ` · 量 ${fmtN(q.total_volume)} 張` : "";
  return `<article class="wcard${hit ? " hit" : ""}">
    <div class="w-top">
      <div><div class="w-name">${esc(w.code)}<span>${esc(name)}</span></div><div class="w-note">${esc(w.note)}</div></div>
      <div class="w-px"><div class="p ${dir(q.change_rate)}">${fmtN(px, px < 100 ? 2 : 1)}</div>
        <div class="c ${dir(q.change_rate)}">${signed(q.change_rate, 2)}%</div></div>
    </div>
    ${ladder}
    ${levels ? `<div class="levels">${levels}</div>` : `<p class="muted small">沒有設定提醒價</p>`}
    ${pos}
    ${tags.length ? `<div class="w-tags">${tags.join("")}</div>` : ""}
    <div class="w-meta">${esc(md(q.date))} ${esc(q.time)} · ${esc(q.source)}${range}${vol}${hit ? ' · <b style="color:var(--accent)">今日有警示</b>' : ""}</div>
  </article>`;
}

const MSG_STATUS = {
  sent: ["good", "已推播"], no_line: ["warn", "未推播：LINE 未設定"], over_cap: ["", "超過每日上限，未推播"],
  over_month: ["warn", "本月 LINE 額度將用完，未推播"],
  dry_run: ["info", "測試模式"], failed: ["up", "推播失敗"],
};
function renderDay() {
  const D = PRIV.data?.days?.[PRIV.day];
  const al = (D?.alerts || []).slice().reverse();
  $("#alerts").innerHTML = al.length
    ? al.map((a) => `<li class="${a.urgent ? "urgent" : ""}"><span class="t">${esc(a.t)}</span><span class="m">${esc(a.msg)}</span></li>`).join("")
    : `<li><span class="t"></span><span class="muted">這天沒有觸發警示</span></li>`;
  const ms = (D?.messages || []).slice().reverse();
  $("#msg-count").textContent = ms.length ? `${ms.length} 則` : "";
  $("#messages").innerHTML = ms.length ? ms.map((m, i) => {
    const st = MSG_STATUS[m.status] || MSG_STATUS[m.status?.startsWith("failed") ? "failed" : ""] || ["", m.status];
    const first = esc(m.text.split("\n").slice(0, 2).join("　"));
    return `<div class="msg" data-i="${i}"><div class="msg-head"><span><b>${esc(m.t)}</b> <span class="tag ${st[0]}">${st[1]}</span></span>
      <button type="button">展開</button></div><div class="first">${first}</div><pre>${esc(m.text)}</pre></div>`;
  }).join("") : `<p class="muted">這天沒有送出訊息</p>`;
}
$("#messages").onclick = (e) => {
  const b = e.target.closest(".msg-head button"); if (!b) return;
  const box = b.closest(".msg"); box.classList.toggle("open");
  b.textContent = box.classList.contains("open") ? "收合" : "展開";
};

/* ---------------- 啟動與自動更新 ---------------- */
async function refresh(first = false) {
  let s;
  try { s = await getJSON("data/status.json"); } catch { return; }
  const changed = !STATE.status || s.built_at !== STATE.status.built_at;
  const pmChanged = !STATE.status || s.postmarket_latest !== STATE.status.postmarket_latest;
  STATE.status = s;
  renderStatus();
  $("#me-unavailable").hidden = s.private_available;
  $("#unlock-form").hidden = !s.private_available;
  if (first || pmChanged) await loadIndex();
  if (changed && PRIV.pw && !first) {
    try { PRIV.data = await decrypt(await getJSON("data/private.enc.json"), PRIV.pw); renderPrivate(); } catch { /* 密碼換了 */ }
  } else if (PRIV.data) renderPrivate(); // 更新「幾分鐘前」
}
(async () => {
  await refresh(true);
  if (location.hash === "#me") showTab("me");
  else if (ls.get()) tryRemembered();
  setInterval(refresh, 60000);
})();
