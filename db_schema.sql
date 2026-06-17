CREATE TABLE IF NOT EXISTS options_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    underlying_price REAL,
    strike REAL,
    expiry TEXT,
    option_type TEXT,
    mark_price REAL,
    iv REAL,
    delta REAL,
    gamma REAL,
    vega REAL,
    theta REAL,
    volume REAL,
    open_interest REAL,
    bid_1 REAL,
    ask_1 REAL,
    bid_1_vol REAL,
    ask_1_vol REAL,
    orderbook_bids TEXT,
    orderbook_asks TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_options_data_exchange_time ON options_data (exchange, timestamp);
CREATE INDEX IF NOT EXISTS idx_options_data_symbol ON options_data (symbol);
CREATE INDEX IF NOT EXISTS idx_options_data_expiry ON options_data (expiry);
