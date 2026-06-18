import sqlite3
import pandas as pd
import numpy as np

DB_PATH = 'data.sqlite'

def simulate_h3():
    conn = sqlite3.connect(DB_PATH)
    
    # 1. Fetch raw data
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
    df['time_min'] = df['timestamp'].dt.floor('Min')
    
    # Filter only last 6 hours
    max_time = df['time_min'].max()
    start_time = max_time - pd.Timedelta(hours=6)
    df = df[df['time_min'] >= start_time]
    
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
    df['dte'] = (df['expiry_utc'] - df['timestamp']).dt.total_seconds() / 86400.0
    df = df[df['dte'] < 30]
    
    # Calculate moneyness
    df['moneyness'] = df['strike'] / df['underlying_price']
    
    def get_money(opt, m):
        if opt == 'C': return 'ITM' if m < 0.95 else 'OTM' if m > 1.05 else 'ATM'
        else: return 'ITM' if m > 1.05 else 'OTM' if m < 0.95 else 'ATM'
            
    df['moneyness_cat'] = df.apply(lambda row: get_money(row['option_type'], row['moneyness']), axis=1)
    
    # Pivot
    aevo = df[df['exchange'] == 'AEVO']
    deri = df[df['exchange'] == 'DERIVE']
    
    merged = pd.merge(aevo, deri, on=['time_min', 'expiry', 'strike', 'option_type'], suffixes=('_a', '_d')).sort_values('time_min')
    merged['diff_abs'] = (merged['mid_a'] - merged['mid_d']).abs()
    
    # Simulation Parameters
    INITIAL_DEPOSIT = 100.0
    MARGIN_PER_TRADE = 18.0
    TRADE_SIZE = 0.05
    
    balance = INITIAL_DEPOSIT
    open_trades = []
    trade_history = []
    
    # Group by time_min to simulate chronologically
    time_groups = merged.groupby('time_min')
    
    for t_min, group in time_groups:
        # 1. Process unwinds first
        closed_this_minute = []
        for trade in open_trades:
            # find current price for this option
            current_row = group[(group['expiry'] == trade['expiry']) & (group['option_type'] == trade['opt_type']) & (group['strike'] == trade['strike'])]
            
            close_trade = False
            if not current_row.empty:
                current_gap = current_row.iloc[0]['diff_abs']
                if current_gap <= 2.0:
                    close_trade = True
                    close_gap = current_gap
            
            duration_mins = (t_min - trade['entry_time']).total_seconds() / 60.0
            if not close_trade and duration_mins >= 15:
                close_trade = True
                if not current_row.empty:
                    close_gap = current_row.iloc[0]['diff_abs']
                else:
                    close_gap = trade['entry_gap'] # Fallback
            
            if close_trade:
                pnl = (trade['entry_gap'] - close_gap) * TRADE_SIZE
                balance += pnl
                trade['pnl'] = pnl
                trade['close_time'] = t_min
                trade['duration'] = duration_mins
                trade_history.append(trade)
                closed_this_minute.append(trade)
                
        for t in closed_this_minute:
            open_trades.remove(t)
            
        # 2. Look for new entries
        # Hyper Filters
        opportunities = group[
            (group['diff_abs'] >= 15.0) &
            (group['moneyness_cat_a'] != 'ITM') &
            ((group['dte_a'] < 3) | (group['diff_abs'] >= 20.0))
        ]
        
        for idx, row in opportunities.iterrows():
            # Check if already open
            is_open = any(t['expiry'] == row['expiry'] and t['opt_type'] == row['option_type'] and t['strike'] == row['strike'] for t in open_trades)
            if is_open: continue
            
            # Check margin
            locked_margin = len(open_trades) * MARGIN_PER_TRADE
            if (balance - locked_margin) >= MARGIN_PER_TRADE:
                open_trades.append({
                    'expiry': row['expiry'],
                    'opt_type': row['option_type'],
                    'strike': row['strike'],
                    'entry_time': t_min,
                    'entry_gap': row['diff_abs']
                })
                
    # Close remaining trades at end of simulation
    for t in open_trades:
        t['pnl'] = 0 # Assume scratch if not closed natively
        trade_history.append(t)
        
    hist_df = pd.DataFrame(trade_history)
    print("=== H3 Hyper-Filter Simulation Results ===")
    print(f"Timeframe: {start_time} to {max_time} (6 Hours)")
    print(f"Initial Deposit: ${INITIAL_DEPOSIT}")
    if hist_df.empty:
        print("No trades executed (Hyper-Filters are strict and no signals matched in the last 6h).")
    else:
        total_pnl = hist_df['pnl'].sum()
        win_trades = len(hist_df[hist_df['pnl'] > 0])
        total_trades = len(hist_df)
        print(f"Total Trades: {total_trades}")
        print(f"Winning Trades: {win_trades} ({win_trades/total_trades*100:.1f}%)")
        print(f"Total Net Profit: ${total_pnl:.2f}")
        print(f"Final Balance: ${INITIAL_DEPOSIT + total_pnl:.2f}")
        print(f"ROI for 6 hours: {(total_pnl/INITIAL_DEPOSIT)*100:.2f}%")

if __name__ == "__main__":
    simulate_h3()
