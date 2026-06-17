import os
import json
import time
import threading
import sqlite3
import pandas as pd
import telebot

TOKEN = "7135215656:AAF276QckBUylAPWKD-VLy6DanfwBxhqAng"
bot = telebot.TeleBot(TOKEN)

ADMINS_FILE = "admins.json"
DB_PATH = "data.sqlite"

# Load or initialize authorized admins
if os.path.exists(ADMINS_FILE):
    with open(ADMINS_FILE, "r") as f:
        authorized_admins = set(json.load(f))
else:
    authorized_admins = set()

def save_admins():
    with open(ADMINS_FILE, "w") as f:
        json.dump(list(authorized_admins), f)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
    CREATE TABLE IF NOT EXISTS open_trades (
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        expiry TEXT,
        strike REAL,
        opt_type TEXT,
        pair TEXT,
        wide_exchange TEXT,
        tight_exchange TEXT,
        entry_aevo_mid REAL,
        entry_deri_mid REAL,
        trade_size REAL,
        status TEXT,
        actual_pnl REAL DEFAULT 0.0,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        close_time DATETIME
    )
    ''')
    conn.execute('''
    CREATE TABLE IF NOT EXISTS paper_accounts (
        exchange TEXT PRIMARY KEY,
        balance REAL
    )
    ''')
    
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM paper_accounts")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO paper_accounts (exchange, balance) VALUES ('AEVO', 50.0)")
        cursor.execute("INSERT INTO paper_accounts (exchange, balance) VALUES ('DERIVE', 50.0)")
        
    conn.commit()
    conn.close()

init_db()

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    if chat_id not in authorized_admins:
        authorized_admins.add(chat_id)
        save_admins()
        bot.send_message(chat_id, "✅ Успешно! Вы авторизованы. Радар-Бот будет присылать вам сигналы.")
    else:
        bot.send_message(chat_id, "ℹ️ Вы уже подписаны на сигналы Радар-Бота.")

def get_h7_opportunities():
    try:
        conn = sqlite3.connect(DB_PATH)
        query = """
        SELECT timestamp, exchange, symbol, strike, expiry, option_type, 
               mark_price, bid_1, ask_1
        FROM options_data
        WHERE exchange IN ('DERIVE', 'AEVO')
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        if df.empty: return []
        
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['strike'] = df['strike'].astype(float)
        df['bid_1'] = df['bid_1'].astype(float)
        df['ask_1'] = df['ask_1'].astype(float)
        df['mark_price'] = df['mark_price'].astype(float)
        
        df['spread'] = df['ask_1'] - df['bid_1']
        df['mid_price'] = (df['ask_1'] + df['bid_1']) / 2
        df = df[(df['spread'] > 0) & (df['bid_1'] > 0)]
        
        latest_time = df['timestamp'].max()
        latest_df = df[df['timestamp'] >= latest_time - pd.Timedelta(minutes=2)]
        
        merged = latest_df.pivot_table(
            index=['expiry', 'option_type', 'strike'], 
            columns='exchange', 
            values=['mid_price', 'spread', 'bid_1', 'ask_1']
        )
        merged = merged.dropna(subset=[('mid_price', 'AEVO'), ('mid_price', 'DERIVE')])
        
        opportunities = []
        for idx, row in merged.iterrows():
            expiry, opt_type, strike = idx
            
            # Parse expiry date correctly
            try:
                dt = pd.to_datetime(expiry)
                pretty_expiry = dt.strftime("%d %b %y") # '18 Jun 26'
                raw_expiry = dt.strftime("%d%b%y").upper() # '18JUN26'
            except:
                pretty_expiry = str(expiry)
                raw_expiry = str(expiry)
            
            aevo_bid, aevo_ask = row['bid_1']['AEVO'], row['ask_1']['AEVO']
            deri_bid, deri_ask = row['bid_1']['DERIVE'], row['ask_1']['DERIVE']
            aevo_mid, deri_mid = row['mid_price']['AEVO'], row['mid_price']['DERIVE']
            aevo_spread, deri_spread = row['spread']['AEVO'], row['spread']['DERIVE']
            
            # Check Derive Wide / Aevo Tight
            if deri_spread > aevo_spread * 2 and deri_spread > 5.0:
                if aevo_mid > deri_bid and aevo_mid < deri_ask:
                    edge = min(abs(deri_mid - aevo_mid), (deri_ask - aevo_mid), (aevo_mid - deri_bid))
                    if edge > 20: # $20 Threshold
                        opportunities.append({
                            'type': 'MAKER_ARB',
                            'wide_exchange': 'Derive (Lyra)',
                            'tight_exchange': 'Aevo',
                            'expiry_iso': expiry,
                            'pretty_expiry': pretty_expiry,
                            'raw_expiry': raw_expiry,
                            'pair': f"ETH-{raw_expiry}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить BUY Limit внутри спреда на Derive (~${aevo_mid-1})",
                            'fair_price': aevo_mid,
                            'entry_aevo_mid': aevo_mid,
                            'entry_deri_mid': deri_mid
                        })
            
            # Check Aevo Wide / Derive Tight
            if aevo_spread > deri_spread * 2 and aevo_spread > 5.0:
                if deri_mid > aevo_bid and deri_mid < aevo_ask:
                    edge = min(abs(aevo_mid - deri_mid), (aevo_ask - deri_mid), (deri_mid - aevo_bid))
                    if edge > 20:
                        opportunities.append({
                            'type': 'MAKER_ARB',
                            'wide_exchange': 'Aevo',
                            'tight_exchange': 'Derive (Lyra)',
                            'expiry_iso': expiry,
                            'pretty_expiry': pretty_expiry,
                            'raw_expiry': raw_expiry,
                            'pair': f"ETH-{raw_expiry}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить BUY Limit внутри спреда на Aevo (~${deri_mid-1})",
                            'fair_price': deri_mid,
                            'entry_aevo_mid': aevo_mid,
                            'entry_deri_mid': deri_mid
                        })
                        
        return opportunities
    except Exception as e:
        print(f"Error checking H7: {e}")
        return []

def check_unwind_signals():
    unwinds = []
    try:
        conn = sqlite3.connect(DB_PATH)
        open_trades = pd.read_sql_query("SELECT * FROM open_trades WHERE status = 'OPEN'", conn)
        
        if open_trades.empty:
            conn.close()
            return []
            
        latest_time_query = "SELECT MAX(timestamp) FROM options_data"
        latest_time = pd.read_sql_query(latest_time_query, conn).iloc[0,0]
        
        query = f"""
        SELECT exchange, expiry, strike, option_type, ask_1, bid_1
        FROM options_data
        WHERE timestamp = '{latest_time}'
        """
        latest_df = pd.read_sql_query(query, conn)
        
        latest_df['mid'] = (latest_df['ask_1'] + latest_df['bid_1']) / 2
        
        merged = latest_df.pivot_table(
            index=['expiry', 'option_type', 'strike'],
            columns='exchange',
            values='mid'
        ).reset_index()
        
        for _, trade in open_trades.iterrows():
            trade_id = trade['trade_id']
            mask = (merged['expiry'] == trade['expiry']) & (merged['option_type'] == trade['opt_type']) & (merged['strike'] == trade['strike'])
            if not mask.any(): continue
            
            row = merged[mask].iloc[0]
            if pd.isna(row.get('AEVO')) or pd.isna(row.get('DERIVE')): continue
            
            aevo_mid = row['AEVO']
            deri_mid = row['DERIVE']
            
            current_gap = abs(aevo_mid - deri_mid)
            if current_gap < 5.0:
                actual_pnl = (abs(trade['entry_aevo_mid'] - trade['entry_deri_mid']) - current_gap) * trade['trade_size']
                unwinds.append({
                    'trade_id': trade_id,
                    'pair': trade['pair'],
                    'wide_exchange': trade['wide_exchange'],
                    'tight_exchange': trade['tight_exchange'],
                    'actual_pnl': actual_pnl
                })
                conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade_id}")
                
                # Split profit evenly between the two exchanges
                half_pnl = actual_pnl / 2.0
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'AEVO'")
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = 'DERIVE'")
                conn.commit()
                
        conn.close()
        return unwinds
    except Exception as e:
        print(f"Error checking unwinds: {e}")
        return []

def background_radar_loop():
    print("Radar loop started.")
    sent_signals = set()
    
    while True:
        try:
            if authorized_admins:
                opps = get_h7_opportunities()
                for opp in opps:
                    sig_id = f"{opp['pair']}_{opp['wide_exchange']}"
                    
                    if sig_id not in sent_signals:
                        opt_type_ru = "CALL (Колл)" if opp['opt_type'] == "C" else "PUT (Пут)"
                        TRADE_SIZE = 0.05
                        actual_profit = opp['edge'] * TRADE_SIZE

                        # Connect to DB and check margins
                        conn = sqlite3.connect(DB_PATH)
                        cursor = conn.cursor()
                        
                        open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN'").fetchone()[0]
                        MARGIN_REQUIRED_PER_TRADE = 18.0
                        total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
                        
                        aevo_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'AEVO'").fetchone()[0]
                        deri_bal = cursor.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE'").fetchone()[0]
                        
                        if (aevo_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE or (deri_bal - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                            print(f"Skipping {sig_id} due to insufficient margin.")
                            conn.close()
                            continue

                        # Record trade in DB
                        cursor.execute('''
                        INSERT INTO open_trades (expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
                        ''', (opp['expiry_iso'], opp['strike'], opp['opt_type'], opp['pair'], opp['wide_exchange'], opp['tight_exchange'], opp['entry_aevo_mid'], opp['entry_deri_mid'], TRADE_SIZE))
                        trade_id = cursor.lastrowid
                        conn.commit()
                        conn.close()

                        msg = (
                            f"🚨 СИГНАЛ НА ОТКРЫТИЕ (Сделка #{trade_id})\n"
                            f"Тикет: ETH\n"
                            f"Дата Экспирации: {opp['pretty_expiry']} ({opp['raw_expiry']})\n"
                            f"Страйк: {opp['strike']}\n"
                            f"Сторона: {opt_type_ru}\n"
                            f"Действие: {opp['action']}\n"
                            f"Выставить по цене: {opp['fair_price']:.2f}$ (Справедливая цена)\n"
                            f"Размер поз: {TRADE_SIZE}\n"
                            f"Ориентировочная прибыль: {actual_profit:.2f}$\n\n"
                            f"⚠️ *Шаг 2 (Хедж):* После исполнения сразу продать (SELL Market) на {opp['tight_exchange']}."
                        )
                        
                        for admin_id in authorized_admins:
                            bot.send_message(admin_id, msg, parse_mode='Markdown')
                            
                        sent_signals.add(sig_id)
                        
                # Check for unwinds
                unwinds = check_unwind_signals()
                for unwind in unwinds:
                    unwind_msg = (
                        f"✅ СИГНАЛ НА ЗАКРЫТИЕ (Сделка #{unwind['trade_id']})\n"
                        f"Спред по {unwind['pair']} успешно схлопнулся!\n"
                        f"Прибыль: ~${unwind['actual_pnl']:.2f}\n\n"
                        f"Действие: Закройте обе ноги:\n"
                        f"1. На {unwind['wide_exchange']} нажмите SELL (закрыть купленное/проданное)\n"
                        f"2. На {unwind['tight_exchange']} нажмите BUY (закрыть проданное/купленное)"
                    )
                    for admin_id in authorized_admins:
                        bot.send_message(admin_id, unwind_msg, parse_mode='Markdown')
                        
                # Clear sent signals every 30 minutes to re-trigger if edge persists
                if len(sent_signals) > 100:
                    sent_signals.clear()
                    
            time.sleep(60)
        except Exception as e:
            print(f"Radar loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    t = threading.Thread(target=background_radar_loop, daemon=True)
    t.start()
    
    print("Telegram bot polling started...")
    bot.infinity_polling()
