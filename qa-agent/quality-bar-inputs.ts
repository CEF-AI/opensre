// Canonical inputs for the hiring-coach daily quality bar.
//
// 16 clips × 3 dimensions (authenticity / clarity / engagement).
// Each dimension has an expected threshold expression:
//   '>0.60' → pass iff actual > 0.60
//   '<0.40' → pass iff actual < 0.40
//   'observe' → always FAIL (there is no "skip" — forces proper labeling)
//
// A clip passes iff all three dimension checks pass. Aggregate = passes / total.

export interface QualityBarClip {
  clip_code: string;
  stem: string;
  name: string;
  chunks: string[];
  expected: {
    authenticity: string;
    clarity: string;
    engagement: string;
  };
}

export const QUALITY_BAR_CLIPS: readonly QualityBarClip[] = [
  { clip_code: 'HIA-A2',  stem: 'heinrich_clarity_good', name: 'Heinrich — Reading false positive',           chunks: ['000','001','002','003','004'], expected: { authenticity: '<0.40', clarity: '>0.75', engagement: '<0.50' } },
  { clip_code: 'HIA-A3',  stem: 'b3_reading_ai',         name: 'Batch 3 - Reading AI from screen',           chunks: ['000','001'],                   expected: { authenticity: '>0.60', clarity: '>0.40', engagement: '<0.30' } },
  { clip_code: 'HIA-A8',  stem: 'b3_ceo_interview',      name: 'Batch 3 - CEO interview',                    chunks: ['000','001'],                   expected: { authenticity: '<0.40', clarity: '>0.80', engagement: '<0.30' } },
  { clip_code: 'HIA-A10', stem: 'b3_bad_presentation',   name: 'Batch 3 - Bad presentation (reading)',       chunks: ['000','001','002'],             expected: { authenticity: '>0.60', clarity: '<0.50', engagement: '<0.30' } },
  { clip_code: 'HIA-C1',  stem: 'heinrich_clarity_good', name: 'Heinrich — High Clarity',                    chunks: ['000','001','002','003','004'], expected: { authenticity: '<0.40', clarity: '>0.75', engagement: '<0.50' } },
  { clip_code: 'HIA-C7',  stem: 'Saad_clarity_zero',     name: 'Saad — Very Low Clarity',                    chunks: ['000','001','002','003','004'], expected: { authenticity: '<0.40', clarity: '<0.40', engagement: '<0.25' } },
  { clip_code: 'HIA-C9',  stem: 'b3_ceo_interview',      name: 'Batch 3 - CEO interview (clarity axis)',     chunks: ['000','001'],                   expected: { authenticity: '<0.40', clarity: '>0.80', engagement: '<0.30' } },
  { clip_code: 'HIA-C10', stem: 'b4_caitlin_upton',      name: 'Batch 4 - Miss Teen USA word salad',         chunks: ['000','001'],                   expected: { authenticity: '<0.40', clarity: '<0.30', engagement: '<0.30' } },
  { clip_code: 'HIA-C14', stem: 'b4_boris_johnson',      name: 'Batch 4 - Boris Johnson Peppa Pig ramble',   chunks: ['000','001','002','003'],       expected: { authenticity: '<0.40', clarity: '<0.30', engagement: '<0.30' } },
  { clip_code: 'HIA-C16', stem: 'star_csm_agency',       name: 'STAR - CSM agency (genuine)',                chunks: ['000','001','002','003','004','005'], expected: { authenticity: '<0.40', clarity: '>0.70', engagement: '<0.40' } },
  { clip_code: 'HIA-C17', stem: 'star_consulting_sia',   name: 'STAR - Consulting mock (scripted prep)',     chunks: ['000','001','002','003','004'], expected: { authenticity: '>0.40', clarity: '>0.80', engagement: '<0.40' } },
  { clip_code: 'HIA-E1',  stem: 'henrich_engaged_pos',   name: 'Henrich — Engaged Positive',                 chunks: ['000','001','002','003','004'], expected: { authenticity: '<0.40', clarity: '>0.90', engagement: '>0.60' } },
  { clip_code: 'HIA-E3',  stem: 'b5_naval_meaning_life', name: 'Naval — Meaning of Life (high engagement)',  chunks: ['000','001','002','003','004','005','006','007','008','009'], expected: { authenticity: '<0.40', clarity: '>0.70', engagement: '>0.60' } },
  { clip_code: 'HIA-E4',  stem: 'b1_heinrich_questions', name: 'B1 Heinrich — Questions (high engagement)',  chunks: ['000','001','002','003','004','005','006','007'], expected: { authenticity: '<0.40', clarity: '>0.50', engagement: '>0.60' } },
  { clip_code: 'HIA-E6',  stem: 'b1_saad_questions',     name: 'B1 Saad — Questions (low engagement)',       chunks: ['000','001','002','003','004','005'], expected: { authenticity: '<0.40', clarity: '>0.50', engagement: '<0.40' } },
  { clip_code: 'HIA-E11', stem: 'b5_fisher_string',      name: 'B5 Jefferson Fisher — String demo',          chunks: ['000','001','002','003','004','005','006','007','008','009','010','011'], expected: { authenticity: '<0.40', clarity: '<0.50', engagement: '>0.60' } },
];

/** Evaluate a threshold expression. `observe` is always FAIL. */
export function checkExpected(
  actual: number | null | undefined,
  expr: string,
): { pass: boolean; reason: string } {
  if (expr === 'observe') return { pass: false, reason: 'observe (no label = FAIL)' };
  if (actual == null || Number.isNaN(actual)) return { pass: false, reason: 'no actual score' };
  const m = /^([<>])\s*(\d+(?:\.\d+)?)$/.exec(expr.trim());
  if (!m) return { pass: false, reason: `unparseable expected: ${expr}` };
  const op = m[1];
  const threshold = parseFloat(m[2]);
  const pass = op === '>' ? actual > threshold : actual < threshold;
  return { pass, reason: `${actual} ${op} ${threshold} = ${pass}` };
}
