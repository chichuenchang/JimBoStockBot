// StockBot dashboard. Colors follow the entity: arm i -> categorical slot i (fixed order);
// the benchmark is a neutral dashed line, never a categorical slot.
"use strict";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isBench = (s) => s.startsWith("benchmark");
const ANALYST = "analyst";  // usage rows with no arm (queries.ANALYST)
const gone = [];  // arms in the DB but not in config: slots after config arms, in first-seen order
function colorOf(series) {
  if (isBench(series) || series === ANALYST) return css("--bench");  // not a bot: neutral
  const arms = window.SERIES.filter((s) => !isBench(s));
  let i = arms.indexOf(series);
  if (i < 0) i = arms.length + (gone.includes(series) ? gone.indexOf(series) : gone.push(series) - 1);
  return css(`--series-${(i % 8) + 1}`);
}
const nameOf = (s) => (s === ANALYST ? "market report" : window.NAMES[s] || s);
const stateColor = (state) => css(state === "ok" ? "--good" : state === "warn" ? "--warning" : "--critical");
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const kTok = (n) => (n >= 1e6 ? fmt(n / 1e6, 1) + "M" : n >= 1e3 ? fmt(n / 1e3, 0) + "k" : String(n));
const signed = (v, d = 2) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const tri = (v) => (v > 0 ? "▲" : v < 0 ? "▼" : "");
const dollars = (v) => "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
// signed dollars, sign before the $: +$82, -$1,204
const money = (v, d = 0) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : v < 0 ? "-" : "") + "$"
  + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const ET = "America/New_York";
