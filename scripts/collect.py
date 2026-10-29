"""매일 실행되는 데이터 수집기 v2 (키는 출력하지 않음)"""
import csv, datetime as dt, io, json, os, pathlib, shutil, time
import urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
BASE = "https://apis.data.go.kr/B552115"
RAW = pathlib.Path("data/raw")
START = dt.date(2025, 1, 1)
START_S = START.strftime("%Y%m%d")
KST = dt.timezone(dt.timedelta(hours=9))
LOG = []

SMP_PATH = "SmpWithForecastDemand/getSmpWithForecastDemand"
FUEL_PATH = "SmpDecByFuel2/getSmpDecByFuel2"
GEN_PATH = "PwrAmountByGen/getPwrAmountByGen"
SMP_BUDGET = 95
GEN_BUDGET = 95

GEN_COLS = [("hydro", "fuelPwr1"), ("oil", "fuelPwr2"), ("coal", "fuelPwr3"),
            ("nuclear", "fuelPwr4"), ("pumped", "fuelPwr5"), ("gas", "fuelPwr6"),
            ("domestic_coal", "fuelPwr7"), ("renewable", "fuelPwr8"),
            ("solar", "fuelPwr9"), ("total", "fuelPwrTot")]


class ApiError(Exception):
    pass


def log(msg):
    for k in (KEY, urllib.parse.quote(KEY, safe=""), urllib.parse.quote_plus(KEY)):
        if k:
            msg = msg.replace(k, "***")
    print(msg)
    LOG.append(msg)


def call(path, params):
    q = {"serviceKey": KEY, "dataType": "JSON", **params}
    url = f"{BASE}/{path}?{urllib.parse.urlencode(q)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    text = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8", "replace")
            break
        except urllib.error.HTTPError as e:
            raise ApiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        except Exception as e:
            if attempt == 2:
                raise ApiError(f"{type(e).__name__}: {e}")
            time.sleep(5)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ApiError(f"JSON 아님: {text[:200]}")
    resp = data.get("response")
    if not resp:
        raise ApiError(f"오류 응답: {text[:200]}")
    header = resp.get("header", {})
    if header.get("resultCode") != "00":
        raise ApiError(f"오류 코드: {header}")
    body = resp.get("body") or {}
    items = body.get("items") or {}
    item = items.get("item", []) if isinstance(items, dict) else []
    if isinstance(item, dict):
        item = [item]
    return item, int(body.get("totalCount") or 0)


def save_json(f, rows):
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    f.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


# ---------- 연료원별 SMP 결정 ----------
def collect_fuel():
    f = RAW / "fuel" / "smp_dec_by_fuel.json"
    old = json.loads(f.read_text("utf-8")) if f.exists() else []
    items, calls, page = [], 0, 1
    try:
        while True:
            got, total = call(FUEL_PATH, {"pageNo": page, "numOfRows": 1000})
            calls += 1
            items.extend(got)
            if not got or len(items) >= total or (old and page >= 1) or page >= 60:
                break
            page += 1
    except ApiError as e:
        log(f"[fuel] 실패: {e}")
        return
    def key(r):
        return (r.get("tradeDay") or "", r.get("areaNm") or "", r.get("fuelType") or "")
    merged = {key(r): r for r in old}
    for r in items:
        r = {k: v for k, v in r.items() if k != "rn"}
        merged[key(r)] = r
    rows = sorted(merged.values(), key=key)
    save_json(f, rows)
    log(f"[fuel] 호출 {calls}회, 총 {len(rows)}줄")


