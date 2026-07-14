import { test as base } from '@playwright/test';
import { PlaywrightAiFixture } from '@midscene/web/playwright';
import { openWidget } from './helpers';

// "Record meeting" tests, FULLY AI. Runs ONLY under the `chrome-media` project (playwright.config),
// which fakes the mic with our WAV and auto-accepts screen/tab capture so the mic + tab-audio flow
// (getUserMedia + getDisplayMedia) runs without human clicks. SCOPE: up to "analyse audio".
//
// ⚠ NOT YET VALIDATED LIVE: getDisplayMedia auto-capture in headed Chrome via Playwright is unproven
// (QA-UX-STATUS.md §8 step 2). Result-completion + playback are deferred (§6).
const test = base.extend(PlaywrightAiFixture());

const RECORD_SECONDS = 12;
const ANALYSIS_START_MS = 90_000;

// T4 · Record via microphone → stop → analysis starts (mic audio = our fake WAV).
test('T4 · record (mic) → stop → analysis starts', async ({ page, aiTap, aiAssert, aiWaitFor }) => {
  test.setTimeout(ANALYSIS_START_MS + 240_000);
  await openWidget(page);

  await aiTap('the "Record meeting" button');
  // Fake-media flags auto-grant the mic + auto-accept the tab-capture dialog; recording should start.
  await aiWaitFor('a recording is in progress — a live waveform/timer and a "Stop" button are shown');

  await page.waitForTimeout(RECORD_SECONDS * 1000); // fake mic plays our clip
  await aiTap('the "Stop" button to end the recording');

  await aiAssert('The widget shows that it is uploading or analysing the recording');
});

// T5 · Record with tab audio → analysis starts. Needs a tab playing the clip + auto-select of that
// source — kept as fixme until validated (QA-UX-STATUS.md §8 step 3).
test.fixme('T5 · record (tab audio) → stop → analysis starts', async () => {});

// DEFERRED (need a completed result): recorded result completes ≤5min, plays back, downloads.
test.fixme('T4b · recorded result completes ≤5min, plays back, downloads', async () => {});
