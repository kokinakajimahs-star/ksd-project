// 共有リンクを作る・一覧にする・失効させる（API-RPT-002・005・003。RPT-03）。share.manage を持つ人にだけ出す
// （7.2。サーバでも必ず判定する）。トークンは作った応答でだけ返る（サーバはハッシュだけを持ち、一覧にも載らない）ので、
// リンクの URL はこの画面で作ったときにだけ見せる。一覧（失効・期限切れも含む）の有効なリンクは失効させられる
import { useEffect, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import {
  createShareLink,
  listShareLinks,
  revokeShareLink,
  type ShareLinkCreated,
  type ShareLinkList,
  type ShareScope,
} from "../../api/rpt";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import {
  localDateTimeToIso,
  SHARE_LINK_STATE_LABELS,
  SHARE_SCOPE_LABELS,
  SHARE_SCOPES,
  shareLinkState,
} from "./labels";

/** 一覧の 1 頁の件数（6.1：limit の既定 100） */
export const SHARE_LINKS_PAGE_SIZE = 100;

interface CreatedLink {
  link: ShareLinkCreated;
  /** 失効させたら真（失効させたリンクの URL は出さない） */
  revoked: boolean;
}

/** 共有ビュー（UI-RPT-002。ルート /shared/:token）の URL */
export function sharedViewUrl(origin: string, token: string): string {
  return `${origin}/shared/${encodeURIComponent(token)}`;
}

/** 失効させるボタン。戻せないので確認の段を 1 つ挟む。onRevoke は失効できたら真を返す */
function RevokeControl({
  linkId,
  idleText,
  busy,
  onRevoke,
}: {
  linkId: number;
  /** 確認の前にボタンの前へ出す文言（状態など） */
  idleText?: string;
  busy: boolean;
  onRevoke: (linkId: number) => Promise<boolean>;
}) {
  const [confirming, setConfirming] = useState(false);

  async function handleRevoke() {
    if (await onRevoke(linkId)) {
      setConfirming(false);
    }
  }

  if (confirming) {
    return (
      <span role="group" aria-label="失効の確認">
        失効させると戻せません。{" "}
        <button type="button" onClick={() => void handleRevoke()} disabled={busy}>
          失効させる
        </button>{" "}
        <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
          やめる
        </button>
      </span>
    );
  }
  return (
    <>
      {idleText !== undefined && `${idleText} `}
      <button type="button" onClick={() => setConfirming(true)} disabled={busy}>
        失効させる…
      </button>
    </>
  );
}

export default function ShareLinkPanel({ projectId }: { projectId: number }) {
  const [scope, setScope] = useState<ShareScope>("REPORT");
  const [expiresAt, setExpiresAt] = useState("");
  const [created, setCreated] = useState<CreatedLink[]>([]);
  /** 一覧と、読んだ時刻（状態の期限切れはこの時刻で決める） */
  const [listed, setListed] = useState<{ page: ShareLinkList; now: number } | null>(null);
  const [listOffset, setListOffset] = useState(0);
  const [listKey, setListKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    let cancelled = false;
    listShareLinks(projectId, { limit: SHARE_LINKS_PAGE_SIZE, offset: listOffset }).then(
      (page) => {
        if (!cancelled) {
          setListed({ page, now: Date.now() });
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, listOffset, listKey]);

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
      setListKey((k) => k + 1);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  async function handleRevoke(linkId: number): Promise<boolean> {
    setBusy(true);
    setError(null);
    try {
      await revokeShareLink(projectId, linkId);
      setCreated((links) => links.map((c) => (c.link.id === linkId ? { ...c, revoked: true } : c)));
      return true;
    } catch (e: unknown) {
      setError(toErrorView(e));
      return false;
    } finally {
      setBusy(false);
      // 失効させた後も、失効の失敗（他の人が先に失効させた 409 など）の後も一覧を読み直す
      setListKey((k) => k + 1);
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
            リンクは作った今だけ表示する（サーバはリンクの鍵を保存しないため、画面を離れると二度と表示できない。下の一覧にも出ない）
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
                    ) : (
                      <RevokeControl linkId={link.id} idleText="有効" busy={busy} onRevoke={handleRevoke} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h4>共有リンクの一覧</h4>
      {listed !== null && listed.page.items.length === 0 && <p>共有リンクはまだありません</p>}
      {listed !== null && listed.page.items.length > 0 && (
        <>
          <table aria-label="共有リンクの一覧">
            <thead>
              <tr>
                <th scope="col">見せる範囲</th>
                <th scope="col">有効期限</th>
                <th scope="col">状態</th>
                <th scope="col">作った日時</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {listed.page.items.map((link) => {
                const state = shareLinkState(link, listed.now);
                return (
                  <tr key={link.id} data-share-link-id={link.id} data-state={state}>
                    <td>{SHARE_SCOPE_LABELS[link.scope]}</td>
                    <td>{formatDateTime(link.expires_at)}</td>
                    <td>
                      {SHARE_LINK_STATE_LABELS[state]}
                      {link.revoked_at !== null && `（${formatDateTime(link.revoked_at)}）`}
                    </td>
                    <td>{formatDateTime(link.created_at)}</td>
                    <td>
                      {state === "ACTIVE" ? (
                        <RevokeControl linkId={link.id} busy={busy} onRevoke={handleRevoke} />
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pager
            total={listed.page.total}
            limit={listed.page.limit}
            offset={listed.page.offset}
            onChange={setListOffset}
          />
        </>
      )}
    </section>
  );
}
