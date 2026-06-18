"""
analytics_collector.py — Phase-1 measurement infrastructure.

Every cycle (default 600s) it reads the latest ALIGNED snapshot from options_data and
appends compact, persistent features for two hypotheses:
  A) Variance Risk Premium  -> iv_snapshots  (ATM IV / skew / RR / FLY per tenor + trailing RV)
  C) BYBIT order flow        -> bybit_flow + bybit_oi_strikes  (OI/volume/imbalance)

These tables survive options_data pruning. NO trading is performed here.
Run: python analytics_collector.py            (loop)
     python analytics_collector.py --dry-run   (compute one cycle, print, write nothing)
"""
import sqlite3
import time
import sys
import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('analytics')

DB_FILE = "data.sqlite"
INTERVAL = 600          # seconds between cycles
WINDOW_SEC = 90         # alignment window for "latest snapshot" across venues
TENORS = [7, 14, 30]    # target tenors (days) for the IV term structure
VENUES = ['DERIVE', 'BYBIT', 'AEVO']
RETENTION_DAYS = 14     # prune options_data older than this (analytics tables never pruned)


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        with open('analytics_schema.sql') as f:
            conn.executescript(f.read())
        conn.commit()


def load_aligned_snapshot(conn):
    """Latest row per (exchange, expiry, option_type, strike) within WINDOW_SEC of max(ts)."""
    max_ts = pd.read_sql_query("SELECT MAX(timestamp) m FROM options_data", conn).iloc[0, 0]
    if not max_ts:
        return None, None
    cutoff = (pd.to_datetime(max_ts, utc=True) - timedelta(seconds=WINDOW_SEC)).isoformat()
    df = pd.read_sql_query(
        "SELECT timestamp, exchange, expiry, strike, option_type, bid_1, ask_1, mark_price, "
        "iv, delta, underlying_price, volume, open_interest, bid_1_vol, ask_1_vol "
        "FROM options_data WHERE timestamp >= ?", conn, params=(cutoff,))
    if df.empty:
        return None, None
    for c in ['strike', 'bid_1', 'ask_1', 'mark_price', 'iv', 'delta',
              'underlying_price', 'volume', 'open_interest', 'bid_1_vol', 'ask_1_vol']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['ts'] = pd.to_datetime(df['timestamp'], utc=True)
    df = (df.sort_values('ts')
            .groupby(['exchange', 'expiry', 'option_type', 'strike'], as_index=False).last())
    df['mid'] = (df['bid_1'] + df['ask_1']) / 2
    df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True, errors='coerce')
    df = df.dropna(subset=['expiry_utc'])
    df['dte'] = (df['expiry_utc'] - pd.to_datetime(max_ts, utc=True)).dt.total_seconds() / 86400.0
    df['m'] = df['strike'] / df['underlying_price']
    snap_ts = pd.to_datetime(max_ts, utc=True).isoformat()
    return df, snap_ts


def _iv_at_moneyness(grp, target_m, opt_type):
    """IV of the contract closest to target moneyness for a given option type."""
    sub = grp[(grp['option_type'] == opt_type) & grp['iv'].notna() & (grp['iv'] > 0)]
    if sub.empty:
        return None
    row = sub.iloc[(sub['m'] - target_m).abs().argmin()]
    return float(row['iv'])


def compute_iv_rows(df, snap_ts, trailing_rv):
    rows = []
    for ex in VENUES:
        e = df[(df['exchange'] == ex) & df['iv'].notna() & (df['iv'] > 0) & (df['iv'] < 5)]
        if e.empty:
            continue
        spot = float(e['underlying_price'].median())
        for tenor in TENORS:
            # nearest expiry to the target tenor
            exp_dtes = e.groupby('expiry')['dte'].first()
            exp_dtes = exp_dtes[exp_dtes > 0.5]
            if exp_dtes.empty:
                continue
            chosen_exp = (exp_dtes - tenor).abs().idxmin()
            dte_actual = float(exp_dtes[chosen_exp])
            grp = e[e['expiry'] == chosen_exp]
            # ATM = strike nearest spot; atm_iv = mean(call,put) at that strike
            atm_strike = float(grp.iloc[(grp['strike'] - spot).abs().argmin()]['strike'])
            atm_grp = grp[grp['strike'] == atm_strike]
            atm_iv = float(atm_grp['iv'].mean())
            put90 = _iv_at_moneyness(grp, 0.90, 'P')
            put95 = _iv_at_moneyness(grp, 0.95, 'P')
            call105 = _iv_at_moneyness(grp, 1.05, 'C')
            call110 = _iv_at_moneyness(grp, 1.10, 'C')
            rr = (call110 - put90) if (call110 is not None and put90 is not None) else None
            fly = (((put90 + call110) / 2) - atm_iv) if (put90 is not None and call110 is not None) else None
            rows.append((snap_ts, ex, spot, tenor, dte_actual, atm_strike, atm_iv,
                         put90, put95, call105, call110, rr, fly,
                         trailing_rv.get(ex), int(len(grp))))
    return rows


