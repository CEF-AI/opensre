import { test } from '@playwright/test';
import { openWidget, reveal } from './helpers';

// "Record meeting" tests. Runs ONLY under the `chrome-media` project (playwright.config), which fakes
// the mic with our WAV and auto-accepts screen/tab capture so the mic + tab-audio flow runs without
// human clicks. Native login first, then the Midscene agent (see hiring-coach.spec.ts for why).
// SCOPE: up to "analyse audio". ⚠ getDisplayMedia auto-capture on CI is unproven (QA-UX-STATUS.md §8).

const RECORD_SECONDS = 12;
const ANALYSIS_START_MS = 90_000;

// T4 · Record via microphone → stop → analysis starts (mic audio = our fake WAV).
test('T4 · record (mic) → stop → analysis starts', async ({ page }) => {
  test.setTimeout(ANALYSIS_START_MS + 240_000);
  const { frame: w, agent: ai } = await openWidget(page);

  await reveal(w, /record meeting/i);
  await ai.aiTap('the "Record meeting" button');
  await ai.aiWaitFor('a recording is in progress — a live waveform/timer and a "Stop" button are shown');

  await page.waitForTimeout(RECORD_SECONDS * 1000); // fake mic plays our clip
  await ai.aiTap('the "Stop" button to end the recording');

  await ai.aiAssert('The widget shows that it is uploading or analysing the recording');
});

// T5 · Record with tab audio → analysis starts. Needs a tab playing the clip + auto-select of that
// source — fixme until validated (QA-UX-STATUS.md §8 step 3).
test.fixme('T5 · record (tab audio) → stop → analysis starts', async () => {});

// DEFERRED (need a completed result): recorded result completes ≤5min, plays back, downloads.
test.fixme('T4b · recorded result completes ≤5min, plays back, downloads', async () => {});
