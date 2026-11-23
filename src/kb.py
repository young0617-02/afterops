"""政策/FAQ 知识库（F-006）：双时态有效性 + 版本链 + TTL 缓存 + stale-retrieval 监控。

检索默认使用内置 BM25（纯 stdlib，零依赖、可复跑）。配置 KB_EMBEDDING=bge-m3 时
尝试本地向量后端，加载失败会**如实记录降级原因**并在响应/日志中标注，不静默假装成功。

双时态含义：
  - valid_from / valid_until  → 数据有效期（政策在业务上生效的时间区间）
  - published_at              → 事务时间（该条进入知识库的时间）
查询时点 as_of 落在有效期内才允许命中；「当时有效、现已过期」的条目会被返回但打上
historical_only 标记，用于 A10 的时效验证。
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from typing import Iterable

from src.config import settings
from src.schemas import KBEntry, RetrievalHit, parse_date, today
from src.shop.db import connect, ensure_shop_db

_ASCII_RE = re.compile(r"[A-Za-z0-9]+")
_CJK_RE = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    """中文按字符双 gram（bigram）+ 英文/数字按词，避免依赖外部分词器。"""
    toks: list[str] = []
    for m in _ASCII_RE.finditer(text):
        toks.append(m.group().lower())
    for span in _CJK_RE.finditer(text):
        s = span.group()
        toks.extend(s)  # 单字召回
        toks.extend(s[i : i + 2] for i in range(len(s) - 1))
    return toks


def _to_date(v: str | None) -> date | None:
    return parse_date(v) if v else None


def _row_to_entry(r: dict) -> KBEntry:
    return KBEntry(
        entry_id=r["entry_id"],
        version=r["version"],
        title=r["title"],
        body=r["body"],
        category=r["category"],
        topic=r["topic"],
        source=r["source"],
        valid_from=_to_date(r["valid_from"]) or date.min,
        valid_until=_to_date(r["valid_until"]),
        published_at=_to_date(r["published_at"]) or date.min,
        supersedes=r["supersedes"],
    )


class BM25:
    def __init__(self, docs: dict[str, list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.doc_ids = list(docs)
        self.tf = {d: Counter(t) for d, t in docs.items()}
        self.len = {d: max(1, sum(self.tf[d].values())) for d in self.doc_ids}
        self.avg_len = sum(self.len.values()) / max(1, len(self.doc_ids))
        df: Counter = Counter()
        for d in self.doc_ids:
            for t in self.tf[d]:
                df[t] += 1
        n = len(self.doc_ids)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, query_tokens: list[str]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for d in self.doc_ids:
            tf = self.tf[d]
            s = 0.0
            for t in set(query_tokens):
                f = tf.get(t, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * self.len[d] / self.avg_len)
                s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / denom
            if s > 0:
                out[d] = s
        return dict(out)


@dataclass
class StaleEvent:
    kind: str  # ttl_expired | stale_retrieval | historical_only | backend_degraded
    detail: str
    at: float
    entry_id: str | None = None


@dataclass
class _CacheSlot:
    hits: list[RetrievalHit]
    written_at: float
    as_of: date


class KnowledgeBase:
    def __init__(self, db_path=None, *, as_of: str | None = None, ttl_seconds: int | None = None,
                 embedding: str | None = None):
        s = settings()
        self.db_path = ensure_shop_db(db_path or s.path(s.shop_db_path))
        self.ttl = s.kb_ttl_seconds if ttl_seconds is None else ttl_seconds
        self.top_k = s.kb_top_k
        self.default_as_of = today(as_of or s.kb_as_of)
        self.backend = "bm25"
        self.backend_note = "内置 BM25（纯标准库实现）"
        self.embedding_requested = (embedding or s.kb_embedding).lower()
        self.stale_events: list[StaleEvent] = []
        self._cache: dict[tuple[str, date], _CacheSlot] = {}
        self.reload()
        if self.embedding_requested not in ("bm25", ""):
            self._try_vector_backend(self.embedding_requested)

    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        conn = connect(self.db_path)
        try:
            rows = [dict(r) for r in conn.execute("SELECT * FROM kb_entries").fetchall()]
        finally:
            conn.close()
        self.entries: dict[tuple[str, str], KBEntry] = {}
        for r in rows:
            e = _row_to_entry(r)
            self.entries[(e.entry_id, e.version)] = e
        self._reindex()

    def _index_key(self, e: KBEntry) -> str:
        return f"{e.entry_id}@{e.version}"

    def _reindex(self) -> None:
        docs = {self._index_key(e): tokenize(f"{e.title} {e.body} {e.topic} {e.category}")
                for e in self.entries.values()}
        self.bm25 = BM25(docs)
        self._cache.clear()

    def _try_vector_backend(self, name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - 可选依赖
            self._record(StaleEvent("backend_degraded",
                                    f"请求向量后端 {name} 不可用（{type(exc).__name__}: {exc}），已降级为 BM25",
                                    time.time()))
            self.backend_note = f"已降级为 BM25：请求的后端 {name} 不可用（{type(exc).__name__}）"
            return
        try:  # pragma: no cover - 需下载模型，默认路径不执行
            model_name = "BAAI/bge-m3" if "m3" in name else name
            self._vector_model = SentenceTransformer(model_name)
            self.backend = "vector"
            self.backend_note = f"本地向量后端 {model_name}（sentence-transformers）"
        except Exception as exc:
            self._record(StaleEvent("backend_degraded",
                                    f"向量后端 {name} 加载失败，已降级为 BM25：{exc}", time.time()))
            self.backend_note = f"已降级为 BM25：{name} 加载失败"

    def _record(self, ev: StaleEvent) -> None:
        self.stale_events.append(ev)

    def _day(self, as_of: str | date | None) -> date:
        """查询时点：显式 > KB_AS_OF/构造参数 default_as_of。"""
        if isinstance(as_of, date):
            return as_of
        if isinstance(as_of, str) and as_of:
            return parse_date(as_of) or self.default_as_of
        return self.default_as_of

    # ------------------------------------------------------------------ #
    def valid_entries(self, as_of: date) -> list[KBEntry]:
        return [e for e in self.entries.values() if e.is_valid_on(as_of)]

    def versions(self, entry_id: str) -> list[KBEntry]:
        vs = [e for (eid, _), e in self.entries.items() if eid == entry_id]
        return sorted(vs, key=lambda e: e.valid_from)

    def get_current(self, entry_id: str | None = None, topic: str | None = None,
                    as_of: str | date | None = None) -> KBEntry | None:
        """返回某条目/某主题在 as_of 时点生效的版本（版本链取 valid_from 最大者）。"""
        day = self._day(as_of)
        pool: Iterable[KBEntry] = self.entries.values()
        if entry_id:
            pool = [e for e in pool if e.entry_id == entry_id]
        if topic:
            pool = [e for e in pool if e.topic == topic]
        valid = [e for e in pool if e.is_valid_on(day)]
        if not valid:
            return None
        return max(valid, key=lambda e: (e.valid_from, e.published_at))

    def set_entry_validity(self, entry_id: str, version: str, valid_until: str | None) -> None:
        """测试/演示用：修改某版本的有效期（A10），同时重建索引。"""
        key = (entry_id, version)
        if key not in self.entries:
            raise KeyError(f"未知条目 {entry_id}@{version}")
        old = self.entries[key]
        self.entries[key] = KBEntry(
            entry_id=old.entry_id, title=old.title, body=old.body, category=old.category,
            topic=old.topic, version=old.version, source=old.source, valid_from=old.valid_from,
            valid_until=_to_date(valid_until), published_at=old.published_at, supersedes=old.supersedes,
        )
        self._reindex()

    def current_hits_for(self, topics: tuple[str, ...], query: str,
                         as_of: str | date | None = None) -> list[RetrievalHit]:
        """主题内择优：对每个主题取「该时点生效的条文」，再按与本轮问题的相关度排序。

        这样既保证有主题对齐的依据可引（不会因 BM25 弱命中而漏），
        又不会让某个条文以固定满分压过同主题里更贴切的那条（如换货 vs 退货）。
        """
        day = self._day(as_of)
        raw: list[tuple[KBEntry, float]] = []
        for tp in topics:
            entry = self.get_current(topic=tp, as_of=day)
            if entry is None:
                continue
            score = self.bm25.score(tokenize(query)).get(f"{entry.entry_id}@{entry.version}", 0.0)
            raw.append((entry, score))
        if not raw:
            return []
        top = max(r for _, r in raw) or 1.0
        hits = [RetrievalHit(entry=e, score=round(r / top, 4) if top else 0.0, reason=self.backend)
                for e, r in raw]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits

    def _scores(self, query: str) -> dict[str, float]:
        if self.backend == "vector":  # pragma: no cover - 需要本地模型
            return self._vector_scores(query)
        return self.bm25.score(tokenize(query))

    def _vector_scores(self, query: str) -> dict[str, float]:  # pragma: no cover - 需要本地模型
        qv = self._embed([query])[0]
        out: dict[str, float] = {}
        for key, e in self.entries.items():
            dv = self._embed([f"{e.title}。{e.body}"])[0]
            sim = sum(a * b for a, b in zip(qv, dv))
            if sim > 0:
                out[key] = sim
        return out

    def _embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover - 需要本地模型
        vecs = self._vector_model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]

    # ------------------------------------------------------------------ #
    def search(self, query: str, as_of: str | date | None = None, top_k: int | None = None,
               use_cache: bool = True) -> list[RetrievalHit]:
        day = self._day(as_of)
        k = top_k or self.top_k
        cache_key = (query.strip().lower(), day)
        now = date.today()

        if use_cache:
            slot = self._cache.get(cache_key)
            if slot:
                age = time.time() - slot.written_at
                if age <= self.ttl:
                    return self._refresh_from_cache(slot, day, k)[:k]
                self._record(StaleEvent("ttl_expired", f"缓存超过 TTL {self.ttl}s（age={age:.1f}s），已强制重检索", time.time()))
                del self._cache[cache_key]

        scores = self._scores(query)
        valid_by_id = {self._index_key(e): e for e in self.valid_entries(day)}
        ranked = sorted(
            ((key, sc) for key, sc in scores.items() if key in valid_by_id),
            key=lambda x: x[1], reverse=True,
        )[:k]
        max_score = ranked[0][1] if ranked else 0.0
        hits = [
            RetrievalHit(entry=valid_by_id[key], score=round(sc / max_score, 4) if max_score else 0.0,
                         reason=self.backend)
            for key, sc in ranked
        ]
        for h in hits:
            if h.entry.valid_until is not None and h.entry.valid_until < now and day < now:
                h.reason = "historical_only"
                self._record(StaleEvent("historical_only",
                                        f"{h.entry.entry_id}@{h.entry.version} 在 as_of={day} 有效，但现实中已过期",
                                        time.time(), h.entry.entry_id))
        self._cache[cache_key] = _CacheSlot(hits=hits, written_at=time.time(), as_of=day)
        return hits

    def _refresh_from_cache(self, slot: _CacheSlot, day: date, k: int) -> list[RetrievalHit]:
        """命中缓存时按「当前知识库状态」复核：条目被删除/过期都要剔除并记 stale。"""
        out: list[RetrievalHit] = []
        for h in slot.hits:
            cur = self.entries.get((h.entry.entry_id, h.entry.version))
            if cur is None:
                self._record(StaleEvent("stale_retrieval",
                                        f"缓存中的 {h.entry.entry_id}@{h.entry.version} 已从知识库移除",
                                        time.time(), h.entry.entry_id))
                continue
            if cur.valid_until is not None and cur.valid_until < date.today():
                self._record(StaleEvent("stale_retrieval",
                                        f"缓存命中 {cur.entry_id}@{cur.version}，但该条目已于 "
                                        f"{cur.valid_until} 失效，本次剔除", time.time(), cur.entry_id))
                continue
            if not cur.is_valid_on(day):
                continue
            out.append(replace(h, entry=cur))
        return out

    def stats(self) -> dict:
        kinds = Counter(ev.kind for ev in self.stale_events)
        return {
            "backend": self.backend,
            "backend_note": self.backend_note,
            "entries": len(self.entries),
            "valid_today": len(self.valid_entries(date.today())),
            "cache_size": len(self._cache),
            "stale_events": dict(kinds),
        }
