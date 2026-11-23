"""建库命令：`python -m src.shop.seed [--print]`。"""
from __future__ import annotations

import argparse
from collections import OrderedDict

from src.shop.db import TABLES, VIEWS, build_shop_db, connect, content_hash, ensure_shop_db


def counts(db_path) -> "OrderedDict[str, int]":
    conn = connect(ensure_shop_db(db_path))
    out: "OrderedDict[str, int]" = OrderedDict()
    try:
        for name in TABLES + VIEWS:
            out[name] = conn.execute(f"SELECT COUNT(*) AS c FROM {name}").fetchone()["c"]
    finally:
        conn.close()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="构建/校验合成单店 SQLite 库")
    ap.add_argument("--db", default=None, help="目标 db 路径，默认取 .env 的 SHOP_DB_PATH")
    ap.add_argument("--verify", action="store_true", help="只校验现有库的行数与内容哈希")
    args = ap.parse_args(argv)

    if not args.verify:
        path = build_shop_db(args.db)
        print(f"[seed] 重建合成店铺库: {path}")
    else:
        path = ensure_shop_db(args.db)
        print(f"[seed] 校验合成店铺库: {path}")
    for name, n in counts(path).items():
        print(f"  {name:<22} {n}")
    print(f"[seed] content_sha256={content_hash(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
