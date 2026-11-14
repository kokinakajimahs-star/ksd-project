// ルートの定義（設計書 2.1・7.1）。7.1 の画面のルートは全てここに書く。
// 画面は frontend/src/pages/<群>/ に置き、下の <Routes> に 1 行ずつ足す。
import { useState } from "react";
import { Link, Route, Routes, useMatch, useNavigate } from "react-router-dom";

import { logout } from "./api/cmn";
import { RequireAuth, SessionProvider, useSession } from "./auth/session";
import ProjectNav from "./components/ProjectNav";
import AuditLogsPage from "./pages/chg/AuditLogsPage";
import ChangeRequestsPage from "./pages/chg/ChangeRequestsPage";
import CorrectiveActionsPage from "./pages/chg/CorrectiveActionsPage";
import ApprovalsPage from "./pages/cmn/ApprovalsPage";
import LoginPage from "./pages/cmn/LoginPage";
import UserAdminPage from "./pages/cmn/UserAdminPage";
import EffortPage from "./pages/eff/EffortPage";
import TimesheetPage from "./pages/eff/TimesheetPage";
import ImportExportPage from "./pages/int/ImportExportPage";
import DelaysPage from "./pages/prg/DelaysPage";
import StatusReportPage from "./pages/prg/StatusReportPage";
import ProjectListPage from "./pages/prj/ProjectListPage";
import ProjectSettingsPage from "./pages/prj/ProjectSettingsPage";
import MeetingReportPage from "./pages/rpt/MeetingReportPage";
import SharedViewPage from "./pages/rpt/SharedViewPage";
import ActivityListPage from "./pages/sch/ActivityListPage";
import BaselinesPage from "./pages/sch/BaselinesPage";
import GanttPage from "./pages/sch/GanttPage";
import ResponsibilityPage from "./pages/wbs/ResponsibilityPage";
import WbsElementPage from "./pages/wbs/WbsElementPage";
import WbsPage from "./pages/wbs/WbsPage";
import WbsReviewPage from "./pages/wbs/WbsReviewPage";

function Home() {
  return (
    <section>
      <h2>ホーム</h2>
      <p>WBS・工数・進捗管理システム</p>
    </section>
  );
}

function NotFound() {
  return (
    <section role="alert">
      <h2>ページが見つかりません</h2>
      <p>
        <Link to="/">ホームへ戻る</Link>
      </p>
    </section>
  );
}

/** ログインしている人の名前・行き先・ログアウト（API-CMN-002） */
function SessionMenu() {
  const { session, signOut } = useSession();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  if (session === null) {
    return (
      <nav aria-label="利用者" className="app-nav">
        <span className="app-nav-end">
          <Link to="/login">ログイン</Link>
        </span>
      </nav>
    );
  }

  async function handleLogout() {
    setBusy(true);
    try {
      await logout();
    } catch {
      // サーバでの失効に失敗しても（接続できない・既に失効している）、この端末のログインは忘れる
    }
    setBusy(false);
    signOut();
    navigate("/login", { replace: true });
  }

  // 上のバー（A-53）：左にプロジェクト（一覧から切り替える）、右に自分の行き先・名前・ログアウト
  return (
    <nav aria-label="利用者" className="app-nav">
      <Link to="/projects">プロジェクト</Link>
      <span className="app-nav-end">
        <Link to="/timesheet">タイムシート</Link>
        <Link to="/approvals">承認待ち</Link>
        {session.user.is_system_admin && <Link to="/admin/users">ユーザー管理</Link>}
        <span>{session.user.display_name}</span>
        <button type="button" onClick={() => void handleLogout()} disabled={busy}>
          ログアウト
        </button>
      </span>
    </nav>
  );
}

/** プロジェクトの中の画面（/projects/:projectId/*）では、ログインしていれば左メニューを出す */
function ProjectMenu() {
  const { session } = useSession();
  const match = useMatch("/projects/:projectId/*");
  const projectId = Number(match?.params.projectId);
  if (session === null || !Number.isInteger(projectId) || projectId <= 0) {
    return null;
  }
  return <ProjectNav projectId={projectId} userId={session.user.id} />;
}

export default function App() {
  return (
    <SessionProvider>
      <div className="app">
        <header className="app-header">
          <h1>
            <Link to="/">PMT</Link>
          </h1>
          <SessionMenu />
        </header>
        <ProjectMenu />
        <main>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/admin/users"
              element={
                <RequireAuth>
                  <UserAdminPage />
                </RequireAuth>
              }
            />
            <Route
              path="/approvals"
              element={
                <RequireAuth>
                  <ApprovalsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects"
              element={
                <RequireAuth>
                  <ProjectListPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/settings"
              element={
                <RequireAuth>
                  <ProjectSettingsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/wbs"
              element={
                <RequireAuth>
                  <WbsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/wbs/:elementId"
              element={
                <RequireAuth>
                  <WbsElementPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/wbs-review"
              element={
                <RequireAuth>
                  <WbsReviewPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/responsibility"
              element={
                <RequireAuth>
                  <ResponsibilityPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/activities"
              element={
                <RequireAuth>
                  <ActivityListPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/gantt"
              element={
                <RequireAuth>
                  <GanttPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/baselines"
              element={
                <RequireAuth>
                  <BaselinesPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/status-reports/:reportId"
              element={
                <RequireAuth>
                  <StatusReportPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/status-reports/:reportId/delays"
              element={
                <RequireAuth>
                  <DelaysPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/status-reports/:reportId/meeting"
              element={
                <RequireAuth>
                  <MeetingReportPage />
                </RequireAuth>
              }
            />
            {/* 共有ビューはログインを要らない（6.1 の API-RPT-004）。RequireAuth で囲まない */}
            <Route path="/shared/:token" element={<SharedViewPage />} />
            <Route
              path="/timesheet"
              element={
                <RequireAuth>
                  <TimesheetPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/effort"
              element={
                <RequireAuth>
                  <EffortPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/change-requests"
              element={
                <RequireAuth>
                  <ChangeRequestsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/corrective-actions"
              element={
                <RequireAuth>
                  <CorrectiveActionsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/audit-logs"
              element={
                <RequireAuth>
                  <AuditLogsPage />
                </RequireAuth>
              }
            />
            <Route
              path="/projects/:projectId/import-export"
              element={
                <RequireAuth>
                  <ImportExportPage />
                </RequireAuth>
              }
            />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
    </SessionProvider>
  );
}
