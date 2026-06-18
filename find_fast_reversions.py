import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def analyze():
    print("Connecting to DB to find 10-15min micro-reversions...")
    conn = sqlite3.connect(DB_PATH)
    
    # We need timestamp, exchange, expiry, strike, opt_type, mid
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
    
    # We round to nearest minute for easier merging
    df['time_min'] = df['timestamp'].dt.floor('Min')
    
    # Filter for active options only (DTE < 30)
    df['dte'] = (pd.to_datetime(df['expiry'], utc=True) - df['timestamp']).dt.total_seconds() / 86400.0
    df = df[df['dte'] < 30]
    
    aevo = df[df['exchange'] == 'AEVO'].copy()
    deri = df[df['exchange'] == 'DERIVE'].copy()
    
    print("Merging timelines...")
    merged = pd.merge(
        aevo, deri,
        on=['time_min', 'expiry', 'strike', 'option_type'],
        suffixes=('_a', '_d')
    )
    
    if merged.empty:
        print("No overlapping data.")
        return
        
    merged = merged.sort_values('time_min')
    
    # Calculate price difference
    merged['price_diff'] = merged['mid_a'] - merged['mid_d']
    merged['diff_abs'] = merged['price_diff'].abs()
    
    print("\nLooking for rapid price divergences that close within 15 minutes...")
    
    # We will simulate a simple strategy over the historical data:
    # Entry: when diff_abs > Threshold (e.g. $10)
    # Exit: when diff_abs < 2 (converged) OR 15 minutes pass.
    # We will test thresholds: $5, $10, $15, $20
    
    thresholds = [5, 10, 15, 20]
    
    for thresh in thresholds:
        print(f"\n--- Testing Entry Threshold: ${thresh} ---")
        
        # Group by the option
        grouped = merged.groupby(['expiry', 'option_type', 'strike'])
        
        total_trades = 0
        profitable_trades = 0
        total_pnl = 0
        durations = []
        
        for name, group in grouped:
            in_trade = False
            entry_diff = 0
            entry_time = None
            
            for idx, row in group.iterrows():
                if not in_trade:
                    if row['diff_abs'] >= thresh:
                        in_trade = True
                        entry_diff = row['diff_abs']
                        entry_time = row['time_min']
                else:
                    duration_mins = (row['time_min'] - entry_time).total_seconds() / 60.0
                    
                    # Exit condition: Converged (< $2) OR 15 mins passed
                    if row['diff_abs'] <= 2.0 or duration_mins >= 15:
                        pnl = entry_diff - row['diff_abs']
                        
                        total_trades += 1
                        total_pnl += pnl
                        durations.append(duration_mins)
                        
                        if pnl > 0:
                            profitable_trades += 1
                            
                        in_trade = False
                        
        if total_trades > 0:
            win_rate = profitable_trades / total_trades * 100
            avg_pnl = total_pnl / total_trades
            avg_duration = np.mean(durations)
            print(f"Total Trades: {total_trades}")
            print(f"Win Rate: {win_rate:.1f}%")
            print(f"Avg PnL per trade: ${avg_pnl:.2f}")
            print(f"Avg Hold Time: {avg_duration:.1f} minutes")
            print(f"Estimated APY / Return potential: VERY HIGH" if win_rate > 55 and avg_pnl > 2 else "Low/Negative")
        else:
            print("No trades triggered at this threshold.")

if __name__ == "__main__":
    analyze()
