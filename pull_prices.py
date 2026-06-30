import json, time, urllib.request, datetime, sys, os

TICKERS = ["BK","STT","JPM","DD","CL","C","DE","PG","SWK","CHD","MO","PFE","GLW",
"WFC","BUD","BMY","UNP","GIS","SHW","CPB","BF.B","XOM","KMB","LLY","CVX","PPG",
"JNJ","KO","MKC","MRK","HRL","GE","HSY","SJM","MMM","ADM","NEM","CAT"]

HERE = os.path.dirname(os.path.abspath(__file__))

def ysym(t): return t.replace(".", "-")  # Yahoo uses '-' for class shares

def fetch(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=10y&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == 3: raise
            time.sleep(1.5)

def parse(j):
    res = j["chart"]["result"][0]
    meta = res["meta"]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    series = [(t, c) for t, c in zip(ts, closes) if c is not None]
    return meta, series

def price_at(series, target_epoch):
    best = None
    for t, c in series:
        if t <= target_epoch: best = c
        else: break
    return best if best is not None else series[0][1]

now_dt = datetime.datetime.now(datetime.timezone.utc)
def days_ago_epoch(d): return (now_dt - datetime.timedelta(days=d)).timestamp()
ytd_epoch = datetime.datetime(2025, 12, 31, 23, 59, tzinfo=datetime.timezone.utc).timestamp()

def returns_for(series, last):
    return {
        "1y":  last / price_at(series, days_ago_epoch(365)) - 1,
        "5y":  last / price_at(series, days_ago_epoch(365*5)) - 1,
        "10y": last / price_at(series, days_ago_epoch(365*10)) - 1,
        "ytd": last / price_at(series, ytd_epoch) - 1,
    }

# month-start sample dates for the last 10 years (inclusive of current month)
def month_starts(years_back):
    cur = datetime.datetime(now_dt.year - years_back, now_dt.month, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(now_dt.year, now_dt.month, 1, tzinfo=datetime.timezone.utc)
    pts = []
    while cur <= end:
        pts.append(cur)
        ny, nm = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
        cur = datetime.datetime(ny, nm, 1, tzinfo=datetime.timezone.utc)
    return pts

data, series_map = {}, {}
asof_epoch = 0
for t in TICKERS:
    try:
        meta, series = parse(fetch(ysym(t)))
        last = meta.get("regularMarketPrice") or series[-1][1]
        prev = series[-2][1] if len(series) >= 2 else last
        asof_epoch = max(asof_epoch, meta.get("regularMarketTime", 0))
        series_map[t] = series
        data[t] = {"price": round(last, 2), "chg": round((last/prev - 1)*100, 2),
                   "ret": returns_for(series, last)}
        print(f"  {t:6s} ${last:>9.2f}  1y {data[t]['ret']['1y']*100:6.1f}%", file=sys.stderr)
    except Exception as e:
        print(f"  !! {t}: {e}", file=sys.stderr)
        data[t] = None
    time.sleep(0.15)

meta, sp_ser = parse(fetch("%5EGSPC"))
sp_last = meta.get("regularMarketPrice") or sp_ser[-1][1]
sp = {"price": round(sp_last, 2), "ret": returns_for(sp_ser, sp_last)}
asof_epoch = max(asof_epoch, meta.get("regularMarketTime", 0))

# equal-weight index period returns = simple average of constituent returns
good = [v for v in data.values() if v]
index_ret = {k: sum(v["ret"][k] for v in good)/len(good) for k in ["1y","5y","10y","ytd"]}

# monthly growth series, one per horizon. Each is an equal-weight basket REBALANCED to
# equal at that horizon's start, anchored to the EXACT trailing date the table uses, so the
# chart's endpoint matches the table to the decimal. Indexed to 100 at the base date.
pts10 = month_starts(10)
epochs10 = [p.timestamp() for p in pts10]
labels10 = [p.strftime("%Y-%m") for p in pts10]

def index_series(base_epoch):
    base_label = datetime.datetime.fromtimestamp(base_epoch, datetime.timezone.utc).strftime("%Y-%m")
    months = [(e, l) for e, l in zip(epochs10, labels10) if e > base_epoch]
    epoch_list = [base_epoch] + [e for e, l in months]
    labels = [base_label] + [l for e, l in months]
    lindy = [round(100*sum(price_at(s, e)/price_at(s, base_epoch) for s in series_map.values())/len(series_map), 2)
             for e in epoch_list]
    sp_base = price_at(sp_ser, base_epoch)
    spv = [round(100*price_at(sp_ser, e)/sp_base, 2) for e in epoch_list]
    # final point = live snapshot, so the right edge agrees with current prices and the table
    lindy[-1] = round(100*sum(data[t]["price"]/price_at(series_map[t], base_epoch) for t in series_map)/len(series_map), 2)
    spv[-1] = round(100*sp_last/sp_base, 2)
    return {"t": labels, "lindy": lindy, "sp": spv}

# bases are the SAME epochs used by returns_for(), so endpoints line up exactly
series = {
    "10y": index_series(days_ago_epoch(365*10)),
    "5y":  index_series(days_ago_epoch(365*5)),
    "1y":  index_series(days_ago_epoch(365)),
    "ytd": index_series(ytd_epoch),
}

# per-company monthly close history (for the expandable detail charts); last point = live
hist_by = {}
for t in TICKERS:
    s = series_map.get(t)
    if not s: continue
    closes = [round(price_at(s, e), 2) for e in epochs10]
    closes[-1] = data[t]["price"]
    hist_by[t] = closes

asof = datetime.datetime.fromtimestamp(asof_epoch, datetime.timezone.utc).strftime("%Y-%m-%d")
out = {"asof": asof, "constituents": data, "index": index_ret, "sp500": sp,
       "series": series, "hist": {"months": labels10, "by": hist_by}, "n": len(good)}

with open(os.path.join(HERE, "prices.json"), "w") as f:
    json.dump(out, f, indent=2)
print("\nwrote prices.json (the page loads it at runtime — no HTML edit needed)", file=sys.stderr)

print(f"\n=== SUMMARY (as of {asof}) === constituents priced: {len(good)}/{len(TICKERS)}", file=sys.stderr)
for k in ["ytd","1y","5y","10y"]:
    print(f"  {k:4s}  Lindy {index_ret[k]*100:7.1f}%   S&P {sp['ret'][k]*100:7.1f}%", file=sys.stderr)
