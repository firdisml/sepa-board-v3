"""Throwaway: characterise the EODHD news endpoint before designing against it."""
import json, os, urllib.request

TOK = os.environ["EODHD_API_TOKEN"]

def get(**kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    url = f"https://eodhd.com/api/news?{q}&api_token={TOK}&fmt=json"
    return json.load(urllib.request.urlopen(url, timeout=60))

d = get(s="NVDA.US", limit=1)
print("FIELDS:", list(d[0].keys()))
print("  symbols:", (d[0].get("symbols") or [])[:8], "count", len(d[0].get("symbols") or []))
print("  tags:", (d[0].get("tags") or [])[:6])
print("  sentiment:", d[0].get("sentiment"))
print("  content chars:", len(d[0].get("content") or ""))

for tkr, keys in [("AAPL.US", ("apple", "aapl", "iphone", "cook")),
                  ("ELF.US", ("e.l.f", "elf ")),
                  ("1155.KLSE", ("maybank", "malayan"))]:
    try:
        items = get(s=tkr, limit=20)
    except Exception as e:
        print(f"\n{tkr}: ERROR {e}")
        continue
    hits = [x for x in items if any(k in (x.get("title") or "").lower() for k in keys)]
    nsym = [len(x.get("symbols") or []) for x in items]
    print(f"\n{tkr}: {len(items)} items, {len(hits)} titles name the company "
          f"({100*len(hits)//max(len(items),1)}%); symbols/article med "
          f"{sorted(nsym)[len(nsym)//2] if nsym else 0}")
    for x in items[:5]:
        print(f"   [{len(x.get('symbols') or []):>3}sym] {(x.get('title') or '')[:66]}")
