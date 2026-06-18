import sqlite3
import pandas as pd
import time
import numpy as np
from datetime import datetime, timedelta

DB_PATH = "data.sqlite"
TRADE_SIZE = 0.05
MARGIN_REQUIRED_PER_TRADE = 18.0

def check_h3_signals():
    try:
        conn = sqlite3.connect(DB_PATH)
        latest_time = pd.read_sql_query("SELECT MAX(timestamp) FROM options_data", conn).iloc[0,0]
        
        query = f"""
        SELECT timestamp, exchange, expiry, strike, option_type, ask_1, bid_1, underlying_price
        FROM options_data
        WHERE timestamp = '{latest_time}' AND bid_1 > 0 AND ask_1 > bid_1
        """
        df = pd.read_sql_query(query, conn)
        df['mid'] = (df['ask_1'] + df['bid_1']) / 2
        
        # SMART FILTERS
        df['timestamp_utc'] = pd.to_datetime(df['timestamp'], utc=True)
        df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
        df['dte'] = (df['expiry_utc'] - df['timestamp_utc']).dt.total_seconds() / 86400.0
        df = df[df['dte'] < 30]
        
        merged = df.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values='mid')
        merged = merged.dropna(subset=['AEVO', 'DERIVE'])
        
        if merged.empty:
            conn.close()
            return
            
        merged['diff_abs'] = (merged['AEVO'] - merged['DERIVE']).abs()
        
        # Look for price divergence >= 10.0
        opportunities = merged[merged['diff_abs'] >= 10.0]
        
        cursor = conn.cursor()
        for idx, row in opportunities.iterrows():
            expiry, opt_type, strike = idx
            pair = f"ETH-{expiry.split(' ')[0]}-{strike}-{opt_type}"
            
            # Check if already open
            is_open = cursor.execute(f"SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H3' AND pair = '{pair}'").fetchone()[0]
            if is_open > 0:
                continue
                
            # Check margins
            open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H3'").fetchone()[0]
            total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
            
            aevo_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'AEVO' AND strategy = 'H3'").fetchone()[0]
            deri_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE' AND strategy = 'H3'").fetchone()[0]
            
            if (aevo_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE or (deri_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                continue
                
            # Enter trade
            aevo_mid = row['AEVO']
            deri_mid = row['DERIVE']
            cursor.execute('''
            INSERT INTO open_trades (strategy, expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
            VALUES ('H3', ?, ?, ?, ?, 'AEVO', 'DERIVE', ?, ?, ?, 'OPEN')
            ''', (expiry, strike, opt_type, pair, aevo_mid, deri_mid, TRADE_SIZE))
            conn.commit()
            print(f"H3 Signal triggered on {pair} (Gap: ${row['diff_abs']:.2f})")
            
        conn.close()
    except Exception as e:
        print(f"H3 check error: {e}")

def check_h3_unwinds():
    try:
        conn = sqlite3.connect(DB_PATH)
        open_trades = pd.read_sql_query("SELECT * FROM open_trades WHERE status = 'OPEN' AND strategy = 'H3'", conn)
        
        if open_trades.empty:
            conn.close()
            return
            
        latest_time = pd.read_sql_query("SELECT MAX(timestamp) FROM options_data", conn).iloc[0,0]
        df = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, ask_1, bid_1 FROM options_data WHERE timestamp = '{latest_time}'", conn)
        df['mid'] = (df['ask_1'] + df['bid_1']) / 2
        merged = df.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values='mid')
        
        for _, trade in open_trades.iterrows():
            try:
                row = merged.loc[(trade['expiry'], trade['opt_type'], trade['strike'])]
                if pd.isna(row.get('AEVO')) or pd.isna(row.get('DERIVE')):
                    continue
                    
                current_aevo_mid = row['AEVO']
                current_deri_mid = row['DERIVE']
                
                entry_gap = abs(trade['entry_aevo_mid'] - trade['entry_deri_mid'])
                current_gap = abs(current_aevo_mid - current_deri_mid)
                actual_pnl = (entry_gap - current_gap) * trade['trade_size']
                
                dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
                duration_mins = (datetime.utcnow() - dt).total_seconds() / 60.0
                
                close_trade = False
                if current_gap <= 2.0:
                    close_trade = True
                elif duration_mins >= 15:
                    close_trade = True
                    
                if close_trade:
                    conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade['trade_id']}")
                    half_pnl = actual_pnl / 2.0
                    conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'AEVO' AND strategy = 'H3'")
                    conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'DERIVE' AND strategy = 'H3'")
                    conn.commit()
                    print(f"H3 Trade {trade['trade_id']} closed with PnL ${actual_pnl:.2f} (Duration: {duration_mins:.1f}m)")
            except KeyError:
                continue
                
        conn.close()
    except Exception as e:
        print(f"H3 unwind error: {e}")

def main():
    print("H3 Bot started.")
    while True:
        check_h3_signals()
        check_h3_unwinds()
        time.sleep(60)

if __name__ == "__main__":
    main()
