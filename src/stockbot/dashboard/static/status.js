"use strict";

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
