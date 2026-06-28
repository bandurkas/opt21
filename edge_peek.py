import sqlite3, statistics as st
from datetime import datetime

con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()

print("=== A) VRP per (exchange, tenor) — atm_iv vs rv_trailing_24h ===")
cur.execute("""
  SELECT exchange, tenor_target, COUNT(*),
         AVG(atm_iv), AVG(rv_trailing_24h), AVG(atm_iv - rv_trailing_24h),
         AVG(rr_25), AVG(fly)
  FROM iv_snapshots
  WHERE atm_iv IS NOT NULL AND rv_trailing_24h IS NOT NULL
  GROUP BY exchange, tenor_target
  ORDER BY exchange, tenor_target
""")
for row in cur.fetchall():
    exch, tenor, n, aiv, rv, vrp, rr, fly = row
    print(f"  {exch:8} tenor={tenor:3} n={n:5}  ATM_IV={aiv:.4f}  RV24h={rv:.4f}  VRP={vrp:+.4f}  RR25={rr:+.4f}  FLY={fly:+.4f}")

print("\n=== A) VRP trend over time (split first half vs second half of window) ===")
cur.execute("SELECT MIN(ts), MAX(ts) FROM iv_snapshots")
tmin, tmax = cur.fetchone()
cur.execute("""
  SELECT exchange, tenor_target,
    CASE WHEN ts < (SELECT MIN(ts) FROM iv_snapshots) THEN 'x' ELSE '' END
  FROM iv_snapshots LIMIT 1
""")
# split by midpoint timestamp string compare works since ISO format
cur.execute("SELECT COUNT(*) FROM iv_snapshots")
total_n = cur.fetchone()[0]
cur.execute("SELECT ts FROM iv_snapshots ORDER BY ts")
all_ts = [r[0] for r in cur.fetchall()]
mid_ts = all_ts[len(all_ts)//2]
for half, cond in [("first", f"ts < '{mid_ts}'"), ("second", f"ts >= '{mid_ts}'")]:
    cur.execute(f"""
      SELECT exchange, tenor_target, COUNT(*), AVG(atm_iv - rv_trailing_24h)
      FROM iv_snapshots WHERE atm_iv IS NOT NULL AND rv_trailing_24h IS NOT NULL AND {cond}
      GROUP BY exchange, tenor_target ORDER BY exchange, tenor_target
    """)
    print(f"  -- {half} half --")
    for exch, tenor, n, vrp in cur.fetchall():
        print(f"     {exch:8} tenor={tenor:3} n={n:5} VRP={vrp:+.4f}")

print("\n=== C) Bybit flow -> forward return correlation ===")
cur.execute("SELECT ts, spot, total_oi, pcr_oi, book_imb FROM bybit_flow ORDER BY ts")
rows = cur.fetchall()
print(f"  n rows = {len(rows)}")

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z","+00:00"))

times = [parse(r[0]) for r in rows]
spot = [r[1] for r in rows]
oi = [r[2] for r in rows]
pcr = [r[3] for r in rows]
imb = [r[4] for r in rows]

def fwd_return(i, horizon_steps):
    j = i + horizon_steps
    if j >= len(spot) or spot[i] in (None,0) or spot[j] is None:
        return None
    return (spot[j] - spot[i]) / spot[i]

def corr(xs, ys):
    pairs = [(x,y) for x,y in zip(xs,ys) if x is not None and y is not None]
    if len(pairs) < 10:
        return None, len(pairs)
    xv = [p[0] for p in pairs]; yv = [p[1] for p in pairs]
    if st.pstdev(xv) == 0 or st.pstdev(yv) == 0:
        return None, len(pairs)
    n = len(xv)
    mx, my = st.fmean(xv), st.fmean(yv)
    cov = sum((a-mx)*(b-my) for a,b in zip(xv,yv)) / n
    return cov / (st.pstdev(xv)*st.pstdev(yv)), n

# horizons in steps (cycle ~10min): 30m~3, 1h~6, 4h~24
for label, steps in [("30m",3), ("1h",6), ("4h",24)]:
    fwd = [fwd_return(i, steps) for i in range(len(spot))]
    # delta OI / delta book_imb as feature, and raw book_imb/pcr
    oi_delta = [None] + [ (oi[i]-oi[i-1]) if oi[i] is not None and oi[i-1] is not None else None for i in range(1,len(oi))]
    c_imb, n1 = corr(imb, fwd)
    c_pcr, n2 = corr(pcr, fwd)
    c_oid, n3 = corr(oi_delta, fwd)
    print(f"  horizon={label:4} corr(book_imb,fwd_ret)={c_imb} (n={n1})  corr(pcr_oi,fwd_ret)={c_pcr} (n={n2})  corr(d_OI,fwd_ret)={c_oid} (n={n3})")

con.close()
