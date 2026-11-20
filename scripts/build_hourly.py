"""시간별 SMP + 추정 가격결정 발전기·연료 자료 만들기 (docs/data/hourly_est.json)

발전가격 순위표(data/manual/merit_order.json, 저장소에 올리지 않음)가 있는 PC에서만 실행.
결과(시간별 SMP·추정 발전기명·연료 구분)만 저장소에 올라감. 페이지: docs/hourly.html
사용: python scripts/build_hourly.py 2026-09-01 2026-11-18
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import build_report as B

CAT = {"석탄": "coal", "LNG 개별": "ind", "LNG 직도입": "ind", "LNG 집단": "ind", "LNG 평균": "avg"}


def cat(label, fuel_raw):
    if "평균" in fuel_raw:          # 'LNG (평균·직도입)' 같은 혼합은 평균연료비로 분류
        return "avg"
    return CAT.get(label, "etc")


def main(a, b):
    if not B.MERIT:
        raise SystemExit("data/manual/merit_order.json(발전가격 순위표)이 없어 실행할 수 없음")
    raw_fuel = {m: {r[0]: r[1] for r in L} for m, L in B.MERIT.items()}
    out = {}
    for d in B.rng(a, b):
        if d not in B.SMP or d[:7] not in B.MERIT:
            continue
        e = B.est_hours(d, range(24))
        out[d] = {"smp": [round(v, 2) for v in B.SMP[d]],
                  "u": [n for h, n, lb in e], "lb": [lb for h, n, lb in e],
                  "c": [cat(lb, raw_fuel[d[:7]].get(n, "")) for h, n, lb in e],
                  "wd": B.is_wd(d), "a": dict(B.fuel_daily().get(d, {}))}
    p = B.DOCS / "data" / "hourly_est.json"
    p.write_text(json.dumps({"from": a, "to": b, "days": out}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out)}일 → {p}")


if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) > 2 else ("2026-09-01", "2026-11-18")))
