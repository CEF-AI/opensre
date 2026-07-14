import 'dotenv/config';
import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// Reuse Midscene's AI element-location plans across runs (fast + free for action steps; re-plans
// only when the widget changes). NB: aiAssert/aiQuery still call the model each run — not cached.
process.env.MIDSCENE_CACHE ||= '1';

// Absolute path to the fake microphone input (a real WAV Chromium feeds to getUserMedia).
const FAKE_MIC_WAV = resolve(process.cwd(), 'fixtures/test-clip.wav');

// Midscene drives a real browser and calls a multimodal LLM per AI step,
// so tests are slower than selector-based ones — give them generous timeouts.
export default defineConfig({
  testDir: './e2e',
  timeout: 480 * 1000,
  expect: { timeout: 30 * 1000 },
  fullyParallel: false,
  workers: 1,
  // The Cere wallet login (fresh-vault provisioning) is timing-sensitive on CI — retry a flaky run.
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ['list'],
    // Playwright HTML report + trace — captures EVERY step (native Playwright AND Midscene AI
    // fallbacks), with screenshots + a timeline. This is the complete UX report (→ Notion report-url).
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
    // Midscene report — supplementary "what the vision model saw" for the AI steps only.
    ['@midscene/web/playwright-reporter', { type: 'merged' }],
  ],
  use: {
    // Tall viewport so more of the (tall) widget iframe is on-screen — Midscene reasons over the
    // visible viewport, so more visible = fewer off-screen misses. reveal() still scrolls per step.
    viewport: { width: 1280, height: 2000 },
    trace: 'on', // record the full step-by-step trace on every run, not just retries
    screenshot: 'on',
  },
  projects: [
    // Default: deterministic tests (login, T1, T2, exploration). No media fakery needed.
    {
      name: 'chrome',
      testIgnore: /record\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], channel: 'chrome' },
    },
    // Record tests (T4/T5): Chromium fakes the mic with a WAV and auto-accepts screen/tab capture so
    // the "Record meeting" flow (mic + tab audio via getUserMedia + getDisplayMedia) runs without
    // human clicks. Only *-record.spec.ts runs here. NOTE: getDisplayMedia auto-capture in headed
    // Chrome is not yet validated end-to-end — see QA-UX-STATUS.md.
    {
      name: 'chrome-media',
      testMatch: /record\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        channel: 'chrome',
        permissions: ['microphone', 'camera'],
        launchOptions: {
          args: [
            '--use-fake-ui-for-media-stream', // auto-grant mic/cam prompts
            '--use-fake-device-for-media-stream',
            `--use-file-for-fake-audio-capture=${FAKE_MIC_WAV}%noloop`, // feed our clip as the mic
            '--auto-accept-this-tab-capture', // auto-accept getDisplayMedia for the current tab
            '--autoplay-policy=no-user-gesture-required',
          ],
        },
      },
    },
  ],
});
