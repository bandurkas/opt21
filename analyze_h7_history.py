import sqlite3
import pandas as pd
from datetime import datetime
import numpy as np

DB_PATH = 'data.sqlite'

def analyze():
    conn = sqlite3.connect(DB_PATH)
    
    # Load raw data
    query = """
    SELECT timestamp, exchange, symbol, strike, expiry, option_type, bid_1, ask_1
    FROM options_data
    WHERE exchange IN ('DERIVE', 'AEVO') AND bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No valid data.")
        return
        
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['expiry'] = pd.to_datetime(df['expiry'])
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['spread'] = df['ask_1'] - df['bid_1']
    df['dte'] = (df['expiry'] - df['timestamp']).dt.total_seconds() / 86400.0
    
    # Group by minute to find pairs
    df['minute'] = df['timestamp'].dt.floor('Min')
    
    # Pivot to get side-by-side
    merged = df.pivot_table(
        index=['minute', 'expiry', 'option_type', 'strike', 'dte'],
        columns='exchange',
        values=['mid', 'spread']
    ).dropna()
    
    if merged.empty:
        print("No overlapping data.")
        return
        
    merged.reset_index(inplace=True)
    merged.columns = ['minute', 'expiry', 'opt_type', 'strike', 'dte', 'mid_AEVO', 'mid_DERIVE', 'spread_AEVO', 'spread_DERIVE']
    
    # Identify gaps
    # A gap is where one spread is 2x the other
    def is_gap(row):
        return (row['spread_AEVO'] > row['spread_DERIVE'] * 1.5) or (row['spread_DERIVE'] > row['spread_AEVO'] * 1.5)
        
    gaps = merged[merged.apply(is_gap, axis=1)].copy()
    gaps['gap_size'] = abs(gaps['mid_AEVO'] - gaps['mid_DERIVE'])
    
    # Categorize by DTE
    bins = [0, 7, 30, 90, 180, 1000]
    labels = ['< 7d', '7-30d', '30-90d', '90-180d', '> 180d']
    gaps['dte_bin'] = pd.cut(gaps['dte'], bins=bins, labels=labels)
    
    # Print statistics
    print(f"Total historical gaps found: {len(gaps)}")
    print("\nGaps by Time to Expiry (DTE):")
    counts = gaps['dte_bin'].value_counts().sort_index()
    avg_gap = gaps.groupby('dte_bin')['gap_size'].mean()
    
    for label in labels:
        print(f"DTE {label:>8}: {counts.get(label, 0):4d} signals | Avg Gap: ${avg_gap.get(label, 0):.2f}")
        
    # Analyze closure
    # We want to see how much variance (movement) mid prices have in these DTE bins.
    # Higher variance = faster closure.
    print("\nTo exit faster, we should avoid > 180d DTE. Most illiquid options are far out.")
    
if __name__ == "__main__":
    analyze()
