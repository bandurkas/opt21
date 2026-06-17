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
                            'pair': f"ETH-{expiry.split(' ')[0]}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить BUY Limit внутри спреда на Derive (~${aevo_mid-1})",
                            'fair_price': aevo_mid
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
                            'pair': f"ETH-{expiry.split(' ')[0]}-{strike}-{opt_type}",
                            'strike': strike,
                            'opt_type': opt_type,
                            'edge': edge,
                            'action': f"Выставить BUY Limit внутри спреда на Aevo (~${deri_mid-1})",
                            'fair_price': deri_mid
                        })
                        
        return opportunities
    except Exception as e:
        print(f"Error checking H7: {e}")
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
                        msg = (
                            f"🚨 *MAKER ARBITRAGE EDGE: ${opp['edge']:.2f}*\n\n"
                            f"📍 Биржа для лимитки: {opp['wide_exchange']}\n"
                            f"📦 Пара: {opp['pair']}\n"
                            f"⚡ Действие: {opp['action']}\n\n"
                            f"*(Справедливая цена конкурента: ${opp['fair_price']:.2f})*"
                        )
                        
                        for admin_id in authorized_admins:
                            bot.send_message(admin_id, msg, parse_mode='Markdown')
                            
                        sent_signals.add(sig_id)
                        
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
