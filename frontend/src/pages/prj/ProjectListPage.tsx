// UI-PRJ-001 プロジェクト一覧（設計書 7.1 のルート /projects。API-PRJ-002・001、API-CMN-008・003）。
// 自分が見られるプロジェクトを並べ、7.2 の遷移の行き先（設定・WBS・タスクリスト…）へ進む。
// 作成（PRJ-01）の部品は project.create を持つ人にだけ出す（7.2。サーバでも判定する）
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { canCreateProject, getMe, hasProjectPermission, type Me, type Page, type UserListItem } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { createProject, listProjects, type Project } from "../../api/prj";
import ErrorAlert from "../../components/ErrorAlert";
import Pager from "../../components/Pager";
import StatusBadge, { WBS_STATUS_TONES } from "../../components/StatusBadge";
import { optionalText } from "../../lib/form";
import { wbsStatusLabel } from "./labels";
import { loadActiveUsers } from "./activeUsers";
import { PROJECT_CODE_MAX, PROJECT_NAME_MAX } from "./limits";
import OwnerSelect from "./OwnerSelect";

export const PROJECTS_PAGE_SIZE = 100;

interface Destination {
  label: string;
  path: (projectId: number) => string;
  /** どれか 1 つを持てば出す（無ければ project.view＝一覧に出る全員） */
  anyOf?: string[];
}

/** 7.2 の UI-PRJ-001 からの遷移（ルートは 7.1）。権限の要る画面は、その権限が無ければ出さない */
export const DESTINATIONS: readonly Destination[] = [
  { label: "設定", path: (id) => `/projects/${id}/settings` },
  { label: "WBS", path: (id) => `/projects/${id}/wbs` },
  { label: "タスクリスト", path: (id) => `/projects/${id}/activities` },
  { label: "ガント", path: (id) => `/projects/${id}/gantt` },
  { label: "ベースライン", path: (id) => `/projects/${id}/baselines` },
  { label: "工数", path: (id) => `/projects/${id}/effort`, anyOf: ["effort.view"] },
  { label: "変更要求", path: (id) => `/projects/${id}/change-requests` },
  { label: "変更履歴", path: (id) => `/projects/${id}/audit-logs`, anyOf: ["audit.view"] },
  { label: "入出力", path: (id) => `/projects/${id}/import-export`, anyOf: ["data.export", "data.import"] },
];

function formatPeriod(project: Project): string {
  return `${project.start_date}〜${project.target_finish_date ?? ""}`;
}

