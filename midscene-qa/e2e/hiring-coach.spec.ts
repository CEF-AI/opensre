import { test, expect } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { openWidget } from './helpers';

// Hiring Coach — Result widget UX tests. SCOPE (for now): up to "analyse audio" — i.e. the widget
// loads, a clip is accepted, and processing STARTS. Result-completion, playback and seek are
// deferred: on qaagent-vault a 12s clip does not finish processing within 5 min (backend Whisper
// throughput block — see QA-UX-STATUS.md). Those tests live below as test.fixme until a vault where
// processing completes is available.

const HERE = dirname(fileURLToPath(import.meta.url));
const CLIP_MP3 = resolve(HERE, '../fixtures/test-clip-12s.mp3');
const ANALYSIS_START_MS = 90_000; // time allowed to reach the "uploading/analysing" state

// T1 · The widget loads (install assumed; the curator just opens it).
test('T1 · widget loads', async ({ page }) => {
  const w = await openWidget(page);
  await expect(w.getByText(/analyze an interview/i).first()).toBeVisible();
  await expect(w.getByText(/import recording/i).first()).toBeVisible();
  await expect(w.getByText(/record meeting/i).first()).toBeVisible();
});

// T2 · Import a clip → upload accepted → analysis starts (the "till analyse audio" checkpoint).
test('T2 · import → clip accepted → analysis starts', async ({ page }) => {
  test.setTimeout(ANALYSIS_START_MS + 200_000);
  const w = await openWidget(page);

  await w.locator('input[type=text]').first().fill('QA Test Candidate').catch(() => {});
  await w.locator('input[type=file]').first().setInputFiles(CLIP_MP3);

  // The selected clip shows as a "New upload · 0:12 captured" card.
  await expect(w.getByText(/new upload/i).first()).toBeVisible({ timeout: 15_000 });
  await expect(w.getByText(/captured/i).first()).toBeVisible();

  // Start analysis, then confirm it reaches upload/analysing — audio accepted, ASR kicked off.
  const analyze = w.getByRole('button', { name: /analyze interview/i }).first();
  if (await analyze.count().catch(() => 0)) await analyze.click({ timeout: 8_000 }).catch(() => {});
  await expect(w.getByText(/uploading|analyz/i).first()).toBeVisible({ timeout: ANALYSIS_START_MS });
});

// ── DEFERRED (need a completed result; blocked on backend processing speed) ───────────────────
// T2b · result completes within the 5-min deadline (11–12s clip should be fast).
test.fixme('T2b · result completes within the processing deadline', async () => {});
// T3 · playback: click Play → the timeline/timer advances (guards the 2-hour temp-credential expiry).
test.fixme('T3 · playback advances (fresh result, non-expired audio link)', async () => {});
// T3b · seek: click a point on the conversation timeline → playback jumps to that section.
test.fixme('T3b · timeline seek jumps to the clicked section', async () => {});
