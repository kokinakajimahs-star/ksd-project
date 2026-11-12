import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setAuthToken } from "../../api/client";
import type { Me } from "../../api/cmn";
import { makeMe, makeMember, makeUser } from "../../test/fixtures";
import { memberPage } from "../../test/chgFixtures";
import { errorResponse, jsonResponse, mockApi, type RecordedCall, type RouteHandler } from "../../test/mockApi";
import { makeElement, makeEstimateRecord, makeTree, sampleElements } from "../../test/wbsFixtures";
import { changedFields } from "./ElementEditor";
import { parseEstimateValue } from "./EstimateForm";
import { buildTree } from "./WbsPage";
import WbsPage from "./WbsPage";

const BASE = "/api/v1/projects/3";

function meWith(permissions: string[]): Me {
  return makeMe({
    user: makeUser({ id: 5, is_system_admin: false }),
    system_permissions: [],
    projects: [{ project_id: 3, roles: ["PLANNER"], permissions, view_level: "ALL" }],
  });
}

const PLANNER = ["project.view", "wbs.edit", "estimate.edit"];

function routes(me: Me, overrides: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
  return {
    "GET /api/v1/me": () => jsonResponse(200, me),
    [`GET ${BASE}/wbs`]: () => jsonResponse(200, makeTree()),
    [`GET ${BASE}/members`]: () =>
      jsonResponse(200, memberPage([makeMember(), makeMember({ user_id: 4, login_id: "rev", display_name: "査読 花子" })])),
    [`GET ${BASE}/wbs/code-history`]: () =>
      jsonResponse(200, {
        project_id: 3,
        items: [
          {
            id: 1,
            element_id: 102,
            current_code: "1.2",
            old_code: "2.1",
            new_code: "1.2",
            renumbered_at: "2026-11-01T00:00:00Z",
            renumbered_by: 5,
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      }),
    ...overrides,
  };
}

function renderAt(path = "/projects/3/wbs") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/wbs" element={<WbsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

function sent(calls: RecordedCall[], method: string, path: string) {
  return calls.filter((c) => c.method === method && c.path === path);
}

async function selectElement(code: string) {
  fireEvent.click(await screen.findByRole("button", { name: `${code} を選ぶ` }));
  return screen.findByRole("region", { name: new RegExp(`^選んだ要素（${code.replace(".", "\\.")} `) });
}

describe("UI-WBS-001 WBS（ツリー図・アウトライン）", () => {
  beforeEach(() => {
    setAuthToken("t-planner");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    setAuthToken(null);
  });

  it("アウトラインに木・集計値・責任者・改番待ちを出し、概要に状態と検査の件数を出す（WBS-04・EST-02）", async () => {
    mockApi(routes(meWith(["project.view"])));
    const { container } = renderAt();

    expect(container.querySelector('[data-screen-id="UI-WBS-001"]')).not.toBeNull();
    const outline = await screen.findByRole("table", { name: "WBS のアウトライン" });
    const rows = within(outline).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent)).toEqual(["1.0", "1.1", "1.2", "2.0"]);
    expect(rows[0]).toHaveTextContent("56 人時（下位の合計）");
    expect(rows[1]).toHaveTextContent("ワークパッケージ");
    expect(rows[1]).toHaveTextContent("40 人時（自分の見積）");
    expect(await within(rows[1]!).findByText("計画 太郎")).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("（設計チーム）");
    expect(rows[2]).toHaveTextContent("改番待ち");
    expect(within(rows[1]!).getByRole("link", { name: "1.1 の辞書" })).toHaveAttribute("href", "/projects/3/wbs/101");

    const summary = screen.getByLabelText("WBS の概要");
    expect(summary).toHaveTextContent("作成中");
    expect(summary).toHaveTextContent("56 人時");
    expect(summary).toHaveTextContent("エラー 1 件・警告 2 件・完了条件の欠け 1 件");
    expect(screen.getByRole("link", { name: "検証・レビュー・承認" })).toHaveAttribute("href", "/projects/3/wbs-review");
    expect(screen.getByRole("link", { name: "責任分担表" })).toHaveAttribute("href", "/projects/3/responsibility");
  });

  it("ツリー図に切り替えても同じ木を階層で出し、選べる（WBS-04）", async () => {
    mockApi(routes(meWith(PLANNER)));
    renderAt();
    await screen.findByRole("table", { name: "WBS のアウトライン" });

    fireEvent.click(screen.getByRole("radio", { name: "ツリー図" }));
    const tree = screen.getByRole("tree", { name: "WBS のツリー図" });
    expect(screen.queryByRole("table", { name: "WBS のアウトライン" })).toBeNull();
    const items = within(tree).getAllByRole("treeitem");
    expect(items.map((i) => i.getAttribute("aria-level"))).toEqual(["1", "2", "2", "1"]);
    expect(items[2]).toHaveTextContent("改番待ち");

    // ツリー図から選んでも、同じ編集の部品が開く
    fireEvent.click(within(tree).getByRole("button", { name: "1.1 業務整理" }));
    expect(await screen.findByRole("form", { name: "要素を変える" })).toBeInTheDocument();
    expect(items[1]).toHaveAttribute("aria-selected", "true");
  });

  it("project.view だけの人には変える部品を出さない（7.2）", async () => {
    mockApi(routes(meWith(["project.view"])));
    renderAt();
    fireEvent.click(await screen.findByRole("button", { name: "1.1 を選ぶ" }));
    await screen.findByRole("link", { name: /「1\.1 業務整理」の辞書/ });

    expect(screen.queryByRole("form", { name: "要素を足す" })).toBeNull();
    expect(screen.queryByRole("form", { name: "要素を変える" })).toBeNull();
    expect(screen.queryByRole("form", { name: /見積を書く/ })).toBeNull();
    expect(screen.queryByRole("form", { name: "採番規則を変える" })).toBeNull();
    expect(screen.queryByRole("button", { name: "改番する" })).toBeNull();
    // 新旧対応の一覧は読める（WBS-06）
    const history = await screen.findByRole("table", { name: "WBS ID の新旧対応" });
    expect(history).toHaveTextContent("2.1");
  });

  it("要素の名称と責任者を変えると、変えた項目だけを送って木を読み直す（API-WBS-003）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`PATCH ${BASE}/wbs/elements/101`]: () =>
          jsonResponse(200, makeElement({ id: 101, kind: "WORK_PACKAGE", wbs_code: "1.1", name: "業務の整理" })),
      }),
    );
    renderAt();
    const editor = await selectElement("1.1");
    const form = within(editor).getByRole("form", { name: "要素を変える" });

    // 何も変えずに保存しても送らない
    fireEvent.click(within(form).getByRole("button", { name: "変更を保存する" }));
    expect(await within(editor).findByRole("alert")).toHaveTextContent("変えた項目がありません");

    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "業務の整理" } });
    fireEvent.change(within(form).getByLabelText("責任者"), { target: { value: "4" } });
    fireEvent.click(within(form).getByRole("button", { name: "変更を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("「1.1 業務の整理」を変えた");
    expect(sent(calls, "PATCH", `${BASE}/wbs/elements/101`).map((c) => c.body)).toEqual([
      { name: "業務の整理", responsible_user_id: 4 },
    ]);
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/wbs`)).toHaveLength(2));
  });

  it("要素を足す：親・区分・名称を送る。WP の下は選べず、サーバの 422 を出す（API-WBS-002・WBS-02）", async () => {
    let status = 422;
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/wbs/elements`]: () =>
          status === 422
            ? errorResponse(422, "WORK_PACKAGE_HAS_CHILDREN", "ワークパッケージの下には要素を足せません")
            : jsonResponse(201, makeElement({ id: 104, parent_id: 100, kind: "WORK_PACKAGE", name: "移行", wbs_code: "1.3" })),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "要素を足す" });
    await screen.findByRole("table", { name: "WBS のアウトライン" });

    const parent = within(form).getByLabelText("親");
    const options = within(parent).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["（最上位）", "1.0 要件定義", "2.0 管理"]);

    // 名称が空なら送らない
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("名称を入力してください");

    fireEvent.change(parent, { target: { value: "100" } });
    fireEvent.change(within(form).getByLabelText("区分"), { target: { value: "WORK_PACKAGE" } });
    fireEvent.change(within(form).getByLabelText("名称"), { target: { value: "移行" } });
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("ワークパッケージの下には要素を足せません");

    status = 201;
    fireEvent.click(within(form).getByRole("button", { name: "足す" }));
    expect(await screen.findByRole("status")).toHaveTextContent("「1.3 移行」を足した");
    expect(sent(calls, "POST", `${BASE}/wbs/elements`).at(-1)!.body).toEqual({
      parent_id: 100,
      kind: "WORK_PACKAGE",
      name: "移行",
    });
  });

  it("移動：自分と配下・WP は移動先に出さず、位置は 0 から数えて送る（API-WBS-004・WBS-06）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`POST ${BASE}/wbs/elements/100/move`]: () =>
          jsonResponse(200, makeElement({ id: 100, parent_id: 103, depth: 2, needs_renumber: true })),
      }),
    );
    renderAt();
    const editor = await selectElement("1.0");
    const form = within(editor).getByRole("form", { name: "要素を移動する" });
    const parent = within(form).getByLabelText("移動先の親");
    expect(within(parent).getAllByRole("option").map((o) => o.textContent)).toEqual(["（最上位）", "2.0 管理"]);

    fireEvent.change(parent, { target: { value: "103" } });
    fireEvent.change(within(form).getByLabelText(/兄弟の中の位置/), { target: { value: "0" } });
    fireEvent.click(within(form).getByRole("button", { name: "移動する" }));
    expect(await within(editor).findByRole("alert")).toHaveTextContent("1 以上の整数");
    expect(sent(calls, "POST", `${BASE}/wbs/elements/100/move`)).toHaveLength(0);

    fireEvent.change(within(form).getByLabelText(/兄弟の中の位置/), { target: { value: "1" } });
    fireEvent.click(within(form).getByRole("button", { name: "移動する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("改番待ち");
    expect(sent(calls, "POST", `${BASE}/wbs/elements/100/move`).map((c) => c.body)).toEqual([
      { parent_id: 103, position: 0 },
    ]);
  });

  it("子を持つ要素を消すときは配下の件数を示して確かめ、取り消せば送らない（API-WBS-005・WBS-01）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), { [`DELETE ${BASE}/wbs/elements/100`]: () => jsonResponse(204, null) }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    renderAt();
    const editor = await selectElement("1.0");

    fireEvent.click(within(editor).getByRole("button", { name: "配下ごと消す" }));
    expect(confirm).toHaveBeenLastCalledWith("「1.0 要件定義」と配下の要素 2 件を消します。よろしいですか");
    expect(sent(calls, "DELETE", `${BASE}/wbs/elements/100`)).toHaveLength(0);

    fireEvent.click(within(editor).getByRole("button", { name: "配下ごと消す" }));
    expect(await screen.findByRole("status")).toHaveTextContent("「1.0 要件定義」を消した");
    expect(sent(calls, "DELETE", `${BASE}/wbs/elements/100`)).toHaveLength(1);
  });

  it("見積を人日で書くと単位・手法・前提を送り、負の値は送らない（API-EST-001・EST-01・EST-08）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`PUT ${BASE}/estimates/ELEMENT/101`]: () =>
          jsonResponse(200, { record: makeEstimateRecord(), current_hours: 40, wbs_status: "DRAFT", project_estimate_hours: 56 }),
      }),
    );
    renderAt();
    const editor = await selectElement("1.1");
    const form = within(editor).getByRole("form", { name: "見積を書く（1.1 業務整理）" });

    fireEvent.change(within(form).getByLabelText("見積"), { target: { value: "-1" } });
    fireEvent.click(within(form).getByRole("button", { name: "見積を保存する" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("0 以上の数");
    expect(sent(calls, "PUT", `${BASE}/estimates/ELEMENT/101`)).toHaveLength(0);

    fireEvent.change(within(form).getByLabelText("見積"), { target: { value: "5" } });
    fireEvent.change(within(form).getByLabelText("単位"), { target: { value: "DAY" } });
    fireEvent.change(within(form).getByLabelText("見積手法"), { target: { value: "ANALOGOUS" } });
    fireEvent.change(within(form).getByLabelText("前提"), { target: { value: "前回と同じ規模" } });
    fireEvent.click(within(form).getByRole("button", { name: "見積を保存する" }));

    expect(await screen.findByRole("status")).toHaveTextContent("「1.1 業務整理」の見積を 40 人時にした（プロジェクトの総見積 56 人時）");
    expect(sent(calls, "PUT", `${BASE}/estimates/ELEMENT/101`).map((c) => c.body)).toEqual([
      { input_value: 5, input_unit: "DAY", method: "ANALOGOUS", assumptions: "前回と同じ規模", references: null },
    ]);
  });

  it("採番規則を変え、改番すると新旧の対応を出す（API-WBS-006・007・WBS-05・06）", async () => {
    const { calls } = mockApi(
      routes(meWith(PLANNER), {
        [`PUT ${BASE}/wbs/numbering-rule`]: () =>
          jsonResponse(200, { separator: "-", min_digits: 2, prefix: "P", pad_first_level: false }),
        [`POST ${BASE}/wbs/renumber`]: () =>
          jsonResponse(200, {
            renumbered_at: "2026-11-12T00:00:00Z",
            changes: [{ element_id: 100, old_code: "1.0", new_code: "P01" }],
          }),
      }),
    );
    renderAt();
    const form = await screen.findByRole("form", { name: "採番規則を変える" });
    expect(within(form).getByLabelText("区切り文字")).toHaveValue(".");

    fireEvent.change(within(form).getByLabelText("区切り文字"), { target: { value: "-" } });
    fireEvent.change(within(form).getByLabelText("最小の桁数"), { target: { value: "10" } });
    fireEvent.click(within(form).getByRole("button", { name: "規則を保存する" }));
    expect(await screen.findByText(/最小の桁数は 1〜9/)).toBeInTheDocument();

    fireEvent.change(within(form).getByLabelText("最小の桁数"), { target: { value: "2" } });
    fireEvent.change(within(form).getByLabelText("接頭辞"), { target: { value: "P" } });
    fireEvent.click(within(form).getByLabelText(/第 1 階層を補完する/));
    fireEvent.click(within(form).getByRole("button", { name: "規則を保存する" }));
    expect(await screen.findByRole("status")).toHaveTextContent("今の ID は改番するまで変わらない");
    expect(sent(calls, "PUT", `${BASE}/wbs/numbering-rule`).map((c) => c.body)).toEqual([
      { separator: "-", min_digits: 2, prefix: "P", pad_first_level: false },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "改番する" }));
    const result = await screen.findByRole("table", { name: "改番の結果" });
    expect(result).toHaveTextContent("1.0");
    expect(result).toHaveTextContent("P01");
    expect(screen.getByRole("status")).toHaveTextContent("ID が変わった要素 1 件");
    await waitFor(() => expect(sent(calls, "GET", `${BASE}/wbs/code-history`).length).toBeGreaterThanOrEqual(2));
  });

  it("プロジェクトの ID が数でなければ見つからないことを示す", () => {
    const { calls } = mockApi({});
    render(
      <MemoryRouter initialEntries={["/projects/x/wbs"]}>
        <Routes>
          <Route path="/projects/:projectId/wbs" element={<WbsPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("プロジェクトが見つかりません");
    expect(calls).toHaveLength(0);
  });
});

