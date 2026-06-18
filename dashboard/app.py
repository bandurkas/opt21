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
    response_data = {}
    
    for strategy in ['H7', 'H1', 'H2', 'H3']:
        # 1. Get Balances
        aevo_bal_row = conn.execute("SELECT balance FROM paper_accounts WHERE exchange = 'AEVO' AND strategy = ?", (strategy,)).fetchone()
        deri_bal_row = conn.execute("SELECT balance FROM paper_accounts WHERE exchange = 'DERIVE' AND strategy = ?", (strategy,)).fetchone()
        aevo_bal = aevo_bal_row[0] if aevo_bal_row else 50.0
        deri_bal = deri_bal_row[0] if deri_bal_row else 50.0
        
        # 2. Get Open Trades
        open_trades_raw = conn.execute("SELECT * FROM open_trades WHERE status = 'OPEN' AND strategy = ? ORDER BY timestamp DESC", (strategy,)).fetchall()
        open_trades = [dict(r) for r in open_trades_raw]
        
        if open_trades:
            latest_time = conn.execute("SELECT MAX(timestamp) FROM options_data").fetchone()[0]
            latest_data = pd.read_sql_query(f"SELECT exchange, expiry, strike, option_type, ask_1, bid_1 FROM options_data WHERE timestamp = '{latest_time}'", conn)
            latest_data['mid'] = (latest_data['ask_1'] + latest_data['bid_1']) / 2
            merged = latest_data.pivot_table(index=['expiry', 'option_type', 'strike'], columns='exchange', values='mid').reset_index()
            
            for trade in open_trades:
                trade['floating_pnl'] = 0.0
                trade['current_gap'] = 0.0
                mask = (merged['expiry'] == trade['expiry']) & (merged['option_type'] == trade['opt_type']) & (merged['strike'] == trade['strike'])
                if mask.any():
                    row = merged[mask].iloc[0]
                    if not pd.isna(row.get('AEVO')) and not pd.isna(row.get('DERIVE')):
                        aevo_mid = row['AEVO']
                        deri_mid = row['DERIVE']
                        if strategy == 'H1':
                            # Simple directional for H1 since we just bought/sold on Derive
                            trade['current_gap'] = 0.0
                            trade['floating_pnl'] = abs(deri_mid - trade['entry_deri_mid']) * trade['trade_size'] - 0.25
                        else:
                            current_gap = abs(aevo_mid - deri_mid)
                            entry_gap = abs(trade['entry_aevo_mid'] - trade['entry_deri_mid'])
                            trade['current_gap'] = current_gap
                            trade['floating_pnl'] = (entry_gap - current_gap) * trade['trade_size']
                            
                # Format time
                dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
                trade['time_str'] = dt.strftime('%H:%M:%S')
                now = datetime.utcnow()
                diff = now - dt.replace(tzinfo=None)
                mins, secs = divmod(diff.total_seconds(), 60)
                hours, mins = divmod(mins, 60)
                trade['duration_str'] = f"{int(hours)}h {int(mins)}m" if hours > 0 else f"{int(mins)}m {int(secs)}s"

        # 3. Get Closed Trades
        closed_trades_raw = conn.execute("SELECT * FROM open_trades WHERE status = 'CLOSED' AND strategy = ? ORDER BY timestamp DESC LIMIT 20", (strategy,)).fetchall()
        closed_trades = []
        for r in closed_trades_raw:
            trade = dict(r)
            dt = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
            trade['time_str'] = dt.strftime('%H:%M:%S')
            if trade.get('close_time'):
                close_dt = datetime.fromisoformat(trade['close_time'].replace('Z', '+00:00'))
                diff = close_dt - dt
                mins, secs = divmod(diff.total_seconds(), 60)
                hours, mins = divmod(mins, 60)
                trade['duration_str'] = f"{int(hours)}h {int(mins)}m" if hours > 0 else f"{int(mins)}m {int(secs)}s"
            else:
                trade['duration_str'] = "-"
            closed_trades.append(trade)
            
        total_balance = aevo_bal + deri_bal
        floating_total = sum([t.get('floating_pnl', 0.0) for t in open_trades])
        total_equity = total_balance + floating_total
        
        response_data[strategy] = {
            "aevo_balance": aevo_bal,
            "deri_balance": deri_bal,
            "total_equity": total_equity,
            "floating_pnl": floating_total,
            "open_trades": open_trades,
            "closed_trades": closed_trades
        }
        
    conn.close()
    return response_data
