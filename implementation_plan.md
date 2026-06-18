# Implementation Plan — VRP & BYBIT-Flow Measurement Infrastructure

**Date:** 2026-06-18
**Author:** agent (under DEVELOPMENT_FLOW.md)
**Status:** Phase 1 = build measurement infrastructure. NO trading yet.

## 0. Context & honest framing

Prior analysis (see walkthrough notes) proved:
- **No static arbitrage exists** across AEVO/DERIVE/BYBIT: matched ATM IV ratio = 1.00,
  put-call parity holds within spread (<1% of pairs have |dev| > spread cost).
- **H3 and H7 are structurally unprofitable** (the spread you must cross always exceeds the
  cross-venue mid-gap). Both bots have been **stopped** (`docker compose stop h3-bot radar`).

The only paths to a *real* edge are risk premia / predictive signals, neither of which can be
validated on the current ~16h of data. Therefore Phase 1 builds the **measurement
infrastructure** to accumulate the right data for two hypotheses:

- **Hypothesis A — Variance Risk Premium (VRP):** is implied vol systematically richer than
  subsequently-realized vol? Tradeable via short vol if VRP > 0 and stable. (Current 16h window
  shows NEGATIVE VRP — RV~61% > IV~49% — so this MUST be measured over weeks before any trade.)
- **Hypothesis C — BYBIT order flow → short-term vol/direction:** BYBIT is the only venue with
  real volume/OI/book depth. Do OI build-ups, volume bursts, or book imbalance predict forward
  underlying returns / IV changes?

## 1. Architecture

### 1.1 New process: `analytics_collector.py`
A lightweight loop (default **600s / 10 min**), same Docker image, new compose service `analytics`.
Each cycle:
1. Read the most recent **aligned** snapshot from `options_data`:
   - compute `max(timestamp)`, then select rows with `timestamp >= max_ts - 90s`
     (per-venue timestamps differ; exact-match would catch only one venue — this was a latent
     bug in the old bots).
   - keep the latest row per `(exchange, expiry, option_type, strike)`.
2. Compute VRP features → append to `iv_snapshots`.
3. Compute BYBIT flow features → append to `bybit_flow` and near-money `bybit_oi_strikes`.
4. Once per day: prune `options_data` older than **14 days** (disk safety; ~300 MB/day growth,
   28 GB free). `iv_snapshots` / `bybit_flow` are compact and **never pruned**.

Single responsibility: `collector.py` stays raw ingestion; `analytics_collector.py` owns derived
features. Reuses `data.sqlite` (same volume mount).

### 1.2 Schema (`analytics_schema.sql`) — compact, append-only, survives pruning

```
iv_snapshots(
  ts TEXT, exchange TEXT, spot REAL,
  tenor_target INT,        -- 7 / 14 / 30 (days)
  dte_actual REAL,         -- actual DTE of the chosen expiry
  atm_strike REAL, atm_iv REAL,
  put_iv_90 REAL, put_iv_95 REAL,    -- IV at moneyness ~0.90 / ~0.95
  call_iv_105 REAL, call_iv_110 REAL,-- IV at moneyness ~1.05 / ~1.10
  rr_25 REAL,              -- risk reversal = call_iv_110 - put_iv_90
  fly REAL,                -- butterfly = (put_iv_90+call_iv_110)/2 - atm_iv
  rv_trailing_24h REAL,    -- realized vol over last 24h of iv_snapshots spot (Parkinson-robust)
  n_contracts INT
)  -- one row per (ts, exchange, tenor_target)

bybit_flow(
  ts TEXT, spot REAL,
  total_oi REAL, total_volume REAL,
  oi_calls REAL, oi_puts REAL, vol_calls REAL, vol_puts REAL,
  pcr_oi REAL, pcr_vol REAL,           -- put/call ratios
  atm_iv REAL,
  book_imb REAL,                       -- near-money (sum bid_vol - sum ask_vol)/(sum)
  near_money_oi REAL
)  -- one row per cycle

bybit_oi_strikes(
  ts TEXT, expiry TEXT, dte REAL, strike REAL, opt_type TEXT,
  oi REAL, volume REAL, iv REAL
)  -- only |strike/spot - 1| <= 0.25, per cycle (~tens of rows)
```

Forward realized vol (for VRP) is computed **offline in Phase 2** from the persistent
`iv_snapshots.spot` series (10-min spacing → fine for horizons >= 1h). ΔOI / flow deltas
likewise derived offline from the `bybit_flow` / `bybit_oi_strikes` time series.

### 1.3 Feature definitions
- **ATM IV:** for each venue & target tenor {7,14,30}, pick the expiry with DTE nearest target;
  ATM = strike nearest spot; `atm_iv` = mean of call & put IV at that strike (fallback to whichever
  exists). Available tenors confirmed: DERIVE {8.3,15.3,22.3,43.3,...}, BYBIT {7.6,14.6,21.6,42.6}.
- **Skew points:** nearest strikes to moneyness 0.90/0.95 (puts) and 1.05/1.10 (calls); store their IVs.
- **RR / FLY:** standard risk-reversal & butterfly from the above.
- **Trailing RV (24h):** Parkinson/coarse estimator on `iv_snapshots.spot` history (noise-robust;
  minute-sampled RV is microstructure-inflated — proven: 100% @1min vs 61% @range-estimator).
- **BYBIT book_imb:** near-money (|m-1|<=0.05) aggregate `(Σbid_1_vol - Σask_1_vol)/(Σbid_1_vol+Σask_1_vol)`.

### 1.4 docker-compose changes
- Remove services `radar` and `h3-bot`.
- Keep `collector`, `dashboard`.
- Add `analytics` (image=build ., `command: python analytics_collector.py`, mount `./data.sqlite`,
  `restart: unless-stopped`, json-file logging 10m x3).
- Remove obsolete `version:` key (compose warns).

## 2. Phasing
- **Phase 1 (now):** implement + review + dry-run test + deploy `analytics_collector`. Verify rows
  land with sane values. Then **accumulate >= 3 weeks** of data.
- **Phase 2 (~3-4 weeks):** offline backtests:
  - A: regress forward-RV on IV per tenor; compute mean VRP & its term/skew dependence; if VRP>0
    and stable OOS → design short-vol strategy with delta-hedge + tail stop.
  - C: label flow features with forward underlying return / IV change at {30m,1h,4h}; test predictive
    power OOS (train/holdout split). Only build a bot if a signal survives holdout.
  - No live trading until an edge is validated OOS per DEVELOPMENT_FLOW.

## 3. Test plan (flow step 4)
This is data-collection infra (not an edge search), so tests are functional:
1. **Dry-run** `analytics_collector` against the real `data.sqlite` in read-only/compute mode:
   print the rows it WOULD insert; assert IV in (0,5), spot within venue range, OI>0, ratios finite.
2. **Live run** one cycle writing to the new tables; verify row counts & values via SQL.
3. Confirm idempotent schema creation and no impact on `collector`/`dashboard`.

## 4. Risks / notes
- DERIVE/BYBIT have no daily expiry beyond ~2-3d for some tenors; tenor selection uses nearest-DTE
  and records `dte_actual` so Phase-2 can normalize.
- `options_data` pruning uses DELETE (no VACUUM — VACUUM on 1-CPU VPS may stall SSH); freed pages reused.
- Defaults chosen (interval 600s, retention 14d, new `analytics` service) are adjustable.
```
```
