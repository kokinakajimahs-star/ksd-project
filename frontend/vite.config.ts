// 画面の構成（設計書 2.1・2.2）。開発時は /api をサーバ（uvicorn）へ渡す
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/setupTests.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // 同時に走らせる試験のワーカー（jsdom を積んだ Node のプロセス）を 2 つまでにする。既定は CPU 数 − 1（この VM で 7）で、
    // ループの検証が別のセッションの試験と重なると VM ごと落ちる（docs/decisions/ASSUMPTIONS.md の A-24）
    // （Vitest 4 で minWorkers は無くなった。上限の maxWorkers だけを置く）
    maxWorkers: 2,
  },
});
