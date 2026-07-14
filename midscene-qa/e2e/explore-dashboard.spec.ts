import { test } from './fixture';
import { login } from './helpers';

// EXPLORATORY (throwaway): log in, open the "+ Create Service" form, and have
// Qwen3-VL describe every field + the submit control, so we can author accurate
// aiInput/aiTap steps for the real test.
test('explore: describe the Create Service form', async ({
  page,
  aiInput,
  aiTap,
  aiWaitFor,
  aiQuery,
}) => {
  await login(page, { aiInput, aiTap, aiWaitFor });
  await page.waitForLoadState('networkidle');
  await aiWaitFor('the Agent Services dashboard is visible');

  await aiTap('the "+ Create Service" button');
  await aiWaitFor('a form or dialog for creating a new agent service is visible');

  const form = await aiQuery<{
    title: string;
    fields: Array<{ label: string; kind: string; required: boolean; options?: string[] }>;
    submitButton: string;
    isMultiStep: boolean;
  }>(
    'Describe the create-agent-service form: { title: string, ' +
      'fields: [{ label: string, kind: "text"|"select"|"toggle"|"radio"|"number"|"other", ' +
      'required: boolean, options?: string[] (for selects/radios) }], ' +
      'submitButton: string (the label that submits/creates), ' +
      'isMultiStep: boolean (is this a wizard with Next steps?) }',
  );
  console.log('\n===== CREATE SERVICE FORM (Qwen3-VL) =====');
  console.log(JSON.stringify(form, null, 2));
  console.log('==========================================\n');
});
