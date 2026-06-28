import sqlite3, math

con = sqlite3.connect("/root/opt21/data.sqlite")
cur = con.cursor()
cur.execute("""
  SELECT tenor_target, AVG(spot), AVG(atm_iv), AVG(put_iv_90), AVG(put_iv_95),
         AVG(call_iv_105), AVG(call_iv_110), AVG(dte_actual)
  FROM iv_snapshots WHERE exchange='BYBIT' AND atm_iv IS NOT NULL
  GROUP BY tenor_target ORDER BY tenor_target
""")
rows = cur.fetchall()

def bs_price(S, K, T, sigma, is_call):
    if T <= 0 or sigma <= 0:
        return max(0, (S-K) if is_call else (K-S))
    d1 = (math.log(S/K) + 0.5*sigma**2*T) / (sigma*math.sqrt(T))
    d2 = d1 - sigma*math.sqrt(T)
    N = lambda x: 0.5*(1+math.erf(x/math.sqrt(2)))
    if is_call:
        return S*N(d1) - K*N(d2)
    else:
        return K*N(-d2) - S*N(-d1)

print(f"{'tenor':>5} {'spot':>8} {'ATM_IV':>7} {'putIV90':>8} {'callIV110':>9} {'dte':>5}")
for tenor, spot, atm_iv, put90, put95, call105, call110, dte in rows:
    print(f"{tenor:5} {spot:8.1f} {atm_iv:7.3f} {put90:8.3f} {call110:9.3f} {dte:5.2f}")
    T = dte/365.0
    K_atm = round(spot/25)*25
    K_put90 = round(spot*0.90/25)*25
    K_call110 = round(spot*1.10/25)*25

    atm_put_prem = bs_price(spot, K_atm, T, atm_iv, False)
    atm_call_prem = bs_price(spot, K_atm, T, atm_iv, True)
    otm_put_prem = bs_price(spot, K_put90, T, put90, False)
    otm_call_prem = bs_price(spot, K_call110, T, call110, True)

    print(f"   ATM straddle (K={K_atm}): put_prem=${atm_put_prem:.2f} call_prem=${atm_call_prem:.2f} total=${atm_put_prem+atm_call_prem:.2f}")
    print(f"   OTM strangle (Kput={K_put90},Kcall={K_call110}): put_prem=${otm_put_prem:.2f} call_prem=${otm_call_prem:.2f} total=${otm_put_prem+otm_call_prem:.2f}")
    # what if call wing priced at SAME flat IV as put wing magnitude implies no skew (use atm_iv for both as baseline counterfactual)
    flat_put_prem = bs_price(spot, K_put90, T, atm_iv, False)
    flat_call_prem = bs_price(spot, K_call110, T, atm_iv, True)
    print(f"   Counterfactual no-skew strangle (both legs at flat ATM_IV={atm_iv:.3f}): put_prem=${flat_put_prem:.2f} call_prem=${flat_call_prem:.2f} total=${flat_put_prem+flat_call_prem:.2f}")
    skew_uplift_put = otm_put_prem - flat_put_prem
    print(f"   --> skew-driven extra $ on the put wing alone: ${skew_uplift_put:.2f}  ({100*skew_uplift_put/flat_put_prem if flat_put_prem else 0:.1f}% richer than flat-vol)")
    print()
