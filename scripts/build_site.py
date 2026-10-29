"""대시보드용 요약 데이터(docs/data/summary.json) 생성"""
import csv, glob, json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
RAW = Path("data/raw")
OUT = Path("docs/data")
FIRST = "2024-01"
GROUPS = ["원자력", "석탄", "LNG", "신재생(기타)", "태양광(시장)", "풍력", "기타"]

def mk(d):
    d = str(d).replace("-", "")
    return f"{d[:4]}-{d[4:6]}"

def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def z24():
    return [0.0] * 24

# 1) 발전용 가스요금(원/㎥)
gas = {}
p = Path("data/manual/gas_price.csv")
if p.exists():
    with p.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = num(r.get("gas_won_per_m3"))
            if v:
                gas[r["month"].strip()] = v

# 2) 시간별 SMP + 육지 수요예측
smp_s, smp_n = defaultdict(z24), defaultdict(z24)
dem_s, dem_n = defaultdict(z24), defaultdict(z24)
smp_days = defaultdict(set)
for fp in glob.glob(str(RAW / "smp" / "*" / "*.json")):
    try:
        rows = json.loads(Path(fp).read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(rows, dict):
        rows = rows.get("items") or rows.get("item") or []
    for r in rows:
        if r.get("areaName") != "육지":
            continue
        h = int(num(r.get("hour")) or 0) - 1
        v = num(r.get("smp"))
        if not (0 <= h < 24) or v is None:
            continue
        m = mk(r["date"])
        smp_s[m][h] += v
        smp_n[m][h] += 1
        smp_days[m].add(str(r["date"]))
        d = num(r.get("mlfd"))
        if d:
            dem_s[m][h] += d
            dem_n[m][h] += 1

# 3) 5분 단위 발전원별 발전량(KPX 실시간 수급현황)
gen_s = defaultdict(lambda: {g: z24() for g in GROUPS + ["숨은태양광"]})
gen_n = defaultdict(z24)
gen_days = defaultdict(set)
for fp in glob.glob(str(RAW / "kpx_live" / "*" / "*.csv")):
    with open(fp, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = (r.get("regDate") or "").strip()
            if len(t) < 13:
                continue
            m, h = t[:7], int(t[11:13])
            g = lambda k: num(r.get(k)) or 0.0
            coal = g("totCoal") if r.get("totCoal") not in (None, "") else g("coal") + g("localCoal")
            vals = {
                "원자력": g("nuclearPower"), "석탄": coal, "LNG": g("gas"),
                "신재생(기타)": g("newRenewable"), "태양광(시장)": g("sunlight"),
                "풍력": g("windPower"),
                "기타": g("waterPower") + g("raisingWater") + g("oil") + g("essMw"),
                "숨은태양광": g("ppa") + g("btm"),
            }
            for k, v in vals.items():
                gen_s[m][k][h] += v
            gen_n[m][h] += 1
            gen_days[m].add(t[:10])

# 4) 연료원별 SMP 결정 횟수(육지)
fuel = defaultdict(lambda: defaultdict(int))
fp = RAW / "fuel" / "smp_dec_by_fuel.json"
if fp.exists():
    for r in json.loads(fp.read_text(encoding="utf-8")):
        if r.get("areaNm") != "육지":
            continue
        m = mk(r.get("tradeDay", ""))
        if m < FIRST:
            continue
        t = r.get("fuelType", "")
        k = "LNG" if "LNG" in t else ("석탄" if "탄" in t else "기타")
        fuel[m][k] += int(num(r.get("cnt")) or 0)

def avg(s, n):
    return [round(s[i] / n[i], 2) if n[i] else None for i in range(24)]

def mean(lst):
    v = [x for x in lst if x is not None]
    return round(sum(v) / len(v), 2) if v else None

months = sorted(m for m in set(gas) | set(smp_s) | set(gen_s) | set(fuel) if m >= FIRST)
out = {"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "months": months, "m": {}}
for m in months:
    e = {"gas": gas.get(m), "fuel": dict(fuel[m]) if m in fuel else None,
         "smpDays": len(smp_days[m]), "genDays": len(gen_days[m])}
    if m in smp_s:
        e["smpH"] = avg(smp_s[m], smp_n[m])
        cnt = sum(smp_n[m])
        e["smp"] = round(sum(smp_s[m]) / cnt, 2) if cnt else None
        e["demH"] = avg(dem_s[m], dem_n[m])
        e["dem"] = mean(e["demH"])
    if m in gen_s:
        e["genH"] = {k: avg(v, gen_n[m]) for k, v in gen_s[m].items()}
    out["m"][m] = e

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "summary.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(months)}개월 요약 저장 · SMP {sum(1 for m in months if out['m'][m]['smpDays'])}개월 · 발전량 {sum(1 for m in months if out['m'][m]['genDays'])}개월")
