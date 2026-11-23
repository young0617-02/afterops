"""服务定义层：每个服务声明「怎么预检、怎么起、怎么判健康、怎么优雅停」。

抽象层次（新增服务只需实现 ServiceSpec 的四个方法，编排器不用改）：

    preflight(ctx) -> [Problem]        起之前能不能起；每个问题必须带「建议动作」
    start(ctx) -> Started | None       None = 不接管（例如外部服务已在运行且未开接管开关）
    health(ctx, state) -> Health       起来没有；ready=True 才允许接流量
    graceful_stop(ctx, state) -> bool  让服务自己退出；返回 False 表示需要编排器强杀

状态文件 `run/<name>.json` 记录 pid / 进程组 / 端口 / 是否我们启动的，
`run/<name>.token` 记录停机令牌 —— 这样「一键停止」在任何 shell 会话里都能找到并干净收尾。
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ops import process as proc


@dataclass
class Problem:
    """一条预检问题：必须同时给出建议动作，否则只是把失败原样吐给用户。"""

    what: str
    advice: str
    fatal: bool = True

    def render(self) -> str:
        level = "阻塞" if self.fatal else "提醒"
        return f"[{level}] {self.what}\n         建议动作：{self.advice}"


@dataclass
class Started:
    pid: int
    pgid: int | None = None
    port: int | None = None
    token: str = ""
    managed_by_us: bool = True
    command: list[str] = field(default_factory=list)


@dataclass
class Health:
    ok: bool
    detail: str = ""
    ready: bool = False


@dataclass
class Ctx:
    cfg: object
    run_dir: Path
    log_dir: Path
    repo_root: Path

    def log_path(self, name: str) -> Path:
        return self.log_dir / f"{name}.log"

    def state_path(self, name: str) -> Path:
        return self.run_dir / f"{name}.json"

    def token_path(self, name: str) -> Path:
        return self.run_dir / f"{name}.token"

    def read_state(self, name: str) -> dict | None:
        p = self.state_path(name)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return {"_corrupt": str(exc)}

    def write_state(self, name: str, data: dict) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path(name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def clear_state(self, name: str) -> None:
        for p in (self.state_path(name), self.token_path(name)):
            try:
                p.unlink()
            except OSError:
                pass

    def tail(self, name: str, n: int = 20) -> str:
        p = self.log_path(name)
        if not p.exists():
            return f"(无日志文件：{p})"
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"(日志不可读：{exc})"
        return "\n".join(lines[-n:]) if lines else "(日志为空)"


def http_json(url: str, method: str = "GET", headers: dict | None = None,
              body: dict | None = None, timeout: float = 2.0):
    """短超时 HTTP 调用；网络层失败返回 None（区别于「服务活着但返回错误」）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:
            raw = ""
        return e.code, raw[:300]
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    try:
        return 200, json.loads(raw)
    except json.JSONDecodeError:
        return 200, raw[:300]


class ServiceSpec:
    name: str = "service"
    title: str = "服务"
    depends_on: tuple[str, ...] = ()
    optional: bool = False

    def preflight(self, ctx: Ctx) -> list[Problem]:
        return []

    def start(self, ctx: Ctx) -> Started | None:
        raise NotImplementedError

    def health(self, ctx: Ctx, state: dict) -> Health:
        raise NotImplementedError

    def graceful_stop(self, ctx: Ctx, state: dict) -> bool:
        return False

    # ------------------------------------------------------------------ #
    def live_state(self, ctx: Ctx) -> dict | None:
        """返回「仍在运行」的服务状态；pid 已死或状态文件损坏都视为没在跑。"""
        st = ctx.read_state(self.name)
        if not st or st.get("_corrupt"):
            return None
        if proc.pid_alive(int(st.get("pid", -1))):
            return st
        return None


