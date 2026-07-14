import { test } from '@playwright/test';
import { login } from './helpers';
test('login (native) and capture the vault home', async ({ page }) => {
  await login(page);
  await page.waitForTimeout(4000);
  await page.screenshot({ path: 'test-results/10-vault-home.png', fullPage: true });
  console.log('landed URL:', page.url());
  console.log('title:', await page.title());
});
