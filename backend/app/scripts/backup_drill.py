"""Backup/restore drill using the running Docker Postgres.

Creates a pg_dump of the `afterops` database, restores it into a temporary
database, checks table/row counts, then drops the temporary database.
The primary `afterops` database is never modified.

Run:
    uv run python -m app.scripts.backup_drill
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ["docker", "compose", "-f", str(REPO_ROOT / "infra" / "docker-compose.yml")]


def _compose_exec(*args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    return _compose_exec_service("postgres", *args, input_bytes=input_bytes)


def _compose_exec_service(
    service: str, *args: str, input_bytes: bytes | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        COMPOSE + ["exec", "-T", service, *args],
        input=input_bytes,
        capture_output=True,
        check=True,
    )


def _psql(database: str, sql: str) -> str:
    result = _compose_exec(
        "psql",
        "-U",
        "afterops",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
    )
    return result.stdout.decode("utf-8", "replace").strip()


def _psql_admin(sql: str) -> None:
    result = _compose_exec(
        "psql", "-U", "afterops", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", sql
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AfterOps Postgres 备份恢复演练")
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "backups"))
    parser.add_argument("--report", default=str(REPO_ROOT / "reports" / "backup-drill.md"))
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = out_dir / f"afterops-{stamp}.sql.gz"
    attachments_backup = out_dir / f"afterops-attachments-{stamp}.tar.gz"
    temp_db = f"afterops_drill_{stamp.replace('-', '_')}"
    attachment_count = "0"
    attachment_verified = "0"

    try:
        _psql("afterops", "SELECT 1")
        dump = _compose_exec("pg_dump", "-U", "afterops", "-d", "afterops")
        backup.write_bytes(gzip.compress(dump.stdout))

        tar = _compose_exec_service(
            "api", "tar", "-C", "/data/uploads", "-czf", "-", "."
        )
        attachments_backup.write_bytes(tar.stdout)
        attachment_count = _psql(
            "afterops", "SELECT count(*) FROM attachments"
        )
        with tempfile.TemporaryDirectory(prefix="afterops-attachments-") as tmp:
            with tarfile.open(attachments_backup, "r:gz") as archive:
                members = [m for m in archive.getmembers() if m.isfile()]
                archive.extractall(tmp, filter="data")
            expected = dict(
                line.split("|", 1)
                for line in _psql(
                    "afterops",
                    "SELECT storage_name || '|' || sha256 FROM attachments",
                ).splitlines()
                if line
            )
            verified = 0
            for member in members:
                name = Path(member.name).name
                path = Path(tmp) / name
                if name in expected and path.is_file():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest == expected[name]:
                        verified += 1
            attachment_verified = str(verified)
            if verified != int(attachment_count):
                raise RuntimeError(
                    f"附件校验不一致：DB {attachment_count}，归档匹配 {verified}"
                )

        _psql_admin(f'CREATE DATABASE "{temp_db}"')
        restore = _compose_exec(
            "psql",
            "-U",
            "afterops",
            "-d",
            temp_db,
            "-v",
            "ON_ERROR_STOP=1",
            input_bytes=gzip.decompress(backup.read_bytes()),
        )
        if restore.returncode != 0:
            raise RuntimeError(restore.stderr.decode("utf-8", "replace"))

        table_count = _psql(
            temp_db,
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'",
        )
        merchants = _psql(temp_db, "SELECT count(*) FROM merchants")
        orders = _psql(temp_db, "SELECT count(*) FROM orders")
        _psql_admin(f'DROP DATABASE "{temp_db}"')
        dropped = "已删除"
        passed = True
        error = ""
    except Exception as exc:  # noqa: BLE001 - drill reports the exact failure
        passed = False
        dropped = "清理失败或未执行"
        table_count = merchants = orders = "-"
        attachment_verified = "0"
        error = f"{type(exc).__name__}: {exc}"
        try:
            _psql_admin(f'DROP DATABASE IF EXISTS "{temp_db}"')
            dropped = "异常后已清理"
        except Exception as cleanup_exc:  # noqa: BLE001
            dropped = f"清理失败：{cleanup_exc}"

    lines = [
        "# 备份恢复演练",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 方式：`pg_dump` → 临时库 `{temp_db}` 恢复 → 计数校验 → 删除临时库；uploads 归档 → 临时目录 SHA-256 校验",
        f"- 数据库备份：`{backup}`（{backup.stat().st_size if backup.exists() else 0} bytes，gzip）",
        f"- 附件归档：`{attachments_backup}`（{attachments_backup.stat().st_size if attachments_backup.exists() else 0} bytes，tar.gz）",
        f"- 结果：**{'PASS' if passed else 'FAIL'}**",
        f"- 恢复后表数：{table_count}；merchants：{merchants}；orders：{orders}",
        f"- 附件：DB {attachment_count} 个，归档 SHA-256 匹配 {attachment_verified} 个",
        f"- 临时库清理：{dropped}",
        "",
        "> 演练不修改主库 `afterops`；数据库恢复到临时库，附件只解压到临时目录做哈希校验，不覆盖线上卷。",
        "> 本演练只验证备份文件可恢复、附件归档可解压且哈希一致，不覆盖 PITR、跨机恢复或恢复时长目标。",
    ]
    if error:
        lines += ["", f"- 错误：`{error}`"]
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[backup-drill] {'PASS' if passed else 'FAIL'} → {backup}；表数 {table_count}；{report}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
