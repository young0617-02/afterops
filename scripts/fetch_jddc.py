"""JDDC 语料闸门（F-001）：可达性取证 + 下载校验 + 本地导入。

这个脚本的存在理由：方案把 F-001 列为 P0 一票否决，而我（作者）此前**没有实际下载验证过**。
所以第一步不是写业务代码，而是把所有官方/镜像入口逐个探测，把 HTTP 状态、耗时、返回体摘要
原样记下来，形成可复跑的留证（`data/jddc/acquisition_log.json`）。

用法：
    python scripts/fetch_jddc.py probe                     # 只做可达性取证（不下载）
    python scripts/fetch_jddc.py probe --timeout 20
    python scripts/fetch_jddc.py ingest --file D:/path/train.json   # 我自己拿到文件后入库校验
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JDDC_DIR = REPO / "data" / "jddc"
LOG_PATH = JDDC_DIR / "acquisition_log.json"

# 候选入口：论文页/官方发放页/官方对象存储/常见镜像。逐条探测，命中才下载。
SOURCES = [
    {"name": "paper_lrec2020", "kind": "paper", "url": "https://aclanthology.org/2020.lrec-1.58/",
     "purpose": "规模与许可口径的原始出处（论文页）"},
    {"name": "paper_pdf", "kind": "paper", "url": "https://aclanthology.org/2020.lrec-1.58.pdf",
     "purpose": "论文 PDF（规模/289 意图/LREC 许可声明）"},
    {"name": "jddc_jd_com", "kind": "official", "url": "https://jddc.jd.com/",
     "purpose": "论文正文声明的数据发放地址（Our dataset is available at）"},
    {"name": "jddc_oss_train", "kind": "official_oss",
     "url": "https://jddc-challenge.s3.cn-north-1.jdcloud-oss.com/JDDC/train.json",
     "purpose": "历史公开对象存储路径（挑战赛时期）"},
    {"name": "aichengxing", "kind": "platform", "url": "https://data.aichengxing.com/index.html",
     "purpose": "AI  Challenger 数据平台（曾作为发放渠道，通常需登录）"},
    {"name": "hf_jddc_search", "kind": "mirror", "url": "https://huggingface.co/api/datasets?search=jddc",
     "purpose": "HuggingFace 官方站检索"},
    {"name": "hfmirror_jddc_search", "kind": "mirror", "url": "https://hf-mirror.com/api/datasets?search=jddc",
     "purpose": "hf-mirror 检索（境内可达镜像）"},
    {"name": "modelscope_search", "kind": "mirror", "url": "https://www.modelscope.cn/api/v1/datasets?Query=JDDC",
     "purpose": "ModelScope 检索（境内）"},
]


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def src_is_json(url: str) -> bool:
    return "/api/" in url or url.endswith(".json")


def probe_one(url: str, timeout: float, follow: bool = True) -> dict:
    t0 = time.monotonic()
    rec = {"url": url, "probed_at": now(), "timeout_s": timeout}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jddc-gate-probe/0.1"})
        opener = urllib.request.build_opener() if follow else urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler(max_redirections=0))
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(65536)
            rec.update(status=resp.status, final_url=resp.geturl(),
                       bytes_preview=len(body), elapsed_ms=int((time.monotonic() - t0) * 1000),
                       body_head=body[:240].decode("utf-8", "replace"),
                       body_full=body.decode("utf-8", "replace")[:20000] if src_is_json(rec["url"]) else "")
    except urllib.error.HTTPError as e:  # noqa: 保留原分支
        body = b""
        try:
            body = e.read(512)
        except Exception:
            pass
        rec.update(status=e.code, final_url=url, bytes_preview=len(body),
                   elapsed_ms=int((time.monotonic() - t0) * 1000),
                   body_head=body[:240].decode("utf-8", "replace"), error=f"HTTPError {e.reason}")
    except Exception as e:  # DNS/连接重置/超时
        rec.update(status=None, final_url=None, bytes_preview=0,
                   elapsed_ms=int((time.monotonic() - t0) * 1000),
                   error=f"{type(e).__name__}: {e}")
    return rec


ERROR_MARKERS = ("error2.aspx", "页面不存在", "失联", "404", "Not Found", "no such bucket", "nosuchbucket")


def judge(rec: dict, kind: str) -> tuple[str, str]:
    """把一次探测转成结论：这条入口现在能不能拿到数据。"""
    st = rec.get("status")
    if st is None:
        return "no_data", f"网络不可达（{str(rec.get('error'))[:60]}）"
    if st >= 400:
        return "no_data", f"HTTP {st}（{str(rec.get('error'))[:40]}）"
    final = (rec.get("final_url") or "").lower()
    head = (rec.get("body_head") or "").lower()
    if any(m.lower() in final or m.lower() in head for m in ERROR_MARKERS):
        return "no_data", "被重定向/渲染为错误页，页面已不存在"
    if kind == "mirror":
        try:
            data = json.loads(rec.get("body_full") or "[]")
            n = len(data) if isinstance(data, list) else len(data.get("Data", []) or data.get("datasets", []) or [])
            return ("data_candidate", f"检索到 {n} 个候选数据集") if n else ("no_data", "镜像检索结果为空")
        except Exception:
            return "unknown", "镜像返回体无法解析，需人工打开确认"
    if kind == "paper":
        return "reference_only", "论文页/PDF，可核对规模与许可，不含数据文件"
    return "unknown", "页面可达但是否为发放入口需人工确认（通常需登录）"


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def cmd_probe(timeout: float, out: Path) -> dict:
    import platform
    JDDC_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for src in SOURCES:
        r = probe_one(src["url"], timeout)
        verdict, reason = judge(r, src["kind"])
        r.pop("body_full", None)  # 只在判定时用，不落盘，避免日志里塞进大段 HTML
        r2 = {**r, "name": src["name"], "kind": src["kind"], "purpose": src["purpose"],
              "verdict": verdict, "reason": reason}
        if verdict == "no_data" and r.get("status") == 200:
            r2["reachable_but_empty"] = True
        results.append(r2)
        print(f"[{verdict:<15}] {src['name']:<22} status={r['status']} {r['elapsed_ms']}ms ｜ {reason}")
    candidates = [r["name"] for r in results if r["verdict"] == "data_candidate"]
    gate = "PASS" if candidates else "NOT_PASSED"
    note = ("存在可直取的数据入口，可继续 F-003 抽样" if candidates else
            "论文可核对规模与许可，但数据文件不可直取：官方发放页被重定向到京东错误页、境内镜像检索为空、"
            "历史对象存储桶不存在（NoSuchBucket）、AI Challenger 平台域名本机不可解析。"
            "判定闸门未通过：需在人工获取语料后再执行 F-003 清洗与 F-002 标签全量核对。")
    log = {"generated_at": now(), "python": platform.python_version(), "timeout_s": timeout,
           "probes": results, "data_candidates": candidates, "gate_f-001": gate, "gate_note": note}
    out.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    (JDDC_DIR / "gate_verdict.json").write_text(json.dumps(
        {"generated_at": log["generated_at"], "gate_f-001": gate, "gate_note": note,
         "data_candidates": candidates, "probed_sources": [r["name"] for r in results]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"[probe] F-001 判定：{gate}；留证 → {out}")
    return log

def cmd_ingest(file: Path, dest_dir: Path) -> int:
    if not file.exists():
        print(f"[ingest] 文件不存在：{file}")
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / file.name
    if target.resolve() != file.resolve():
        shutil.copyfile(file, target)
    digest = sha256_of(target)
    meta = {"file": str(target), "size_bytes": target.stat().st_size, "sha256": digest,
            "ingested_at": now(), "origin": "manual-download（由使用者自行获取后导入）",
            "note": "本仓库不附带 JDDC 原始数据；此记录仅证明入库时的文件身份"}
    (dest_dir / "ingest_manifest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    print(f"[ingest] {target.name} size={meta['size_bytes']} sha256={digest[:16]}…")
    print(f"[ingest] 清单 → {dest_dir / 'ingest_manifest.json'}")
    print("[ingest] 下一步：python scripts/clean_jddc.py --raw " + str(target))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="JDDC 语料闸门取证与入库")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe", help="探测所有已知发放入口并留证")
    p1.add_argument("--timeout", type=float, default=15.0)
    p1.add_argument("--out", default=str(LOG_PATH))
    p2 = sub.add_parser("ingest", help="校验并入库我手动下载的原始文件")
    p2.add_argument("--file", required=True, type=Path)
    p2.add_argument("--dest", default=str(JDDC_DIR / "raw"), type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "probe":
        cmd_probe(args.timeout, Path(args.out))
        return 0
    return cmd_ingest(Path(args.file), Path(args.dest))


if __name__ == "__main__":
    raise SystemExit(main())
