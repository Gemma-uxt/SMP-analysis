"""월간 상세 보고서 자동 생성 (docs/YYYY-MM.html, docs/report.html)

- 이번 달: 어제까지 자료로 '잠정' 보고서를 매번 새로 만듦 (자료 10일 미만이면 건너뜀)
- 지난달: 한 달 자료가 다 모이면 '확정' 보고서로 한 번 더 만들고 이후로는 고정
  → 예) 12월 1일 오전 자동 갱신 때 11월 보고서가 11.1~11.30 확정본으로 바뀜
- docs/report.html = 가장 최근 달 보고서 (매월 1~10일은 지난달 확정본, 이후는 이번 달 잠정본)
- 숫자·문장은 모두 자료에서 자동 계산. 그 달에만 붙일 해석은 data/manual/report_notes.json
- 태양광 설비 실적은 data/manual/solar_capacity.json (연말 실적이 나오면 한 줄 추가)
"""
import bisect, calendar, csv, json, re, shutil, statistics as st
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
RAW, MAN, DOCS = Path("data/raw"), Path("data/manual"), Path("docs")
TPL = Path("scripts/report_template.html")
STATE = DOCS / "data" / "reports.json"
mean = st.mean
DOW = "월화수목금토일"

S = json.loads((DOCS / "data" / "summary.json").read_text(encoding="utf-8"))
SMP, GAS, WX = S["smp"], S["gas"], S.get("wx", {})
_ix = (DOCS / "index.html").read_text(encoding="utf-8")
HOL = set(re.findall(r"'(\d{4}-\d\d-\d\d)'", re.search(r"const HOL=new Set\(\[(.*?)\]\)", _ix, re.S).group(1)))
CAP = json.loads((MAN / "solar_capacity.json").read_text(encoding="utf-8"))
NOTES = json.loads((MAN / "report_notes.json").read_text(encoding="utf-8")) if (MAN / "report_notes.json").exists() else {}
MAINT = json.loads((DOCS / "data" / "maint.json").read_text(encoding="utf-8")) if (DOCS / "data" / "maint.json").exists() else {}


# ---------------- 공통 ----------------
def num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def rng(a, b):
    a, b = date.fromisoformat(a), date.fromisoformat(b)
    out = []
    while a <= b:
        out.append(a.isoformat()); a += timedelta(1)
    return out


def is_wd(d):
    return date.fromisoformat(d).weekday() < 5 and d not in HOL


def f1(v):
    return "-" if v is None else f"{v:,.1f}"


def sg(v, unit=""):
    return ("+" if v > 0 else "▼" if v < 0 else "") + f1(abs(v)) + unit


def mlen(m):
    return calendar.monthrange(int(m[:4]), int(m[5:]))[1]


def shift(m, k):
    y, mo = int(m[:4]), int(m[5:]) + k
    while mo < 1: y, mo = y - 1, mo + 12
    while mo > 12: y, mo = y + 1, mo - 12
    return f"{y}-{mo:02d}"


def mo_n(m):
    return int(m[5:])


# 시간별 발전원 출력: 실시간 수급현황(5분) + 빠진 시간은 공공데이터포털 5분 발전량
LF = {"nuc": "nuclearPower", "lng": "gas", "oil": "oil", "hydro": "waterPower", "pump": "raisingWater",
      "renew": "newRenewable", "wind": "windPower", "sunM": "sunlight", "ppa": "ppa", "btm": "btm", "ess": "essMw"}
_HC = {}


def hourly(d):
    if d in _HC:
        return _HC[d]
    ds = d.replace("-", "")
    acc, cnt = defaultdict(lambda: defaultdict(float)), defaultdict(int)
    p = RAW / "kpx_live" / ds[:4] / f"{ds}.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = (r.get("regDate") or "").strip()
                if len(t) < 13:
                    continue
                h = int(t[11:13])
                for k, c in LF.items():
                    acc[h][k] += num(r.get(c)) or 0
                tc = num(r.get("totCoal"))
                acc[h]["coal"] += tc if tc is not None else (num(r.get("coal")) or 0) + (num(r.get("localCoal")) or 0)
                cnt[h] += 1
    out = {h: {k: v / cnt[h] for k, v in acc[h].items()} for h in acc if cnt[h] >= 6}
    p = RAW / "gen5m" / ds[:4] / f"{ds}.csv"
    if p.exists():
        a2, c2 = defaultdict(lambda: defaultdict(float)), defaultdict(int)
        with p.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                t = str(r.get("datetime", ""))
                if len(t) < 10:
                    continue
                h = int(t[8:10])
                a2[h]["nuc"] += num(r.get("nuclear")) or 0
                a2[h]["coal"] += (num(r.get("coal")) or 0) + (num(r.get("domestic_coal")) or 0)
                for k, c in (("lng", "gas"), ("oil", "oil"), ("hydro", "hydro"), ("pump", "pumped"), ("sunM", "solar"), ("renew", "renewable")):
                    a2[h][k] += num(r.get(c)) or 0
                c2[h] += 1
        for h in a2:
            if h not in out and c2[h] >= 6:
                v = {k: x / c2[h] for k, x in a2[h].items()}
                v.update(wind=0.0, ppa=0.0, btm=0.0, ess=0.0)
                out[h] = v
    for v in out.values():
        v["dem"] = sum(v.get(k, 0) for k in ("nuc", "coal", "lng", "oil", "hydro", "pump", "renew", "wind", "sunM", "ess"))
        v["hid"] = v.get("ppa", 0) + v.get("btm", 0)
    _HC[d] = out
    return out