const etTime = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: ET,
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
// one formatter each, reused: toLocaleString builds a new one per call, too slow for a few thousand chart points
const etClockFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const etDayFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, month: "short", day: "numeric" });
const etLongDayFmt = new Intl.DateTimeFormat("en-US", { timeZone: ET, weekday: "short", month: "short", day: "numeric" });
const etClock = (t) => etClockFmt.format(new Date(t));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const get = (url) => fetch(url).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${url}: HTTP ${r.status}`))));
const tipAttr = (text) => (text ? ` data-tip="${esc(text)}"` : "");
const pref = (key, fallback) => { try { return localStorage.getItem("jbsb." + key) ?? fallback; } catch (e) { return fallback; } };
const setPref = (key, value) => { try { localStorage.setItem("jbsb." + key, value); } catch (e) { /* private window: this page only */ } };
const quantile = (xs, q) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0; };

const paintSwatches = (root) => root.querySelectorAll(".swatch[data-series]").forEach((el) => {
  el.style.background = colorOf(el.dataset.series); });
paintSwatches(document);

// segmented buttons (data-<key>="value"): a click marks that one on and hands its value to pick.
// remember: the choice outlives the page (localStorage) and is returned so the caller starts from it
function segmented(key, pick, remember = false) {
  const bs = [...document.querySelectorAll(`button[data-${key}]`)];
  const mark = (b) => bs.forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b); });
  bs.forEach((b) => b.onclick = () => { mark(b); if (remember) setPref(key, b.dataset[key]); pick(b.dataset[key]); });
  const saved = remember && bs.find((b) => b.dataset[key] === pref(key, null));
  mark(saved || bs.find((b) => b.classList.contains("on")));
  return saved ? saved.dataset[key] : null;
}

// light / dark: base.html applies the saved choice before paint; charts read colors once, so reload
document.getElementById("theme").onclick = () => {
  const next = css("color-scheme") === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", next); } catch (e) { /* private window: this page only */ }
  document.documentElement.dataset.theme = next;
  location.reload();
};

// ---------- help bubbles: [data-tip] ----------
// Not title=: that waits a second, cannot be styled, and never shows on a touch screen. Shows on hover, keyboard focus and tap.
(function initTips() {
  const tip = Object.assign(document.createElement("div"), { id: "tip", hidden: true });
  tip.setAttribute("role", "tooltip");
  document.body.append(tip);
  let owner = null;
  const hide = () => { owner = null; tip.hidden = true; };
  function show(el) {
    owner = el;
    tip.textContent = el.dataset.tip;  // text, never HTML: tips carry model and stock names
    tip.hidden = false;
    const r = el.getBoundingClientRect(), t = tip.getBoundingClientRect(), gap = 8;
    tip.style.top = `${r.top - t.height - gap >= 4 ? r.top - t.height - gap : r.bottom + gap}px`;  // above, else below
    tip.style.left = `${Math.max(4, Math.min(r.left + r.width / 2 - t.width / 2, innerWidth - t.width - 4))}px`;
  }
  const over = (e) => {
    const el = e.target.closest?.("[data-tip]");
    if (!el || !el.dataset.tip) hide();
    else if (el !== owner) show(el);
  };
  document.addEventListener("pointerover", over);
  document.addEventListener("focusin", over);
  document.addEventListener("focusout", hide);
  document.addEventListener("pointerdown", (e) => { if (e.pointerType === "mouse") hide(); });  // a click is not a question
  document.documentElement.addEventListener("pointerleave", hide);
  addEventListener("scroll", hide, { capture: true, passive: true });
  setInterval(() => { if (owner && !owner.isConnected) hide(); }, 1000);  // a poll replaced the node under the pointer
})();

// polled content: change only the nodes that differ, so focus, a press in progress and text selection survive the latest data
function patchChildren(el, fresh) {
  const had = [...el.childNodes], want = [...fresh.childNodes];
  want.forEach((n, i) => {
    const o = had[i];
    if (!o) el.append(n);
    else if (o.isEqualNode(n)) return;
    else if (o.cloneNode(false).isEqualNode(n.cloneNode(false))) patchChildren(o, n);
    else o.replaceWith(n);
  });
  had.slice(want.length).forEach((o) => o.remove());
}
function setHtml(el, html) {
  const fresh = el.cloneNode(false);  // a table's rows only parse inside a table
  fresh.innerHTML = html;
  patchChildren(el, fresh);
}

// server-rendered stats (tiles, stats tables, KPIs): re-fetch this page and swap them in, so they keep pace with the charts.
// dress[selector](fresh): finish a fresh node the way the one on screen was (layout, sort), so equal data patches nothing
async function refreshRendered(selectors, dress = {}) {
  const doc = new DOMParser().parseFromString(await fetch(location.pathname).then((r) => r.text()), "text/html");
  for (const s of selectors) {
    const el = document.querySelector(s), fresh = doc.querySelector(s);
    if (!el || !fresh) continue;
    paintSwatches(fresh);  // before comparing: a painted swatch must equal the one on screen
    dress[s]?.(fresh, el);
    patchChildren(el, fresh);
  }
}

// ---------- status strip ----------
let marketOpen = false, lastStatus = null;
const meter = (ratio, state) => `<span class="meter" style="--mc:${stateColor(state)}"><i style="width:${(Math.min(1, Math.max(0, ratio)) * 100).toFixed(1)}%"></i></span>`;
async function loadStatus() {
  const st = lastStatus = await get("/api/status");
  marketOpen = st.market_open;
  const age = st.heartbeat ? (Date.now() - new Date(st.heartbeat)) / 1000 : Infinity;
  const up = age < 120;
  const dot = (ok, live) => `<span class="dot${live ? " live" : ""}" style="background:${css(ok ? "--good" : "--critical")}"></span>`;
  const items = [
    `<span class="hstat${up ? "" : " bad"}" data-tip="The background program that runs the bots. Down = nothing is trading and stops are not watched.">${dot(up, up && st.market_open)}Daemon <b>${up ? "running" : "down"}</b>${up || !st.heartbeat ? "" : ` since ${etTime(st.heartbeat)}`}</span>`,
    `<span class="hstat" data-tip="US stock market, regular hours: 09:30 to 16:00 New York time, weekdays. Bots only trade while it is open.">Market <b>${st.market_open ? "open" : "closed"}</b></span>`,
    `<span class="hstat${st.data_ok ? "" : " bad"}" data-tip="Live prices from Alpaca. On error the bots skip their decisions until it is back.">${dot(st.data_ok)}Prices <b>${st.data_ok ? "ok" : "error"}</b></span>`,
  ];
  if (st.paused_all) items.push(`<span class="hstat bad"><span class="dot" style="background:${css("--warning")}"></span><b>All bots paused</b></span>`);
  // meter shows the worst state of any model cap or the global cap; text names the models over
  const t = st.budget._total;
  const rank = { ok: 0, warn: 1, exhausted: 2 };
  const hot = Object.entries(st.budget).filter(([, b]) => b.state !== "ok");
  const worst = hot.reduce((w, [, b]) => (rank[b.state] > rank[w] ? b.state : w), "ok");
  const why = hot.map(([name, b]) => `${name === "_total" ? "total" : esc(name)} ${b.state}`).join(", ");
  const perModel = Object.entries(st.budget).filter(([name, b]) => name !== "_total" && b.cap).map(([name, b]) => `${name} $${fmt(b.spent)} of $${fmt(b.cap, 0)}`).join("\n");
  items.push(`<span class="hsep"></span><span class="hstat"${tipAttr(`Billed for AI calls this month, against the monthly cap. A model at its cap stops deciding.${perModel ? "\n" + perModel : ""}`)}>AI budget ${meter(t.cap ? t.spent / t.cap : 0, worst)}<b>$${fmt(t.spent)}</b> / $${fmt(t.cap, 0)}${why ? ` · <b>${why}</b>` : ""}</span>`);
  // flat-plan models (llm/quota.py): the fullest window's share of its allowance; every window and the month's tokens in the bubble
  for (const [name, q] of Object.entries(st.quota)) {
    const tk = st.budget[name]?.tokens, pct = (w) => `${fmt(w.used_ratio * 100, 0)}%`;
    const tokens = tk ? `\nThis month: ${tk.calls} calls, ${kTok(tk.input)} tokens in / ${kTok(tk.output)} out.` : "";
    const worst = q.windows.reduce((a, w) => (w.used_ratio > a.used_ratio ? w : a));
    items.push(`<span class="hstat"${tipAttr(`${window.TIPS.flat_plan}\n`
      + `${q.windows.map((w) => `${w.window} window: ${pct(w)}`).join("\n")}${tokens}\nChecked ${etTime(worst.ts)}.`)}>${esc(name)} plan `
      + `${meter(worst.used_ratio, q.state)}<b>${pct(worst)}</b> of ${esc(worst.window)}</span>`);
  }
  setHtml(document.getElementById("status"), items.join(""));
  drawControls(st);
  return st;
}
// interval + pause: built once, then only what changed is touched, so a number being typed is never wiped
function drawControls(st) {
  const box = document.getElementById("controls"), $ = (id) => document.getElementById(id);
  if (!box.firstChild) {
    box.innerHTML = `<label class="interval" data-tip="How often each bot looks at the market and decides. Shorter = faster reactions, more AI calls.">Decide every <input id="intervalin" type="number" min="1" max="60" inputmode="numeric"> min</label>`
      + `<button id="intervalset">Set</button><button id="intervalreset" data-tip="Back to the value in settings.yaml" hidden>Reset</button>`
      + `<button id="pauseall"${tipAttr(window.TIPS.pause)}></button>`
      + `<span id="updated" data-tip="When these numbers were last refreshed: every 5 seconds while the market is open, else every minute."></span>`;
    const postInterval = async (minutes) => {
      const r = await fetch("/api/interval", { method: "POST", headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ minutes }) });
      $("intervalin").classList.toggle("bad", !r.ok);
      if (r.ok) { $("intervalin").blur(); loadStatus(); }
    };
    $("intervalset").onclick = () => postInterval(Number($("intervalin").value));
    $("intervalin").onkeydown = (e) => { if (e.key === "Enter") $("intervalset").click(); };
    $("intervalreset").onclick = () => postInterval(null);
    $("pauseall").onclick = async () => {
      await fetch(lastStatus.paused_all ? "/api/resume_all" : "/api/pause_all", { method: "POST" });
      loadStatus();
    };
  }
  if (document.activeElement !== $("intervalin")) $("intervalin").value = st.interval.minutes;
  $("intervalreset").hidden = st.interval.source !== "override";
  $("pauseall").textContent = st.paused_all ? "Resume all" : "Pause all";
  $("pauseall").classList.toggle("warn", st.paused_all);
  $("updated").textContent = `↻ ${new Date().toLocaleTimeString("en-US", { hour12: false })}`;
}

// ---------- chart helpers ----------
const INK = { muted: css("--text-muted"), grid: css("--grid"), surface: css("--surface-1") };  // theme switch reloads the page
const Y_AXIS = 64;  // px, every chart: stacked charts share their x positions
if (window.Chart) {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = INK.muted;
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: css("--surface-2"), borderColor: css("--border"), borderWidth: 1,
    titleColor: css("--text-secondary"), bodyColor: css("--text-primary"), titleFont: { weight: 500 }, bodyFont: { weight: 600 },
    padding: 10, cornerRadius: 8, caretSize: 0, boxWidth: 12, boxHeight: 2, boxPadding: 6 });  // 2px-high box = a line key
  // crosshair: a hairline at the hovered moment on line charts; the reader aims at a time, not at a 2px line
  Chart.register({ id: "crosshair", afterDatasetsDraw(chart) {
    const [hit] = chart.getActiveElements();
    if (chart.config.type !== "line" || !hit) return;
    const { ctx, chartArea: { top, bottom } } = chart;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(hit.element.x, top);
    ctx.lineTo(hit.element.x, bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = INK.muted;
    ctx.stroke();
    ctx.restore();
  } });
}
function baseOptions(yFmt) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { display: false },  // drawLegend: HTML, so it can carry values and answer hover
      // every series at the hovered moment, best first; value before name: the reader has the series, wants the number
      tooltip: { itemSort: (a, b) => b.parsed.y - a.parsed.y, callbacks: { label: (c) => ` ${yFmt(c.parsed.y)}  ${c.dataset.label}` } },
    },
    scales: {
      x: { grid: { color: INK.grid }, border: { display: false }, ticks: { color: INK.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
      y: { grid: { color: INK.grid }, border: { display: false }, ticks: { color: INK.muted, callback: yFmt, maxTicksLimit: 6 },
           afterFit: (axis) => { axis.width = Y_AXIS; } },
    },
  };
}
const lineStyle = (s) => ({ key: s, label: nameOf(s), borderColor: colorOf(s), backgroundColor: colorOf(s), borderWidth: 2,
  pointRadius: 0, pointHoverRadius: 4, pointHoverBorderWidth: 2, pointHoverBorderColor: INK.surface,
  borderDash: isBench(s) ? [6, 4] : [], order: isBench(s) ? 1 : 0, tension: 0, spanGaps: true });
// clock-time x axis within one day (epoch ms), a tick on whole New York hours
function timeLinear(opts, from, to) {
  const HOUR = 36e5;
  Object.assign(opts.scales.x, { type: "linear", min: from, max: to, afterBuildTicks: (axis) => {
    const step = Math.max(1, Math.ceil((to - from) / HOUR / Math.max(2, Math.floor(axis.width / 80)))) * HOUR;
    axis.ticks = [];
    for (let t = Math.ceil(from / step) * step; t <= to; t += step) axis.ticks.push({ value: t });
  } });
  opts.scales.x.ticks = { color: INK.muted, maxRotation: 0, autoSkip: false, callback: (v) => etClock(v) };
  opts.plugins.tooltip.callbacks.title = (items) => (items.length ? etTime(items[0].parsed.x) : "");
  return opts;
}
const charts = {};
function draw(id, type, data, opts) {
  if (charts[id]?.config.type === type) { charts[id].data = data; charts[id].options = opts; charts[id].update(); return; }
  charts[id]?.destroy();  // type changed (usage: line today, bars over days): update cannot switch it
  charts[id] = new Chart(document.getElementById(id), { type, data, options: opts });
}
// a chart with nothing to plot: a sentence in its place, not bare axes. Returns empty, so callers can stop there.
// why: the sentence; omitted = the template's own p.chartnote right after the chart
function chartEmpty(id, empty, why) {
  const box = document.getElementById(id).parentElement;
  let note = box.nextElementSibling;
  if (!note?.classList.contains("chartnote")) box.after(note = Object.assign(document.createElement("p"), { className: "empty chartnote" }));
  if (why) note.textContent = why;
  box.hidden = empty;
  note.hidden = !empty;
  return empty;
}
const barStyle = (s) => ({ backgroundColor: colorOf(s), borderColor: css("--surface-1"), borderWidth: 1, borderRadius: 4,
                           borderSkipped: "start", maxBarThickness: 24 });

// ---------- trading-time axis ----------
// Snapshots exist only while the market is open: on a clock axis nights and weekends take 3/4 of the width as flat lines.
// Instead x is trading time: sessions back to back, as on any stock chart.
const SESSION_GAP = 6 * 36e5;  // a longer silence = the next session (overnight is 17.5 h; the PC off at lunch is not a new day)
// ms of trading time since the first stamp: real spacing inside a session, nothing for the closed hours between two
function tradingX(ts) {
  const x = [0];
  for (let i = 1; i < ts.length; i++) x.push(x[i - 1] + (ts[i] - ts[i - 1] > SESSION_GAP ? 0 : ts[i] - ts[i - 1]));
  return x;
}
// every snapshot time of every series, in order: ts (real), x (trading time), starts (index of each session's first)
function sessionGrid(data, lastSessions = 0) {
  const all = [...new Set(Object.values(data).flatMap((pts) => pts.map((p) => p[0])))].sort((a, b) => a - b);
  const starts = all.flatMap((t, i) => (i === 0 || t - all[i - 1] > SESSION_GAP ? [i] : []));
  const from = lastSessions && starts.length > lastSessions ? starts[starts.length - lastSessions] : 0;
  const ts = all.slice(from);
  return { ts, x: tradingX(ts), starts: starts.filter((i) => i >= from).map((i) => i - from) };
}
// a series on the grid: at each slot its last value at or before it; null before its first, and after its last unless hold
function alignTo(ts, pts, hold = false) {
  const out = new Array(ts.length);
  const end = hold ? Infinity : pts.length ? pts[pts.length - 1][0] : -Infinity;
  let j = 0, last = null;
  for (let i = 0; i < ts.length; i++) {
    while (j < pts.length && pts[j][0] <= ts[i]) last = pts[j++][1];
    out[i] = ts[i] > end ? null : last;
  }
  return out;
}
// [x, label] pairs that fit in width: many sessions = one date each; a day or two = the hours too
function sessionTicks(grid, width) {
  const room = Math.max(2, Math.floor(width / 76));
  const dayAt = (i) => [grid.x[i], etDayFmt.format(new Date(grid.ts[i]))];
  if (grid.starts.length > 2) {
    const step = Math.ceil(grid.starts.length / room), n = grid.starts.length;
    return grid.starts.filter((_, k) => (n - 1 - k) % step === 0).map(dayAt);  // counted from the newest: today always labeled
  }
  const hours = [];
  let prev = null;
  grid.ts.forEach((t, i) => {
    const hour = etClock(t).slice(0, 2);
    if (prev !== null && hour !== prev && !grid.starts.includes(i)) hours.push([grid.x[i], `${hour}:00`]);
    prev = hour;
  });
  const step = Math.ceil(hours.length / Math.max(1, room - grid.starts.length));
  const far = (x) => grid.starts.every((i) => Math.abs(grid.x[i] - x) > 30 * 60e3);  // 10:00 sits on top of a 09:30 open's date
  return [...grid.starts.map(dayAt), ...hours.filter(([x], k) => k % step === 0 && far(x))];
}
function sessionOptions(grid, yFmt) {
  const o = baseOptions(yFmt), labels = new Map();
  Object.assign(o.scales.x, { type: "linear", min: 0, max: grid.x.at(-1) || 1,
    afterBuildTicks: (axis) => {
      labels.clear();
      for (const [x, text] of sessionTicks(grid, axis.width)) labels.set(x, text);
      axis.ticks = [...labels.keys()].sort((a, b) => a - b).map((value) => ({ value }));
    } });
  o.scales.x.ticks = { color: INK.muted, maxRotation: 0, autoSkip: false, callback: (v) => labels.get(v) ?? "" };
  o.plugins.tooltip.callbacks.title = (items) => (items.length ? etTime(grid.ts[items[0].dataIndex]) : "");
  return o;
}
const sessionDatasets = (grid, data, series = window.SERIES) => series.filter((s) => data[s]?.length).map((s) =>
  ({ ...lineStyle(s), data: alignTo(grid.ts, data[s]).map((y, i) => ({ x: grid.x[i], y })) }));
const lastValue = (ds) => ds.data.findLast((p) => p.y !== null)?.y ?? null;

// ---------- legends ----------
// items: { key, name, color, dashed, value }. live: rows are buttons (hover dims the other lines, click hides one)
function drawLegend(id, items, { head = null, live = false } = {}) {
  const tag = live ? "button" : "span";
  setHtml(document.getElementById(id), (head ? `<div class="legendhead"><span>${head[0]}</span><span>${head[1]}</span></div>` : "")
    + items.map((it) => `<${tag} class="litem${hiddenSeries.has(it.key) && live ? " off" : ""}${hotSeries === it.key && live ? " hot" : ""}" data-key="${esc(it.key)}"`
      + `${live ? ` aria-pressed="${!hiddenSeries.has(it.key)}"` : ""}><span class="key${it.dashed ? " dash" : ""}" style="--c:${it.color}"></span>`
      + `<span class="lname">${esc(it.name)}</span>${it.value === undefined ? "" : `<b>${it.value}</b>`}</${tag}>`).join(""));
}
const legendItem = (s, value) => ({ key: s, name: nameOf(s), color: colorOf(s), dashed: isBench(s), value });
const hiddenSeries = new Set();
let hotSeries = null;
const SERIES_CHARTS = ["equity", "drawdown"];  // one legend drives both
function styleSeries(ds) {
  const color = colorOf(ds.key);
  ds.hidden = hiddenSeries.has(ds.key);
  ds.borderColor = hotSeries && hotSeries !== ds.key ? Chart.helpers.color(color).alpha(0.2).rgbString() : color;  // the rest fade
  ds.borderWidth = hotSeries === ds.key ? 3 : 2;
  return ds;
}
function restyleSeries() {
  for (const id of SERIES_CHARTS) {
    if (!charts[id]) continue;
    charts[id].data.datasets.forEach(styleSeries);
    charts[id].update("none");
  }
  document.querySelectorAll("#serieslegend button").forEach((b) => {
    b.classList.toggle("off", hiddenSeries.has(b.dataset.key));
    b.classList.toggle("hot", hotSeries === b.dataset.key);
    b.setAttribute("aria-pressed", !hiddenSeries.has(b.dataset.key));
  });
}
function initSeriesLegend() {
  const el = document.getElementById("serieslegend");
  const heat = (key) => { if (key !== hotSeries) { hotSeries = key; restyleSeries(); } };
  const keyOf = (e) => e.target.closest?.("button")?.dataset.key ?? null;
  el.addEventListener("pointerover", (e) => heat(keyOf(e)));
  el.addEventListener("pointerleave", () => heat(null));
  el.addEventListener("focusin", (e) => heat(keyOf(e)));
  el.addEventListener("focusout", () => heat(null));
  el.addEventListener("click", (e) => {
    const key = keyOf(e);
    if (!key) return;
    if (!hiddenSeries.delete(key)) hiddenSeries.add(key);
    restyleSeries();
  });
}

// ---------- index page: treemap (kaito.ai mindshare-arena layout) ----------
// Squarified treemap: areas in descending order -> rects filling w x h.
function squarify(areas, w, h) {
  const out = [];
  let x = 0, y = 0, row = [];
  const worst = (r, side) => {
    const s = r.reduce((a, b) => a + b, 0);
    return Math.max((side * side * Math.max(...r)) / (s * s), (s * s) / (side * side * Math.min(...r)));
  };
  const flush = () => {
    const s = row.reduce((a, b) => a + b, 0);
    if (w >= h) {  // strip down the left edge
      const cw = s / h; let yy = y;
      for (const a of row) { out.push({ x, y: yy, w: cw, h: a / cw }); yy += a / cw; }
      x += cw; w -= cw;
    } else {  // strip along the top
      const rh = s / w; let xx = x;
      for (const a of row) { out.push({ x: xx, y, w: a / rh, h: rh }); xx += a / rh; }
      y += rh; h -= rh;
    }
    row = [];
  };
  for (const a of areas) {
    if (row.length && worst([...row, a], Math.min(w, h)) > worst(row, Math.min(w, h))) flush();
    row.push(a);
  }
  if (row.length) flush();
  return out;
}
// Place box's .tile children (biggest first) by size; tint each by its change, strongest = most saturated.
// cap: change that gets full color; default = biggest change on the map.
// sized: the element whose pixels to fill, when box is a fresh copy not yet on screen
function layoutTiles(box, sizes, rets, cap, sized = box) {
  const tiles = [...box.querySelectorAll(".tile")];
  if (!tiles.length) return;
  const W = sized.clientWidth, H = sized.clientHeight;
  sizes = sizes.map((v) => Math.max(1e-9, v));
  const total = sizes.reduce((a, b) => a + b, 0);
  const maxAbs = cap || Math.max(1e-9, ...rets.map(Math.abs));
  const [up, down, base] = ["--tile-up", "--tile-down", "--surface-0"].map(css);  // once per layout, not per tile
  squarify(sizes.map((v) => (v / total) * W * H), W, H).forEach((r, i) => {
    const t = tiles[i], gap = 2;  // px each side, so neighbours sit 4px apart
    const tint = rets[i] ? Math.round(18 + 52 * Math.min(1, Math.abs(rets[i]) / maxAbs)) : 0;  // flat bot: no tint
    // rounded: a poll with the same data must produce the same style text, or every tile is swapped under the pointer
    Object.assign(t.style, { left: `${Math.round(r.x + gap)}px`, top: `${Math.round(r.y + gap)}px`,
      width: `${Math.round(r.w - 2 * gap)}px`, height: `${Math.round(r.h - 2 * gap)}px`,
      background: `color-mix(in oklab, ${rets[i] >= 0 ? up : down} ${tint}%, ${base})` });
    t.classList.toggle("md", r.w < 210);
    t.classList.toggle("sm", r.w < 150 || r.h < 110);
    t.classList.toggle("xs", r.w < 70 || r.h < 50);
    t.classList.toggle("xxs", r.w < 44 || r.h < 34);
  });
}
// line of pts ([x, y]); base: a y to rule a hairline at (a bot's starting cash), and the wash under the line hangs from it
const sparkSvg = (pts, up, base = null) => {
  if (pts.length < 2) return "";
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]).concat(base === null ? [] : [base]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const xOf = (px) => (((px - x0) / (x1 - x0 || 1)) * 100).toFixed(2);
  const yOf = (py) => (y1 === y0 ? 15 : 30 - ((py - y0) / (y1 - y0)) * 28 - 1).toFixed(2);  // flat series: mid-line
  const d = pts.map(([px, py]) => `${xOf(px)},${yOf(py)}`).join(" ");
  const color = css(up ? "--good" : "--critical"), floor = base === null ? 30 : yOf(base);
  return `<svg viewBox="0 0 100 30" preserveAspectRatio="none"><polygon points="0,${floor} ${d} 100,${floor}" fill="${color}" opacity=".12"/>`
    + (base === null ? "" : `<line x1="0" x2="100" y1="${floor}" y2="${floor}" stroke="currentColor" opacity=".35" stroke-width="1" vector-effect="non-scaling-stroke"/>`)
    + `<polyline points="${d}" fill="none" stroke="${color}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
};
// thin a line to about n points: a tile's sparkline is 200px wide, not 2000
const thin = (pts, n = 120) => { const step = Math.max(1, Math.ceil(pts.length / n)); return pts.filter((_, i) => i % step === 0 || i === pts.length - 1); };
// bots: server sends tiles ranked by account value. box: the map on screen, or a fresh copy about to replace it
let lastUsd = {};
function dressTreemap(box = document.getElementById("treemap")) {
  const tiles = [...box.querySelectorAll(".tile")];
  const nav = (t) => Number(t.dataset.nav), ret = (t) => Number(t.dataset.ret) || 0;
  layoutTiles(box, tiles.map(nav), tiles.map(ret), 0, document.getElementById("treemap"));
  tiles.forEach((t) => {
    const pts = lastUsd[t.dataset.arm] || [], start = nav(t) / (1 + ret(t) / 100);
    const xs = tradingX(pts.map((p) => p[0]));  // like the big chart: on a clock axis the nights would be most of the line
    t.querySelector(".spark").innerHTML = sparkSvg(thin(pts.map(([, v], i) => [xs[i], v])), ret(t) >= 0, start);
  });
}

