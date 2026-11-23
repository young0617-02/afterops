"""口径纪律测试（验收 A4 / A12）：把「不许说什么」做成可执行检查。

A4：不得把「拦截率 / deflection rate / assumed resolution（沉默即解决）」当我方指标；
A12：不得出现「已上线 / 商家在用 / 店铺接入 / 生产可用 / 零幻觉」这类虚假宣称。
这两条是纸面约定，最容易在写 README 时破功，所以用 grep 级别的红线测试锁住。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCAN_GLOBS = ["README.md", "交付总结.md", "项目方案-*.md", "docs/*.md", "reports/*.md", "evals/*.py", "src/*.py", "data/**/*.sql",
              "data/**/*.csv"]

# A4：我方指标口径里禁止出现的表述（允许作为「反面基线/外部对照」出现，但同一行必须带标记词）
BANNED_METRICS = ["拦截率", "deflection rate", "deflection", "assumed resolution", "沉默即解决", "沉默即视为解决"]
CONTEXT_MARKERS = ["反面", "不使用", "不采用", "不报", "禁止", "非我方", "口径对照", "外部", "相反",
                   "明确不", "禁用", "不产出", "不是", "不做", "不属于", "不用于"]

# A12：虚假宣称
BANNED_CLAIMS = ["已上线", "商家在用", "店铺接入", "真实店铺接入", "生产可用", "零幻觉", "100% 准确",
                 "100%准确", "完全避免", "杜绝一切", "线上验证"]

# 项目自述里出现这些数字必须带「合成/离线/设计值/未测」限定词
NEEDS_QUALIFIER = {
    "≥98%": ["从业者模板", "非行业标准", "设计", "目标"],
    "≥85%": ["从业者模板", "非行业标准", "设计", "目标"],
}


def scanned_files() -> list[Path]:
    out: list[Path] = []
    for pattern in SCAN_GLOBS:
        out.extend(sorted(REPO.glob(pattern)))
    return [f for f in out if f.is_file()]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_A4_no_deflection_or_assumed_resolution_as_our_metric(path):
    for i, line in enumerate(_lines(path), 1):
        low = line.lower()
        for term in BANNED_METRICS:
            if term in low or term.lower() in low:
                assert any(m in line for m in CONTEXT_MARKERS), (
                    f"{path.relative_to(REPO)}:{i} 把「{term}」当作我方指标表述；"
                    f"若要提及必须写成反面基线/外部对照，例如：{line.strip()[:80]}")


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_A12_no_false_claims(path):
    for i, line in enumerate(_lines(path), 1):
        for term in BANNED_CLAIMS:
            assert term not in line, f"{path.relative_to(REPO)}:{i} 出现虚假宣称「{term}」：{line.strip()[:80]}"


@pytest.mark.parametrize("path", scanned_files(), ids=lambda p: str(p.relative_to(REPO)))
def test_industry_thresholds_are_marked_as_practitioner_templates(path):
    for i, line in enumerate(_lines(path), 1):
        for token, markers in NEEDS_QUALIFIER.items():
            if token in line and not line.strip().startswith("#"):
                assert any(m in line for m in markers) or any(m in "".join(_lines(path)) for m in markers), (
                    f"{path.relative_to(REPO)}:{i} 引用了 {token} 的行业区间，"
                    f"方案 §十 要求同时声明「从业者模板，非行业标准」")


def test_requirements_contains_pytest():
    req = (REPO / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^pytest", req, re.M), "方案 §五：requirements 必含 pytest（一票否决项）"


def test_readme_declares_offline_scope():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for must in ["离线", "合成", "公开语料", "无真实流量"]:
        assert must in readme, f"README 必须包含口径声明关键词：{must}"


def test_env_placeholders_are_centralised():
    """所有需要人来定的东西集中出现在 .env.example 与待补清单里，不散落。"""
    allowed = {(REPO / ".env.example").resolve(),
               (REPO / "docs" / "待补与歧义清单.md").resolve(),
               (REPO / "src" / "config.py").resolve()}  # 该文件负责实现「待填占位」的识别规则本身
    offenders = []
    for f in scanned_files():
        if f.resolve() in allowed:
            continue
        if "【填：" in f.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(f.relative_to(REPO)))
    assert not offenders, f"待填占位符散落在此处，应集中到 .env.example / 待补清单：{offenders}"
