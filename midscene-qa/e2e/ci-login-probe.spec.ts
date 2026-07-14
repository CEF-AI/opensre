import { test, expect } from '@playwright/test';
import { openWidget } from './helpers';

// CI LOGIN PROBE — the single riskiest thing about running UX on CI: does the Cere wallet login
// (Connect wallet → iframe → email → OTP → fresh-vault provisioning) and the widget render survive
// on a headless ubuntu runner under xvfb? This uses a NATIVE assertion only (no Midscene/OpenRouter)
// so it isolates the login/render question from the vision-model question. If this is green on CI,
// the full UX job is safe to wire.
test('CI probe · Cere login + widget loads (native, no OpenRouter)', async ({ page }) => {
  test.setTimeout(300_000);
  const w = await openWidget(page);
  await expect(w.getByText(/analyze an interview/i).first()).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: 'test-results/ci-login-probe.png', fullPage: true });
});