describe("WBS の画面の部品の関数", () => {
  it("buildTree は先行順の配列を親子にし、親の見えない要素を最上位に置く", () => {
    const roots = buildTree(sampleElements());
    expect(roots.map((r) => r.element.wbs_code)).toEqual(["1.0", "2.0"]);
    expect(roots[0]!.children.map((c) => c.element.wbs_code)).toEqual(["1.1", "1.2"]);
    const orphan = buildTree([makeElement({ id: 7, parent_id: 99, wbs_code: "3.1" })]);
    expect(orphan.map((r) => r.element.wbs_code)).toEqual(["3.1"]);
  });

  it("changedFields は変えた項目だけを返し、空の担当チームは null にする", () => {
    const element = { name: "A", kind: "DELIVERABLE" as const, responsible_user_id: 2, responsible_team: "X" };
    expect(changedFields(element, { name: " A ", kind: "DELIVERABLE", responsibleUserId: 2, team: "X" })).toEqual({});
    expect(changedFields(element, { name: "B", kind: "COMPONENT", responsibleUserId: null, team: " " })).toEqual({
      name: "B",
      kind: "COMPONENT",
      responsible_user_id: null,
      responsible_team: null,
    });
  });

  it("parseEstimateValue は 0 以上・小数 2 桁までだけを受ける", () => {
    expect(parseEstimateValue("0")).toBe(0);
    expect(parseEstimateValue(" 12.25 ")).toBe(12.25);
    expect(parseEstimateValue("1.234")).toBeNull();
    expect(parseEstimateValue("-1")).toBeNull();
    expect(parseEstimateValue("")).toBeNull();
  });
});
