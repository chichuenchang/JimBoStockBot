"use strict";

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
  // the status bar wraps to two rows on a narrow window: things that stick or jump below it need its real height
  const bar = document.querySelector(".topbar");
  new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`)).observe(bar);
  // phone: tabs scroll sideways; bring the open one into view (not scrollIntoView: it would also pull the page to the top)
  const tabs = document.querySelector("nav.tabs"), open = tabs.querySelector("a.on");
  if (open) tabs.scrollLeft = open.offsetLeft - tabs.offsetLeft - (tabs.clientWidth - open.offsetWidth) / 2;
  const refresh = page === "index" ? initIndex() : page === "arm" ? initArm() : page === "usage" ? initUsage() : async () => {};
  // one round: status, then the page. The dashboard is served by the daemon: no answer = it is down,
  // and every number on screen is going stale
  window.refreshAll = async () => {
    let st = null;
    try { st = await loadStatus(); } catch (e) { console.error(e); }
    document.body.classList.toggle("offline", !st);
    if (st) try { await refresh(st); } catch (e) { console.error(e); }
    return st;
  };
  (async function tick() {
    const st = await window.refreshAll();
    setTimeout(tick, !st || marketOpen ? 5000 : 60000);
  })();
})();