class ApiService(ServiceSpec):
    """本仓库自带的 HTTP 服务（`python -m src.server`）。"""

    name = "api"
    title = "Agent HTTP 服务"

    def __init__(self, host: str, port: int, drain_seconds: int):
        self.host, self.port, self.drain_seconds = host, port, drain_seconds

    def url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}{path}"

    # ------------------------------------------------------------------ #
    def preflight(self, ctx: Ctx) -> list[Problem]:
        cfg, problems = ctx.cfg, []
        if self.live_state(ctx):
            problems.append(Problem(
                what=f"{self.name} 已在运行（端口 {self.port}）",
                advice="一键启动会先停掉它再按序重启；只想补起别的服务用 `start --only <name>`",
                fatal=False))
        elif not proc.port_free(self.host, self.port):
            finder = "netstat -ano | findstr :%d" % self.port if proc.IS_WINDOWS else "ss -lntp | grep :%d" % self.port
            problems.append(Problem(
                what=f"端口 {self.host}:{self.port} 被其它进程占用（不是本编排器登记的 {self.name}）",
                advice=f"改端口（.env 里 APP_PORT=其它值），或用 `{finder}` 找到占用方自行决定是否结束；"
                       f"编排器不会去杀不属于自己的进程"))
        problems += self._provider_problems(cfg)
        problems += self._writable_problems(ctx)
        return problems

    def _provider_problems(self, cfg) -> list[Problem]:
        out: list[Problem] = []
        provider = cfg.llm_provider
        if provider == "ollama":
            probe = http_json(f"{cfg.ollama_base_url}/api/tags", timeout=1.5)
            if probe is None:
                out.append(Problem(
                    what=f"LLM_PROVIDER=ollama，但 {cfg.ollama_base_url} 无响应",
                    advice="先起 ollama serve（或把 ollama 加进 OPS_SERVICES 并设 OLLAMA_TAKEOVER=1）；"
                           "只想跑离线确定性链路就把 LLM_PROVIDER 改回 template"))
            elif not cfg.ollama_model:
                out.append(Problem(what="选了 ollama 但没填 OLLAMA_MODEL",
                                   advice="在 .env 里填模型名，例如 qwen2.5:7b-instruct"))
        elif provider == "openai":
            if not (cfg.openai_api_key and cfg.openai_model and cfg.openai_base_url):
                out.append(Problem(
                    what="LLM_PROVIDER=openai，但 OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL 未配齐",
                    advice="补齐三项，或改用 template / ollama（默认 template 可零密钥跑通）"))
        elif provider != "template":
            out.append(Problem(what=f"未知的 LLM_PROVIDER={provider}",
                               advice="只能是 template / ollama / openai"))
        return out

    def _writable_problems(self, ctx: Ctx) -> list[Problem]:
        out: list[Problem] = []
        shop = Path(ctx.cfg.path(ctx.cfg.shop_db_path))
        if not shop.exists():
            out.append(Problem(what=f"合成店铺库不存在：{shop}",
                               advice="跑 `python -m src.shop.seed` 建库（启动流程也会自动补建）"))
        for d in (ctx.run_dir, ctx.log_dir, shop.parent):
            probe_file = d / ".write_probe"
            try:
                d.mkdir(parents=True, exist_ok=True)
                probe_file.write_text("ok", encoding="utf-8")
                probe_file.unlink()
            except OSError as exc:
                out.append(Problem(what=f"目录不可写：{d}（{type(exc).__name__}）",
                                   advice="检查磁盘空间与权限；run/ 与 logs/ 不可写就无法落 pid 与日志"))
        return out

    # ------------------------------------------------------------------ #
    def start(self, ctx: Ctx) -> Started:
        token = secrets.token_hex(16)
        log = ctx.log_path(self.name)
        env = os.environ.copy()
        env.update({"APP_HOST": self.host, "APP_PORT": str(self.port),
                    "DRAIN_SECONDS": str(self.drain_seconds), "OPS_SHUTDOWN_TOKEN": token,
                    # 子进程把停机令牌写到编排器指定的 run 目录，否则测试注入的独立目录会漏文件
                    "OPS_RUN_DIR": str(ctx.run_dir)})
        command = proc.spawn([sys.executable, "-m", "src.server",
                              "--host", self.host, "--port", str(self.port),
                              "--drain-seconds", str(self.drain_seconds)],
                             log, env=env, cwd=ctx.repo_root)
        ctx.token_path(self.name).parent.mkdir(parents=True, exist_ok=True)
        ctx.token_path(self.name).write_text(token, encoding="utf-8")
        return Started(pid=command.pid, pgid=command.pgid, port=self.port, token=token,
                       managed_by_us=True, command=command.command)

    def health(self, ctx: Ctx, state: dict) -> Health:
        if not proc.pid_alive(int(state.get("pid", -1))):
            return Health(False, f"进程已退出（PID {state.get('pid')}）。日志尾部：\n{ctx.tail(self.name, 15)}")
        if not proc.wait_port(self.host, self.port, 0.2, want_open=True):
            return Health(False, f"端口 {self.port} 未监听。日志尾部：\n{ctx.tail(self.name, 15)}")
        probe = http_json(self.url("/health"), timeout=2.5)
        if probe is None:
            return Health(False, "进程在、端口在，但 /health 无响应（可能仍在装配依赖）")
        code, payload = probe
        if code != 200 or not isinstance(payload, dict):
            return Health(False, f"/health 返回 {code}：{str(payload)[:200]}")
        if payload.get("status") != "ok":
            return Health(False, f"健康检查不通过：{payload.get('reason') or payload.get('components')}")
        comps = payload.get("components", {})
        return Health(True, f"provider={payload.get('provider')}｜检索={comps.get('intents', {}).get('l2', '?')} 个二级意图"
                            f"｜规则版本={payload.get('ruleset_version')}", ready=True)

    def graceful_stop(self, ctx: Ctx, state: dict) -> bool:
        token = ""
        if ctx.token_path(self.name).exists():
            token = ctx.token_path(self.name).read_text(encoding="utf-8").strip()
        token = token or state.get("token", "")
        probe = http_json(self.url("/admin/shutdown"), method="POST",
                          headers={"X-Ops-Token": token}, body={}, timeout=3.0)
        return bool(probe and probe[0] in (200, 202))


