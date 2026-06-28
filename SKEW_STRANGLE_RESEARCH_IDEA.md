# ETH Put Skew → OTM Strangle — research idea (parked, revisit ~mid/late July 2026)

**Status:** Idea only, NOT validated, NOT backtested with crash stress. Do not build until Phase 2
data window (3-4 weeks from 2026-06-18, i.e. ~2026-07-16+) completes and the full OOS process runs.

## Origin

Came out of a descriptive peek at `iv_snapshots` (opt21 measurement infra, 10 days of data as of
2026-06-28) while checking Hypothesis A (VRP). Found a real, cross-venue-consistent put skew on ETH:

| Tenor | RR25 (Bybit) | Fly (Bybit) |
|---|---|---|
| 7d  | -0.176 | +0.068 |
| 14d | -0.124 | +0.031 |
| 30d | -0.076 | +0.016 |

Same sign and similar magnitude on AEVO/DERIVE/BYBIT independently → looks like real market structure,
not noise/venue artifact.

## Key correction made during discussion — read this before doing anything

**ATM put IV and ATM call IV at the SAME strike cannot differ** (put-call parity / no-arbitrage).
So this skew does **NOT** mean Boba1/Grogu1's existing ATM straddle legs are mispriced relative to
each other — there is nothing to "reweight" in the current ATM bots. The skew only exists **across
different strikes** (OTM put vs OTM call), so it can only be harvested by trading a **different
structure** (OTM strangle / risk reversal), not by tuning the existing ATM straddle bots.

## Back-of-envelope economics (real BS calc on live Bybit IV surface, spot ETH ~$1666, 2026-06-28)

| Tenor | ATM straddle premium (current bots) | OTM strangle (90%/110%) actual premium | OTM strangle if no skew (flat ATM vol) | Skew-driven extra $ on put wing |
|---|---|---|---|---|
| 7d  | $98.19/ETH | $15.83/ETH | $10.62/ETH | **+$6.07** (+152% vs flat-vol) |
| 14d | $140.10/ETH | $37.90/ETH | $33.32/ETH | +$7.39 (+53%) |
| 30d | $218.87/ETH | $97.44/ETH | $92.92/ETH | +$8.42 (+20%) |

At Grogu1's current contract size (~0.4 ETH/leg), the put-wing skew uplift is roughly **$2.4-3.4 per
cycle** at 7d tenor — a real but small number relative to current ATM straddle premium (~$16-18/leg
collected today). **Switching to an OTM strangle would itself collect far LESS total premium than
the current ATM straddle** ($15.83 vs $98.19/ETH at 7d) — this is a different risk/reward product,
not a strict improvement on what's running now.

## Why this is NOT a free lunch — the actual risk

Selling OTM puts because "the market overpays for crash insurance" is the textbook **short-skew /
tail-risk premium trade**. It's the same mechanism this project has already been burned by:
- Grogu SL incidents (thin-liquidity spike, dollar-margin SL fix)
- Boba1's 2022 LUNA/FTX crash stress test (survived, but only because of the dollar-margin SL design)

A new OTM-strangle strategy selling far puts would need its **own independent crash stress test**
(2022-style) before being trusted — the tail risk here is plausibly *larger* than the current ATM
approach, not smaller, since you're explicitly selling the thing the market is paying up for.

## What would need to happen before building this

1. Wait for opt21 Phase 2 data window to mature (real skew history needs weeks, not 10 days, to know
   if RR25/fly are stable or themselves regime-dependent).
2. Backtest an OTM-strangle variant through the existing BS-based engine
   (`btc_straddle_dollar_account_sim.py` / `eth_straddle` equivalents), using a **skew-adjusted**
   pricing model (not the current flat-sigma assumption) — re-price historical legs with a
   calibrated skew curve, not just ATM IV.
3. Stress-test specifically against the 2022 LUNA/FTX window (BTC) and equivalent ETH drawdowns,
   same rigor as Boba1's `btc_straddle_dollar_account_sim.py`.
4. Only then decide whether to deploy as a **new, separate paper bot** — this is not a parameter
   tweak to Boba1/Grogu1, it's a new strategy with a new risk profile.

## Source of the raw numbers (for reproducibility)

- `iv_snapshots` / `bybit_flow` tables in `/root/opt21/data.sqlite` on VPS2 (168.231.118.173)
- Ad-hoc scripts used: `/root/opt21/edge_peek.py` (VRP + flow-correlation peek),
  `/root/opt21/skew_econ.py` (BS pricing comparison above) — both committed alongside this doc for
  reproducibility, not meant as production code.

## Friction sanity-check (real live bid/ask, 2026-06-28) — answers "does anything survive spread+fees?"

Pulled real top-of-book quotes from `options_data` (not theoretical BS-mid) for ETH 90%-moneyness
puts on Bybit, spot ~$1569:

| Expiry | bid | ask | mid | spread | spread % of mid | depth (bid/ask size) |
|---|---|---|---|---|---|---|
| 5d (2026-07-03), K=1400 | $6.80 | $6.90 | $6.85 | $0.10 | 1.5% | 478 / 578 contracts |
| 12d (2026-07-10), K=1400 | $18.60 | $18.80 | $18.70 | $0.20 | 1.1% | 40 / 592 contracts |

At Grogu1's position size (0.4 ETH/leg): credit $2.74, round-trip spread $0.04, exchange fees
(~0.03% notional/side per project convention) ~$0.34 → **friction ≈ $0.38 (~14% of premium)**,
net ≈ $2.36 remaining. On this single live snapshot, friction does NOT wipe out the skew-driven
premium — most of it (the skew uplift was ~$2.43 on this position size) survives.

**Caveats — do not over-trust this:**
1. One moment in time, one strike. Spread% varies a lot across strikes/expiries in the same
   snapshot (saw 10-50% spread on some near-ATM short-DTE contracts in the same pull).
2. **Direct precedent for the opposite outcome exists**: `finding_eth_ironfly_spread_rejected` —
   iron butterfly (4-leg wing-selling) looked fine on BS-mid, lost to real spread on the historical
   data. Same underlying mechanism (selling OTM premium), different structure. A clean snapshot
   today is not proof this survives over time.
3. Friction surviving says nothing about tail risk — the real risk in this trade isn't spread, it's
   the crash scenario where the short put blows up. This check only addresses "is there anything to
   lose to friction," not "is this worth the risk."

**Conclusion: keep collecting.** `options_data` already logs real bid_1/ask_1 every cycle — once we
have weeks of this, recompute round-trip P&L (entry at bid, decay/expiry, real fee model) across many
days/strikes instead of trusting a single snapshot. Revisit alongside the Phase 2 window (~mid/late
July 2026).
