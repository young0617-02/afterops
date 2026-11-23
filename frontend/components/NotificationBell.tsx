"use client";

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import { formatDateTime } from "@/lib/status";
import type { NotificationItem, NotificationList } from "@/lib/types";

export function NotificationBell({
  scope,
}: {
  scope: "customer" | "admin";
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [live, setLive] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const lastIdRef = useRef(0);
  const seenIdsRef = useRef<Set<number>>(new Set());
  const base =
    scope === "admin" ? "/api/admin/notifications" : "/api/store/notifications";

  const load = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        const body = await api<NotificationList>(`${base}?limit=10`);
        lastIdRef.current = Math.max(
          lastIdRef.current,
          body.items[0]?.id ?? 0,
        );
        for (const item of body.items) {
          seenIdsRef.current.add(item.id);
        }
        setItems(body.items);
        setUnread(body.unread_count);
        setError("");
        setReady(true);
      } catch (err) {
        setReady(false);
        if (!silent) setError(errorMessage(err));
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [base],
  );

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(true), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!ready) return;
    const source = new EventSource(
      `${base}/stream?after_id=${lastIdRef.current}&wait_seconds=25`,
    );
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    source.onmessage = (event) => {
      try {
        const item = JSON.parse(event.data) as NotificationItem;
        if (seenIdsRef.current.has(item.id)) {
          return;
        }
        seenIdsRef.current.add(item.id);
        lastIdRef.current = Math.max(lastIdRef.current, item.id);
        setItems((previous) => [item, ...previous].slice(0, 10));
        if (!item.read_at) {
          setUnread((previous) => previous + 1);
        }
      } catch {
        // Ignore malformed stream frames; polling remains the fallback.
      }
    };
    return () => {
      source.close();
      setLive(false);
    };
  }, [base, ready]);

  useEffect(() => {
    void load(true);
  }, [pathname, load]);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (
        wrapRef.current &&
        !wrapRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (scope === "customer" && pathname.startsWith("/admin")) {
    return null;
  }

  const openItem = async (item: NotificationItem) => {
    setOpen(false);
    try {
      if (!item.read_at) {
        await api(`${base}/${item.id}/read`, { method: "POST" });
        setItems((previous) =>
          previous.map((row) =>
            row.id === item.id
              ? { ...row, read_at: new Date().toISOString() }
              : row,
          ),
        );
        setUnread((previous) => Math.max(0, previous - 1));
      }
    } catch {
      // Navigation is more useful than blocking on a read receipt failure.
    }
    if (item.link) router.push(item.link);
  };

  const readAll = async () => {
    try {
      await api(`${base}/read-all`, { method: "POST" });
      const now = new Date().toISOString();
      setItems((previous) =>
        previous.map((row) => ({ ...row, read_at: row.read_at ?? now })),
      );
      setUnread(0);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="notification-wrap" ref={wrapRef}>
      <button
        className="notification-bell"
        type="button"
        aria-label={`通知${unread > 0 ? `，${unread} 条未读` : ""}`}
        aria-expanded={open}
        data-testid="notification-bell"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) void load(true);
        }}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {unread > 0 ? (
          <span className="notification-badge" data-testid="notification-unread">
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="notification-panel">
          <div className="notification-head">
            <strong>通知</strong>
            {live ? (
              <span
                className="badge badge-ok"
                data-testid="notification-live"
              >
                实时
              </span>
            ) : null}
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              data-testid="notification-read-all"
              onClick={() => void readAll()}
              disabled={unread === 0}
            >
              全部已读
            </button>
          </div>

          {error ? (
            <div className="notice notice-error">{error}</div>
          ) : loading ? (
            <div className="notification-empty">加载通知…</div>
          ) : items.length === 0 ? (
            <div className="notification-empty">暂无通知。</div>
          ) : (
            <div className="notification-list">
              {items.map((item) => (
                <button
                  className={`notification-item ${
                    item.read_at ? "" : "unread"
                  }`}
                  type="button"
                  key={item.id}
                  data-testid={`notification-item-${item.id}`}
                  onClick={() => void openItem(item)}
                >
                  <span className="notification-title">{item.title}</span>
                  {item.body ? (
                    <span className="notification-body">{item.body}</span>
                  ) : null}
                  <span className="notification-time">
                    {formatDateTime(item.created_at)}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
