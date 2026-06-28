import sqlite3
con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()
cur.execute("SELECT MAX(timestamp) FROM options_data WHERE exchange='BYBIT'")
max_ts = cur.fetchone()[0]
cur.execute("""
  SELECT expiry, strike, bid_1, ask_1, bid_1_vol, ask_1_vol, underlying_price
  FROM options_data
  WHERE exchange='BYBIT' AND timestamp = ? AND option_type='P' AND strike IN (1400,1450)
  ORDER BY expiry
""", (max_ts,))
for expiry, strike, bid, ask, bv, av, spot in cur.fetchall():
    mid = (bid+ask)/2 if bid and ask else None
    print(f"{expiry[:10]} K={strike} bid={bid} ask={ask} mid={mid} bid_size={bv} ask_size={av}")

print("\n=== fee model context (Tyagach portfolio convention) ===")
print("fee_rate ~0.0003 (0.03% notional/side), capped at 12.5% of premium/side")
