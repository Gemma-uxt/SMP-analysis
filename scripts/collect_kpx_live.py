"""KPX 발전원별 실시간 수급현황 수집 (오늘 + 과거 역방향 채우기)"""
import csv, json, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

KST = timezone(timedelta(hours=9))
BASE = "https://www.kpx.or.kr"
PAGE = BASE + "/powerSource.es?mid=a10606030000&device=chart"
MENU = BASE + "/menu.es?mid=a10606030000"
OUT = Path("data/raw/kpx_live")
START = datetime(2024, 11, 23).date()
MAX_BACKFILL = int(os.getenv("KPX_BACKFILL_PER_RUN", "60"))
FULL_DAY = 280  # 5분 단위 288개 중 280개 이상이면 완료로 간주
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
LOG = []

def log(msg):
    print(msg)
    LOG.append(msg)

def parse(html):
    m = re.search(r"var\s+ictArr\s*=\s*(\[.*?\])\s*;", html, re.S)
    return json.loads(m.group(1)) if m else None

def path_of(day):  # day: 'YYYYMMDD'
    return OUT / day[:4] / f"{day}.csv"

def row_count(d):
    p = path_of(d.strftime("%Y%m%d"))
    if not p.exists():
        return 0
    with p.open(encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))

def save(rows):
    by_day = {}
    for r in rows:
        once = str(r.get("once", ""))[:12]
        if len(once) == 12:
            by_day.setdefault(once[:8], {})[once] = r
    for day, recs in by_day.items():
        p = path_of(day)
        p.parent.mkdir(parents=True, exist_ok=True)
        merged = {}
        if p.exists():
            with p.open(encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    merged[str(r.get("once", ""))[:12]] = r
        merged.update(recs)
        keys = set()
        for r in merged.values():
            keys.update(r.keys())
        first = [k for k in ("once", "regDate") if k in keys]
        fields = first + sorted(keys - set(first))
        with p.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for k in sorted(merged):
                w.writerow(merged[k])

def get_csrf(s):
    uuid = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    pats = [r'name="_csrf"\s+content="([^"]+)"', r'content="([^"]+)"\s+name="_csrf"',
            r'name="_csrf"\s+value="([^"]+)"', r'value="([^"]+)"\s+name="_csrf"',
            r"_csrf[^0-9a-f]{1,60}" + uuid]
    for url in (MENU, PAGE):
        html = s.get(url, headers=UA, timeout=60).text
        for p in pats:
            m = re.search(p, html)
            if m:
                return m.group(1)
    return None

def fetch_day(s, token, d):
    ds = d.isoformat()
    data = {"mid": "a10606030000", "device": "chart",
            "view_sdate": ds, "view_edate": ds,
            "view_sdate31": ds, "view_edate31": ds, "_csrf": token}
    headers = dict(UA)
    headers.update({"Referer": MENU, "Origin": BASE,
                    "X-XSRF-TOKEN": token, "X-CSRF-TOKEN": token})
    r = s.post(PAGE, data=data, headers=headers, timeout=60)
    r.raise_for_status()
    rows = parse(r.text) or []
    want = d.strftime("%Y%m%d")
    hit = [x for x in rows if str(x.get("once", "")).startswith(want)]
    if not hit and rows:
        log(f"  {ds}: 날짜가 반영되지 않음 (받은 첫 시각 {rows[0].get('regDate')})")
    return hit

def main():
    s = requests.Session()
    today = datetime.now(KST).date()

    r = s.get(PAGE, headers=UA, timeout=60)
    r.raise_for_status()
    rows = parse(r.text)
    if not rows:
        raise SystemExit("데이터를 찾지 못함 - 페이지 구조가 바뀌었을 수 있음")
    save(rows)
    log(f"[오늘] {len(rows)}건 저장")

    todo, d = [], today - timedelta(days=1)
    while d >= START and len(todo) < MAX_BACKFILL:
        if row_count(d) < FULL_DAY:
            todo.append(d)
        d -= timedelta(days=1)
    log(f"[과거] 이번 실행에서 채울 날짜 {len(todo)}일")

    if todo:
        token = get_csrf(s)
        if not token:
            log("[과거] 보안 토큰(_csrf)을 찾지 못해 건너뜀")
        else:
            ok = fail = 0
            for d in todo:
                try:
                    got = fetch_day(s, token, d)
                except Exception as e:
                    got = None
                    log(f"  {d}: 오류 {e}")
                if got:
                    save(got)
                    ok += 1
                else:
                    fail += 1
                    if ok == 0 and fail >= 3:
                        log("[과거] 처음 3번 연속 실패 - 조회 방식 확인 필요, 중단")
                        break
                time.sleep(1.5)
            log(f"[과거] 성공 {ok}일, 실패 {fail}일")

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    (OUT / "_log.txt").write_text(stamp + "\n" + "\n".join(LOG) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
