import { test } from './fixture';
import { VAULT_URL } from './helpers';

// ── EXPLORE THE CONNECT / LOGIN FLOW ─────────────────────────────────────────
// The Vault opens on a "Connect wallet" screen. Capture screenshots at each step
// so we can see the actual UI and design the real login precisely.
test('explore: Vault connect / login flow', async ({ page, aiTap, aiQuery }) => {
  await page.goto(VAULT_URL);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: 'test-results/01-landing.png', fullPage: true });

  await aiTap('the "Connect wallet" button');
  await page.waitForTimeout(10000); // let the wallet modal + its panels render
  await page.screenshot({ path: 'test-results/02-connect-modal.png', fullPage: true });

  // qaagent already has a wallet → take the "I already have a wallet" path.
  await aiTap('the "I already have a wallet" button');
  await page.waitForTimeout(8000);
  await page.screenshot({ path: 'test-results/03-existing-wallet.png', fullPage: true });

  const emailStep = await aiQuery<{
    everythingVisible: string;
    inputs: string[];
    buttons: string[];
    anyEmailField: boolean;
  }>(
    'Describe the ENTIRE screen: ' +
      '{ everythingVisible: string, inputs: string[] (each field + its label/placeholder), ' +
      'buttons: string[], anyEmailField: boolean }',
  );
  console.log('\n===== AFTER "I already have a wallet" =====\n' + JSON.stringify(emailStep, null, 2));
  console.log('URL:', page.url());
});
