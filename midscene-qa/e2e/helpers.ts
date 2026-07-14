import type { Page, Frame } from '@playwright/test';

// Target: the Vault UI (where a hiring-coach run's result / widget is rendered to the user).
export const VAULT_URL = process.env.VAULT_URL ?? 'https://vault.compute.test.ddcdragon.com/';
export const TEST_EMAIL = process.env.VAULT_TEST_EMAIL ?? 'qaagent@cere.io';
export const TEST_OTP = process.env.VAULT_TEST_OTP ?? '555555';

// The deployed Manykind hiring agent (hiring-coach only, no Lab) — the widget users see.
export const AGENT_URL =
  process.env.AGENT_URL ??
  'https://vault.compute.test.ddcdragon.com/agents/148c4da814d9a7e9f5ffe8fb934c29b8931bdc49b9503d21d9454c869c77d5ac%3Ahiring-coach-lab2';

// The Hiring Coach — Result widget renders in a sandboxed iframe hosted on the S3 gateway.
export const WIDGET_IFRAME = 'iframe[src*="hiringcoach-public/widgets"]';

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
  // Provisioning your encrypted storage"). qaagent's vault provisions fresh each
  // login, so wait for that screen to APPEAR, then to CLEAR — otherwise a
  // check-too-early passes instantly and we navigate mid-provision (→ Connect wallet).
  const provisioning = page
    .getByText(/provisioning your encrypted storage|creating security vault/i)
    .first();
  await provisioning.waitFor({ state: 'visible', timeout: 30000 }).catch(() => {
    /* already provisioned / never showed */
  });
  await provisioning.waitFor({ state: 'hidden', timeout: 180000 }).catch(() => {
    /* took longer than 3 min, or never showed */
  });
  await page.waitForTimeout(4000);
}

// Log in, open the deployed hiring agent, grant the one-time widget-signing permission, and return
// the widget's iframe Frame (with its "Analyze an interview" input view ready). Shared by T1–T5.
export async function openWidget(page: Page): Promise<Frame> {
  await login(page);

  await page.goto(AGENT_URL);
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(5000);

  // One-time "Allow widgets to sign with your wallet?" — the widget can't load data until allowed.
  const allow = page.getByRole('button', { name: /^allow$/i }).first();
  if (await allow.count().catch(() => 0)) {
    await allow.click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(5000);
  }

  // Wait for the widget iframe to appear AND its input view to render. Generous, because on a cold
  // first login the vault provisions fresh and the widget can take a while to load its content.
  const deadline = Date.now() + 150_000;
  while (Date.now() < deadline) {
    // A late "Allow" prompt can still appear as the widget initializes — dismiss it if so.
    const late = page.getByRole('button', { name: /^allow$/i }).first();
    if (await late.count().catch(() => 0)) await late.click({ timeout: 4000 }).catch(() => {});
    const f = page.frames().find((fr) => /hiringcoach-public\/widgets/.test(fr.url()));
    if (f && (await f.getByText(/analyze an interview/i).first().count().catch(() => 0))) return f;
    await page.waitForTimeout(3000);
  }
  throw new Error('Hiring Coach widget frame ("Analyze an interview") never appeared');
}

// Bring a widget element into the viewport before a Midscene vision step. The widget is a tall
// iframe, and Midscene reasons over the VISIBLE viewport screenshot — if the target renders below
// the fold (or lazy-loads), the model can't see it. Scroll it into view + settle first. Native +
// free; matches the Midscene docs' "confirm readiness / scroll into view before asserting" guidance.
export async function reveal(frame: Frame, re: RegExp, settleMs = 700): Promise<void> {
  await frame
    .getByText(re)
    .first()
    .scrollIntoViewIfNeeded({ timeout: 8000 })
    .catch(() => {
      /* not present yet / already in view — the AI step will still try */
    });
  await frame.page().waitForTimeout(settleMs);
}
