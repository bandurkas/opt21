from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import sqlite3
import os
import pandas as pd
from datetime import datetime

app = FastAPI()

app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

DB_PATH = "data.sqlite"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/data")
async def get_data():
    if not os.path.exists(DB_PATH):
        return {"error": "Database not found"}
        
    conn = get_db_connection()
    
    # 1. Get Balances
    aevo_bal = conn.execute("SELECT balance FROM paper_accounts WHERE exchange = 'AEVO'").fetchone()
    deri_bal = conn.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE'").fetchone()
    aevo_bal = aevo_bal[0] if aevo_bal else 50.0
    deri_bal = deri_bal[0] if deri_bal else 50.0
    
    # 2. Get Open Trades and Calculate Floating PnL
    open_trades_raw = conn.execute("SELECT * FROM open_trades WHERE status = 'OPEN' ORDER BY timestamp DESC").fetchall()
    open_trades = [dict(r) for r in open_trades_raw]
    
    if open_trades:
        # Get latest mid prices
        latest_time = conn.execute("SELECT MAX(timestamp) FROM options_data").fetchone()[0]
        latest_data = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, ask_1, bid_1 FROM options_data WHERE timestamp = '{latest_time}'", conn)
        latest_data['mid'] = (latest_data['ask_1'] + latest_data['bid_1']) / 2
        merged = latest_data.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values='mid').reset_index()
        
        for trade in open_trades:
            mask = (merged['expiry'] == trade['expiry']) & (merged['option_type'] == trade['opt_type']) & (merged['strike'] == trade['strike'])
            trade['floating_pnl'] = 0.0
            trade['current_gap'] = 0.0
            if mask.any():
                row = merged[mask].iloc[0]
                if not pd.isna(row.get('AEVO')) and not pd.isna(row.get('DERIVE')):
                    aevo_mid = row['AEVO']
                    deri_mid = row['DERIVE']
                    current_gap = abs(aevo_mid - deri_mid)
                    entry_gap = abs(trade['entry_aevo_mid'] - trade['entry_deri_mid'])
                    trade['current_gap'] = current_gap
                    trade['floating_pnl'] = (entry_gap - current_gap) * trade['trade_size']
                    
            # Format time
            dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
            trade['time_str'] = dt.strftime('%H:%M:%S')

    # 3. Get Closed Trades
    closed_trades_raw = conn.execute("SELECT * FROM open_trades WHERE status = 'CLOSED' ORDER BY timestamp DESC LIMIT 20").fetchall()
    closed_trades = []
    for r in closed_trades_raw:
        trade = dict(r)
        dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
        trade['time_str'] = dt.strftime('%H:%M:%S')
        # Closed trades actual PnL is not explicitly stored in open_trades, but we can reconstruct roughly or we can just say "Closed"
        # We should probably store actual_pnl in open_trades when closing!
        # For now, just show it.
        closed_trades.append(trade)
        
    conn.close()
    
    # Calculate Total
    total_balance = aevo_bal + deri_bal
    # Add floating
    floating_total = sum([t['floating_pnl'] for t in open_trades])
    total_equity = total_balance + floating_total
    
    return {
        "aevo_balance": aevo_bal,
        "deri_balance": deri_bal,
        "total_equity": total_equity,
        "floating_pnl": floating_total,
        "open_trades": open_trades,
        "closed_trades": closed_trades
    }
