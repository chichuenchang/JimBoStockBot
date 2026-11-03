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
const etTime = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York",
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const get = (url) => fetch(url).then((r) => r.json());

const paintSwatches = (root) => root.querySelectorAll(".swatch[data-series]").forEach((el) => {
  el.style.background = colorOf(el.dataset.series); });
paintSwatches(document);

// segmented buttons (data-<key>="value"): a click marks that one on and hands its value to pick
function segmented(key, pick) {
  const bs = document.querySelectorAll(`button[data-${key}]`);
  bs.forEach((b) => b.onclick = () => { bs.forEach((x) => x.classList.toggle("on", x === b)); pick(b.dataset[key]); });
}

// light / dark: base.html applies the saved choice before paint; charts read colors once, so reload
document.getElementById("theme").onclick = () => {
  const next = css("color-scheme") === "dark" ? "light" : "dark";
  try { localStorage.setItem("theme", next); } catch (e) { /* private window: this page only */ }
  document.documentElement.dataset.theme = next;
  location.reload();
};

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
function setTable(el, html) {
  const fresh = el.cloneNode(false);  // parsing needs table context
  fresh.innerHTML = html;
  patchChildren(el, fresh);
}

// server-rendered stats (tiles, stats tables, KPIs): re-fetch this page and swap them in, so they keep pace with the charts
async function refreshRendered(selectors) {
  const doc = new DOMParser().parseFromString(await fetch(location.pathname).then((r) => r.text()), "text/html");
  for (const s of selectors) {
    const el = document.querySelector(s), fresh = doc.querySelector(s);
    if (!el || !fresh) continue;
    paintSwatches(fresh);  // before comparing: a painted swatch must equal the one on screen
    patchChildren(el, fresh);
  }
}

// ---------- status strip ----------
let marketOpen = false;
async function loadStatus() {
  const st = await get("/api/status");
  marketOpen = st.market_open;
  const age = st.heartbeat ? (Date.now() - new Date(st.heartbeat)) / 1000 : Infinity;
  const up = age < 120;
  const chips = [
    `<span class="chip"><span class="dot" style="background:${up ? css("--good") : css("--critical")}"></span>daemon <b>${up ? "running" : "down"}</b></span>`,
    `<span class="chip">market <b>${st.market_open ? "open" : "closed"}</b></span>`,
    `<span class="chip"><span class="dot" style="background:${st.data_ok ? css("--good") : css("--critical")}"></span>price feed <b>${st.data_ok ? "ok" : "error"}</b></span>`,
  ];
  // dot shows the worst state of any model cap or the global cap; text names the models over
  const t = st.budget._total;
  const rank = { ok: 0, warn: 1, exhausted: 2 };
  const hot = Object.entries(st.budget).filter(([, b]) => b.state !== "ok");
  const worst = hot.reduce((w, [, b]) => (rank[b.state] > rank[w] ? b.state : w), "ok");
  const why = hot.map(([name, b]) => `${name === "_total" ? "total" : esc(name)} ${b.state}`).join(", ");
  chips.push(`<span class="chip"><span class="dot" style="background:${stateColor(worst)}"></span>LLM budget <b>$${fmt(t.spent)} / $${fmt(t.cap, 0)}</b>${why ? " " + why : ""}</span>`);
  // flat-plan models (llm/quota.py): quota used per window, tokens this month
  for (const [name, q] of Object.entries(st.quota)) {
    const used = q.windows.map((w) => `${esc(w.window)} ${fmt(w.used_ratio * 100, 0)}%`).join(" · ");
    const tk = st.budget[name]?.tokens;
    const tokens = tk ? ` · ${tk.calls} calls, ${kTok(tk.input)} in / ${kTok(tk.output)} out this month` : "";
    chips.push(`<span class="chip" title="polled ${etTime(q.windows[0].ts)} ET"><span class="dot" style="background:${stateColor(q.state)}"></span>${esc(name)} quota <b>${used}</b>${tokens}</span>`);
  }
  const iv = st.interval;
  chips.push(`<span class="chip">decide every <input id="intervalin" type="number" min="1" max="60" value="${iv.minutes}" style="width:3.5em"> min`
    + ` <button id="intervalset">Set</button>${iv.source === "override" ? ' <button id="intervalreset" title="back to settings.yaml">Reset</button>' : ""}</span>`);
  chips.push(`<button id="pauseall">${st.paused_all ? "Resume all" : "Pause all"}</button>`);
  if (document.activeElement?.id === "intervalin") return st;  // re-render would wipe what is being typed
  document.getElementById("status").innerHTML = chips.join("");
  document.getElementById("pauseall").onclick = async () => {
    await fetch(st.paused_all ? "/api/resume_all" : "/api/pause_all", { method: "POST" });
    loadStatus();
  };
  const postInterval = async (minutes) => {
    const r = await fetch("/api/interval", { method: "POST", headers: { "Content-Type": "application/json" },
                                             body: JSON.stringify({ minutes }) });
    if (!r.ok) document.getElementById("intervalin").style.borderColor = css("--critical");
    else loadStatus();
  };
  document.getElementById("intervalset").onclick = () => postInterval(Number(document.getElementById("intervalin").value));
  const reset = document.getElementById("intervalreset");
  if (reset) reset.onclick = () => postInterval(null);
  return st;
}

