import { test, expect } from '@playwright/test';
import { VAULT_URL } from './helpers';

// ── EXPLORATION WITHOUT THE VISION MODEL ─────────────────────────────────────
// Pure Playwright. The Cere wallet UI is an iframe (wallet.stage.cere.io/authorize),
// reachable via frameLocator — no Midscene / OpenRouter calls. Screenshots are read
// by Claude to map the flow; the vision model is saved for the final test cases.
const AUTH_FRAME = 'iframe[src*="wallet.stage.cere.io/authorize"]';

test('explore (native): Vault → wallet iframe → email login screen', async ({ page }) => {
  const shot = (n: string) => page.screenshot({ path: `test-results/${n}.png`, fullPage: true });

  await page.goto(VAULT_URL);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);

  await page.getByRole('button', { name: /connect wallet/i }).first().click({ timeout: 8000 });
  await page.waitForTimeout(9000);
  await shot('02-connect-modal');

  // Drill into the Cere wallet authorize iframe (native, free).
  const wallet = page.frameLocator(AUTH_FRAME);
  const already = wallet.getByText(/i already have a wallet/i).first();
  await expect(already).toBeVisible({ timeout: 15000 });
  await already.click();
  await page.waitForTimeout(6000);
  await shot('03-after-already-have-wallet');

  // Dump the iframe's interactive elements so we can see the email step.
  const inputs = await wallet.locator('input').all();
  for (const el of inputs) {
    const ph = await el.getAttribute('placeholder').catch(() => null);
    const type = await el.getAttribute('type').catch(() => null);
    const name = await el.getAttribute('name').catch(() => null);
    console.log(`INPUT: type=${type} name=${name} placeholder=${ph}`);
  }
  const btns = await wallet.getByRole('button').all();
  for (const b of btns) console.log('BTN:', JSON.stringify((await b.textContent().catch(() => ''))?.trim()));
  console.log('URL:', page.url());
});
