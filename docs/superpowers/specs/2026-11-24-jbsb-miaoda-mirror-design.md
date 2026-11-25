# JBSB Miaoda mirror — design

Status: live 2026-11-24, app `app_17jr1c29a4u`. Branch `feat/jbsb-miaoda`. Pattern source: sibling `BRecruitHelper` creatorio socket (branch `chore/creatorio-drop-env`, `docs/handbooks/creatorio.md`; repo `../brecruit-creatorio-miaoda`).

## Intent

Jim views JBSB from phone and this PC without localhost (user). Look only (user). Hosted on Miaoda, tenant `ycnx0knkk9ic`, lark-cli profile `brecruit` (user).

## Goals

- Same pages as localhost: `/`, `/arm/<arm>`, `/usage`, with their `/api/*` data. Same templates and JS, no second renderer.
- Minutes behind localhost during session at most.
- Only Jim can open it. Password, not Feishu login (user, 2026-11-25).

## Non-goals

- Controls: pause/resume/pause_all/resume_all, interval, tab-order drag (user: look only).
- Live Alpaca on socket. Candles/market = last push.
- Every decision detail and chart PNG ever. Bounded set (below).
- Stage socket. One prod app, like creatorio (CLAUDE.md §Deploy exception there).

## Anti-goals

- Anything on socket that changes trading or the local DB.
- Page reachable without password. P&L, trades, prompts stay private.
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

`create_app(..., mirror: MirrorView | None)`. Set: `root` = socket base path, write buttons + drag hidden, banner "mirror · as of <ET time>" from push time, `/api/*` 404 = `get()` error "not mirrored": shown under candle chart and in decision detail; other panels fixed-key, crawl test covers them.

### Crawl (daemon side, new `src/stockbot/mirror.py`)

Second app instance (`mirror` set) driven by `httpx.ASGITransport` — no network, no uvicorn. Key = path + query sorted. Set:

- HTML: `/`, `/usage`, `/arm/<arm>` each enabled arm.
- Static: `/jbsb-js/*.js?v=<js_version>` (§Measured: platform owns `/static`).
- API, no params: leaderboard, drawdown, daily_pnl, heatmap, status, `arm/<arm>/{positions,trades,conviction,rejections}`.
- API, enumerated params: `equity` `mode=usd|pct`, `arms=<arm>,<benchmark>` per arm page as `arm.js` sends; `usage` `span=today|7d`; `market` `sessions=` each `button[data-sessions]` in `index.html`; `decisions` `acted=false|true` per arm; `candles` universe × each `#tf` option (1Day/1Week/1Month hourly only); `arm/<arm>/fills?symbol=` per universe symbol.
- Bounded: `decision/<id>` for ids the two decision lists return; `chart` PNGs those reference.

Builder derives exact query strings from the JS so keys match what browser asks. Test asserts every fetch URL pattern in `static/*.js` is covered or listed as unmirrored.

### Push

- `.env`: `JBSB_MIAODA_URL` (`https://ycnx0knkk9ic.feishuapp.com/app/<app_id>`), `JBSB_MIAODA_KEY`. Either missing = mirror off, one log line at start.
- Daemon task beside dashboard (`daemon.run_forever`): crawl + push every `cycle_every_min` while market open, hourly otherwise, once at start. Push every due tick: `/api/status` heartbeat moves every step, and stamp proves PC up; unchanged blobs already on socket. Errors logged, never raised into cycles.
- Wire (delta, content-addressed — upload ~40 kB/s, see Measured): body gzipped, `hash` = sha256 of gzipped bytes. Client remembers hashes the socket acked. `POST /openapi/jbsb/blobs` `{blobs:[{hash, content_type, gz_b64}]}` chunked ≤ 400 kB request, only hashes not acked. Then `POST /openapi/jbsb/commit` `{index:{key: hash}, pushed_at}`: socket in one transaction replaces index if every hash stored, else 409 `{missing:[hash]}` — client uploads those, commits again (once). Old index serves until commit lands. Unreferenced blobs pruned on commit.

### Socket (new Miaoda repo `../jbsb-miaoda`)

