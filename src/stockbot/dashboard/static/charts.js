"use strict";

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
