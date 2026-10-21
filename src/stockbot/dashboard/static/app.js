// StockBot dashboard. Colors follow the entity: arm i -> categorical slot i (fixed order);
// the benchmark is a neutral dashed line, never a categorical slot.
"use strict";

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isBench = (s) => s.startsWith("benchmark");
function colorOf(series) {
  if (isBench(series)) return css("--bench");
  const i = window.SERIES.filter((s) => !isBench(s)).indexOf(series);
  return css(`--series-${(i % 8) + 1}`);
}
const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const signed = (v, d = 2) => (v === null || v === undefined ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d));
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const etTime = (iso) => new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York",
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }) + " ET";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const get = (url) => fetch(url).then((r) => r.json());

document.querySelectorAll(".swatch[data-series]").forEach((el) => { el.style.background = colorOf(el.dataset.series); });

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
  const t = st.budget._total;
  const bcol = t.state === "ok" ? css("--good") : t.state === "warn" ? css("--warning") : css("--critical");
  chips.push(`<span class="chip"><span class="dot" style="background:${bcol}"></span>LLM budget <b>$${fmt(t.spent)} / $${fmt(t.cap, 0)}</b>${t.state !== "ok" ? " " + t.state : ""}</span>`);
  chips.push(`<button id="pauseall">${st.paused_all ? "Resume all" : "Pause all"}</button>`);
  document.getElementById("status").innerHTML = chips.join("");
  document.getElementById("pauseall").onclick = async () => {
    await fetch(st.paused_all ? "/api/resume_all" : "/api/pause_all", { method: "POST" });
    loadStatus();
  };
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
    label: s, data: data[s].map(([x, y]) => ({ x, y })), borderColor: colorOf(s), backgroundColor: colorOf(s),
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
  if (charts[id]) { charts[id].data = data; charts[id].options = opts; charts[id].update(); return; }
  charts[id] = new Chart(document.getElementById(id), { type, data, options: opts });
}

