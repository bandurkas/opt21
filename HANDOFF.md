# Project Handoff: opt21 — Crypto Options Edge Research

## What this project is now
A **data-measurement platform** for ETH options across **AEVO**, **Derive (Lyra)** and **Bybit**,
plus offline research to find a *real* edge. Deployed on VPS `168.231.118.173` at `/root/opt21/`,
Dockerized, SQLite (`data.sqlite`).

## History & the pivot (2026-06-18)
The project began as cross-venue **arbitrage** bots (H3 taker mid-reversion, H7 maker spread-capture).
Rigorous backtests on the collected data **rejected both**:
- **No static arbitrage exists.** Matched ATM IV ratio AEVO/DERIVE = **1.00**; put-call parity holds
  within spread (<1% of pairs have |dev| > spread cost). The venues agree on fair value.
- **H7** (maker): realistic exit (crossing the >$20 spread that is the entry condition) → **−$2.28/trade,
  14% win**. Advertised "profit" was ~3× the theoretical max.
- **H3** (taker): sum of both legs' spreads (~$57) is ~3× the entry mid-gap (~$19) → **100% of signals
  lose even on perfect convergence**; realistic −$1.02/trade, 0% win.
- Both bots had **0 realized trades** in production. All arb code removed; bots stopped & deleted.

Root cause: the cross-venue "gap" is an artifact of wide, illiquid spreads (mostly AEVO top-of-book);
the spread you must cross to trade it always exceeds the gap. Not fixable by tuning.

## Current components
- **`collector.py`** — raw ingestion. Polls Aevo/Derive/Bybit REST every 60s into `options_data`
  (Bybit via `GET /v5/market/tickers?category=option&baseCoin=ETH`). Container `options-collector`.
- **`analytics_collector.py`** — derived-feature logger, every **600s**. Reads an *aligned* snapshot
  (window = last 90s, latest per (exchange,contract); exact-`MAX(timestamp)` would see only one venue).
  Writes compact, append-only tables that survive a **14-day** `options_data` prune. Container
  `options-analytics` (reuses the collector image; code mounted, no rebuild needed).
- **`dashboard/`** — FastAPI dashboard (`:8080`). Still shows legacy arb tables; **to be repurposed**
  in Phase 2 to display VRP/skew/flow. Container `options-dashboard`.

### Analytics tables (`analytics_schema.sql`)
- `iv_snapshots` — per (ts, exchange, tenor∈{7,14,30}): spot, dte_actual, atm_iv, put/call wing IVs,
  `rr_25` (risk reversal), `fly` (butterfly), `rv_trailing_24h`, n_contracts.
- `bybit_flow` — per cycle: total/put/call OI & volume, PCR, atm_iv, `book_imb` (near-money L1 imbalance).
- `bybit_oi_strikes` — near-money (|m-1|≤0.25) per-strike OI/volume/iv.

## Two hypotheses under measurement (Phase 1 = collect data; NO trading)
- **A — Variance Risk Premium:** is IV systematically richer than subsequently-realized vol?
  NOTE: the initial 16h window showed **negative** VRP (RV~61% noise-robust > IV~49%), so this MUST be
  measured over weeks before any trade. Bonus signal found: ETH carries real **put skew** (RR<0).
- **C — BYBIT order flow → short-term vol/direction:** Bybit is the only venue with real volume/OI/depth.
  Do OI build-ups / volume bursts / book imbalance predict forward returns or IV moves?

## Phase 2 (≈3–4 weeks out, once data accumulates)
Offline backtests in a `backtest/` module: regress forward-RV on IV (A); label flow features with
forward returns/IV at {30m,1h,4h} and test OOS (C). Build a strategy **only if an edge survives holdout**.
Then live trading per DEVELOPMENT_FLOW.

## Ops
- Build images **on the developer machine if heavy** — VPS is 1-CPU x86, 3.8 GB RAM. The analytics
  service deliberately reuses `opt21-collector:latest` with code mounted to avoid VPS rebuilds.
- Health: `docker ps` (collector, analytics, dashboard should be Up); freshness = `MAX(ts)` in
  `iv_snapshots` advancing every ~10 min.
- Local diagnostics (`bt_h7.py`, `bt_h3.py`, `explore_edges.py`, `rv_check.py`, `probe_tenors.py`) are
  kept out of git intentionally; they reproduce the rejection analyses.

## Flow (DEVELOPMENT_FLOW.md)
architecture → code → review → test(backtest+holdout / unit) → review → deploy(commit/push/git pull on
VPS) → verify(container Up, 0 errors) → sync local=GitHub=VPS + docs.
