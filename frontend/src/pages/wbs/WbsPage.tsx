// UI-WBS-001 WBS（ツリー図・アウトライン）（設計書 7.1 のルート /projects/:projectId/wbs。
// WBS-01・02・04・05・06・08、EST-01・02）。
// 木（API-WBS-001：集計値・改番待ち・検査の件数付き）を、ツリー図とアウトライン（表）の 2 つの表示で切り替えて出す（WBS-04）。
// どちらの表示でも要素を選ぶと同じ編集の部品（ElementEditor）が開く。要素を足す（API-WBS-002）・採番と改番（NumberingPanel）も置く。
// 変える部品は wbs.edit、見積は estimate.edit を持つ人にだけ出す（7.2。`GET /api/v1/me`）。正の判定はサーバが行う
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { createElement, getWbsTree, type ElementKind, type WbsElement, type WbsTree } from "../../api/wbs";
import ErrorAlert from "../../components/ErrorAlert";
import StatusBadge, { WBS_STATUS_TONES } from "../../components/StatusBadge";
import { useProjectMembers } from "../chg/useProjectMembers";
import { parseProjectId } from "../prj/ProjectSettingsPage";
import ElementEditor from "./ElementEditor";
import {
  ELEMENT_KIND_LABELS,
  ELEMENT_KINDS,
  ROLLUP_SOURCE_LABELS,
  WBS_STATUS_LABELS,
  hoursText,
  labelOf,
  responsibleText,
} from "./labels";
import NumberingPanel from "./NumberingPanel";

export type WbsView = "outline" | "tree";

/** 最上位の親の選択肢の値 */
const TOP = "";

interface TreeNode {
  element: WbsElement;
  children: TreeNode[];
}

/** 先行順の平らな配列を木にする（親が見えない要素は最上位に置く：委託先の絞り込みで親が欠けることがある） */
export function buildTree(elements: WbsElement[]): TreeNode[] {
  const nodes = new Map<number, TreeNode>(elements.map((e) => [e.id, { element: e, children: [] }]));
  const roots: TreeNode[] = [];
  for (const e of elements) {
    const node = nodes.get(e.id)!;
    const parent = e.parent_id === null ? undefined : nodes.get(e.parent_id);
    if (parent === undefined) {
      roots.push(node);
    } else {
      parent.children.push(node);
    }
  }
  return roots;
}

function TreeBranch({
  nodes,
  level,
  selectedId,
  onSelect,
}: {
  nodes: TreeNode[];
  level: number;
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <ul role={level === 1 ? "tree" : "group"} aria-label={level === 1 ? "WBS のツリー図" : undefined}>
      {nodes.map(({ element: e, children }) => (
        <li
          key={e.id}
          role="treeitem"
          aria-level={level}
          aria-selected={selectedId === e.id}
          aria-expanded={children.length > 0 ? true : undefined}
          data-kind={e.kind}
        >
          <button type="button" onClick={() => onSelect(e.id)}>
            {e.wbs_code} {e.name}
          </button>
          <span>
            {" "}
            {ELEMENT_KIND_LABELS[e.kind]}・{hoursText(e.rollup_hours)}
            {e.needs_renumber && "・改番待ち"}
          </span>
          {children.length > 0 && <TreeBranch nodes={children} level={level + 1} selectedId={selectedId} onSelect={onSelect} />}
        </li>
      ))}
    </ul>
  );
}

