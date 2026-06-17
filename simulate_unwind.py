import sqlite3
import pandas as pd

def run_simulation():
    conn = sqlite3.connect('data.sqlite')
    
    # Load all data
    query = """
        SELECT timestamp, exchange, symbol, expiry, option_type, strike, bid_1, ask_1
        FROM options_data
        WHERE bid_1 > 0 AND ask_1 > 0
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No data available for simulation.")
        return

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid'] = (df['bid_1'] + df['ask_1']) / 2
    df['spread'] = df['ask_1'] - df['bid_1']
    
    # Create unified pair symbol (ignoring exchange specific prefixes if any)
    df['pair'] = df['symbol'].apply(lambda x: "-".join(x.split('-')[1:]) if len(x.split('-')) >= 4 else x)
    
    # Pivot to compare Aevo and Derive
    pivot = df.pivot_table(
        index=['timestamp', 'pair'],
        columns='exchange',
        values=['bid_1', 'ask_1', 'mid', 'spread']
    ).reset_index()
    
    # Flatten multi-index
    pivot.columns = ['_'.join(col).strip('_') for col in pivot.columns.values]
    
    # Forward fill to handle missing timestamps
    pivot = pivot.sort_values(['pair', 'timestamp'])
    
    # Identify signals
    signals = []
    
    for pair, group in pivot.groupby('pair'):
        # Forward fill within the pair
        group = group.ffill().dropna()
        
        in_position = False
        entry_time = None
        entry_profit = 0
        wide_exc = None
        
        for _, row in group.iterrows():
            if not in_position:
                aevo_bid, aevo_ask = row['bid_1_AEVO'], row['ask_1_AEVO']
                deri_bid, deri_ask = row['bid_1_DERIVE'], row['ask_1_DERIVE']
                aevo_mid, deri_mid = row['mid_AEVO'], row['mid_DERIVE']
                aevo_spread, deri_spread = row['spread_AEVO'], row['spread_DERIVE']
                
                # Derive Wide
                if deri_spread > aevo_spread * 2 and deri_spread > 5.0:
                    if aevo_mid > deri_bid and aevo_mid < deri_ask:
                        edge = min(abs(deri_mid - aevo_mid), (deri_ask - aevo_mid), (aevo_mid - deri_bid))
                        if edge > 20:
                            in_position = True
                            entry_time = row['timestamp']
                            entry_profit = edge
                            wide_exc = 'Derive'
                            continue
                
                # Aevo Wide
                if aevo_spread > deri_spread * 2 and aevo_spread > 5.0:
                    if deri_mid > aevo_bid and deri_mid < aevo_ask:
                        edge = min(abs(aevo_mid - deri_mid), (aevo_ask - deri_mid), (deri_mid - aevo_bid))
                        if edge > 20:
                            in_position = True
                            entry_time = row['timestamp']
                            entry_profit = edge
                            wide_exc = 'Aevo'
                            continue
            else:
                # We are in a position, check if prices aligned
                aevo_mid, deri_mid = row['mid_AEVO'], row['mid_DERIVE']
                if abs(aevo_mid - deri_mid) < 5.0: # Unwind threshold
                    exit_time = row['timestamp']
                    duration = (exit_time - entry_time).total_seconds() / 60
                    signals.append({
                        'Pair': pair,
                        'Entry Time': entry_time,
                        'Exit Time': exit_time,
                        'Duration (mins)': duration,
                        'Initial Edge ($)': entry_profit,
                        'Wide Exchange': wide_exc
                    })
                    in_position = False
                    
    results = pd.DataFrame(signals)
    if not results.empty:
        print(f"Found {len(results)} completed arbitrage cycles:")
        print(results[['Pair', 'Duration (mins)', 'Initial Edge ($)', 'Wide Exchange']].to_string())
        print(f"\nAverage Unwind Time: {results['Duration (mins)'].mean():.1f} minutes")
    else:
        print("No completed cycles found (either no signals or prices haven't aligned yet).")

if __name__ == "__main__":
    run_simulation()
