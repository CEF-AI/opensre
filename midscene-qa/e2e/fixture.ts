import { test as base } from '@playwright/test';
import type { PlayWrightAiFixtureType } from '@midscene/web/playwright';
import { PlaywrightAiFixture } from '@midscene/web/playwright';

// Extends Playwright's `test` with Midscene's AI helpers, which then become
// available as fixtures on the test callback:
//   aiTap, aiInput, aiHover, aiScroll, aiKeyboardPress  -> AI-located actions
//   ai / aiAction                                        -> multi-step auto-planning
//   aiQuery / aiBoolean / aiNumber / aiString            -> extract data from the page
//   aiAssert                                             -> natural-language assertions
//   aiWaitFor                                            -> wait until a condition is visually true
export const test = base.extend<PlayWrightAiFixtureType>(
  PlaywrightAiFixture({
    waitForNetworkIdleTimeout: 2000,
    replanningCycleLimit: 30,
  }),
);
