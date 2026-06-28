import sqlite3

con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()

cur.execute("SELECT MAX(timestamp) FROM options_data WHERE exchange='BYBIT'")
max_ts = cur.fetchone()[0]
print("latest bybit ts:", max_ts)

cur.execute("""
  SELECT expiry, option_type, strike, bid_1, ask_1, mark_price, iv, underlying_price
  FROM options_data
  WHERE exchange='BYBIT' AND timestamp = ?
  ORDER BY expiry, strike
""", (max_ts,))
rows = cur.fetchall()
print(f"n rows in latest snapshot: {len(rows)}")
if not rows:
    # fallback: widen window using string prefix match (same minute)
    cur.execute("""
      SELECT expiry, option_type, strike, bid_1, ask_1, mark_price, iv, underlying_price
      FROM options_data
      WHERE exchange='BYBIT' AND timestamp LIKE ?
      ORDER BY expiry, strike
    """, (max_ts[:16]+'%',))
    rows = cur.fetchall()
    print(f"fallback n rows: {len(rows)}")

if rows:
    spot = rows[0][7]
    print(f"spot ~ {spot}")
    expiries = sorted(set(r[0] for r in rows))
    near_expiry = expiries[0]
    print(f"available expiries: {expiries[:6]}")
    print(f"nearest expiry: {near_expiry}")
    print(f"\n{'type':5} {'strike':>8} {'bid':>8} {'ask':>8} {'mid':>8} {'spread$':>8} {'spread%mid':>10} {'iv':>6}")
    for expiry, otype, strike, bid, ask, mark, iv, sp in rows:
        if expiry != near_expiry:
            continue
        if strike is None or abs(strike/spot - 1) > 0.15:
            continue
        if bid is None or ask is None or bid == 0:
            continue
        mid = (bid+ask)/2
        spr = ask-bid
        sprpct = (spr/mid*100) if mid else None
        print(f"{otype:5} {strike:8.0f} {bid:8.2f} {ask:8.2f} {mid:8.2f} {spr:8.2f} {sprpct:9.1f}% {iv if iv else 0:6.3f}")
