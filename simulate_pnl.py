import sqlite3
import pandas as pd
import numpy as np

def run_pnl_simulation():
    conn = sqlite3.connect('/root/opt21/data.sqlite')
    
    query = """
        SELECT timestamp, exchange, expiry, option_type, strike, bid_1, ask_1
        FROM options_data
        WHERE bid_1 > 0 AND ask_1 > 0
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("No data available.")
        return

    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df['mid'] = (df['bid_1'] + df['ask_1']) / 2
    df['spread'] = df['ask_1'] - df['bid_1']
    
    # Create unified pair index
    df['unified_pair'] = df['expiry'] + '-' + df['strike'].astype(str) + '-' + df['option_type']
    
    pivot = df.pivot_table(
        index=['timestamp', 'unified_pair'],
        columns='exchange',
        values=['bid_1', 'ask_1', 'mid', 'spread']
    ).reset_index()
    
    pivot.columns = ['_'.join(col).strip('_') for col in pivot.columns.values]
    pivot = pivot.sort_values(['unified_pair', 'timestamp'])
    
    CAPITAL_PER_EXCHANGE = 100
    TRADE_SIZE = 0.05 
    
    open_trades = []
    closed_trades = []
    
    total_realized_pnl = 0
    trade_id_counter = 1
    
    for pair, group in pivot.groupby('unified_pair'):
        group = group.ffill().dropna()
        
        in_position = False
        trade = {}
        
        for _, row in group.iterrows():
            aevo_bid, aevo_ask = row['bid_1_AEVO'], row['ask_1_AEVO']
            deri_bid, deri_ask = row['bid_1_DERIVE'], row['ask_1_DERIVE']
            aevo_mid, deri_mid = row['mid_AEVO'], row['mid_DERIVE']
            aevo_spread, deri_spread = row['spread_AEVO'], row['spread_DERIVE']
            
            if not in_position:
                edge = 0
                action = None
                wide = None
                
                if deri_spread > aevo_spread * 2 and deri_spread > 5.0 and aevo_mid > deri_bid and aevo_mid < deri_ask:
                    edge = min(abs(deri_mid - aevo_mid), (deri_ask - aevo_mid), (aevo_mid - deri_bid))
                    wide = 'Derive'
                elif aevo_spread > deri_spread * 2 and aevo_spread > 5.0 and deri_mid > aevo_bid and deri_mid < aevo_ask:
                    edge = min(abs(aevo_mid - deri_mid), (aevo_ask - deri_mid), (deri_mid - aevo_bid))
                    wide = 'Aevo'
                    
                if edge > 20: # Threshold
                    in_position = True
                    trade = {
                        'Trade ID': trade_id_counter,
                        'Pair': pair,
                        'Entry Time': row['timestamp'],
                        'Wide Exchange': wide,
                        'Entry Edge (per 1 ETH)': edge,
                        'Projected Profit ($)': edge * TRADE_SIZE,
                        'Entry Aevo Mid': aevo_mid,
                        'Entry Deri Mid': deri_mid
                    }
                    trade_id_counter += 1
            else:
                current_gap = abs(aevo_mid - deri_mid)
                if current_gap < 5.0:
                    trade['Exit Time'] = row['timestamp']
                    duration = (trade['Exit Time'] - trade['Entry Time']).total_seconds() / 60
                    trade['Duration (mins)'] = duration
                    trade['Status'] = 'CLOSED'
                    
                    entry_gap = abs(trade['Entry Aevo Mid'] - trade['Entry Deri Mid'])
                    actual_pnl = (entry_gap - current_gap) * TRADE_SIZE
                    
                    trade['Realized PnL ($)'] = actual_pnl
                    total_realized_pnl += actual_pnl
                    
                    closed_trades.append(trade)
                    in_position = False
                    trade = {}
                    
        if in_position:
            last_row = group.iloc[-1]
            current_gap = abs(last_row['mid_AEVO'] - last_row['mid_DERIVE'])
            entry_gap = abs(trade['Entry Aevo Mid'] - trade['Entry Deri Mid'])
            floating_pnl = (entry_gap - current_gap) * TRADE_SIZE
            
            trade['Status'] = 'OPEN'
            trade['Current Floating PnL ($)'] = floating_pnl
            trade['Current Gap'] = current_gap
            open_trades.append(trade)

    print("="*50)
    print("SIMULATION RESULTS (Capital: $100/exchange, Size: 0.05 ETH)")
    print("="*50)
    print(f"Total Signals Fired: {len(closed_trades) + len(open_trades)}")
    print(f"Trades Closed (Spread converged): {len(closed_trades)}")
    print(f"Trades Currently Open: {len(open_trades)}")
    print(f"Total Realized Profit: ${total_realized_pnl:.2f}\n")
    
    if closed_trades:
        print("--- CLOSED TRADES ---")
        df_closed = pd.DataFrame(closed_trades)
        print(df_closed[['Trade ID', 'Pair', 'Duration (mins)', 'Projected Profit ($)', 'Realized PnL ($)']].to_string())
    
    if open_trades:
        print("\n--- OPEN TRADES (Awaiting Convergence) ---")
        df_open = pd.DataFrame(open_trades)
        print(df_open[['Trade ID', 'Pair', 'Entry Time', 'Projected Profit ($)', 'Current Floating PnL ($)']].to_string())

if __name__ == "__main__":
    run_pnl_simulation()
