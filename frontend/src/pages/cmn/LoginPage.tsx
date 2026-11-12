// UI-CMN-001 ログイン（設計書 7.1 のルート /login。API-CMN-001）。
// ログインの後は、ログインを求められた元の画面へ、無ければプロジェクト一覧（7.2）へ進む
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { login } from "../../api/cmn";
import { describeError } from "../../api/errors";
import { useSession, type LoginRedirectState } from "../../auth/session";
import { LOGIN_ID_MAX, PASSWORD_MAX } from "./limits";

export const AFTER_LOGIN_PATH = "/projects";

function redirectTarget(state: unknown): string {
  const from = (state as Partial<LoginRedirectState> | null)?.from;
  // 自分のアプリの中のパスだけを戻り先にする（"//host" のような外への遷移を受けない）
  if (typeof from === "string" && from.startsWith("/") && !from.startsWith("//") && from !== "/login") {
    return from;
  }
  return AFTER_LOGIN_PATH;
}

export default function LoginPage() {
  const { session, signIn } = useSession();
  const location = useLocation();
  const navigate = useNavigate();
  const target = redirectTarget(location.state);

  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (session !== null && !submitting) {
    return <Navigate to={target} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await login(loginId, password);
      signIn(response);
      navigate(target, { replace: true });
    } catch (e: unknown) {
      setError(describeError(e));
      setPassword("");
      setSubmitting(false);
    }
  }

  return (
    <section data-screen-id="UI-CMN-001" className="login-page">
      <h2>ログイン</h2>
      <form onSubmit={handleSubmit} aria-label="ログイン">
        <div>
          <label htmlFor="login-id">ログイン名</label>
          <input
            id="login-id"
            name="login_id"
            autoComplete="username"
            required
            maxLength={LOGIN_ID_MAX}
            value={loginId}
            onChange={(e) => setLoginId(e.target.value)}
          />
        </div>
        <div>
          <label htmlFor="login-password">パスワード</label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={PASSWORD_MAX}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error !== null && <p role="alert">{error}</p>}
        <button type="submit" disabled={submitting}>
          ログイン
        </button>
      </form>
    </section>
  );
}
