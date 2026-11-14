// 画面写し（A-53・ksd-ls の ADR-011 の A）。試験ではない：関門・spec-ids.toml の [[checks]]・vitest（src/**）に入れず、
// 合否も決めない。主な画面を固定のデータ（API を偽装する）で撮り、.kloop/ui-shots/<HEAD の 12 桁>/ に出す（コミットしない）。
// 流し方（CPU を空けるため 3 コアに絞り、優先度を下げる。関門の最中には流さない）:
//   taskset -c 0-2 nice -n 10 npm --prefix frontend run ui-shots
// ブラウザは手元の chromium-1243（@playwright/test 1.63.0 が使う版）を使う
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

const PORT = 5179;
const frontendDir = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  testDir: ".",
  testMatch: "*.shots.ts",
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: fileURLToPath(new URL("../../.kloop/ui-shots/.results", import.meta.url)),
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1440, height: 900 },
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
  },
  webServer: {
    command: `npx vite --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    cwd: frontendDir,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