// Stocks: size = share of dollar volume over the span, color = price change (queries.market).
const CROWN = `<svg class="crown" viewBox="0 0 24 24"><path d="M3 18h18l-1.6-11-4.6 4.4L12 4l-2.8 7.4L4.6 7z" fill="currentColor"/></svg>`;
let marketSessions = 1, lastMarket = [];
function layoutStocks(box = document.getElementById("stockmap")) {
  // fixed scale (3% a day, widening like volatility with sqrt of span), so one outlier does not wash out the rest
  layoutTiles(box, lastMarket.map((m) => m.dollar_volume), lastMarket.map((m) => m.change_pct),
              3 * Math.sqrt(marketSessions), document.getElementById("stockmap"));
}
const spanLabel = () => document.querySelector("button[data-sessions].on").textContent;  // label lives in index.html only
function drawMovers() {
  const by = [...lastMarket].sort((a, b) => b.change_pct - a.change_pct);
  const li = (s) => `<li><button class="stocklink" data-symbol="${esc(s.symbol)}">${esc(s.symbol)}</button><span class="${cls(s.change_pct)}">${signed(s.change_pct)}%</span></li>`;
  setHtml(document.getElementById("moversup"), by.filter((s) => s.change_pct > 0).slice(0, 3).map(li).join("") || `<li class="muted">none</li>`);
  setHtml(document.getElementById("moversdown"), by.filter((s) => s.change_pct < 0).slice(-3).reverse().map(li).join("") || `<li class="muted">none</li>`);
  document.querySelectorAll(".moverspan").forEach((el) => { el.textContent = `(${spanLabel()})`; });
}
async function loadMarket() {
  const span = marketSessions;
  const m = await get(`/api/market?sessions=${span}`);
  if (span !== marketSessions) return;  // span clicked while in flight
  lastMarket = m;
  const box = document.getElementById("stockmap"), fresh = box.cloneNode(false), picked = pickedStock();
  fresh.innerHTML = m.length ? m.map((s, i) => `<button class="tile${i < 3 ? ` r${i + 1}` : ""}${s.symbol === picked ? " picked" : ""}" data-symbol="${esc(s.symbol)}"`
      + ` data-tip="${esc(s.symbol)} ${signed(s.change_pct)}% over ${spanLabel()}\n$${fmt(s.price)} a share · ${fmt(s.share_pct, 1)}% of the dollar volume\nClick for its price chart">
      <div class="tile-head">${i < 3 ? CROWN : ""}<b>${esc(s.symbol)}</b></div>
      <div class="big"><span class="${cls(s.change_pct)}">${tri(s.change_pct)}${fmt(Math.abs(s.change_pct))}%</span></div>
      <div class="sub">$${fmt(s.price)} · ${fmt(s.share_pct, 1)}% of volume</div>
      <div class="spark">${sparkSvg(s.spark.map((v, j) => [j, v]), s.change_pct >= 0)}</div></button>`).join("")
    : `<p class="empty">${marketOpen ? "No live prices from the feed yet." : "No daily price history yet (daemon syncs it)."}</p>`;
  layoutStocks(fresh);  // before the swap: equal tiles then stay, with their hover and focus
  patchChildren(box, fresh);
  drawMovers();
}
// stock tile click: candle chart below the map; its timeframe tracks the span (1D: minutes, 7D: hours, 30D: days)
const SPAN_TF = { 1: "1Min", 5: "1Hour", 21: "1Day" };
const pickedStock = () => (document.getElementById("stockchart").hidden ? null : document.getElementById("symbol").value);
const markPicked = () => {
  const picked = pickedStock();
  document.querySelectorAll("#stockmap .tile").forEach((t) => t.classList.toggle("picked", t.dataset.symbol === picked));
};
const loadSpanCandles = () => { document.getElementById("tf").value = SPAN_TF[marketSessions]; loadCandles(); };
function showStockChart(symbol) {
  const pane = document.getElementById("stockchart");
  document.getElementById("symbol").value = symbol;
  pane.hidden = false;
  markPicked();
  pane.scrollIntoView({ block: "nearest" });
  loadSpanCandles();
}

