import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import App from "./App";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <App />
    </MemoryRouter>,
  );
}

describe("App（ルートの枠）", () => {
  it("ホームを描画する", () => {
    renderAt("/");
    expect(screen.getByRole("heading", { level: 1, name: "PMT" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "ホーム" })).toBeInTheDocument();
  });

  it("知らないルートでは見つからないことを示す", () => {
    renderAt("/no-such-page");
    expect(screen.getByRole("alert")).toHaveTextContent("ページが見つかりません");
    expect(screen.getByRole("link", { name: "ホームへ戻る" })).toHaveAttribute("href", "/");
  });
});
