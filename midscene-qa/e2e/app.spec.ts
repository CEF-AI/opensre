import { expect } from '@playwright/test';
import { test } from './fixture';

// Target app for the POC (hash-routed SPA).
const APP_URL = 'https://rob.compute.test.ddcdragon.com/#/';

// ── EXPLORATORY SPEC ─────────────────────────────────────────────────────────
// We don't hard-code any selectors or assumptions about this app's UI. Instead
// we let Midscene look at the rendered screenshot and TELL us what's there. Run
// this once, read the console output + the HTML report (midscene_run/report/),
// then tighten the steps below into concrete actions/assertions for your app.
test('explore: perceive and describe the app landing page', async ({
  page,
  aiQuery,
  aiAssert,
}) => {
  await page.goto(APP_URL);
  // SPA: wait for the client-side app to finish rendering, not just network.
  await page.waitForLoadState('networkidle');

  // 1) Ask the model what the page actually is.
  const overview = await aiQuery<{
    pageTitle: string;
    purpose: string;
    primaryActions: string[];
    visibleSections: string[];
  }>(
    'Summarize this page: { pageTitle: string, purpose: string, ' +
      'primaryActions: string[] (buttons/links a user can click), ' +
      'visibleSections: string[] (major UI regions) }',
  );
  console.log('\n===== PAGE OVERVIEW (from Qwen3-VL) =====');
  console.log(JSON.stringify(overview, null, 2));
  console.log('=========================================\n');

  // 2) Enumerate interactive elements so we know what we can drive next.
  const controls = await aiQuery<Array<{ label: string; kind: string }>>(
    'every interactive control on screen as { label: string, kind: "button"|"link"|"input"|"tab"|"other" }',
  );
  console.log('Interactive controls:', JSON.stringify(controls, null, 2));

  // 3) A deliberately loose assertion — just that the app rendered something,
  //    not an error/blank screen. Tighten this once we know the real content.
  expect(controls.length).toBeGreaterThan(0);
  await aiAssert(
    'the page has rendered real application content (not a blank page, ' +
      'not a loading spinner only, and not an error message)',
  );
});

// ── NEXT STEP (template) ─────────────────────────────────────────────────────
// Once the exploration above reveals the UI, replace this with a real flow, e.g.:
//
// test('drive a real action', async ({ aiInput, aiTap, aiAssert }) => {
//   await page.goto(APP_URL);
//   await aiInput('<value>', '<the field described in plain English>');
//   await aiTap('<the button described in plain English>');
//   await aiAssert('<what should now be true on screen>');
// });
