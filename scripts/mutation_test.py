"""变异测试（F-014 / 验收 A6）：往源码里注入已知缺陷，看测试套件抓不抓得住。

为什么做这个：门禁类项目最容易自欺——测试全绿可能只是因为断言写得太松。
这里逐个把「只解释不确认」「承诺拦截」「人工出口」「留痕」「双时态」「数字来源」等机制打断，
要求每个缺陷都被至少一条用例捕获，最后还原源码并校验 MD5 与注入前一致。

用法：
    python scripts/mutation_test.py            # 跑全部变异体
    python scripts/mutation_test.py --only M1 M3
    python scripts/mutation_test.py --report reports/变异测试.md
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Mutant:
    mid: str
    defect: str
    file: str
    anchor: str
    replacement: str
    tests: list[str]
    result: dict = field(default_factory=dict)


MUTANTS = [
    Mutant("M1", "高危意图不再触发「只解释不确认」（F-008 失效）", "src/gates.py",
           "        if intent.is_high_risk:", "        if False:",
           ["tests/test_obligation1_explain_only.py"]),
    Mutant("M2", "承诺请求不再在生成前拦截（F-010 第一道锁失效）", "src/gates.py",
           "        if commit_requested:", "        if False:",
           ["tests/test_obligation3_commitment_whitelist.py"]),
    Mutant("M3", "输出侧复核直接放行（F-010 第二道锁失效）", "src/gates.py",
           "        if not self.rules.enabled:",
           "        if True:\n            return ScanResult(blocked=False)\n        if not self.rules.enabled:",
           ["tests/test_obligation3_commitment_whitelist.py::test_A2_second_layer_blocks_untrusted_model_output"]),
    Mutant("M4", "审计不再记录检索证据（F-011 反查断链）", "src/audit.py",
           '"evidence": _canon(evidence), "blocked_commits"',
           '"evidence": _canon([]), "blocked_commits"',
           ["tests/test_obligation4_audit_trace.py::test_A11_trace_reverse_lookup_rule_version_and_evidence"]),
    Mutant("M5", "检索忽略有效期，过期条款仍可命中（F-006 双时态失效）", "src/kb.py",
           "        return [e for e in self.entries.values() if e.is_valid_on(as_of)]",
           "        return list(self.entries.values())",
           ["tests/test_kb_bitemporal.py"]),
    Mutant("M6", "人工出口后置断言关闭（F-009 失守）", "src/agent.py",
           '        if "人工" not in reply:', "        if False:",
           ["tests/test_obligation2_human_entry.py"]),
    Mutant("M7", "工具把 SQL 算出的实付金额 +1（A9 数字来源被破坏）", "src/tools.py",
           '                "payable_cents": r["payable_cents"],',
           '                "payable_cents": r["payable_cents"] + 1,',
           ["tests/test_agent_e2e.py::test_A9_amounts_equal_sql_view_output"]),
    Mutant("M8", "指代消解不再工作（F-007）", "src/dialogue.py",
           "        resolved: dict[str, str] = {}\n        out = text",
           "        resolved: dict[str, str] = {}\n        return text, resolved\n        out = text",
           ["tests/test_dialogue.py::test_coreference_resolves_to_last_order"]),
    Mutant("M9", "低置信门禁关闭，允许硬答（F-012）", "src/gates.py",
           "        soft = conf < self.confidence_threshold",
           "        soft = False",
           ["tests/test_confidence_gate.py"]),
    Mutant("M10", "白名单判定恒为允许（承诺类别可外溢）", "src/gates.py",
           "        return v in self.allowed and v not in COMMITTAL_VALUES",
           "        return True",
           ["tests/test_data_contracts.py::test_whitelist_covers_every_commit_class",
            "tests/test_obligation3_commitment_whitelist.py::test_whitelist_admits_only_allowed_classes"]),
    Mutant("M11", "停止流程跳过端口释放与孤儿校验（报告「已停止」但其实没验）", "ops/manager.py",
           '        port = getattr(svc, "port", None)',
           '        return  # 变异：不校验端口释放\n        port = getattr(svc, "port", None)',
           ["tests/test_ops.py::test_start_then_graceful_stop_leaves_nothing_behind"]),
    Mutant("M12", "pid_alive 对非法 pid 返回 True（探活说谎，孤儿看不出来）", "ops/process.py",
           "    if pid <= 0:\n        return False",
           "    if pid <= 0:\n        return True",
           ["tests/test_ops.py::test_pid_alive_never_kills_on_windows_semantics",
            "tests/test_ops.py::test_stop_when_process_already_dead_cleans_state"]),
]


def md5(text: bytes) -> str:
    return hashlib.md5(text).hexdigest()


def run_pytest(tests: list[str], timeout: int = 300) -> tuple[bool, int, str]:
    """返回 (套件是否失败=捕获, 失败用例数, 摘要)。"""
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "--no-header", *tests],
                          cwd=str(REPO), capture_output=True, text=True, timeout=timeout)
    tail = proc.stdout.strip().splitlines()[-4:] if proc.stdout.strip() else []
    failed = proc.returncode != 0
    n_fail = proc.stdout.count("FAILED") + sum(1 for l in tail if l.startswith("ERROR"))
    return failed, n_fail, " | ".join(tail[-2:])


def apply_and_restore(m: Mutant, keep: bytes) -> tuple[bool, int, str, str, str]:
    path = REPO / m.file
    src = keep.decode("utf-8")
    # 源文件可能是 CRLF：把锚点/替换里的换行适配成文件本身的风格，否则跨行锚点匹配不到
    CRLF = chr(13) + chr(10)
    nl = CRLF if CRLF in src else chr(10)
    anchor = m.anchor.replace(chr(10), nl)
    replacement = m.replacement.replace(chr(10), nl)
    if anchor not in src:
        raise RuntimeError(f"{m.mid} 锚点未找到，说明源码结构已变化：{m.file}")
    mutated = src.replace(anchor, replacement, 1)
    path.write_text(mutated, encoding="utf-8")
    try:
        killed, n_fail, summary = run_pytest(m.tests)
    finally:
        path.write_bytes(keep)
    after = path.read_bytes()
    return killed, n_fail, summary, md5(keep), md5(after)


def render(mutants: list[Mutant], started: float) -> str:
    killed = sum(1 for m in mutants if m.result.get("killed"))
    restored = sum(1 for m in mutants if m.result.get("restored_ok"))
    lines = [
        "# 变异测试（F-014 / 验收 A6）",
        "",
        f"- 运行时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(started))}，"
        f"耗时 {time.time() - started:.1f}s",
        "- 命令：`python scripts/mutation_test.py --report reports/变异测试.md`",
        f"- 环境：Python {sys.version.split()[0]}，每个变异体单独起一次 pytest 进程",
        "",
        "## 结论（实测）",
        "",
        f"- 注入缺陷：**{len(mutants)} 个**；被测试套件捕获：**{killed} 个**；"
        f"存活（未被捕获）：**{len(mutants) - killed} 个**",
        f"- 源码还原：{restored}/{len(mutants)} 个 MD5 与注入前一致"
        f"（{'全部一致' if restored == len(mutants) else '有不一致，需人工检查'}）",
        f"- A6 判据「注入 ≥5 缺陷全捕获」：**{'通过' if killed == len(mutants) and len(mutants) >= 5 else '未通过'}**",
        "",
        "| 变异体 | 注入的缺陷 | 文件 | 覆盖用例 | 结果 | 首个失败摘要 | 注入前 MD5 | 还原后 MD5 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in mutants:
        r = m.result
        lines.append(
            "| " + m.mid + " | " + m.defect + " | `" + m.file + "` | " + " ".join(m.tests) + " | "
            f"{'✅ 捕获' if r.get('killed') else '❌ 存活'} | {(r.get('summary') or '')[:90]} | "
            f"{r.get('md5_before', '')[:12]} | {r.get('md5_after', '')[:12]}{'（一致）' if r.get('restored_ok') else '（不一致）'} |")
    lines += [
        "",
        "## 怎么读这张表",
        "",
        "- **捕获=该缺陷让至少一条用例变红**。存活意味着套件对这个缺陷不敏感，需要补用例，"
        "而不是宣布「机制没问题」。",
        "- 每个变异体只改一处、跑完立刻还原，还原后用 MD5 比对确认源码没有被改写残留。",
        "- 变异体覆盖的是**机制层**（门禁、留痕、时效、数字来源），不涉及模型效果；"
        "模型效果类断言见 `reports/评测结果.md`。",
        "",
        "## 与四条国标义务的对应",
        "",
        "- M1 → 义务「重大事项只解释不确认」；M2/M3/M10 → 义务「承诺白名单」；"
        "M6 → 义务「人工入口不得隐藏」；M4 → 义务「留痕可溯」。",
        "- M5/M7/M8/M9 → 支撑机制（双时态、数字来源、多轮、置信度），不属于国标四条但属于本项目红线的前置。",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="变异测试：注入缺陷、验证套件鉴别力、还原并校验哈希")
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定变异体 id")
    ap.add_argument("--report", default=None, help="写出 Markdown 报告的路径")
    ap.add_argument("--backup-dir", default=".probe/mutation-backup", help="注入前的源码快照目录")
    args = ap.parse_args(argv)

    targets = [m for m in MUTANTS if not args.only or m.mid in args.only]
    # 保险：先把将被改写的源码文件快照一份，即使进程被中断也能人工恢复
    backup = REPO / args.backup_dir
    backup.mkdir(parents=True, exist_ok=True)
    for m in targets:
        src = REPO / m.file
        (backup / (src.name + ".orig")).write_bytes(src.read_bytes())
    started = time.time()
    for m in targets:
        path = REPO / m.file
        keep = path.read_bytes()
        print(f"[mutation] {m.mid} 注入 → {m.file}：{m.defect}")
        try:
            killed, n_fail, summary, h_before, h_after = apply_and_restore(m, keep)
        except Exception as exc:
            path.write_bytes(keep)
            print(f"  !! {m.mid} 执行异常：{exc}")
            m.result = {"killed": False, "error": str(exc), "md5_before": md5(keep),
                        "md5_after": md5(path.read_bytes()), "restored_ok": True, "summary": str(exc)}
            continue
        m.result = {"killed": killed, "failing_cases": n_fail, "summary": summary,
                    "md5_before": h_before, "md5_after": h_after, "restored_ok": h_before == h_after}
        print(f"  -> {'捕获' if killed else '存活'}；还原 MD5 {'一致' if h_before == h_after else '不一致'}"
              f"（{h_after[:12]}）")

    report = render(targets, started)
    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"[mutation] 报告 → {out}")

    survivors = [m.mid for m in targets if not m.result.get("killed")]
    bad_restore = [m.mid for m in targets if not m.result.get("restored_ok")]
    print(f"[mutation] 注入 {len(targets)}，捕获 {len(targets) - len(survivors)}，存活 {survivors or '无'}，"
          f"还原异常 {bad_restore or '无'}")
    return 0 if not survivors and not bad_restore else 1


if __name__ == "__main__":
    raise SystemExit(main())
