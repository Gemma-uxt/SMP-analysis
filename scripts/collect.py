"""매일 실행되는 데이터 수집기 (키는 출력하지 않음)"""
import datetime as dt
import json, os, pathlib, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
BASE = "https://apis.data.go.kr/B552115"
RAW = pathlib.Path("data/raw")
START = dt.date(2025, 1, 1)
KST = dt.timezone(dt.timedelta(hours=9))
LOG = []

# 이름: (주소, 날짜 변수명, 1회 실행당 최대 호출 수)
DAILY = {
    "smp": ("SmpWithForecastDemand/getSmpWithForecastDemand", "date", 700),
    "gen": ("PwrAmountByGen/getPwrAmountByGen", "baseDate", 90),
}
FUEL_PATH = "SmpDecByFuel2/getSmpDecByFuel2"


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


def fetch_all(path, params, rows=1000, max_pages=60):
    out, page, calls = [], 1, 0
    while True:
        items, total = call(path, {**params, "pageNo": page, "numOfRows": rows})
        calls += 1
        out.extend(items)
        if not items or len(out) >= total or page >= max_pages:
            return out, calls
        page += 1
        time.sleep(0.3)


def save(f, rows):
    f.parent.mkdir(parents=True, exist_ok=True)
    lines = ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    f.write_text("[\n" + lines + "\n]\n", encoding="utf-8")


def collect_daily(name, path, date_key, budget):
    yesterday = dt.datetime.now(KST).date() - dt.timedelta(days=1)
    missing, day = [], yesterday
    while day >= START:
        f = RAW / name / day.strftime("%Y") / f"{day:%Y%m%d}.json"
        if not f.exists():
            missing.append((day, f))
        day -= dt.timedelta(days=1)
    log(f"[{name}] 빠진 날짜 {len(missing)}일, 이번 한도 {budget}회")
    used = saved = 0
    warned = False
    for day, f in missing:
        if used >= budget:
            break
        ymd = f"{day:%Y%m%d}"
        try:
            items, calls = fetch_all(path, {date_key: ymd})
        except ApiError as e:
            log(f"[{name}] {ymd} 실패, 이번 실행 중단: {e}")
            break
        used += calls
        if not items and (yesterday - day).days < 7:
            log(f"[{name}] {ymd} 아직 데이터 없음, 다음에 재시도")
            continue
        if items and not warned:
            blob = json.dumps(items[:5], ensure_ascii=False)
            if ymd not in blob and f"{day:%Y-%m-%d}" not in blob:
                log(f"[{name}] 경고: {ymd} 요청 결과에 해당 날짜가 안 보임. 샘플: {blob[:300]}")
                warned = True
        save(f, items)
        saved += 1
        time.sleep(0.3)
    log(f"[{name}] 저장 {saved}일, 호출 {used}회, 남은 날짜 {len(missing) - saved}일")


def collect_fuel():
    f = RAW / "fuel" / "smp_dec_by_fuel.json"
    old = json.loads(f.read_text("utf-8")) if f.exists() else []
    try:
        items, calls = fetch_all(FUEL_PATH, {}, max_pages=1 if old else 60)
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
    save(f, rows)
    log(f"[fuel] 호출 {calls}회, 총 {len(rows)}줄 저장")


def main():
    if not KEY:
        raise SystemExit("DATA_GO_KR_KEY가 없습니다")
    collect_fuel()
    for name, (path, date_key, budget) in DAILY.items():
        collect_daily(name, path, date_key, budget)
    stamp = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    logf = RAW / "_log" / "last_run.txt"
    logf.parent.mkdir(parents=True, exist_ok=True)
    logf.write_text(stamp + "\n" + "\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
