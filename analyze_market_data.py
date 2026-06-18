import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def analyze():
    print("Connecting to DB...")
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT timestamp, exchange, expiry, strike, option_type, bid_1, ask_1, iv
    FROM options_data
    WHERE bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No valid data.")
        return
        
    print(f"Loaded {len(df)} rows.")
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['expiry'] = pd.to_datetime(df['expiry'], utc=True)
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['spread'] = df['ask_1'] - df['bid_1']
    df['spread_pct'] = df['spread'] / df['mid'] * 100
    
    # Calculate DTE (Days to Expiry)
    df['dte'] = (df['expiry'] - df['timestamp']).dt.total_seconds() / 86400.0
    
    aevo = df[df['exchange'] == 'AEVO'].sort_values('timestamp')
    deri = df[df['exchange'] == 'DERIVE'].sort_values('timestamp')
    
    print(f"Aevo rows: {len(aevo)}, Derive rows: {len(deri)}")
    
    print("Merging data...")
    # Exact merge on 5-minute rounded timestamps for simplicity
    aevo['time_bin'] = aevo['timestamp'].dt.floor('5Min')
    deri['time_bin'] = deri['timestamp'].dt.floor('5Min')
    
    merged = pd.merge(
        aevo, deri,
        on=['time_bin', 'expiry', 'strike', 'option_type'],
        suffixes=('_a', '_d')
    )
    
    print(f"Merged pairs: {len(merged)}")
    
    if merged.empty:
        print("No overlapping data found.")
        return

    # 1. Spread Analysis by DTE
    bins = [0, 7, 30, 90, 1000]
    labels = ['< 7d', '7-30d', '30-90d', '> 90d']
    merged['dte_bin'] = pd.cut(merged['dte_a'], bins=bins, labels=labels)
    
    print("\n--- 1. Average Spread Width by DTE ---")
    spread_stats = merged.groupby('dte_bin')[['spread_pct_a', 'spread_pct_d']].mean()
    print(spread_stats.round(2))
    
    # 2. Smile Errors (IV Differences)
    merged['iv_diff'] = (merged['iv_a'] - merged['iv_d']).abs()
    merged['iv_diff_pct'] = merged['iv_diff'] / merged['iv_d'] * 100
    
    print("\n--- 2. Smile Errors (IV Diff > 20%) Frequency by DTE ---")
    large_diffs = merged[merged['iv_diff_pct'] > 20.0]
    freq = large_diffs.groupby('dte_bin').size() / merged.groupby('dte_bin').size() * 100
    print(freq.round(2).astype(str) + '%')
    
    # 3. Market Inefficiencies (Arbitrage opportunities)
    # AEVO spread is at least 1.5x DERI, and DERI mid is inside AEVO spread
    def is_maker_arb(row):
        cond1 = row['spread_a'] > row['spread_d'] * 1.5
        cond2 = (row['mid_d'] > row['bid_1_a']) and (row['mid_d'] < row['ask_1_a'])
        if cond1 and cond2: return 'AEVO_WIDE'
        
        cond3 = row['spread_d'] > row['spread_a'] * 1.5
        cond4 = (row['mid_a'] > row['bid_1_d']) and (row['mid_a'] < row['ask_1_d'])
        if cond3 and cond4: return 'DERI_WIDE'
        
        return 'NONE'
        
    merged['arb_type'] = merged.apply(is_maker_arb, axis=1)
    arbs = merged[merged['arb_type'] != 'NONE']
    
    print("\n--- 3. Maker Arbitrage Frequency by DTE ---")
    arb_freq = arbs.groupby('dte_bin').size()
    total_bins = merged.groupby('dte_bin').size()
    print((arb_freq / total_bins * 100).round(2).astype(str) + '%')
    
    # Analyze the reversion time of large gaps (How fast do they close?)
    # We will look at specific strikes over time
    print("\n--- 4. Reversion Tracking ---")
    print("Tracking how fast extreme spreads revert to normal...")
    
    # Group by option
    # For options that had a wide spread, how long until it wasn't wide?
    # This requires a more complex iteration
    
if __name__ == "__main__":
    analyze()
