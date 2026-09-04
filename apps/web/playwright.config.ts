import { existsSync } from "node:fs";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

/** Locate the interpreter that has the API's dependencies installed.
 *  Prefers the repo virtualenv, so `npm run test:e2e` works without the
 *  developer having activated it first. Override with PYTHON=... */
function pythonBin(): string {
  if (process.env.PYTHON) return process.env.PYTHON;
  const root = path.resolve(__dirname, "..", "..");
  for (const candidate of [
    path.join(root, ".venv", "Scripts", "python.exe"),
    path.join(root, ".venv", "bin", "python"),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  return "python";
}

const PYTHON = pythonBin();

/** End-to-end configuration.
 *
 *  Both servers are started by Playwright so `npm run test:e2e` works from a
 *  clean checkout without a separate terminal. The API is started with a
 *  dedicated SQLite file so an e2e run never touches the demo database you
 *  might have open in a browser.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `"${PYTHON}" -m uvicorn recover.main:app --port 8100 --host 127.0.0.1`,
      cwd: "../api",
      port: 8100,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        DATABASE_URL: "sqlite:///./e2e.db",
        PUBLIC_WEB_URL: "http://localhost:3100",
        CORS_ORIGINS: "http://localhost:3100,http://127.0.0.1:3100",
        ANTHROPIC_API_KEY: "",
        RAZORPAY_KEY_ID: "",
        RAZORPAY_KEY_SECRET: "",
      },
    },
    {
      // `next` directly rather than `npm run dev`, whose script already pins
      // port 3000 and would fight this flag.
      command: "npx next dev --port 3100",
      port: 3100,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: { NEXT_PUBLIC_API_URL: "http://127.0.0.1:8100" },
    },
  ],
});
