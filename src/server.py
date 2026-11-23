"""HTTP 服务：把 Agent 暴露成 4 个只读/写入端点，并保证**优雅停机**。

启动：`python -m src.server --host 127.0.0.1 --port 8080`（编排器负责起停，见 `ops/cli.py`）

端点契约（输入 → 输出）：
  GET  /health              → 200 {status, components{shop_db,audit_db,intents}, kb, provider, ...}
  GET  /metrics             → 200 {counters, gate_actions, tool_statuses, kb, uptime_s}
  POST /v1/chat             → 入参 {text(必填,1-500字), session_id?, client_request_id?}
                             200 {request_key, reply, intent, confidence, gate_actions,
                                  decisions, handoff, citations, tool_calls, degraded, replayed, latency_ms}
                             400 非法 JSON / 413 过大 / 422 缺少 text / 500 内部错误（带 trace_id）
  GET  /v1/audit/<key>      → 200 审计反查 / 404 未知 key
  POST /admin/shutdown      → 需请求头 X-Ops-Token；202 后进程在 drain 完在途请求后退出

停机语义：/admin/shutdown（或 Ctrl+C）→ 停止接收新连接 → 等待在途请求结束（最多 --drain-seconds）
→ 关闭 Application（审计连接 commit 后关闭、线程池 shutdown）→ server_close 释放端口。
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.app import Application, PreflightError
from src.config import settings

MAX_BODY_BYTES = 64 * 1024


class ServiceDown(Exception):
    """服务正在停止时拒绝新请求（宁可返回 503，也不要半死状态接单）。"""


def _log(msg: str) -> None:
    print(f"[api] {msg}", flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "ecommerce-support-agent/0.1"
    protocol_version = "HTTP/1.1"

    # 由 serve() 注入
    app: Application = None            # type: ignore[assignment]
    token: str = ""
    state: dict = {}
    timeout = 10                        # 连接空闲上限，避免 keep-alive 长挂
    _body_consumed = False              # 请求体是否已被读走（决定还能不能 drain）

    # ------------------------------------------------------------------ #
    def log_message(self, fmt: str, *args) -> None:  # 静音默认 stderr 访问日志
        _log(f"{self.address_string()} {fmt % args}")

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
                _log(f"! 客户端提前断开（{type(exc).__name__}），响应未送达：{self.command} {self.path}")

    def _authorized(self) -> bool:
        return bool(self.token) and self.headers.get("X-Ops-Token", "") == self.token

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            raise ValueError("Content-Length 非法")
        if length > MAX_BODY_BYTES:
            raise ValueError(f"请求体超过 {MAX_BODY_BYTES} 字节上限")
        data = self.rfile.read(length)
        self._body_consumed = True
        return data

    def _drain(self) -> None:
        if getattr(self, "_body_consumed", False):
            return
        """回错误响应前先把请求体读干净。

        不排空就关连接，Windows 上会让客户端在写入时撞上已关闭的 socket
        （ConnectionAbortedError）——keep-alive 下这是间歇性 flake 的来源。
        """
        try:
            left = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            left = 0
        while left > 0:
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)

    def _fail(self, code: int, payload: dict) -> None:
        self._drain()
        self._json(code, payload)

    # ------------------------------------------------------------------ #
    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        if self.state.get("stopping"):
            return self._fail(503, {"error": "服务正在停止，不再接收新请求", "retry_after_s": 2})
        if path == "/":
            return self._json(200, {"service": "ecommerce-support-agent", "endpoints": [
                "/health", "/metrics", "POST /v1/chat", "/v1/audit/<request_key>", "POST /admin/shutdown"]})
        if path == "/health":
            return self._json(200, self.app.health())
        if path == "/metrics":
            return self._json(200, self.app.metrics())
        if path.startswith("/v1/audit/"):
            key = path.split("/v1/audit/", 1)[1].strip("/")
            trace = self.app.agent.audit.trace(key)
            return self._json(200 if trace.get("found") else 404, trace)
        return self._json(404, {"error": f"未知路径 {path}"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/admin/shutdown":
            if not self._authorized():
                return self._fail(401, {"error": "缺少或错误的 X-Ops-Token"})
            self._json(202, {"status": "shutting_down", "drain_seconds": self.state.get("drain_seconds", 5)})
            threading.Thread(target=self.state["request_shutdown"], daemon=True).start()
            return
        if self.state.get("stopping"):
            return self._fail(503, {"error": "服务正在停止，不再接收新请求", "retry_after_s": 2})
        if path != "/v1/chat":
            return self._fail(404, {"error": f"未知路径 {path}"})

        try:
            raw = self._read_body()
        except ValueError as exc:
            return self._fail(413 if "上限" in str(exc) else 400, {"error": str(exc)})
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return self._fail(400, {"error": f"请求体不是合法 JSON：{exc}"})

        text = (payload.get("text") or "").strip()
        if not text:
            return self._fail(422, {"error": "text 必填且不能为空"})
        if len(text) > 500:
            return self._fail(422, {"error": "text 超过 500 字上限"})
        session_id = payload.get("session_id")
        if session_id is not None and (not isinstance(session_id, str) or len(session_id) > 64):
            return self._fail(422, {"error": "session_id 需为不超过 64 字符的字符串"})

        try:
            with self.state["inflight"]:
                resp = self.app.handle(text, session_id=session_id,
                                       client_request_id=payload.get("client_request_id"))
        except Exception as exc:
            trace_id = uuid.uuid4().hex[:8]
            _log(f"!! 处理失败 trace_id={trace_id}: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            with self.app._lock:
                self.app.counters["errors"] += 1
            return self._fail(500, {"error": f"内部错误（trace_id={trace_id}，详见 logs/api.log）",
                                     "trace_id": trace_id})
        return self._json(200, _serialize(resp))


class _Inflight:
    """在途请求计数：停机要等它归零，避免正在写的审计半途被打断。"""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def __enter__(self):
        with self._lock:
            self._n += 1
        return self

    def __exit__(self, *exc):
        with self._cond:
            self._n -= 1
            self._cond.notify_all()
        return False

    def wait_zero(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        with self._cond:
            while self._n:
                left = deadline - time.time()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True


def _serialize(resp) -> dict:
    return {
        "request_key": resp.request_key, "session_id": resp.session_id, "turn_id": resp.turn_id,
        "reply": resp.reply,
        "intent": {"code": resp.intent.code, "l1": resp.intent.l1, "score": resp.intent.score,
                   "matched": resp.intent.matched, "high_risk": resp.intent.is_high_risk},
        "confidence": resp.confidence, "must_handoff": resp.must_handoff,
        "gate_actions": [a.value for a in resp.gate_actions],
        "decisions": [{"gate": d.gate, "action": d.action.value, "stage": d.stage, "reason": d.reason}
                      for d in resp.decisions],
        "handoff": {"available": resp.handoff.available, "urgent": resp.handoff.urgent,
                    "auto_transferred": resp.handoff.auto_transferred, "channel": resp.handoff.channel,
                    "prompt": resp.handoff.prompt, "ticket_ref": resp.handoff.ticket_ref},
        "citations": resp.citations,
        "tool_calls": [{"tool": t.tool, "status": t.status.value, "elapsed_ms": t.elapsed_ms,
                        "result_ref": t.result_ref, "error": t.error} for t in resp.tool_calls],
        "degraded": resp.degraded, "replayed": resp.replayed, "answerable": resp.answerable,
        "latency_ms": resp.latency_ms, "token_estimate": resp.token_estimate,
    }


def serve(host: str, port: int, drain_seconds: int = 5, app: Application | None = None) -> int:
    """主线程负责收尾：任何线程都只在「设标志」时动，收尾动作（drain→关监听→关审计）只在主线程做一次。

    早先的写法让后台线程做收尾并打印日志，进程解释器已进入 finalize，就会撞上
    `Fatal Python error: could not acquire lock for <stdout> ... due to daemon threads`
    ——脏退出。现在的结构：请求/信号线程只触发 shutdown，主线程收尾并打印，最后 flush。
    """
    application = app or Application.build()
    token = os.environ.get("OPS_SHUTDOWN_TOKEN") or uuid.uuid4().hex
    os.environ["OPS_SHUTDOWN_TOKEN"] = token
    run_dir = Path(os.environ.get("OPS_RUN_DIR") or settings().path("run"))
    token_file = run_dir / "api.token"
    try:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(token, encoding="utf-8")
    except OSError as exc:
        _log(f"! 停机令牌文件写入失败（仍可用环境变量）：{exc}")

    inflight = _Inflight()
    try:
        httpd = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        _log(f"!! 无法监听 {host}:{port}：{exc}（端口可能已被占用；先跑 python -m ops.cli check 看占用方）")
        application.close()
        return 1
    httpd.daemon_threads = True
    state = {"stopping": False, "inflight": inflight, "drain_seconds": drain_seconds}
    stop_flag = threading.Event()

    def request_stop(reason: str) -> None:
        """只设标志 + 让 serve_forever 返回；不做任何收尾动作，也不打印（避免与主线程抢 stdout）。"""
        if state["stopping"]:
            return
        state["stopping"] = True
        _log(f"收到停机请求（{reason}）：停止接收新请求，等待在途请求最多 {drain_seconds}s")
        stop_flag.set()

    def trigger_loop() -> None:
        stop_flag.wait()
        httpd.shutdown()          # 让主线程的 serve_forever 返回

    state["request_shutdown"] = lambda: request_stop("HTTP /admin/shutdown")
    Handler.app, Handler.token, Handler.state = application, token, state

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, lambda *_: request_stop(f"signal {sig.name}"))
            except (ValueError, OSError):
                pass

    threading.Thread(target=trigger_loop, name="api-trigger", daemon=True).start()
    exit_code = 0
    try:
        _log(f"监听 http://{host}:{port}｜provider={getattr(application.agent.provider, 'name', '?')}"
             f"｜检索={application.agent.kb.backend}｜规则版本={application.agent.rules.version}")
        print(f"[api] READY port={port}", flush=True)
        httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        request_stop("Ctrl+C")
    except Exception as exc:  # 任何未预料异常都要走同一条收尾路径，不能裸退
        _log(f"!! 服务循环异常：{type(exc).__name__}: {exc}")
        traceback.print_exc()
        exit_code = 1
    finally:
        # —— 收尾四步，顺序不能换：等在途 → 关监听socket → 关应用资源 → 清临时文件 ——
        if not inflight.wait_zero(drain_seconds):
            _log(f"! 仍有在途请求未在 {drain_seconds}s 内结束，继续关闭"
                 f"（尚未 commit 的那一轮不会落库，已落库的审计行不受影响）")
        httpd.server_close()
        application.close()
        try:
            token_file.unlink()
        except OSError:
            pass
        _log("收尾完成：端口已释放、审计连接已关闭、临时文件已清理")
        sys.stdout.flush()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="电商客服 Agent HTTP 服务")
    s = settings()
    ap.add_argument("--host", default=os.environ.get("APP_HOST") or s.app_host)
    ap.add_argument("--port", type=int, default=int(os.environ.get("APP_PORT") or s.app_port))
    ap.add_argument("--drain-seconds", type=int, default=int(os.environ.get("DRAIN_SECONDS", "5")))
    args = ap.parse_args(argv)
    try:
        return serve(args.host, args.port, args.drain_seconds)
    except PreflightError as exc:
        _log(f"启动失败（配置/数据问题）：{exc}")
        _log("建议：先跑 python -m ops.cli check 看具体缺哪一项")
        return 2


if __name__ == "__main__":
    sys.exit(main())
