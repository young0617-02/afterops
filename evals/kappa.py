"""机判 vs 人工判的一致性校准（F-015）：Cohen's kappa。

流程：
  1. `python -m evals.run_eval` 生成 `evals/human_sampling_5pct.csv`（分层抽样，机判列已填，人工列留空）；
  2. **由你（人）独立填写 human_verdict 列**（pass/fail），不要先看 machine_verdict；
  3. `python -m evals.kappa` 计算 kappa 与混淆矩阵。

抽样方法（写进报告的口径）：按四档分层，名额按档位用例数比例分配（每档至少 1 条），
随机种子固定（默认 20261006），总体 170 条 → 样本 8 条（≈4.7%）。
样本量 8 条只能给出方向性一致度，置信区间宽，报告里如实标注，不外推。
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CSV = REPO / "evals" / "human_sampling_5pct.csv"
LABELS = ("pass", "fail")


def cohen_kappa(pairs: list[tuple[str, str]]) -> dict:
    """标准 Cohen's kappa：k = (p_o - p_e) / (1 - p_e)。"""
    n = len(pairs)
    if n == 0:
        return {"n": 0, "kappa": None, "note": "样本为空"}
    po = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum((ca[l] / n) * (cb[l] / n) for l in LABELS)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    # kappa 的近似标准误（Stat1 区间，样本小时仅供方向判断）
    se = None
    if kappa is not None and kappa < 1:
        p1 = sum(1 for a, b in pairs if a == b) / n
        se = math.sqrt((p1 * (1 - p1)) / (n * (1 - pe) ** 2)) if n > 1 else None
    conf = {}
    if se:
        conf = {"ci95_low": round(kappa - 1.96 * se, 3), "ci95_high": round(kappa + 1.96 * se, 3)}
    matrix = {f"machine_{m}/human_{h}": 0 for m in LABELS for h in LABELS}
    for a, b in pairs:
        matrix[f"machine_{a}/human_{b}"] += 1
    return {"n": n, "observed_agreement": round(po, 3), "expected_agreement": round(pe, 3),
            "kappa": None if kappa is None else round(kappa, 3), "se": None if se is None else round(se, 3),
            **conf, "confusion": matrix}


def read_pairs(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    pairs, pending = [], []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            machine = (row["machine_verdict"] or "").strip().lower()
            human = (row["human_verdict"] or "").strip().lower()
            if machine not in LABELS:
                continue
            if human not in LABELS:
                pending.append(row["case_id"])
                continue
            pairs.append((machine, human))
    return pairs, pending


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="计算机判与人工判的 Cohen's kappa")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--selftest", action="store_true", help="用构造样本校验计算器（不是评测结果）")
    args = ap.parse_args(argv)

    if args.selftest:
        # 构造样本（仅校验计算器，不是评测结果）：n=10，po=8/10=0.8
        # 机判边缘 pass=8/fail=2，人判边缘 pass=6/fail=4 → pe=0.8*0.6+0.2*0.4=0.56
        # kappa=(0.8-0.56)/(1-0.56)=0.54545…
        pairs = [("pass", "pass")] * 6 + [("fail", "fail")] * 2 + [("pass", "fail")] * 2
        got = cohen_kappa(pairs)
        expected = (0.8 - 0.56) / (1 - 0.56)
        print("[selftest] 构造样本（非评测结果）：", got)
        assert abs(got["kappa"] - round(expected, 3)) < 1e-9, (got, expected)
        print(f"[selftest] 计算器与手算一致（{round(expected, 5)}）")
        return 0

    path = Path(args.csv)
    if not path.exists():
        print(f"[kappa] 找不到 {path}；先运行 python -m evals.run_eval 生成抽检表")
        return 1
    pairs, pending = read_pairs(path)
    if pending:
        print(f"[kappa] 还有 {len(pending)} 条未填 human_verdict：{pending}")
    if not pairs:
        print("[kappa] 人工列全空，无法计算。请填写 evals/human_sampling_5pct.csv 的 human_verdict 列后重跑。")
        return 2
    res = cohen_kappa(pairs)
    print(f"[kappa] 已标注 {res['n']} 条：一致度 {res['observed_agreement']}，期望一致 {res['expected_agreement']}，"
          f"kappa = {res['kappa']} 95%CI [{res.get('ci95_low')}, {res.get('ci95_high')}]")
    print(f"[kappa] 混淆矩阵：{res['confusion']}")
    if res["n"] < 30:
        print("[kappa] 提醒：样本 <30 条，仅供方向判断，不作为对外宣称的一致性水平。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
