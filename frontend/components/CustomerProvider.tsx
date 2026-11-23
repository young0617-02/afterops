"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError } from "@/lib/api";
import type { Customer } from "@/lib/types";

type CustomerContextValue = {
  customer: Customer | null;
  loading: boolean;
  login: (phone: string, code: string, name?: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const CustomerContext = createContext<CustomerContextValue | null>(null);

export function CustomerProvider({ children }: { children: ReactNode }) {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const me = await api<Customer>("/api/store/me");
      setCustomer(me);
    } catch (error) {
      if (error instanceof ApiError && error.status !== 401) {
        // Keep the header quiet; pages surface their own request errors.
      }
      setCustomer(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (phone: string, code: string, name?: string) => {
    const me = await api<Customer>("/api/store/session", {
      method: "POST",
      body: JSON.stringify({ phone, code, name: name || undefined }),
    });
    setCustomer(me);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/api/store/session", { method: "DELETE" });
    } finally {
      setCustomer(null);
    }
  }, []);

  const value = useMemo(
    () => ({ customer, loading, login, logout, refresh }),
    [customer, loading, login, logout, refresh],
  );

  return (
    <CustomerContext.Provider value={value}>
      {children}
    </CustomerContext.Provider>
  );
}

export function useCustomer(): CustomerContextValue {
  const context = useContext(CustomerContext);
  if (!context) {
    throw new Error("useCustomer must be used within CustomerProvider");
  }
  return context;
}