// ---------- index page ----------
let equityMode = "usd";
async function loadIndex() {
  const [eq, dd, daily, heat] = await Promise.all([
    get(`/api/equity?mode=${equityMode}`), get("/api/drawdown"), get("/api/daily_pnl"), get("/api/heatmap")]);
  const money = (v) => "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const pct = (v) => signed(v, 2) + "%";
  draw("equity", "line", { datasets: lineDatasets(eq) }, timeLinear(baseOptions("", equityMode === "usd" ? money : pct)));
  draw("drawdown", "line", { datasets: lineDatasets(dd) }, timeLinear(baseOptions("", (v) => fmt(v, 1) + "%")));

  const days = [...new Set(Object.values(daily).flat().map(([d]) => d))].sort();
  const ds = window.SERIES.filter((s) => daily[s]).map((s) => {
    const m = Object.fromEntries(daily[s]);
    return { label: s, data: days.map((d) => m[d] ?? null), backgroundColor: colorOf(s),
             borderColor: css("--surface-1"), borderWidth: 1, borderRadius: 4, borderSkipped: "start" };
  });
  draw("daily", "bar", { labels: days, datasets: ds }, baseOptions("", (v) => signed(v, 0)));

  const symbols = [...new Set(Object.values(heat).flatMap((r) => Object.keys(r)))].sort();
  const maxAbs = Math.max(1, ...Object.values(heat).flatMap((r) => Object.values(r).map(Math.abs)));
  const shade = (v) => {
    const t = Math.min(1, Math.abs(v) / maxAbs);
    const pole = v >= 0 ? css("--div-pos") : css("--div-neg");
    return `color-mix(in oklab, ${pole} ${Math.round(t * 85)}%, ${css("--div-mid")})`;
  };
  const rows = window.SERIES.filter((s) => heat[s]).map((s) =>
    `<tr><td class="l">${esc(s)}</td>${symbols.map((sym) => {
      const v = heat[s][sym];
      return v === undefined ? `<td class="muted">–</td>`
        : `<td style="background:${shade(v)}" title="${esc(s)} ${sym}: ${signed(v)}">${signed(v, 0)}</td>`;
    }).join("")}</tr>`);
  document.getElementById("heatmap").innerHTML = symbols.length
    ? `<thead><tr><th class="l">bot</th>${symbols.map((s) => `<th>${s}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody>`
    : `<tr><td class="l muted">No closed trades yet.</td></tr>`;
}
function initIndex() {
  document.querySelectorAll("button[data-mode]").forEach((b) => b.onclick = () => {
    equityMode = b.dataset.mode;
    document.querySelectorAll("button[data-mode]").forEach((x) => x.classList.toggle("on", x === b));
    loadIndex();
  });
  return loadIndex;
}

// ---------- arm page ----------
let candleChart, candleSeries;
function table(id, cols, rows, empty) {
  const el = document.getElementById(id);
  if (!rows.length) { el.innerHTML = `<tr><td class="l muted">${empty}</td></tr>`; return; }
  el.innerHTML = `<thead><tr>${cols.map(([k, label, l]) => `<th class="${l ? "l" : ""}" title="${esc(window.TIPS[k] || "")}">${label}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr>${cols.map(([k, , l, f]) => `<td class="${l ? (l === "wrap" ? "wrap" : "l") : ""}">${f ? f(r[k], r) : esc(r[k])}</td>`).join("")}</tr>`).join("")}</tbody>`;
}
async function loadCandles() {
  const sym = document.getElementById("symbol").value, tf = document.getElementById("tf").value;
  const res = await get(`/api/candles?symbol=${sym}&arm=${encodeURIComponent(window.ARM)}&tf=${tf}`);
  const el = document.getElementById("candles");
  if (!candleChart) {
    candleChart = LightweightCharts.createChart(el, { autoSize: true,
      layout: { background: { color: css("--surface-1") }, textColor: css("--text-secondary") },
      grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
      localization: { timeFormatter: (s) => etTime(s * 1000) },
      timeScale: { timeVisible: true, tickMarkFormatter: (s) => new Date(s * 1000).toLocaleString("en-US",
        { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false }) } });
    candleSeries = candleChart.addCandlestickSeries({ upColor: css("--good"), downColor: css("--critical"),
      wickUpColor: css("--good"), wickDownColor: css("--critical"), borderVisible: false });
  }
  candleSeries.setData(res.bars);
  candleSeries.setMarkers(res.markers.map((m) => ({ time: m.time, position: m.side === "buy" ? "belowBar" : "aboveBar",
    color: m.side === "buy" ? css("--series-1") : css("--series-2"), shape: m.side === "buy" ? "arrowUp" : "arrowDown",
    text: `${m.side} ${fmt(m.price)}${m.reason !== "entry" && m.reason !== "close" ? " " + m.reason : ""}${m.replayed ? " (replayed)" : ""}` })).sort((a, b) => a.time - b.time));
  candleChart.timeScale().fitContent();
  document.getElementById("candlenote").textContent = res.error ? `Price data unavailable: ${res.error}`
    : !res.bars.length ? "No price bars available (replay mode or no data yet); trade markers only." : "";
}
async function loadArm() {
  const arm = encodeURIComponent(window.ARM);
  const [pos, trades, decs, rej, st] = await Promise.all([get(`/api/arm/${arm}/positions`), get(`/api/arm/${arm}/trades`),
    get(`/api/arm/${arm}/decisions`), get(`/api/arm/${arm}/rejections`), get("/api/status")]);
  const me = st.arms.find((a) => a.arm === window.ARM) || {};
  document.getElementById("armstate").innerHTML =
    `<span class="chip">model <b>${esc(me.model)}</b></span><span class="chip">setup <b>${esc(me.variant)}</b></span>` +
    `<span class="chip">last decision <b>${me.last_decision ? etTime(me.last_decision) : "–"}</b></span>` +
    (me.paused ? `<span class="chip"><span class="dot" style="background:${css("--warning")}"></span><b>paused</b></span>` : "");
  const btn = document.getElementById("pausebtn");
  btn.textContent = me.paused ? "Resume" : "Pause";
  btn.onclick = async () => { await fetch(`/api/${me.paused ? "resume" : "pause"}/${arm}`, { method: "POST" }); loadArm(); };

  const money = (v) => `<span class="${cls(v)}">${signed(v)}</span>`;
  table("positions", [["symbol", "stock", 1], ["qty", "shares", 0, (v) => fmt(v, 3)], ["entry_price", "entry", 0, (v) => fmt(v)],
    ["price", "price now", 0, (v) => fmt(v)], ["unrealized", "unrealized", 0, money], ["stop_loss", "stop", 0, (v) => fmt(v)],
    ["profit_target", "target", 0, (v) => fmt(v)], ["invalidation", "proves it wrong", "wrap"]], pos, "No open positions.");
  table("trades", [["symbol", "stock", 1], ["entry_ts", "bought", 1, etTime], ["exit_ts", "sold", 1, etTime],
    ["entry_price", "in", 0, (v) => fmt(v)], ["exit_price", "out", 0, (v) => fmt(v)], ["hold_hours", "held (h)", 0, (v) => fmt(v, 1)],
    ["pnl", "P&L", 0, money], ["r_multiple", "R", 0, (v) => signed(v, 2)], ["exit_reason", "exit", 1, (v, r) => esc(v) + (r.replayed ? ' <span class="chip" title="Filled while catching up after the PC was off">replayed</span>' : "")],
    ["justification", "reason", "wrap"]], trades, "No closed trades yet.");
  table("rejections", [["ts", "time", 1, etTime], ["symbol", "stock", 1], ["rule", "rule broken", 1], ["detail", "detail", "wrap"]],
    rej, "Nothing rejected.");
  document.getElementById("decisions").innerHTML = decs.length ? decs.map((d) =>
    `<details data-id="${d.id}"><summary><span class="muted">${etTime(d.slot_ts)} · ${esc(d.reason)} · ${esc(d.status)} · $${fmt(d.cost, 3)}</span> — ${esc(d.summary)}</summary><div class="detail">loading…</div></details>`).join("")
    : `<p class="muted">No decisions yet.</p>`;
  document.querySelectorAll("#decisions details").forEach((el) => el.addEventListener("toggle", async () => {
    if (!el.open || el.dataset.loaded) return;
    const d = await get(`/api/decision/${el.dataset.id}`);
    el.querySelector(".detail").innerHTML = `<h2>Prompt</h2><pre>${esc(d.prompt)}</pre><h2>Reply</h2><pre>${esc(d.raw)}</pre>`;
    el.dataset.loaded = "1";
  }));
}
function initArm() {
  document.getElementById("symbol").onchange = loadCandles;
  document.getElementById("tf").onchange = loadCandles;
  loadCandles();
  return loadArm;
}

// ---------- polling: 5 s while the market is open, else 60 s ----------
(async function main() {
  const page = document.body.dataset.page;
  const refresh = page === "index" ? initIndex() : page === "arm" ? initArm() : async () => {};
  async function tick() {
    try { await loadStatus(); await refresh(); } catch (e) { console.error(e); }
    setTimeout(tick, marketOpen ? 5000 : 60000);
  }
  tick();
})();
