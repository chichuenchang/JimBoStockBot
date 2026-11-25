"use strict";

// ---------- price chart: candles, fill badges, open position lines (overview's market panel and bot page) ----------
let candleChart, candleSeries, candleSeq = 0;
const TRADE_VIEW_BARS = 40;  // at least this many bars each side of a closed trade's middle
const DAY_VIEW_BARS = 250;  // about a trading year
const BUY_TFS = ["1Min", "1Hour", "1Day"];  // finest first: intraday windows are short (app.py INTRADAY_SESSIONS)
let focused = null;  // closed trade picked in the table: { symbol, from, to }. Its badges stay solid, the rest fade
const isoSeconds = (iso) => Math.floor(new Date(iso).getTime() / 1000);  // floor: as queries.chart_fills
const isFocused = (symbol, buyIso) => focused?.symbol === symbol && focused.from === isoSeconds(buyIso);
function focusCandles(trade) { focused = trade; repaintBadges?.(); }  // only writer of focused
// stock name that points the chart at it; plain text once the symbol left the universe (/api/candles 404s)
const stockLink = (symbol, buyIso = "", sellIso = "") => inUniverse(symbol)
  ? `<button class="stocklink${isFocused(symbol, buyIso) ? " on" : ""}" data-symbol="${esc(symbol)}" data-buy="${esc(buyIso)}" data-sell="${esc(sellIso)}">${esc(symbol)}</button>` : esc(symbol);

// no ARM (overview's market chart): bars only, no fills. Bot page: the same bars + its fills apart, so the mirror
// (stockbot/mirror.py) stores each stock's bars once, not once per bot
async function fetchCandles(tf) {
  const symbol = document.getElementById("symbol").value;
  const [res, fills] = await Promise.all([get(`/api/candles?symbol=${symbol}&tf=${tf}`),
    window.ARM ? get(`/api/arm/${encodeURIComponent(window.ARM)}/fills?symbol=${symbol}`) : []]);
  return { ...res, fills };
}
// a fetch can take seconds (live feed): fade the old stock's bars meanwhile, so they don't pass for the new one's
const candlesLoading = (on) => document.getElementById("candles").classList.toggle("loading", on);
async function loadCandles() {
  const seq = ++candleSeq, tf = document.getElementById("tf").value;
  candlesLoading(true);
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
  candlesLoading(true);
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
// open position on the charted stock: its entry, stop and target as lines across the chart, kept in view
let openPositions = [], priceLines = [], planLevels = [], planKey = "";
function syncPriceLines() {
  const p = openPositions.find((x) => x.symbol === document.getElementById("symbol").value);
  const plan = !p ? [] : [[p.entry_price, "entry", css("--text-muted")], [p.stop_loss, "stop", css("--critical")],
                          [p.profit_target, "target", css("--good")]].filter(([price]) => price !== null && price !== undefined);
  const key = JSON.stringify(plan);
  const legend = document.getElementById("linekey");
  if (legend) legend.hidden = !plan.length;
  if (!candleSeries || key === planKey) return;
  planKey = key;
  priceLines.forEach((l) => candleSeries.removePriceLine(l));
  priceLines = plan.map(([price, title, color]) => candleSeries.createPriceLine({ price, title, color, lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true }));
  planLevels = plan.map(([price]) => price);
  candleChart.priceScale("right").applyOptions({ autoScale: true });  // fit the new levels
}
function drawCandles(tf, res) {
  const el = document.getElementById("candles");
  candlesLoading(false);  // only the latest load draws (candleSeq), so this is its answer
  if (!candleChart) {
    candleChart = LightweightCharts.createChart(el, { autoSize: true,
      layout: { background: { color: css("--surface-1") }, textColor: css("--text-secondary"), fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
      rightPriceScale: { borderColor: css("--border") },
      timeScale: { minBarSpacing: 0.1, borderColor: css("--border") } });  // default 0.5: on a phone a long trade's buy fell off the left edge
    candleSeries = candleChart.addCandlestickSeries({ upColor: css("--good"), downColor: css("--critical"),
      wickUpColor: css("--good"), wickDownColor: css("--critical"), borderVisible: false,
      // stop and target count as data: the distance to each is the point of drawing them
      autoscaleInfoProvider: (bars) => {
        const info = bars();
        if (info && planLevels.length) {
          info.priceRange.minValue = Math.min(info.priceRange.minValue, ...planLevels);
          info.priceRange.maxValue = Math.max(info.priceRange.maxValue, ...planLevels);
        }
        return info;
      } });
    candleSeries.attachPrimitive(fillBadges);
    const info = document.getElementById("fillinfo");  // bot page only
    if (info) {
      const showFills = (p) => { info.textContent = (fillsAtBar.get(p.time)?.fills || []).map(fillText).join(" · "); };
      candleChart.subscribeCrosshairMove(showFills);
      candleChart.subscribeClick(showFills);  // a tap moves no crosshair
    }
    planKey = "";
  }
  // day+ bars sit at ET midnight: show dates, not 00:00
  const long = ["1Day", "1Week", "1Month"].includes(tf);
  const etDay = (s) => new Date(s * 1000).toLocaleDateString("en-US", { timeZone: ET, year: "numeric", month: "short", day: "numeric" });
  candleChart.applyOptions({ localization: { timeFormatter: (s) => long ? etDay(s) : etTime(s * 1000) },
    timeScale: { timeVisible: !long, tickMarkFormatter: (s, type) => new Date(s * 1000).toLocaleString("en-US",
      long ? { timeZone: ET, year: "2-digit", month: "short", day: tf === "1Day" ? "numeric" : undefined }
           : type < LightweightCharts.TickMarkType.Time ? { timeZone: ET, month: "short", day: "numeric" }  // a new day
           : { timeZone: ET, hour: "2-digit", minute: "2-digit", hour12: false }) } });
  fillsAtBar = new Map();
  for (const fill of res.fills) {
    const bar = res.bars.findLast((b) => b.time <= fill.time);
    if (!bar || fill.time >= bar.time + BAR_SECONDS[tf]) continue;  // chart has no bar for it: older than the window, or feed down
    if (!fillsAtBar.has(bar.time)) fillsAtBar.set(bar.time, { bar, fills: [] });
    fillsAtBar.get(bar.time).fills.push(fill);
  }
  candleSeries.setData(res.bars);
  syncPriceLines();
  document.getElementById("candlenote").textContent = res.error ? `Price data unavailable: ${res.error}`
    : !res.bars.length ? "No price bars available (replay mode or no data yet)." : "";
}
