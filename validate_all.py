import sqlite3
import pandas as pd
import numpy as np
import warnings

warnings.filterwarnings('ignore')

DB_PATH = 'data.sqlite'

def load_all_data():
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT timestamp, exchange, symbol, underlying_price, strike, expiry, option_type, 
           mark_price, iv, bid_1, ask_1, bid_1_vol, ask_1_vol
    FROM options_data
    WHERE exchange IN ('DERIVE', 'AEVO')
    ORDER BY timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['expiry'] = pd.to_datetime(df['expiry'])
    df['strike'] = df['strike'].astype(float)
    df['bid_1'] = df['bid_1'].astype(float)
    df['ask_1'] = df['ask_1'].astype(float)
    df['mark_price'] = df['mark_price'].astype(float)
    df['iv'] = df['iv'].astype(float)
    
    df['spread'] = df['ask_1'] - df['bid_1']
    df['mid_price'] = (df['ask_1'] + df['bid_1']) / 2
    
    # Minute bins for alignment
    df['minute'] = df['timestamp'].dt.floor('Min')
    
    return df

def validate_h1_lead_lag(df):
    print("\n" + "="*50)
    print("=== H1: VALIDATION OF LEAD-LAG (Rolling Window) ===")
    
    # Use all options to create an IV index per exchange per minute
    iv_series = df.groupby(['minute', 'exchange'])['iv'].mean().unstack()
    iv_series = iv_series.dropna()
    
    if len(iv_series) < 30:
        print("Not enough data to run rolling H1 validation. Need at least 30 minutes.")
        return

    derive_iv = iv_series['DERIVE'].pct_change().dropna()
    aevo_iv = iv_series['AEVO'].pct_change().dropna()
    
    common_idx = derive_iv.index.intersection(aevo_iv.index)
    derive_iv = derive_iv.loc[common_idx]
    aevo_iv = aevo_iv.loc[common_idx]
    
    # Rolling 60-minute windows (or use the whole set if less than a few hours)
    window_size = 60
    if len(common_idx) < window_size:
        window_size = len(common_idx) // 2

    lags = range(-5, 6)
    derive_leads_count = 0
    aevo_leads_count = 0
    neutral_count = 0
    
    # We will slide a window over the data to see how consistently one exchange leads
    for i in range(0, len(common_idx) - window_size, max(1, window_size // 4)):
        d_window = derive_iv.iloc[i:i+window_size]
        a_window = aevo_iv.iloc[i:i+window_size]
        
        corrs = []
        for lag in lags:
            corr = d_window.corr(a_window.shift(lag))
            corrs.append(corr)
            
        best_lag = lags[np.nanargmax(corrs)]
        if best_lag > 0:
            derive_leads_count += 1
        elif best_lag < 0:
            aevo_leads_count += 1
        else:
            neutral_count += 1
            
    total_windows = derive_leads_count + aevo_leads_count + neutral_count
    if total_windows > 0:
        print(f"Total rolling windows analyzed: {total_windows}")
        print(f"Derive led Aevo in {derive_leads_count} windows ({(derive_leads_count/total_windows)*100:.1f}%)")
        print(f"Aevo led Derive in {aevo_leads_count} windows ({(aevo_leads_count/total_windows)*100:.1f}%)")
        print(f"Synchronous in {neutral_count} windows ({(neutral_count/total_windows)*100:.1f}%)")
        
        if derive_leads_count > aevo_leads_count and derive_leads_count / total_windows > 0.6:
            print(">>> VERDICT H1: Confirmed. Derive is a consistent leading indicator.")
        elif aevo_leads_count > derive_leads_count and aevo_leads_count / total_windows > 0.6:
            print(">>> VERDICT H1: Confirmed. Aevo is a consistent leading indicator.")
        else:
            print(">>> VERDICT H1: Mixed. No clear leader over time.")
    else:
        print("Not enough data windows.")


def validate_h2_smile_errors(df):
    print("\n" + "="*50)
    print("=== H2: VALIDATION OF SMILE ERRORS PERSISTENCE ===")
    
    merged = df.pivot_table(
        index=['minute', 'expiry', 'option_type', 'strike'], 
        columns='exchange', 
        values='iv'
    ).dropna()
    
    if merged.empty:
        print("No overlapping strikes/expiries found for H2.")
        return
        
    merged['iv_diff'] = merged['AEVO'] - merged['DERIVE']
    merged['iv_diff_pct'] = (merged['iv_diff'] / merged['DERIVE']).abs() * 100
    
    # Find instances where IV differs by more than 20%
    significant = merged[merged['iv_diff_pct'] > 20].reset_index()
    
    if significant.empty:
        print("No significant IV smile discrepancies (>20%) found in dataset.")
        return
        
    # Group by instrument to track persistence
    grouped = significant.groupby(['expiry', 'option_type', 'strike']).agg(
        minutes_active=('minute', 'nunique'),
        avg_diff_pct=('iv_diff_pct', 'mean')
    ).reset_index()
    
    grouped = grouped.sort_values('minutes_active', ascending=False)
    
    print("Top Validated Smile Errors (Persistence over time):")
    for _, row in grouped.head(5).iterrows():
        print(f"{row['expiry'].date()} | {row['option_type'].upper()} | Strike {row['strike']}")
        print(f"   Time Active  : {row['minutes_active']} minute(s)")
        print(f"   Avg IV Diff  : {row['avg_diff_pct']:.1f}%")
        print("-" * 30)
        
    persistent_count = len(grouped[grouped['minutes_active'] >= 2])
    print(f"Total options with persistent smile errors (>=2 mins): {persistent_count}")
    if persistent_count > 0:
        print(">>> VERDICT H2: Confirmed. Smile mispricings are persistent and exploitable.")
    else:
        print(">>> VERDICT H2: Rejected. Mispricings are transient/glitches.")


def validate_h7_maker_arbitrage(df):
    print("\n" + "="*50)
    print("=== H7: VALIDATION OF MAKER ARBITRAGE PERSISTENCE ===")
    
    valid_df = df[(df['spread'] > 0) & (df['bid_1'] > 0)]
    merged = valid_df.pivot_table(
        index=['minute', 'expiry', 'option_type', 'strike'], 
        columns='exchange', 
        values=['mid_price', 'spread', 'bid_1', 'ask_1']
    )
    merged = merged.dropna(subset=[('mid_price', 'AEVO'), ('mid_price', 'DERIVE')])
    
    if merged.empty:
        print("Not enough overlapping time-series data for H7 validation.")
        return

    opportunities = []
    
    for idx, row in merged.iterrows():
        minute, expiry, opt_type, strike = idx
        
        aevo_bid, aevo_ask = row['bid_1']['AEVO'], row['ask_1']['AEVO']
        deri_bid, deri_ask = row['bid_1']['DERIVE'], row['ask_1']['DERIVE']
        aevo_mid, deri_mid = row['mid_price']['AEVO'], row['mid_price']['DERIVE']
        aevo_spread, deri_spread = row['spread']['AEVO'], row['spread']['DERIVE']
        
        edge = 0
        wide_exchange = None
        
        if aevo_spread > deri_spread * 2 and aevo_spread > 5.0:
            if deri_mid > aevo_bid and deri_mid < aevo_ask:
                edge = min(abs(aevo_mid - deri_mid), (aevo_ask - deri_mid), (deri_mid - aevo_bid))
                wide_exchange = 'AEVO'
                
        if deri_spread > aevo_spread * 2 and deri_spread > 5.0:
            if aevo_mid > deri_bid and aevo_mid < deri_ask:
                edge = min(abs(deri_mid - aevo_mid), (deri_ask - aevo_mid), (aevo_mid - deri_bid))
                wide_exchange = 'DERIVE'
                
        if edge > 10: # Minimum $10 edge to care
            opportunities.append({
                'minute': minute,
                'strike': strike, 
                'type': opt_type, 
                'expiry': expiry.date(),
                'wide_exchange': wide_exchange,
                'edge': edge
            })

    opp_df = pd.DataFrame(opportunities)
    if opp_df.empty:
        print("No consistent Maker Arbitrage opportunities found.")
        print(">>> VERDICT H7: Rejected. No significant edges.")
        return
        
    grouped = opp_df.groupby(['expiry', 'type', 'strike', 'wide_exchange']).agg(
        minutes_active=('minute', 'nunique'),
        avg_edge=('edge', 'mean')
    ).reset_index()
    
    grouped = grouped.sort_values('minutes_active', ascending=False)
    
    print("Top Validated Maker Arbitrage Opportunities:")
    for _, row in grouped.head(5).iterrows():
        print(f"{row['expiry']} | {row['type'].upper()} | Strike {row['strike']}")
        print(f"   Wide Exchange : {row['wide_exchange']}")
        print(f"   Time Active   : {row['minutes_active']} minute(s)")
        print(f"   Average Edge  : ${row['avg_edge']:.2f}")
        print("-" * 30)
        
    persistent_count = len(grouped[grouped['minutes_active'] >= 2])
    print(f"Total persistent opportunities (>=2 mins): {persistent_count}")
    if persistent_count > 0:
        print(">>> VERDICT H7: Confirmed. Wide spreads envelop mid-prices consistently.")
    else:
        print(">>> VERDICT H7: Rejected. Opportunities are transient.")


def main():
    print("Loading continuous database...")
    df = load_all_data()
    print(f"Total rows loaded: {len(df)}")
    
    validate_h1_lead_lag(df)
    validate_h2_smile_errors(df)
    validate_h7_maker_arbitrage(df)
    
    print("\nValidation framework ready. Run this tomorrow after collecting 12-24 hours of data.")

if __name__ == "__main__":
    main()
