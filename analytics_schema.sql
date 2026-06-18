-- Analytics tables: compact, append-only, SURVIVE options_data pruning.
-- Phase-1 measurement infrastructure for Hypothesis A (VRP) and C (BYBIT flow).

CREATE TABLE IF NOT EXISTS iv_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,        -- aligned snapshot time (UTC ISO)
    exchange      TEXT NOT NULL,
    spot          REAL,
    tenor_target  INTEGER NOT NULL,    -- 7 / 14 / 30 days
    dte_actual    REAL,                -- actual DTE of chosen expiry
    atm_strike    REAL,
    atm_iv        REAL,                -- mean(call,put) IV at ATM strike
    put_iv_90     REAL,                -- put IV at moneyness ~0.90
    put_iv_95     REAL,
    call_iv_105   REAL,
    call_iv_110   REAL,
    rr_25         REAL,                -- risk reversal: call_iv_110 - put_iv_90
    fly           REAL,                -- butterfly: (put_iv_90+call_iv_110)/2 - atm_iv
    rv_trailing_24h REAL,              -- realized vol over last 24h of iv_snapshots spot
    n_contracts   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ivsnap_ts ON iv_snapshots (ts);
CREATE INDEX IF NOT EXISTS idx_ivsnap_ex_tenor ON iv_snapshots (exchange, tenor_target, ts);

CREATE TABLE IF NOT EXISTS bybit_flow (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    spot          REAL,
    total_oi      REAL,
    total_volume  REAL,
    oi_calls      REAL,
    oi_puts       REAL,
    vol_calls     REAL,
    vol_puts      REAL,
    pcr_oi        REAL,                -- put/call open-interest ratio
    pcr_vol       REAL,               -- put/call volume ratio
    atm_iv        REAL,
    book_imb      REAL,               -- near-money (Sum bid_vol - Sum ask_vol)/(Sum)
    near_money_oi REAL,
    n_contracts   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bybitflow_ts ON bybit_flow (ts);

CREATE TABLE IF NOT EXISTS bybit_oi_strikes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    expiry    TEXT,
    dte       REAL,
    strike    REAL,
    opt_type  TEXT,
    oi        REAL,
    volume    REAL,
    iv        REAL
);
CREATE INDEX IF NOT EXISTS idx_bybitoi_ts ON bybit_oi_strikes (ts);
