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
MINI_HTML = {}
DGR = json.loads(Path("data/manual/demand_growth.json").read_text(encoding="utf-8"))
SMPY = json.loads(Path("data/manual/smp_annual.json").read_text(encoding="utf-8"))
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


# ---------------- 가격결정 발전기 추정 ----------------
# 발전가격 순위표(data/manual/merit_order.json)로 시간별 가격결정 발전기를 추정
# 후보: SMP와 발전가격 차이 5원 이내 → 점수 = 가격 차이(원) + 순위 위치 차이(GW)
#  (순위 위치: 정비 중 발전기를 뺀 순위표에서 그 시간 원전·석탄·LNG·유류 발전량이 닿는 지점)
# SMP에는 무부하·기동가격이 포함돼 순위표 단가와 정확히 같지 않으므로 '추정'임
# 순위표 자체는 저장소에 올리지 않음(.gitignore). 순위표가 있는 PC에서 돌리면 추정 결과만
# data/manual/price_setting_est.json 에 저장하고, 자동 작업은 그 결과 파일만 읽음
MERIT = json.loads((MAN / "merit_order.json").read_text(encoding="utf-8")).get("months", {}) if (MAN / "merit_order.json").exists() else {}
EST_P = MAN / "price_setting_est.json"
EST = json.loads(EST_P.read_text(encoding="utf-8")).get("days", {}) if EST_P.exists() else {}


def flabel(f):
    f = str(f)
    if "핵" in f or "원자" in f:
        return "원자력"
    if "탄" in f:
        return "석탄"
    if "LNG" in f:
        for k, v in (("도시가스", "LNG 도시가스"), ("평균", "LNG 평균"), ("직도입", "LNG 직도입"), ("개별", "LNG 개별"), ("집단", "LNG 집단"), ("발전", "LNG 발전")):
            if k in f:
                return v
        return "LNG"
    return f


def fgroup(lb):
    return "LNG" if lb.startswith("LNG") else lb if lb in ("석탄", "원자력") else "기타"


_FD = None


def fuel_daily():
    global _FD
    if _FD is None:
        _FD = defaultdict(Counter)
        p = RAW / "fuel" / "smp_dec_by_fuel.json"
        if p.exists():
            for r in json.loads(p.read_text(encoding="utf-8")):
                if r.get("areaNm") != "육지":
                    continue
                td = str(r.get("tradeDay", ""))
                t = str(r.get("fuelType", ""))
                g = "LNG" if "LNG" in t else "석탄" if "탄" in t else "원자력" if "원자" in t else "기타"
                _FD[f"{td[:4]}-{td[4:6]}-{td[6:8]}"][g] += int(num(r.get("cnt")) or 0)
    return _FD


def est_hours(d, hours, fit=True):
    """시간별 (시, 발전기, 연료표기). fit=True면 그날 전력거래소 연료별 결정 시간 수에 맞춰 연료 배분을 조정"""
    L = MERIT.get(d[:7])
    if not L or d not in SMP:
        return None
    t = d + "T12:00"
    out = {u[0] for u in MAINT.get("units", []) if u[3] <= t < u[4]}
    A = sorted((r for r in L if r[0] not in out), key=lambda r: r[2])
    cum, c = [], 0.0
    for r in A:
        c += r[3] or 0; cum.append(c - (r[3] or 0) / 2)
    H = hourly(d)
    cand = {}
    for h in range(24):
        v = SMP[d][h]; hv = H.get(h)
        nl = (hv["nuc"] + hv["coal"] + hv["lng"] + hv["oil"]) if hv and A[0][3] else None
        best = {}
        for i, r in enumerate(A):
            dp = abs(r[2] - v)
            if dp > 15:
                continue
            s = dp + (abs(cum[i] - nl) / 1000 if nl else 0) + (0 if dp <= 5 else 10)
            g = fgroup(flabel(r[1]))
            if g not in best or s < best[g][0]:
                best[g] = (s, r)
        if not best:
            r = min(A, key=lambda r: abs(r[2] - v)); best = {fgroup(flabel(r[1])): (0, r)}
        cand[h] = best
    pick = {h: min(b.values(), key=lambda x: x[0]) for h, b in cand.items()}
    act = fuel_daily().get(d) if fit else None
    if act and sum(act.values()) == 24:
        for _ in range(48):
            cnt = Counter(fgroup(flabel(pick[h][1][1])) for h in pick)
            over = [g for g in cnt if cnt[g] > act.get(g, 0)]
            under = [g for g in act if act[g] > cnt.get(g, 0)]
            if not over or not under:
                break
            moves = [(cand[h][g][0] - pick[h][0], h, g) for h in pick if fgroup(flabel(pick[h][1][1])) in over
                     for g in under if g in cand[h]]
            if not moves:
                break
            _, h, g = min(moves); pick[h] = cand[h][g]
    return [(h, pick[h][1][0], flabel(pick[h][1][1])) for h in hours]


def est_day(d):
    """그날 낮(10~17시 SMP 8개 시간) 추정 가격결정 발전기·연료. 순위표가 없으면 저장된 결과 사용"""
    act = dict(fuel_daily().get(d, {}))
    if d[:7] in MERIT and d in SMP:
        e = est_hours(d, range(9, 17))
        cu = Counter((n, lb) for h, n, lb in e)
        fg = Counter(fgroup(lb) for h, n, lb in e)
        rec = {"u": [[n, lb, k] for (n, lb), k in cu.most_common(3)], "f": dict(fg.most_common())}
        raw, a24 = est_hours(d, range(24), fit=False), fuel_daily().get(d)
        if raw and a24:
            pr = Counter(fgroup(lb) for h, n, lb in raw)
            rec["ag"] = sum(min(pr[k], a24[k]) for k in set(pr) | set(a24)); rec["tot"] = sum(a24.values())
        EST[d] = rec
    rec = EST.get(d)
    if not rec:
        return None
    return {"u": rec["u"], "f": rec["f"], "a": act}


