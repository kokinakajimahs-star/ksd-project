// UI-PRJ-002 プロジェクト設定（設計書 7.1 のルート /projects/:projectId/settings。PRJ-01・02・04・06、CMN-01）。
// 基本情報（API-PRJ-003・004）・スコープ（API-PRJ-005・006）・稼働カレンダー（API-PRJ-007・008）・
// メンバー（API-PRJ-009〜011）・権限（API-CMN-006・007）を 1 つの画面に並べる。
// 見るのは project.view。変える部品は、それぞれの権限（project.manage・member.manage・permission.manage）を
// `GET /api/v1/me`（API-CMN-008）で見て出す（7.2。サーバでも必ず判定する）
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { getMe, hasProjectPermission, type Me } from "../../api/cmn";
import { toErrorView, type ErrorView } from "../../api/errors";
import { getProject, type Project } from "../../api/prj";
import ErrorAlert from "../../components/ErrorAlert";
import BasicInfoSection from "./settings/BasicInfoSection";
import CalendarSection from "./settings/CalendarSection";
import MembersSection from "./settings/MembersSection";
import RolePermissionsSection from "./settings/RolePermissionsSection";
import ScopeSection from "./settings/ScopeSection";

/** ルートの :projectId を数にする。正の整数でなければ null */
export function parseProjectId(value: string | undefined): number | null {
  if (value === undefined || !/^[1-9]\d*$/.test(value)) {
    return null;
  }
  const id = Number(value);
  return Number.isSafeInteger(id) ? id : null;
}

export default function ProjectSettingsPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = parseProjectId(params.projectId);
  const [me, setMe] = useState<Me | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [loadError, setLoadError] = useState<ErrorView | null>(null);

  useEffect(() => {
    if (projectId === null) {
      return;
    }
    let cancelled = false;
    setLoadError(null);
    setProject(null);
    Promise.all([getMe(), getProject(projectId)]).then(
      ([meResult, projectResult]) => {
        if (!cancelled) {
          setMe(meResult);
          setProject(projectResult);
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setLoadError(toErrorView(e));
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // メンバー・権限の表を変えると自分の権限も変わりうるので、/me を読み直して出し分けを合わせる
  const refreshMe = useCallback(() => {
    getMe().then(setMe, (e: unknown) => setLoadError(toErrorView(e)));
  }, []);

  if (projectId === null) {
    return (
      <section data-screen-id="UI-PRJ-002" className="project-settings-page">
        <h2>プロジェクト設定</h2>
        <p role="alert">プロジェクトが見つかりません</p>
        <p>
          <Link to="/projects">プロジェクト一覧へ</Link>
        </p>
      </section>
    );
  }

  const can = (permission: string) => me !== null && hasProjectPermission(me, projectId, permission);

  return (
    <section data-screen-id="UI-PRJ-002" className="project-settings-page">
      <h2>プロジェクト設定{project !== null && `：${project.code} ${project.name}`}</h2>
      <p>
        <Link to="/projects">プロジェクト一覧へ</Link>
      </p>
      <ErrorAlert error={loadError} />
      {me !== null && project !== null && (
        <>
          <BasicInfoSection
            key={project.id}
            project={project}
            canEdit={can("project.manage")}
            onSaved={setProject}
          />
          <ScopeSection projectId={projectId} canEdit={can("project.manage")} />
          <CalendarSection projectId={projectId} canEdit={can("project.manage")} />
          <MembersSection projectId={projectId} canEdit={can("member.manage")} onChanged={refreshMe} />
          <RolePermissionsSection
            projectId={projectId}
            canEdit={can("permission.manage")}
            onChanged={refreshMe}
          />
        </>
      )}
    </section>
  );
}
