"""전력거래소 발전원별 실시간 수급현황(5분) 저장 - 태양광 BTM/PPA 추정 포함"""
import csv, datetime as dt, io, json, pathlib, re, urllib.request

URL = "https://www.kpx.or.kr/powerSource.es?mid=a10606030000&device=chart"
OUT = pathlib.Path("data/raw/kpx_live")
KST = dt.timezone(dt.timedelta(hours=9))

req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
m = re.search(r"var\s+ictArr\s*=\s*(\[.*?\]);", html, re.S)
if not m:
    raise SystemExit("데이터를 찾지 못함 - 페이지 구조가 바뀌었을 수 있음")
rows = json.loads(m.group(1))

by_day = {}
for r in rows:
    ts = str(r.get("once") or "")[:12]
    if len(ts) == 12:
        by_day.setdefault(ts[:8], {})[ts] = r

for day, new in by_day.items():
    f = OUT / day[:4] / f"{day}.csv"
    merged = {}
    if f.exists():
        for old in csv.DictReader(io.StringIO(f.read_text("utf-8"))):
            merged[old["datetime"]] = old
    for ts, r in new.items():
        merged[ts] = {"datetime": ts, **{k: v for k, v in r.items()
                                          if k not in ("once", "regDate", "seq")}}
    cols = ["datetime"] + sorted({k for v in merged.values() for k in v} - {"datetime"})
    f.parent.mkdir(parents=True, exist_ok=True)
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for ts in sorted(merged):
        w.writerow(merged[ts])
    f.write_text(out.getvalue(), encoding="utf-8")
    print(f"{day}: {len(merged)}개 시점 저장")
