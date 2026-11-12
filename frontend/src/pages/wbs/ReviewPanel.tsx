// UI-WBS-003 のレビューの部品（WBS-20）：レビューの一覧（API-WBS-025。新しい順）・始める（API-WBS-014。wbs.edit）・
// 読む（API-WBS-015）・閉じる（API-WBS-024。wbs.edit。WBS は作成中に戻り、指摘は残る）・
// 指摘を書く（API-WBS-016。wbs.review。開いているレビューだけ）・指摘の状態と対応を更新する（API-WBS-017。wbs.edit。
// 閉じたレビューの指摘も：閉じても指摘は残り、申請の前提の未対応の指摘に数えるので、閉じた後にも対応できるようにする）。
// 開いているレビューは画面の URL（`?review=<ID>`）で持つ。始めたレビューはその ID へ進み、別のレビューは一覧から開く
import { useEffect, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import type { Member } from "../../api/prj";
import {
  addReviewComment,
  closeReview,
  getReview,
  listReviews,
  startReview,
  updateReviewComment,
  type CommentCategory,
  type CommentStatus,
  type Review,
  type ReviewComment,
  type ReviewList,
  type WbsElement,
} from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import { formatDateTime } from "../../lib/format";
import {
  COMMENT_CATEGORY_LABELS,
  COMMENT_STATUS_LABELS,
  REVIEW_STATUS_LABELS,
  labelOf,
} from "./labels";

const WHOLE = "";

export const REVIEWS_PAGE_SIZE = 50;

export interface ReviewPanelProps {
  projectId: number;
  /** 開いているレビュー（URL の review）。無ければ null */
  reviewId: number | null;
  onOpen: (reviewId: number) => void;
  elements: WbsElement[];
  members: Member[];
  names: ReadonlyMap<number, string>;
  canEdit: boolean;
  canReview: boolean;
  /** 画面が読み直すたびに増える（承認の申請でレビューが終わる など。一覧と開いているレビューを読み直す） */
  reloadKey: number;
  /** レビューを始めた・閉じた・指摘を変えた後（WBS の状態・検査の件数を読み直す） */
  onChanged: () => void;
}

function CommentRow({
  comment,
  target,
  author,
  editable,
  onSave,
}: {
  comment: ReviewComment;
  target: string;
  author: string;
  editable: boolean;
  onSave: (comment: ReviewComment, status: CommentStatus, resolution: string) => Promise<void>;
}) {
  const [status, setStatus] = useState<CommentStatus>(comment.status);
  const [resolution, setResolution] = useState(comment.resolution ?? "");

  useEffect(() => {
    setStatus(comment.status);
    setResolution(comment.resolution ?? "");
  }, [comment]);

  return (
    <tr data-status={comment.status}>
      <td>{target}</td>
      <td>{COMMENT_CATEGORY_LABELS[comment.category] ?? comment.category}</td>
      <td>{comment.body}</td>
      <td>
        {editable ? (
          <select
            aria-label={`指摘 #${comment.id} の状態`}
            value={status}
            onChange={(e) => setStatus(e.target.value as CommentStatus)}
          >
            {(Object.keys(COMMENT_STATUS_LABELS) as CommentStatus[]).map((s) => (
              <option key={s} value={s}>
                {COMMENT_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        ) : (
          (COMMENT_STATUS_LABELS[comment.status] ?? comment.status)
        )}
      </td>
      <td>
        {editable ? (
          <input
            aria-label={`指摘 #${comment.id} の対応`}
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
          />
        ) : (
          (comment.resolution ?? "—")
        )}
      </td>
      <td>{author}</td>
      <td>{formatDateTime(comment.updated_at)}</td>
      {editable && (
        <td>
          <button type="button" aria-label={`指摘 #${comment.id} を更新する`} onClick={() => void onSave(comment, status, resolution)}>
            更新する
          </button>
        </td>
      )}
    </tr>
  );
}

export default function ReviewPanel({
  projectId,
  reviewId,
  onOpen,
  elements,
  members,
  names,
  canEdit,
  canReview,
  reloadKey,
  onChanged,
}: ReviewPanelProps) {
  const [list, setList] = useState<ReviewList | null>(null);
  const [listOffset, setListOffset] = useState(0);
  const [review, setReview] = useState<Review | null>(null);
  const [reviewKey, setReviewKey] = useState(0);
  const [reviewers, setReviewers] = useState<Set<number>>(new Set());
  const [target, setTarget] = useState(WHOLE);
  const [category, setCategory] = useState<CommentCategory>("OMISSION");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

  useEffect(() => {
    let cancelled = false;
    listReviews(projectId, { limit: REVIEWS_PAGE_SIZE, offset: listOffset }).then(
      (result) => {
        if (!cancelled) {
          setList(result);
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
  }, [projectId, listOffset, reloadKey]);

  useEffect(() => {
    if (reviewId === null) {
      setReview(null);
      return;
    }
    let cancelled = false;
    getReview(projectId, reviewId).then(
      (result) => {
        if (!cancelled) {
          setReview(result);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setReview(null);
          setError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId, reviewId, reviewKey, reloadKey]);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(toErrorView(e));
    } finally {
      setBusy(false);
    }
  }

  function handleStart(event: FormEvent) {
    event.preventDefault();
    if (reviewers.size === 0) {
      setError({ message: "レビューを頼む人を 1 人以上選んでください", details: [] });
      return;
    }
    void run(async () => {
      const started = await startReview(
        projectId,
        members.filter((m) => reviewers.has(m.user_id)).map((m) => m.user_id),
      );
      setReviewers(new Set());
      onOpen(started.id);
      onChanged();
    });
  }

  function handleOpen(id: number) {
    setError(null);
    onOpen(id);
  }

  function handleClose() {
    if (review === null) {
      return;
    }
    const closing = review.id;
    void run(async () => {
      setReview(await closeReview(projectId, closing));
      onChanged();
    });
  }

  function handleComment(event: FormEvent) {
    event.preventDefault();
    if (review === null) {
      return;
    }
    if (body.trim() === "") {
      setError({ message: "指摘の内容を入力してください", details: [] });
      return;
    }
    void run(async () => {
      await addReviewComment(projectId, review.id, {
        element_id: target === WHOLE ? null : Number(target),
        category,
        body: body.trim(),
      });
      setBody("");
      setReviewKey((k) => k + 1);
      onChanged();
    });
  }

  async function handleSave(comment: ReviewComment, status: CommentStatus, resolution: string) {
    if (review === null) {
      return;
    }
    const trimmed = resolution.trim() === "" ? null : resolution.trim();
    const input: { status?: CommentStatus; resolution?: string | null } = {};
    if (status !== comment.status) {
      input.status = status;
    }
    if (trimmed !== comment.resolution) {
      input.resolution = trimmed;
    }
    if (Object.keys(input).length === 0) {
      setError({ message: "変えた項目がありません", details: [] });
      return;
    }
    await run(async () => {
      await updateReviewComment(projectId, review.id, comment.id, input);
      setReviewKey((k) => k + 1);
      onChanged();
    });
  }

  const elementLabel = new Map(elements.map((e) => [e.id, `${e.wbs_code} ${e.name}`]));
  const isOpen = review !== null && review.status === "OPEN";
  const personName = (id: number) => names.get(id) ?? `#${id}`;

  return (
    <section aria-labelledby="review-heading" className="review-panel">
      <h3 id="review-heading">レビュー</h3>
      <ErrorAlert error={error} />

      {canEdit && (
        <form aria-label="レビューを始める" onSubmit={handleStart}>
          <fieldset>
            <legend>レビューを頼む人</legend>
            {members.map((m) => (
              <label key={m.user_id}>
                <input
                  type="checkbox"
                  checked={reviewers.has(m.user_id)}
                  onChange={(e) =>
                    setReviewers((current) => {
                      const next = new Set(current);
                      if (e.target.checked) {
                        next.add(m.user_id);
                      } else {
                        next.delete(m.user_id);
                      }
                      return next;
                    })
                  }
                />
                {m.display_name}
              </label>
            ))}
          </fieldset>
          <button type="submit" disabled={busy}>
            レビューを始める
          </button>
        </form>
      )}

      {list !== null && list.items.length === 0 && <p>レビューはまだありません</p>}
      {list !== null && list.items.length > 0 && (
        <>
          <table aria-label="レビューの一覧">
            <thead>
              <tr>
                <th scope="col">レビュー</th>
                <th scope="col">状態</th>
                <th scope="col">依頼した人</th>
                <th scope="col">依頼した日時</th>
                <th scope="col">レビューする人</th>
                <th scope="col">終了した日時</th>
                <th scope="col">操作</th>
              </tr>
            </thead>
            <tbody>
              {list.items.map((r) => (
                <tr key={r.id} data-status={r.status} className={r.id === reviewId ? "selected" : undefined}>
                  <td>#{r.id}</td>
                  <td>{labelOf(REVIEW_STATUS_LABELS, r.status)}</td>
                  <td>{personName(r.requested_by)}</td>
                  <td>{formatDateTime(r.requested_at)}</td>
                  <td>{r.reviewer_ids.map(personName).join("、")}</td>
                  <td>{r.closed_at === null ? "—" : formatDateTime(r.closed_at)}</td>
                  <td>
                    <button type="button" aria-label={`レビュー #${r.id} を開く`} onClick={() => handleOpen(r.id)}>
                      開く
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Pager total={list.total} limit={list.limit} offset={list.offset} onChange={setListOffset} />
        </>
      )}

      {review === null && reviewId === null && <p>開いているレビューはありません（一覧から開くか、始める）</p>}
      {review !== null && (
        <>
          <h4>
            レビュー #{review.id}（{labelOf(REVIEW_STATUS_LABELS, review.status)}）
          </h4>
          <p>
            依頼：{personName(review.requested_by)}・{formatDateTime(review.requested_at)}
            {review.closed_at !== null && `・終了 ${formatDateTime(review.closed_at)}`}
          </p>
          <p>レビューする人：{review.reviewer_ids.map(personName).join("、")}</p>
          {canEdit && isOpen && (
            <p>
              <button type="button" disabled={busy} onClick={handleClose}>
                レビューを閉じる
              </button>{" "}
              閉じると WBS はレビュー中から作成中に戻る（指摘は残る）。
            </p>
          )}
          {review.comments.length === 0 && <p>指摘はまだありません</p>}
          {review.comments.length > 0 && (
            <table aria-label="指摘">
              <thead>
                <tr>
                  <th scope="col">対象</th>
                  <th scope="col">分類</th>
                  <th scope="col">内容</th>
                  <th scope="col">状態</th>
                  <th scope="col">対応</th>
                  <th scope="col">書いた人</th>
                  <th scope="col">更新した日時</th>
                  {canEdit && <th scope="col">操作</th>}
                </tr>
              </thead>
              <tbody>
                {review.comments.map((c) => (
                  <CommentRow
                    key={c.id}
                    comment={c}
                    target={c.element_id === null ? "WBS 全体" : (elementLabel.get(c.element_id) ?? `#${c.element_id}`)}
                    author={names.get(c.author_id) ?? `#${c.author_id}`}
                    editable={canEdit}
                    onSave={handleSave}
                  />
                ))}
              </tbody>
            </table>
          )}
          {canReview && isOpen && (
            <form aria-label="指摘を書く" onSubmit={handleComment}>
              <label>
                対象
                <select value={target} onChange={(e) => setTarget(e.target.value)}>
                  <option value={WHOLE}>WBS 全体</option>
                  {elements.map((e) => (
                    <option key={e.id} value={String(e.id)}>
                      {e.wbs_code} {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                分類
                <select value={category} onChange={(e) => setCategory(e.target.value as CommentCategory)}>
                  {(Object.keys(COMMENT_CATEGORY_LABELS) as CommentCategory[]).map((k) => (
                    <option key={k} value={k}>
                      {COMMENT_CATEGORY_LABELS[k]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                内容
                <textarea value={body} onChange={(e) => setBody(e.target.value)} />
              </label>
              <button type="submit" disabled={busy}>
                指摘を書く
              </button>
            </form>
          )}
        </>
      )}
    </section>
  );
}
