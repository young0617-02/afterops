"use client";

import Link from "next/link";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import { api, errorMessage } from "@/lib/api";
import type {
  ChatHistoryMessage,
  ChatResponse,
  ConversationDetail,
  ConversationSummary,
} from "@/lib/types";
import { useCustomer } from "./CustomerProvider";

const POLICY_CITATION_RE = /^([A-Z][A-Z0-9-]*)@v\d+$/;

function policyHref(citation: string): string | null {
  const match = POLICY_CITATION_RE.exec(citation);
  return match ? `/help#policy-${match[1]}` : null;
}

type PendingAttachment = {
  file: File;
  preview: string;
};

type ChatMessage = {
  role: "user" | "assistant" | "agent";
  text: string;
  images?: string[];
  citations?: string[];
  proposals?: ChatResponse["proposals"];
  returns?: ChatResponse["returns"];
  degraded?: boolean;
  fallback?: string | null;
  ticketRef?: string | null;
};

function toChatMessage(message: ChatHistoryMessage): ChatMessage {
  return {
    role:
      message.role === "user"
        ? "user"
        : message.role === "agent"
          ? "agent"
          : "assistant",
    text: message.content,
    images: message.image_urls,
    citations: message.tool_calls_json?.citations,
    proposals: message.tool_calls_json?.proposals,
    returns: message.tool_calls_json?.returns,
    degraded: message.tool_calls_json?.degraded,
    fallback: message.tool_calls_json?.fallback_reason ?? null,
    ticketRef: message.tool_calls_json?.handoff_ticket_ref ?? null,
  };
}

