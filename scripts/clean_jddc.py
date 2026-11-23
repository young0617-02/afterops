"""JDDC 子集清洗与可复现抽样（F-003）。

抽样/清洗规则全部写死在这里并带随机种子，目的是「换台机器也能重跑出同一批样本」。
输入格式：JDDC 会话（论文 §3.1 / 挑战赛发布格式）
    {"ID": ..., "seller_id": ..., "user": [{"utterance": ...}], "vendor": [{"utterance": ...}],
     "labelled": [{"utterance": ..., "label": ...}]}
支持 .json（整个数组）与 .jsonl（每行一个会话）；也支持 --fixture 生成一份**合成样例**用于自测。

注意：本仓库不附带 JDDC 原始数据（见 reports/P0-语料闸门.md）。用 --fixture 生成的文件
是合成样例，只能用于验证脚本本身，不能当成语料统计结果对外引用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import unicodedata
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SEED = 20261006
MIN_USER_TURNS = 2
MIN_VENDOR_TURNS = 1
MAX_UTTERANCE_CHARS = 200

DROP_REASONS = ("empty_session", "too_few_user_turns", "too_few_vendor_turns",
                "empty_or_blank_utterance", "utterance_too_long", "duplicate_session")


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").strip()
    return re.sub(r"\s+", " ", t)


def load_sessions(raw: Path) -> list[dict]:
    data = raw.read_text(encoding="utf-8", errors="replace")
    stripped = data.lstrip()
    if stripped.startswith("["):
        return json.loads(data)
    return [json.loads(l) for l in data.splitlines() if l.strip()]


def key_of(sess: dict) -> str:
    seq = "|".join(norm(u.get("utterance", "")) for u in sess.get("user", []))
    return hashlib.sha256(seq.encode("utf-8")).hexdigest()


def clean(sessions: list[dict]) -> tuple[list[dict], Counter, Counter]:
    kept: list[dict] = []
    dropped: Counter = Counter()
    labels: Counter = Counter()
    seen: set[str] = set()
    for s in sessions:
        user = [norm(u.get("utterance", "")) for u in s.get("user", [])]
        vendor = [norm(v.get("utterance", "")) for v in s.get("vendor", [])]
        if not user and not vendor:
            dropped["empty_session"] += 1
            continue
        # 先判空白再判轮数：先过滤掉空串会让「有空话轮」的会话躲过检查，统计口径就假了
        if any(not t for t in user + vendor):
            dropped["empty_or_blank_utterance"] += 1
            continue
        if len(user) < MIN_USER_TURNS:
            dropped["too_few_user_turns"] += 1
            continue
        if len(vendor) < MIN_VENDOR_TURNS:
            dropped["too_few_vendor_turns"] += 1
            continue
        if any(len(u) > MAX_UTTERANCE_CHARS for u in user + vendor):
            dropped["utterance_too_long"] += 1
            continue
        k = key_of(s)
        if k in seen:
            dropped["duplicate_session"] += 1
            continue
        seen.add(k)
        for lab in s.get("labelled", []) or []:
            if lab.get("label"):
                labels[lab["label"]] += 1
        kept.append({"id": s.get("ID") or s.get("id") or k[:16],
                     "user": user, "vendor": vendor,
                     "labels": [lab.get("label") for lab in (s.get("labelled") or []) if lab.get("label")],
                     "turns": len(user) + len(vendor)})
    return kept, dropped, labels


def stratified_sample(kept: list[dict], n: int, seed: int) -> list[dict]:
    """按首个意图标签分层抽样；名额按层占比分配（每层至少 1 条），层内用固定种子随机。"""
    if n <= 0 or n >= len(kept):
        return list(kept)
    buckets: dict[str, list[dict]] = {}
    for s in kept:
        buckets.setdefault((s["labels"] or ["<no-label>"])[0], []).append(s)
    rng = random.Random(seed)
    quota: dict[str, int] = {}
    for label, pool in buckets.items():
        quota[label] = max(1, round(n * len(pool) / len(kept)))
    # 名额超出时从最大的层回收，保证总数恰为 n
    while sum(quota.values()) > n:
        big = max(quota, key=lambda k: quota[k] - max(1, round(n * len(buckets[k]) / len(kept))))
        if quota[big] <= 1:
            break
        quota[big] -= 1
    picked: list[dict] = []
    for label, pool in buckets.items():
        take = min(quota.get(label, 1), len(pool))
        picked.extend(rng.sample(pool, take))
    picked.sort(key=lambda s: str(s["id"]))
    return picked[:n] if len(picked) > n else picked


def quantiles(values: list[int]) -> dict:
    if not values:
        return {}
    vs = sorted(values)
    return {"n": len(vs), "min": vs[0], "p50": vs[len(vs) // 2], "p95": vs[min(len(vs) - 1, int(0.95 * len(vs)))],
            "max": vs[-1], "mean": round(sum(vs) / len(vs), 2)}


def make_fixture(path: Path) -> None:
    """合成一份 JDDC 结构的样例（不是语料本身），用于验证清洗/抽样脚本可跑。"""
    sessions = []
    for i in range(1, 13):
        label = ["Warranty and return policy", "Delivery duration", "Change order information",
                 "Check order status", "Contact customer service"][(i - 1) % 5]
        sessions.append({
            "ID": f"sess-{i:03d}", "seller_id": "synthetic-seller",
            "user": [{"utterance": f"订单 ORD-2026-0001{i:02d} 什么时候到"},
                     {"utterance": f"那能退吗，第{i}个问题"}],
            "vendor": [{"utterance": "这边帮您查一下哦"}, {"utterance": "预计 2 个工作日送达"}],
            "labelled": [{"utterance": f"那能退吗，第{i}个问题", "label": label}]})
    # 故意混入要被剔除的样本：单轮、空白、超长、重复
    sessions.append({"ID": "sess-bad-single", "user": [{"utterance": "在吗"}],
                     "vendor": [{"utterance": "在的"}], "labelled": []})
    sessions.append({"ID": "sess-bad-blank", "user": [{"utterance": "  "}, {"utterance": "退款"}],
                     "vendor": [{"utterance": "好的"}], "labelled": []})
    sessions.append({"ID": "sess-bad-long", "user": [{"utterance": "问题"}, {"utterance": "长" * 300}],
                     "vendor": [{"utterance": "好的"}], "labelled": []})
    sessions.append(json.loads(json.dumps(sessions[0])))  # 完全重复的一条
    path.write_text(json.dumps(sessions, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JDDC 子集清洗与抽样")
    ap.add_argument("--raw", type=Path, default=None, help="JDDC 原始文件（json/jsonl）")
    ap.add_argument("--fixture", action="store_true", help="先合成一份结构样例（自测用，非语料）")
    ap.add_argument("--out-dir", type=Path, default=REPO / "data" / "jddc" / "processed")
    ap.add_argument("--n", type=int, default=2000, help="抽样条数上限（0=全量）")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args(argv)

    raw = args.raw
    if args.fixture:
        raw = args.out_dir / "synthetic_fixture.json"
        raw.parent.mkdir(parents=True, exist_ok=True)
        make_fixture(raw)
        print(f"[clean] 已生成合成结构样例（仅用于脚本自测）：{raw}")
    if not raw or not Path(raw).exists():
        print("[clean] 需要 --raw 指向已入库的 JDDC 文件，或先用 --fixture 跑自测。"
              "获取语料见 scripts/fetch_jddc.py")
        return 1

    sessions = load_sessions(Path(raw))
    kept, dropped, labels = clean(sessions)
    sampled = stratified_sample(kept, args.n, args.seed)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "sampled_dialogues.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in sampled) + "\n", encoding="utf-8")
    (out / "observed_labels.json").write_text(json.dumps(dict(labels.most_common()), ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    stats = {
        "source_file": str(raw),
        "generated_at": str(__import__("datetime").datetime.now())[:19],
        "seed": args.seed,
        "rules": {"MIN_USER_TURNS": MIN_USER_TURNS, "MIN_VENDOR_TURNS": MIN_VENDOR_TURNS,
                  "MAX_UTTERANCE_CHARS": MAX_UTTERANCE_CHARS, "dedupe": "user 序列 sha256"},
        "is_synthetic_fixture": args.fixture,
        "loaded_sessions": len(sessions), "kept_sessions": len(kept),
        "dropped": dict(dropped), "sampled": len(sampled),
        "turns_quantiles": quantiles([s["turns"] for s in kept]),
        "distinct_labels": len(labels), "label_top50": dict(labels.most_common(50)),
    }
    (out / "corpus_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[clean] 载入 {len(sessions)} → 合格 {len(kept)} → 抽样 {len(sampled)}；"
          f"剔除分布 {dict(dropped)}")
    print(f"[clean] 观测到意图标签 {len(labels)} 个；统计 → {out / 'corpus_stats.json'}")
    if args.fixture:
        print("[clean] 提醒：本次输入是合成样例，统计数字不代表 JDDC 语料。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
