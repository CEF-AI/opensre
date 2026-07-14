import { test } from './fixture';
import { login } from './helpers';

// Read-only: log in, RELOAD (the list doesn't auto-refresh), then dump every
// Agent Service so we can see whether any "poc-agent-*" services were created.
test('list all agent services', async ({
  page,
  aiInput,
  aiTap,
  aiWaitFor,
  aiQuery,
}) => {
  await login(page, { aiInput, aiTap, aiWaitFor });
  await aiWaitFor('the "Agent Services" dashboard/list is visible');

  // The list only shows newly-created services after a manual refresh.
  await page.reload();
  await page.waitForLoadState('networkidle');
  await aiWaitFor('the "Agent Services" list is visible after reload');
  await page.mouse.wheel(0, 2000).catch(() => {});
  await page.waitForTimeout(1000);

  const services = await aiQuery<Array<{ name: string; created: string }>>(
    'every agent service shown on the page as { name: string, created: string } ' +
      '(read the exact visible name text and the "Created:" timestamp for each)',
  );
  console.log('\n===== ALL AGENT SERVICES =====');
  console.log(JSON.stringify(services, null, 2));
  console.log(
    'poc-agent-* present:',
    services.filter((s) => /poc-agent/i.test(s.name)).map((s) => s.name),
  );
  console.log('==============================\n');
});
