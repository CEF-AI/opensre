import type { Page, Frame } from '@playwright/test';
import { PlaywrightAgent } from '@midscene/web/playwright';

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

async function waitForAuthFrame(page: Page, timeoutMs = 25000): Promise<Frame> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const f = authFrame(page);
    if (f) return f;
    await page.waitForTimeout(1000);
  }
  throw new Error('Cere wallet authorize iframe never appeared');
}

// State-based wait (the Midscene way). With an agent, `aiWaitFor` vision-polls until the DESCRIBED
// state is true — the fix for CI flakiness: we wait on what's actually on screen (vault provisioned,
// widget rendered), not a fixed clock. Without an agent (old specs), fall back to a fixed sleep.
async function waitFor(page: Page, agent: PlaywrightAgent | undefined, desc: string, timeout: number, fallbackMs: number) {
  if (agent) {
    await agent.aiWaitFor(desc, { timeout }).catch(() => {
      /* aiWaitFor throws on timeout; the following native step still tries/asserts */
    });
  } else {
    await page.waitForTimeout(fallbackMs);
  }
}

// Login to the Vault via the Cere embedded wallet. Clicks/fills stay native (deterministic iframe
// locators); the WAITS use aiWaitFor when an agent is passed (state-based, robust on slow CI) —
// especially the fresh-vault provisioning wait that was the CI flake.
export async function login(page: Page, agent?: PlaywrightAgent): Promise<void> {
  await page.goto(VAULT_URL);
  await page.waitForLoadState('networkidle').catch(() => {});
  await waitFor(page, agent, 'a "Connect wallet" button is visible', 60_000, 3000);

  await page.getByRole('button', { name: /connect wallet/i }).first().click({ timeout: 20000 });

  await waitFor(page, agent, 'a Cere wallet dialog with an "I already have a wallet" option is visible', 60_000, 4000);
  let auth = await waitForAuthFrame(page);
  await auth.getByText(/i already have a wallet/i).first().click({ timeout: 20000 });

  await waitFor(page, agent, 'an email input field and a "Sign In" button are visible', 60_000, 4000);
  auth = await waitForAuthFrame(page);
  await auth.locator('input[type=email]').first().fill(TEST_EMAIL);
  await auth.getByText(/^sign in$/i).first().click();

  await waitFor(page, agent, 'a 6-digit verification code entry field is visible', 60_000, 5000);
  auth = await waitForAuthFrame(page);
  await auth.locator('input[maxlength="6"]').first().fill(TEST_OTP);
  await auth.getByText(/^verify$/i).first().click();

  // The vault provisions fresh ("Creating security vault"). Wait for the DASHBOARD to be ready — the
  // exact step that flaked on CI when we used a fixed timeout.
  await waitFor(
    page,
    agent,
    'the vault dashboard is fully loaded — the left sidebar shows "Explore agents" and the scopes list — and any "Creating security vault" / "Provisioning your encrypted storage" screen is gone',
    200_000,
    12000,
  );
}

// Log in, open the deployed hiring agent, grant the one-time widget-signing permission, and return
// the widget's iframe Frame + the Midscene agent (reused by the test). Waits are aiWaitFor-driven.
export async function openWidget(page: Page): Promise<{ frame: Frame; agent: PlaywrightAgent }> {
  // Bump the network-idle wait so Midscene tolerates the slow CI → test-cluster path after actions.
  const agent = new PlaywrightAgent(page, { waitForNetworkIdleTimeout: 20_000 });

  await login(page, agent);

  await page.goto(AGENT_URL);
  await page.waitForLoadState('networkidle').catch(() => {});

  // One-time "Allow widgets to sign with your wallet?" — native click; may appear now or during init.
  const clickAllow = async () => {
    const a = page.getByRole('button', { name: /^allow$/i }).first();
    if (await a.count().catch(() => 0)) await a.click({ timeout: 8000 }).catch(() => {});
  };
  await agent.aiWaitFor('the "Manykind - Hiring Assistant" agent page is shown (Widgets/Activity tabs), or a "Allow widgets to sign with your wallet?" dialog is visible', { timeout: 120_000 }).catch(() => {});
  await clickAllow();

  // Wait — by state — for the widget's input view to actually render (vision sees the iframe).
  await agent.aiWaitFor('the Hiring Coach widget shows the "Analyze an interview" view with "Import recording" and "Record meeting" options', { timeout: 150_000 });
  await clickAllow(); // late-prompt guard

  const frame = page.frames().find((fr) => /hiringcoach-public\/widgets/.test(fr.url()));
  if (!frame) throw new Error('Hiring Coach widget iframe not found after it rendered');
  return { frame, agent };
}

// Bring a widget element into the viewport before a Midscene vision step. The widget is a tall
// iframe, and Midscene reasons over the VISIBLE viewport screenshot — if the target renders below
// the fold (or lazy-loads), the model can't see it. Scroll it into view + settle first.
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