// ---------- chart helpers ----------
function baseOptions(yLabel, yFmt) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "top", align: "start", labels: { color: css("--text-secondary"), boxWidth: 14, boxHeight: 3 } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${yFmt(c.parsed.y)}` } },
    },
    scales: {
      x: { grid: { color: css("--grid") }, ticks: { color: css("--text-muted"), maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
      y: { grid: { color: css("--grid") }, ticks: { color: css("--text-muted"), callback: yFmt },
           title: { display: !!yLabel, text: yLabel, color: css("--text-muted") } },
    },
  };
}
function lineDatasets(data) {
  return window.SERIES.filter((s) => data[s] && data[s].length).map((s) => ({
    label: nameOf(s), data: data[s].map(([x, y]) => ({ x, y })), borderColor: colorOf(s), backgroundColor: colorOf(s),
    borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, borderDash: isBench(s) ? [6, 4] : [], tension: 0,
  }));
}
function timeLinear(opts) {
  opts.scales.x.type = "linear";
  opts.scales.x.ticks.callback = (v) => new Date(v).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  opts.plugins.tooltip.callbacks.title = (items) => items.length ? etTime(items[0].parsed.x) : "";
  return opts;
}
const charts = {};
function draw(id, type, data, opts) {
  if (charts[id]?.config.type === type) { charts[id].data = data; charts[id].options = opts; charts[id].update(); return; }
  charts[id]?.destroy();  // type changed (usage: line today, bars over days): update cannot switch it
  charts[id] = new Chart(document.getElementById(id), { type, data, options: opts });
}
const barStyle = (s) => ({ backgroundColor: colorOf(s), borderColor: css("--surface-1"), borderWidth: 1, borderRadius: 4,
                           borderSkipped: "start" });

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
function layoutTiles(box, sizes, rets, cap) {
  const tiles = [...box.querySelectorAll(".tile")];
  if (!tiles.length) return;
  const W = box.clientWidth, H = box.clientHeight;
  sizes = sizes.map((v) => Math.max(1e-9, v));
  const total = sizes.reduce((a, b) => a + b, 0);
  const maxAbs = cap || Math.max(1e-9, ...rets.map(Math.abs));
  const [up, down, base] = ["--tile-up", "--tile-down", "--surface-0"].map(css);  // once per layout, not per tile
  squarify(sizes.map((v) => (v / total) * W * H), W, H).forEach((r, i) => {
    const t = tiles[i], gap = 2;  // px each side, so neighbours sit 4px apart
    const tint = rets[i] ? Math.round(18 + 52 * Math.min(1, Math.abs(rets[i]) / maxAbs)) : 0;  // flat bot: no tint
    Object.assign(t.style, { left: `${r.x + gap}px`, top: `${r.y + gap}px`, width: `${r.w - 2 * gap}px`, height: `${r.h - 2 * gap}px`,
      background: `color-mix(in oklab, ${rets[i] >= 0 ? up : down} ${tint}%, ${base})` });
    t.classList.toggle("sm", r.w < 150 || r.h < 110);
    t.classList.toggle("xs", r.w < 70 || r.h < 50);
  });
}
function layoutTreemap() {  // bots: server sends tiles ranked by account value
  const tiles = [...document.querySelectorAll("#treemap .tile")];
  layoutTiles(document.getElementById("treemap"), tiles.map((t) => Number(t.dataset.nav)),
              tiles.map((t) => Number(t.dataset.ret) || 0));
}

// Stocks: size = share of dollar volume over the span, color = price change (queries.market).
const CROWN = `<svg class="crown" viewBox="0 0 24 24"><path d="M3 18h18l-1.6-11-4.6 4.4L12 4l-2.8 7.4L4.6 7z" fill="currentColor"/></svg>`;
let marketSessions = 1, lastMarket = [];
function layoutStocks() {
  // fixed scale (3% a day, widening like volatility with sqrt of span), so one outlier does not wash out the rest
  layoutTiles(document.getElementById("stockmap"), lastMarket.map((m) => m.dollar_volume), lastMarket.map((m) => m.change_pct),
              3 * Math.sqrt(marketSessions));
}
function drawMovers() {
  const by = [...lastMarket].sort((a, b) => b.change_pct - a.change_pct);
  const li = (s) => `<li><span>${esc(s.symbol)}</span><span class="${cls(s.change_pct)}">${signed(s.change_pct)}%</span></li>`;
  document.getElementById("moversup").innerHTML = by.filter((s) => s.change_pct > 0).slice(0, 3).map(li).join("") || `<li class="muted">none</li>`;
  document.getElementById("moversdown").innerHTML = by.filter((s) => s.change_pct < 0).slice(-3).reverse().map(li).join("") || `<li class="muted">none</li>`;
  const span = document.querySelector("button[data-sessions].on").textContent;  // label lives in index.html only
  document.querySelectorAll(".moverspan").forEach((el) => { el.textContent = `(${span})`; });
}
async function loadMarket() {
  const span = marketSessions;
  const m = await get(`/api/market?sessions=${span}`);
  if (span !== marketSessions) return;  // span clicked while in flight
  lastMarket = m;
  const box = document.getElementById("stockmap");
  box.innerHTML = m.length ? m.map((s, i) => `<div class="tile${i < 3 ? ` r${i + 1}` : ""}" data-symbol="${esc(s.symbol)}" title="${esc(s.symbol)}: ${signed(s.change_pct)}% · $${fmt(s.price)} · ${fmt(s.share_pct, 1)}% of dollar volume">
      <div class="tile-head"><b>${esc(s.symbol)}</b>${i < 3 ? CROWN : ""}</div>
      <div class="big"><span class="${cls(s.change_pct)}">${tri(s.change_pct)}${fmt(Math.abs(s.change_pct))}%</span></div>
      <div class="sub">$${fmt(s.price)} · ${fmt(s.share_pct, 1)}% of volume</div>
      <div class="spark">${sparkSvg(s.spark.map((v, j) => [j, v]), s.change_pct >= 0)}</div></div>`).join("")
    : `<p class="muted">${marketOpen ? "No live prices from the feed yet." : "No daily price history yet (daemon syncs it)."}</p>`;
  layoutStocks();
  markPicked();
  drawMovers();
}
// stock tile click: candle chart below the map, timeframe matching the span (1D: minutes, 7D: hours, 30D: days)
const SPAN_TF = { 1: "1Min", 5: "1Hour", 21: "1Day" };
const pickedStock = () => (document.getElementById("stockchart").hidden ? null : document.getElementById("symbol").value);
const markPicked = () => document.querySelectorAll("#stockmap .tile").forEach((t) => t.classList.toggle("picked", t.dataset.symbol === pickedStock()));
function showStockChart(symbol) {
  const pane = document.getElementById("stockchart");
  document.getElementById("symbol").value = symbol;
  document.getElementById("tf").value = SPAN_TF[marketSessions];
  pane.hidden = false;
  markPicked();
  pane.scrollIntoView({ block: "nearest" });
  loadCandles();
}
const sparkSvg = (pts, up) => {
  if (pts.length < 2) return "";
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const yOf = (py) => (y1 === y0 ? 15 : 30 - ((py - y0) / (y1 - y0)) * 28 - 1);  // flat series: mid-line
  const d = pts.map(([px, py]) => `${(((px - x0) / (x1 - x0 || 1)) * 100).toFixed(2)},${yOf(py).toFixed(2)}`).join(" ");
  return `<svg viewBox="0 0 100 30" preserveAspectRatio="none"><polyline points="${d}" fill="none" stroke="${css(up ? "--good" : "--critical")}" stroke-width="1.5" vector-effect="non-scaling-stroke"/></svg>`;
};

// Top gainer: change in account value over the last 1 / 7 / 30 days, $ or %.
let gainMode = "usd", lastUsd = {};
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
  setTable(document.getElementById("gainers"), rows.length
    ? `<thead><tr><th class="l">bot</th><th title="${gainMode === "usd" ? esc(window.TIPS.nav) : esc(window.TIPS.return_pct)}">now</th>${GAIN_DAYS.map((d) =>
        `<th title="Change in account value over the last ${d} day${d > 1 ? "s" : ""}">Δ${d}D</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr><td class="l"><span class="swatch" style="background:${colorOf(r.arm)}"></span><a href="/arm/${encodeURIComponent(r.arm)}" style="color:inherit;text-decoration:none">${esc(nameOf(r.arm))}</a></td>` +
        `<td>${num(r.now)}</td>${r.d.map((v) => `<td>${arrow(v)}</td>`).join("")}</tr>`).join("") + `</tbody>`
    : `<tr><td class="l muted">No bots yet.</td></tr>`);
}

