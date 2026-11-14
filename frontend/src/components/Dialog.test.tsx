// 画面の上に重ねるダイアログ（CR-027）：開くと中へ的を移し、Tab は中で回し、Esc・「閉じる」・外側のクリックで閉じて元の所へ的を戻す。
// 開いている間は的を外へ出さない（中の部品の作り直し・背景への移動・的がどこにも無いときのキー）
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import Dialog from "./Dialog";

function Harness({ onEscapeInside }: { onEscapeInside?: boolean }) {
  const [open, setOpen] = useState(false);
  // 保存の後の読み直しのように、Enter で入力欄を作り直す（的のあった要素が消える）
  const [version, setVersion] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        開く
      </button>
      {open && (
        <Dialog title="試しのダイアログ" onClose={() => setOpen(false)}>
          <label>
            最初の欄
            <input
              onKeyDown={(e) => {
                if (onEscapeInside === true && e.key === "Escape") {
                  e.preventDefault();
                }
              }}
            />
          </label>
          <input
            key={version}
            aria-label="作り直す欄"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setVersion((v) => v + 1);
              }
            }}
          />
          <button type="button">最後のボタン</button>
        </Dialog>
      )}
    </>
  );
}

function open() {
  const button = screen.getByRole("button", { name: "開く" });
  button.focus();
  fireEvent.click(button);
  return { button, dialog: screen.getByRole("dialog", { name: "試しのダイアログ" }) };
}

describe("Dialog（CR-027）", () => {
  it("開くと本文の最初の入力へ的を移し、Esc で閉じて開いたボタンへ戻す", () => {
    render(<Harness />);
    const { button, dialog } = open();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByLabelText("最初の欄")).toHaveFocus();
    fireEvent.keyDown(screen.getByLabelText("最初の欄"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("Tab・Shift+Tab は中で回す", () => {
    render(<Harness />);
    open();
    const close = screen.getByRole("button", { name: "閉じる" });
    const last = screen.getByRole("button", { name: "最後のボタン" });
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(close).toHaveFocus();
    fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  it("「閉じる」と外側のクリックで閉じる。中のクリックでは閉じない", () => {
    render(<Harness />);
    const { dialog } = open();
    fireEvent.mouseDown(dialog);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.mouseDown(dialog.parentElement!);
    expect(screen.queryByRole("dialog")).toBeNull();
    open();
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("中の部品が Esc を使ったとき（行の中の入力の取り消しなど）は閉じない", () => {
    render(<Harness onEscapeInside />);
    open();
    fireEvent.keyDown(screen.getByLabelText("最初の欄"), { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("文字の変換中の Esc（変換の取り消し）では閉じない", () => {
    render(<Harness />);
    open();
    fireEvent.keyDown(screen.getByLabelText("最初の欄"), { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByLabelText("最初の欄"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("中の入力欄が作り直されて的が消えたら、ダイアログへ戻す（Esc で閉じ、開いたボタンへ戻す）", async () => {
    render(<Harness />);
    const { button, dialog } = open();
    const field = screen.getByLabelText("作り直す欄");
    field.focus();
    fireEvent.keyDown(field, { key: "Enter" });
    expect(field.isConnected).toBe(false);
    await waitFor(() => expect(dialog).toHaveFocus());
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button).toHaveFocus();
  });

  it("背景へ的が移ったらダイアログへ戻す。的がどこにも無いときも Tab で中へ戻り、Esc で閉じる", () => {
    render(<Harness />);
    const { button, dialog } = open();
    button.focus();
    expect(dialog).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    expect(document.activeElement).toBe(document.body);
    fireEvent.keyDown(document.body, { key: "Tab" });
    expect(dialog).toHaveFocus();
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(document.body, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
