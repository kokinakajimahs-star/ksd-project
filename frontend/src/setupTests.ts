// Vitest の共通の準備：Testing Library の DOM の照合（toBeInTheDocument など）を使えるようにする
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
