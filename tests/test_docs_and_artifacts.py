"""交付物完整性测试：门面表里引用的用例编号必须真的存在，产物必须真的存在。

动机：`docs/GBT-47746义务-测试映射.md` 是本项目的门面。表里写一个不存在的用例编号，
比不写更糟——它会让「义务有测试覆盖」这个论断变成无法核对的口号。所以把它做成测试。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MAPPING_DOC = REPO / "docs" / "GBT-47746义务-测试映射.md"

REQUIRED = [
    "README.md", "requirements.txt", ".env.example", "pytest.ini",
    "docs/GBT-47746义务-测试映射.md", "docs/意图体系.md", "docs/语料与许可留证.md",
    "docs/架构图.md", "docs/口径声明.md", "docs/演示脚本.md", "docs/待补与歧义清单.md",
    "data/synthetic_shop/schema.sql", "data/synthetic_shop/seed.sql", "data/synthetic_shop/kb_seed.sql",
    "data/intents/taxonomy.csv", "data/intents/jddc_label_map.csv",
    "data/gates/commitment_whitelist.csv", "data/gates/committal_lexicon.csv", "data/gates/urgency_lexicon.txt",
    "data/jddc/acquisition_log.json", "data/jddc/gate_verdict.json", "data/jddc/mapping_coverage.json",
    "data/jddc/evidence/jddc-lrec2020.pdf", "data/jddc/evidence/jddc.txt",
    "evals/golden_set_v1.jsonl", "evals/judge.py", "evals/metrics.py", "evals/run_eval.py",
    "evals/kappa.py", "evals/human_sampling_5pct.csv", "evals/results.jsonl", "evals/metrics.json",
    "reports/P0-语料闸门.md", "reports/评测结果.md", "reports/变异测试.md", "reports/延迟实测.json",
    "src/intents.py", "src/tools.py", "src/kb.py", "src/dialogue.py", "src/gates.py",
    "src/audit.py", "src/agent.py", "src/cli.py", "src/llm.py", "src/schemas.py", "src/config.py",
    "src/app.py", "src/server.py",
    "ops/__init__.py", "ops/process.py", "ops/services.py", "ops/manager.py", "ops/cli.py",
    "docs/服务编排说明.md", "项目方案-工程质量与能力优化.md",
    "scripts/fetch_jddc.py", "scripts/clean_jddc.py", "scripts/verify_jddc_mapping.py",
    "scripts/mutation_test.py", "scripts/perf_probe.py",
]


@pytest.mark.parametrize("rel", REQUIRED, ids=REQUIRED)
def test_deliverable_exists(rel):
    assert (REPO / rel).exists(), f"交付物缺失：{rel}"


@pytest.fixture(scope="module")
def collected_node_ids() -> set[str]:
    # 注意：这个 pytest 版本在 -q 下 --collect-only 只打印「文件: 条数」，不打印节点号，
    # 所以这里不加 -q，逐行抓含 :: 的节点 id。
    proc = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "tests"],
                          cwd=str(REPO), capture_output=True, text=True, timeout=300, encoding="utf-8")
    ids = {l.strip() for l in proc.stdout.splitlines() if "::" in l}
    assert ids, f"未能收集到用例节点号（returncode={proc.returncode}）：{proc.stdout[-400:]}{proc.stderr[-400:]}"
    return ids


def test_mapping_doc_cites_existing_tests(collected_node_ids):
    text = MAPPING_DOC.read_text(encoding="utf-8")
    cited = set(re.findall(r"tests/test_\w+\.py::[\w\[\]\.\-—…]+", text.replace("…", "")))
    base = set(re.findall(r"`(tests/test_[\w.]+\.py(?:::[\w]+)?(?:\[[^\]]*\])?)`", text))
    targets = {c.strip("`") for c in base | cited}
    assert targets, "门面表必须引用具体用例编号"
    missing = []
    for t in targets:
        node = re.sub(r"\[.*$", "", t)          # 去掉参数化片段
        node = node.split(" …")[0]
        if node.endswith(".py"):
            continue
        if not any(c == node or c.startswith(node + "[") for c in collected_node_ids):
            missing.append(t)
    assert not missing, f"映射表引用了不存在的用例：{sorted(set(missing))}"


def test_mapping_doc_covers_four_obligations():
    text = MAPPING_DOC.read_text(encoding="utf-8")
    for head in ["义务①", "义务②", "义务③", "义务④"]:
        assert head in text, f"四条义务缺对照章节：{head}"
    for a in ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11", "A12", "A13", "A14"]:
        assert f"| {a} " in text or f"| {a}★" in text or f"| {a} ★" in text, f"验收表缺少 {a}"


def test_mutation_report_records_all_killed():
    text = (REPO / "reports" / "变异测试.md").read_text(encoding="utf-8")
    m = re.search(r"注入缺陷：\*\*(\d+) 个\*\*；被测试套件捕获：\*\*(\d+) 个\*\*；存活（未被捕获）：\*\*(\d+) 个",
                  text)
    assert m, "变异测试报告必须写明注入/捕获/存活数量"
    injected, killed, survived = (int(x) for x in m.groups())
    assert injected >= 5 and survived == 0, f"A6 未达成：注入 {injected}，捕获 {killed}，存活 {survived}"


def test_eval_metrics_artifact_is_consistent():
    d = json.loads((REPO / "evals" / "metrics.json").read_text(encoding="utf-8"))
    m, meta = d["metrics"], d["meta"]
    assert meta["provider"] == "template", "默认评测必须跑在可复现的离线 provider 上"
    assert m["total_cases"] >= 150
    assert m["commitment_violations"] == 0, "错承诺条数是红线，必须为 0"
    assert len(m["per_tier"]) == 4, "四档都要有"
    assert "tool_call_success_definition" in m and "correct_handoff_definition" in m, "指标必须带分母口径"


def test_golden_set_tier_counts():
    rows = [json.loads(l) for l in (REPO / "evals" / "golden_set_v1.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    from collections import Counter
    c = Counter(r["tier"] for r in rows)
    assert c["T1_retrieval"] >= 40 and c["T2_tool"] >= 40 and c["T3_mixed"] >= 30 and c["T4_high_risk"] >= 30


def test_human_sampling_file_has_empty_human_column():
    text = (REPO / "evals" / "human_sampling_5pct.csv").read_text(encoding="utf-8-sig")
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines[0].startswith("case_id,tier,user_input,agent_reply,machine_verdict")
    assert "human_verdict" in lines[0]
    assert len(lines) - 1 >= 8, "5% 抽检样本至少 8 条"
    assert all(l.split(",")[6] == "" or '"' in l for l in lines[1:]), "人工列必须是空的（等独立标注）"


def test_kappa_selftest_runs():
    proc = subprocess.run([sys.executable, "-m", "evals.kappa", "--selftest"], cwd=str(REPO),
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "计算器与手算一致" in proc.stdout


def test_ops_docs_document_commands():
    text = (REPO / "docs" / "服务编排说明.md").read_text(encoding="utf-8")
    for cmd in ["python -m ops.cli start", "python -m ops.cli stop", "python -m ops.cli status",
                "python -m ops.cli check", "python -m ops.cli logs"]:
        assert cmd in text, f"编排文档缺命令：{cmd}"
    for section in ["预检", "优雅", "孤儿", "端口", "新增服务"]:
        assert section in text, f"编排文档缺章节：{section}"


def test_p0_report_states_gate_failure():
    text = (REPO / "reports" / "P0-语料闸门.md").read_text(encoding="utf-8")
    assert "NOT_PASSED" in text
    for url in ["jddc.jd.com", "data.aichengxing.com", "hf-mirror.com"]:
        assert url in text, f"P0 报告应记录逐入口证据：{url} 缺失"
    log = json.loads((REPO / "data" / "jddc" / "acquisition_log.json").read_text(encoding="utf-8"))
    assert log["gate_f-001"] == "NOT_PASSED"
    assert len(log["probes"]) >= 6
    assert log["python"] != "req", "留证必须记录真实运行环境版本"


def test_readme_links_are_real_files():
    text = (REPO / "README.md").read_text(encoding="utf-8")
    for m in re.findall(r"\]\(([^)#][^)]*)\)", text):
        assert (REPO / m).exists(), f"README 链接指向不存在的文件：{m}"
