import sqlite3, json

con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()

# raw options_data schema check
cur.execute("PRAGMA table_info(options_data)")
cols = [c[1] for c in cur.fetchall()]
print("options_data columns:", cols)

cur.execute("SELECT MAX(timestamp) FROM options_data WHERE exchange='BYBIT'")
max_ts = cur.fetchone()[0]
print("latest bybit ts:", max_ts)

# pull latest snapshot of bybit ETH options near 90% and 100% moneyness, nearest weekly expiry
cur.execute("""
  SELECT expiry, option_type, strike, bid, ask, mark_price, iv, spot
  FROM options_data
  WHERE exchange='BYBIT' AND timestamp >= ? - 60
  ORDER BY expiry, strike
""", (max_ts,))
rows = cur.fetchall()
print(f"n rows in latest snapshot: {len(rows)}")
if rows:
    spot = rows[0][7]
    print(f"spot ~ {spot}")
    # find nearest weekly expiry
    expiries = sorted(set(r[0] for r in rows))
    near_expiry = expiries[0]
    print(f"nearest expiry: {near_expiry}")
    print(f"\n{'type':5} {'strike':>8} {'bid':>8} {'ask':>8} {'mid':>8} {'spread$':>8} {'spread%mid':>10} {'iv':>6}")
    for expiry, otype, strike, bid, ask, mark, iv, sp in rows:
        if expiry != near_expiry:
            continue
        if abs(strike/spot - 1) > 0.15:
            continue
        if bid is None or ask is None:
            continue
        mid = (bid+ask)/2
        spr = ask-bid
        sprpct = (spr/mid*100) if mid else None
        print(f"{otype:5} {strike:8.0f} {bid:8.2f} {ask:8.2f} {mid:8.2f} {spr:8.2f} {sprpct:9.1f}% {iv if iv else 0:6.3f}")
