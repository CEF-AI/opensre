import { test as base } from '@playwright/test';
import { PlaywrightAiFixture } from '@midscene/web/playwright';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { openWidget } from './helpers';

// Hiring Coach — Result widget UX tests, FULLY AI (Midscene vision) for the widget flow. The widget
// is versioned + not our DOM, so vision-driven steps survive layout/structure changes; the AI plan
// is cached (MIDSCENE_CACHE, set in playwright.config) so repeat runs skip re-planning for action
// steps (aiTap/aiInput). aiAssert still calls the model each run. Login + file-upload stay native
// (Cere OTP iframe = flaky by vision; a file can't be injected by looking at the screen).
// SCOPE for now: up to "analyse audio" — result completion/playback/seek deferred (QA-UX-STATUS.md §6).
const test = base.extend(PlaywrightAiFixture());

const HERE = dirname(fileURLToPath(import.meta.url));
const CLIP_MP3 = resolve(HERE, '../fixtures/test-clip-12s.mp3');
const ANALYSIS_START_MS = 90_000;

// T1 · The widget loads (install assumed; the curator just opens it).
test('T1 · widget loads', async ({ page, aiAssert }) => {
  await openWidget(page); // native login + open agent + grant widget-signing permission
  await aiAssert(
    'The Hiring Coach widget shows an "Analyze an interview" view with an "Import recording" option and a "Record meeting" option',
  );
});

// T2 · Import a clip → upload accepted → analysis starts (the "till analyse audio" checkpoint).
test('T2 · import → clip accepted → analysis starts', async ({ page, aiInput, aiTap, aiAssert }) => {
  test.setTimeout(ANALYSIS_START_MS + 200_000);
  const w = await openWidget(page);

  await aiInput('QA Test Candidate', 'the "Candidate name or ID" text field');

  // File selection has no vision equivalent — inject via the hidden file input (native).
  await w.locator('input[type=file]').first().setInputFiles(CLIP_MP3);
  await aiAssert('A "New upload" card is shown with the captured clip duration');

  await aiTap('the "Analyze interview" button');
  await aiAssert('The widget shows that it is uploading or analysing the interview');
});

// ── DEFERRED (need a completed result; blocked on backend processing — QA-UX-STATUS.md §6) ─────────
test.fixme('T2b · result completes within the processing deadline', async () => {});
test.fixme('T3 · playback advances (fresh result, non-expired audio link)', async () => {});
test.fixme('T3b · timeline seek jumps to the clicked section', async () => {});