function ProjectCreateForm({
  me,
  ownerCandidates,
  onCreated,
}: {
  me: Me;
  ownerCandidates: UserListItem[] | null;
  onCreated: (project: Project) => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [requirements, setRequirements] = useState("");
  const [keyDeliverables, setKeyDeliverables] = useState("");
  const [startDate, setStartDate] = useState("");
  const [targetFinishDate, setTargetFinishDate] = useState("");
  const [ownerUserId, setOwnerUserId] = useState(me.user.id);
  const [error, setError] = useState<ErrorView | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const created = await createProject({
        code,
        name,
        purpose: optionalText(purpose),
        requirements: optionalText(requirements),
        key_deliverables: optionalText(keyDeliverables),
        start_date: startDate,
        target_finish_date: targetFinishDate === "" ? null : targetFinishDate,
        owner_user_id: ownerUserId,
      });
      setCode("");
      setName("");
      setPurpose("");
      setRequirements("");
      setKeyDeliverables("");
      setStartDate("");
      setTargetFinishDate("");
      setOwnerUserId(me.user.id);
      onCreated(created);
    } catch (e: unknown) {
      setError(toErrorView(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} aria-label="プロジェクトの作成">
      <h3>プロジェクトの作成</h3>
      <div>
        <label htmlFor="new-project-code">コード</label>
        <input
          id="new-project-code"
          required
          maxLength={PROJECT_CODE_MAX}
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-name">名称</label>
        <input
          id="new-project-name"
          required
          maxLength={PROJECT_NAME_MAX}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-purpose">目的</label>
        <textarea id="new-project-purpose" value={purpose} onChange={(e) => setPurpose(e.target.value)} />
      </div>
      <div>
        <label htmlFor="new-project-requirements">要求事項</label>
        <textarea
          id="new-project-requirements"
          value={requirements}
          onChange={(e) => setRequirements(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-deliverables">主要成果物</label>
        <textarea
          id="new-project-deliverables"
          value={keyDeliverables}
          onChange={(e) => setKeyDeliverables(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-start">開始日</label>
        <input
          id="new-project-start"
          type="date"
          required
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-finish">終了予定日</label>
        <input
          id="new-project-finish"
          type="date"
          min={startDate === "" ? undefined : startDate}
          value={targetFinishDate}
          onChange={(e) => setTargetFinishDate(e.target.value)}
        />
      </div>
      <div>
        <label htmlFor="new-project-owner">責任者</label>
        <OwnerSelect
          id="new-project-owner"
          value={ownerUserId}
          onChange={setOwnerUserId}
          always={[me.user]}
          candidates={ownerCandidates}
        />
      </div>
      <ErrorAlert error={error} />
      <button type="submit" disabled={submitting}>
        作成する
      </button>
    </form>
  );
}

function ProjectDestinations({ me, project }: { me: Me; project: Project }) {
  const visible = DESTINATIONS.filter(
    (d) => d.anyOf === undefined || d.anyOf.some((permission) => hasProjectPermission(me, project.id, permission)),
  );
  return (
    <nav aria-label={`${project.code} の行き先`} className="project-destinations">
      {visible.map((d) => (
        <Link key={d.label} to={d.path(project.id)}>
          {d.label}
        </Link>
      ))}
    </nav>
  );
}

export default function ProjectListPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [page, setPage] = useState<Page<Project> | null>(null);
  const [offset, setOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [ownerCandidates, setOwnerCandidates] = useState<UserListItem[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    Promise.all([getMe(), listProjects({ limit: PROJECTS_PAGE_SIZE, offset })]).then(
      ([meResult, projects]) => {
        if (!cancelled) {
          setMe(meResult);
          setPage(projects);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setPage(null);
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [offset, reloadKey]);

  const canCreate = me !== null && canCreateProject(me);

  // 責任者の候補は作成の部品を出すときだけ読む
  useEffect(() => {
    if (!canCreate) {
      return;
    }
    let cancelled = false;
    void loadActiveUsers().then((users) => {
      if (!cancelled) {
        setOwnerCandidates(users);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [canCreate]);

  return (
    <section data-screen-id="UI-PRJ-001" className="project-list-page">
      <h2>プロジェクト一覧</h2>
      {notice !== null && <p role="status">{notice}</p>}
      <ErrorAlert error={loadError} />
      {me !== null && canCreate && (
        <ProjectCreateForm
          me={me}
          ownerCandidates={ownerCandidates}
          onCreated={(created) => {
            setNotice(`プロジェクト「${created.code}」を作成しました`);
            setReloadKey((key) => key + 1);
          }}
        />
      )}
      {me !== null && page !== null && (
        <>
          <table aria-label="プロジェクトの一覧">
            <thead>
              <tr>
                <th scope="col">コード</th>
                <th scope="col">名称</th>
                <th scope="col">期間</th>
                <th scope="col">責任者</th>
                <th scope="col">WBSの状態</th>
                <th scope="col">行き先</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((project) => (
                <tr key={project.id}>
                  <td>{project.code}</td>
                  <td>{project.name}</td>
                  <td>{formatPeriod(project)}</td>
                  <td>{project.owner.display_name}</td>
                  <td>
                    <StatusBadge tone={WBS_STATUS_TONES[project.wbs_status]}>{wbsStatusLabel(project.wbs_status)}</StatusBadge>
                  </td>
                  <td>
                    <ProjectDestinations me={me} project={project} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {page.items.length === 0 && <p>見られるプロジェクトがありません</p>}
          <Pager total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
        </>
      )}
    </section>
  );
}