class OllamaService(ServiceSpec):
    """本机 Ollama：默认**只探测不接管**（动用户自己的服务进程要显式开开关）。"""

    name = "ollama"
    title = "本机 Ollama（外部进程）"
    optional = True

    def __init__(self, base_url: str, model: str, takeover: bool, binary: str):
        self.base_url, self.model = base_url.rstrip("/"), model
        self.takeover, self.binary = takeover, binary

    def probe(self):
        return http_json(f"{self.base_url}/api/tags", timeout=1.5)

    def reachable(self) -> bool:
        return self.probe() is not None

    def preflight(self, ctx: Ctx) -> list[Problem]:
        if ctx.cfg.llm_provider != "ollama" and not self.takeover:
            return [Problem(what="Ollama 未参与本次链路（LLM_PROVIDER 不是 ollama 且未开接管）",
                            advice="需要它就把 LLM_PROVIDER=ollama 并填 OLLAMA_MODEL", fatal=False)]
        if self.reachable():
            return [Problem(what=f"Ollama 已在 {self.base_url} 运行，编排器不重复起、也不接管它的停止",
                            advice="无需动作；要让编排器负责停止，先手动停掉现有进程并设 OLLAMA_TAKEOVER=1",
                            fatal=False)]
        if not self.takeover:
            return [Problem(what="Ollama 未运行且未开接管（OLLAMA_TAKEOVER=0）",
                            advice="要接管：设 OLLAMA_TAKEOVER=1 并确认 OLLAMA_BIN 可用；"
                                   "不接管：把 LLM_PROVIDER 改回 template，否则 api 预检会因连不上而阻塞",
                            fatal=False)]
        if shutil.which(self.binary) is None and not Path(self.binary).exists():
            return [Problem(what=f"要接管 Ollama，但找不到可执行文件 `{self.binary}`",
                            advice="安装 Ollama，或在 .env 把 OLLAMA_BIN 指到绝对路径")]
        return []

    def start(self, ctx: Ctx) -> Started | None:
        if self.reachable() or not self.takeover:
            return None
        cmd = proc.spawn([self.binary, "serve"], ctx.log_path(self.name), cwd=ctx.repo_root)
        return Started(pid=cmd.pid, pgid=cmd.pgid, managed_by_us=True, command=cmd.command)

    def health(self, ctx: Ctx, state: dict) -> Health:
        result = self.probe()
        if result is None:
            detail = f"{self.base_url} 无响应"
            if state.get("pid") and not proc.pid_alive(int(state["pid"])):
                detail += f"；日志尾部：\n{ctx.tail(self.name, 10)}"
            return Health(False, detail)
        code, payload = result
        if code != 200:
            return Health(False, f"/api/tags 返回 {code}")
        models = [m.get("name", "") for m in (payload.get("models") or [])] if isinstance(payload, dict) else []
        if self.model and models and not any(self.model in (m or "") for m in models):
            return Health(True, f"服务在线，但未找到配置的模型 {self.model}（现有：{models[:5]}）")
        return Health(True, f"服务在线{'｜' + ', '.join(models[:3]) if models else ''}", ready=True)

    def graceful_stop(self, ctx: Ctx, state: dict) -> bool:
        if not state.get("managed_by_us"):
            return False
        proc.terminate(int(state["pid"]), state.get("pgid"))
        return True


def build_registry(cfg) -> dict[str, ServiceSpec]:
    """服务清单来自配置，不在代码里写死端口与服务名。"""
    specs: list[ServiceSpec] = [
        ApiService(cfg.app_host, cfg.app_port, cfg.drain_seconds),
        OllamaService(cfg.ollama_base_url, cfg.ollama_model, cfg.ollama_takeover, cfg.ollama_bin),
    ]
    return {s.name: s for s in specs}
