"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorMessage } from "@/lib/api";
import type { PolicyGroup, PolicyVersion } from "@/lib/types";

type FormMode = { kind: "new" } | { kind: "version"; group: PolicyGroup };

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function emptyForm() {
  return {
    entry_id: "",
    topic: "",
    title: "",
    body: "",
    source: "商家后台",
    valid_from: today(),
  };
}

function validity(row: PolicyVersion): string {
  return `${row.valid_from} 至 ${row.valid_until ?? "长期"}`;
}

export function AdminPolicies({ role }: { role: "owner" | "agent" }) {
  const [groups, setGroups] = useState<PolicyGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [mode, setMode] = useState<FormMode | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setGroups(await api<PolicyGroup[]>("/api/admin/policies"));
      setError("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openNew = () => {
    setMode({ kind: "new" });
    setForm(emptyForm());
    setNotice("");
    setError("");
  };

  const openVersion = (group: PolicyGroup) => {
    const source = group.current ?? group.versions[0];
    setMode({ kind: "version", group });
    setForm({
      entry_id: group.entry_id,
      topic: group.topic,
      title: source?.title ?? "",
      body: source?.body ?? "",
      source: source?.source ?? "商家后台",
      valid_from: today(),
    });
    setNotice("");
    setError("");
  };

  const publish = async (event: FormEvent) => {
    event.preventDefault();
    if (!mode) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await api<PolicyVersion>("/api/admin/policies", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setNotice(`政策 ${created.entry_id}@${created.version} 已发布`);
      setMode(null);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-policies">
      <div className="section-head">
        <div>
          <h3 style={{ margin: 0 }}>政策库</h3>
          <p>
            AI 与售后接口只引用现行版本；发布新版本会写入审计并结束上一版本。
          </p>
        </div>
        {role === "owner" ? (
          <button
            className="btn btn-primary"
            type="button"
            data-testid="new-policy"
            onClick={openNew}
          >
            新增政策
          </button>
        ) : (
          <span className="muted">客服只读；发布政策需要店主权限。</span>
        )}
      </div>

      {notice ? <div className="notice notice-success">{notice}</div> : null}
      {error ? <div className="notice notice-error">{error}</div> : null}

      {mode ? (
        <form className="panel panel-pad" onSubmit={publish}>
          <div className="section-head" style={{ marginBottom: 10 }}>
            <div>
              <h3 style={{ margin: 0 }}>
                {mode.kind === "new"
                  ? "新增政策"
                  : `发布 ${mode.group.entry_id} 新版本`}
              </h3>
              <p>
                已发布版本不可修改；生效日必须晚于当前最新版本。
              </p>
            </div>
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="policy-entry-id">政策条目</label>
              <input
                id="policy-entry-id"
                data-testid="policy-entry-id"
                value={form.entry_id}
                onChange={(event) =>
                  setForm({ ...form, entry_id: event.target.value })
                }
                maxLength={64}
                disabled={mode.kind === "version"}
                placeholder="如 POLICY-WARRANTY"
              />
            </div>
            <div className="field">
              <label htmlFor="policy-topic">主题</label>
              <input
                id="policy-topic"
                data-testid="policy-topic"
                value={form.topic}
                onChange={(event) =>
                  setForm({ ...form, topic: event.target.value })
                }
                maxLength={64}
                disabled={mode.kind === "version"}
                placeholder="如 warranty"
              />
            </div>
            <div className="field full">
              <label htmlFor="policy-title">标题</label>
              <input
                id="policy-title"
                data-testid="policy-title"
                value={form.title}
                onChange={(event) =>
                  setForm({ ...form, title: event.target.value })
                }
                maxLength={255}
              />
            </div>
            <div className="field full">
              <label htmlFor="policy-body">正文</label>
              <textarea
                id="policy-body"
                data-testid="policy-body"
                value={form.body}
                onChange={(event) =>
                  setForm({ ...form, body: event.target.value })
                }
                maxLength={10000}
                rows={5}
              />
            </div>
            <div className="field">
              <label htmlFor="policy-source">来源</label>
              <input
                id="policy-source"
                data-testid="policy-source"
                value={form.source}
                onChange={(event) =>
                  setForm({ ...form, source: event.target.value })
                }
                maxLength={255}
              />
            </div>
            <div className="field">
              <label htmlFor="policy-valid-from">生效日</label>
              <input
                id="policy-valid-from"
                data-testid="policy-valid-from"
                type="date"
                value={form.valid_from}
                onChange={(event) =>
                  setForm({ ...form, valid_from: event.target.value })
                }
              />
            </div>
          </div>
          <div className="proposal-actions" style={{ marginTop: 12 }}>
            <button
              className="btn btn-primary"
              type="submit"
              data-testid="publish-policy"
              disabled={busy}
            >
              {busy ? "发布中" : "发布政策"}
            </button>
            <button
              className="btn btn-ghost"
              type="button"
              onClick={() => setMode(null)}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}

      {loading ? (
        <div className="loading">加载政策库…</div>
      ) : groups.length === 0 ? (
        <div className="empty-state">还没有政策条目。</div>
      ) : (
        <div className="admin-grid" style={{ marginTop: 12 }}>
          {groups.map((group) => (
            <article
              className="proposal-card"
              key={group.entry_id}
              data-testid={`policy-${group.entry_id}`}
            >
              <div className="proposal-head">
                <div className="proposal-title">
                  <strong>
                    {group.current?.title ?? "当前无生效版本"}
                  </strong>
                  <span className="badge badge-neutral">{group.topic}</span>
                  {group.current ? (
                    <span className="badge badge-ok">
                      {group.current.version}
                    </span>
                  ) : (
                    <span className="badge badge-warn">待生效</span>
                  )}
                </div>
                {role === "owner" ? (
                  <button
                    className="btn btn-ghost btn-sm"
                    type="button"
                    data-testid={`publish-${group.entry_id}`}
                    onClick={() => openVersion(group)}
                  >
                    发布新版本
                  </button>
                ) : null}
              </div>
              <div className="proposal-meta">
                <span>{group.entry_id}</span>
                <span>
                  {group.current
                    ? validity(group.current)
                    : `最新版本 ${group.versions[0]?.valid_from} 起`}
                </span>
                <span>
                  {group.current?.source ?? group.versions[0]?.source}
                </span>
              </div>
              {group.current ? (
                <p className="after-sales-reason">{group.current.body}</p>
              ) : null}
              <details style={{ marginTop: 10 }}>
                <summary className="muted">
                  版本历史（{group.versions.length}）
                </summary>
                <div
                  className="proposal-meta"
                  style={{
                    flexDirection: "column",
                    alignItems: "flex-start",
                    gap: 6,
                    marginTop: 8,
                  }}
                >
                  {group.versions.map((row) => (
                    <span key={row.version}>
                      {row.version} · {validity(row)} · 发布于{" "}
                      {row.published_at}
                    </span>
                  ))}
                </div>
              </details>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
