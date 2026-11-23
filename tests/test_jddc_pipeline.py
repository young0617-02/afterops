"""JDDC 管道脚本的可复现性测试（F-003）。

这些用例**不联网**：语料可达性只用构造的探测结果验证判定函数；
清洗/抽样用合成结构样例验证规则与随机种子是否真的可复跑。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cleaner():
    return load_script("clean_jddc.py")


@pytest.fixture(scope="module")
def fetcher():
    return load_script("fetch_jddc.py")


def _sessions(cleaner, tmp_path):
    p = Path(tmp_path) / "jddc_fixture.json"
    cleaner.make_fixture(p)
    return cleaner.load_sessions(p), p


def test_clean_rules_applied(cleaner, tmp_path):
    sessions, src = _sessions(cleaner, tmp_path)
    kept, dropped, labels = cleaner.clean(sessions)
    assert len(kept) == 12, f"合格会话应为 12，实际 {len(kept)}"
    assert dropped["too_few_user_turns"] == 1
    assert dropped["empty_or_blank_utterance"] == 1
    assert dropped["utterance_too_long"] == 1
    assert dropped["duplicate_session"] == 1
    assert labels, "应统计到意图标签"


def test_sampling_is_reproducible(cleaner, tmp_path):
    sessions, src = _sessions(cleaner, tmp_path)
    kept, _d, _l = cleaner.clean(sessions)
    a = cleaner.stratified_sample(kept, 6, seed=20261006)
    b = cleaner.stratified_sample(kept, 6, seed=20261006)
    c = cleaner.stratified_sample(kept, 6, seed=999)
    assert [x["id"] for x in a] == [x["id"] for x in b], "同种子必须产出同一样本"
    assert len(a) == 6
    assert {x["id"] for x in a} != {x["id"] for x in c} or len(kept) <= 6
    src.unlink()


def test_sampling_covers_every_label_bucket(cleaner, tmp_path):
    sessions, src = _sessions(cleaner, tmp_path)
    kept, _d, _l = cleaner.clean(sessions)
    picked = cleaner.stratified_sample(kept, 5, seed=20261006)
    buckets = {(s["labels"] or ["<no-label>"])[0] for s in kept}
    assert len({(s["labels"] or ["<no-label>"])[0] for s in picked}) == min(5, len(buckets)), "分层抽样应覆盖各层"
    src.unlink()


def test_quantiles_shape(cleaner):
    q = cleaner.quantiles([2, 4, 6, 8, 10])
    assert q["min"] == 2 and q["max"] == 10 and q["n"] == 5


def test_probe_verdict_logic(fetcher):
    """闸门判定是纯函数：用构造的探测结果验证，不依赖网络。"""
    cases = [
        ({"status": None, "error": "URLError"}, "mirror", "no_data"),
        ({"status": 404, "error": "HTTPError", "final_url": "x"}, "official_oss", "no_data"),
        ({"status": 200, "final_url": "https://www.jd.com/error2.aspx?from=shopdomain", "body_head": "抱歉"},
         "official", "no_data"),
        ({"status": 200, "final_url": "https://hf-mirror.com/api/datasets?search=jddc", "body_full": "[]"},
         "mirror", "no_data"),
        ({"status": 200, "final_url": "u", "body_full": json.dumps([{"id": "a/jddc"}])}, "mirror", "data_candidate"),
        ({"status": 200, "final_url": "u", "body_head": "pdf"}, "paper", "reference_only"),
    ]
    for rec, kind, expect in cases:
        verdict, reason = fetcher.judge(rec, kind)
        assert verdict == expect, (rec, kind, verdict, reason)


def test_ingest_manifest_roundtrip(fetcher, tmp_path):
    f = tmp_path / "train.json"
    f.write_text(json.dumps([{"ID": "1", "user": [{"utterance": "a"}]}]), encoding="utf-8")
    rc = fetcher.cmd_ingest(f, tmp_path / "raw")
    assert rc == 0
    manifest = json.loads((tmp_path / "raw" / "ingest_manifest.json").read_text(encoding="utf-8"))
    assert manifest["size_bytes"] == f.stat().st_size
    assert len(manifest["sha256"]) == 64
    assert "不附带" in manifest["note"], "入库清单必须声明本仓库不附带语料"


def test_mapping_coverage_without_corpus(tmp_path, monkeypatch):
    mod = load_script("verify_jddc_mapping.py")
    out = tmp_path / "coverage.json"
    monkeypatch.chdir(REPO)
    rc = mod.main(["--observed", str(tmp_path / "不存在.json"), "--out", str(out)])
    data = json.loads(out.read_text(encoding="utf-8"))
    assert rc == 0
    assert data["corpus_available"] is False
    assert "无法全量核对" in data["conclusion"]
    assert data["paper_verified_rows"] == 10