export function ChatWidget() {
  const { customer } = useCustomer();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState(false);
  const [error, setError] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingAttachment[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const lastMessageId = useRef(0);

  useEffect(() => {
    if (open) {
      endRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, open]);

  useEffect(() => {
    if (!customer) {
      setMessages([]);
      setConversationId(null);
      lastMessageId.current = 0;
      return;
    }
    if (!open) return;
    let cancelled = false;
    const loadHistory = async () => {
      try {
        const conversations = await api<ConversationSummary[]>(
          "/api/chat/conversations",
        );
        if (cancelled || conversations.length === 0) return;
        const detail = await api<ConversationDetail>(
          `/api/chat/conversations/${conversations[0].id}`,
        );
        if (cancelled) return;
        lastMessageId.current =
          detail.messages[detail.messages.length - 1]?.id ?? 0;
        setConversationId(detail.id);
        setMessages(detail.messages.map(toChatMessage));
      } catch {
        // History is optional; a failed reload must not block a new chat.
      }
    };
    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [customer, open]);

  useEffect(() => {
    if (!open || !customer || conversationId === null) {
      setLive(false);
      return;
    }
    const source = new EventSource(
      `/api/chat/conversations/${conversationId}/stream?after_id=${lastMessageId.current}`,
    );
    source.onopen = () => setLive(true);
    source.onmessage = (event) => {
      try {
        const incoming = JSON.parse(event.data) as ChatHistoryMessage;
        if (incoming.id <= lastMessageId.current) return;
        lastMessageId.current = incoming.id;
        setMessages((previous) => [...previous, toChatMessage(incoming)]);
      } catch {
        // A malformed event must not break the live stream.
      }
    };
    source.onerror = () => setLive(false);
    return () => {
      source.close();
      setLive(false);
    };
  }, [open, customer, conversationId]);

  useEffect(() => {
    if (!open || !customer || conversationId === null || live) return;
    let cancelled = false;
    const timer = window.setInterval(async () => {
      if (busy) return;
      try {
        const detail = await api<ConversationDetail>(
          `/api/chat/conversations/${conversationId}?after_id=${lastMessageId.current}`,
        );
        if (cancelled || detail.messages.length === 0) return;
        lastMessageId.current = detail.messages[detail.messages.length - 1].id;
        setMessages((previous) => [
          ...previous,
          ...detail.messages.map(toChatMessage),
        ]);
      } catch {
        // Polling is best-effort; keep the current transcript on failure.
      }
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, customer, conversationId, busy, live]);

  const pickFiles = (files: FileList | null) => {
    if (!files) return;
    const room = Math.max(3 - pendingFiles.length, 0);
    const picked = Array.from(files).slice(0, room);
    if (picked.length < files.length) {
      setError("每条消息最多 3 张图片");
    } else {
      setError("");
    }
    setPendingFiles((previous) =>
      [
        ...previous,
        ...picked.map((file) => ({
          file,
          preview: URL.createObjectURL(file),
        })),
      ].slice(0, 3),
    );
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removePendingFile = (index: number) => {
    setPendingFiles((previous) => {
      const removed = previous[index];
      if (removed) URL.revokeObjectURL(removed.preview);
      return previous.filter((_, current) => current !== index);
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if ((!text && pendingFiles.length === 0) || busy) return;
    if (!customer) {
      setError("请先在右上角用手机号登录");
      return;
    }
    const files = pendingFiles.map((item) => item.file);
    const previews = pendingFiles.map((item) => item.preview);
    setBusy(true);
    setError("");
    try {
      const attachmentIds: number[] = [];
      for (const file of files) {
        const data = new FormData();
        data.append("file", file);
        const uploaded = await api<{ id: number }>("/api/store/attachments", {
          method: "POST",
          body: data,
        });
        attachmentIds.push(uploaded.id);
      }
      setMessages((previous) => [
        ...previous,
        { role: "user", text, images: previews },
      ]);
      setInput("");
      setPendingFiles([]);
      const response = await api<ChatResponse>("/api/chat", {
        method: "POST",
        body: JSON.stringify({
          text,
          conversation_id: conversationId,
          attachment_ids: attachmentIds,
        }),
      });
      setConversationId(response.conversation_id);
      try {
        const detail = await api<ConversationDetail>(
          `/api/chat/conversations/${response.conversation_id}`,
        );
        lastMessageId.current =
          detail.messages[detail.messages.length - 1]?.id ?? 0;
        setMessages(detail.messages.map(toChatMessage));
        previews.forEach((url) => URL.revokeObjectURL(url));
      } catch {
        lastMessageId.current = Math.max(
          lastMessageId.current,
          response.message_id,
        );
        setMessages((previous) => [
          ...previous,
          {
            role: "assistant",
            text: response.reply,
            citations: response.citations,
            proposals: response.proposals,
            returns: response.returns,
            degraded: response.degraded,
            fallback: response.fallback_reason,
            ticketRef: response.handoff.ticket_ref,
          },
        ]);
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        className="chat-fab"
        type="button"
        data-testid="chat-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-label="打开或关闭售后助手"
      >
        {open ? "关闭" : "售后助手"}
      </button>

      {open ? (
        <section className="chat-panel" aria-label="售后助手">
          <header className="chat-header">
            <div>
              <strong>售后助手</strong>
              <span>订单 · 物流 · 政策 · 售后申请</span>
              {live ? (
                <span className="chat-live" data-testid="chat-live">
                  实时
                </span>
              ) : null}
            </div>
            <button
              className="icon-btn"
              type="button"
              aria-label="关闭助手"
              onClick={() => setOpen(false)}
            >
              ×
            </button>
          </header>

          {!customer ? (
            <div className="chat-empty">
              请先在右上角用手机号登录，再开始咨询。
            </div>
          ) : (
            <div className="chat-messages">
              {messages.length === 0 ? (
                <div className="chat-empty">
                  可以问：我的订单到哪了、耳机能退吗、SKU-NCM-001 多少钱。
                </div>
              ) : null}
              {messages.map((message, index) => (
                <div
                  key={`${message.role}-${index}`}
                  className={`chat-message ${message.role}`}
                >
                  {message.role === "agent" ? (
                    <span className="chat-author">客服</span>
                  ) : null}
                  {message.text ? (
                    <div className="chat-bubble">{message.text}</div>
                  ) : null}
                  {message.images && message.images.length > 0 ? (
                    <div className="chat-images">
                      {message.images.map((url) => (
                        <a
                          href={url}
                          key={url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img
                            className="chat-image"
                            src={url}
                            alt="会话图片"
                            data-testid="chat-image"
                          />
                        </a>
                      ))}
                    </div>
                  ) : null}
                  {message.citations && message.citations.length > 0 ? (
                    <div className="citation-row">
                      {message.citations.map((citation) => {
                        const href = policyHref(citation);
                        return href ? (
                          <Link
                            className="citation"
                            href={href}
                            key={citation}
                            data-testid="policy-citation-link"
                            style={{ textDecoration: "underline" }}
                          >
                            {citation}
                          </Link>
                        ) : (
                          <span className="citation" key={citation}>
                            {citation}
                          </span>
                        );
                      })}
                    </div>
                  ) : null}
                  {message.proposals && message.proposals.length > 0 ? (
                    <div className="proposal-row">
                      {message.proposals.map((proposal) => (
                        <span className="proposal-chip" key={proposal.id}>
                          提案 #{proposal.id} ·{" "}
                          {proposal.status === "pending"
                            ? "等待人工审核"
                            : proposal.status}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {message.returns && message.returns.length > 0 ? (
                    <div className="proposal-row">
                      {message.returns.map((item) => (
                        <span className="proposal-chip" key={`return-${item.id}`}>
                          退货 #{item.id} ·{" "}
                          {item.resolution === "exchange" ? "换货" : "退货退款"} ·
                          等待人工审核
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {message.ticketRef ? (
                    <div
                      className="handoff-chip"
                      data-testid="chat-handoff-ticket"
                    >
                      已转人工 · 工单 {message.ticketRef}
                    </div>
                  ) : null}
                  {message.degraded ? (
                    <div className="chat-meta">
                      已回退模板：
                      {message.fallback ?? "模型输出未通过校验"}
                    </div>
                  ) : null}
                </div>
              ))}
              <div ref={endRef} />
            </div>
          )}

          {pendingFiles.length > 0 ? (
            <div className="chat-attachments" data-testid="chat-attachments">
              {pendingFiles.map((item, index) => (
                <div
                  className="chat-attachment"
                  key={`${item.file.name}-${index}`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={item.preview}
                    alt={item.file.name}
                    className="chat-image"
                  />
                  <button
                    type="button"
                    className="chat-attachment-remove"
                    aria-label={`移除 ${item.file.name}`}
                    onClick={() => removePendingFile(index)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <form className="chat-form" onSubmit={submit}>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              hidden
              data-testid="chat-image-input"
              onChange={(event) => pickFiles(event.target.files)}
            />
            <button
              className="btn btn-ghost btn-sm"
              type="button"
              data-testid="chat-image-button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!customer || busy || pendingFiles.length >= 3}
            >
              图片{pendingFiles.length > 0 ? ` ${pendingFiles.length}/3` : ""}
            </button>
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={customer ? "输入问题，可附 3 张图片…" : "请先登录"}
              disabled={!customer || busy}
              maxLength={500}
              aria-label="聊天输入"
              data-testid="chat-input"
            />
            <button
              className="btn btn-primary btn-sm"
              type="submit"
              data-testid="chat-send"
              disabled={
                !customer ||
                busy ||
                (!input.trim() && pendingFiles.length === 0)
              }
            >
              {busy ? "发送中" : "发送"}
            </button>
          </form>
          {error ? <div className="chat-error">{error}</div> : null}
        </section>
      ) : null}
    </>
  );
}
