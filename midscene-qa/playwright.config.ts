import 'dotenv/config';
import { defineConfig, devices } from '@playwright/test';

// Midscene drives a real browser and calls a multimodal LLM per AI step,
// so tests are slower than selector-based ones — give them generous timeouts.
export default defineConfig({
  testDir: './e2e',
  // AI steps take a few seconds each; a login + multi-step flow easily exceeds
  // 2 min uncached. Generous timeout; caching cuts real runtime dramatically.
  timeout: 480 * 1000,
  expect: { timeout: 30 * 1000 },
  fullyParallel: false,
  workers: 1,
  reporter: [
    ['list'],
    // Emits an interactive HTML report to midscene_run/report/ showing
    // each AI step, the screenshot it reasoned over, and what it did.
    ['@midscene/web/playwright-reporter', { type: 'merged' }],
  ],
  use: {
    viewport: { width: 1280, height: 768 },
    trace: 'on-first-retry',
  },
  projects: [
    // Use the system-installed Google Chrome (channel: 'chrome') instead of
    // Playwright's bundled Chromium, so no browser download is required.
    { name: 'chrome', use: { ...devices['Desktop Chrome'], channel: 'chrome' } },
  ],
});