let equityMode = "usd";
async function loadIndex() {
  const [usd, pctEq, dd, daily, heat] = await Promise.all([
    get("/api/equity?mode=usd"), equityMode === "pct" ? get("/api/equity?mode=pct") : null,
    get("/api/drawdown"), get("/api/daily_pnl"), get("/api/heatmap"),
    refreshRendered(["#treemap", "#benchnote", "#boards", "#leaderboard"]), loadMarket()]);
  lastUsd = usd;
  layoutTreemap();
  document.querySelectorAll("#treemap .tile").forEach((t) => {
    t.querySelector(".spark").innerHTML = sparkSvg(usd[t.dataset.arm] || [], Number(t.dataset.ret) >= 0);
  });
  drawGainers();
  const eq = pctEq || usd;
  const pct = (v) => signed(v, 2) + "%";
  draw("equity", "line", { datasets: lineDatasets(eq) }, timeLinear(baseOptions("", equityMode === "usd" ? dollars : pct)));
  // the small charts share the account-value legend above them
  const noLegend = (o) => { o.plugins.legend.display = false; return o; };
  draw("drawdown", "line", { datasets: lineDatasets(dd) }, noLegend(timeLinear(baseOptions("", (v) => fmt(v, 1) + "%"))));

  const days = [...new Set(Object.values(daily).flat().map(([d]) => d))].sort();
  const ds = window.SERIES.filter((s) => daily[s]).map((s) => {
    const m = Object.fromEntries(daily[s]);
    return { label: nameOf(s), data: days.map((d) => m[d] ?? null), ...barStyle(s) };
  });
  draw("daily", "bar", { labels: days, datasets: ds }, noLegend(baseOptions("", (v) => signed(v, 0))));

  const symbols = [...new Set(Object.values(heat).flatMap((r) => Object.keys(r)))].sort();
  const maxAbs = Math.max(1, ...Object.values(heat).flatMap((r) => Object.values(r).map(Math.abs)));
  const shade = (v) => {
    const t = Math.min(1, Math.abs(v) / maxAbs);
    const pole = v >= 0 ? css("--tile-up") : css("--tile-down");  // same up/down colors as the treemaps
    return `color-mix(in oklab, ${pole} ${Math.round(t * 85)}%, ${css("--div-mid")})`;
  };
  const rows = window.SERIES.filter((s) => heat[s]).map((s) =>
    `<tr><td class="l">${esc(nameOf(s))}</td>${symbols.map((sym) => {
      const v = heat[s][sym];
      return v === undefined ? `<td class="muted">–</td>`
        : `<td style="background:${shade(v)}" title="${esc(nameOf(s))} ${sym}: ${signed(v)}">${signed(v, 0)}</td>`;
    }).join("")}</tr>`);
  setTable(document.getElementById("heatmap"), symbols.length
    ? `<thead><tr><th class="l">bot</th>${symbols.map((s) => `<th>${s}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody>`
    : `<tr><td class="l muted">No closed trades yet.</td></tr>`);
}
function initIndex() {
  segmented("mode", (v) => { equityMode = v; loadIndex(); });
  segmented("gain", (v) => { gainMode = v; drawGainers(); });
  segmented("sessions", (v) => { marketSessions = Number(v); loadMarket(); });
  new ResizeObserver(layoutTreemap).observe(document.getElementById("treemap"));
  new ResizeObserver(layoutStocks).observe(document.getElementById("stockmap"));
  // delegated: polling re-renders the tiles
  document.getElementById("stockmap").addEventListener("click", (e) => {
    const symbol = e.target.closest(".tile")?.dataset.symbol;
    if (symbol) showStockChart(symbol);
  });
  document.getElementById("stockclose").onclick = () => { document.getElementById("stockchart").hidden = true; markPicked(); };
  document.getElementById("symbol").onchange = () => { markPicked(); loadCandles(); };
  document.getElementById("tf").onchange = loadCandles;
  return loadIndex;
}

// ---------- arm page ----------
let candleChart, candleSeries, candleSeq = 0;
const TRADE_VIEW_BARS = 40;  // at least this many bars each side of a closed trade's middle
const DAY_VIEW_BARS = 250;  // about a trading year
const BUY_TFS = ["1Min", "1Hour", "1Day"];  // finest first: intraday windows are short (app.py INTRADAY_SESSIONS)
let focused = null;  // closed trade picked in the table: { symbol, from, to }. Its badges stay solid, the rest fade
const isoSeconds = (iso) => Math.floor(new Date(iso).getTime() / 1000);  // floor: as queries.chart_fills
const isFocused = (symbol, buyIso) => focused?.symbol === symbol && focused.from === isoSeconds(buyIso);
// stock name that points the chart at it; plain text once the symbol left the universe (/api/candles 404s)
const stockLink = (symbol, buyIso = "", sellIso = "") => [...document.getElementById("symbol").options].some((o) => o.value === symbol)
  ? `<button class="stocklink${isFocused(symbol, buyIso) ? " on" : ""}" data-symbol="${esc(symbol)}" data-buy="${esc(buyIso)}" data-sell="${esc(sellIso)}">${esc(symbol)}</button>` : esc(symbol);
function focusTrade(trade) {
  focused = trade;
  document.querySelectorAll("#trades .stocklink").forEach((l) => l.classList.toggle("on", isFocused(l.dataset.symbol, l.dataset.buy)));
  repaintBadges?.();
}
function showStock(symbol, buyIso, sellIso) {
  document.getElementById("symbol").value = symbol;
  document.getElementById("candles").closest(".card").scrollIntoView({ block: "nearest" });
  focusTrade(buyIso ? { symbol, from: isoSeconds(buyIso), to: isoSeconds(sellIso) } : null);
  if (focused) loadCandlesForTrade(focused);
  else loadCandles();
}
function table(id, cols, rows, empty) {
  setTable(document.getElementById(id), !rows.length ? `<tr><td class="l muted">${empty}</td></tr>` :
    `<thead><tr>${cols.map(([k, label, l]) => `<th class="${l ? "l" : ""}" title="${esc(window.TIPS[k] || "")}">${label}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr>${cols.map(([k, , l, f]) => `<td class="${l ? (l === "wrap" ? "wrap" : "l") : ""}">${f ? f(r[k], r) : esc(r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`);
}
// no ARM (overview's market chart): bars only, no fills
const fetchCandles = (tf) => get(`/api/candles?symbol=${document.getElementById("symbol").value}&tf=${tf}`
  + (window.ARM ? `&arm=${encodeURIComponent(window.ARM)}` : ""));
async function loadCandles() {
  const seq = ++candleSeq, tf = document.getElementById("tf").value;
  const res = await fetchCandles(tf);
  if (seq !== candleSeq) return;  // another load started while in flight
  drawCandles(tf, res);
  fitCandles();
}
// 1Day opens on the recent stretch: all of it would squeeze years of bars below a pixel each
async function fitCandles() {
  const seq = candleSeq;
  await chartSized();
  if (seq !== candleSeq) return;  // another load started: it fits
  const n = candleSeries.data().length;
  if (document.getElementById("tf").value === "1Day" && n > DAY_VIEW_BARS) candleChart.timeScale().setVisibleLogicalRange({ from: n - DAY_VIEW_BARS, to: n });
  else candleChart.timeScale().fitContent();
}
// closed trade: first timeframe whose bars reach back to the buy, framed so buy and sell both show
async function loadCandlesForTrade(trade) {
  const seq = ++candleSeq;
  let tf, res;
  for (tf of BUY_TFS) {
    res = await fetchCandles(tf);
    if (seq !== candleSeq) return;  // another load started while in flight
    if (res.bars[0]?.time <= trade.from) break;
  }
  document.getElementById("tf").value = tf;
  drawCandles(tf, res);
  const buyBar = res.bars.findLastIndex((b) => b.time <= trade.from);
  if (buyBar < 0 || focused !== trade) { fitCandles(); return; }  // no bar to frame, or tab switched mid-load
  const sellBar = res.bars.findLastIndex((b) => b.time <= trade.to);
  const middle = (buyBar + sellBar) / 2, half = Math.max(TRADE_VIEW_BARS, (sellBar - buyBar) * 0.75);
  candleChart.timeScale().setVisibleLogicalRange({ from: middle - half, to: middle + half });  // no chartSized: a stock link only shows with the chart
}
// a chart just shown has no width until its next frame; a range set sooner keeps only its right edge, bars at minimum spacing
const chartSized = () => new Promise((resolve) => {
  const scale = candleChart.timeScale();
  if (scale.width() > 0) { resolve(); return; }
  const sized = () => { scale.unsubscribeSizeChange(sized); resolve(); };
  scale.subscribeSizeChange(sized);
});
// trades as solid lettered badges, one per bar and side. Not setMarkers: its labels overlap, it has no letter in a shape,
// and it draws a fill on a later bar, or on the first bar when older than the window
let fillsAtBar = new Map(), repaintBadges;  // bar time -> { bar, fills }
const BADGE_R = parseFloat(css("--badge-size")) / 2, BADGE_GAP = 6, BADGE_FADED = 0.25;  // px; gap from bar's high or low; opacity
const BAR_SECONDS = { "1Min": 60, "1Hour": 3600, "1Day": 86400, "1Week": 7 * 86400, "1Month": 31 * 86400 };  // week, month: upper bound
const BADGE_FONT = `bold ${css("--badge-font")} sans-serif`, BADGE_HOLLOW = css("--surface-1");
const BADGES = { buy: { letter: "B", color: css("--buy"), ink: css("--on-buy"), anchor: "low", dir: 1 },
  sell: { letter: "S", color: css("--sell"), ink: css("--on-sell"), anchor: "high", dir: -1 } };
const fillText = (f) => `${f.side} ${fmt(f.price)}${f.reason !== "entry" && f.reason !== "close" ? " " + f.reason : ""}${f.replayed ? " (replayed)" : ""}`;
function drawBadge(ctx, x, y, badge, hollow) {
  ctx.beginPath();
  ctx.arc(x, y, BADGE_R, 0, 2 * Math.PI);
  ctx.fillStyle = hollow ? BADGE_HOLLOW : badge.color;
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = badge.color;
  ctx.stroke();
  ctx.fillStyle = hollow ? badge.color : badge.ink;
  ctx.font = BADGE_FONT;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(badge.letter, x, y);
}
function drawBadges({ context }) {
  for (const { bar, fills } of fillsAtBar.values()) {
    const x = candleChart.timeScale().timeToCoordinate(bar.time);
    if (x === null) continue;
    for (const [side, badge] of Object.entries(BADGES)) {
      const sideFills = fills.filter((f) => f.side === side);
      if (!sideFills.length) continue;
      const y = candleSeries.priceToCoordinate(bar[badge.anchor]) + badge.dir * (BADGE_GAP + BADGE_R);
      context.globalAlpha = !focused || sideFills.some((f) => focused.from <= f.time && f.time <= focused.to) ? 1 : BADGE_FADED;
      drawBadge(context, x, y, badge, sideFills.every((f) => f.replayed));
    }
  }
}
const fillBadges = { attached: ({ requestUpdate }) => { repaintBadges = requestUpdate; },
  paneViews: () => [{ renderer: () => ({ draw: (target) => target.useMediaCoordinateSpace(drawBadges) }) }] };
function drawCandles(tf, res) {
  const el = document.getElementById("candles");
  if (!candleChart) {
    candleChart = LightweightCharts.createChart(el, { autoSize: true,
      layout: { background: { color: css("--surface-1") }, textColor: css("--text-secondary") },
      grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
      timeScale: { minBarSpacing: 0.1 } });  // default 0.5: on a phone a long trade's buy fell off the left edge
    candleSeries = candleChart.addCandlestickSeries({ upColor: css("--good"), downColor: css("--critical"),
      wickUpColor: css("--good"), wickDownColor: css("--critical"), borderVisible: false });
    candleSeries.attachPrimitive(fillBadges);
    const info = document.getElementById("fillinfo");  // bot page only
    const showFills = (p) => { if (info) info.textContent = (fillsAtBar.get(p.time)?.fills || []).map(fillText).join(" · "); };
    candleChart.subscribeCrosshairMove(showFills);
    candleChart.subscribeClick(showFills);  // a tap moves no crosshair
  }
  // day+ bars sit at ET midnight: show dates, not 00:00
  const long = ["1Day", "1Week", "1Month"].includes(tf);
  const etDay = (s) => new Date(s * 1000).toLocaleDateString("en-US", { timeZone: "America/New_York", year: "numeric", month: "short", day: "numeric" });
  candleChart.applyOptions({ localization: { timeFormatter: (s) => long ? etDay(s) : etTime(s * 1000) },
    timeScale: { timeVisible: !long, tickMarkFormatter: (s, type) => new Date(s * 1000).toLocaleString("en-US",
      long ? { timeZone: "America/New_York", year: "2-digit", month: "short", day: tf === "1Day" ? "numeric" : undefined }
           : type < LightweightCharts.TickMarkType.Time ? { timeZone: "America/New_York", month: "short", day: "numeric" }  // a new day
           : { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) } });
  fillsAtBar = new Map();
  for (const fill of res.fills) {
    const bar = res.bars.findLast((b) => b.time <= fill.time);
    if (!bar || fill.time >= bar.time + BAR_SECONDS[tf]) continue;  // chart has no bar for it: older than the window, or feed down
    if (!fillsAtBar.has(bar.time)) fillsAtBar.set(bar.time, { bar, fills: [] });
    fillsAtBar.get(bar.time).fills.push(fill);
  }
  candleSeries.setData(res.bars);
  document.getElementById("candlenote").textContent = res.error ? `Price data unavailable: ${res.error}`
    : !res.bars.length ? "No price bars available (replay mode or no data yet)." : "";
}
async function loadArm(st) {
  const arm = encodeURIComponent(window.ARM);
  const me = st.arms.find((a) => a.arm === window.ARM) || {};
  const sized = me.variant === "improved";  // only improved arms send conviction
  const [pos, trades, decs, rej, conv] = await Promise.all([get(`/api/arm/${arm}/positions`), get(`/api/arm/${arm}/trades`),
    get(`/api/arm/${arm}/decisions`), get(`/api/arm/${arm}/rejections`), sized ? get(`/api/arm/${arm}/conviction`) : [],
    refreshRendered([".kpis"])]);
  document.getElementById("armstate").innerHTML =
    `<span class="chip">model <b>${esc(me.model)}</b></span><span class="chip">setup <b>${esc(me.variant)}</b></span>` +
    `<span class="chip">last decision <b>${me.last_decision ? etTime(me.last_decision) : "–"}</b></span>` +
    (me.paused ? `<span class="chip"><span class="dot" style="background:${css("--warning")}"></span><b>paused</b></span>` : "");
  const btn = document.getElementById("pausebtn");
  btn.textContent = me.paused ? "Resume" : "Pause";
  btn.onclick = async () => { await fetch(`/api/${me.paused ? "resume" : "pause"}/${arm}`, { method: "POST" }); loadArm(await loadStatus()); };

  const money = (v) => `<span class="${cls(v)}">${signed(v)}</span>`;
  table("positions", [["symbol", "stock", 1, (v) => stockLink(v)], ["qty", "shares", 0, (v) => fmt(v, 3)], ["entry_price", "entry", 0, (v) => fmt(v)],
    ["price", "price now", 0, (v) => fmt(v)], ["unrealized", "unrealized", 0, money], ["stop_loss", "stop", 0, (v) => fmt(v)],
    ["profit_target", "target", 0, (v) => fmt(v)], ["invalidation", "proves it wrong", "wrap"]], pos, "No open positions.");
  table("trades", [["symbol", "stock", 1, (v, r) => stockLink(v, r.entry_ts, r.exit_ts)], ["entry_ts", "bought", 1, etTime], ["exit_ts", "sold", 1, etTime],
    ["entry_price", "in", 0, (v) => fmt(v)], ["exit_price", "out", 0, (v) => fmt(v)], ["hold_hours", "held (h)", 0, (v) => fmt(v, 1)],
    ["pnl", "P&L", 0, money], ["r_multiple", "R", 0, (v) => signed(v, 2)],
    ...(sized ? [["conviction", "conviction", 0, (v) => fmt(v)]] : []),
    ["exit_reason", "exit", 1, (v, r) => esc(v) + (r.replayed ? ' <span class="chip" title="Filled while catching up after the PC was off">replayed</span>' : "")],
    ["justification", "reason", "wrap"]], trades, "No closed trades yet.");
  document.getElementById("convcard").hidden = !sized;
  if (sized) table("conviction", [["conviction", "conviction at entry", 1], ["trades", "trades"], ["win_rate", "win rate %", 0, (v) => fmt(v, 0)],
    ["avg_r", "avg R", 0, (v) => signed(v, 2)]], conv, "No closed trades yet.");
  table("rejections", [["ts", "time", 1, etTime], ["symbol", "stock", 1], ["rule", "rule broken", 1], ["detail", "detail", "wrap"]],
    rej, "Nothing rejected.");
  // decisions and rejections come capped (50 / 100), so only uncapped lists get a count
  document.getElementById("n-positions").textContent = pos.length;
  document.getElementById("n-trades").textContent = trades.length;
  document.getElementById("decisions").innerHTML = decs.length ? decs.map((d) =>
    `<details data-id="${d.id}"><summary><span class="muted">${etTime(d.slot_ts)} · ${esc(d.reason)} · ${esc(d.status)} · $${fmt(d.cost, 3)}</span> — ${esc(d.summary)}</summary><div class="detail">loading…</div></details>`).join("")
    : `<p class="muted">No decisions yet.</p>`;
  document.querySelectorAll("#decisions details").forEach((el) => el.addEventListener("toggle", async () => {
    if (!el.open || el.dataset.loaded) return;
    const d = await get(`/api/decision/${el.dataset.id}`);
    // chart-tool pictures the bot saw, re-drawn (agent/chart_tool.py seen)
    const pics = d.charts.map((c) =>
      `<img alt="${esc(c.symbol)} chart" style="max-width:100%" src="/api/chart?${esc(new URLSearchParams(c))}">`);
    el.querySelector(".detail").innerHTML = `<h2>Prompt</h2>${pics.join("")}<pre>${esc(d.prompt)}</pre><h2>Reply</h2><pre>${esc(d.raw)}</pre>`;
    el.dataset.loaded = "1";
  }));
}
// tabs on the bot page; the open tab lives in the URL hash so reload and back keep it
function showTab(name) {
  const tabs = [...document.querySelectorAll(".subtabs button")];
  if (!tabs.some((b) => b.dataset.tab === name)) name = tabs[0].dataset.tab;
  tabs.forEach((b) => { b.classList.toggle("on", b.dataset.tab === name); b.setAttribute("aria-selected", b.dataset.tab === name); });
  document.querySelectorAll(".tabpane").forEach((p) => { p.hidden = !p.dataset.pane.split(" ").includes(name); });
  focusTrade(null);  // only a click on a closed trade focuses; that stays on this tab
  if (candleChart && !document.getElementById("candles").closest(".tabpane").hidden) fitCandles();
}
function initArm() {
  document.querySelectorAll(".subtabs button").forEach((b) => b.onclick = () => { location.hash = b.dataset.tab; });
  window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
  showTab(location.hash.slice(1));
  document.getElementById("symbol").onchange = () => { focusTrade(null); loadCandles(); };
  document.getElementById("tf").onchange = loadCandles;
  // delegated: polling re-renders the rows
  ["positions", "trades"].forEach((id) => document.getElementById(id).addEventListener("click", (e) => {
    const link = e.target.closest(".stocklink");
    if (link) showStock(link.dataset.symbol, link.dataset.buy, link.dataset.sell);
  }));
  loadCandles();
  return loadArm;
}

// ---------- usage page: tokens per bot (queries.usage) ----------
let usageSpan = "today";
async function loadUsage() {
  const span = usageSpan;
  const u = await get(`/api/usage?span=${span}`);
  if (span !== usageSpan) return;  // span clicked while in flight: the click's own load draws
  const bots = u.bots.map((b) => b.bot), flat = Object.fromEntries(u.bots.map((b) => [b.bot, b.flat]));
  const cost = (t, b) => (flat[b] ? "plan" : "$" + fmt(t.cost, 3));
  document.getElementById("tokens-note").textContent = span === "today" ? "running total today" : "per ET day · hover a bar for detail";
  if (span === "today") {
    draw("tokens", "line", { datasets: bots.filter((b) => u.series[b]?.length).map((b) => ({
      label: nameOf(b), data: u.series[b].map(([x, y]) => ({ x, y })), borderColor: colorOf(b), backgroundColor: colorOf(b),
      borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, stepped: true })) }, timeLinear(baseOptions("", kTok)));
  } else {
    // one stacked bar per ET day, a segment per bot; tooltip: the hovered segment's day totals (queries._totals)
    const tok = (t) => t.input + t.output;
    // null, not 0, on a bot's idle day: no zero-height segment to hover, so tooltips always have totals
    const ds = bots.filter((b) => u.daily.some((d) => d.bots[b])).map((b) => ({
      label: nameOf(b), bot: b, data: u.daily.map((d) => d.bots[b] ? tok(d.bots[b]) : null), ...barStyle(b) }));
    const o = baseOptions("", kTok);
    o.scales.x.stacked = o.scales.y.stacked = true;
    o.interaction = { mode: "point", intersect: true };
    Object.assign(o.plugins.tooltip, { callbacks: {
      title: (items) => items.length ? new Date(u.daily[items[0].dataIndex].day + "T12:00:00Z").toLocaleDateString("en-US",
        { timeZone: "UTC", weekday: "short", year: "numeric", month: "short", day: "numeric" }) : "",
      label: (c) => { const t = u.daily[c.dataIndex].bots[c.dataset.bot];
        return [`${c.dataset.label}: ${kTok(tok(t))} tokens · ${t.calls} calls · ${cost(t, c.dataset.bot)}`,
                `  ${kTok(t.input)} in (${kTok(t.cached)} cached) / ${kTok(t.output)} out`]; } } });
    draw("tokens", "bar", { labels: u.daily.map((d) => `${+d.day.slice(5, 7)}/${+d.day.slice(8)}`), datasets: ds }, o);
  }

  const cols = ["calls", "input", "cached", "output", "cost"];
  const tip = { calls: "Model calls", input: "Prompt tokens sent", cached: "Part of input the provider served from cache (cheaper)",
                output: "Tokens the model wrote back", cost: "Dollars billed; $0 on a flat plan" };
  const cell = (t, k, b) => k === "cost" ? `<span class="${b.flat ? "muted" : ""}">${cost(t, b.bot)}</span>` : k === "calls" ? t[k] : kTok(t[k]);
  setTable(document.getElementById("usage"),
    `<thead><tr class="groups"><th class="l"></th><th class="l"></th><th colspan="5" class="l gstart">Today</th><th colspan="5" class="l gstart">This month</th></tr>` +
    `<tr><th class="l">bot</th><th class="l">model</th>${["today", "month"].map(() => cols.map((k, i) =>
      `<th title="${tip[k]}" class="${i ? "" : "gstart"}">${k}</th>`).join("")).join("")}</tr></thead><tbody>` +
    u.bots.map((b) => `<tr><td class="l"><span class="swatch" style="background:${colorOf(b.bot)}"></span>${esc(nameOf(b.bot))}</td>` +
      `<td class="l">${esc(b.model)}</td>${["today", "month"].map((p) => cols.map((k, i) =>
        `<td class="${i ? "" : "gstart"}">${cell(b[p], k, b)}</td>`).join("")).join("")}</tr>`).join("") + `</tbody>`);
}
function initUsage() {
  segmented("span", (v) => { usageSpan = v; loadUsage(); });
  return loadUsage;
}

// ---------- bot tabs: drag to reorder, saved server-side (dashboard/tab_order.py) ----------
(function initTabDrag() {
  const nav = document.querySelector("nav.tabs");
  const order = () => [...nav.querySelectorAll("a[data-arm]")].map((a) => a.dataset.arm);
  let dragged = null, before = "";
  nav.addEventListener("dragstart", (e) => {
    dragged = e.target.closest?.("a[data-arm]") || null;
    if (!dragged) return;
    before = JSON.stringify(order());
    e.dataTransfer.effectAllowed = "move";
    dragged.classList.add("dragging");
  });
  nav.addEventListener("dragover", (e) => {
    if (!dragged) return;
    e.preventDefault();
    const over = e.target.closest?.("a[data-arm]");
    if (!over || over === dragged) return;
    const r = over.getBoundingClientRect();
    // sidebar stacks bots vertically; on narrow screens it folds into a sideways row
    const first = getComputedStyle(nav).flexDirection === "column" ? e.clientY < r.top + r.height / 2
                                                                     : e.clientX < r.left + r.width / 2;
    nav.insertBefore(dragged, first ? over : over.nextSibling);
  });
  nav.addEventListener("drop", (e) => e.preventDefault());
  nav.addEventListener("dragend", async () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    dragged = null;
    const arms = order();
    if (JSON.stringify(arms) === before) return;
    const r = await fetch("/api/tab_order", { method: "POST", headers: { "Content-Type": "application/json" },
                                              body: JSON.stringify({ arms }) }).catch(() => null);
    if (!r?.ok) location.reload();  // refused (config changed?) or unreachable: show what the server holds
  });
})();

// ---------- polling: 5 s while the market is open, else 60 s ----------
(async function main() {
  const page = document.body.dataset.page;
  const refresh = page === "index" ? initIndex() : page === "arm" ? initArm() : page === "usage" ? initUsage() : async () => {};
  async function tick() {
    try { await refresh(await loadStatus()); } catch (e) { console.error(e); }
    setTimeout(tick, marketOpen ? 5000 : 60000);
  }
  tick();
})();
