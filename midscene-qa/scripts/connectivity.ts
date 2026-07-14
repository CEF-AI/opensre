import 'dotenv/config';

// Quick pre-flight: confirms your model env vars are set and the endpoint
// answers a trivial multimodal-capable request before you burn time on a
// full browser run. Run with: npm run connectivity
const base = process.env.MIDSCENE_MODEL_BASE_URL;
const key = process.env.MIDSCENE_MODEL_API_KEY;
const model = process.env.MIDSCENE_MODEL_NAME;

function fail(msg: string): never {
  console.error(`❌ ${msg}`);
  process.exit(1);
}

if (!base) fail('MIDSCENE_MODEL_BASE_URL is not set (copy .env.example to .env)');
if (!key || key.includes('REPLACE_ME')) fail('MIDSCENE_MODEL_API_KEY is not set');
if (!model) fail('MIDSCENE_MODEL_NAME is not set');

console.log(`→ Endpoint: ${base}`);
console.log(`→ Model:    ${model}`);

const res = await fetch(`${base.replace(/\/$/, '')}/chat/completions`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${key}`,
  },
  body: JSON.stringify({
    model,
    messages: [{ role: 'user', content: 'Reply with the single word: pong' }],
    max_tokens: 10,
  }),
});

if (!res.ok) {
  fail(`Endpoint returned ${res.status} ${res.statusText}\n${await res.text()}`);
}

const data = (await res.json()) as { choices?: Array<{ message?: { content?: string } }> };
const reply = data.choices?.[0]?.message?.content?.trim();
console.log(`✅ Model reachable. Reply: "${reply}"`);
