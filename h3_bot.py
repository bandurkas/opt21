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
        SELECT timestamp, exchange, expiry, strike, option_type, ask_1, bid_1, underlying_price, bid_1_vol, ask_1_vol
        FROM options_data
        WHERE timestamp = '{latest_time}' AND bid_1 > 0 AND ask_1 > bid_1 AND bid_1_vol >= 0.1 AND ask_1_vol >= 0.1
        """
        df = pd.read_sql_query(query, conn)
        df['mid'] = (df['ask_1'] + df['bid_1']) / 2
        df['spread_pct'] = (df['ask_1'] - df['bid_1']) / df['mid']
        
        # SMART FILTERS
        df['timestamp_utc'] = pd.to_datetime(df['timestamp'], utc=True)
        df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
        df['dte'] = (df['expiry_utc'] - df['timestamp_utc']).dt.total_seconds() / 86400.0
        df = df[df['dte'] < 30]
        
        df['moneyness'] = df['strike'] / df['underlying_price']
        df['m_dist'] = (df['moneyness'] - 1.0).abs()
        
        # Filter out bad OTM spreads (> 30%) and enforce M_DIST <= 0.15
        df = df[~((df['m_dist'] > 0.05) & (df['spread_pct'] > 0.30))]
        df = df[df['m_dist'] <= 0.15]
        
        merged = df.pivot_table(index=['expiry', 'option_type', 'strike', 'dte', 'm_dist'], columns='exchange', values='mid').reset_index()
        
        if merged.empty:
            conn.close()
            return
            
        exchanges = [col for col in ['AEVO', 'DERIVE', 'BYBIT'] if col in merged.columns]
        opportunities = []
        
        for i in range(len(exchanges)):
            for j in range(i+1, len(exchanges)):
                ex1 = exchanges[i]
                ex2 = exchanges[j]
                
                temp_diff = (merged[ex1] - merged[ex2]).abs()
                mask = (
                    (temp_diff >= 10.0) &
                    (merged['dte'] >= 0.5) &
                    merged[ex1].notna() & merged[ex2].notna()
                )
                
                for _, row in merged[mask].iterrows():
                    opportunities.append({
                        'expiry': row['expiry'],
                        'option_type': row['option_type'],
                        'strike': row['strike'],
                        'ex1': ex1,
                        'ex2': ex2,
                        'mid1': row[ex1],
                        'mid2': row[ex2],
                        'gap': temp_diff.loc[_],
                        'lead_lag_note': f"Derive leads Aevo" if 'DERIVE' in [ex1, ex2] and 'AEVO' in [ex1, ex2] else "Triangle Arbitrage" if 'BYBIT' in [ex1, ex2] else ""
                    })
        
        cursor = conn.cursor()
        for opp in opportunities:
            expiry = opp['expiry']
            opt_type = opp['option_type']
            strike = opp['strike']
            ex1 = opp['ex1']
            ex2 = opp['ex2']
            pair = f"ETH-{expiry.split(' ')[0]}-{strike}-{opt_type}"
            
            # Check if already open
            is_open = cursor.execute(f"SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H3' AND pair = '{pair}'").fetchone()[0]
            if is_open > 0:
                continue
                
            # Check margins
            open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H3'").fetchone()[0]
            total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
            
            bal1 = cursor.execute(f"SELECT balance FROM paper_accounts WHERE exchange = '{ex1}' AND strategy = 'H3'").fetchone()[0]
            bal2 = cursor.execute(f"SELECT balance FROM paper_accounts WHERE exchange = '{ex2}' AND strategy = 'H3'").fetchone()[0]
            
            if (bal1 - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE or (bal2 - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                continue
                
            cursor.execute('''
            INSERT INTO open_trades (strategy, expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
            VALUES ('H3', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            ''', (expiry, strike, opt_type, pair, ex1, ex2, opp['mid1'], opp['mid2'], TRADE_SIZE))
            conn.commit()
            print(f"H3 Signal triggered on {pair} between {ex1} and {ex2} (Gap: ${opp['gap']:.2f}) {opp['lead_lag_note']}")
            
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
            dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
            duration_mins = (datetime.utcnow() - dt).total_seconds() / 60.0
            
            close_trade = False
            actual_pnl = 0.0
            
            try:
                row = merged.loc[(trade['expiry'], trade['opt_type'], trade['strike'])]
                ex1 = trade['wide_exchange']
                ex2 = trade['tight_exchange']
                
                if pd.isna(row.get(ex1)) or pd.isna(row.get(ex2)):
                    # Data missing right now, but check if we need to force close due to timeout
                    if duration_mins >= 15:
                        close_trade = True
                        actual_pnl = 0.0 # Force close as scratch if missing data
                else:
                    current_mid1 = row[ex1]
                    current_mid2 = row[ex2]
                    
                    entry_gap = abs(trade['entry_aevo_mid'] - trade['entry_deri_mid'])
                    current_gap = abs(current_mid1 - current_mid2)
                    actual_pnl = (entry_gap - current_gap) * trade['trade_size']
                    
                    if current_gap <= 2.0 or duration_mins >= 15:
                        close_trade = True
            except KeyError:
                # Option not found in current data (expired/delisted)
                if duration_mins >= 15:
                    close_trade = True
                    actual_pnl = 0.0
                    
            if close_trade:
                conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade['trade_id']}")
                half_pnl = actual_pnl / 2.0
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = '{trade['wide_exchange']}' AND strategy = 'H3'")
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = '{trade['tight_exchange']}' AND strategy = 'H3'")
                conn.commit()
                print(f"H3 Trade {trade['trade_id']} closed with PnL ${actual_pnl:.2f} (Duration: {duration_mins:.1f}m)")
                
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