// Top gainer: change in account value over the last 1 / 7 / 30 days, $ or %.
let gainMode = "usd";
const GAIN_DAYS = [1, 7, 30];
function drawGainers() {
  const tiles = [...document.querySelectorAll("#treemap .tile")];
  const rows = tiles.map((t) => {
    const arm = t.dataset.arm, nav = Number(t.dataset.nav), start = nav / (1 + (Number(t.dataset.ret) || 0) / 100);
    const pts = lastUsd[arm] || [];
    const endTs = pts.length ? pts[pts.length - 1][0] : 0;
    // value at the cutoff: last snapshot at or before it, else start cash (bot younger than the window)
    const then = (days) => pts.findLast(([ts]) => ts <= endTs - days * 864e5)?.[1] ?? start;
    const delta = (days) => (gainMode === "usd" ? nav - then(days) : (nav / then(days) - 1) * 100);
    return { arm, now: gainMode === "usd" ? nav : (nav / start - 1) * 100, d: GAIN_DAYS.map(delta) };
  }).sort((a, b) => b.d[0] - a.d[0]);
  const num = (v) => (gainMode === "usd" ? dollars(v) : signed(v, 2) + "%");
  const arrow = (v) => `<span class="${cls(v)}">${tri(v)}${gainMode === "usd" ? "$" + Math.abs(v).toFixed(0) : Math.abs(v).toFixed(2) + "%"}</span>`;
  setHtml(document.getElementById("gainers"), rows.length
    ? `<thead><tr><th class="l">bot</th><th${tipAttr(gainMode === "usd" ? window.TIPS.nav : window.TIPS.return_pct)}>now</th>${GAIN_DAYS.map((d) =>
        `<th data-tip="Change in account value over the last ${d} day${d > 1 ? "s" : ""}">Δ${d}D</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr><td class="l"><span class="swatch" style="background:${colorOf(r.arm)}"></span><a href="/arm/${encodeURIComponent(r.arm)}">${esc(nameOf(r.arm))}</a></td>` +
        `<td>${num(r.now)}</td>${r.d.map((v) => `<td>${arrow(v)}</td>`).join("")}</tr>`).join("") + `</tbody>`
    : `<tr><td class="l muted">No bots yet.</td></tr>`);
}

// ---------- index page: over time ----------
let equityMode = "usd", rangeSessions = 0;  // 0 = all
let lastPct = null, lastDd = null;  // with lastUsd: the latest poll, so $ / % and the range redraw at once instead of re-fetching
function drawOverTime() {
  const eq = equityMode === "usd" ? lastUsd : lastPct, dd = lastDd;
  if (!eq || !dd) return;
  const grid = sessionGrid(eq, rangeSessions);
  const pct = (v) => signed(v, 2) + "%";
  const yFmt = equityMode === "usd" ? dollars : pct;
  const lines = sessionDatasets(grid, eq).map(styleSeries);
  document.getElementById("timecharts").hidden = !lines.length;
  document.getElementById("timeempty").hidden = lines.length > 0;
  if (!lines.length) return;
  draw("equity", "line", { datasets: lines }, sessionOptions(grid, yFmt));
  draw("drawdown", "line", { datasets: sessionDatasets(grid, dd).map(styleSeries) }, sessionOptions(grid, (v) => fmt(v, 1) + "%"));
  // the legend is the ranking too: latest value beside each name, best first
  const ranked = lines.map((ds) => [ds.key, lastValue(ds)]).sort((a, b) => (b[1] ?? -Infinity) - (a[1] ?? -Infinity));
  drawLegend("serieslegend", ranked.map(([s, v]) => legendItem(s, v === null ? "–" : yFmt(v))),
             { head: ["bot", equityMode === "usd" ? "value now" : "return"], live: true });
}
// colour for a signed value: polarity by hue, strength by saturation, nothing at zero (same poles as the treemaps)
// cap: the value that gets full colour
function shader(cap) {
  const [up, down, mid] = ["--tile-up", "--tile-down", "--div-mid"].map(css);
  return (v) => `color-mix(in oklab, ${v >= 0 ? up : down} ${Math.round(Math.min(1, Math.abs(v) / cap) * 85)}%, ${mid})`;
}
// Daily P&L: a square per bot per trading day. Eight bots over a month of days as bars is two hundred hairlines
function drawDaily(daily) {
  const el = document.getElementById("daily");
  const series = window.SERIES.filter((s) => daily[s]?.length);
  const days = [...new Set(series.flatMap((s) => daily[s].map(([d]) => d)))].sort();
  if (!days.length) { setHtml(el, `<tr><td class="l muted">Shows after the first full trading day.</td></tr>`); return; }
  // 90th percentile gets full colour: one wild day must not wash out all the others
  const shade = shader(Math.max(1, quantile(series.flatMap((s) => daily[s].map(([, v]) => Math.abs(v))), 0.9)));
  const at = (d) => new Date(d + "T12:00:00Z");
  const month = (d) => at(d).toLocaleDateString("en-US", { timeZone: "UTC", month: "short" });
  const long = (d) => at(d).toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
  const months = [];  // [label, span] runs
  for (const d of days) if (months.at(-1)?.[0] === month(d)) months.at(-1)[1]++; else months.push([month(d), 1]);
  // name + total columns 274 px; "-123" (28 px at 12 px type) needs a 32 px square
  el.classList.toggle("dense", el.parentElement.clientWidth < 274 + days.length * 32);
  setHtml(el, `<colgroup><col class="namecol">${days.map(() => "<col>").join("")}<col class="sumcol"></colgroup>`
    + `<thead><tr><th class="l"></th>${months.map(([m, n]) => `<th class="mon" colspan="${n}">${m}</th>`).join("")}<th class="sum"></th></tr>`
    + `<tr><th class="l">bot</th>${days.map((d) => `<th data-tip="${long(d)}">${+d.slice(8)}</th>`).join("")}<th class="sum" data-tip="All these days added up">total</th></tr></thead><tbody>`
    + series.map((s) => {
      const m = Object.fromEntries(daily[s]), sum = daily[s].reduce((a, [, v]) => a + v, 0);
      return `<tr><td class="l"><span class="swatch" style="background:${colorOf(s)}"></span>${esc(nameOf(s))}</td>` + days.map((d) => (m[d] === undefined ? `<td></td>`
        : `<td class="c" style="background:${shade(m[d])}" data-tip="${esc(nameOf(s))} · ${long(d)}\n${money(m[d], 2)}">${Math.abs(m[d]) < 0.5 ? "" : signed(m[d], 0)}</td>`)).join("")
        + `<td class="sum"><span class="${cls(sum)}">${money(sum)}</span></td></tr>`;
    }).join("") + `</tbody>`);
}
// Realized P&L by stock: a row per stock (many, and the universe keeps growing), a column per bot that closed a trade (few).
// Best stock first, in two halves side by side: bots as rows needed a sideways scroll past the tenth stock
function drawHeatmap(heat) {
  const traded = window.SERIES.filter((s) => heat[s] && Object.keys(heat[s]).length);
  const idle = window.SERIES.filter((s) => heat[s] && !traded.includes(s) && !isBench(s));
  const total = {};  // stock -> every bot's result on it
  for (const s of traded) for (const [sym, v] of Object.entries(heat[s])) total[sym] = (total[sym] || 0) + v;
  const symbols = Object.keys(total).sort((a, b) => total[b] - total[a]);
  const shade = shader(Math.max(1, quantile(traded.flatMap((s) => Object.values(heat[s]).map(Math.abs)), 0.95)));
  const head = `<colgroup><col class="namecol"><col class="sumcol"></colgroup><thead><tr><th class="l">stock</th><th class="sum" data-tip="Every bot's closed trades on this stock, added up">all bots</th>`
    + `${traded.map((s) => `<th><span class="swatch" style="background:${colorOf(s)}"></span><br>${esc(nameOf(s))}</th>`).join("")}</tr></thead>`;
  const row = (sym) => `<tr><th class="l" scope="row">${inUniverse(sym) ? `<button class="stocklink" data-symbol="${esc(sym)}">${esc(sym)}</button>` : esc(sym)}</th>`
    + `<td class="sum"><span class="${cls(total[sym])}">${money(total[sym])}</span></td>${traded.map((s) => {
      const v = heat[s][sym];
      return v === undefined ? `<td class="muted">·</td>`
        : `<td class="c" style="background:${shade(v)}" data-tip="${esc(nameOf(s))} on ${esc(sym)}\n${money(v, 2)} from closed trades">${signed(v, 0)}</td>`;
    }).join("")}</tr>`;
  const tableOf = (rows) => `<table class="heat stocks">${head}<tbody>${rows.map(row).join("")}</tbody></table>`;
  const half = symbols.length > 8 ? Math.ceil(symbols.length / 2) : symbols.length;
  setHtml(document.getElementById("heatmap"), !symbols.length ? `<p class="empty">No closed trades yet.</p>`
    : tableOf(symbols.slice(0, half)) + (half < symbols.length ? tableOf(symbols.slice(half)) : ""));
  document.getElementById("heatnote").textContent = symbols.length && idle.length ? `No closed trades yet: ${idle.map(nameOf).join(", ")}.` : "";
}

// All stats: click a column name to sort by it; again to flip; a third time for the server's order (best account first)
let statSort = null;  // { col, dir }: dir 1 = biggest first (names: A first)
function dressStats(table) {
  const heads = [...table.querySelectorAll("thead tr:last-child th")], body = table.tBodies[0];
  heads.forEach((th, i) => th.setAttribute("aria-sort", statSort?.col !== i ? "none" : (statSort.dir > 0) === (i > 0) ? "descending" : "ascending"));
  [...body.rows].forEach((tr, i) => { tr.dataset.i ??= i; });  // server order, to return to
  const val = (tr) => tr.cells[statSort.col].dataset.v;
  body.append(...[...body.rows].sort((a, b) => {
    if (!statSort) return a.dataset.i - b.dataset.i;
    const [x, y] = [val(a), val(b)];
    if (x === "" || y === "") return (x === "") - (y === "");  // no value yet: last, either direction
    return statSort.dir * (statSort.col ? y - x : x.localeCompare(y));
  }));
}
function initStatSort() {
  const table = document.getElementById("leaderboard");
  dressStats(table);
  table.tHead.addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    const col = [...th.parentElement.children].indexOf(th);
    statSort = statSort?.col !== col ? { col, dir: 1 } : statSort.dir > 0 ? { col, dir: -1 } : null;
    dressStats(table);
  });
  table.tHead.addEventListener("keydown", (e) => {  // column names take keyboard focus (and show their help there)
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("th.sortable")) { e.preventDefault(); e.target.click(); }
  });
}

// each piece draws when its own data lands: the server answers one request at a time, the slowest must not hold the rest
async function loadIndex() {
  const rendered = refreshRendered(["#headline", "#treemap", "#benchnote", "#boards", "#leaderboard"],
                                   { "#treemap": dressTreemap, "#leaderboard": dressStats }).then(drawGainers);
  const lines = Promise.all([
    get("/api/equity?mode=usd").then((d) => { lastUsd = d; dressTreemap(); drawGainers(); }),
    equityMode === "pct" ? get("/api/equity?mode=pct").then((d) => { lastPct = d; }) : null,
    get("/api/drawdown").then((d) => { lastDd = d; })]).then(drawOverTime);
  await Promise.all([rendered, lines, loadMarket(), get("/api/daily_pnl").then(drawDaily), get("/api/heatmap").then(drawHeatmap)]);
}
async function setEquityMode(mode) {
  equityMode = mode;
  if (mode === "pct" && !lastPct) lastPct = await get("/api/equity?mode=pct");  // polls keep it fresh from here on
  drawOverTime();
}
function initIndex() {
  equityMode = segmented("mode", setEquityMode, true) || equityMode;
  rangeSessions = Number(segmented("range", (v) => { rangeSessions = Number(v); drawOverTime(); }, true) ?? rangeSessions);
  gainMode = segmented("gain", (v) => { gainMode = v; drawGainers(); }, true) || gainMode;
  segmented("sessions", (v) => { marketSessions = Number(v); loadMarket(); if (pickedStock()) loadSpanCandles(); });
  initSeriesLegend();
  initStatSort();
  new ResizeObserver(() => dressTreemap()).observe(document.getElementById("treemap"));
  new ResizeObserver(() => layoutStocks()).observe(document.getElementById("stockmap"));
  // delegated: polling re-renders the tiles
  document.getElementById("stockmap").addEventListener("click", (e) => {
    const symbol = e.target.closest(".tile")?.dataset.symbol;
    if (symbol) showStockChart(symbol);
  });
  // a stock's name anywhere on the page opens its price chart in the market panel
  for (const box of [document.querySelector(".movers"), document.getElementById("heatmap")]) box.addEventListener("click", (e) => {
    const symbol = e.target.closest(".stocklink")?.dataset.symbol;
    if (symbol) showStockChart(symbol);
  });
  document.getElementById("stockclose").onclick = () => { document.getElementById("stockchart").hidden = true; markPicked(); };
  document.getElementById("symbol").onchange = () => { markPicked(); loadCandles(); };
  document.getElementById("tf").onchange = loadCandles;
  initSubnav();
  return loadIndex;
}
// sidebar's section links: the one being read lights up
function initSubnav() {
  const links = [...document.querySelectorAll(".subnav a")];
  const targets = links.map((a) => document.querySelector(a.getAttribute("href")));
  let queued = false;
  const mark = () => {
    queued = false;
    const here = targets.findLastIndex((t) => t && t.getBoundingClientRect().top < innerHeight * 0.35);
    links.forEach((a, i) => a.classList.toggle("here", i === Math.max(0, here)));
  };
  addEventListener("scroll", () => { if (!queued) { queued = true; requestAnimationFrame(mark); } }, { passive: true });
  mark();
}
