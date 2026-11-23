"""服务编排与 HTTP 服务的真机测试：真的起进程、真的占端口、真的停机，并验证无残留。

这些用例故意跑真实子进程（不是 mock）：「停止后无孤儿进程」「端口是否释放」「优雅停机是否丢日志」
这类问题只有真起真停才测得出来。每个 fixture 都在 finally 里兜底强停。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from ops import process as proc
from ops.manager import Manager
from ops.services import Health, Problem, ServiceSpec, Started, http_json
from src.config import REPO_ROOT, load_settings
from src.server import _Inflight


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_manager(tmp_path, port, **kw) -> Manager:
    cfg = replace(load_settings(), app_host="127.0.0.1", app_port=port, llm_provider="template")
    return Manager(cfg, selection=kw.pop("selection", ["api"]), run_dir=Path(tmp_path) / "run",
                   log_dir=Path(tmp_path) / "logs", start_timeout=kw.pop("start_timeout", 40.0),
                   stop_timeout=kw.pop("stop_timeout", 10.0), **kw)


# --------------------------------------------------------------------------- #
# 进程层
# --------------------------------------------------------------------------- #
def test_port_free_and_port_open_agree():
    port = free_port()
    assert proc.port_free("127.0.0.1", port) is True
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", port))
        holder.listen(1)
        assert proc.port_free("127.0.0.1", port) is False
        assert proc.port_open("127.0.0.1", port) is True


def test_pid_alive_never_kills_on_windows_semantics():
    """pid_alive 必须是只读的：绝不能变成「探活顺手把进程杀掉」。"""
    assert proc.pid_alive(0) is False
    assert proc.pid_alive(2 ** 30) is False
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert proc.pid_alive(p.pid) is True
    finally:
        p.terminate()
        p.wait(timeout=10)
    assert proc.pid_alive(p.pid) is False


def test_terminate_and_kill_tree_reclaim_child():
    cmd = proc.spawn([sys.executable, "-c", "import time; time.sleep(120)"],
                     REPO_ROOT / "logs" / "_probe.log")
    try:
        assert proc.pid_alive(cmd.pid)
        proc.kill_tree(cmd.pid, cmd.pgid)
        assert proc.wait_gone(cmd.pid, 10.0), "强杀后进程未回收 → 会留下孤儿"
    finally:
        proc.kill_tree(cmd.pid, cmd.pgid)
        (REPO_ROOT / "logs" / "_probe.log").unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 预检
# --------------------------------------------------------------------------- #
def test_preflight_blocks_when_port_taken_by_other_process(tmp_path):
    port = free_port()
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:
        m = make_manager(tmp_path, port)
        results = m.start()
        assert any(r.action == "preflight" and not r.ok for r in results), "端口被占时不该照常启动"
        assert any("端口" in r.message for r in results)
        assert not (Path(tmp_path) / "run" / "api.json").exists(), "预检失败却留下了状态文件"
        advice = " ".join(r.advice for r in results)
        assert "建议动作" not in advice or ("APP_PORT" in advice or "占用" in advice)
    finally:
        holder.close()


def test_preflight_rejects_bad_provider_config(tmp_path):
    cfg = replace(load_settings(), app_host="127.0.0.1", app_port=free_port(),
                  llm_provider="openai", openai_api_key="", openai_model="")
    m = Manager(cfg, selection=["api"], run_dir=Path(tmp_path) / "run", log_dir=Path(tmp_path) / "logs")
    problems = m.preflight_all()
    bad = [r for r in problems if not r.ok]
    assert bad and "OPENAI" in bad[0].message
    assert "template" in bad[0].advice, "错误提示要给得出可执行的退路"


def test_problem_render_contains_advice_action():
    text = Problem(what="端口被占", advice="改 APP_PORT").render()
    assert "端口被占" in text and "建议动作：改 APP_PORT" in text


# --------------------------------------------------------------------------- #
# 启动 / 停止 全链路（真子进程）
# --------------------------------------------------------------------------- #
def test_start_then_graceful_stop_leaves_nothing_behind(tmp_path):
    port = free_port()
    m = make_manager(tmp_path, port)
    started_pid = None
    try:
        results = m.start()
        assert all(r.ok for r in results), [r.render() for r in results]
        state = json.loads((Path(tmp_path) / "run" / "api.json").read_text(encoding="utf-8"))
        started_pid = state["pid"]
        assert proc.pid_alive(started_pid)
        assert proc.port_open("127.0.0.1", port, timeout=5)
        assert (Path(tmp_path) / "logs" / "api.log").read_text(encoding="utf-8").count("READY") == 1

        stop = m.stop()
        assert all(r.ok for r in stop), [r.render() for r in stop]
        assert any(r.action == "cleanup" and "已释放" in r.message for r in stop),             "停止流程没有真正校验端口释放（只在嘴上说停了）"
        assert any(r.action == "cleanup" and "无孤儿进程" in r.message for r in stop),             "停止流程没有做孤儿进程校验"
        assert not proc.pid_alive(started_pid), "停止后进程仍在 = 孤儿进程"
        assert not proc.port_open("127.0.0.1", port, timeout=2), "停止后端口仍被占用"
        assert not (Path(tmp_path) / "run" / "api.json").exists(), "状态文件未清理"
        assert not (Path(tmp_path) / "run" / "api.token").exists(), "停机令牌文件未清理"
        log = (Path(tmp_path) / "logs" / "api.log").read_text(encoding="utf-8")
        assert "收尾完成" in log and "Fatal Python error" not in log, "停机退出路径不干净"
        started_pid = None
    finally:
        if started_pid:
            proc.kill_tree(started_pid)


def test_start_twice_restarts_instead_of_double_binding(tmp_path):
    port = free_port()
    m = make_manager(tmp_path, port)
    first = m.start()
    assert all(r.ok for r in first)
    pid1 = json.loads((Path(tmp_path) / "run" / "api.json").read_text(encoding="utf-8"))["pid"]
    second = m.start()
    try:
        assert all(r.ok for r in second), [r.render() for r in second]
        assert any("先全部停止" in r.message or "已在运行" in r.message for r in second)
        pid2 = json.loads((Path(tmp_path) / "run" / "api.json").read_text(encoding="utf-8"))["pid"]
        assert pid2 != pid1, "第二次启动没有真正接管，端口上是旧进程"
        assert not proc.pid_alive(pid1), "重启后旧进程残留"
    finally:
        m.stop()


def test_stop_when_process_already_dead_cleans_state(tmp_path):
    port = free_port()
    m = make_manager(tmp_path, port)
    run = Path(tmp_path) / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "api.json").write_text(json.dumps({"pid": 2 ** 30, "pgid": None, "port": port,
                                              "managed_by_us": True}), encoding="utf-8")
    results = m.stop()
    assert all(r.ok for r in results), [r.render() for r in results]
    assert any("已不存在" in r.message for r in results)
    assert not (run / "api.json").exists()


def test_force_stop_escalates_when_service_ignores_graceful_stop(tmp_path):
    """不配合优雅停的服务，编排器必须升级到强杀并确认无残留。"""

    class Stubborn(ServiceSpec):
        name = "stubborn"
        title = "测试用顽固服务"
        port = None

        def start(self, ctx):
            cmd = proc.spawn([sys.executable, "-c", "import time; time.sleep(300)"],
                             ctx.log_path(self.name), cwd=ctx.repo_root)
            return Started(pid=cmd.pid, pgid=cmd.pgid)

        def health(self, ctx, state):
            return Health(proc.pid_alive(int(state["pid"])), "进程在", ready=True)

        def graceful_stop(self, ctx, state):
            return False   # 故意不配合

    stubborn = Stubborn()
    m = make_manager(tmp_path, free_port(), registry={"stubborn": stubborn}, selection=["stubborn"])
    assert all(r.ok for r in m.start())
    pid = json.loads((Path(tmp_path) / "run" / "stubborn.json").read_text(encoding="utf-8"))["pid"]
    res = m.stop(force=True)
    assert all(r.ok for r in res), [r.render() for r in res]
    assert proc.wait_gone(pid, 8.0), "顽固进程没被回收"


def test_stop_order_is_reverse_of_dependencies(tmp_path):
    calls: list[str] = []

    class Fake(ServiceSpec):
        def __init__(self, name, deps=()):
            self.name, self.title, self.depends_on = name, name, tuple(deps)

        def start(self, ctx):
            calls.append(f"start:{self.name}")
            cmd = proc.spawn([sys.executable, "-c", "import time; time.sleep(60)"],
                             ctx.log_path(self.name), cwd=ctx.repo_root)
            return Started(pid=cmd.pid, pgid=cmd.pgid)

        def health(self, ctx, state):
            return Health(True, "ok", ready=True)

        def graceful_stop(self, ctx, state):
            calls.append(f"stop:{self.name}")
            proc.terminate(int(state["pid"]), state.get("pgid"))
            return True

    a, b = Fake("a"), Fake("b", deps=["a"])
    m = make_manager(tmp_path, free_port(), registry={"a": a, "b": b}, selection=["b"])
    assert all(r.ok for r in m.start())
    assert calls == ["start:a", "start:b"], f"依赖顺序错：{calls}"
    m.stop()
    assert calls[:4] == ["start:a", "start:b", "stop:b", "stop:a"], f"停止顺序不是逆依赖：{calls}"


def test_dependency_cycle_is_rejected_not_guessed(tmp_path):
    class Cyc(ServiceSpec):
        name = "cyc"
        depends_on = ("cyc",)

        def start(self, ctx):
            return None

        def health(self, ctx, state):
            return Health(True, ready=True)

    with pytest.raises(ValueError, match="依赖成环"):
        make_manager(tmp_path, free_port(), registry={"cyc": Cyc()}, selection=["cyc"])


def test_unknown_service_name_reports_actionable_error(tmp_path):
    with pytest.raises(KeyError, match="未登记的服务"):
        make_manager(tmp_path, free_port(), selection=["nope"])


# --------------------------------------------------------------------------- #
# HTTP 契约（复用同一个真服务，避免每次都起进程）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def live_server():
    port = free_port()
    tmp = Path(pytest.__file__).parent / "_live_ops"
    tmp.mkdir(exist_ok=True)
    cfg = replace(load_settings(), app_host="127.0.0.1", app_port=port, llm_provider="template")
    m = Manager(cfg, selection=["api"], run_dir=tmp / "run", log_dir=tmp / "logs")
    results = m.start()
    assert all(r.ok for r in results), [r.render() for r in results]
    try:
        yield f"http://127.0.0.1:{port}", m
    finally:
        stop = m.stop(force=True)
        assert all(r.ok for r in stop), [r.render() for r in stop]


def _post(url, body, headers=None):
    data = json.dumps(body).encode("utf-8") if not isinstance(body, (bytes, type(None))) else body
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def test_http_health_and_metrics(live_server):
    base, m = live_server
    code, body = http_json(f"{base}/health", timeout=5)
    assert code == 200
    h = json.loads(body) if isinstance(body, str) else body
    assert h["status"] == "ok" and h["provider"] == "template"
    assert h["components"]["shop_db"]["orders"] == 14
    assert h["components"]["intents"]["l2"] == 20
    code2, metrics = http_json(f"{base}/metrics", timeout=5)
    assert code2 == 200


def test_http_chat_returns_full_contract(live_server):
    base, _ = live_server
    code, raw = _post(f"{base}/v1/chat", {"text": "你能保证全额退款吗，订单 ORD-2026-000103",
                                          "session_id": "http-test"})
    assert code == 200
    payload = json.loads(raw)
    for key in ("request_key", "reply", "intent", "gate_actions", "decisions", "handoff",
                "citations", "tool_calls", "confidence", "must_handoff", "latency_ms"):
        assert key in payload, f"响应缺少字段 {key}"
    assert "block_commit" in payload["gate_actions"] and "explain_only" in payload["gate_actions"]
    assert payload["handoff"]["available"] is True and "人工" in payload["reply"]
    for phrase in ("保证", "已为您", "全额退款没问题"):
        assert phrase not in payload["reply"]
    assert any(d["stage"] == "pre_generation" for d in payload["decisions"])

    code2, trace = http_json(f"{base}/v1/audit/{payload['request_key']}", timeout=5)
    assert code2 == 200
    t = json.loads(trace) if isinstance(trace, str) else trace
    assert t["found"] and t["ruleset_version"] and t["kb_versions"]


def test_http_validation_and_error_codes(live_server):
    base, _ = live_server
    assert _post(f"{base}/v1/chat", {"session_id": "x"})[0] == 422
    assert _post(f"{base}/v1/chat", {"text": " "})[0] == 422
    assert _post(f"{base}/v1/chat", {"text": "超长" * 400})[0] == 422
    assert _post(f"{base}/v1/chat", {"text": "订单", "session_id": "s" * 100})[0] == 422
    code, body = _post(f"{base}/v1/chat", b"{not json")
    assert code == 400 and "JSON" in body
    assert http_json(f"{base}/nope")[0] == 404
    assert http_json(f"{base}/v1/audit/" + "f" * 64)[0] == 404
    assert _post(f"{base}/admin/shutdown", {}, headers={"X-Ops-Token": "wrong"})[0] == 401


def test_http_concurrent_requests_are_serialised_correctly(live_server):
    base, m = live_server
    codes = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        code, raw = _post(f"{base}/v1/chat", {"text": f"订单 ORD-2026-0001{i:02d} 实付多少钱",
                                             "session_id": f"conc-{i}"})
        with lock:
            codes.append((code, raw))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert [c for c, _ in codes] == [200] * 8, "并发下有请求没返回 200"
    # 并发写最容易把审计哈希链写断，这里让服务自己校验一次
    code, body = http_json(f"{base}/health", timeout=5)
    payload = json.loads(body) if isinstance(body, str) else body
    chain = payload["components"]["audit_db"]
    assert chain["chain_ok"] is True, f"并发写之后哈希链断了：{chain}"
    assert chain["rows"] >= 8, f"审计行数少于成功请求数：{chain}"
    assert payload["counters"]["requests"] >= 8


# --------------------------------------------------------------------------- #
# 单元层
# --------------------------------------------------------------------------- #
def test_inflight_waits_until_zero():
    infl = _Inflight()
    with infl:
        assert infl.wait_zero(0.2) is False
    assert infl.wait_zero(0.2) is True


def test_log_rotation_keeps_latest_file_small(tmp_path, monkeypatch):
    import ops.manager as mgr

    monkeypatch.setattr(mgr, "LOG_MAX_BYTES", 200)
    m = make_manager(tmp_path, free_port())
    log = m.ctx.log_path("api")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 5000, encoding="utf-8")

    m._rotate_log(m.registry["api"])

    rotated = Path(str(log) + ".1")
    assert rotated.exists(), "轮转后的旧日志被丢了（排错时要不到现场）"
    assert len(rotated.read_text(encoding="utf-8")) == 5000
    # 当前日志被搬走 → 子进程重新创建，等于「本次运行从空日志开始」，不会无限增长
    assert not log.exists() or log.stat().st_size <= 200, "日志超上限却没被轮转"


def test_log_rotation_leaves_small_logs_alone(tmp_path, monkeypatch):
    import ops.manager as mgr

    monkeypatch.setattr(mgr, "LOG_MAX_BYTES", 10_000)
    m = make_manager(tmp_path, free_port())
    log = m.ctx.log_path("api")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("short", encoding="utf-8")
    m._rotate_log(m.registry["api"])
    assert log.read_text(encoding="utf-8") == "short"
    assert not Path(str(log) + ".1").exists(), "没超上限不该轮转"


def test_manager_status_reports_stale_pid(tmp_path):
    port = free_port()
    m = make_manager(tmp_path, port)
    run = Path(tmp_path) / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "api.json").write_text(json.dumps({"pid": 2 ** 30, "port": port}), encoding="utf-8")
    st = [r for r in m.status() if r.service == "api"]
    assert st and "残留" in st[0].message
