import { test as base } from '@playwright/test';
import { PlaywrightAiFixture } from '@midscene/web/playwright';
import { openWidget } from './helpers';

// Midscene smoke test: proves the vision path (Qwen3-VL via OpenRouter) works end-to-end against the
// live widget. Native login/openWidget (free) gets us there; a single aiAssert exercises Midscene.
// Midscene reasons over a screenshot, so it can "see" the widget even though it's in an iframe.
const test = base.extend(PlaywrightAiFixture());

test('midscene smoke · sees the widget input view', async ({ page, aiAssert }) => {
  test.setTimeout(300_000);
  await openWidget(page);
  await aiAssert(
    'A "Hiring Coach" interview-analysis widget is shown, offering an "Import recording" option and a "Record meeting" option',
  );
});
