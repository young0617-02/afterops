"""跨平台进程控制：起、探活、优雅停、强杀整棵树、端口占用检测。

为什么单独一层：Windows 与 POSIX 在这三件事上差异最大，而差异写错正好会造出
「以为停了其实没停」的孤儿进程，或 `os.kill(pid, 0)` 在 Windows 上把目标进程终止掉。
"""
from __future__ import annotations

import ctypes
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

IS_WINDOWS = os.name == "nt"


@dataclass
class Spawned:
    pid: int
    log_path: Path
    command: list[str]
    started_at: float
    pgid: int | None = None


def port_free(host: str, port: int) -> bool:
    """能不能占这个端口。不设 SO_REUSEADDR：Windows 上它会让「已被 LISTEN 的端口」也能绑定，检测会失真。"""
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.bind((probe_host, port))
            return True
        except OSError:
            return False


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    probe_host = "127.0.0.1" if host in ("", "0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((probe_host, port)) == 0


def wait_port(host: str, port: int, timeout: float, *, want_open: bool = True,
              interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(host, port) == want_open:
            return True
        time.sleep(interval)
    return port_open(host, port) == want_open


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    # Windows 上 os.kill(pid, 0) 会 TerminateProcess，绝不能用来探活
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


def spawn(command: list[str], log_path: Path, *, env: dict | None = None,
          cwd: Path | None = None) -> Spawned:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logf = open(log_path, "ab", buffering=0)
    kwargs: dict = dict(stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                        cwd=str(cwd) if cwd else None, env=env or os.environ.copy())
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True          # 独立进程组，便于整组回收
    proc = subprocess.Popen(command, **kwargs)
    logf.close()                                    # 子进程已继承 fd，父进程关掉自己的副本
    pgid = None if IS_WINDOWS else os.getpgid(proc.pid)
    return Spawned(pid=proc.pid, log_path=log_path, command=command, started_at=time.time(), pgid=pgid)


def _signal_tree_posix(pid: int, pgid: int | None, sig: int) -> None:
    targets = []
    if pgid:
        try:
            os.killpg(pgid, sig)
            targets.append(("进程组", pgid))
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        os.kill(pid, sig)
        targets.append(("进程", pid))
    except ProcessLookupError:
        pass


def terminate(pid: int, pgid: int | None = None) -> None:
    """请求优雅退出：POSIX 发 SIGTERM 给进程组；Windows 用 taskkill（不带 /F）。"""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, text=True,
                       errors="replace", timeout=15)   # 中文 Windows 的输出是 GBK，不能按 UTF-8 硬解
    else:
        _signal_tree_posix(pid, pgid, signal.SIGTERM)


def kill_tree(pid: int, pgid: int | None = None) -> None:
    """强制回收整棵进程树（编排器的最后一道，用来满足「停止后无孤儿进程」）。"""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True,
                       errors="replace", timeout=15)
    else:
        _signal_tree_posix(pid, pgid, signal.SIGKILL)


def wait_gone(pid: int, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(interval)
    return not pid_alive(pid)


def child_pids_windows(pid: int) -> list[int]:
    """用 wmic 查子进程（无第三方依赖）；查不到就返回空，交由 taskkill /T 兜底。"""
    if not IS_WINDOWS:
        return []
    try:
        out = subprocess.run(["wmic", "process", "where", f"ParentProcessId={pid}", "get", "ProcessId"],
                             capture_output=True, text=True, errors="replace", timeout=15).stdout
    except Exception:
        return []
    ids = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line.isdigit():
            ids.append(int(line))
    return ids


def python_command(*module_args: str) -> list[str]:
    return [sys.executable, *module_args]
