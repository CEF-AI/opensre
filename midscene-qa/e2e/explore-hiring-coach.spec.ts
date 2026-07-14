import { test } from '@playwright/test';
import { login } from './helpers';

// ── EXPLORATION of the Hiring Coach — Result widget (native, no vision model) ──
// Logs in (qaagent@cere.io / 555555), jumps straight to the deployed Manykind
// hiring agent, and captures screenshots + DOM structure at each state so the
// real test cases (T1–T5) can use robust selectors. Screenshots are read by
// Claude — the OpenRouter vision model is saved for the final Midscene tests.

const AGENT_URL =
  'https://vault.compute.test.ddcdragon.com/agents/148c4da814d9a7e9f5ffe8fb934c29b8931bdc49b9503d21d9454c869c77d5ac%3Ahiring-coach-lab2';

test('explore: Hiring Coach — Result widget', async ({ page }) => {
  test.setTimeout(300_000);
  const shot = (n: string) => page.screenshot({ path: `test-results/hc-${n}.png`, fullPage: true });
  const dump = async (label: string) => {
    const els = await page
      .locator('h1,h2,h3,button,[role=button],a')
      .allTextContents()
      .catch(() => [] as string[]);
    const clean = [...new Set(els.map((t) => t.trim()).filter(Boolean))].slice(0, 80);
    console.log(`\n=== ${label} @ ${page.url()} ===\n` + JSON.stringify(clean, null, 0));
  };

  // 1) Log in and land in the vault.
  await login(page);
  await shot('01-after-login');
  await dump('after-login');

  // 2) Jump straight to the deployed hiring agent.
  await page.goto(AGENT_URL);
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.waitForTimeout(6000);
  await shot('02-agent-page');
  await dump('agent-page');

  // 2b) Grant the one-time "Allow widgets to sign with your wallet?" permission — the widget can't
  //     load its data (Recent events / result) until this is allowed.
  const allow = page.getByRole('button', { name: /^allow$/i }).first();
  if (await allow.count().catch(() => 0)) {
    console.log('→ clicking "Allow" (widget signing permission)');
    await allow.click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(6000);
  }
  await shot('02b-after-allow');
  await dump('after-allow');

  // 3) The widget renders in an IFRAME — enumerate frames to find it.
  console.log('\n=== FRAMES ===');
  for (const f of page.frames()) console.log(`FRAME url=${f.url()} name=${f.name()}`);

  // Pick the widget frame: the one that contains the widget UI text.
  let widget = page.mainFrame();
  for (const f of page.frames()) {
    const hit = await f.getByText(/analyze an interview|record meeting/i).first().count().catch(() => 0);
    if (hit) { widget = f; break; }
  }
  console.log(`\n>>> widget frame: ${widget.url()}`);

  // 4) Dump the widget frame's interactive elements + inputs (for selectors).
  const wtexts = await widget.locator('h1,h2,h3,button,[role=button],a').allTextContents().catch(() => []);
  console.log('WIDGET ELEMENTS:', JSON.stringify([...new Set(wtexts.map((t) => t.trim()).filter(Boolean))].slice(0, 60)));
  for (const el of await widget.locator('input,textarea').all()) {
    const ph = await el.getAttribute('placeholder').catch(() => null);
    const type = await el.getAttribute('type').catch(() => null);
    if (ph || type) console.log(`WIDGET INPUT: type=${type} placeholder=${ph}`);
  }
  for (const label of ['Record meeting', 'Import recording', 'Analyze an interview', 'Recent events', 'Authenticity']) {
    const n = await widget.getByText(new RegExp(label, 'i')).first().count().catch(() => 0);
    console.log(`widget present? "${label}": ${n > 0}`);
  }
  await shot('03-widget-input');

  // 5) Recent events in this (fresh qaagent) vault? Click one if present to see the result view.
  const firstEvent = widget.getByText(/authenticity/i).first();
  if (await firstEvent.count().catch(() => 0)) {
    await firstEvent.click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(5000);
    await shot('04-result-view');
    const audios = await widget.locator('audio').count().catch(() => 0);
    console.log(`audio elements in widget: ${audios}`);
  } else {
    console.log('no recent event in qaagent vault (fresh) — T2/T3 will record fresh first');
  }
});
