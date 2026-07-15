import { test, expect } from '@playwright/test';
import { PlaywrightAgent } from '@midscene/web/playwright';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { openWidget, reveal } from './helpers';

// Hiring Coach — Result widget UX tests, FULLY AI (Midscene vision) for the widget flow. Key
// ordering: NATIVE login first (openWidget), THEN create the Midscene agent — so the vision model's
// page instrumentation never runs during the flaky Cere wallet login (which broke on CI when the
// PlaywrightAiFixture instrumented the page mid-login). Login is 100% native (proven by the CI
// probe); Midscene engages only once the widget is up. AI plans cached (MIDSCENE_CACHE).
// SCOPE for now: up to "analyse audio" — completion/playback/seek deferred (QA-UX-STATUS.md §6).

const HERE = dirname(fileURLToPath(import.meta.url));
const CLIP_MP3 = resolve(HERE, '../fixtures/test-clip-12s.mp3');
const ANALYSIS_START_MS = 90_000;

// T1 · The widget loads (install assumed; the curator just opens it).
test('T1 · widget loads', async ({ page }) => {
  const w = await openWidget(page); // native login + open agent + grant widget-signing permission
  const ai = new PlaywrightAgent(page); // Midscene engages only AFTER login
  await reveal(w, /record meeting/i); // scroll the input view into the viewport before asserting
  await page.screenshot({ path: 'test-results/t1-ready.png', fullPage: true }).catch(() => {});
  await ai.aiAssert(
    'The Hiring Coach widget shows an "Analyze an interview" view with an "Import recording" option and a "Record meeting" option',
  );
});

// T2 · Import a clip → upload accepted → analysis starts (the "till analyse audio" checkpoint).
test('T2 · import → clip accepted → analysis starts', async ({ page }) => {
  test.setTimeout(ANALYSIS_START_MS + 200_000);
  const w = await openWidget(page);
  const ai = new PlaywrightAgent(page);

  await reveal(w, /candidate name|analyze an interview/i);
  await ai.aiInput('QA Test Candidate', 'the "Candidate name or ID" text field');

  // File selection has no vision equivalent — inject via the hidden file input (native).
  await w.locator('input[type=file]').first().setInputFiles(CLIP_MP3);
  await reveal(w, /new upload|captured/i);
  await ai.aiAssert('A "New upload" card is shown with the captured clip duration');

  await reveal(w, /analyze interview/i);
  await ai.aiTap('the "Analyze interview" button');
  await reveal(w, /uploading|analyz/i);
  await page.screenshot({ path: 'test-results/t2-analysing.png', fullPage: true }).catch(() => {});
  await ai.aiAssert('The widget shows that it is uploading or analysing the interview');
});

// ── DEFERRED (need a completed result; blocked on backend processing — QA-UX-STATUS.md §6) ─────────
test.fixme('T2b · result completes within the processing deadline', async () => {});
test.fixme('T3 · playback advances (fresh result, non-expired audio link)', async () => {});
test.fixme('T3b · timeline seek jumps to the clicked section', async () => {});
