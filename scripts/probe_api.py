# API 연결 테스트 v3 (키는 출력하지 않음)
import os, pathlib, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
OUT = pathlib.Path("probe_results")
OUT.mkdir(exist_ok=True)
BASE = "https://apis.data.go.kr/B552115"

TESTS = [
    ("gen_20250115", "PvAmountByPwrGen/getPwrAmountByGen", {"baseDate": "20250115", "numOfRows": "30"}),
    ("gen_20251015", "PvAmountByPwrGen/getPwrAmountByGen", {"baseDate": "20251015", "numOfRows": "30"}),
    ("fuel_1000rows", "SmpDecByFuel2/getSmpDecByFuel2", {"numOfRows": "1000"}),
]

def mask(text):
    if not KEY:
        return text
    for k in (KEY, urllib.parse.quote(KEY, safe=""), urllib.parse.quote_plus(KEY)):
        text = text.replace(k, "***")
    return text

def call(path, extra):
    params = {"serviceKey": KEY, "pageNo": "1", "numOfRows": "100", "dataType": "JSON", **extra}
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

summary = [f"key_present: {bool(KEY)}  key_length: {len(KEY)}", ""]
for name, path, extra in TESTS:
    status, body = call(path, extra)
    body = mask(body)
    (OUT / f"{name}.txt").write_text(body[:8000], encoding="utf-8")
    line = f"{name}: status={status} length={len(body)} preview={body[:150]!r}"
    summary.append(line)
    print(line)

(OUT / "summary_v3.txt").write_text("\n".join(summary), encoding="utf-8")
