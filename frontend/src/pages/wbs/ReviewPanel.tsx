// UI-WBS-003 のレビューの部品（WBS-20）：レビューを始める（API-WBS-014。wbs.edit）・読む（API-WBS-015）・
// 指摘を書く（API-WBS-016。wbs.review）・指摘の状態と対応を更新する（API-WBS-017。wbs.edit）。
// レビューの一覧を読む API が 6.2 に無いので、開いているレビューは画面の URL（`?review=<ID>`）で持つ。
// 始めたレビューはその ID へ進み、別のレビューは番号で開く（OQ-41）
import { useEffect, useState, type FormEvent } from "react";

import { toErrorView, type ErrorView } from "../../api/errors";
import type { Member } from "../../api/prj";
import {
  addReviewComment,
  getReview,
  startReview,
  updateReviewComment,
  type CommentCategory,
  type CommentStatus,
  type Review,
  type ReviewComment,
  type WbsElement,
} from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import { formatDateTime } from "../../lib/format";
import {
  COMMENT_CATEGORY_LABELS,
  COMMENT_STATUS_LABELS,
  REVIEW_STATUS_LABELS,
  labelOf,
} from "./labels";

const WHOLE = "";

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
  /** レビューを始めた・指摘を変えた後（WBS の状態・検査の件数を読み直す） */
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
  onChanged,
}: ReviewPanelProps) {
  const [review, setReview] = useState<Review | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [reviewers, setReviewers] = useState<Set<number>>(new Set());
  const [openNumber, setOpenNumber] = useState("");
  const [target, setTarget] = useState(WHOLE);
  const [category, setCategory] = useState<CommentCategory>("OMISSION");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ErrorView | null>(null);

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
  }, [projectId, reviewId, reloadKey]);

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

  function handleOpen(event: FormEvent) {
    event.preventDefault();
    const trimmed = openNumber.trim();
    if (!/^[1-9]\d*$/.test(trimmed)) {
      setError({ message: "レビューの番号を入力してください", details: [] });
      return;
    }
    setError(null);
    onOpen(Number(trimmed));
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
      setReloadKey((k) => k + 1);
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
      setReloadKey((k) => k + 1);
      onChanged();
    });
  }

  const elementLabel = new Map(elements.map((e) => [e.id, `${e.wbs_code} ${e.name}`]));
  const isOpen = review !== null && review.status === "OPEN";

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

      <form aria-label="レビューを番号で開く" onSubmit={handleOpen}>
        <label>
          レビューの番号
          <input inputMode="numeric" value={openNumber} onChange={(e) => setOpenNumber(e.target.value)} />
        </label>
        <button type="submit">開く</button>
      </form>

      {review === null && reviewId === null && <p>開いているレビューはありません（始めるか、番号で開く）</p>}
      {review !== null && (
        <>
          <h4>
            レビュー #{review.id}（{labelOf(REVIEW_STATUS_LABELS, review.status)}）
          </h4>
          <p>
            依頼：{names.get(review.requested_by) ?? `#${review.requested_by}`}・{formatDateTime(review.requested_at)}
            {review.closed_at !== null && `・終了 ${formatDateTime(review.closed_at)}`}
          </p>
          <p>レビューする人：{review.reviewer_ids.map((id) => names.get(id) ?? `#${id}`).join("、")}</p>
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
                  {canEdit && isOpen && <th scope="col">操作</th>}
                </tr>
              </thead>
              <tbody>
                {review.comments.map((c) => (
                  <CommentRow
                    key={c.id}
                    comment={c}
                    target={c.element_id === null ? "WBS 全体" : (elementLabel.get(c.element_id) ?? `#${c.element_id}`)}
                    author={names.get(c.author_id) ?? `#${c.author_id}`}
                    editable={canEdit && isOpen}
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
