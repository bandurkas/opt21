import sqlite3

conn = sqlite3.connect('data.sqlite')
try:
    conn.execute("ALTER TABLE open_trades ADD COLUMN strategy TEXT DEFAULT 'H7'")
except Exception as e:
    print(e)

try:
    # SQLite doesn't support DROP PRIMARY KEY easily, so we have to recreate paper_accounts if we want a composite primary key.
    # Alternatively, since we just added 'strategy' column, we can do it manually.
    conn.execute("ALTER TABLE paper_accounts ADD COLUMN strategy TEXT")
    conn.execute("UPDATE paper_accounts SET strategy = 'H7'")
    
    # Insert for H1 and H2
    for strat in ['H1', 'H2']:
        conn.execute("INSERT INTO paper_accounts (strategy, exchange, balance) VALUES (?, 'AEVO', 50.0)", (strat,))
        conn.execute("INSERT INTO paper_accounts (strategy, exchange, balance) VALUES (?, 'DERIVE', 50.0)", (strat,))
        
except Exception as e:
    print(e)

conn.commit()
conn.close()