# ---------- 시간별 SMP + 수요예측 ----------
def collect_smp():
    yesterday = dt.datetime.now(KST).date() - dt.timedelta(days=1)
    missing, day = [], yesterday
    while day >= START:
        f = RAW / "smp" / day.strftime("%Y") / f"{day:%Y%m%d}.json"
        if not f.exists():
            missing.append((day, f))
        day -= dt.timedelta(days=1)
    log(f"[smp] 빠진 날짜 {len(missing)}일")
    used = saved = 0
    for day, f in missing:
        if used >= SMP_BUDGET:
            break
        ymd = f"{day:%Y%m%d}"
        try:
            items, _ = call(SMP_PATH, {"date": ymd, "pageNo": 1, "numOfRows": 100})
        except ApiError as e:
            log(f"[smp] {ymd} 실패, 중단: {e}")
            break
        used += 1
        if not items:
            log(f"[smp] {ymd} 데이터 없음, 다음에 재시도")
            continue
        save_json(f, items)
        saved += 1
        time.sleep(0.3)
    log(f"[smp] 저장 {saved}일, 호출 {used}회, 남은 날짜 {len(missing) - saved}일")


# ---------- 5분 단위 발전원별 발전량 ----------
def collect_gen():
    legacy = RAW / "gen"
    if legacy.exists():
        shutil.rmtree(legacy)
        log("[gen] 이전 형식 파일 삭제")
    folder = RAW / "gen5m"
    state_f = RAW / "_state" / "gen.json"
    state = json.loads(state_f.read_text("utf-8")) if state_f.exists() else {}
    existing = sorted(p.stem for p in folder.rglob("*.csv"))
    newest = existing[-1] if existing else None

    buf, used, total, page = {}, 0, 0, 0
    finished = state.get("finished", False)

    def get(p):
        nonlocal used, total
        items, total = call(GEN_PATH, {"pageNo": p, "numOfRows": 1000})
        used += 1
        stamps = []
        for r in items:
            ts = str(r.get("baseDatetime") or "")
            if len(ts) >= 12:
                buf[ts] = r
                stamps.append(ts)
        lo, hi = (min(stamps), max(stamps)) if stamps else ("", "")
        log(f"[gen] page {p}: {lo} ~ {hi}")
        time.sleep(0.3)
        return lo

    try:
        # 1) 최신 데이터 따라잡기
        while used < GEN_BUDGET:
            page += 1
            lo = get(page)
            if not lo or newest is None or lo[:8] < newest:
                break
        # 2) 과거 채우기
        if not finished:
            if "rows_done" in state and total:
                done = state["rows_done"] + (total - state["total"])
                page = max(page, done // 1000)
            while used < GEN_BUDGET:
                page += 1
                lo = get(page)
                if not lo or lo[:8] < START_S:
                    finished = True
                    log("[gen] 과거 채우기 완료")
                    break
    except ApiError as e:
        log(f"[gen] 중단: {e}")

    if total:
        state_f.parent.mkdir(parents=True, exist_ok=True)
        state_f.write_text(json.dumps({"rows_done": page * 1000, "total": total,
                                       "finished": finished}), encoding="utf-8")

    by_day = {}
    for ts, r in buf.items():
        if ts[:8] >= START_S:
            by_day.setdefault(ts[:8], {})[ts[:12]] = {n: r.get(k) for n, k in GEN_COLS}
    for day, rows in by_day.items():
        f = folder / day[:4] / f"{day}.csv"
        if f.exists():
            for old in csv.DictReader(io.StringIO(f.read_text("utf-8"))):
                rows.setdefault(old["datetime"], {n: old[n] for n, _ in GEN_COLS})
        f.parent.mkdir(parents=True, exist_ok=True)
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(["datetime"] + [n for n, _ in GEN_COLS])
        for ts in sorted(rows):
            w.writerow([ts] + [rows[ts][n] for n, _ in GEN_COLS])
        f.write_text(out.getvalue(), encoding="utf-8")
    log(f"[gen] 호출 {used}회, {len(by_day)}일 갱신")


def main():
    if not KEY:
        raise SystemExit("DATA_GO_KR_KEY가 없습니다")
    collect_fuel()
    collect_smp()
    collect_gen()
    stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    logf = RAW / "_log" / "last_run.txt"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text(stamp + "\n" + "\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
