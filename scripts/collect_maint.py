"""KPX 발전기별 예방정비계획(주간) 수집
게시판: https://www.kpx.or.kr/board.es?mid=a10109030500&bid=0018
- 주차별 글의 첨부 PDF 표를 읽어 data/raw/maint/posts/<글번호>.csv 로 저장
- 전체를 합쳐 docs/data/maint.json 생성 (대시보드용, 육지만)
"""
import csv, io, json, re
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
import requests
import pdfplumber

KST = timezone(timedelta(hours=9))
BASE = "https://www.kpx.or.kr"
LIST = BASE + "/board.es?mid=a10109030500&bid=0018&nPage={p}"
FILE = BASE + "/boardDownload.es?bid=0018&list_no={n}&seq=1"
POSTS = Path("data/raw/maint/posts")
LOG = Path("data/raw/maint/_log.txt")
WEB = Path("docs/data/maint.json")
FROM_YM = (2024, 12)      # 이 달 이후 글만
MAX_PER_RUN = 40          # 한 번에 받을 PDF 수
HEAD = {"User-Agent": "Mozilla/5.0"}
FUEL = {"원자력": "nuc", "유연탄": "coal", "무연탄": "coal", "IGCC": "coal",
        "LNG": "lng", "양수": "pump", "수력": "hydro",
        "바이오중유": "oil", "중유": "oil", "경유": "oil", "유류": "oil"}
DATE = re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")
TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
FIELDS = ["list_no", "fuel_raw", "fuel", "name", "mw", "start", "end"]
msgs = []

def log(s):
    print(s); msgs.append(s)

def list_posts(s):
    posts = {}
    for p in range(1, 80):
        html = s.get(LIST.format(p=p), headers=HEAD, timeout=60).text
        found = {}
        for m in re.finditer(r"list_no=(\d+)", html):
            n = int(m.group(1))
            t = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d)\s*주차", html[m.end():m.end() + 800])
            if t and n not in found:
                found[n] = (int(t.group(1)), int(t.group(2)))
        new = {n: v for n, v in found.items() if n not in posts}
        if not new:
            break
        posts.update(new)
        if min(new.values()) < FROM_YM:
            break
    return {n: v for n, v in posts.items() if v >= FROM_YM}

def num(x):
    try:
        return float(x.replace(",", ""))
    except ValueError:
        return None

def to_dt(cells, i):
    m = DATE.search(cells[i])
    y, mo, d = map(int, m.groups())
    rest = cells[i][m.end():].strip()
    tm = TIME.match(rest) or (TIME.match(cells[i + 1]) if i + 1 < len(cells) else None)
    hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (0, 0)
    return datetime(y, mo, d) + timedelta(hours=hh, minutes=mm)

def infer(name):
    return "lng" if re.search(r"GT|ST|복합|열병합|CC", name) else "etc"

def parse_cells(cells):
    cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in cells]
    cells = [c for c in cells if c]
    di = [i for i, c in enumerate(cells) if DATE.match(c)]
    if len(di) < 2 or di[0] < 2:
        return None
    i1, i2 = di[0], di[1]
    mw = num(cells[i1 - 1])
    if mw is None:
        return None
    fj = next((j for j in range(i1 - 2, -1, -1) if cells[j] in FUEL), None)
    if fj is None:
        name, fuel_raw = cells[i1 - 2], ""
    else:
        name, fuel_raw = " ".join(cells[fj + 1:i1 - 1]), cells[fj]
    if not name:
        return None
    try:
        s0, e0 = to_dt(cells, i1), to_dt(cells, i2)
    except ValueError:
        return None
    return {"fuel_raw": fuel_raw, "fuel": FUEL.get(fuel_raw) or infer(name), "name": name,
            "mw": mw, "start": s0.strftime("%Y-%m-%dT%H:%M"), "end": e0.strftime("%Y-%m-%dT%H:%M")}

def parse_pdf(data):
    rows, sample = [], ""
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            a = [x for tb in (page.extract_tables() or []) for r in tb if (x := parse_cells(r))]
            text = page.extract_text() or ""
            sample = sample or text[:800]
            b = [x for line in text.splitlines() if (x := parse_cells(line.split()))]
            rows += a if len(a) >= len(b) else b
    return rows, sample

def build():
    units = {}
    for p in sorted(POSTS.glob("*.csv"), key=lambda p: int(p.stem)):
        with open(p, encoding="utf-8") as f:
            for x in csv.DictReader(f):
                if "제주" in x["name"]:
                    continue
                units[(x["name"], x["start"])] = [x["name"], x["fuel"], float(x["mw"]), x["start"], x["end"]]
    lst = sorted((u for u in units.values() if u[4] >= "2024-12-01"), key=lambda u: u[3])
    daily, d = {}, date(2025, 1, 1)
    last = datetime.now(KST).date() + timedelta(days=14)
    while d <= last:
        t, o = d.isoformat() + "T14:00", {}
        for n, f, mw, s0, e0 in lst:
            if s0 <= t < e0:
                o[f] = round(o.get(f, 0) + mw, 1)
        daily[d.isoformat()] = o
        d += timedelta(days=1)
    WEB.parent.mkdir(parents=True, exist_ok=True)
    WEB.write_text(json.dumps({"updated": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                               "source": "KPX 발전기별 예방정비계획(주간, 계획 기준)",
                               "units": lst, "daily": daily},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"정비 발전기 {len(lst)}건 → docs/data/maint.json")

def main():
    POSTS.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.get(BASE + "/menu.es?mid=a10109030500", headers=HEAD, timeout=60)
    posts = list_posts(s)
    done = {int(p.stem) for p in POSTS.glob("*.csv")}
    todo = sorted((n for n in posts if n not in done), reverse=True)[:MAX_PER_RUN]
    log(f"게시글 {len(posts)}개 확인, 저장됨 {len(done)}개, 이번에 {len(todo)}개 수집")
    for n in todo:
        try:
            for seq in (1, 2, 3):
                r = s.get(FILE.format(n=n).replace("seq=1", f"seq={seq}"), headers=HEAD, timeout=120)
                if r.content.startswith(b"%PDF"):
                    break
            else:
                raise ValueError("PDF가 아님")
            rows, sample = parse_pdf(r.content)
            if not rows:
                raise ValueError("표를 못 읽음 / 첫 글자: " + sample[:300].replace("\n", " | "))
            with open(POSTS / f"{n}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, FIELDS); w.writeheader()
                for x in rows:
                    w.writerow({"list_no": n, **x})
            log(f"  {n} ({posts[n][0]}-{posts[n][1]:02d}) {len(rows)}행")
        except Exception as e:
            log(f"  {n} 실패: {e}")
    build()

if __name__ == "__main__":
    try:
        main()
    finally:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
        head = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
        LOG.write_text("\n".join((old + [head] + msgs)[-300:]) + "\n", encoding="utf-8")
