"""대시보드용 요약 데이터(docs/data/summary.json) 생성 v2
- 시간별 SMP: data/manual/*.xlsx(전력거래소 내려받기) + data/raw/smp(API) 병합
- 발전원별 발전량·계통수요·시장 외 태양광: data/raw/kpx_live(5분)
- 가스요금: data/manual/gas_price.csv · 연료원별 SMP 결정: data/raw/fuel
- 날씨: Open-Meteo 과거 기상(키 불필요)
"""
import csv, glob, json, re, zipfile, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
RAW, MAN, OUT = Path("data/raw"), Path("data/manual"), Path("docs/data")
FIRST = "2025-01-01"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------- 1) 발전용 가스요금(원/㎥) ----------
gas = {}
p = MAN / "gas_price.csv"
if p.exists():
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = num(r.get("gas_won_per_m3"))
            if v:
                gas[r["month"].strip()] = round(v, 2)


# ---------- 2) 시간별 SMP(육지) ----------
def col_idx(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    z = zipfile.ZipFile(path)
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si"):
            ss.append("".join(t.text or "" for t in si.iter(NS + "t")))
    sheets = sorted((n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[-1]))
    rows = []
    for row in ET.fromstring(z.read(sheets[0])).iter(NS + "row"):
        vals, k = {}, 0
        for c in row.iter(NS + "c"):
            ref = c.get("r")
            k = col_idx(ref) if ref else k
            t, v = c.get("t"), c.find(NS + "v")
            if t == "s" and v is not None:
                val = ss[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(NS + "t"))
            else:
                val = v.text if v is not None else None
            vals[k] = val
            k += 1
        if vals:
            rows.append([vals.get(i) for i in range(max(vals) + 1)])
    return rows


def to_date(v):
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    f = num(s)
    if f and 30000 < f < 60000:
        return (date(1899, 12, 30) + timedelta(days=int(f))).isoformat()
    return None


smp, src = {}, {"엑셀": 0, "API": 0}
for fp in sorted(glob.glob(str(MAN / "*.xlsx"))):
    try:
        rows = read_xlsx(fp)
    except Exception as e:
        print("엑셀 읽기 실패:", fp, e)
        continue
    hdr = None
    for i, r in enumerate(rows[:15]):
        cols = {}
        for j, v in enumerate(r):
            m = re.fullmatch(r"\s*(\d{1,2})\s*시\s*", str(v or ""))
            if m and 1 <= int(m.group(1)) <= 24:
                cols[int(m.group(1)) - 1] = j
        if len(cols) == 24:
            dc = next((j for j, v in enumerate(r) if re.search(r"기간|일자|날짜", str(v or ""))), 0)
            hdr = (i, cols, dc)
            break
    if not hdr:
        print("엑셀에서 01시~24시 제목 줄을 못 찾음:", fp)
        continue
    i0, cols, dc = hdr
    for r in rows[i0 + 1:]:
        d = to_date(r[dc] if dc < len(r) else None)
        if not d or d < FIRST:
            continue
        vals = [num(r[cols[h]]) if cols[h] < len(r) else None for h in range(24)]
        if all(v is not None for v in vals):
            smp[d] = [round(v, 2) for v in vals]
            src["엑셀"] += 1

for fp in glob.glob(str(RAW / "smp" / "*" / "*.json")):
    try:
        rows = json.loads(Path(fp).read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("item") or []
    day = defaultdict(dict)
    for r in rows:
        if r.get("areaName") != "육지":
            continue
        h, v = int(num(r.get("hour")) or 0) - 1, num(r.get("smp"))
        ds = str(r.get("date", "")).replace("-", "")
        if 0 <= h < 24 and v is not None and len(ds) >= 8:
            day[f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"][h] = v
    for d, hv in day.items():
        if len(hv) == 24 and d >= FIRST:
            smp[d] = [round(hv[h], 2) for h in range(24)]
            src["API"] += 1


# ---------- 3) 5분 단위 발전원별 출력(KPX 실시간 수급현황) ----------
MARKET = ["nuc", "coal", "lng", "oil", "hydro", "pump", "renew", "wind", "sunM", "ess"]
FIELDS = {"nuc": "nuclearPower", "lng": "gas", "oil": "oil", "hydro": "waterPower", "pump": "raisingWater",
          "renew": "newRenewable", "wind": "windPower", "sunM": "sunlight", "ppa": "ppa", "btm": "btm", "ess": "essMw"}
acc = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
cnt = defaultdict(lambda: defaultdict(int))
for fp in glob.glob(str(RAW / "kpx_live" / "*" / "*.csv")):
    with open(fp, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = (r.get("regDate") or "").strip()
            if len(t) < 13 or t[:10] < FIRST:
                continue
            d, h = t[:10], int(t[11:13])
            for k, col in FIELDS.items():
                acc[d][h][k] += num(r.get(col)) or 0.0
            tc = num(r.get("totCoal"))
            acc[d][h]["coal"] += tc if tc is not None else (num(r.get("coal")) or 0.0) + (num(r.get("localCoal")) or 0.0)
            cnt[d][h] += 1

gen, dem, hid, lng, sun = {}, {}, {}, {}, {}
for d in sorted(acc):
    hrs = {h: {k: v / cnt[d][h] for k, v in acc[d][h].items()} for h in acc[d] if cnt[d][h]}
    if len(hrs) < 20:
        continue
    gen[d] = {k: round(sum(hrs[h].get(k, 0) for h in hrs) / len(hrs), 1) for k in MARKET + ["ppa", "btm"]}
    dem[d] = [round(sum(hrs[h].get(k, 0) for k in MARKET)) if h in hrs else None for h in range(24)]
    hid[d] = [round(hrs[h].get("ppa", 0) + hrs[h].get("btm", 0)) if h in hrs else None for h in range(24)]
    lng[d] = [round(hrs[h].get("lng", 0)) if h in hrs else None for h in range(24)]
    sun[d] = [round(hrs[h].get("sunM", 0)) if h in hrs else None for h in range(24)]

# ---------- 4) 연료원별 SMP 결정 횟수(육지, 월 합계) ----------
fuel = defaultdict(lambda: defaultdict(int))
fp = RAW / "fuel" / "smp_dec_by_fuel.json"
if fp.exists():
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        data = []
    for r in data:
        if r.get("areaNm") != "육지":
            continue
        td = str(r.get("tradeDay", ""))
        m = f"{td[:4]}-{td[4:6]}"
        if m < FIRST[:7]:
            continue
        t = str(r.get("fuelType", ""))
        k = "LNG" if "LNG" in t else "석탄" if "탄" in t else "원자력" if "원자" in t else "유류" if "유" in t else "기타"
        fuel[m][k] += int(num(r.get("cnt")) or 0)


# ---------- 5) 날씨(태양광 밀집 4곳 평균 + 서울 기온) ----------
SITES = [("목포", 34.82, 126.38), ("전주", 35.82, 127.15), ("서산", 36.78, 126.49), ("안동", 36.57, 128.71), ("서울", 37.57, 126.97)]
VARS_FULL = "shortwave_radiation_sum,cloud_cover_mean,precipitation_sum,sunshine_duration,temperature_2m_mean"
VARS_LITE = "shortwave_radiation_sum,precipitation_sum,sunshine_duration,temperature_2m_mean"


def fetch_weather(end, daily):
    q = {"latitude": ",".join(str(s[1]) for s in SITES), "longitude": ",".join(str(s[2]) for s in SITES),
         "start_date": FIRST, "end_date": end, "daily": daily, "timezone": "Asia/Seoul"}
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=120) as res:
        return json.loads(res.read())


wx, wdata, today = {}, None, datetime.now(KST).date()
for daily in (VARS_FULL, VARS_LITE):
    for lag in (2, 6, 10):
        try:
            wdata = fetch_weather((today - timedelta(days=lag)).isoformat(), daily)
            break
        except Exception as e:
            print("날씨 받기 실패", lag, "일 전까지:", e)
    if wdata:
        break
if isinstance(wdata, list) and len(wdata) == len(SITES):
    for i, d in enumerate(wdata[0]["daily"]["time"]):
        def av(key, idx):
            vs = [wdata[j]["daily"].get(key, [None] * (i + 1))[i] for j in idx]
            vs = [v for v in vs if v is not None]
            return round(sum(vs) / len(vs), 2) if vs else None
        sun = av("sunshine_duration", range(4))
        wx[d] = {"rad": av("shortwave_radiation_sum", range(4)), "cloud": av("cloud_cover_mean", range(4)),
                 "prcp": av("precipitation_sum", range(4)), "sun": round(sun / 3600, 2) if sun is not None else None,
                 "tmp": av("temperature_2m_mean", [4])}


# ---------- 저장 ----------
out = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "first": FIRST,
       "gas": gas, "smp": dict(sorted(smp.items())), "gen": gen, "dem": dem, "hid": hid, "lng": lng, "sun": sun,
       "fuel": {m: dict(v) for m, v in sorted(fuel.items())}, "wx": wx, "src": src}
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "summary.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
ks = sorted(smp)
print(f"SMP {len(smp)}일({ks[0] if ks else '-'}~{ks[-1] if ks else '-'}, 엑셀 {src['엑셀']}·API {src['API']}) · "
      f"발전량 {len(gen)}일 · 날씨 {len(wx)}일 · 가스요금 {len(gas)}개월")
