import { expect } from '@playwright/test';
import { test } from './fixture';

const APP_URL = 'https://rob.compute.test.ddcdragon.com/#/';

// Test credentials for the ROB app (fixed test OTP).
const TEST_EMAIL = process.env.ROB_TEST_EMAIL ?? 'demo+100@cere.network';
const TEST_OTP = process.env.ROB_TEST_OTP ?? '555555';

// ── LOGIN FLOW ───────────────────────────────────────────────────────────────
// Email gate -> CONTINUE -> OTP screen -> enter code -> logged in.
// Every step is described in natural language; no selectors.
test('log in to the ROB app with email + OTP', async ({
  page,
  aiInput,
  aiTap,
  aiAssert,
  aiWaitFor,
}) => {
  await page.goto(APP_URL);
  await page.waitForLoadState('networkidle');
  await aiWaitFor('the email login form is fully visible and interactive');

  // Step 1 — email
  await aiInput(TEST_EMAIL, 'the "Email Address" input field');
  await aiTap('the CONTINUE button');

  // Step 2 — OTP
  await aiWaitFor('a one-time code / verification code input is visible on screen');
  // Many OTP screens use several single-digit boxes; describe the intent and let
  // the model place the digits. If the app uses separate boxes and this mis-fills,
  // switch to: await aiKeyboardPress for each digit, or aiInput per box.
  await aiInput(TEST_OTP, 'the verification code / OTP input');
  // Some OTP forms auto-submit on the last digit; others need a button. Try the
  // button but don't fail if it has already advanced.
  await page
    .waitForTimeout(500)
    .then(() => aiTap('the verify / continue / submit button for the code'))
    .catch(() => { /* auto-submitted, no button to press */ });

  // Step 3 — assert we're inside the app
  await aiWaitFor('the page has navigated past the email/OTP entry screens');
  await aiAssert(
    'the user is logged in and the main application UI is visible, ' +
      'not the email or OTP entry form',
  );

  // Bonus: capture what the post-login screen actually is, to guide next steps.
  const landed = await page.title();
  console.log('Post-login page title:', landed);
});
