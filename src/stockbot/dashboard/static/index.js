"use strict";

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
  const li = (s) => `<li>${stockLink(s.symbol)}<span class="${cls(s.change_pct)}">${signed(s.change_pct)}%</span></li>`;
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
      rows.map((r) => `<tr><td class="l"><span class="swatch" style="background:${colorOf(r.arm)}"></span><a href="${esc(url("/arm/" + encodeURIComponent(r.arm)))}">${esc(nameOf(r.arm))}</a></td>` +
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
  const long = (d) => etLongDayFmt.format(at(d));  // noon UTC is the same date in New York; one formatter for every cell
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
  const row = (sym) => `<tr><th class="l" scope="row">${stockLink(sym)}</th>`
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