def fuel_counts(a, b):
    p = RAW / "fuel" / "smp_dec_by_fuel.json"
    c = Counter()
    if not p.exists():
        return {}
    for r in json.loads(p.read_text(encoding="utf-8")):
        td = str(r.get("tradeDay", ""))
        d = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
        if r.get("areaNm") == "육지" and a <= d <= b:
            t = str(r.get("fuelType", ""))
            c["LNG" if "LNG" in t else "석탄" if "탄" in t else "기타"] += int(num(r.get("cnt")) or 0)
    return dict(c)


# ---------------- 한 달 분석 ----------------
K = ["nuc", "coal", "lng", "oil", "hydro", "pump", "renew", "wind", "sunM", "hid", "ess", "dem"]


def period(m, cut):
    return f"{m}-01", f"{m}-{cut:02d}"


def wd_days(a, b):
    return [d for d in rng(a, b) if d in SMP and is_wd(d)]


def smp_wd(a, b):
    ds = wd_days(a, b)
    return mean(v for d in ds for v in SMP[d]) if ds else None


def stats(a, b):
    ds = wd_days(a, b)
    H = {d: hourly(d) for d in ds}
    hrs = [(d, h) for d in ds for h in H[d]]
    day = [(d, h) for d, h in hrs if 9 <= h <= 15]
    g = {"all": {x: mean(H[d][h].get(x, 0) for d, h in hrs) / 1000 for x in K},
         "day": {x: mean(H[d][h].get(x, 0) for d, h in day) / 1000 for x in K}}
    pk = max(((H[d][h]["dem"], d, h) for d, h in hrs), default=(0, None, None))
    dprof = [round(mean(H[d][h]["dem"] for d in ds if h in H[d]) / 1000, 1) if any(h in H[d] for d in ds) else None for h in range(24)]
    bins = defaultdict(list)
    for d, h in hrs:
        bins[int(H[d][h]["dem"] / 1000 // 5 * 5)].append(SMP[d][h])
    vs = [v for d in ds for v in SMP[d]]
    n = len(vs)
    allrng = rng(a, b)
    return dict(
        n=len(ds), smp=mean(vs), day=mean(v for d in ds for v in SMP[d][9:17]), night=mean(v for d in ds for v in SMP[d][0:7]),
        eve=mean(H[d][h]["dem"] for d in ds for h in (16, 17, 18) if h in H[d]) / 1000,
        prof=[round(mean(SMP[d][h] for d in ds), 1) for h in range(24)],
        p160=[round(sum(SMP[d][h] >= 160 for d in ds) / len(ds) * 100) for h in range(24)],
        band=[round(sum(v < 110 for v in vs) / n * 100, 1), round(sum(110 <= v < 160 for v in vs) / n * 100, 1), round(sum(v >= 160 for v in vs) / n * 100, 1)],
        gen={p: {k: round(v, 2) for k, v in x.items()} for p, x in g.items()}, peak=round(pk[0] / 1000, 1), peakD=pk[1], peakH=pk[2],
        dprof=dprof, bins={b_: [round(mean(v), 1), len(v)] for b_, v in sorted(bins.items()) if len(v) >= 10},
        fuel=fuel_counts(a, b),
        wx={k: round(mean(WX[d][k] for d in allrng if d in WX and WX[d].get(k) is not None), 1) for k in ("tmp", "rad", "sun")},
        missing=sum(24 - len(H[d]) for d in ds))


# ---------------- 중장기 태양광 시나리오 ----------------
def scenario(year, b):
    R = []
    for d in rng(f"{year}-01-01", b):
        if d not in SMP:
            continue
        H = hourly(d)
        for h, v in H.items():
            R.append(dict(d=d, m=d[:7], h=h, wd=is_wd(d), smp=SMP[d][h], nl=v["dem"] - v.get("sunM", 0) - v.get("wind", 0), sol=v.get("sunM", 0) + v["hid"]))
    NMIN, KN = min(r["nl"] for r in R), 40
    bym = defaultdict(list)
    for r in R:
        bym[r["m"]].append(r)
    for v in bym.values():
        v.sort(key=lambda r: r["nl"])
    mnl = {m: [r["nl"] for r in v] for m, v in bym.items()}
    mlo = {m: v[int(len(v) * 0.02)]["nl"] for m, v in bym.items()}
    pool = sorted(R, key=lambda r: r["nl"]); pnl = [r["nl"] for r in pool]

    def knn(arr, nls, x, excl):
        i = bisect.bisect_left(nls, x); lo, hi = i - 1, i; out = []
        while len(out) < KN and (lo >= 0 or hi < len(arr)):
            if hi >= len(arr) or (lo >= 0 and x - nls[lo] <= nls[hi] - x):
                c = arr[lo]; lo -= 1
            else:
                c = arr[hi]; hi += 1
            if c["d"] != excl:
                out.append(c["smp"])
        return mean(out)

    def price(m, x, excl):
        return knn(bym[m], mnl[m], x, excl) if x >= mlo[m] else knn(pool, pnl, max(x, NMIN), excl)

    # 검증(같은 날은 빼고 재현)
    pm = defaultdict(list); am = defaultdict(list)
    for r in R:
        pm[r["m"]].append(price(r["m"], r["nl"], r["d"])); am[r["m"]].append(r["smp"])
    err = max(abs(mean(pm[m]) - mean(am[m])) for m in am)

    end = {int(k): v for k, v in CAP["end"].items()}
    ys = sorted(end)[-4:]
    add = (end[ys[-1]] - end[ys[0]]) / (len(ys) - 1)
    cagr = (end[ys[-1]] / end[ys[0]]) ** (1 / (len(ys) - 1)) - 1
    last = ys[-1]
    fr = (date.fromisoformat(b) - date(year, 1, 1)).days / 365 / 2

    def lin_end(y):
        return end[y] if y in end else end[last] + add * (y - last)

    def cg_end(y):
        return end[y] if y in end else end[last] * (1 + cagr) ** (y - last)

    target = max(2030, year + 4)
    YRS = list(range(year, target + 1))
    res = {}
    for c, fe in (("lin", lin_end), ("cg", cg_end)):
        eff = {y: fe(y - 1) + (fe(y) - fe(y - 1)) * fr for y in YRS}
        res[c] = {}
        for y in YRS:
            k = eff[y] / eff[year]; out = []
            for r in R:
                nl = r["nl"] - (k - 1) * r["sol"]
                out.append((r, price(r["m"], nl, r["d"]), max(0, NMIN - nl)))
            A = lambda f: round(mean(p for r, p, cu in out if f(r)), 1)
            res[c][y] = dict(k=round(k, 3), all=A(lambda r: True), day=A(lambda r: 9 <= r["h"] <= 16),
                             all0=round(mean(0 if cu > 0 else p for r, p, cu in out), 1),
                             curH=sum(1 for r, p, cu in out if cu > 0), curTWh=round(sum(cu for r, p, cu in out) / 1e6, 2),
                             hiH=sum(1 for r, p, cu in out if p >= 150),
                             mon={m: round(mean(p for r, p, cu in out if r["m"] == m), 1) for m in sorted(bym)},
                             prof=[round(mean(p for r, p, cu in out if r["h"] == h), 1) for h in range(24)])
    low = sorted(R, key=lambda r: r["nl"])[:30]
    lowp = sorted(r["smp"] for r in low)
    return dict(year=year, b=b, NMIN=round(NMIN / 1000, 1), err=round(err, 1), add=round(add, 2), cagr=round(cagr * 100, 1),
                adds=[round(end[ys[i]] - end[ys[i - 1]], 1) for i in range(1, len(ys))], ys=ys, end={str(k): v for k, v in end.items()},
                lin_end={str(y): round(lin_end(y), 1) for y in range(ys[0], target + 1)}, cg_end={str(y): round(cg_end(y), 1) for y in range(ys[0], target + 1)},
                target=target, res=res, low_nl=[round(low[0]["nl"] / 1000, 1), round(low[-1]["nl"] / 1000, 1)],
                low_smp=[round(lowp[len(lowp) // 4]), round(lowp[len(lowp) * 3 // 4])],
                act=round(mean(r["smp"] for r in R), 1))


# ---------------- 보고서 한 개 만들기 ----------------
def build(m, today):
    days = [d for d in rng(f"{m}-01", f"{m}-{mlen(m):02d}") if d in SMP and d < today]
    if not days:
        return None
    cut = int(days[-1][8:])
    # 한 달 자료가 다 모이면 확정. 발전량 몇 시간이 끝내 비어도 월말 3일 뒤에는 확정(빠진 시간은 주석으로 표시)
    late = date.fromisoformat(today) >= date.fromisoformat(f"{m}-{mlen(m):02d}") + timedelta(days=3)
    final = cut == mlen(m) and (late or all(len(hourly(d)) >= 23 for d in days))
    pm, ly = shift(m, -1), shift(m, -12)
    a, b = period(m, cut)
    a0, b0 = period(ly, min(cut, mlen(ly)))
    pa, pb = period(pm, mlen(pm))
    cur, prv, lyr = stats(a, b), stats(pa, pb), stats(a0, b0)
    yr, mo, pmo = int(m[:4]), mo_n(m), mo_n(pm)
    M_, P_ = f"{mo}월", f"{pmo}월"
    lab = dict(cur=f"{yr}년 {mo}월", prev=f"{pm[:4]}년 {pmo}월", ly=f"{yr-1}년 {mo}월",
               curS=f"{str(yr)[2:]}년 {mo}월", prevS=f"{pm[2:4]}년 {pmo}월", lyS=f"{str(yr-1)[2:]}년 {mo}월",
               curT=f"{str(yr)[2:]}.{mo}", prevT=f"{pm[2:4]}.{pmo}", lyT=f"{str(yr-1)[2:]}.{mo}", curM=M_, prevM=P_)
    per = f"{mo}.1~{mo}.{cut}"
    state = "확정" if final else "잠정"

    # 월별 추이(올해 1월~이번 달)
    monthly = []
    for i in range(1, mo + 1):
        mm = f"{yr}-{i:02d}"; c = cut if mm == m else mlen(mm)
        s1, s0 = smp_wd(*period(mm, c)), smp_wd(*period(shift(mm, -12), min(c, mlen(shift(mm, -12)))))
        g1, g0 = GAS.get(mm), GAS.get(shift(mm, -12))
        if None in (s1, s0, g1, g0):
            continue
        monthly.append(dict(m=i, partial=(mm == m and not final), smp=round(s1, 1), smp0=round(s0, 1), gas=g1, gas0=g0,
                            lngYoY=round((g1 / g0 - 1) * 100, 1), smpYoY=round((s1 / s0 - 1) * 100, 1),
                            ratio=round(s1 / g1 * 100, 1), ratio0=round(s0 / g0 * 100, 1)))
    N = monthly[-1]; Pm = monthly[-2] if len(monthly) > 1 else None
    mom = (cur["smp"] / prv["smp"] - 1) * 100

    # 일별(이번 달 시작 60일 전부터)
    st_d = (date.fromisoformat(a) - timedelta(days=61)).isoformat()
    daily = [[d, round(mean(SMP[d][9:17]), 1), is_wd(d), (WX.get(d) or {}).get("tmp")] for d in rng(st_d, b) if d in SMP]

    # 특이 시간
    allh = [(SMP[d][h], d, h) for d in rng(a, b) if d in SMP for h in range(24)]
    mx, mn = max(allh), min(allh)

    def at(d, h, k):
        v = hourly(d).get(h)
        return v[k] / 1000 if v and k in v else None

    sc = scenario(yr, b)
    T = text(m, lab, per, state, final, cut, cur, prv, lyr, N, Pm, mom, mx, mn, at, sc)
    D = dict(lab=lab, monthly=monthly, prof=dict(cur=cur["prof"], prev=prv["prof"], ly=lyr["prof"]),
             band=dict(cur=cur["band"], prev=prv["band"], ly=lyr["band"]), dprof=dict(cur=cur["dprof"], prev=prv["dprof"]),
             gen=dict(cur=cur["gen"], prev=prv["gen"], ly=lyr["gen"]), wx=dict(cur=cur["wx"], prev=prv["wx"]),
             bins=dict(cur=cur["bins"], prev=prv["bins"], ly=lyr["bins"]), fuel=dict(cur=cur["fuel"], prev=prv["fuel"], ly=lyr["fuel"]),
             peak=dict(cur=cur["peak"], ly=lyr["peak"], curD=cur["peakD"]), daily=daily, monthStart=a, scen=sc)
    html = TPL.read_text(encoding="utf-8")
    for k, v in T.items():
        html = html.replace("{{" + k + "}}", v)
    html = html.replace("__DATA__", json.dumps(D, ensure_ascii=False, separators=(",", ":")))
    left = re.findall(r"\{\{\w+\}\}", html)
    if left:
        raise SystemExit("채우지 못한 자리: " + ", ".join(sorted(set(left))))
    (DOCS / f"{m}.html").write_text(html, encoding="utf-8")
    return dict(m=m, final=final, period=per, title=lab["cur"])


def text(m, lab, per, state, final, cut, cur, prv, lyr, N, Pm, mom, mx, mn, at, sc):
    note = NOTES.get(m, {})
    M_, P_ = lab["curM"], lab["prevM"]
    T = {}
    T["title"] = f"{lab['cur']}" + ("" if final else f"(1~{cut}일)")
    T["cover_tags"] = (f"<span>{state} · {per} 실적</span><span>비교: 전년 {per} · {lab['prev']}</span>"
                       "<span>근거: KPX·한국가스공사·Open-Meteo</span>")
    T["per_note"] = f"{per} {state} · 전년 같은 기간 대비"
    # 1
    rp, rc = (Pm["ratio"] if Pm else None), N["ratio"]
    if rp is None:
        tail = f"SMP/LNG 비율 {f1(rc)}"
    elif rc > rp + 0.5:
        tail = f"가스요금이 SMP에 더 반영되며 SMP/LNG 비율 {f1(rp)} → {f1(rc)}"
    elif rc < rp - 0.5:
        tail = f"가스요금보다 SMP가 약세, SMP/LNG 비율 {f1(rp)} → {f1(rc)}"
    else:
        tail = f"SMP/LNG 비율 {f1(rp)} → {f1(rc)}로 비슷"
    T["s1_msg"] = f"{M_} 평일 SMP <span class=\"em\">{f1(N['smp'])}원</span>(전년 {sg(N['smpYoY'], '%')}, {P_} 대비 {sg(mom, '%')}) — {tail}"
    T["s1_note"] = (f"※ 평일 = 주말·공휴일 제외({M_} {cur['n']}일, 전년 {lyr['n']}일) · SMP: KPX 육지 시간별 · "
                    "LNG: 한국가스공사 발전용(일반발전) · 계통수요 피크는 평일 시간 평균 최대")
    T["ratio_sub"] = "평일 SMP(원/kWh) ÷ LNG(원/㎥) × 100" + ("" if final else f" · {M_}은 1~{cut}일")
    # 2
    dd, dn = cur["day"] - prv["day"], cur["night"] - prv["night"]
    up = "상승" if mom >= 0 else "하락"
    T["s2_title"] = f"{M_} SMP {up}은 어디서 왔나"
    if abs(dd) >= 5 and abs(dd) >= 2 * abs(dn):
        T["s2_msg"] = (f"{'상승' if dd > 0 else '하락'}은 <span class=\"em\">평일 낮(9~17시)</span>에 집중 — 낮 SMP {f1(prv['day'])} → "
                       f"<span class=\"{'rd' if dd > 0 else 'dn'}\">{f1(cur['day'])}원</span>, 새벽(1~7시)은 {f1(prv['night'])} → {f1(cur['night'])}원")
    else:
        T["s2_msg"] = f"낮(9~17시) SMP {f1(prv['day'])} → {f1(cur['day'])}원, 새벽(1~7시) {f1(prv['night'])} → {f1(cur['night'])}원 — 하루 전반이 함께 {'상승' if dd + dn > 0 else '하락'}"
    hh = [h for h in range(24) if cur["p160"][h] >= 50]
    b2 = [f"160원 이상 시간: {P_} <b>{f1(prv['band'][2])}%</b> → {M_} <b class=\"rd\">{f1(cur['band'][2])}%</b> (전년 {M_} {f1(lyr['band'][2])}%)"]
    if hh:
        b2.append(f"{M_} 평일 {hh[0]+1}~{hh[-1]+1}시 SMP는 절반 이상의 날에 160원 이상")
    b2.append("160원 이상은 주로 LNG 평균요금제 복합 발전가격대(발전가격 순위표 기준)")
    T["s2_bullets"] = "".join(f"<li>{x}</li>" for x in b2)
    # 3-1
    gc, gp = cur["gen"]["day"], prv["gen"]["day"]
    sol_c, sol_p = gc["sunM"] + gc["hid"], gp["sunM"] + gp["hid"]
    dD = gc["dem"] - gp["dem"]
    T["s3_msg"] = (f"평일 낮 계통수요 <span class=\"em\">{sg(dD, 'GW')}</span> ({f1(gp['dem'])} → {f1(gc['dem'])}GW) — "
                   f"태양광 {f1(sol_p)} → {f1(sol_c)}GW, 서울 기온 {f1(prv['wx']['tmp'])} → {f1(cur['wx']['tmp'])}℃")
    T["s3_bullets"] = (f"<li>낮 시간 태양광(시장+시장 외) <b>{f1(sol_p)} → {f1(sol_c)}GW</b>"
                       f"{', 그만큼 계통이 더 공급' if sol_c < sol_p else ', 그만큼 계통 부담 감소'}</li>"
                       f"<li>저녁(17~19시) 계통수요 {f1(prv['eve'])} → {f1(cur['eve'])}GW</li>")
    T["s3_note"] = ("※ 계통수요 = KPX 실시간 수급현황 발전원 합 · 시장 외 태양광 = 한전 PPA·자가용(BTM, KPX 추정) · "
                    "날씨: Open-Meteo, 기온은 서울, 일사량은 목포·전주·서산·안동 평균"
                    + (f" · 발전량 미수집 {cur['missing']}시간 제외" if cur["missing"] else ""))
    T["sub_31"] = f"평일 · {P_} → {M_}"
    # 3-2
    common = [k for k in cur["bins"] if k in prv["bins"]]
    diffs = [cur["bins"][k][0] - prv["bins"][k][0] for k in common]
    if diffs:
        lo, hi = min(diffs), max(diffs)
        if lo > 0:
            s4 = f"수요가 같아도 {M_} SMP가 <span class=\"rd\">{round(lo)}~{round(hi)}원 높음</span>"
        elif hi < 0:
            s4 = f"수요가 같아도 {M_} SMP가 <span class=\"dn\">{round(-hi)}~{round(-lo)}원 낮음</span>"
        else:
            s4 = f"같은 수요 구간의 SMP 차이 {round(lo)}~{round(hi)}원"
    else:
        lo = hi = 0; s4 = "같은 수요 구간 비교 자료 부족"
    if note.get("s4_msg_tail"):
        s4 += " — " + note["s4_msg_tail"]
    T["s4_msg"] = s4

    def share(f):
        t = sum(f.values()) or 1
        return f.get("LNG", 0) / t * 100
    T["s4_bullets"] = "".join(f"<li>{x}</li>" for x in
                              [f"LNG가 가격을 정한 비중 {P_} {f1(share(prv['fuel']))}% → {M_} {f1(share(cur['fuel']))}%"] + note.get("s4_bullets", []))
    T["s4_note"] = f"※ 수요 구간: 5GW 단위(예: 65 = 65~70GW), 10시간 이상 구간만 표시 · 연료별 결정: EPSIS 연료원별 SMP 결정 횟수({per})"
    # 3-3
    ga, gl = cur["gen"]["all"], lyr["gen"]["all"]
    dB = ga["nuc"] + ga["coal"] - gl["nuc"] - gl["coal"]; dL = ga["lng"] - gl["lng"]
    T["s5_msg"] = f"전년 {M_}보다 원전·석탄 <span class=\"em\">{sg(dB, 'GW')}</span>, LNG <span class=\"{'dn' if dL < 0 else 'rd'}\">{sg(dL, 'GW')}</span> (평일 평균 출력)"
    T["s5_bullets"] = (f"<li>{P_} → {M_} 낮 출력: LNG {f1(gp['lng'])} → {f1(gc['lng'])}GW({sg(gc['lng']-gp['lng'])}), "
                       f"석탄 {f1(gp['coal'])} → {f1(gc['coal'])}GW({sg(gc['coal']-gp['coal'])}), 태양광 {f1(sol_p)} → {f1(sol_c)}GW</li>"
                       f"<li>LNG 단가 전년 대비 {sg(N['lngYoY'], '%')} — LNG가 가격을 정하는 시간의 SMP 수준을 좌우</li>")
    # 4
    dl_wd = [x for x in [mean(SMP[d][9:17]) for d in rng(f"{m}-01", f"{m}-{cut:02d}") if d in SMP and is_wd(d)]]
    dl_we = [x for x in [mean(SMP[d][9:17]) for d in rng(f"{m}-01", f"{m}-{cut:02d}") if d in SMP and not is_wd(d)]]
    T["s6_sub"] = f"{date.fromisoformat(f'{m}-01') - timedelta(days=61):%-m.%-d}~{per.split('~')[1]}"
    T["s6_msg"] = (f"{M_} 낮(9~17시) 평균 SMP: 평일 <span class=\"em\">{f1(mean(dl_wd))}원</span>"
                   + (f" · 주말·공휴일 {f1(mean(dl_we))}원" if dl_we else ""))
    v, d, h = mx
    dt = date.fromisoformat(d)
    T["s6_box1_t"] = f"{dt.month}/{dt.day}({DOW[dt.weekday()]}) {M_} 최고 {f1(v)}원"
    T["s6_box1"] = (f"<li>{h+1}시 SMP {f1(v)}원 · 서울 평균기온 {f1((WX.get(d) or {}).get('tmp'))}℃</li>"
                    f"<li>{M_} 평일 계통수요 최대 {f1(cur['peak'])}GW"
                    + (f" ({int(cur['peakD'][5:7])}/{int(cur['peakD'][8:])} {cur['peakH']+1}시)" if cur["peakD"] else "")
                    + f", 전년 {M_} {f1(lyr['peak'])}GW</li>")
    v, d, h = mn
    dt = date.fromisoformat(d)
    dm, so = at(d, h, "dem"), None
    hv = hourly(d).get(h)
    if hv:
        so = (hv.get("sunM", 0) + hv["hid"]) / 1000
    T["s6_box2_t"] = f"{dt.month}/{dt.day}({DOW[dt.weekday()]}) {h+1}시 {M_} 최저 {f1(v)}원"
    T["s6_box2"] = (f"<li>{'평일' if is_wd(d) else '주말·공휴일'} · 계통수요 {f1(dm)}GW, 태양광(시장+시장 외) {f1(so)}GW</li>"
                    "<li>수요가 낮고 태양광이 많은 시간에 저가 입찰 발전기가 가격을 정한 것으로 추정</li>")
    # 5 시나리오
    y0, tg = sc["year"], sc["target"]
    L, G = sc["res"]["lin"], sc["res"]["cg"]
    base = L[y0]
    lo_e, hi_e = sc["lin_end"][str(tg)], sc["cg_end"][str(tg)]
    T["s8_msg"] = (f"최근 3년 태양광은 연 {' → '.join(f1(x) for x in sc['adds'])}GW 증가(평균 <span class=\"em\">+{f1(sc['add'])}GW</span>, 연 {f1(sc['cagr'])}%) "
                   f"— 이 속도면 {tg}년 말 <span class=\"em\">{round(lo_e)}~{round(hi_e)}GW</span>")
    T["s8_sub"] = f"GW · {sc['ys'][0]}~{sc['ys'][-1]} 실적, {sc['ys'][-1]+1}~{tg} 시나리오"
    T["s8_rows"] = (f"<tr><td>기본(정량 증가)</td><td class=\"tl\">매년 <b>+{f1(sc['add'])}GW</b>({sc['ys'][1]}~{str(sc['ys'][-1])[2:]} 평균)</td></tr>"
                    f"<tr><td>상단(비율 증가)</td><td class=\"tl\">매년 <b>+{f1(sc['cagr'])}%</b>({sc['ys'][0]}→{str(sc['ys'][-1])[2:]} 연평균 증가율)</td></tr>"
                    f"<tr><td>날씨</td><td class=\"tl\">올해(1.1~{per.split('~')[1]}) 시간별 일사·기온 그대로</td></tr>"
                    "<tr><td>태양광 출력</td><td class=\"tl\">시장·PPA·자가용 모두 설비 배수만큼 증가</td></tr>"
                    "<tr><td>그 외</td><td class=\"tl\">수요·연료비·원전/석탄 설비·입찰 행태 <b>올해와 동일</b></td></tr>"
                    f"<tr><td>출력제어</td><td class=\"tl\">순부하가 올해 최저({f1(sc['NMIN'])}GW) 아래면 그만큼 출력제어</td></tr>")
    T["s8_valid"] = f"같은 방법으로 올해 실적을 재현하면 월평균 SMP 오차 ±{f1(sc['err'])}원 이내"
    T["s8_note"] = ("※ 설비: " + CAP.get("_출처", "") +
                    (f" · 정부의 2030년 목표({f1(CAP['gov_target_2030'])}GW)는 이 시나리오보다 훨씬 큼" if CAP.get("gov_target_2030") else ""))
    a1, a2 = base["all"] - L[tg]["all"], base["all"] - G[tg]["all"]
    d1, d2 = base["day"] - L[tg]["day"], base["day"] - G[tg]["day"]
    T["s9_sub"] = f"올해 연료비·수요 기준 · 1.1~{per.split('~')[1]} 시간 평균"
    T["s9_msg"] = (f"{tg}년 연평균 SMP는 <span class=\"em\">{round(min(a1, a2))}~{round(max(a1, a2))}원"
                   f"(▼{f1(min(a1, a2) / base['all'] * 100)}~{f1(max(a1, a2) / base['all'] * 100)}%) 하락</span> — "
                   f"낮 SMP {round(min(d1, d2))}~{round(max(d1, d2))}원 하락, 대신 <span class=\"rd\">출력제어 {f1(L[tg]['curTWh'])}~{f1(G[tg]['curTWh'])}TWh</span> 필요")
    T["s9_bullets"] = (f"<li>SMP 150원 이상 시간: 올해 {base['hiH']}시간 → {tg}년 {min(L[tg]['hiH'], G[tg]['hiH'])}~{max(L[tg]['hiH'], G[tg]['hiH'])}시간</li>"
                       f"<li>출력제어 시간 SMP를 0원으로 보면 {tg}년 연평균 {f1(min(L[tg]['all0'], G[tg]['all0']))}~{f1(max(L[tg]['all0'], G[tg]['all0']))}원</li>")
    drops = {mm: (L[y0]["mon"][mm] - L[tg]["mon"][mm], G[y0]["mon"][mm] - G[tg]["mon"][mm]) for mm in L[y0]["mon"]}
    top = sorted(drops, key=lambda k: -drops[k][1])[:2]
    top_s = "·".join(f"{mo_n(x)}월" for x in sorted(top))
    t_lo = round(min(min(drops[x]) for x in top)); t_hi = round(max(max(drops[x]) for x in top))
    T["s9_bullets2"] = (f"<li><b>하락 폭이 작은 이유</b>: 태양광이 늘면 낮 가격이 LNG 수준에서 석탄·열병합 수준으로 내려오지만 그 아래로는 잘 떨어지지 않음. "
                        f"올해 순부하가 가장 낮았던 시간({f1(sc['low_nl'][0])}~{f1(sc['low_nl'][1])}GW)에도 SMP는 대부분 {sc['low_smp'][0]}~{sc['low_smp'][1]}원</li>"
                        f"<li><b>영향이 큰 달</b>: {top_s}({tg}년 ▼{t_lo}~{t_hi}원) · {M_}은 ▼{round(min(drops[m]))}~{round(max(drops[m]))}원</li>"
                        f"<li><b>남는 위험</b>: 남는 태양광을 줄여야 하는 시간이 {tg}년 {L[tg]['curH']}~{G[tg]['curH']}시간 — 연료비·수요가 그대로라면 SMP 하락보다 출력제어 확대가 더 큰 변화</li>")
    T["s9_note"] = (f"※ 연도별 값은 해당 연도에도 올해(1.1~{per.split('~')[1]})와 같은 날씨·수요가 반복된다고 본 값"
                    + ("" if m.endswith("12") and final else " · 남은 달 미포함") + " · 저장장치(ESS)·양수 확대, 원전 신규·석탄 폐지, LMP 도입은 미반영")
    # 6 종합
    T["s7_msg"] = note.get("s7_msg") or T["s1_msg"]
    rows = list(note.get("s7_rows_before", []))
    rows.append([M_, f"평일 SMP <strong>{f1(N['smp'])}원</strong>(전년 {sg(N['smpYoY'], '%')}, {P_} 대비 {sg(mom, '%')}) · "
                     f"낮 SMP {f1(prv['day'])} → {f1(cur['day'])}원 · 평일 낮 계통수요 <strong>{sg(dD, 'GW')}</strong>(태양광 {sg(sol_c - sol_p, 'GW')}) · "
                     f"SMP/LNG 비율 {f1(N['ratio'])}"])
    md = mean(diffs) if diffs else 0
    if md >= 10:
        jd = f"같은 수요에서도 SMP가 {round(lo)}~{round(hi)}원 높아 <strong>공급 측(연료비·발전가격 순위) 변화가 주된 요인</strong>, 수요 변화가 이를 드러냄"
    elif md <= -10:
        jd = f"같은 수요에서도 SMP가 {round(-hi)}~{round(-lo)}원 낮아 <strong>공급 측(연료비·발전가격 순위) 변화가 주된 요인</strong>"
    else:
        jd = "같은 수요에서의 SMP 차이가 작아 <strong>수요(태양광·기온) 변화가 주된 요인</strong>"
    if note.get("s7_judge"):
        jd += ". " + note["s7_judge"]
    rows.append(["판단", jd + f". 새벽 SMP는 {f1(cur['night'])}원"])
    rows.append(["중장기", f"태양광이 최근 3년 속도(연 +{f1(sc['add'])}GW~{f1(sc['cagr'])}%)로 늘면 {tg}년 <strong>연평균 SMP는 "
                         f"{round(min(a1, a2))}~{round(max(a1, a2))}원 하락</strong>에 그치고, 효과는 <strong>낮 시간</strong>에 집중. "
                         f"<strong>출력제어가 {f1(L[tg]['curTWh'])}~{f1(G[tg]['curTWh'])}TWh로 확대</strong>"])
    nm = shift(m, 1)
    chk = []
    if GAS.get(nm):
        chk.append(f"{mo_n(nm)}월 발전용 가스요금 {f1(GAS[nm])}원/㎥({sg((GAS[nm] / GAS[m] - 1) * 100, '%')}) 반영 영향")
    else:
        chk.append(f"{mo_n(nm)}월 발전용 가스요금·발전가격 순위 변화")
    chk.append(f"{mo_n(nm)}월 계절 수요 변화({M_} 평일 최대 {f1(cur['peak'])}GW)")
    ends = {}
    end_d = date.fromisoformat(f"{m}-{cut:02d}")
    for u in MAINT.get("units", []):
        if u[1] == "nuc":
            e = date.fromisoformat(u[4][:10])
            if end_d < e <= end_d + timedelta(days=75) and u[3][:10] <= f"{m}-{cut:02d}":
                ends[u[0]] = max(ends.get(u[0], e), e)
    if ends:
        chk.append("원전 정비 복귀 예정(KPX 예방정비계획): " + ", ".join(f"{n} {e.month}/{e.day}" for n, e in sorted(ends.items(), key=lambda x: x[1])))
    rows.append(["점검 사항", "<br>".join("· " + x for x in chk)])
    T["s7_rows"] = "".join(f"<div class=\"cl\"><b>{r[0]}</b><div>{r[1]}</div></div>" for r in rows)
    T["s7_note"] = (f"※ {M_}은 {per} {state} 실적" + ("" if final else "(월말 이후 자동으로 확정본 갱신)") +
                    " · 판단은 공개 자료 기반 추정이며, 개별 발전기 가격결정 여부는 KPX 확정 자료로 확인 필요")
    T["updated"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    return T


def main():
    today = datetime.now(KST).date()
    tdy = today.isoformat()
    cur_m = tdy[:7]
    reps = {r["m"]: r for r in json.loads(STATE.read_text(encoding="utf-8"))} if STATE.exists() else {}
    for m in (shift(cur_m, -1), cur_m):
        if reps.get(m, {}).get("final") and (DOCS / f"{m}.html").exists():
            print(m, "확정본 있음 - 건너뜀"); continue
        n = sum(1 for d in rng(f"{m}-01", f"{m}-{mlen(m):02d}") if d in SMP and d < tdy)
        if n < (5 if m < cur_m else 10) or m < "2026-01":
            print(m, f"자료 {n}일 - 건너뜀"); continue
        r = build(m, tdy)
        if r:
            reps[m] = r
            print(m, "작성", "확정" if r["final"] else "잠정", r["period"])
    lst = sorted(reps.values(), key=lambda r: r["m"])
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = lst[-1] if lst else None  # 가장 최근 달(이번 달은 10일 이후부터 잠정본 생성)
    if latest:
        shutil.copyfile(DOCS / f"{latest['m']}.html", DOCS / "report.html")
        print("report.html ←", latest["m"])


if __name__ == "__main__":
    main()
