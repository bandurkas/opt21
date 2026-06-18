from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import sqlite3
import os
from datetime import datetime, timezone, timedelta

app = FastAPI()
app.mount("/static", StaticFiles(directory="dashboard/static"), name="static")
templates = Jinja2Templates(directory="dashboard/templates")

DB_PATH = "data.sqlite"
CYCLE_SEC = 600          # analytics_collector interval

# Data needed before Phase-2 backtests can run (data-readiness, not edge-validation)
HYP = {
    "A": {"name": "Variance Risk Premium", "target_days": 28,
          "needs": "≥4 weeks of IV vs subsequently-realized vol across regimes"},
    "C": {"name": "BYBIT Order Flow → Vol/Direction", "target_days": 21,
          "needs": "≥3 weeks of OI / volume / book-imbalance vs forward returns"},
}


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _scalar(c, q, default=None):
    try:
        r = c.execute(q).fetchone()
        return r[0] if r and r[0] is not None else default
    except sqlite3.OperationalError:
        return default


def _iso(s):
    if not s:
        return None
    return datetime.fromisoformat(s)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/status")
async def status():
    if not os.path.exists(DB_PATH):
        return {"error": "Database not found"}
    c = conn()
    now = datetime.now(timezone.utc)

    counts = {
        "iv_snapshots": _scalar(c, "SELECT COUNT(*) FROM iv_snapshots", 0),
        "bybit_flow": _scalar(c, "SELECT COUNT(*) FROM bybit_flow", 0),
        "bybit_oi_strikes": _scalar(c, "SELECT COUNT(*) FROM bybit_oi_strikes", 0),
    }

    start_s = _scalar(c, "SELECT MIN(ts) FROM iv_snapshots")
    last_s = _scalar(c, "SELECT MAX(ts) FROM iv_snapshots")
    start, last = _iso(start_s), _iso(last_s)
    cycles = _scalar(c, "SELECT COUNT(DISTINCT ts) FROM bybit_flow", 0)

    elapsed_days = (now - start).total_seconds() / 86400.0 if start else 0.0
    secs_since = (now - last).total_seconds() if last else None
    live = secs_since is not None and secs_since < CYCLE_SEC * 2
    expected_cycles = max(1, int((now - start).total_seconds() / CYCLE_SEC)) if start else 1
    coverage = min(100.0, 100.0 * cycles / expected_cycles) if cycles else 0.0

    # Hypotheses progress (data-readiness)
    hyps = []
    for hid, meta in HYP.items():
        pct = min(100.0, 100.0 * elapsed_days / meta["target_days"]) if start else 0.0
        eta = (start + timedelta(days=meta["target_days"])) if start else None
        days_left = max(0.0, meta["target_days"] - elapsed_days)
        hyps.append({
            "id": hid, "name": meta["name"], "needs": meta["needs"],
            "target_days": meta["target_days"], "progress_pct": round(pct, 1),
            "eta": eta.strftime("%d %b %Y") if eta else None,
            "days_left": round(days_left, 1),
            "ready": pct >= 100.0,
        })

    # Latest market snapshot (DERIVE term structure + skew, BYBIT flow)
    term = []
    for ten in (7, 14, 30):
        r = c.execute(
            "SELECT atm_iv, rr_25, fly FROM iv_snapshots WHERE exchange='DERIVE' AND tenor_target=? "
            "ORDER BY ts DESC LIMIT 1", (ten,)).fetchone()
        if r:
            term.append({"tenor": ten,
                         "atm_iv": round(r["atm_iv"] * 100, 1) if r["atm_iv"] is not None else None,
                         "rr": round(r["rr_25"] * 100, 1) if r["rr_25"] is not None else None,
                         "fly": round(r["fly"] * 100, 1) if r["fly"] is not None else None})

    rv = _scalar(c, "SELECT rv_trailing_24h FROM iv_snapshots WHERE exchange='DERIVE' "
                    "AND rv_trailing_24h IS NOT NULL ORDER BY ts DESC LIMIT 1")
    iv14 = next((t["atm_iv"] for t in term if t["tenor"] == 14), None)
    vrp = round(iv14 - rv * 100, 1) if (rv is not None and iv14 is not None) else None

    bf = c.execute("SELECT spot,total_oi,pcr_oi,book_imb,atm_iv FROM bybit_flow "
                   "ORDER BY ts DESC LIMIT 1").fetchone()
    bybit = dict(bf) if bf else {}

    c.close()
    return {
        "live": live, "last_update": last_s, "secs_since": secs_since,
        "collection": {"start": start_s, "elapsed_days": round(elapsed_days, 2),
                       "cycles": cycles, "expected_cycles": expected_cycles,
                       "coverage_pct": round(coverage, 1), "cycle_min": CYCLE_SEC // 60},
        "counts": counts,
        "hypotheses": hyps,
        "market": {
            "spot": round(bybit.get("spot"), 1) if bybit.get("spot") else None,
            "term": term,
            "rv_24h": round(rv * 100, 1) if rv is not None else None,
            "vrp": vrp,
            "bybit": {
                "total_oi": round(bybit["total_oi"]) if bybit.get("total_oi") else None,
                "pcr_oi": round(bybit["pcr_oi"], 2) if bybit.get("pcr_oi") else None,
                "book_imb": round(bybit["book_imb"], 3) if bybit.get("book_imb") is not None else None,
                "atm_iv": round(bybit["atm_iv"] * 100, 1) if bybit.get("atm_iv") else None,
            },
        },
    }