def est_accuracy(days):
    ag = sum(EST[d].get("ag", 0) for d in days if d in EST)
    tot = sum(EST[d].get("tot", 0) for d in days if d in EST and "ag" in EST[d])
    return round(ag / tot * 100) if tot else None


# 발전가격 stack(공급곡선) 요약: 순위표가 있는 PC에서 계산해 data/manual/merit_summary.json 에 저장
#  - stack: 5원 단위 가격 구간별 누적 입찰량(GW) · 개별 발전기 단가는 저장하지 않음
#  - movers: 전월 대비 160원 선을 넘어 올라간 발전기(메인 페이지에 이미 공개된 발전기만 단가 표기, 그 외는 가격대)
MS_P = MAN / "merit_summary.json"
MS = json.loads(MS_P.read_text(encoding="utf-8")) if MS_P.exists() else {}
_PUB = set(re.findall(r"\['([^']+)','\w',[\d.]+,[\d.]+\]", re.search(r"const PR=\[(.*?)\];", _ix, re.S).group(1))) if "const PR=[" in _ix else set()


def merit_summary(m):
    if m in MERIT:
        L = [r for r in MERIT[m] if r[3]]
        stack = []
        for p in range(0, 305, 5):
            stack.append([p, round(sum(r[3] for r in L if r[2] < p) / 1000, 2)])
        rec = {"stack": stack, "below160": round(sum(r[3] for r in L if r[2] < 160) / 1000, 1),
               "below110": round(sum(r[3] for r in L if r[2] < 110) / 1000, 1), "total": round(sum(r[3] for r in L) / 1000, 1)}
        pm = shift(m, -1)
        if pm in MERIT:
            po = {r[0]: r for r in MERIT[pm]}
            mv = []
            for r in L:
                if r[0] in po and po[r[0]][2] < 160 <= r[2]:
                    a_, b_ = po[r[0]][2], r[2]
                    pub = r[0] in _PUB
                    mv.append([r[0], flabel(r[1]), round(a_) if pub else f"{int(a_ // 10 * 10)}원대",
                               round(b_) if pub else f"{int(b_ // 10 * 10)}원대", round(r[3])])
            rec["movers"] = sorted(mv, key=lambda x: -x[4])
        MS[m] = rec
    return MS.get(m)


