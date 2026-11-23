"""JDDC 标签 ↔ 自有意图体系 的映射核对（F-002 的机检部分）。

拿到语料并跑过 `scripts/clean_jddc.py` 之后，本脚本把**语料里实际出现的标签**逐个对照
`data/intents/jddc_label_map.csv`：映射不到的标签进「未覆盖清单」（方案 R3：不硬凑）。

语料没拿到时（当前状态）它只做两件事：
  1. 校验映射表自身：每条 paper_verified 的行都指向存在的二级意图，且给出可核对定位；
  2. 明确输出「无法全量核对」的结论与原因，而不是假装已经映射完成。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MAP_CSV = REPO / "data" / "intents" / "jddc_label_map.csv"
TAXONOMY = REPO / "data" / "intents" / "taxonomy.csv"
OBSERVED = REPO / "data" / "jddc" / "processed" / "observed_labels.json"
OUT = REPO / "data" / "jddc" / "mapping_coverage.json"


def load_map() -> list[dict]:
    with open(MAP_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JDDC 标签映射覆盖率核对")
    ap.add_argument("--observed", default=str(OBSERVED))
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    codes = {r["code"] for r in csv.DictReader(TAXONOMY.read_text(encoding="utf-8").splitlines())}
    rows = load_map()
    bad_codes = [r["jddc_label_raw"] for r in rows
                 if r["map_status"] == "paper_verified" and r["mapped_l2_code"] not in codes]
    no_evidence = [r["jddc_label_raw"] for r in rows if not r["evidence_locator"].strip()]

    observed_path = Path(args.observed)
    corpus_available = observed_path.exists()
    unmapped: list[str] = []
    coverage: dict = {"corpus_available": corpus_available}

    fixture_mode = False
    stats_path = observed_path.parent / "corpus_stats.json"
    if stats_path.exists():
        fixture_mode = bool(json.loads(stats_path.read_text(encoding="utf-8")).get("is_synthetic_fixture"))
    if corpus_available:
        labels = json.loads(observed_path.read_text(encoding="utf-8"))
        mapped_raw = {r["jddc_label_raw"] for r in rows if r["mapped_l2_code"] != "UNMAPPED"}
        unmapped = sorted(l for l in labels if l not in mapped_raw)
        coverage.update({"observed_labels": len(labels), "mapped_labels": len(labels) - len(unmapped),
                         "unmapped_labels": len(unmapped),
                         "coverage_rate": f"{(len(labels) - len(unmapped))}/{len(labels)}" if labels else "n/a"})
    else:
        coverage.update({
            "observed_labels": 0,
            "paper_verified_rows": sum(1 for r in rows if r["map_status"] == "paper_verified"),
            "pending_corpus_rows": sum(1 for r in rows if r["map_status"] == "pending_corpus"),
            "conclusion": "无法全量核对：JDDC 原始文件未入库（F-001 闸门未通过）。"
                          "当前只有论文正文可核对的 10 行映射，其余标签必须拿到语料后逐条核对。",
        })

    coverage["labels_from_synthetic_fixture"] = fixture_mode
    if fixture_mode:
        coverage["conclusion"] = ("当前 observed_labels 来自 --fixture 生成的合成结构样例，"
                                 "只证明清洗/核对脚本可跑，不能作为语料覆盖率结论。")
    coverage.update({"map_rows": len(rows), "taxonomy_codes": len(codes),
                     "rows_pointing_to_unknown_code": bad_codes, "rows_without_evidence": no_evidence,
                     "unmapped_examples": unmapped[:50]})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[mapping] 映射表 {len(rows)} 行；taxonomy {len(codes)} 个二级意图")
    if bad_codes:
        print(f"[mapping] !! 指向不存在意图的行：{bad_codes}")
    if no_evidence:
        print(f"[mapping] !! 缺少证据定位的行：{no_evidence}")
    if corpus_available:
        tag = "（合成样例，数字不可引用）" if fixture_mode else ""
        print(f"[mapping] 标签 {coverage['observed_labels']} 个，已映射 {coverage['mapped_labels']}，"
              f"未覆盖 {coverage['unmapped_labels']}{tag}")
    else:
        print(f"[mapping] {coverage['conclusion']}")
    print(f"[mapping] 结果 → {args.out}")
    return 1 if (bad_codes or no_evidence) else 0


if __name__ == "__main__":
    raise SystemExit(main())
