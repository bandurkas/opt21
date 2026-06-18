import sqlite3
import pandas as pd
import numpy as np
import itertools
from datetime import datetime

def run_h3_grid_search():
    print("Loading data for H3 Grid Search...")
    conn = sqlite3.connect('data.sqlite')
    
    query = """
    SELECT timestamp, exchange, symbol, expiry, strike, option_type, 
           ask_1, bid_1, underlying_price
    FROM options_data
    WHERE bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['expiry'] = pd.to_datetime(df['expiry'], utc=True)
    df['mid'] = (df['bid_1'] + df['ask_1']) / 2
    df['dte'] = (df['expiry'] - df['timestamp']).dt.total_seconds() / 86400.0
    df['moneyness'] = df['strike'] / df['underlying_price']
    df['m_dist'] = (df['moneyness'] - 1.0).abs()
    
    print("Grouping into 10-minute snapshots...")
    df['time_snap'] = df['timestamp'].dt.floor('10min')
    df.sort_values('timestamp', inplace=True)
    latest_quotes = df.groupby(['time_snap', 'exchange', 'expiry', 'strike', 'option_type']).last().reset_index()
    
    print("Pivoting data to find 3-way gaps...")
    pivot_df = latest_quotes.pivot_table(
        index=['time_snap', 'expiry', 'strike', 'option_type'],
        columns='exchange',
        values='mid',
        aggfunc='last'
    ).reset_index()
    
    # Get average dte and m_dist for each option
    meta_df = latest_quotes.groupby(['time_snap', 'expiry', 'strike', 'option_type'])[['dte', 'm_dist']].mean().reset_index()
    pivot_df = pivot_df.merge(meta_df, on=['time_snap', 'expiry', 'strike', 'option_type'])
    
    exchanges = [c for c in ['AEVO', 'DERIVE', 'BYBIT'] if c in pivot_df.columns]
    
    opps = []
    
    for _, row in pivot_df.iterrows():
        for i in range(len(exchanges)):
            for j in range(i+1, len(exchanges)):
                ex1, ex2 = exchanges[i], exchanges[j]
                mid1, mid2 = row[ex1], row[ex2]
                
                if pd.notna(mid1) and pd.notna(mid2):
                    gap = abs(mid1 - mid2)
                    if gap >= 2.0:
                        opps.append({
                            'time_snap': row['time_snap'],
                            'expiry': row['expiry'],
                            'strike': row['strike'],
                            'option_type': row['option_type'],
                            'dte': row['dte'],
                            'm_dist': row['m_dist'],
                            'gap': gap
                        })
                        
    base_opps = pd.DataFrame(opps)
    if base_opps.empty:
        print("No base H3 opportunities found.")
        return
        
    # Filter 1 signal per day per symbol
    base_opps['date'] = base_opps['time_snap'].dt.date
    base_opps.sort_values('gap', ascending=False, inplace=True)
    base_opps.drop_duplicates(subset=['date', 'expiry', 'strike', 'option_type'], inplace=True)
    
    print(f"Base superset of H3 anomalies: {len(base_opps)}")
    
    print("\nRunning Grid Search for H3 (Taker) Parameters...")
    min_dte_list = [0.5, 1, 3, 7]
    max_m_dist_list = [0.03, 0.05, 0.10, 0.15]
    min_gap_list = [5.0, 10.0, 15.0, 20.0]
    
    results = []
    
    for dte, m_dist, gap in itertools.product(min_dte_list, max_m_dist_list, min_gap_list):
        mask = (
            (base_opps['dte'] >= dte) & 
            (base_opps['dte'] < 30) &
            (base_opps['m_dist'] <= m_dist) & 
            (base_opps['gap'] >= gap)
        )
        
        subset = base_opps[mask]
        total_signals = len(subset)
        if total_signals == 0:
            continue
            
        # For H3, we assume 100% fill rate because it's a TAKER strategy 
        # (if the gap exists in the orderbook and volume is sufficient)
        # We assume net profit per trade is roughly (Gap - 3.0) to account for taker fees and slippage
        avg_gap = subset['gap'].mean()
        net_profit_per_trade = avg_gap - 3.0 
        total_profit = total_signals * net_profit_per_trade
        
        results.append({
            'min_dte': dte,
            'max_m_dist': m_dist,
            'min_gap': gap,
            'total_signals': total_signals,
            'avg_gap': avg_gap,
            'net_profit': total_profit
        })
        
    res_df = pd.DataFrame(results)
    res_df = res_df[res_df['total_signals'] >= 3] # Filter outliers
    res_df.sort_values('net_profit', ascending=False, inplace=True)
    
    print("\nTop 15 Parameter Combinations by Total Net Profit:")
    for i, row in res_df.head(15).iterrows():
        print(f"DTE>={row['min_dte']:.1f}, M_DIST<={row['max_m_dist']:.2f}, GAP>={row['min_gap']:.1f} | "
              f"Signals: {row['total_signals']:.0f}, AvgGap: ${row['avg_gap']:.1f} | Total Net Profit: ${row['net_profit']:.1f}")

if __name__ == "__main__":
    run_h3_grid_search()