export default function WbsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [me, setMe] = useState<Me | null>(null);
  const [tree, setTree] = useState<WbsTree | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [view, setView] = useState<WbsView>("outline");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<ErrorView | null>(null);
  const [addParent, setAddParent] = useState(TOP);
  const [addKind, setAddKind] = useState<ElementKind>("DELIVERABLE");
  const [addName, setAddName] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<ErrorView | null>(null);
  const { members, names } = useProjectMembers(projectId);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getMe().then(
      (result) => {
        if (!cancelled) {
          setMe(result);
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
  }, [projectId]);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    getWbsTree(projectId).then(
      (result) => {
        if (!cancelled) {
          setTree(result);
          // 消した要素を選んだままにしない
          setSelectedId((current) => (current !== null && result.elements.some((e) => e.id === current) ? current : null));
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
  }, [projectId, reloadKey]);

  const roots = useMemo(() => (tree === null ? [] : buildTree(tree.elements)), [tree]);
  const selected = tree?.elements.find((e) => e.id === selectedId) ?? null;

  if (projectId === null) {
    return (
      <section data-screen-id="UI-WBS-001" className="wbs-page">
        <h2>WBS</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const canEdit = me !== null && hasProjectPermission(me, projectId, "wbs.edit");
  const canEstimate = me !== null && hasProjectPermission(me, projectId, "estimate.edit");
  // 子を持てるのは WP 以外（WBS-02）
  const parentChoices = tree?.elements.filter((e) => e.kind !== "WORK_PACKAGE") ?? [];

  function reload(message: string) {
    setNotice(message);
    setError(null);
    setReloadKey((k) => k + 1);
  }

  function select(id: number) {
    setSelectedId(id);
    // 選んだ要素の下へ足せるなら、追加の親の既定にする
    const element = tree?.elements.find((e) => e.id === id);
    if (element !== undefined && element.kind !== "WORK_PACKAGE") {
      setAddParent(String(id));
    }
  }

  async function handleAdd(event: FormEvent) {
    event.preventDefault();
    if (projectId === null) {
      return;
    }
    setAddError(null);
    if (addName.trim() === "") {
      setAddError({ message: "名称を入力してください", details: [] });
      return;
    }
    setAdding(true);
    try {
      const created = await createElement(projectId, {
        parent_id: addParent === TOP ? null : Number(addParent),
        kind: addKind,
        name: addName.trim(),
      });
      setAddName("");
      setSelectedId(created.id);
      reload(`「${created.wbs_code} ${created.name}」を足した`);
    } catch (e) {
      setAddError(toErrorView(e));
    } finally {
      setAdding(false);
    }
  }

  return (
    <section data-screen-id="UI-WBS-001" className="wbs-page">
      <h2>WBS</h2>
      <nav aria-label="行き先" className="page-links">
        <Link to="/projects">プロジェクト一覧へ</Link>
        <Link to={`/projects/${projectId}/wbs-review`}>検証・レビュー・承認</Link>
        <Link to={`/projects/${projectId}/responsibility`}>責任分担表</Link>
        <Link to={`/projects/${projectId}/activities`}>タスクリスト</Link>
      </nav>
      <ErrorAlert error={error} />
      {notice !== null && <p role="status">{notice}</p>}

      {tree !== null && (
        <dl aria-label="WBS の概要" className="wbs-summary">
          <dt>状態</dt>
          <dd>
            <StatusBadge tone={WBS_STATUS_TONES[tree.wbs_status]}>{labelOf(WBS_STATUS_LABELS, tree.wbs_status)}</StatusBadge>
          </dd>
          <dt>プロジェクトの総見積</dt>
          <dd>{hoursText(tree.project_estimate_hours)}</dd>
          <dt>改番待ち</dt>
          <dd>{tree.renumber_pending} 件</dd>
          <dt>検査</dt>
          <dd>
            エラー {tree.validation.errors} 件・警告 {tree.validation.warnings} 件・完了条件の欠け {tree.validation.missing_completion} 件
          </dd>
        </dl>
      )}
      {tree?.restricted === true && <p className="note">自分の割当の経路の要素だけを表示している</p>}

      <fieldset className="view-switch">
        <legend>表示</legend>
        <label>
          <input type="radio" name="wbs-view" checked={view === "outline"} onChange={() => setView("outline")} />
          アウトライン
        </label>
        <label>
          <input type="radio" name="wbs-view" checked={view === "tree"} onChange={() => setView("tree")} />
          ツリー図
        </label>
      </fieldset>

      {tree !== null && tree.elements.length === 0 && <p>要素はまだありません</p>}
      {tree !== null && tree.elements.length > 0 && view === "outline" && (
        <table aria-label="WBS のアウトライン">
          <thead>
            <tr>
              <th scope="col">WBS ID</th>
              <th scope="col">名称</th>
              <th scope="col">区分</th>
              <th scope="col">自分の見積</th>
              <th scope="col">集計値</th>
              <th scope="col">責任者</th>
              <th scope="col">改番待ち</th>
              <th scope="col">操作</th>
            </tr>
          </thead>
          <tbody>
            {tree.elements.map((e) => (
              <tr key={e.id} data-selected={selectedId === e.id ? "true" : undefined} data-depth={e.depth}>
                <td>{e.wbs_code}</td>
                <td style={{ paddingLeft: `${(e.depth - 1) * 1.5}em` }}>{e.name}</td>
                <td>{ELEMENT_KIND_LABELS[e.kind]}</td>
                <td>{hoursText(e.own_estimate_hours)}</td>
                <td>
                  {hoursText(e.rollup_hours)}（{labelOf(ROLLUP_SOURCE_LABELS, e.rollup_source)}）
                </td>
                <td>{responsibleText(e.responsible_user_id, e.responsible_team, names)}</td>
                <td>{e.needs_renumber ? "改番待ち" : ""}</td>
                <td>
                  <button type="button" aria-label={`${e.wbs_code} を選ぶ`} onClick={() => select(e.id)}>
                    選ぶ
                  </button>
                  <Link to={`/projects/${projectId}/wbs/${e.id}`} aria-label={`${e.wbs_code} の辞書`}>
                    辞書
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tree !== null && tree.elements.length > 0 && view === "tree" && (
        <TreeBranch nodes={roots} level={1} selectedId={selectedId} onSelect={select} />
      )}

      {selected !== null && (canEdit || canEstimate) && tree !== null && (
        <ElementEditor
          projectId={projectId}
          element={selected}
          elements={tree.elements}
          members={members}
          canEdit={canEdit}
          canEstimate={canEstimate}
          onChanged={reload}
          onDeleted={(message) => {
            setSelectedId(null);
            setAddParent(TOP);
            reload(message);
          }}
        />
      )}
      {selected !== null && (
        <p>
          <Link to={`/projects/${projectId}/wbs/${selected.id}`}>「{selected.wbs_code} {selected.name}」の辞書・見積の履歴・横断照会へ</Link>
        </p>
      )}

      {canEdit && (
        <form aria-label="要素を足す" onSubmit={(e) => void handleAdd(e)} className="add-element-form">
          <h3>要素を足す</h3>
          <p>WBS ID は採番規則から自動で付く（WBS-05）。ワークパッケージの下には足せない。</p>
          <label>
            親
            <select value={addParent} onChange={(e) => setAddParent(e.target.value)}>
              <option value={TOP}>（最上位）</option>
              {parentChoices.map((e) => (
                <option key={e.id} value={String(e.id)}>
                  {e.wbs_code} {e.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            区分
            <select value={addKind} onChange={(e) => setAddKind(e.target.value as ElementKind)}>
              {ELEMENT_KINDS.map((k) => (
                <option key={k} value={k}>
                  {ELEMENT_KIND_LABELS[k]}
                </option>
              ))}
            </select>
          </label>
          <label>
            名称
            <input value={addName} maxLength={200} onChange={(e) => setAddName(e.target.value)} />
          </label>
          <ErrorAlert error={addError} />
          <button type="submit" disabled={adding}>
            足す
          </button>
        </form>
      )}

      {tree !== null && (
        <NumberingPanel
          projectId={projectId}
          rule={tree.numbering_rule}
          renumberPending={tree.renumber_pending}
          canEdit={canEdit}
          onChanged={reload}
          reloadKey={reloadKey}
        />
      )}
    </section>
  );
}
