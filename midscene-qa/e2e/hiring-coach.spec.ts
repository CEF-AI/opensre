import { test as base, expect } from '@playwright/test';
import { PlaywrightAiFixture } from '@midscene/web/playwright';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { openWidget } from './helpers';
import { smartClick, smartFill, smartVisible } from './smart';

// Hiring Coach — Result widget UX tests. Approach: deterministic-first, AI-fallback (see smart.ts) —
// native Playwright when the widget is stable, Midscene vision when a selector drifts (the widget is
// versioned + not our DOM, so it will change). SCOPE for now: up to "analyse audio". Result
// completion / playback / seek are deferred (backend processing >5min on qaagent-vault — QA-UX-STATUS.md).
const test = base.extend(PlaywrightAiFixture());

const HERE = dirname(fileURLToPath(import.meta.url));
const CLIP_MP3 = resolve(HERE, '../fixtures/test-clip-12s.mp3');
const ANALYSIS_START_MS = 90_000;

// T0 · Proof the AI fallback fires: a deliberately-wrong native selector must fall back to Midscene
// vision and still confirm the element. (Guards that the deterministic-first, AI-fallback path works.)
test('T0 · AI fallback fires when the native selector is broken', async ({ page, aiAssert }) => {
  const w = await openWidget(page);
  const bogus = w.getByText(/__nonexistent_selector_zzz__/i).first();
  const via = await smartVisible(bogus, aiAssert,
    'a "Record meeting" option is visible in the Hiring Coach widget', 3000);
  expect(via).toBe('ai'); // native failed → recovered via the vision model
});

// T1 · The widget loads (install assumed; the curator just opens it).
test('T1 · widget loads', async ({ page, aiAssert }) => {
  const w = await openWidget(page);
  await smartVisible(w.getByText(/analyze an interview/i).first(), aiAssert,
    'the "Analyze an interview" heading is visible in the Hiring Coach widget');
  await smartVisible(w.getByText(/import recording/i).first(), aiAssert,
    'an "Import recording" option is visible');
  await smartVisible(w.getByText(/record meeting/i).first(), aiAssert,
    'a "Record meeting" option is visible');
});

// T2 · Import a clip → upload accepted → analysis starts (the "till analyse audio" checkpoint).
test('T2 · import → clip accepted → analysis starts', async ({ page, aiTap, aiInput, aiAssert }) => {
  test.setTimeout(ANALYSIS_START_MS + 200_000);
  const w = await openWidget(page);

  await smartFill(w.locator('input[type=text]').first(), 'QA Test Candidate', aiInput,
    'the "Candidate name or ID" text field');

  // File selection has no vision equivalent — you can't inject a file by looking at the screen — so
  // this step is inherently native (setInputFiles on the hidden file input).
  await w.locator('input[type=file]').first().setInputFiles(CLIP_MP3);

  // The selected clip shows as a "New upload · 0:12 captured" card.
  await smartVisible(w.getByText(/new upload/i).first(), aiAssert,
    'a "New upload" card with a captured clip duration is shown');

  // Start analysis, then confirm it reaches upload/analysing — audio accepted, ASR kicked off.
  await smartClick(w.getByRole('button', { name: /analyze interview/i }).first(), aiTap,
    'the "Analyze interview" button');
  await smartVisible(w.getByText(/uploading|analyz/i).first(), aiAssert,
    'the widget shows it is uploading or analysing the interview', ANALYSIS_START_MS);
});

// ── DEFERRED (need a completed result; blocked on backend processing speed — see QA-UX-STATUS.md) ──
test.fixme('T2b · result completes within the processing deadline', async () => {});
test.fixme('T3 · playback advances (fresh result, non-expired audio link)', async () => {});
test.fixme('T3b · timeline seek jumps to the clicked section', async () => {});
