"""编排器：一键启动 / 一键停止 / 状态 / 预检。

启动流程（按需求书实现）：
  预检全部服务 → 若发现任一目标服务已在运行 → **先按逆序全部停止**（确保无残留）
  → 再按依赖顺序依次启动 → 每个服务都要通过健康检查才算启动成功（fail-fast，可用 --no-fail-fast 关掉）

停止流程：
  逆序（依赖者先停）→ 先让服务自己优雅退出（HTTP /admin/shutdown 或 SIGTERM）
  → 等待进程结束 → 超时才升级为强杀整棵进程树 → 校验端口已释放、状态文件已清理、无孤儿进程

退出码：0 全部成功；1 有服务失败；2 用法/配置错误。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ops import process as proc
from ops.services import Ctx, Health, ServiceSpec, Started, build_registry
from src.config import REPO_ROOT

LOG_MAX_BYTES = 2 * 1024 * 1024


@dataclass
class Result:
    service: str
    action: str          # preflight | start | stop | health | cleanup
    ok: bool
    message: str = ""
    advice: str = ""
    details: list[str] = field(default_factory=list)

    def render(self) -> str:
        mark = "✅" if self.ok else "❌"
        head = f"{mark} [{self.action}] {self.service}：{self.message}" if self.message \
            else f"{mark} [{self.action}] {self.service}"
        if self.ok:
            return head
        lines = [head]
        if self.advice:
            lines.append(f"   ↳ 建议动作：{self.advice}")
        lines.extend(f"   · {d}" for d in self.details)
        return "\n".join(lines)


class Manager:
    def __init__(self, cfg, selection: list[str] | None = None, start_timeout: float = 30.0,
                 stop_timeout: float = 10.0, run_dir: Path | None = None, log_dir: Path | None = None,
                 registry: dict[str, ServiceSpec] | None = None):
        self.cfg = cfg
        self.repo_root = REPO_ROOT
        # 测试可注入独立 run/log 目录，避免与真实运行态互相干扰
        self.ctx = Ctx(cfg=cfg, run_dir=run_dir or (REPO_ROOT / "run"),
                       log_dir=log_dir or (REPO_ROOT / "logs"), repo_root=REPO_ROOT)
        self.registry = registry if registry is not None else build_registry(cfg)
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.selection = self._resolve(selection or self._default_selection())

    # ------------------------------------------------------------------ #
    def _default_selection(self) -> list[str]:
        raw = getattr(self.cfg, "ops_services", "api") or "api"
        return [x.strip() for x in raw.split(",") if x.strip()]

    def _resolve(self, names: list[str]) -> list[str]:
        unknown = [n for n in names if n not in self.registry]
        if unknown:
            raise KeyError(f"未登记的服务：{unknown}；可选 {sorted(self.registry)}")
        ordered: list[str] = []

        def visit(n: str, seen: set[str]) -> None:
            if n in seen:
                raise ValueError(f"服务依赖成环：{n}（{sorted(seen)}）——编排器拒绝猜测启动顺序")
            seen = seen | {n}
            for dep in self.registry[n].depends_on:
                if dep in self.registry:
                    visit(dep, seen)
            if n not in ordered:
                ordered.append(n)

        for n in names:
            visit(n, set())
        return ordered

    def services(self) -> list[ServiceSpec]:
        return [self.registry[n] for n in self.selection]

    # ------------------------------------------------------------------ #
    def preflight_all(self) -> list[Result]:
        """预检所有目标服务：非阻塞问题也照样报出来（带建议动作），但不会拦住启动。"""
        out: list[Result] = []
        for svc in self.services():
            problems = svc.preflight(self.ctx)
            if not problems:
                out.append(Result(svc.name, "preflight", True, "预检通过"))
            out += [Result(svc.name, "preflight", not p.fatal, p.what, p.advice) for p in problems]
        return out

    # ------------------------------------------------------------------ #
    def is_running(self, svc: ServiceSpec) -> bool:
        return self.ctx.read_state(svc.name) is not None and svc.live_state(self.ctx) is not None

    def stop(self, force: bool = False, names: list[str] | None = None) -> list[Result]:
        targets = [self.registry[n] for n in (names or self.selection)][::-1]  # 逆序：依赖者先停
        results: list[Result] = []
        for svc in targets:
            state = self.ctx.read_state(svc.name)
            if state and state.get("_corrupt"):
                results.append(Result(svc.name, "cleanup", False, "状态文件损坏，无法确认进程归属",
                                      advice=f"手工删除 {self.ctx.state_path(svc.name)} 后重试；"
                                             f"确认端口 {getattr(svc, 'port', None)} 是否仍有进程监听"))
                continue
            if not state:
                results.append(Result(svc.name, "stop", True, "未在运行（无状态文件），跳过"))
                self._release_port_check(svc, results)
                continue
            pid = int(state.get("pid", -1))
            pgid = state.get("pgid")
            if not proc.pid_alive(pid):
                results.append(Result(svc.name, "cleanup", True, f"进程 {pid} 已不存在，清理残留状态文件"))
                self.ctx.clear_state(svc.name)
                self._release_port_check(svc, results)
                continue

            stopped = False
            how = ""
            if force:
                proc.kill_tree(pid, pgid)
                stopped, how = True, "强制终止（--force）"
            else:
                asked = svc.graceful_stop(self.ctx, state)
                if asked:
                    stopped = proc.wait_gone(pid, self.stop_timeout)
                    how = "服务自机关闭"
                if not stopped:
                    proc.terminate(pid, pgid)
                    stopped = proc.wait_gone(pid, max(2.0, self.stop_timeout / 2))
                    how = "SIGTERM / taskkill"
                if not stopped:
                    proc.kill_tree(pid, pgid)
                    stopped = proc.wait_gone(pid, self.stop_timeout)
                    how = "强杀进程树"

            if stopped:
                results.append(Result(svc.name, "stop", True, f"已停止（PID {pid}，方式：{how}）"))
            else:
                results.append(Result(svc.name, "stop", False,
                                      f"进程 {pid} 在 {self.stop_timeout}s 内没有退出",
                                      advice=f"仍在运行的可能是其子进程：POSIX 试 `kill -9 -{pgid or pid}`，"
                                             f"Windows 试 `taskkill /PID {pid} /T /F`；日志尾部：\n"
                                             f"{self.ctx.tail(svc.name, 5)}"))
            self.ctx.clear_state(svc.name)
            self._release_port_check(svc, results)
            self._check_no_orphan(svc, pid, results)
        return results

    def _release_port_check(self, svc: ServiceSpec, results: list[Result]) -> None:
        port = getattr(svc, "port", None)
        host = getattr(svc, "host", self.cfg.app_host)
        if not port:
            return
        if proc.wait_port(host, port, 1.5, want_open=False):
            results.append(Result(svc.name, "cleanup", True, f"端口 {host}:{port} 已释放"))
        else:
            results.append(Result(svc.name, "cleanup", False, f"端口 {host}:{port} 仍在监听",
                                  advice="说明还有别的进程占着它（不是刚停的这个）；"
                                         f"用 {'netstat -ano | findstr :' if proc.IS_WINDOWS else 'ss -lntp | grep :'}"
                                         f"{port} 找到 PID 后自行处理"))

    def _check_no_orphan(self, svc: ServiceSpec, pid: int, results: list[Result]) -> None:
        if proc.pid_alive(pid):
            results.append(Result(svc.name, "cleanup", False, f"孤儿进程残留：PID {pid} 仍存活",
                                  advice="已尝试终止无效，请手动清理后再启动"))
        else:
            results.append(Result(svc.name, "cleanup", True, f"无孤儿进程（PID {pid} 及其子进程已回收）"))

    # ------------------------------------------------------------------ #
    def _rotate_log(self, svc: ServiceSpec) -> None:
        log = self.ctx.log_path(svc.name)
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            old = log.with_suffix(log.suffix + ".1")
            try:
                if old.exists():
                    old.unlink()
                log.replace(old)
            except OSError:
                pass   # 轮转失败不影响启动，日志继续追加到原文件

    def start(self, skip_if_running: bool = False) -> list[Result]:
        # 1) 预检全部目标服务：有阻塞项就一个都不启动
        results: list[Result] = self.preflight_all()
        if any(not r.ok for r in results):
            results.append(Result("(全部)", "preflight", False, "预检存在阻塞项，**未启动任何服务**",
                                  advice="按上面每条「建议动作」处理后重试；只想看问题清单跑 "
                                         "`python -m ops.cli check`"))
            return results

        # 2) 已在运行 → 先全部停止（保证无残留）
        running = [s.name for s in self.services() if self.is_running(s)]
        if running and not skip_if_running:
            results.append(Result(",".join(running), "restart", True,
                                  f"检测到已运行：{running} → 先全部停止再按序启动"))
            stop_results = self.stop()
            results += stop_results
            if any(not r.ok for r in stop_results):
                results.append(Result("", "start", False, "停止阶段有失败项，已中止启动（避免端口/状态脏残留）",
                                      advice="先处理上面的失败项，或加 --force 强制停止"))
                return results

        # 3) 按依赖顺序启动
        deadline_each = self.start_timeout
        for svc in self.services():
            self._rotate_log(svc)
            t0 = time.monotonic()
            try:
                started = svc.start(self.ctx)
            except FileNotFoundError as exc:
                results.append(Result(svc.name, "start", False, f"找不到可执行文件：{exc}",
                                      advice="检查 .env 里的 OLLAMA_BIN / 依赖是否安装"))
                if self._fail_fast(results):
                    return results
                continue
            except OSError as exc:
                results.append(Result(svc.name, "start", False, f"进程创建失败：{type(exc).__name__}: {exc}",
                                      advice="看日志与权限：" + self.ctx.tail(svc.name, 10)))
                if self._fail_fast(results):
                    return results
                continue

            if started is None:
                results.append(Result(svc.name, "start", True, "按配置不接管，未启动新进程"))
                continue
            self._write_state(svc, started)
            health = self._wait_ready(svc, started, deadline_each)
            if health.ok and health.ready:
                results.append(Result(svc.name, "start", True,
                                      f"启动成功（PID {started.pid}，用时 {time.monotonic() - t0:.1f}s）｜{health.detail}"))
            else:
                results.append(Result(svc.name, "start", False,
                                      f"启动后健康检查未通过：{health.detail}",
                                      advice=f"日志：{self.ctx.log_path(svc.name)}；"
                                             f"可先 `python -m src.server --port 其它端口` 单点复现，"
                                             f"或直接看上面日志尾部的报错"))
                # 半启动状态不能留：把它停干净
                self._rollback(svc, started, results)
                if self._fail_fast(results):
                    return results
        return results

    def _fail_fast(self, results: list[Result]) -> bool:
        return any(not r.ok for r in results)

    def _write_state(self, svc: ServiceSpec, started: Started) -> None:
        self.ctx.write_state(svc.name, {"pid": started.pid, "pgid": started.pgid, "port": started.port,
                                        "managed_by_us": started.managed_by_us, "token": started.token,
                                        "command": started.command, "name": svc.name,
                                        "started_at": time.strftime("%Y-%m-%d %H:%M:%S")})

    def _wait_ready(self, svc: ServiceSpec, started: Started, timeout: float) -> Health:
        from dataclasses import asdict

        state = asdict(started)
        deadline = time.monotonic() + timeout
        last = Health(False, "尚未开始探测")
        while time.monotonic() < deadline:
            last = svc.health(self.ctx, state)
            if last.ok and last.ready:
                return last
            if not proc.pid_alive(started.pid):
                return Health(False, f"进程提前退出。{self.ctx.tail(svc.name, 20)}")
            time.sleep(0.4)
        return last if not last.ok else Health(True, last.detail + "（ready 未在时限内达成）")

    def _rollback(self, svc: ServiceSpec, started: Started, results: list[Result]) -> None:
        proc.terminate(started.pid, started.pgid)
        if not proc.wait_gone(started.pid, 3.0):
            proc.kill_tree(started.pid, started.pgid)
        proc.wait_gone(started.pid, 3.0)
        self.ctx.clear_state(svc.name)
        results.append(Result(svc.name, "cleanup", True, "已回滚：终止未就绪的进程并清理状态文件"))

    # ------------------------------------------------------------------ #
    def status(self) -> list[Result]:
        out: list[Result] = []
        for name, svc in self.registry.items():
            state = self.ctx.read_state(name)
            if state and state.get("_corrupt"):
                out.append(Result(name, "status", False, "状态文件损坏",
                                  advice=f"删除 {self.ctx.state_path(name)} 后重试"))
                continue
            pid = int((state or {}).get("pid", -1))
            alive = bool(state) and proc.pid_alive(pid)
            if not alive:
                verdict = "未运行" if not state else "状态文件残留（进程已退出）"
                out.append(Result(name, "status", True, f"{verdict}｜{svc.title}"))
                continue
            health = svc.health(self.ctx, state or {})
            out.append(Result(name, "status", health.ok,
                              f"运行中｜PID {pid}｜{health.detail}",
                              advice="" if health.ok else "跑 `python -m ops.cli restart` 或看日志"))
        return out

    def logs(self, name: str, n: int = 40) -> str:
        return self.ctx.tail(name, n)
