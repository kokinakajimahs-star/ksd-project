// 共有リンクを作る・失効させる（API-RPT-002・003。RPT-03）。share.manage を持つ人にだけ出す（7.2。サーバでも必ず判定する）。
// トークンは作った応答でだけ返る（サーバはハッシュだけを持つ）ので、リンクはこの画面で作ったときにだけ見せる。
// 共有リンクの一覧の API は 6.2 に無い（OQ-36 (7)）ので、失効させられるのはこの画面で作ったリンクだけ
import { useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import { createShareLink, revokeShareLink, type ShareLinkCreated, type ShareScope } from "../../api/rpt";
import ErrorAlert from "../../components/ErrorAlert";
import { formatDateTime } from "../../lib/format";
import { localDateTimeToIso, SHARE_SCOPE_LABELS, SHARE_SCOPES } from "./labels";

interface CreatedLink {
  link: ShareLinkCreated;
  /** 失効させたら真（失効させたリンクの URL は出さない） */
  revoked: boolean;
}

/** 共有ビュー（UI-RPT-002。ルート /shared/:token）の URL */
export function sharedViewUrl(origin: string, token: string): string {
  return `${origin}/shared/${encodeURIComponent(token)}`;
}

export default function ShareLinkPanel({ projectId }: { projectId: number }) {
  const [scope, setScope] = useState<ShareScope>("REPORT");
  const [expiresAt, setExpiresAt] = useState("");
  const [created, setCreated] = useState<CreatedLink[]>([]);
  const [confirmingId, setConfirmingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const iso = localDateTimeToIso(expiresAt);
    if (iso === null) {
      setError({ message: "有効期限を入力してください", details: [] });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const link = await createShareLink(projectId, { scope, expires_at: iso });
      setCreated((links) => [{ link, revoked: false }, ...links]);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(linkId: number) {
    setBusy(true);
    setError(null);
    try {
      await revokeShareLink(projectId, linkId);
      setCreated((links) => links.map((c) => (c.link.id === linkId ? { ...c, revoked: true } : c)));
      setConfirmingId(null);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="share-heading" className="share-links no-print">
      <h3 id="share-heading">共有リンク</h3>
      <p className="note">
        ログインしない関係者に、読むだけのリンクで見せる。見せるのはリンクを開いたときの最新の報告日・進捗で、この報告日に固定しない
      </p>
      <ErrorAlert error={error} />
      <form aria-label="共有リンクを作る" onSubmit={(e) => void handleCreate(e)}>
        <label>
          見せる範囲
          <select value={scope} onChange={(e) => setScope(e.target.value as ShareScope)}>
            {SHARE_SCOPES.map((s) => (
              <option key={s} value={s}>
                {SHARE_SCOPE_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          有効期限
          <input
            type="datetime-local"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
            required
          />
        </label>
        <button type="submit" disabled={busy}>
          共有リンクを作る
        </button>
      </form>

      {created.length > 0 && (
        <>
          <p className="note">
            リンクは作った今だけ表示する（サーバはリンクの鍵を保存しないため、画面を離れると二度と表示できない）。
            作った共有リンクの一覧は出せないので、失効させられるのはこの画面で作ったリンクだけ
          </p>
          <table aria-label="この画面で作った共有リンク">
            <thead>
              <tr>
                <th scope="col">見せる範囲</th>
                <th scope="col">有効期限</th>
                <th scope="col">リンク</th>
                <th scope="col">状態</th>
              </tr>
            </thead>
            <tbody>
              {created.map(({ link, revoked }) => (
                <tr key={link.id} data-share-link-id={link.id}>
                  <td>{SHARE_SCOPE_LABELS[link.scope]}</td>
                  <td>{formatDateTime(link.expires_at)}</td>
                  <td>
                    {revoked ? (
                      "—"
                    ) : (
                      <input
                        type="text"
                        readOnly
                        aria-label={`共有リンクの URL（${link.id}）`}
                        value={sharedViewUrl(window.location.origin, link.token)}
                        onFocus={(e) => e.currentTarget.select()}
                      />
                    )}
                  </td>
                  <td>
                    {revoked ? (
                      "失効済み"
                    ) : confirmingId === link.id ? (
                      <span role="group" aria-label="失効の確認">
                        失効させると戻せません。{" "}
                        <button type="button" onClick={() => void handleRevoke(link.id)} disabled={busy}>
                          失効させる
                        </button>{" "}
                        <button type="button" onClick={() => setConfirmingId(null)} disabled={busy}>
                          やめる
                        </button>
                      </span>
                    ) : (
                      <>
                        有効{" "}
                        <button type="button" onClick={() => setConfirmingId(link.id)} disabled={busy}>
                          失効させる…
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}
