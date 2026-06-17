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
    
    # Calculate Spread
    df['spread'] = df['ask_1'] - df['bid_1']
    df['mid_price'] = (df['ask_1'] + df['bid_1']) / 2
    
    # Filter valid quotes
    df = df[(df['spread'] > 0) & (df['bid_1'] > 0)]
    return df

def validate_h7_persistence(df):
    print("=== VALIDATION: H7 Maker Arbitrage Persistence ===")
    
    # Create 1-minute bins to align Aevo and Derive
    df['minute'] = df['timestamp'].dt.floor('Min')
    
    # Pivot by minute, expiry, type, strike
    merged = df.pivot_table(
        index=['minute', 'expiry', 'option_type', 'strike'], 
        columns='exchange', 
        values=['mid_price', 'spread', 'bid_1', 'ask_1', 'mark_price']
    )
    merged = merged.dropna(subset=[('mid_price', 'AEVO'), ('mid_price', 'DERIVE')])
    
    if merged.empty:
        print("Not enough overlapping time-series data for validation.")
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
                edge = abs(aevo_mid - deri_mid)
                wide_exchange = 'AEVO'
                
        if deri_spread > aevo_spread * 2 and deri_spread > 5.0:
            if aevo_mid > deri_bid and aevo_mid < deri_ask:
                edge = abs(deri_mid - aevo_mid)
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
        print("No consistent opportunities found.")
        return
        
    # Group by instrument to see how long the edge persists
    grouped = opp_df.groupby(['expiry', 'type', 'strike', 'wide_exchange']).agg(
        minutes_active=('minute', 'nunique'),
        avg_edge=('edge', 'mean'),
        max_edge=('edge', 'max')
    ).reset_index()
    
    grouped = grouped.sort_values('minutes_active', ascending=False)
    
    print("Top Validated Opportunities (Persistence over time):")
    print("-" * 60)
    for _, row in grouped.head(15).iterrows():
        print(f"{row['expiry']} | {row['type'].upper()} | Strike {row['strike']}")
        print(f"   Wide Exchange : {row['wide_exchange']}")
        print(f"   Time Active   : {row['minutes_active']} minute(s)")
        print(f"   Average Edge  : ${row['avg_edge']:.2f}")
        print("-" * 60)
        
    print(f"Total unique instruments with persistent edge (>1 min): {len(grouped[grouped['minutes_active'] > 1])}")

if __name__ == "__main__":
    print("Loading continuous database...")
    df = load_all_data()
    validate_h7_persistence(df)
