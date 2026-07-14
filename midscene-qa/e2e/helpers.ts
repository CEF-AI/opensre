import type { Page, Frame } from '@playwright/test';

// Target: the Vault UI (where a hiring-coach run's result / widget is rendered to the user).
export const VAULT_URL = process.env.VAULT_URL ?? 'https://vault.compute.test.ddcdragon.com/';
export const TEST_EMAIL = process.env.VAULT_TEST_EMAIL ?? 'qaagent@cere.io';
export const TEST_OTP = process.env.VAULT_TEST_OTP ?? '555555';

// The Cere embedded-wallet login runs in a cross-origin iframe (wallet.stage.cere.io/authorize).
const authFrame = (page: Page): Frame | undefined =>
  page.frames().find((f) => /cere\.io\/authorize/.test(f.url()));

async function waitForAuthFrame(page: Page, timeoutMs = 20000): Promise<Frame> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const f = authFrame(page);
    if (f) return f;
    await page.waitForTimeout(1000);
  }
  throw new Error('Cere wallet authorize iframe never appeared');
}

// Native (no vision-model) login to the Vault via the Cere embedded wallet:
//   Connect wallet → "I already have a wallet" → email + Sign In → OTP + Verify.
// Deterministic + free (no OpenRouter calls). The second arg is ignored — kept so
// older AI-based callers don't break.
export async function login(page: Page, _ai?: unknown): Promise<void> {
  await page.goto(VAULT_URL);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);

  await page.getByRole('button', { name: /connect wallet/i }).first().click({ timeout: 15000 });

  let auth = await waitForAuthFrame(page);
  await auth.getByText(/i already have a wallet/i).first().click({ timeout: 15000 });
  await page.waitForTimeout(4000);

  auth = await waitForAuthFrame(page);
  await auth.locator('input[type=email]').first().fill(TEST_EMAIL);
  await auth.getByText(/^sign in$/i).first().click();
  await page.waitForTimeout(5000);

  auth = await waitForAuthFrame(page);
  await auth.locator('input[maxlength="6"]').first().fill(TEST_OTP);
  await auth.getByText(/^verify$/i).first().click();

  // Wallet connects, then the vault provisions ("Creating security vault /
  // Provisioning your encrypted storage"). Wait for that to clear before returning.
  await page.waitForTimeout(6000);
  await page
    .getByText(/provisioning your encrypted storage|creating security vault/i)
    .first()
    .waitFor({ state: 'hidden', timeout: 120000 })
    .catch(() => {
      /* provisioning screen may not have appeared (already provisioned) */
    });
  await page.waitForTimeout(3000);
}
