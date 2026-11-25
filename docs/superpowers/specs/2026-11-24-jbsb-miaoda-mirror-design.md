# JBSB Miaoda mirror — design

Status: draft 2026-11-24. Branch `feat/jbsb-miaoda`. Pattern source: sibling `BRecruitHelper` creatorio socket (branch `chore/creatorio-drop-env`, `docs/handbooks/creatorio.md`; repo `../brecruit-creatorio-miaoda`).

## Intent

Jim views JBSB from phone and this PC without localhost (user). Look only (user). Hosted on Miaoda, tenant `ycnx0knkk9ic`, lark-cli profile `brecruit` (user).

## Goals

- Same pages as localhost: `/`, `/arm/<arm>`, `/usage`, with their `/api/*` data. Same templates and JS, no second renderer.
- Minutes behind localhost during session at most.
- Only Jim can open it (Feishu login, access scope `specific` = Jim).

## Non-goals

- Controls: pause/resume/pause_all/resume_all, interval, tab-order drag (user: look only).
- Live Alpaca on socket. Candles/market = last push.
- Every decision detail and chart PNG ever. Bounded set (below).
- Stage socket. One prod app, like creatorio (CLAUDE.md §Deploy exception there).

## Anti-goals

- Anything on socket that changes trading or the local DB.
- Page reachable without Jim's login. P&L, trades, prompts stay private.
- Push failure stalls or slows daemon cycles.
- Secret (API key) in git, logs, or pushed bodies.

## Approaches

- **A. Response mirror** — picked (user). Daemon renders its own GETs in-process, pushes bodies; socket serves them by path. Zero drift, socket dumb.
- B. TS port like creatorio. Dataset JSON + TS view. Rejected: every dashboard change twice.
- C. Push DB rows, socket reruns queries in TS. Rejected: duplicates `queries.py`; 247 MB DB sync bookkeeping.

## Design

### Root prefix (StockBot dashboard)

Miaoda mounts app at `/app/<app_id>`; templates/JS use absolute `/...` (`base.html`, `index.html`, `arm.js`, `core.js`, `status.js`, `main.js`, `index.js`, `usage.js`, `candles.js`). Add `root` to template context (`""` localhost) and `ROOT` JS global from `<body data-root>`; every href/src/fetch prefixed. Localhost output byte-identical except `data-root=""`.

### Mirror flag

`create_app(..., mirror: MirrorView | None)`. Set: `root` = socket base path, write buttons + drag hidden, banner "mirror · as of <ET time>" from push time, `/api/*` JS 404 → muted "not mirrored" in place of panel.

### Crawl (daemon side, new `src/stockbot/mirror.py`)

Second app instance (`mirror` set) driven by `httpx.ASGITransport` — no network, no uvicorn. Key = path + query sorted. Set:

- HTML: `/`, `/usage`, `/arm/<arm>` each enabled arm.
- Static: `/static/*.js?v=<js_version>` + any CSS/assets the templates load.
- API, no params: leaderboard, drawdown, daily_pnl, heatmap, status, `arm/<arm>/{positions,trades,conviction,rejections}`.
- API, enumerated params: `equity` `mode=usd|pct`, `arms=<arm>,<benchmark>` per arm page as `arm.js` sends; `usage` `span=today|7d`; `market` `sessions=` each `button[data-sessions]` in `index.html`; `decisions` `acted=false|true` per arm; `candles` universe × each `#tf` option × (no arm, each arm).
- Bounded: `decision/<id>` latest N per arm (N=50, settings); `chart` PNGs those decisions reference.

Builder derives exact query strings from the JS so keys match what browser asks. Test asserts every fetch URL pattern in `static/*.js` is covered or listed as unmirrored.

### Push

- `.env`: `JBSB_MIAODA_URL` (`https://ycnx0knkk9ic.feishuapp.com/app/<app_id>`), `JBSB_MIAODA_KEY`. Either missing = mirror off, one log line at start.
- Daemon task beside dashboard (`daemon.run_forever`): crawl + push every `cycle_every_min` while market open, hourly otherwise, once at start. Skip push when body hashes unchanged. Errors logged, never raised into cycles. Crawl in thread (`asyncio.to_thread`) if it blocks loop >1s — measure.
- Wire (delta, content-addressed — upload ~40 kB/s, see Measured): body gzipped, `hash` = sha256 of gzipped bytes. Client remembers hashes the socket acked. `POST /openapi/jbsb/blobs` `{blobs:[{hash, content_type, gz_b64}]}` chunked ≤ 400 kB request, only hashes not acked. Then `POST /openapi/jbsb/commit` `{index:{key: hash}, pushed_at}`: socket in one transaction replaces index if every hash stored, else 409 `{missing:[hash]}` — client uploads those, commits again (once). Old index serves until commit lands. Unreferenced blobs pruned on commit.

### Socket (new Miaoda repo `../jbsb-miaoda`)

- `+create` full_stack, enable database, `+init`. Template NestJS like creatorio; template React client + ViewModule not mounted (creatorio `app.module.ts` pattern), `enableCsrf: false`.
- Hand-written TS in repo (no bundle: StockBot is Python). `server/modules/jbsb/`: controller, raw-SQL store via platform Drizzle (creatorio `creatorio.store.ts` pattern).
- Tables `jbsb_blob(hash text pk, content_type text, body bytea)` (gzip bytes), `jbsb_index(key text pk, hash text)`, `jbsb_meta(id int pk, pushed_at timestamptz)`. DDL on dev DB branch, release carries it (creatorio handbook §Writes).
- Routes: `GET /` and `GET /*` = lookup key (strip base, sort query), serve with `Content-Encoding: gzip` (inflate if client lacks gzip), else 404 `{"detail":"not mirrored"}`. `POST /openapi/jbsb/{blobs,commit}` = API key (`Authorization: Bearer`, platform-checked before app).
- Access: `+access-scope-set --scope specific --targets '[{"type":"user","id":"<Jim ou_>"}]'`. Key: `+openapi-key-create --scope-api 'POST /openapi/jbsb/blobs' --scope-api 'POST /openapi/jbsb/commit'`; value straight into `.env`, never echoed.

### Deploy

`scripts/deploy_miaoda.py`: port of BRecruitHelper `scripts/deploy/miaoda-deploy.mjs` minus build step — commit if dirty, push `sprint/default`, wait 15s, `+release-create --apply-reason`, poll `+release-get`, re-release if shipped commit ≠ pushed. GCM credential helper setup: BRecruitHelper `CLAUDE.md` §Miaoda repos from this machine.

## Measured (spike 2026-11-24, app `app_17jr1c29a4u`)

- Login wall: unauthenticated `GET /` = 302. `POST /openapi/**` with `Authorization: Bearer <key>` = 200 past it; any other header = 403 `k_ec_000015` "missing or invalid Authorization header". Fallback not needed.
- Body cap: 512 kB ok, 2 MB = 500 `PayloadTooLargeError`. Cap ≈1 MB (body-parser), unset by us.
- Upload Canada→Miaoda ~40 kB/s: 64 kB 2.3 s, 512 kB 13.1 s. Hence gzip + delta wire.
- `access-scope-set specific` leaves `apply_config.enabled` with Jim as approver: others may request, Jim approves.
- Template `npm run lint` / pre-commit hook: `spawn EINVAL` / `spawnSync npm ENOENT` on Windows. Run `npx eslint` + `npm run type:check:server` by hand.

Open: crawl size/time (candles universe × tf × arms). Measure first push; cut combos if gzipped delta > ~1 MB per cycle.

## Left to builder

Chunk size, N, crawl threading, exact unchanged-skip granularity, banner styling, test split.
