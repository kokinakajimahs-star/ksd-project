import { renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { confirmLeave, UNSAVED_EDITS_MESSAGE, useLeaveGuard } from "./leaveGuard";

/** document に置いたリンクをクリックし、既定の動作（リンクを辿る）が止められたかを返す */
function clickLink(attributes: Record<string, string>, init: MouseEventInit = {}): boolean {
  const anchor = document.createElement("a");
  anchor.href = "/projects";
  for (const [name, value] of Object.entries(attributes)) {
    anchor.setAttribute(name, value);
  }
  anchor.textContent = "プロジェクト一覧へ";
  document.body.appendChild(anchor);
  // jsdom は他の URL へ移れないので、確かめの後（bubble の document）で既定の動作を止め、止めた結果だけを見る
  let prevented = false;
  const record = (event: Event) => {
    prevented = event.defaultPrevented;
    event.preventDefault();
  };
  document.addEventListener("click", record);
  try {
    anchor.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: 0, ...init }));
  } finally {
    document.removeEventListener("click", record);
    anchor.remove();
  }
  return prevented;
}

describe("画面を離れる前の確かめ（A-49 の 13・OQ-42 (21) の c）", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("active の間、リンクのクリックで確かめ、やめればリンクを辿らない。移るなら止めない", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderHook(() => useLeaveGuard(true));

    expect(clickLink({})).toBe(true);
    expect(confirm).toHaveBeenCalledWith(UNSAVED_EDITS_MESSAGE);

    confirm.mockReturnValue(true);
    expect(clickLink({})).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("新しいタブ・保存・修飾キー・左以外のボタンのクリックは今の画面を離れないので確かめない", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderHook(() => useLeaveGuard(true));

    expect(clickLink({ target: "_blank" })).toBe(false);
    expect(clickLink({ download: "a.csv" })).toBe(false);
    expect(clickLink({}, { ctrlKey: true })).toBe(false);
    expect(clickLink({}, { metaKey: true })).toBe(false);
    expect(clickLink({}, { shiftKey: true })).toBe(false);
    expect(clickLink({}, { altKey: true })).toBe(false);
    expect(clickLink({}, { button: 1 })).toBe(false);
    expect(confirm).not.toHaveBeenCalled();

    // target="_self" は今のタブで移る
    expect(clickLink({ target: "_self" })).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("active でなければ確かめない。active でなくなれば（外せば）確かめをやめる", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { rerender, unmount } = renderHook(({ active }) => useLeaveGuard(active), { initialProps: { active: false } });

    expect(clickLink({})).toBe(false);
    rerender({ active: true });
    expect(clickLink({})).toBe(true);
    rerender({ active: false });
    expect(clickLink({})).toBe(false);
    rerender({ active: true });
    unmount();
    expect(clickLink({})).toBe(false);
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("画面の中の操作は、編集があるときだけ確かめる", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

    expect(confirmLeave(false)).toBe(true);
    expect(confirm).not.toHaveBeenCalled();
    expect(confirmLeave(true)).toBe(false);
    confirm.mockReturnValue(true);
    expect(confirmLeave(true)).toBe(true);
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});