def save_ms():
    if MERIT:
        MS_P.write_text(json.dumps(MS, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def save_est():
    if MERIT:
        EST_P.write_text(json.dumps({"_설명": "발전가격 순위표로 추정한 날짜별 낮 시간 가격결정 발전기(상위 3)·연료 시간 수. "
                                              "ag/tot = 일별 집계 보정 없이 추정한 연료가 전력거래소 일별 연료별 결정 횟수와 일치한 시간/전체 시간",
                                     "days": dict(sorted(EST.items()))}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


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
    def prof(f):
        return [round(mean(f(H[d][h]) for d in ds if h in H[d]) / 1000, 1) if any(h in H[d] for d in ds) else None for h in range(24)]
    dprof = {"dem": prof(lambda v: v["dem"]), "tot": prof(lambda v: v["dem"] + v["hid"]), "net": prof(lambda v: v["dem"] - v.get("sunM", 0))}
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
            R.append(dict(d=d, m=d[:7], h=h, wd=is_wd(d), smp=SMP[d][h], nl=v["dem"] - v.get("sunM", 0) - v.get("wind", 0), sol=v.get("sunM", 0) + v["hid"],
                          tot=v["dem"] + v["hid"], nuc=v.get("nuc", 0), coal=v.get("coal", 0), lng=v.get("lng", 0), pump=v.get("pump", 0)))
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
    DG = {k: v["rate"] for k, v in DGR.items() if k in ("t", "p")}
    for (cp, fe), (dk, g) in [(c_, d_) for c_ in (("lin", lin_end), ("cg", cg_end)) for d_ in DG.items()]:
        c = f"{cp}_{dk}"
        eff = {y: fe(y - 1) + (fe(y) - fe(y - 1)) * fr for y in YRS}
        res[c] = {}
        for y in YRS:
            k = eff[y] / eff[year]; gd = (1 + g) ** (y - year); out = []; nls = []
            for r in R:
                nl = r["nl"] + (gd - 1) * r["tot"] - (k - 1) * r["sol"]
                out.append((r, price(r["m"], nl, r["d"]), max(0, NMIN - nl))); nls.append(max(nl, NMIN))
            A = lambda f: round(mean(p for r, p, cu in out if f(r)), 1)
            res[c][y] = dict(k=round(k, 3), gd=round(gd, 3), all=A(lambda r: True),
                             totGW=round(mean(gd * r["tot"] for r in R) / 1000, 1), nlGW=round(mean(nls) / 1000, 1), day=A(lambda r: 9 <= r["h"] <= 16),
                             all0=round(mean(0 if cu > 0 else p for r, p, cu in out), 1),
                             curH=sum(1 for r, p, cu in out if cu > 0), curTWh=round(sum(cu for r, p, cu in out) / 1e6, 2),
                             hiH=sum(1 for r, p, cu in out if p >= 150),
                             solTWh=round(sum(k * r["sol"] for r in R) / 1e6, 1),
                             curPct=round(sum(cu for r, p, cu in out) / max(1, sum(k * r["sol"] for r in R)) * 100, 1),
                             mon={m: round(mean(p for r, p, cu in out if r["m"] == m), 1) for m in sorted(bym)},
                             prof=[round(mean(p for r, p, cu in out if r["h"] == h), 1) for h in range(24)])
    # 순부하 구간별 올해 실제 SMP (계산 방법 설명용)
    bands = defaultdict(list)
    for r in R:
        bands[int(r["nl"] // 5000) * 5].append(r["smp"])
    nlb = [[f"{g}~{g+5}", round(mean(v), 1), len(v)] for g, v in sorted(bands.items()) if len(v) >= 20]
    low = sorted(R, key=lambda r: r["nl"])[:30]
    mins = {f: round(mean(r[f] for r in low) / 1000, 1) for f in ("nuc", "coal", "lng", "pump")}
    minr = {f: [round(min(r[f] for r in low) / 1000, 1), round(max(r[f] for r in low) / 1000, 1)] for f in ("nuc", "coal", "lng")}
    m0 = low[0]
    minh = dict(d=m0["d"], h=m0["h"], nl=round(m0["nl"] / 1000, 1), **{f: round(m0[f] / 1000, 1) for f in ("nuc", "coal", "lng", "pump")})
    minh["etc"] = round(minh["nl"] - minh["nuc"] - minh["coal"] - minh["lng"] - minh["pump"], 1)
    lowd = [d_ for d_, _ in Counter(r["d"] for r in low).most_common(3)]
    lowinfo = dict(n=len(low), hol=sum(1 for r in low if not r["wd"]), h0=min(r["h"] for r in low), h1=max(r["h"] for r in low), top=lowd,
                   nlavg=round(mean(r["nl"] for r in low) / 1000, 1))
    lowp = sorted(r["smp"] for r in low)
    return dict(year=year, b=b, NMIN=round(NMIN / 1000, 1), err=round(err, 1), add=round(add, 2), cagr=round(cagr * 100, 1),
                adds=[round(end[ys[i]] - end[ys[i - 1]], 1) for i in range(1, len(ys))], ys=ys, end={str(k): v for k, v in end.items()},
                lin_end={str(y): round(lin_end(y), 1) for y in range(ys[0], target + 1)}, cg_end={str(y): round(cg_end(y), 1) for y in range(ys[0], target + 1)},
                target=target, res=res, mins=mins, minr=minr, minh=minh, lowinfo=lowinfo, smpy=SMPY["values"], dg={k: dict(v, rate=round(v["rate"] * 100, 1), raw=v["rate"]) for k, v in DGR.items() if k in ("t", "p")},
                demh={k: v for k, v in DGR.get("hist", {}).items() if not k.startswith("_")}, low_nl=[round(low[0]["nl"] / 1000, 1), round(low[-1]["nl"] / 1000, 1)],
                low_smp=[round(lowp[len(lowp) // 4]), round(lowp[len(lowp) * 3 // 4])],
                act=round(mean(r["smp"] for r in R), 1), nlb=nlb)


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
    daily = [[d, round(mean(SMP[d][9:17]), 1), is_wd(d), (WX.get(d) or {}).get("tmp"), est_day(d)] for d in rng(st_d, b) if d in SMP]
    acc = est_accuracy([d for d in rng(st_d, b) if d in SMP])

    # 특이 시간
    allh = [(SMP[d][h], d, h) for d in rng(a, b) if d in SMP for h in range(24)]
    mx, mn = max(allh), min(allh)

    def at(d, h, k):
        v = hourly(d).get(h)
        return v[k] / 1000 if v and k in v else None

    sc = scenario(yr, b)
    T = text(m, lab, per, state, final, cut, cur, prv, lyr, N, Pm, mom, mx, mn, at, sc)
    T["s6_note"] = ("※ 막대에 마우스를 올리면 그날 낮(10~17시 SMP 8개 시간)의 추정 가격결정 발전기·연료 표시 — "
                    "발전가격 순위표의 단가·순위 위치로 후보를 고르고, 연료별 시간 수는 전력거래소 일별 연료별 결정 횟수에 맞춤"
                    + (f"(일별 집계 없이 추정만 했을 때 연료 일치율 {acc}%)" if acc else "")
                    + " · SMP에는 무부하·기동가격이 포함돼 순위표 단가와 정확히 같지 않으므로 발전기는 추정치"
                    + " · 날씨: Open-Meteo(서울 평균기온)")
    D = dict(lab=lab, monthly=monthly, prof=dict(cur=cur["prof"], prev=prv["prof"], ly=lyr["prof"]),
             band=dict(cur=cur["band"], prev=prv["band"], ly=lyr["band"]), dprof=dict(cur=cur["dprof"], prev=prv["dprof"]),
             gen=dict(cur=cur["gen"], prev=prv["gen"], ly=lyr["gen"]), wx=dict(cur=cur["wx"], prev=prv["wx"]),
             ms=dict(cur=merit_summary(m), prev=merit_summary(pm)),
             bins=dict(cur=cur["bins"], prev=prv["bins"], ly=lyr["bins"]), fuel=dict(cur=cur["fuel"], prev=prv["fuel"], ly=lyr["fuel"]),
             peak=dict(cur=cur["peak"], ly=lyr["peak"], curD=cur["peakD"]), daily=daily, monthStart=a, scen=sc)
    def render(mini):
        T2 = dict(T, mini="1" if mini else "0",
                  doc_title="태양광 확대 시 SMP 전망" if mini else f"{lab['cur']} 전력시장 검토 결과")
        tp = Path(f"scripts/report_template_{m}.html")  # 달별 전용 양식이 있으면 그것을 씀(다른 달과 분리)
        html = (tp if tp.exists() else TPL).read_text(encoding="utf-8")
        for k, v in T2.items():
            html = html.replace("{{" + k + "}}", v)
        html = html.replace("__DATA__", json.dumps(D, ensure_ascii=False, separators=(",", ":")))
        left = re.findall(r"\{\{\w+\}\}", html)
        if left:
            raise SystemExit("채우지 못한 자리: " + ", ".join(sorted(set(left))))
        return html
    (DOCS / f"{m}.html").write_text(render(False), encoding="utf-8")
    MINI_HTML[m] = render(True)  # 태양광 시나리오 미니 리포트(가장 최근 달 것을 main에서 저장)
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
        tail = f"SMP/LNG 비율 {f1(rp)} → {f1(rc)}로 상승"
    elif rc < rp - 0.5:
        tail = f"SMP/LNG 비율 {f1(rp)} → {f1(rc)}로 하락"
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
    T["s3_msg"] = (f"평일 낮 총수요 {f1(gp['dem'] + gp['hid'])} → {f1(gc['dem'] + gc['hid'])}GW, 태양광이 {f1(sol_p)} → {f1(sol_c)}GW로 줄어 "
                   f"<span class=\"em\">계통수요 {sg(dD, 'GW')}</span> ({f1(gp['dem'])} → {f1(gc['dem'])}GW)")
    T["s3_bullets"] = (f"<li>낮 시간 태양광(시장+시장 외) <b>{f1(sol_p)} → {f1(sol_c)}GW</b>"
                       f"{', 그만큼 계통이 더 공급' if sol_c < sol_p else ', 그만큼 계통 부담 감소'}</li>"
                       f"<li>저녁(17~19시) 계통수요 {f1(prv['eve'])} → {f1(cur['eve'])}GW</li>")
    T["s3_note"] = ("※ 계통수요 = KPX 실시간 수급현황 발전원 합 · 시장 외 태양광 = 한전 PPA·자가용(BTM, KPX 추정) · "
                    "날씨: Open-Meteo, 기온은 서울, 일사량은 목포·전주·서산·안동 평균"
                    + (f" · 발전량 미수집 {cur['missing']}시간 제외" if cur["missing"] else ""))
    T["sub_31"] = f"평일 · {P_} → {M_}"
    # 3-2 발전가격 stack
    pm_ = shift(m, -1)
    msc, msp = merit_summary(m), merit_summary(pm_)
    common = [k for k in cur["bins"] if k in prv["bins"]]
    diffs = [cur["bins"][k][0] - prv["bins"][k][0] for k in common]
    lo, hi = (min(diffs), max(diffs)) if diffs else (0, 0)
    mv = (msc or {}).get("movers") or []
    if msc and msp and mv:
        fl = Counter(r[1] for r in mv).most_common(1)[0][0]
        fl = {"LNG 직도입": "직도입 LNG", "LNG 개별": "개별요금 LNG", "LNG 평균": "평균요금 LNG"}.get(fl, fl)
        s4 = (f"{fl} 등 <span class=\"rd\">{len(mv)}기({sum(r[4] for r in mv) / 1000:.1f}GW)</span>의 발전가격이 160원 이상으로 뛰어 "
              f"160원 미만 공급 {f1(msp['below160'])} → <span class=\"em\">{f1(msc['below160'])}GW</span>")
    elif msc and msp:
        s4 = f"160원 미만 공급 {f1(msp['below160'])} → {f1(msc['below160'])}GW"
    elif diffs:
        s4 = f"같은 수요 구간의 SMP 차이 {round(lo)}~{round(hi)}원"
    else:
        s4 = "발전가격 순위표 요약 자료 없음"
    if note.get("s4_msg_tail"):
        s4 += " — " + note["s4_msg_tail"]
    T["s4_msg"] = s4

    def share(f):
        t_ = sum(f.values()) or 1
        return f.get("LNG", 0) / t_ * 100
    b4 = []
    if msc and msp:
        b4.append(f"110원 미만 공급은 {f1(msp['below110'])} → {f1(msc['below110'])}GW로 비슷 — 달라진 것은 <b>110~160원 구간</b>"
                  f"({f1(msp['below160'] - msp['below110'])} → {f1(msc['below160'] - msc['below110'])}GW)")
    if diffs:
        b4.append(f"그 결과 같은 수요에서도 {M_} SMP가 {P_}보다 {round(lo)}~{round(hi)}원 {'높음' if lo > 0 else '낮음' if hi < 0 else '차이'}")
    b4.append(f"LNG가 가격을 정한 비중은 {f1(share(prv['fuel']))}% → {f1(share(cur['fuel']))}%로 비슷 — 더 비싼 LNG가 결정")
    # 두 칸 배치(행 순서): [공급 변화, 메모] / [같은 수요 SMP, LNG 비중] — 긴 두 줄끼리 한 행에
    nb = note.get("s4_bullets", [])
    if len(b4) == 3 and nb:
        b4 = [b4[0], nb[0], b4[1], b4[2]] + nb[1:]
    else:
        b4 += nb
    T["s4_bullets"] = "".join(f"<li>{x}</li>" for x in b4)
    T["s4_note"] = (f"※ 발전가격 stack: 사업기획팀 발전가격 순위표(육지·입찰량, 정비·고장 미반영)를 5원 구간으로 누적 · 점 = 160원 미만 누적 입찰량 · "
                    f"같은 수요 SMP 차이: 평일 시간, 5GW 수요 구간별 평균 · 연료별 결정: EPSIS 연료원별 SMP 결정 횟수({per})")
    T["prevM"], T["curM"], T["month"] = P_, M_, m
    # 3-3
    ga, gl = cur["gen"]["all"], lyr["gen"]["all"]
    dB = ga["nuc"] + ga["coal"] - gl["nuc"] - gl["coal"]; dL = ga["lng"] - gl["lng"]
    T["s5_msg"] = f"전년 {M_}보다 원전·석탄 <span class=\"em\">{sg(dB, 'GW')}</span>, LNG <span class=\"{'dn' if dL < 0 else 'rd'}\">{sg(dL, 'GW')}</span> (평일 평균 출력)"
    T["s5_bullets"] = (f"<li>{P_} → {M_} 낮 출력: LNG {f1(gp['lng'])} → {f1(gc['lng'])}GW({sg(gc['lng']-gp['lng'])}), "
                       f"석탄 {f1(gp['coal'])} → {f1(gc['coal'])}GW({sg(gc['coal']-gp['coal'])}), 태양광 {f1(sol_p)} → {f1(sol_c)}GW</li>"
                       f"<li>LNG 단가 전년 대비 {sg(N['lngYoY'], '%')} — LNG가 가격을 정하는 시간의 SMP 수준을 좌우</li>")
    pa = prv["gen"]["all"]
    sA = lambda g: g["sunM"] + g["hid"]
    T["s5m_msg"] = (f"{P_}보다 태양광 <span class=\"dn\">{sg(sA(ga) - sA(pa), 'GW')}</span>, 그만큼 석탄 <span class=\"rd\">{sg(ga['coal'] - pa['coal'], 'GW')}</span>"
                    f" · LNG <span class=\"rd\">{sg(ga['lng'] - pa['lng'], 'GW')}</span>가 더 공급 (평일 평균)")
    T["s5m_bullets"] = (f"<li>낮(10~16시): 태양광 {f1(sol_p)} → {f1(sol_c)}GW, 석탄 {f1(gp['coal'])} → {f1(gc['coal'])}GW, LNG {f1(gp['lng'])} → {f1(gc['lng'])}GW</li>"
                        "<li>늘어난 계통수요와 줄어든 태양광을 석탄·LNG가 채움</li>")
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
    L, G = sc["res"]["lin_t"], sc["res"]["cg_t"]
    RS = list(sc["res"].values())
    def r4(f):  # 4개 시나리오 범위
        v = [f(x) for x in RS]
        return min(v), max(v)
    base = L[y0]
    lo_e, hi_e = sc["lin_end"][str(tg)], sc["cg_end"][str(tg)]
    T["s8_msg"] = (f"올해 실적에서 <span class=\"em\">태양광만 {tg}년 수준({round(sc['lin_end'][str(tg)])}~{round(sc['cg_end'][str(tg)])}GW)</span>으로 늘렸을 때 "
                   "SMP와 출력제어가 어떻게 바뀌는지 계산")
    lowb = [b_ for b_ in sc["nlb"] if int(b_[0].split("~")[0]) < 50]
    T["s8_how"] = ("<li>늘어난 태양광만큼 순부하를 낮추고, <b>같은 달에 순부하가 비슷했던 올해 40개 시간의 평균 SMP</b>를 그 시간 SMP로 봄</li>"
                   + (f"<li>순부하 50GW 아래(파란 막대)에선 SMP가 <b>{round(min(x[1] for x in lowb))}~{round(max(x[1] for x in lowb))}원</b>에서 더 내려가지 않음 "
                      "— 태양광이 늘어도 SMP 하락 폭이 작은 이유</li>" if lowb else ""))
    T["s8b_msg"] = (f"최근 3년 태양광은 연 {' → '.join(f1(x) for x in sc['adds'])}GW 증가(평균 <span class=\"em\">+{f1(sc['add'])}GW</span>, 연 {f1(sc['cagr'])}%) "
                   f"— 이 속도면 {tg}년 말 <span class=\"em\">{round(lo_e)}~{round(hi_e)}GW</span>")
    T["s8_sub"] = f"GW · {sc['ys'][0]}~{sc['ys'][-1]} 실적, {sc['ys'][-1]+1}~{tg} 시나리오"
    T["s8_rows"] = (f"<tr><td>날씨</td><td class=\"tl\">올해(1.1~{per.split('~')[1]}) 시간별 일사·기온이 매년 반복</td></tr>"
                    "<tr><td>수요·연료비</td><td class=\"tl\">올해와 동일(가스요금·석탄가격·전력수요 변화 미반영)</td></tr>"
                    "<tr><td>발전설비</td><td class=\"tl\">원전·석탄·LNG 설비와 입찰 행태 올해와 동일</td></tr>")
    T["s8_case"] = (f"<tr><td>기본<br>(정량 증가)</td><td class=\"tl\">매년 <b>+{f1(sc['add'])}GW</b> — {sc['ys'][1]}~{sc['ys'][-1]}년 연간 증가량 평균"
                    f"({' · '.join(f1(x) for x in sc['adds'])}GW)이 그대로 이어짐</td></tr>"
                    f"<tr><td>상단<br>(비율 증가)</td><td class=\"tl\">매년 <b>+{f1(sc['cagr'])}%</b> — {sc['ys'][0]}→{sc['ys'][-1]}년 연평균 증가율이 이어짐(설비가 늘수록 증가량도 커짐)</td></tr>"
                    f"<tr><td>{tg}년 말</td><td class=\"tl\">기본 <b>{f1(sc['lin_end'][str(tg)])}GW</b> · 상단 <b>{f1(sc['cg_end'][str(tg)])}GW</b> (올해 초 {f1(sc['end'][str(sc['ys'][-1])])}GW)</td></tr>")
    T["nmin"], T["add"], T["cagr"] = f1(sc["NMIN"]), f1(sc["add"]), f1(sc["cagr"])
    T["s8_valid"] = f"같은 방법으로 올해 실적을 재현하면 월평균 SMP 오차 ±{f1(sc['err'])}원 이내"
    T["s8_note"] = "※ 설비: " + CAP.get("_출처", "")
    T["s8_gov"] = ((f"<b class=\"rd\">비교:</b> 정부의 2030년 목표는 <b>{f1(CAP['gov_target_2030'])}GW</b> — 상단 시나리오({f1(sc['cg_end'][str(tg)])}GW)보다 훨씬 커서, "
                    "목표대로 늘면 SMP 하락과 출력제어 모두 이 결과보다 커짐") if CAP.get("gov_target_2030") else
                   "<b class=\"rd\">한계:</b> 수요 증가·연료비 변화·ESS 확대는 반영하지 않음")
    a1, a2 = base["all"] - L[tg]["all"], base["all"] - G[tg]["all"]
    d1, d2 = base["day"] - L[tg]["day"], base["day"] - G[tg]["day"]
    T["s9_sub"] = f"올해 연료비·수요 기준 · 1.1~{per.split('~')[1]} 시간 평균"
    dT = [x[tg]["all"] - base["all"] for k_, x in sc["res"].items() if k_.endswith("_t")]
    dP = [x[tg]["all"] - base["all"] for k_, x in sc["res"].items() if k_.endswith("_p")]
    sgr = lambda v: ("+" if v > 0 else "▼") + str(abs(round(v)))
    rr = lambda a_: sgr(min(a_, key=abs)) + ("" if round(min(a_)) == round(max(a_)) else "~" + str(abs(round(max(a_, key=abs)))))
    T["s9_msg"] = (f"{tg}년 연평균 SMP(올해 {f1(base['all'])}원 대비): 수요 추세 시 <span class=\"em\">{rr(dT)}원</span>, "
                   f"전기본 수요 시 <span class=\"rd\">{rr(dP)}원</span>")
    T["s9_msg_old"] = (f"{tg}년 연평균 SMP는 <span class=\"em\">{round(min(a1, a2))}~{round(max(a1, a2))}원"
                   f"(▼{f1(min(a1, a2) / base['all'] * 100)}~{f1(max(a1, a2) / base['all'] * 100)}%) 하락</span> — "
                   f"낮 SMP {round(min(d1, d2))}~{round(max(d1, d2))}원 하락, 대신 <span class=\"rd\">출력제어 {f1(L[tg]['curTWh'])}~{f1(G[tg]['curTWh'])}TWh</span> 필요")
    T["s9_bullets"] = (f"<li>SMP 150원 이상: 올해 {base['hiH']}시간 → {tg}년 {min(L[tg]['hiH'], G[tg]['hiH'])}~{max(L[tg]['hiH'], G[tg]['hiH'])}시간</li>"
                       f"<li>출력제어 시간 0원 가정 시 {tg}년 연평균 {f1(min(L[tg]['all0'], G[tg]['all0']))}~{f1(max(L[tg]['all0'], G[tg]['all0']))}원</li>")
    drops = {mm: (L[y0]["mon"][mm] - L[tg]["mon"][mm], G[y0]["mon"][mm] - G[tg]["mon"][mm]) for mm in L[y0]["mon"]}
    top = sorted(drops, key=lambda k: -drops[k][1])[:2]
    top_s = "·".join(f"{mo_n(x)}월" for x in sorted(top))
    t_lo = round(min(min(drops[x]) for x in top)); t_hi = round(max(max(drops[x]) for x in top))
    T["s9_bullets2"] = (f"<li><b>하락 폭이 작은 이유</b>: 낮 가격은 석탄·열병합 수준까지만 내려옴 — 올해 순부하 최저 시간({f1(sc['low_nl'][0])}~{f1(sc['low_nl'][1])}GW)에도 SMP {sc['low_smp'][0]}~{sc['low_smp'][1]}원</li>"
                        f"<li><b>영향이 큰 달</b>: {top_s}({tg}년 ▼{t_lo}~{t_hi}원) · {M_}은 ▼{round(min(drops[m]))}~{round(max(drops[m]))}원</li>"
                        f"<li><b>남는 위험</b>: 남는 태양광을 줄여야 하는 시간이 {tg}년 {L[tg]['curH']}~{G[tg]['curH']}시간 — SMP 하락보다 출력제어 확대가 더 큰 변화</li>")
    lo_s = [x[1] for x in sc["nlb"] if int(x[0].split("~")[0]) < 50]
    lo_s = [x[1] for x in sc["nlb"] if int(x[0].split("~")[0]) < 50] or [0]
    fl_lo, fl_hi = round(min(lo_s)), round(max(lo_s))
    cu = r4(lambda x: x[tg]["curH"])
    imp = [("1", "", f"수요 정체 <b>{rr(dT)}원</b><br>전기본 수요 <b>{rr(dP)}원</b>"),
           ("2", "", f"SMP 바닥 <b>{fl_lo}~{fl_hi}원</b>"),
           ("3", " r", f"출력제어 <b>연 {cu[0]}~{cu[1]}시간</b>")]
    T["s9_imp"] = "".join(f'<div class="imp"><span class="num{c}">{n}</span><div class="tx">{x}</div></div>' for n, c, x in imp)
    T["s9_why"] = f"<b>참고:</b> 올해 순부하 50GW 이하 구간 SMP <b>{fl_lo}~{fl_hi}원</b>(석탄·열병합 가격대) · 잉여 태양광은 출력제어"
    T["s8_msg"] = f"순부하가 50GW 아래로 내려가도 SMP는 <span class=\"em\">{fl_lo}~{fl_hi}원</span>에서 멈춤"
    T["s8b_msg"] = (f"최근 3년 태양광 증가 속도(연 +{f1(sc['add'])}GW, {f1(sc['cagr'])}%)가 {tg}년까지 이어지고, 나머지는 올해와 같다고 가정")
    caps = " → ".join(f"{f1(sc['end'][str(y)])}" for y in sc["ys"])
    rows = [("시나리오", f"<b>기본</b> 매년 +{f1(sc['add'])}GW → {tg}년 말 <b>{f1(sc['lin_end'][str(tg)])}GW</b> &nbsp;·&nbsp; "
                       f"<b>상단</b> 매년 +{f1(sc['cagr'])}% → <b>{f1(sc['cg_end'][str(tg)])}GW</b>"),
            ("근거", f"사업용 태양광 연말 설비 {sc['ys'][0]}~{sc['ys'][-1]}년 {caps}GW"),
            ("SMP 계산", "올해 시간별 실적에서 <b>순부하가 비슷한 시간의 SMP</b>를 그대로 적용"),
            ("출력제어", f"순부하가 올해 최저({f1(sc['NMIN'])}GW)보다 낮아지는 만큼 — 모두 태양광 몫으로 계산(상한값)"),
            ("고정 조건", f"날씨·수요·연료비·발전설비는 올해(1.1~{per.split('~')[1]})와 동일"),
            ("검증", f"같은 방법으로 올해 실적 재현 시 월평균 SMP 오차 ±{f1(sc['err'])}원")]
    if CAP.get("gov_target_2030"):
        rows.append(("참고", f"정부 2030년 목표 <b>{f1(CAP['gov_target_2030'])}GW</b> — 상단보다 커서 목표대로면 영향이 더 큼"))
    kl, kc = sc["res"]["lin_t"][tg]["k"], sc["res"]["cg_t"][tg]["k"]
    endv = [sc["end"][str(y)] for y in sc["ys"]]
    T["sc_end"] = per.split("~")[1]
    T["sc1_msg"] = f"전력수요 2개 × 태양광 설비 2개 = <span class=\"em\">시나리오 4개</span>로 {tg}년까지 계산"
    DGs, mi = sc["dg"], sc["mins"]
    mh = sc["minh"]
    mdt = f"'{mh['d'][2:4]}.{int(mh['d'][5:7])}.{int(mh['d'][8:])} {mh['h']}시" + (" (설 연휴)" if mh["d"][5:] in ("02-16", "02-17", "02-18") and mh["d"].startswith("2026") else "")
    bs = lambda s: f'<span class="bs">{s}</span>'
    tgA = lambda s: f'<span class="tg s1">{s}</span>'
    tgB = lambda s: f'<span class="tg s2">{s}</span>'
    kv = lambda s: f'<span class="kv">{s}</span>'
    mrows = [("① 전력수요", "총수요 × 연 증가율",
              tgA("추세") + kv(f"+{f1(DGs['t']['rate'])}%/년") + bs("한전 판매량 '22~'25 연평균"),
              tgB("전기본") + kv(f"+{f1(DGs['p']['rate'])}%/년") + bs("11차 전기본 최대수요 '23~'38 연평균")),
             ("② 설비", "최근 3년 증가 속도",
              tgA("Base") + kv(f"{round(sc['lin_end'][str(tg)])}GW") + bs(f"매년 +{f1(sc['add'])}GW ('23~'25 평균)"),
              tgB("Upside") + kv(f"{round(sc['cg_end'][str(tg)])}GW") + bs(f"매년 +{f1(sc['cagr'])}% ('22~'25 연평균)")),
             ("③ 발전량", "올해 시간별 출력 × 설비 배율", f"{tg}년 올해의 " + kv(f"{f1(kl)}배"), f"{tg}년 올해의 " + kv(f"{f1(kc)}배")),
             ("④ 최소발전량", "올해 전기가 가장 남은 시간 실적",
              f"원전 <b>{f1(mh['nuc'])}</b> · 석탄 <b>{f1(mh['coal'])}</b> · LNG <b>{f1(mh['lng'])}GW</b> → 순부하 " + kv(f"{f1(mh['nl'])}GW")
              + ' <a class="ax" href="#appxMin">근거 보기 ▶</a>'
              + bs(f"올해 전기가 가장 남았던 {mdt.split(' (')[0]} 실적으로 설정")
              + '<span class="warn">→ 최소발전량을 감안하면 태양광 증가분 중 일부는 출력제어됨</span>', None),
             ("⑤ SMP", "비슷한 순부하 시간의 SMP", f"올해 실적 재현 오차 " + kv(f"±{f1(sc['err'])}원"), None)]
    T["sc1_tbl"] = "".join(f'<tr><td class="rh">{a_}</td><td>{b_}</td>' + (f'<td>{c_}</td><td>{d_}</td>' if d_ else f'<td colspan="2">{c_}</td>') + "</tr>"
                           for a_, b_, c_, d_ in mrows)
    T["sc1_note"] = "※ 한전 판매량은 자가용 태양광 소비 제외 · 설비: 에너지공단·산업부·기후부 · 날씨는 올해와 동일"
    def sc0(h, lines, cls=""):
        return (f'<div class="card f1"><div class="ch"{' style="background:#6B7585"' if cls else ''}>{h}</div><div class="cb" style="gap:6px;padding-top:12px"><ul class="d">'
                + "".join(f"<li>{x}</li>" for x in lines) + "</ul></div></div>")
    gov = CAP.get("gov_target_2030")
    T["sc0_msg"] = "수요는 <b>최근 실적</b>과 <b>정부 계획</b>, 설비는 최근 3년 증가를 <b>양</b>으로 볼지 <b>비율</b>로 볼지로 나눔"
    T["sc0_d"] = (sc0(f"수요 ① 추세 +{f1(DGs['t']['rate'])}%/년",
                      ["한전 판매량 '22년 547.9 → '25년 549.4TWh의 3년 연평균", "산업용 감소·경기 둔화로 최근 수요 정체",
                       "자가용 태양광 소비는 빠져 실제보다 낮을 수 있음"])
                  + sc0(f"수요 ② 전기본 +{f1(DGs['p']['rate'])}%/년",
                        ["11차 전기본 최대수요 '23년 98.3 → '38년 129.3GW의 연평균", "반도체·데이터센터·전기화 신규 수요 반영",
                         "최대전력 증가율을 모든 시간에 똑같이 적용"], " r"))
    T["sc0_c"] = (sc0(f"설비 ① Base +{f1(sc['add'])}GW/년",
                      [f"'23~'25년 연간 증가량 {' · '.join(f1(x) for x in sc['adds'])}GW의 평균", f"매년 같은 양씩 증가 → {tg}년 {f1(sc['lin_end'][str(tg)])}GW"])
                  + sc0(f"설비 ② Upside +{f1(sc['cagr'])}%/년",
                        [f"'22~'25년 연평균 증가율({f1(endv[0])} → {f1(endv[-1])}GW)", f"설비가 늘수록 증가량도 커짐 → {tg}년 {f1(sc['cg_end'][str(tg)])}GW"]
                        + ([f"정부 {tg}년 목표 {f1(gov)}GW보다는 낮음"] if gov else []), " r"))
    T["sc0_note"] = "※ 수요: 한전 2024·2025 결산, 11차 전력수급기본계획(2025.2 확정) · 설비: 에너지공단 보급통계, 산업부(2025.1), 기후부(2026.8)"
    li = sc["lowinfo"]; mr = sc["minr"]
    tops = ", ".join(f"{int(d_[5:7])}/{int(d_[8:])}" for d_ in li["top"])
    T["sc2_msg"] = f"올해 전기가 가장 남았던 <span class=\"em\">{mdt.replace(' (', '(')}</span> 실적을 최소발전량으로 설정"
    T["sc2_h"] = f"{int(mh['d'][5:7])}/{int(mh['d'][8:])} {mh['h']}시"
    T["sc2_rows"] = "".join(f'<tr><td class="rh">{n_}</td><td class="r"><b>{f1(mh[k_])}</b></td><td class="r">{f1(mr[k_][0])}~{f1(mr[k_][1])}</td><td>{w_}</td></tr>'
                            for n_, k_, w_ in (("원전", "nuc", "출력 조절이 어려워 상시 운전"), ("석탄", "coal", "필수운전 발전기는 못 세움"),
                                               ("LNG", "lng", "열 공급·저녁 피크 대비")))
    T["sc2_box"] = (f"원전 {f1(mh['nuc'])} + 석탄 {f1(mh['coal'])} + LNG {f1(mh['lng'])} − 양수 충전 {f1(abs(mh['pump']))} + 기타 {f1(mh['etc'])}"
                    f" = 순부하 <b>{f1(mh['nl'])}GW</b> → 이 밑은 태양광 출력제어")
    T["sc2_note"] = (f"※ KPX 실시간 수급현황 · 범위 = 순부하 최저 {li['n']}시간(주말·공휴일 {li['hol']}시간, {li['h0']}~{li['h1']}시 · {tops} 등) · "
                     "석탄: 경부하기에도 비수도권 필수운전 약 13대 운전(산업부 2025 가을 대책)")
    T["s8_tbl"] = "".join(f'<tr><td class="rh">{k}</td><td>{v}</td></tr>' for k, v in rows)
    T["s9_note"] = (f"※ 연평균 · {y0}년 = 1.1~{per.split('~')[1]} 실적 · SMP 2022~2025년 한전 결산 · 총수요 = 계통수요 + 시장 외 태양광 · 순부하 = 계통수요 − 시장 태양광·풍력(출력제어 후)")
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
    if MERIT:  # 순위표가 있으면 순위표 기간 전체를 추정해 결과 파일 갱신
        for m in sorted(MERIT):
            for d in rng(f"{m}-01", f"{m}-{mlen(m):02d}"):
                if d in SMP and d < tdy:
                    est_day(d)
        save_est()
        for mm in sorted(MERIT):
            merit_summary(mm)
        save_ms()
    lst = sorted(reps.values(), key=lambda r: r["m"])
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(lst, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = lst[-1] if lst else None  # 가장 최근 달(이번 달은 10일 이후부터 잠정본 생성)
    if latest:
        shutil.copyfile(DOCS / f"{latest['m']}.html", DOCS / "report.html")
    if latest and latest["m"] in MINI_HTML:
        (DOCS / "solar-scenario.html").write_text(MINI_HTML[latest["m"]], encoding="utf-8")
        print("report.html ←", latest["m"])


if __name__ == "__main__":
    main()
