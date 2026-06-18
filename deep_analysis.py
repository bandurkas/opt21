import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def analyze():
    print("Connecting to DB for Deep Analysis...")
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT timestamp, exchange, expiry, strike, option_type, underlying_price, bid_1, ask_1, iv
    FROM options_data
    WHERE bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No valid data.")
        return
        
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['expiry'] = pd.to_datetime(df['expiry'], utc=True)
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['spread'] = df['ask_1'] - df['bid_1']
    df['spread_pct'] = df['spread'] / df['mid'] * 100
    df['dte'] = (df['expiry'] - df['timestamp']).dt.total_seconds() / 86400.0
    
    # Calculate Moneyness
    # Moneyness = Strike / Underlying
    df['moneyness'] = df['strike'] / df['underlying_price']
    
    # Categorize Moneyness (Call/Put agnostic for now, just relative to spot)
    def categorize_moneyness(row):
        m = row['moneyness']
        opt = row['option_type']
        if opt == 'C':
            if m < 0.95: return 'ITM'
            elif m > 1.05: return 'OTM'
            else: return 'ATM'
        else: # Puts
            if m > 1.05: return 'ITM'
            elif m < 0.95: return 'OTM'
            else: return 'ATM'
            
    df['moneyness_cat'] = df.apply(categorize_moneyness, axis=1)
    
    # Filter only DTE < 30 for deeper analysis (since we know >30 is illiquid)
    df_near = df[df['dte'] < 30].copy()
    
    aevo = df_near[df_near['exchange'] == 'AEVO'].sort_values('timestamp')
    deri = df_near[df_near['exchange'] == 'DERIVE'].sort_values('timestamp')
    
    aevo['time_bin'] = aevo['timestamp'].dt.floor('15Min')
    deri['time_bin'] = deri['timestamp'].dt.floor('15Min')
    
    merged = pd.merge(
        aevo, deri,
        on=['time_bin', 'expiry', 'strike', 'option_type', 'moneyness_cat'],
        suffixes=('_a', '_d')
    )
    
    print("\n--- 1. Call vs Put Inefficiency (DTE < 30) ---")
    cp_stats = merged.groupby('option_type')[['spread_pct_a', 'spread_pct_d']].mean()
    print(cp_stats.round(2))
    
    print("\n--- 2. Moneyness (ITM / ATM / OTM) Analysis ---")
    m_stats = merged.groupby('moneyness_cat')[['spread_pct_a', 'spread_pct_d']].mean()
    print(m_stats.round(2))
    
    print("\n--- 3. Systematic Pricing Premium ---")
    merged['price_diff_pct'] = (merged['mid_a'] - merged['mid_d']) / merged['mid_d'] * 100
    merged['aevo_premium'] = merged['price_diff_pct'] > 0
    premium_freq = merged['aevo_premium'].mean() * 100
    print(f"Aevo prices options higher than Derive {premium_freq:.1f}% of the time.")
    print("Average Price Discrepancy (Aevo vs Derive):")
    print(merged.groupby('option_type')['price_diff_pct'].mean().round(2).astype(str) + "%")
    
    print("\n--- 4. Time of Day Volatility ---")
    merged['hour'] = merged['timestamp_a'].dt.hour
    hourly_spread = merged.groupby('hour')[['spread_pct_a', 'spread_pct_d']].mean()
    print("Hourly Spreads (Top 5 Widest Hours UTC):")
    print(hourly_spread.mean(axis=1).sort_values(ascending=False).head(5).round(2))
    print("Hourly Spreads (Top 5 Tightest Hours UTC):")
    print(hourly_spread.mean(axis=1).sort_values(ascending=True).head(5).round(2))
    
    print("\n--- 5. Extreme Smile Error Reversion Analysis ---")
    # How long does an IV difference > 15% last?
    merged['iv_diff'] = (merged['iv_a'] - merged['iv_d']).abs()
    merged['iv_diff_pct'] = merged['iv_diff'] / merged['iv_d'] * 100
    large_diffs = merged[merged['iv_diff_pct'] > 15.0]
    print(f"Found {len(large_diffs)} periods with IV Diff > 15%.")
    
    if not large_diffs.empty:
        # Check if they are predominantly OTM Puts or Calls?
        print("Breakdown of large IV anomalies:")
        print(large_diffs.groupby(['option_type', 'moneyness_cat']).size())
        
if __name__ == "__main__":
    analyze()
