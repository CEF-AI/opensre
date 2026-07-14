import { test, expect } from '@playwright/test';
import { openWidget } from './helpers';

// Hiring Coach — "Record meeting" tests. Runs ONLY under the `chrome-media` project (see
// playwright.config.ts), which fakes the microphone with our WAV and auto-accepts screen/tab
// capture, so the mic + tab-audio flow (getUserMedia + getDisplayMedia) runs without human clicks.
//
// SCOPE (for now): up to "analyse audio" — Record → recording UI → Stop → analysis starts.
// Result-completion + playback are deferred (backend processing >5min on qaagent-vault; see
// QA-UX-STATUS.md).
//
// ⚠ NOT YET VALIDATED LIVE: getDisplayMedia auto-capture (--auto-accept-this-tab-capture) in headed
// Chrome via Playwright is unproven here. If the tab dialog still blocks, options in QA-UX-STATUS.md.

const RECORD_SECONDS = 12; // keep short — the 5-min deadline should never be needed for this
const ANALYSIS_START_MS = 90_000;

async function startRecordingAndStop(page: import('@playwright/test').Page) {
  const w = await openWidget(page);

  // Start recording. Fake-media flags auto-grant the mic and auto-accept the tab-capture dialog.
  await w.getByText(/record meeting/i).first().click({ timeout: 10_000 });

  // Recording UI: waveform + timer + a Stop control (from the widget: "⏸ Stop").
  const stop = w.getByRole('button', { name: /stop/i }).first();
  await expect(stop).toBeVisible({ timeout: 20_000 });

  // Record for a short, fixed window (the fake mic plays our clip), then stop.
  await page.waitForTimeout(RECORD_SECONDS * 1000);
  await stop.click({ timeout: 10_000 });

  // Reaches upload/analysis — audio captured + ASR kicked off (the "till analyse audio" checkpoint).
  await expect(w.getByText(/uploading|analyz/i).first()).toBeVisible({ timeout: ANALYSIS_START_MS });
  return w;
}

// T4 · Record via microphone → stop → analysis starts (mic audio = our fake WAV).
test('T4 · record (mic) → stop → analysis starts', async ({ page }) => {
  test.setTimeout(ANALYSIS_START_MS + 240_000);
  await startRecordingAndStop(page);
});

// T5 · Record with tab audio → analysis starts. Same flow; the distinguishing input is a tab
// playing known audio. Needs --auto-select-desktop-capture-source + a tab playing the clip — kept as
// fixme until the tab-audio capture path is validated (see QA-UX-STATUS.md).
test.fixme('T5 · record (tab audio) → stop → analysis starts', async () => {});

// DEFERRED (need a completed result): T4b · recorded result completes within the 5-min deadline,
// plays back, and downloads. Blocked on backend processing speed.
test.fixme('T4b · recorded result completes ≤5min, plays back, downloads', async () => {});
