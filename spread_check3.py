import sqlite3

con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()
cur.execute("SELECT MAX(timestamp) FROM options_data WHERE exchange='BYBIT'")
max_ts = cur.fetchone()[0]

cur.execute("""
  SELECT expiry, option_type, strike, bid_1, ask_1, mark_price, iv, underlying_price
  FROM options_data
  WHERE exchange='BYBIT' AND timestamp = ? AND expiry IN ('2026-07-03T00:00:00+00:00','2026-07-10T00:00:00+00:00')
  ORDER BY expiry, strike
""", (max_ts,))
rows = cur.fetchall()
spot = rows[0][7] if rows else None
print(f"spot ~ {spot}, n={len(rows)}")
print(f"\n{'expiry':12} {'type':5} {'strike':>8} {'bid':>8} {'ask':>8} {'mid':>8} {'spread$':>8} {'spread%mid':>10} {'iv':>6} {'moneyness':>9}")
for expiry, otype, strike, bid, ask, mark, iv, sp in rows:
    if strike is None:
        continue
    m = strike/spot
    if not (0.85 <= m <= 1.15):
        continue
    if bid is None or ask is None or bid == 0:
        continue
    mid = (bid+ask)/2
    spr = ask-bid
    sprpct = (spr/mid*100) if mid else None
    print(f"{expiry[:10]:12} {otype:5} {strike:8.0f} {bid:8.2f} {ask:8.2f} {mid:8.2f} {spr:8.2f} {sprpct:9.1f}% {iv if iv else 0:6.3f} {m:9.3f}")
