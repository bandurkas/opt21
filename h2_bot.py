import sqlite3
import pandas as pd
import time
import numpy as np

DB_PATH = "data.sqlite"
TRADE_SIZE = 0.05
MARGIN_REQUIRED_PER_TRADE = 18.0

def check_h2_signals():
    try:
        conn = sqlite3.connect(DB_PATH)
        latest_time = pd.read_sql_query("SELECT MAX(timestamp) FROM options_data", conn).iloc[0,0]
        
        query = f"""
        SELECT exchange, expiry, strike, option_type, iv, ask_1, bid_1
        FROM options_data
        WHERE timestamp = '{latest_time}' AND iv > 0
        """
        df = pd.read_sql_query(query, conn)
        df['mid'] = (df['ask_1'] + df['bid_1']) / 2
        
        merged = df.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values=['iv', 'mid'])
        merged = merged.dropna(subset=[('iv', 'AEVO'), ('iv', 'DERIVE')])
        
        if merged.empty:
            conn.close()
            return
            
        merged['iv_diff'] = (merged['iv']['AEVO'] - merged['iv']['DERIVE']).abs() / merged['iv']['DERIVE'] * 100
        
        # Look for IV differences > 30%
        opportunities = merged[merged['iv_diff'] > 30.0]
        
        cursor = conn.cursor()
        for idx, row in opportunities.iterrows():
            expiry, opt_type, strike = idx
            pair = f"ETH-{expiry.split(' ')[0]}-{strike}-{opt_type}"
            
            # Check if already open
            is_open = cursor.execute(f"SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H2' AND pair = '{pair}'").fetchone()[0]
            if is_open > 0:
                continue
                
            # Check margins
            open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H2'").fetchone()[0]
            total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
            
            aevo_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'AEVO' AND strategy = 'H2'").fetchone()[0]
            deri_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE' AND strategy = 'H2'").fetchone()[0]
            
            if (aevo_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE or (deri_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                continue
                
            # Enter trade
            aevo_mid = row['mid']['AEVO']
            deri_mid = row['mid']['DERIVE']
            cursor.execute('''
            INSERT INTO open_trades (strategy, expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
            VALUES ('H2', ?, ?, ?, ?, 'AEVO', 'DERIVE', ?, ?, ?, 'OPEN')
            ''', (expiry, strike, opt_type, pair, aevo_mid, deri_mid, TRADE_SIZE))
            conn.commit()
            print(f"H2 Signal triggered on {pair} (IV Diff {row['iv_diff']:.1f}%)")
            
        conn.close()
    except Exception as e:
        print(f"H2 check error: {e}")

def check_h2_unwinds():
    try:
        conn = sqlite3.connect(DB_PATH)
        open_trades = pd.read_sql_query("SELECT * FROM open_trades WHERE status = 'OPEN' AND strategy = 'H2'", conn)
        
        if open_trades.empty:
            conn.close()
            return
            
        latest_time = pd.read_sql_query("SELECT MAX(timestamp) FROM options_data", conn).iloc[0,0]
        df = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, iv, ask_1, bid_1 FROM options_data WHERE timestamp = '{latest_time}' AND iv > 0", conn)
        df['mid'] = (df['ask_1'] + df['bid_1']) / 2
        merged = df.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values=['iv', 'mid'])
        
        for _, trade in open_trades.iterrows():
            try:
                row = merged.loc[(trade['expiry'], trade['opt_type'], trade['strike'])]
                if pd.isna(row['iv']['AEVO']) or pd.isna(row['iv']['DERIVE']):
                    continue
                    
                iv_diff = abs(row['iv']['AEVO'] - row['iv']['DERIVE']) / row['iv']['DERIVE'] * 100
                
                # Close if difference falls below 10%
                if iv_diff < 10.0:
                    current_aevo_mid = row['mid']['AEVO']
                    current_deri_mid = row['mid']['DERIVE']
                    
                    entry_gap = abs(trade['entry_aevo_mid'] - trade['entry_deri_mid'])
                    current_gap = abs(current_aevo_mid - current_deri_mid)
                    
                    actual_pnl = (entry_gap - current_gap) * trade['trade_size']
                    
                    conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade['trade_id']}")
                    half_pnl = actual_pnl / 2.0
                    conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'AEVO' AND strategy = 'H2'")
                    conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'DERIVE' AND strategy = 'H2'")
                    conn.commit()
                    print(f"H2 Trade {trade['trade_id']} closed with PnL ${actual_pnl:.2f}")
            except KeyError:
                continue
                
        conn.close()
    except Exception as e:
        print(f"H2 unwind error: {e}")

def main():
    print("H2 Bot started.")
    while True:
        check_h2_signals()
        check_h2_unwinds()
        time.sleep(60)

if __name__ == "__main__":
    main()