- `+create` full_stack, enable database, `+init`. Template NestJS like creatorio; template React client + ViewModule not mounted (creatorio `app.module.ts` pattern), `enableCsrf: false`.
- Hand-written TS in repo (no bundle: StockBot is Python). `server/modules/jbsb/`: controller, raw-SQL store via platform Drizzle (creatorio `creatorio.store.ts` pattern).
- Tables `jbsb_blob(hash text pk, content_type text, body bytea)` (gzip bytes), `jbsb_index(key text pk, hash text)`, `jbsb_meta(id int pk, pushed_at timestamptz)`. DDL on dev DB branch, release carries it (creatorio handbook §Writes).
- Routes: `GET /` and `GET /*` = lookup key (strip base, sort query), serve with `Content-Encoding: gzip` (inflate if client lacks gzip), else 404 `{"detail":"not mirrored"}`. `POST /openapi/jbsb/{blobs,commit}` = API key (`Authorization: Bearer`, platform-checked before app).
- Access: `+access-scope-set --scope public --require-login=false`; app gates every GET (`server/modules/jbsb/jbsb.auth.ts`). Password = online env `JBSB_PASSWORD` (`+env-set --environment online --value @file --yes`); unset or under `MIN_PASSWORD` = locked. No throttle: repo public, URL in README, so global cap lets anyone lock Jim out; per-IP needs client IP the platform proxy vouches for, unknown. Length floor makes online guessing moot instead. Session cookie = expiry HMAC keyed by password: change = all logged out. Key: `+openapi-key-create --scope-api 'POST /openapi/jbsb/blobs' --scope-api 'POST /openapi/jbsb/commit'`; value straight into `.env`, never echoed.

### Deploy

`scripts/deploy_miaoda.py`: port of BRecruitHelper `scripts/deploy/miaoda-deploy.mjs` minus build step — dirty repo = stop (never auto-commit: repo's `.env` tracked), push `sprint/default`, wait 15s, `+release-create --apply-reason`, poll `+release-get`, re-release if shipped commit ≠ pushed. GCM credential helper setup: BRecruitHelper `CLAUDE.md` §Miaoda repos from this machine.

## Measured (spike 2026-11-24, app `app_17jr1c29a4u`)

- Login wall: unauthenticated `GET /` = 302. `POST /openapi/**` with `Authorization: Bearer <key>` = 200 past it; any other header = 403 `k_ec_000015` "missing or invalid Authorization header". Fallback not needed.
- Body cap: 512 kB ok, 2 MB = 500 `PayloadTooLargeError`. Cap ≈1 MB (body-parser), unset by us.
- Upload Canada→Miaoda ~40 kB/s: 64 kB 2.3 s, 512 kB 13.1 s. Hence gzip + delta wire.
- `access-scope-set specific` leaves `apply_config.enabled` with Jim as approver: others may request, Jim approves.
- Template `npm run lint` / pre-commit hook: `spawn EINVAL` / `spawnSync npm ENOENT` on Windows. Run `npx eslint` + `npm run type:check:server` by hand.

- Platform middleware owns `/app/<id>/static/*` (404 "File not found", browser sees 503) before the app: mirror JS at `/jbsb-js` (`MIRROR_STATIC`).
- Platform Drizzle binds a `Buffer` param as text: bytea insert 500s. Store via `decode(<b64>,'base64')`, read `encode(body,'base64')`.
- Crawl, live DB 2026-11-24 (31 symbols, 7 arms): 966 keys, 13 s, 24 MB raw, 7 MB gz (decisions 4.0, candles 1.9, equity 0.5). Per-arm candle copies were 5.1 MB more: bot chart now = overview bars + `/api/arm/<arm>/fills`.
- Loop stall, DB copy 2026-11-25: crawl worst 65 ms (stays on loop); gzip + hash of 22 MB 1.1 s, so `_pack` runs in `asyncio.to_thread`.
- First full upload 9.3 MB base64, 155 s. Restart push (commit first, 409 lists missing): 150 kB, ~19 s.
- Decision details pushed = ids the page lists (50 latest + acted), not a setting.

## Run

`.env` `JBSB_MIAODA_URL` + `JBSB_MIAODA_KEY` (key: `+openapi-key-reset`, never echoed). `stockbot run` pushes; `stockbot.mirror` log line per push. Socket change: edit + commit `../jbsb-miaoda`, `uv run python scripts/deploy_miaoda.py`. DDL: `server/database/jbsb.sql` on dev, release carries it.
