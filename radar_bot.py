import os
import json
import time
import threading
import sqlite3
import pandas as pd
from datetime import datetime
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
        strategy TEXT DEFAULT 'H7',
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
        strategy TEXT,
        exchange TEXT,
        balance REAL,
        PRIMARY KEY (strategy, exchange)
    )
    ''')
    
    # Ensure BYBIT exists
    conn.execute("INSERT OR IGNORE INTO paper_accounts (strategy, exchange, balance) VALUES ('H7', 'BYBIT', 1000.0)")
    conn.execute("INSERT OR IGNORE INTO paper_accounts (strategy, exchange, balance) VALUES ('H3', 'BYBIT', 1000.0)")
        
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
               mark_price, bid_1, ask_1, underlying_price
        FROM options_data
        WHERE exchange IN ('DERIVE', 'AEVO', 'BYBIT')
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
        
        # SMART FILTERS
        df['timestamp_utc'] = pd.to_datetime(df['timestamp'], utc=True)
        df['expiry_utc'] = pd.to_datetime(df['expiry'], utc=True)
        df['dte'] = (df['expiry_utc'] - df['timestamp_utc']).dt.total_seconds() / 86400.0
        df = df[df['dte'] < 30] # DTE < 30 filter
        
        df['moneyness'] = df['strike'] / df['underlying_price']
        def is_otm(row):
            m = row['moneyness']
            opt = row['option_type']
            if opt == 'C' and m > 1.05: return True
            if opt == 'P' and m < 0.95: return True
            return False
        df['is_otm'] = df.apply(is_otm, axis=1)
        df = df[~df['is_otm']] # No OTM filter
        
        latest_time = df['timestamp'].max()
        latest_df = df[df['timestamp'] >= latest_time - pd.Timedelta(minutes=2)]
        
        merged = latest_df.pivot_table(
            index=['expiry', 'option_type', 'strike'], 
            columns='exchange', 
            values=['mid_price', 'spread', 'bid_1', 'ask_1']
        ).reset_index()
        
        opportunities = []
        exchanges = [col for col in ['AEVO', 'DERIVE', 'BYBIT'] if ('mid_price', col) in merged.columns]
        
        for idx, row in merged.iterrows():
            expiry = row['expiry'].iloc[0] if isinstance(row['expiry'], pd.Series) else row['expiry']
            if isinstance(expiry, tuple): expiry = expiry[0]
            opt_type = row['option_type'].iloc[0] if isinstance(row['option_type'], pd.Series) else row['option_type']
            if isinstance(opt_type, tuple): opt_type = opt_type[0]
            strike = row['strike'].iloc[0] if isinstance(row['strike'], pd.Series) else row['strike']
            if isinstance(strike, tuple): strike = strike[0]
            
            try:
                dt = pd.to_datetime(expiry)
                pretty_expiry = dt.strftime("%d %b %y")
                raw_expiry = dt.strftime("%d%b%y").upper()
            except:
                pretty_expiry = str(expiry)
                raw_expiry = str(expiry)
            
            for i in range(len(exchanges)):
                for j in range(i+1, len(exchanges)):
                    ex1 = exchanges[i]
                    ex2 = exchanges[j]
                    
                    if pd.isna(row['mid_price'].get(ex1)) or pd.isna(row['mid_price'].get(ex2)):
                        continue
                        
                    bid1, ask1 = row['bid_1'][ex1], row['ask_1'][ex1]
                    bid2, ask2 = row['bid_1'][ex2], row['ask_1'][ex2]
                    mid1, mid2 = row['mid_price'][ex1], row['mid_price'][ex2]
                    spread1, spread2 = row['spread'][ex1], row['spread'][ex2]
                    
                    # Check ex1 Wide / ex2 Tight
                    if (spread1 - spread2) > 20.0 and spread1 > spread2 * 2:
                        edge = (spread1 - spread2) / 2
                        opportunities.append({
                            'type': 'MAKER_ARB',
                            'wide_exchange': ex1,
                            'tight_exchange': ex2,
                            'expiry_iso': str(expiry),
                            'pretty_expiry': pretty_expiry,
                            'raw_expiry': raw_expiry,
                            'pair': f"ETH-{raw_expiry}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить Maker-ордер внутри спреда на {ex1} по мид-прайсу {ex2} (~${mid2:.1f})",
                            'fair_price': mid2,
                            'entry_aevo_mid': mid1, # Store as mid1 and mid2 for generic use
                            'entry_deri_mid': mid2
                        })
                                
                    # Check ex2 Wide / ex1 Tight
                    elif (spread2 - spread1) > 20.0 and spread2 > spread1 * 2:
                        edge = (spread2 - spread1) / 2
                        opportunities.append({
                            'type': 'MAKER_ARB',
                            'wide_exchange': ex2,
                            'tight_exchange': ex1,
                            'expiry_iso': str(expiry),
                            'pretty_expiry': pretty_expiry,
                            'raw_expiry': raw_expiry,
                            'pair': f"ETH-{raw_expiry}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить Maker-ордер внутри спреда на {ex2} по мид-прайсу {ex1} (~${mid1:.1f})",
                            'fair_price': mid1,
                            'entry_aevo_mid': mid2,
                            'entry_deri_mid': mid1
                        })
                                
        return opportunities
    except Exception as e:
        print(f"Error checking H7: {e}")
        return []

def check_unwind_signals():
    unwinds = []
    try:
        conn = sqlite3.connect(DB_PATH)
        open_trades = pd.read_sql_query("SELECT * FROM open_trades WHERE status = 'OPEN' AND strategy = 'H7'", conn)
        
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
            # handle MultiIndex flattening issue depending on pivot format
            mask = (merged['expiry'] == trade['expiry']) & (merged['option_type'] == trade['opt_type']) & (merged['strike'] == trade['strike'])
            if not mask.any(): continue
            
            row = merged[mask].iloc[0]
            ex1 = trade['wide_exchange']
            ex2 = trade['tight_exchange']
            
            if pd.isna(row.get(ex1)) or pd.isna(row.get(ex2)): continue
            
            mid1 = row[ex1]
            mid2 = row[ex2]
            
            current_gap = abs(mid1 - mid2)
            actual_pnl = (abs(trade['entry_aevo_mid'] - trade['entry_deri_mid']) - current_gap) * trade['trade_size']
            
            dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00')).replace(tzinfo=None)
            duration_hours = (datetime.utcnow() - dt).total_seconds() / 3600.0
            
            close_trade = False
            close_reason = ""
            
            if current_gap < 5.0 or actual_pnl >= 10.0:
                close_trade = True
                close_reason = "Успешный арбитраж / Профит достигнут 🎯"
            elif duration_hours >= 1.0 and actual_pnl >= 0.0:
                close_trade = True
                close_reason = "Таймаут 1 час (выход в Б/У или профит) ⏱️"
            elif duration_hours >= 4.0 and actual_pnl >= -5.0:
                close_trade = True
                close_reason = "Таймаут 4 часа (минимизация убытка) ⚠️"
            elif duration_hours >= 8.0:
                close_trade = True
                close_reason = "Жесткий таймаут 8 часов (Освобождение маржи) 🚨"
                
            if close_trade:
                unwinds.append({
                    'trade_id': trade_id,
                    'pair': trade['pair'],
                    'wide_exchange': ex1,
                    'tight_exchange': ex2,
                    'actual_pnl': actual_pnl,
                    'reason': close_reason
                })
                conn.execute(f"UPDATE open_trades SET status = 'CLOSED', actual_pnl = {actual_pnl}, close_time = CURRENT_TIMESTAMP WHERE trade_id = {trade_id}")
                
                # Split profit evenly between the two exchanges
                half_pnl = actual_pnl / 2.0
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = '{ex1}' AND strategy = 'H7'")
                conn.execute(f"UPDATE paper_accounts SET balance = balance + {half_pnl} WHERE exchange = '{ex2}' AND strategy = 'H7'")
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
                        
                        open_count = cursor.execute("SELECT COUNT(*) FROM open_trades WHERE status = 'OPEN' AND strategy = 'H7'").fetchone()[0]
                        MARGIN_REQUIRED_PER_TRADE = 18.0
                        total_locked_margin = open_count * MARGIN_REQUIRED_PER_TRADE
                        
                        bal1 = cursor.execute(f"SELECT balance FROM paper_accounts WHERE exchange = '{opp['wide_exchange']}' AND strategy = 'H7'").fetchone()[0]
                        bal2 = cursor.execute(f"SELECT balance FROM paper_accounts WHERE exchange = '{opp['tight_exchange']}' AND strategy = 'H7'").fetchone()[0]
                        
                        if (bal1 - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE or (bal2 - total_locked_margin) < MARGIN_REQUIRED_PER_TRADE:
                            print(f"Skipping {sig_id} due to insufficient margin.")
                            conn.close()
                            continue

                        # Record trade in DB
                        cursor.execute('''
                        INSERT INTO open_trades (strategy, expiry, strike, opt_type, pair, wide_exchange, tight_exchange, entry_aevo_mid, entry_deri_mid, trade_size, status)
                        VALUES ('H7', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
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
                        f"{unwind['reason']}\n"
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
