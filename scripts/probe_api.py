# API 연결 테스트 v2 (키는 출력하지 않음)
import os, pathlib, urllib.error, urllib.parse, urllib.request

KEY = os.environ.get("DATA_GO_KR_KEY", "").strip()
OUT = pathlib.Path("probe_results")
OUT.mkdir(exist_ok=True)
BASE = "https://apis.data.go.kr/B552115"

TESTS = [
    # 발전원별 발전량 (재시도)
    ("gen_nodate", "PvAmountByPwrGen/getPvAmountByPwrGen", {}),
    ("gen_20250115", "PvAmountByPwrGen/getPvAmountByPwrGen", {"baseDate": "20250115"}),
    # 연료원별 SMP 결정 - 기능 이름 후보 시도
    ("fuel_a", "SmpDecByFuel2/getSmpDecByFuel2", {}),
    ("fuel_b", "SmpDecByFuel2/getSmpDecByFuel", {}),
    ("fuel_c", "SmpDecByFuel2/getSmpDecByFuel2List", {}),
    ("fuel_d", "SmpDecByFuel2/getSmpDecByFuel2", {"date": "20250115"}),
    ("fuel_e", "SmpDecByFuel2/getSmpDecByFuel2", {"baseDate": "20250115"}),
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
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

summary = [f"key_present: {bool(KEY)}  key_length: {len(KEY)}", ""]
for name, path, extra in TESTS:
    status, body = call(path, extra)
    body = mask(body)
    (OUT / f"{name}.txt").write_text(body[:5000], encoding="utf-8")
    line = f"{name}: status={status} preview={body[:150]!r}"
    summary.append(line)
    print(line)

(OUT / "summary.txt").write_text("\n".join(summary), encoding="utf-8")
