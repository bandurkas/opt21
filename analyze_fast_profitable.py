import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def analyze():
    print("Connecting to DB to analyze fastest closing trades...")
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT timestamp, exchange, expiry, strike, option_type, ask_1, bid_1, underlying_price
    FROM options_data
    WHERE bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No valid data.")
        return
        
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['spread_pct'] = (df['ask_1'] - df['bid_1']) / df['mid'] * 100
    df['time_min'] = df['timestamp'].dt.floor('Min')
    
    df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
    df['dte'] = (df['expiry_utc'] - df['timestamp']).dt.total_seconds() / 86400.0
    df = df[df['dte'] < 30] # current baseline filter
    
    aevo = df[df['exchange'] == 'AEVO'].copy()
    deri = df[df['exchange'] == 'DERIVE'].copy()
    
    merged = pd.merge(
        aevo, deri,
        on=['time_min', 'expiry', 'strike', 'option_type'],
        suffixes=('_a', '_d')
    ).sort_values('time_min')
    
    merged['diff_abs'] = (merged['mid_a'] - merged['mid_d']).abs()
    merged['moneyness'] = merged['strike'] / merged['underlying_price_a']
    
    def get_moneyness_cat(row):
        m = row['moneyness']
        opt = row['option_type']
        if opt == 'C':
            if m < 0.95: return 'ITM'
            elif m > 1.05: return 'OTM'
            else: return 'ATM'
        else:
            if m > 1.05: return 'ITM'
            elif m < 0.95: return 'OTM'
            else: return 'ATM'
            
    merged['moneyness_cat'] = merged.apply(get_moneyness_cat, axis=1)
    
    # We will simulate the H3 logic (entry >= 10, exit <= 2) and record the duration and features
    trades = []
    grouped = merged.groupby(['expiry', 'option_type', 'strike'])
    
    for name, group in grouped:
        in_trade = False
        entry_time = None
        entry_diff = 0
        entry_dte = 0
        entry_money_cat = ''
        entry_price = 0
        
        for idx, row in group.iterrows():
            if not in_trade:
                if row['diff_abs'] >= 10.0:
                    in_trade = True
                    entry_time = row['time_min']
                    entry_diff = row['diff_abs']
                    entry_dte = row['dte_a']
                    entry_money_cat = row['moneyness_cat']
                    entry_price = min(row['mid_a'], row['mid_d'])
            else:
                duration_mins = (row['time_min'] - entry_time).total_seconds() / 60.0
                if row['diff_abs'] <= 2.0 or duration_mins >= 60: # let's allow up to 60 mins to see the true tail
                    closed = (row['diff_abs'] <= 2.0)
                    trades.append({
                        'duration': duration_mins,
                        'closed_naturally': closed,
                        'entry_diff': entry_diff,
                        'dte': entry_dte,
                        'moneyness': entry_money_cat,
                        'price_level': entry_price
                    })
                    in_trade = False
                    
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        print("No trades found.")
        return
        
    print(f"Found {len(trades_df)} historical gap events (>= $10).")
    
    # Analyze by DTE
    print("\n--- Speed by DTE (Days to Expiry) ---")
    bins = [0, 3, 7, 14, 30]
    labels = ['< 3d', '3-7d', '7-14d', '14-30d']
    trades_df['dte_bin'] = pd.cut(trades_df['dte'], bins=bins, labels=labels)
    dte_stats = trades_df[trades_df['closed_naturally']].groupby('dte_bin')['duration'].mean()
    dte_count = trades_df.groupby('dte_bin').size()
    win_rate = trades_df.groupby('dte_bin')['closed_naturally'].mean() * 100
    for lbl in labels:
        print(f"DTE {lbl}: Avg Duration {dte_stats.get(lbl, 0):.1f} mins | Win Rate: {win_rate.get(lbl, 0):.1f}% | Total Signals: {dte_count.get(lbl, 0)}")
        
    # Analyze by Moneyness
    print("\n--- Speed by Moneyness ---")
    money_stats = trades_df[trades_df['closed_naturally']].groupby('moneyness')['duration'].mean()
    money_win = trades_df.groupby('moneyness')['closed_naturally'].mean() * 100
    money_count = trades_df.groupby('moneyness').size()
    for cat in ['ITM', 'ATM', 'OTM']:
        print(f"{cat}: Avg Duration {money_stats.get(cat, 0):.1f} mins | Win Rate: {money_win.get(cat, 0):.1f}% | Total Signals: {money_count.get(cat, 0)}")
        
    # Analyze by Gap Size (Entry Diff)
    print("\n--- Speed by Initial Gap Size ---")
    bins_gap = [10, 15, 20, 50]
    labels_gap = ['$10-15', '$15-20', '>$20']
    trades_df['gap_bin'] = pd.cut(trades_df['entry_diff'], bins=bins_gap, labels=labels_gap)
    gap_stats = trades_df[trades_df['closed_naturally']].groupby('gap_bin')['duration'].mean()
    gap_win = trades_df.groupby('gap_bin')['closed_naturally'].mean() * 100
    for lbl in labels_gap:
        print(f"Gap {lbl}: Avg Duration {gap_stats.get(lbl, 0):.1f} mins | Win Rate: {gap_win.get(lbl, 0):.1f}%")

if __name__ == "__main__":
    analyze()
