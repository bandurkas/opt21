import sqlite3
import pandas as pd

DB_PATH = 'data.sqlite'

def dump_top_3():
    conn = sqlite3.connect(DB_PATH)
    
    query = """
    SELECT timestamp, exchange, expiry, strike, option_type, ask_1, bid_1, underlying_price
    FROM options_data
    WHERE bid_1 > 0 AND ask_1 > bid_1
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        return
        
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    df['time_min'] = df['timestamp'].dt.floor('Min')
    
    max_time = df['time_min'].max()
    start_time = max_time - pd.Timedelta(hours=6)
    df = df[df['time_min'] >= start_time]
    
    df['mid'] = (df['ask_1'] + df['bid_1']) / 2
    df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
    df['dte'] = (df['expiry_utc'] - df['timestamp']).dt.total_seconds() / 86400.0
    df = df[df['dte'] < 30]
    
    df['moneyness'] = df['strike'] / df['underlying_price']
    
    def get_money(opt, m):
        if opt == 'C': return 'ITM' if m < 0.95 else 'OTM' if m > 1.05 else 'ATM'
        else: return 'ITM' if m > 1.05 else 'OTM' if m < 0.95 else 'ATM'
            
    df['moneyness_cat'] = df.apply(lambda row: get_money(row['option_type'], row['moneyness']), axis=1)
    
    aevo = df[df['exchange'] == 'AEVO']
    deri = df[df['exchange'] == 'DERIVE']
    
    merged = pd.merge(aevo, deri, on=['time_min', 'expiry', 'strike', 'option_type'], suffixes=('_a', '_d')).sort_values('time_min')
    merged['diff_abs'] = (merged['mid_a'] - merged['mid_d']).abs()
    
    # We want to identify the exact 3 trades
    open_trades = []
    trade_history = []
    
    time_groups = merged.groupby('time_min')
    for t_min, group in time_groups:
        closed_this_minute = []
        for trade in open_trades:
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
                if not current_row.empty: close_gap = current_row.iloc[0]['diff_abs']
                else: close_gap = trade['entry_gap']
            
            if close_trade:
                trade['duration'] = duration_mins
                trade['close_gap'] = close_gap
                trade_history.append(trade)
                closed_this_minute.append(trade)
                
        for t in closed_this_minute:
            open_trades.remove(t)
            
        opportunities = group[
            (group['diff_abs'] >= 15.0) &
            (group['moneyness_cat_a'] != 'ITM') &
            ((group['dte_a'] < 3) | (group['diff_abs'] >= 20.0))
        ]
        
        for idx, row in opportunities.iterrows():
            is_open = any(t['expiry'] == row['expiry'] and t['opt_type'] == row['option_type'] and t['strike'] == row['strike'] for t in open_trades)
            if is_open: continue
            
            # Since margin is enough for 3 trades, we just add it
            open_trades.append({
                'entry_time': t_min,
                'expiry': row['expiry'],
                'opt_type': row['option_type'],
                'strike': row['strike'],
                'entry_gap': row['diff_abs'],
                'dte': row['dte_a'],
                'moneyness': row['moneyness_cat_a']
            })
            
    for i, t in enumerate(trade_history, 1):
        print(f"Trade {i}:")
        print(f"  Entry Gap: ${t['entry_gap']:.2f}")
        print(f"  DTE: {t['dte']:.1f} days")
        print(f"  Moneyness: {t['moneyness']}")
        print(f"  Duration to close: {t['duration']:.1f} mins")
        print(f"  Close Gap: ${t['close_gap']:.2f}")

if __name__ == "__main__":
    dump_top_3()
