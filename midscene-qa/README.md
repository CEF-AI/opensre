# Midscene POC — AI/vision-driven web UI automation

A proof-of-concept for [Midscene](https://github.com/web-infra-dev/midscene): you
describe UI interactions in **plain English** ("click the search button", "extract
the product prices") and a multimodal LLM locates the elements from a screenshot —
**no CSS selectors, no XPaths**. When the UI changes, the descriptions still work.

**Target app:** `https://rob.compute.test.ddcdragon.com/#/`

## What this POC demonstrates

| Capability | Where |
|---|---|
| AI perception / page understanding (`aiQuery`) | `e2e/app.spec.ts` |
| Structured data extraction into typed JSON | `e2e/app.spec.ts` |
| Natural-language assertions (`aiAssert`) | `e2e/app.spec.ts` |
| AI-located actions (`aiTap`, `aiInput`) | template at bottom of `e2e/app.spec.ts` |
| Low-code YAML scripting | `yaml/explore.yaml` |
| Interactive HTML report of every AI step | `midscene_run/report/` (after a run) |

`app.spec.ts` is intentionally **exploratory**: it asks the model to describe the
app and list its controls rather than assuming selectors. Run it once, read the
output + HTML report, then turn the discovered UI into concrete `aiTap`/`aiInput`
steps using the template at the bottom of the file.

## Prerequisites

- Node.js 18+ (this repo tested on v24)
- A multimodal LLM reachable via an OpenAI-compatible endpoint (GPT-4o, Qwen 2.5-VL, or self-hosted UI-TARS)

## Setup

```bash
npm install
npx playwright install chromium   # one-time browser download
cp .env.example .env              # then paste your model key into .env
```

Edit `.env` — four variables point Midscene at your model. This POC defaults to
Qwen3-VL via OpenRouter (see `.env.example` for UI-TARS / Qwen2.5-VL / GLM presets).
The `MIDSCENE_MODEL_FAMILY` value is important: it selects the visual-grounding
strategy for your model.

```bash
MIDSCENE_MODEL_BASE_URL="https://openrouter.ai/api/v1"
MIDSCENE_MODEL_API_KEY="sk-or-v1-..."
MIDSCENE_MODEL_NAME="qwen/qwen3-vl-235b-a22b-instruct"
MIDSCENE_MODEL_FAMILY="qwen3-vl"
```

## Run

```bash
npm run connectivity   # verify your model key/endpoint work (fast, no browser)
npm test               # run the Playwright spec against the target app (headless)
npm run test:headed    # watch it drive a visible browser
npm run yaml           # run the low-code YAML version
npm run report         # open the generated HTML report
```

## How it works

- `playwright.config.ts` registers Midscene's reporter and gives AI steps generous timeouts.
- `e2e/fixture.ts` extends Playwright's `test` with the `ai*` fixtures via `PlaywrightAiFixture`.
- Each `ai*` call screenshots the page, sends it + your instruction to the model, and
  acts on the returned coordinates/data. Every step is logged to `midscene_run/`.

## Notes & gotchas for evaluation

- **Cost/latency:** every AI step is one (or more) LLM vision call — expect a few
  seconds per step. Midscene supports a cache to replay element locations and cut
  repeat-run cost; enable it once the flow is stable.
- **Model choice matters:** grounding accuracy varies a lot by model. Qwen 2.5-VL
  and UI-TARS are tuned for GUI localization; general models like GPT-4o work but
  can be less precise on dense layouts.
- **Live sites drift:** the demos hit public sites (eBay, TodoMVC). If a site adds
  bot-protection or changes wording, tweak the natural-language prompts — that's the
  whole point: you edit English, not selectors.
