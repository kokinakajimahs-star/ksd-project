// ログインの状態（設計書 6.1：Bearer トークン。API-CMN-001 で得て、API-CMN-002 で失効させる）。
// トークンはタブを閉じるまで sessionStorage に置く（docs/decisions/ASSUMPTIONS.md A-25）
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { setAuthToken, setUnauthorizedHandler } from "../api/client";
import type { LoginResponse, User } from "../api/cmn";

export interface Session {
  token: string;
  expiresAt: string;
  user: User;
}

export const SESSION_STORAGE_KEY = "pmt.session";

function isSession(value: unknown): value is Session {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const v = value as Record<string, unknown>;
  return (
    typeof v.token === "string" &&
    v.token !== "" &&
    typeof v.expiresAt === "string" &&
    typeof v.user === "object" &&
    v.user !== null
  );
}

/** 覚えたログインを読む。形が違う・有効期限を過ぎたものは消して null を返す */
export function loadStoredSession(storage: Storage, now: Date): Session | null {
  const text = storage.getItem(SESSION_STORAGE_KEY);
  if (text === null) {
    return null;
  }
  try {
    const value: unknown = JSON.parse(text);
    if (isSession(value) && Date.parse(value.expiresAt) > now.getTime()) {
      return value;
    }
  } catch {
    // 壊れた値は下で消す
  }
  storage.removeItem(SESSION_STORAGE_KEY);
  return null;
}

interface SessionContextValue {
  session: Session | null;
  /** ログインの応答（API-CMN-001）を覚える */
  signIn: (response: LoginResponse) => void;
  /** この端末のログインを忘れる（サーバのトークンの失効は API-CMN-002 で別に行う） */
  signOut: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

function browserStorage(): Storage {
  return window.sessionStorage;
}

export function SessionProvider({ children, storage }: { children: ReactNode; storage?: Storage }) {
  const store = storage ?? browserStorage();
  // 子の画面の副作用（API の呼び出し）は親の副作用より先に走るので、トークンは描画の前に渡しておく
  const [session, setSession] = useState<Session | null>(() => {
    const stored = loadStoredSession(store, new Date());
    setAuthToken(stored?.token ?? null);
    return stored;
  });

  const signIn = useCallback(
    (response: LoginResponse) => {
      const next: Session = { token: response.access_token, expiresAt: response.expires_at, user: response.user };
      store.setItem(SESSION_STORAGE_KEY, JSON.stringify(next));
      setAuthToken(next.token);
      setSession(next);
    },
    [store],
  );

  const signOut = useCallback(() => {
    store.removeItem(SESSION_STORAGE_KEY);
    setAuthToken(null);
    setSession(null);
  }, [store]);

  // トークンを付けた要求が 401（失効・期限切れ）なら、ログインを忘れてログインの画面へ戻す
  useEffect(() => {
    setUnauthorizedHandler(signOut);
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  const value = useMemo(() => ({ session, signIn, signOut }), [session, signIn, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession は SessionProvider の中で使う");
  }
  return value;
}

/** ログインの画面へ戻すときに、戻り先として渡す状態 */
export interface LoginRedirectState {
  from: string;
}

/** ログインしていなければログインの画面（UI-CMN-001）へ送り、ログインの後に元の画面へ戻す */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { session } = useSession();
  const location = useLocation();
  if (session === null) {
    const state: LoginRedirectState = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/login" replace state={state} />;
  }
  return <>{children}</>;
}
