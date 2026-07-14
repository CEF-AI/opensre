import { test } from './fixture';
import { login } from './helpers';

// REAL TEST: after login, create a new Agent Service and verify it was created.
// Discovered flow: dashboard "Agent Services" -> "+ Create Service" ->
// "Create Agent Service" form with a single required "Name*" field -> "Create".
test('create a new Agent Service and verify it appears', async ({
  page,
  aiInput,
  aiTap,
  aiWaitFor,
  aiAssert,
}) => {
  // Short unique suffix so repeated runs don't collide on the name.
  const serviceName = `poc-agent-${Date.now().toString().slice(-6)}`;

  await login(page, { aiInput, aiTap, aiWaitFor });
  await aiWaitFor('the "Agent Services" dashboard is visible');

  // Open the create form.
  await aiTap('the "+ Create Service" button');
  await aiWaitFor('the "Create Agent Service" form is visible');

  // Fill the required Name field and submit.
  await aiInput(serviceName, 'the "Name" input field in the Create Agent Service form');
  await aiTap('the "Create" button that submits the form');

  // Creating a service submits blockchain extrinsics (a bucket + the service),
  // each needing a wallet signature. Approve every "Signature Request" that
  // appears until none remain. Midscene clicks the popup from the screenshot,
  // even though it's a separate wallet iframe that selectors can't easily reach.
  for (let i = 0; i < 4; i++) {
    try {
      await aiWaitFor(
        'a "Signature Request" wallet dialog with "Sign" and "Reject" buttons is visible',
        { timeoutMs: 25_000, checkIntervalMs: 3_000 },
      );
    } catch {
      break; // no (further) signature prompt appeared
    }
    await aiTap('the "Sign" button in the Signature Request dialog (not "Reject")');
    await page.waitForTimeout(2500);
  }

  // Provisioning is async. Wait for the progress dialog to close (on-chain
  // provisioning done). Don't fail the whole test if the wait is imprecise —
  // the reload + assert below is the real verification.
  await aiWaitFor(
    'the "Creating agent service" progress dialog is no longer visible',
    { timeoutMs: 120_000, checkIntervalMs: 5_000 },
  ).catch(() => { /* dialog may have already redirected/closed */ });

  // KEY: the services list does NOT auto-refresh — reload to see the new one.
  await page.reload();
  await page.waitForLoadState('networkidle');
  await aiWaitFor('the "Agent Services" list is visible after reload');

  // Verify the created service is now really there.
  await aiAssert(
    `an agent service named "${serviceName}" appears in the Agent Services list`,
  );

  console.log(`✅ Created and verified Agent Service: ${serviceName}`);
});
