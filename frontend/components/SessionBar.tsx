"use client";

import { useState, type FormEvent } from "react";

import { api, errorMessage } from "@/lib/api";
import type { OtpResponse } from "@/lib/types";
import { NotificationBell } from "./NotificationBell";
import { useCustomer } from "./CustomerProvider";

export function SessionBar() {
  const { customer, loading, login, logout } = useCustomer();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<"phone" | "code">("phone");
  const [debugCode, setDebugCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (loading) {
    return <div className="session-muted">检查会话…</div>;
  }

  if (customer) {
    return (
      <div className="session">
        <span className="session-name">{customer.name ?? "顾客"}</span>
        <span className="session-phone">{customer.phone}</span>
        <NotificationBell scope="customer" />
        <button
          className="btn btn-ghost btn-sm"
          type="button"
          onClick={() => void logout()}
        >
          退出
        </button>
      </div>
    );
  }

  const requestCode = async () => {
    if (phone.trim().length !== 11) return;
    setBusy(true);
    setError("");
    try {
      const response = await api<OtpResponse>("/api/store/otp/request", {
        method: "POST",
        body: JSON.stringify({ phone: phone.trim() }),
      });
      setStage("code");
      setDebugCode(response.debug_code ?? null);
      setCode("");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (stage === "phone") {
      await requestCode();
      return;
    }
    setBusy(true);
    setError("");
    try {
      await login(phone.trim(), code.trim());
      setPhone("");
      setCode("");
      setStage("phone");
      setDebugCode(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setStage("phone");
    setCode("");
    setDebugCode(null);
    setError("");
  };

  return (
    <form
      className={`session-login ${stage === "code" ? "session-login-code" : ""}`}
      onSubmit={submit}
    >
      {stage === "phone" ? (
        <>
          <input
            value={phone}
            onChange={(event) => setPhone(event.target.value)}
            placeholder="手机号登录"
            inputMode="tel"
            maxLength={11}
            aria-label="手机号"
            data-testid="login-phone"
          />
          <button
            className="btn btn-primary btn-sm"
            type="submit"
            data-testid="otp-request"
            disabled={busy || phone.trim().length !== 11}
          >
            {busy ? "发送中" : "获取验证码"}
          </button>
        </>
      ) : (
        <>
          <span className="session-otp-target">{phone}</span>
          <input
            value={code}
            onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
            placeholder="6 位验证码"
            inputMode="numeric"
            maxLength={6}
            aria-label="验证码"
            data-testid="login-code"
          />
          <button
            className="btn btn-primary btn-sm"
            type="submit"
            data-testid="login-submit"
            disabled={busy || code.trim().length !== 6}
          >
            {busy ? "登录中" : "登录"}
          </button>
          <button
            className="btn btn-ghost btn-sm"
            type="button"
            onClick={reset}
            disabled={busy}
          >
            换号
          </button>
          {debugCode ? (
            <span className="otp-debug">本地验证码：{debugCode}</span>
          ) : null}
        </>
      )}
      {error ? <span className="field-error">{error}</span> : null}
    </form>
  );
}
