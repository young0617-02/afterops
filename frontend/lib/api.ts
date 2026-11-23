import type { ApiErrorBody } from "./types";

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = `${name}=`;
  const row = document.cookie
    .split("; ")
    .find((item) => item.startsWith(prefix));
  return row ? decodeURIComponent(row.slice(prefix.length)) : "";
}

function withCsrfHeaders(headers: Headers, method: string): void {
  if (!UNSAFE_METHODS.has(method)) return;
  if (!headers.has("X-CSRF-Token")) {
    const token = readCookie("afterops_csrf");
    if (token) headers.set("X-CSRF-Token", token);
  }
  if (!headers.has("X-Admin-CSRF-Token")) {
    const token = readCookie("afterops_admin_csrf");
    if (token) headers.set("X-Admin-CSRF-Token", token);
  }
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, message: string, code = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (
    init.body &&
    !(init.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }
  withCsrfHeaders(headers, (init.method ?? "GET").toUpperCase());
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const body = (payload ?? {}) as ApiErrorBody;
    throw new ApiError(
      response.status,
      typeof body.detail === "string" ? body.detail : response.statusText,
      body.code ?? "",
    );
  }
  return payload as T;
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "请求失败，请稍后重试";
}
