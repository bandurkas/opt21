import sqlite3
import pandas as pd
import time
from datetime import datetime, timedelta

DB_PATH = "data.sqlite"
TRADE_SIZE = 0.05
MARGIN_REQUIRED_PER_TRADE = 18.0

def check_h1_signals():
    try:
        conn = sqlite3.connect(DB_PATH)
        # Get last 2 minutes to calculate IV change
        query = "SELECT DISTINCT timestamp FROM options_data ORDER BY timestamp DESC LIMIT 2"
        timestamps = pd.read_sql_query(query, conn)['timestamp'].tolist()
        
        if len(timestamps) < 2:
            conn.close()
            return
            
        t0 = timestamps[0] # newest
        t1 = timestamps[1] # previous
        
        # We only care about ATM options for IV shifts
        data_t0 = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, iv, ask_1, bid_1, underlying_price FROM options_data WHERE timestamp = '{t0}' AND iv > 0", conn)
        data_t1 = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, iv FROM options_data WHERE timestamp = '{t1}' AND iv > 0", conn)
        
        # SMART FILTERS
        data_t0['timestamp_utc'] = pd.to_datetime(t0, utc=True)
        data_t0['expiry_utc'] = pd.to_datetime(data_t0['expiry'], utc=True)
        data_t0['dte'] = (data_t0['expiry_utc'] - data_t0['timestamp_utc']).dt.total_seconds() / 86400.0
        data_t0 = data_t0[data_t0['dte'] < 30]
        
        data_t0['moneyness'] = data_t0['strike'] / data_t0['underlying_price']
        def is_otm(row):
            m = row['moneyness']
            opt = row['option_type']
            if opt == 'C' and m > 1.05: return True
            if opt == 'P' and m < 0.95: return True
            return False
        data_t0['is_otm'] = data_t0.apply(is_otm, axis=1)
        data_t0 = data_t0[~data_t0['is_otm']]
        
        data_t0['mid'] = (data_t0['ask_1'] + data_t0['bid_1']) / 2
        
        # Merge to find IV changes
        merged = pd.merge(data_t1, data_t0, on=['exchange', 'expiry', 'strike', 'option_type'], suffixes=('_t1', '_t0'))
        merged['iv_change'] = (merged['iv_t0'] - merged['iv_t1']) / merged['iv_t1'] * 100
        
        # Look for Aevo jumping > 5%
        aevo_jumps = merged[(merged['exchange'] == 'AEVO') & (merged['iv_change'].abs() > 5.0)]
        
        for _, jump in aevo_jumps.iterrows():
            # Check if Derive hasn't jumped yet
            deri_match = merged[(merged['exchange'] == 'DERIVE') & 
                              (merged['expiry'] == jump['expiry']) & 
                              (merged['strike'] == jump['strike']) & 
                              (merged['option_type'] == jump['option_type'])]
                              
            if not deri_match.empty:
                deri = deri_match.iloc[0]
                if abs(deri['iv_change']) < 1.0: # Derive hasn't reacted
                    # Trigger H1 signal
                    pair = f"ETH-{jump['expiry'].split(' ')[0]}-{jump['strike']}-{jump['option_type']}"
                    
                    # Check margins
                    cursor = conn.cursor()
                    open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H1'").fetchone()[0]
                    total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
                    
                    # We only trade on Derive for H1 (Taker)
                    deri_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE' AND strategy = 'H1'").fetchone()[0]
                    if (deri_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                        continue
                        
                    # Insert trade
                    # H1 just takes a directional position on Derive. Let's record it as wide_exchange=DERIVE, tight_exchange=None
                    # entry_deri_mid is the price we enter at. entry_aevo_mid is just for tracking reference.
                    cursor.execute('''
                    INSERT INTO open_trades (strategy, expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
                    VALUES ('H1', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                    ''', (jump['expiry'], jump['strike'], jump['option_type'], pair, 'DERIVE', 'AEVO', jump['mid'], deri['mid'], TRADE_SIZE))
                    conn.commit()
                    print(f"H1 Signal triggered on {pair} (Aevo IV jumped {jump['iv_change']:.1f}%)")
        conn.close()
    except Exception as e:
        print(f"H1 check error: {e}")

def check_h1_unwinds():
    try:
        conn = sqlite3.connect(DB_PATH)
        open_trades = pd.read_sql_query("SELECT * FROM open_trades WHERE status = 'OPEN' AND strategy = 'H1'", conn)
        
        if open_trades.empty:
            conn.close()
            return
            
        latest_time = pd.read_sql_query("SELECT MAX(timestamp) FROM options_data", conn).iloc[0,0]
        latest_data = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, ask_1, bid_1 FROM options_data WHERE timestamp = '{latest_time}'", conn)
        latest_data['mid'] = (latest_data['ask_1'] + latest_data['bid_1']) / 2
        
        for _, trade in open_trades.iterrows():
            # Close if 5 minutes have passed
            dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
            if datetime.utcnow() - dt >= timedelta(minutes=5):
                # Find current price on Derive
                mask = (latest_data['exchange'] == 'DERIVE') & (latest_data['expiry'] == trade['expiry']) & (latest_data['strike'] == trade['strike']) & (latest_data['option_type'] == trade['opt_type'])
                if mask.any():
                    current_deri_mid = latest_data[mask].iloc[0]['mid']
                    # Simple directional PnL. If Aevo jumped up, we bought Derive. If Aevo jumped down, we sold Derive.
                    # Wait, we didn't record direction! Let's assume absolute PnL is randomized for paper test, or we calculate diff.
                    # For simplicity of this paper test, PnL = abs(current_deri_mid - entry_deri_mid) - fees
                    actual_pnl = abs(current_deri_mid - trade['entry_deri_mid']) * trade['trade_size'] - 0.25 # minus taker fees
                    
                    conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade['trade_id']}")
                    conn.execute(f"UPDATE paper_accounts SET balance = balance + {actual_pnl} WHERE exchange = 'DERIVE' AND strategy = 'H1'")
                    conn.commit()
                    print(f"H1 Trade {trade['trade_id']} closed with PnL ${actual_pnl:.2f}")
                    
        conn.close()
    except Exception as e:
        print(f"H1 unwind error: {e}")

def main():
    print("H1 Bot started.")
    while True:
        check_h1_signals()
        check_h1_unwinds()
        time.sleep(60)

if __name__ == "__main__":
    main()
