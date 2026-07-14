import { expect, test, type Locator } from '@playwright/test';

// Deterministic-first, AI-fallback helpers. Each tries the fast/free native Playwright action; if it
// fails (element moved/restructured, selector broke because the widget changed), it falls back to
// Midscene's vision model and LOGS the fallback — a triggered fallback is a signal that the native
// selector drifted and should be refreshed. Returns which path was used ('native' | 'ai').

type Tap = (prompt: string) => Promise<void>;
type Input = (value: string, prompt: string) => Promise<void>;
type Assert = (prompt: string) => Promise<void>;

const NATIVE_TIMEOUT = Number(process.env.SMART_NATIVE_TIMEOUT_MS ?? 6000);

function fellBack(what: string, ai: string, err: unknown): void {
  const msg = err instanceof Error ? err.message.split('\n')[0] : String(err);
  console.log(`[smart] native ${what} failed (${msg}) → AI fallback: "${ai}"`);
}

// Each helper runs inside a named test.step, so BOTH the native and the AI-fallback path show up as
// a labelled step in the Playwright HTML report/trace (with screenshots) — the deterministic steps
// are no longer invisible. The step title records which path was taken.

export async function smartClick(native: Locator, aiTap: Tap, ai: string, timeout = NATIVE_TIMEOUT) {
  return test.step(`click · ${ai}`, async () => {
    try {
      await native.click({ timeout });
      return 'native' as const;
    } catch (e) {
      fellBack('click', ai, e);
      await aiTap(ai);
      return 'ai' as const;
    }
  });
}

export async function smartFill(
  native: Locator,
  value: string,
  aiInput: Input,
  ai: string,
  timeout = NATIVE_TIMEOUT,
) {
  return test.step(`fill · ${ai}`, async () => {
    try {
      await native.fill(value, { timeout });
      return 'native' as const;
    } catch (e) {
      fellBack('fill', ai, e);
      await aiInput(value, ai);
      return 'ai' as const;
    }
  });
}

export async function smartVisible(native: Locator, aiAssert: Assert, ai: string, timeout = NATIVE_TIMEOUT) {
  return test.step(`assert · ${ai}`, async () => {
    try {
      await expect(native).toBeVisible({ timeout });
      return 'native' as const;
    } catch (e) {
      fellBack('assert-visible', ai, e);
      await aiAssert(ai); // throws if the model also can't confirm — a real failure
      return 'ai' as const;
    }
  });
}