def compute_bybit_rows(df, snap_ts):
    b = df[df['exchange'] == 'BYBIT']
    if b.empty:
        return None, []
    spot = float(b['underlying_price'].median())
    oi = b['open_interest'].fillna(0)
    vol = b['volume'].fillna(0)
    calls = b['option_type'] == 'C'
    puts = b['option_type'] == 'P'
    oi_c, oi_p = float(oi[calls].sum()), float(oi[puts].sum())
    vol_c, vol_p = float(vol[calls].sum()), float(vol[puts].sum())
    nm = b[(b['m'] - 1).abs() <= 0.05]
    bidv, askv = nm['bid_1_vol'].fillna(0).sum(), nm['ask_1_vol'].fillna(0).sum()
    book_imb = float((bidv - askv) / (bidv + askv)) if (bidv + askv) > 0 else None
    atm_iv_series = nm[(nm['dte'] > 3) & (nm['dte'] < 20) & nm['iv'].notna() & (nm['iv'] > 0)]['iv']
    atm_iv = float(atm_iv_series.median()) if len(atm_iv_series) else None
    flow = (snap_ts, spot, float(oi.sum()), float(vol.sum()), oi_c, oi_p, vol_c, vol_p,
            (oi_p / oi_c) if oi_c > 0 else None,
            (vol_p / vol_c) if vol_c > 0 else None,
            atm_iv, book_imb, float(nm['open_interest'].fillna(0).sum()), int(len(b)))
    # near-money per-strike OI rows (|m-1| <= 0.25)
    near = b[(b['m'] - 1).abs() <= 0.25]
    strikes = [(snap_ts, r['expiry'], float(r['dte']), float(r['strike']), r['option_type'],
                float(r['open_interest']) if pd.notna(r['open_interest']) else None,
                float(r['volume']) if pd.notna(r['volume']) else None,
                float(r['iv']) if pd.notna(r['iv']) else None)
               for _, r in near.iterrows()]
    return flow, strikes


def trailing_rv_24h(conn):
    """Noise-robust-ish realized vol from persistent iv_snapshots spot history (last 24h)."""
    out = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    for ex in VENUES:
        s = pd.read_sql_query(
            "SELECT ts, spot FROM iv_snapshots WHERE exchange=? AND ts>=? AND spot>0 "
            "GROUP BY ts ORDER BY ts", conn, params=(ex, cutoff))
        if len(s) < 6:
            continue
        s['ts'] = pd.to_datetime(s['ts'], utc=True)
        ser = s.set_index('ts')['spot'].resample('30min').last().dropna()
        if len(ser) < 4:
            continue
        r = np.log(ser / ser.shift(1)).dropna()
        out[ex] = float(r.std() * np.sqrt(525600 / 30))  # annualize 30-min sampling
    return out


def maybe_prune(conn):
    """Once/day: delete options_data older than RETENTION_DAYS (no VACUUM on 1-CPU VPS)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
    n = conn.execute("DELETE FROM options_data WHERE timestamp < ?", (cutoff,)).rowcount
    conn.commit()
    if n:
        logger.info(f"Pruned {n} options_data rows older than {RETENTION_DAYS}d.")


def run_cycle(dry_run=False):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("PRAGMA busy_timeout=15000")
        df, snap_ts = load_aligned_snapshot(conn)
        if df is None:
            logger.warning("No snapshot data available.")
            return
        trv = trailing_rv_24h(conn)
        iv_rows = compute_iv_rows(df, snap_ts, trv)
        flow, strikes = compute_bybit_rows(df, snap_ts)

        if dry_run:
            logger.info(f"[DRY] snapshot {snap_ts} | venues {sorted(df['exchange'].unique())}")
            logger.info(f"[DRY] iv_snapshots rows ({len(iv_rows)}):")
            for r in iv_rows:
                logger.info("   ex=%s tenor=%dd dte=%.1f spot=%.1f atm_iv=%.3f "
                            "put90=%s call110=%s rr=%s fly=%s rv24h=%s"
                            % (r[1], r[3], r[4], r[2], r[6],
                               f"{r[7]:.3f}" if r[7] else None,
                               f"{r[10]:.3f}" if r[10] else None,
                               f"{r[11]:.3f}" if r[11] else None,
                               f"{r[12]:.3f}" if r[12] else None,
                               f"{r[13]:.3f}" if r[13] else None))
            if flow:
                logger.info("[DRY] bybit_flow: spot=%.1f oi=%.0f vol=%.0f pcr_oi=%s "
                            "atm_iv=%s book_imb=%s strikes=%d"
                            % (flow[1], flow[2], flow[3],
                               f"{flow[8]:.2f}" if flow[8] else None,
                               f"{flow[10]:.3f}" if flow[10] else None,
                               f"{flow[11]:.3f}" if flow[11] else None, len(strikes)))
            return

        if iv_rows:
            conn.executemany(
                "INSERT INTO iv_snapshots (ts,exchange,spot,tenor_target,dte_actual,atm_strike,"
                "atm_iv,put_iv_90,put_iv_95,call_iv_105,call_iv_110,rr_25,fly,rv_trailing_24h,"
                "n_contracts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", iv_rows)
        if flow:
            conn.execute(
                "INSERT INTO bybit_flow (ts,spot,total_oi,total_volume,oi_calls,oi_puts,"
                "vol_calls,vol_puts,pcr_oi,pcr_vol,atm_iv,book_imb,near_money_oi,n_contracts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", flow)
        if strikes:
            conn.executemany(
                "INSERT INTO bybit_oi_strikes (ts,expiry,dte,strike,opt_type,oi,volume,iv) "
                "VALUES (?,?,?,?,?,?,?,?)", strikes)
        conn.commit()
        logger.info(f"Wrote {len(iv_rows)} iv_snapshots + bybit_flow({1 if flow else 0}) "
                    f"+ {len(strikes)} oi_strikes @ {snap_ts}")


def main():
    init_db()
    logger.info("Analytics collector started (interval=%ds).", INTERVAL)
    last_prune = None
    while True:
        try:
            run_cycle()
            today = datetime.now(timezone.utc).date()
            if last_prune != today:
                with sqlite3.connect(DB_FILE) as conn:
                    conn.execute("PRAGMA busy_timeout=15000")
                    maybe_prune(conn)
                last_prune = today
        except Exception as e:
            logger.error(f"cycle error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        init_db()
        run_cycle(dry_run=True)
    else:
        main()
