import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def load_data():
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT timestamp, exchange, symbol, underlying_price, strike, expiry, option_type, 
           mark_price, iv, bid_1, ask_1, bid_1_vol, ask_1_vol
    FROM options_data
    WHERE exchange IN ('DERIVE', 'AEVO')
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

def analyze_h7_maker_arbitrage(df):
    print("=== H7: Maker Arbitrage / Wide Spreads Analysis (Derive vs Aevo) ===")
    
    # Take latest snapshot
    latest_time = df['timestamp'].max()
    latest_df = df[df['timestamp'] >= latest_time - pd.Timedelta(minutes=2)]
    
    merged = latest_df.pivot_table(
        index=['expiry', 'option_type', 'strike'], 
        columns='exchange', 
        values=['mid_price', 'spread', 'bid_1', 'ask_1', 'mark_price']
    )
    
    merged = merged.dropna(subset=[('mid_price', 'AEVO'), ('mid_price', 'DERIVE')])
    
    if merged.empty:
        print("No overlapping strikes/expiries found with valid bid/ask data.")
        return

    # To find maker arbitrage, we look for situations where the spread on Exchange A is huge,
    # and the mid-price on Exchange B is completely inside that spread, with enough margin.
    
    # Example: AEVO spread is $10. Derive mid is $5 away from Aevo's bid.
    # If we place a limit order on Aevo, we have a statistical edge.
    
    opportunities = []
    
    for idx, row in merged.iterrows():
        expiry, opt_type, strike = idx
        
        aevo_bid, aevo_ask = row['bid_1']['AEVO'], row['ask_1']['AEVO']
        deri_bid, deri_ask = row['bid_1']['DERIVE'], row['ask_1']['DERIVE']
        aevo_mid, deri_mid = row['mid_price']['AEVO'], row['mid_price']['DERIVE']
        aevo_spread, deri_spread = row['spread']['AEVO'], row['spread']['DERIVE']
        
        # Check if Aevo spread is anomalously wide compared to Derive
        if aevo_spread > deri_spread * 2 and aevo_spread > 5.0:
            edge = abs(aevo_mid - deri_mid)
            if deri_mid > aevo_bid and deri_mid < aevo_ask:
                opportunities.append({
                    'strike': strike, 'type': opt_type, 'expiry': expiry.date(),
                    'wide_exchange': 'AEVO', 'tight_exchange': 'DERIVE',
                    'wide_spread': aevo_spread, 'tight_spread': deri_spread,
                    'edge': edge
                })
                
        # Check if Derive spread is anomalously wide compared to Aevo
        if deri_spread > aevo_spread * 2 and deri_spread > 5.0:
            edge = abs(deri_mid - aevo_mid)
            if aevo_mid > deri_bid and aevo_mid < deri_ask:
                opportunities.append({
                    'strike': strike, 'type': opt_type, 'expiry': expiry.date(),
                    'wide_exchange': 'DERIVE', 'tight_exchange': 'AEVO',
                    'wide_spread': deri_spread, 'tight_spread': aevo_spread,
                    'edge': edge
                })

    opportunities = pd.DataFrame(opportunities)
    
    if opportunities.empty:
        print("No significant Maker Arbitrage edges found right now.")
    else:
        opportunities = opportunities.sort_values(by='edge', ascending=False).head(10)
        print("Top Maker Arbitrage Opportunities found:")
        print("-" * 60)
        for _, op in opportunities.iterrows():
            print(f"{op['expiry']} | {op['type'].upper()} | Strike {op['strike']}")
            print(f"   Wide Exchange : {op['wide_exchange']} (Spread: ${op['wide_spread']:.2f})")
            print(f"   Tight Exchange: {op['tight_exchange']} (Spread: ${op['tight_spread']:.2f})")
            print(f"   Maker Edge    : ${op['edge']:.2f} per contract")
            print("-" * 60)

def main():
    print("Loading data...")
    df = load_data()
    print(f"Loaded {len(df)} valid records with bid/ask data.")
    analyze_h7_maker_arbitrage(df)

if __name__ == "__main__":
    main()
