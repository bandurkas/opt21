import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
import json
import warnings

warnings.filterwarnings('ignore')

DB_PATH = 'data.sqlite'

def load_data():
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT timestamp, exchange, symbol, underlying_price, strike, expiry, option_type, 
           mark_price, iv, delta, gamma, vega, theta, bid_1, ask_1
    FROM options_data
    WHERE exchange IN ('DERIVE', 'AEVO')
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['expiry'] = pd.to_datetime(df['expiry'])
    df['strike'] = df['strike'].astype(float)
    df['iv'] = df['iv'].astype(float)
    df['underlying_price'] = df['underlying_price'].astype(float)
    df['mark_price'] = df['mark_price'].astype(float)
    
    # Calculate Time to Expiry (TTE) in days
    df['tte'] = (df['expiry'] - df['timestamp']).dt.total_seconds() / (24 * 3600)
    # Filter out expired or too close to expiry
    df = df[df['tte'] > 0]
    
    return df

def analyze_h1_lead_lag(df):
    print("=== H1: Lead-Lag Analysis (Derive vs Aevo) ===")
    
    # We want to see if one exchange leads the other in IV adjustments.
    # Group by 1-minute bins
    df['minute'] = df['timestamp'].dt.floor('Min')
    
    # Use all options for broad IV index if ATM data is sparse due to 10-item limit
    atm_df = df
        
    iv_series = atm_df.groupby(['minute', 'exchange'])['iv'].mean().unstack()
    iv_series = iv_series.dropna()
    
    if iv_series.empty or len(iv_series) < 5 or 'AEVO' not in iv_series.columns or 'DERIVE' not in iv_series.columns:
        print("Not enough overlapping time series data for cross-correlation.")
        return
        
    derive_iv = iv_series['DERIVE'].pct_change().dropna()
    aevo_iv = iv_series['AEVO'].pct_change().dropna()
    
    # Align data
    common_idx = derive_iv.index.intersection(aevo_iv.index)
    derive_iv = derive_iv.loc[common_idx]
    aevo_iv = aevo_iv.loc[common_idx]
    
    # Cross correlation
    lags = range(-5, 6)
    corrs = []
    for lag in lags:
        # If lag > 0, we shift Aevo forward (meaning Derive leads Aevo)
        corr = derive_iv.corr(aevo_iv.shift(lag))
        corrs.append(corr)
        
    print("Cross-correlation of IV percentage changes (Lags in minutes):")
    for lag, corr in zip(lags, corrs):
        print(f"Lag {lag:2d}: {corr:.4f}")
        
    best_lag = lags[np.argmax(corrs)]
    if best_lag > 0:
        print(f"Conclusion: Derive leads Aevo by ~{best_lag} minute(s).")
    elif best_lag < 0:
        print(f"Conclusion: Aevo leads Derive by ~{-best_lag} minute(s).")
    else:
        print("Conclusion: Changes are synchronous (Lag 0 is highest correlation).")
    print("-" * 50)

def analyze_h2_smile_error(df):
    print("=== H2: Volatility Smile Errors (Derive vs Aevo) ===")
    
    # We take the latest snapshot
    latest_time = df['timestamp'].max()
    # allow a 2-minute window
    latest_df = df[df['timestamp'] >= latest_time - pd.Timedelta(minutes=2)]
    
    # Group by Expiry, Option Type, Strike
    # We will find pairs where both Derive and Aevo have data
    merged = latest_df.pivot_table(
        index=['expiry', 'option_type', 'strike'], 
        columns='exchange', 
        values=['iv', 'mark_price', 'bid_1', 'ask_1', 'underlying_price']
    )
    
    # Drop rows where either Aevo or Derive is missing
    merged = merged.dropna(subset=[('iv', 'AEVO'), ('iv', 'DERIVE')])
    
    if merged.empty:
        print("No overlapping strikes/expiries found in the latest snapshot.")
        return

    merged['iv_diff'] = merged['iv']['AEVO'] - merged['iv']['DERIVE']
    merged['iv_diff_pct'] = (merged['iv_diff'] / merged['iv']['DERIVE']).abs() * 100
    
    # Find biggest mispricings
    top_mispricings = merged.sort_values(by='iv_diff_pct', ascending=False).head(5)
    
    print("Top 5 Largest IV Discrepancies (Smile Errors):")
    for idx, row in top_mispricings.iterrows():
        expiry, opt_type, strike = idx
        aevo_iv = row['iv']['AEVO']
        derive_iv = row['iv']['DERIVE']
        aevo_bid = row['bid_1']['AEVO']
        aevo_ask = row['ask_1']['AEVO']
        derive_bid = row['bid_1']['DERIVE']
        derive_ask = row['ask_1']['DERIVE']
        diff = row['iv_diff_pct']
        
        print(f"{expiry.date()} | {opt_type.upper()} | Strike {strike}")
        print(f"   AEVO IV: {aevo_iv:.4f} (Bid: {aevo_bid}, Ask: {aevo_ask})")
        print(f"   DERI IV: {derive_iv:.4f} (Bid: {derive_bid}, Ask: {derive_ask})")
        print(f"   Diff: {diff:.1f}%\n")
        
    print("-" * 50)

def main():
    print("Loading data...")
    df = load_data()
    print(f"Loaded {len(df)} records.")
    
    analyze_h1_lead_lag(df)
    analyze_h2_smile_error(df)

if __name__ == "__main__":
    main()
