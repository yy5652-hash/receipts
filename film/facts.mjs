// Every number the film speaks or shows comes from here -> work/facts.json.
// Sources: bench/results_final/summary.json, samples/*.audit.json, data/live-run.jsonl (the recorded real run).
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const json = p => JSON.parse(readFileSync(join(ROOT, p), 'utf8'));
const pct1 = x => Math.round(x * 1000) / 10;                 // 0.98610 -> 98.6

const bench = json('bench/results_final/summary.json');
const ai = json('samples/ai-energy-briefing.audit.json');
const ml = json('samples/ml-related-work.audit.json');
const live = readFileSync(join(HERE, 'data/live-run.jsonl'), 'utf8').trim().split('\n').map(l => JSON.parse(l));
const meta = json('film/data/live-run.meta.json');
const suggestBench = json('bench/results_suggest/summary.json');   // the "find a real source" evaluation

// ---- ReceiptsBench: the six configurations of the README table
const NAMES = {
  ultra: 'Nemotron 3 Ultra', super: 'Nemotron 3 Super', nano: 'Nemotron 3 Nano',
  routed: 'Nano, Ultra re-checks “supported”', cascade_nano: 'Nano, Ultra re-checks doubts', cascade: 'Super, Ultra re-checks doubts',
};
const configs = Object.keys(NAMES).map(id => {
  const a = bench.configs[id].all;
  return { id, name: NAMES[id], caught: a.problem_recall, falseAlarms: a.false_alarm, macro: pct1(a.macro_accuracy), cost100: a.cost_per_100_claims_usd, seconds: a.median_seconds_per_report };
});
const ultra = configs.find(c => c.id === 'ultra'), nano = configs.find(c => c.id === 'nano');
const ratio = ultra.cost100 / nano.cost100;

// ---- the IEA briefing: the claims the film dwells on, read from the recorded example and checked against the live run
const claim = (audit, id) => audit.claims.find(c => c.id === id);
const verdict2 = (audit, id) => audit.suggestions.find(v => v.claim_id === id);
const verdict = (audit, id) => audit.verdicts.find(v => v.claim_id === id);
const pctIn = s => /(\d+(?:\.\d+)?)\s?%/.exec(s)?.[1] + '%';
const wrongGrowth = pctIn(claim(ai, 'c2').claim);       // the briefing says …
const realGrowth = pctIn(verdict(ai, 'c2').quote);      // … the IEA says
const done = live.find(e => e.event.type === 'done').event.report;
const liveLabel = id => done.verdicts.find(v => v.claim_id === id)?.label;
const sampleLabel = id => verdict(ai, id).label;
for (const id of ['c2', 'c3', 'c6', 'c10', 'c12']) {
  if (liveLabel(id) !== sampleLabel(id)) throw new Error(`the recorded run and the sample disagree on ${id}: ${liveLabel(id)} vs ${sampleLabel(id)}`);
}
if (pctIn(verdict(done, 'c2')?.quote ?? done.verdicts.find(v => v.claim_id === 'c2').quote) !== realGrowth) throw new Error('growth figure differs between run and sample');

const sg = suggestBench.true_claims.all.supported;                   // misattributed or dead-link claims that got a supporting source
if (sg.k !== 39 || sg.n !== 45) console.warn('suggest benchmark changed:', sg);
const liveSuggestions = Object.fromEntries(done.suggestions.map(x => [x.claim_id, x]));
const t = type => live.filter(e => e.event.type === type).map(e => e.t);
const stageAt = s => live.find(e => e.event.type === 'stage' && e.event.stage === s).t;
const verdictTimes = t('verdict');
const counts = {};
for (const c of done.claims) { const vs = done.verdicts.filter(v => v.claim_id === c.id); const l = vs.some(v => v.label === 'supported') ? 'supported' : vs[0].label; counts[l] = (counts[l] || 0) + 1; }
const usage = done.usage.reduce((a, u) => a + u.cost_usd, 0);

const facts = {
  cases: 2202, casesAsOf: '19 Oct 2026', casesSource: 'damiencharlotin.com/hallucinations',
  items: bench.dataset.items, scored: bench.dataset.scored_items, reports: bench.dataset.reports, sources: bench.dataset.sources,
  ultra_k: ultra.caught.k, ultra_n: ultra.caught.n, ultra_fa_k: ultra.falseAlarms.k, ultra_fa_n: ultra.falseAlarms.n,
  ultra_macro: ultra.macro, nano_macro: nano.macro,
  ultra_cost_per_100: ultra.cost100, ultra_cost_per_claim: ultra.cost100 / 100, ultra_cost_cents: Math.round(ultra.cost100 * 10) / 10,
  cost_ratio: ratio, nano_fraction: Math.round(ratio),
  wrong_growth: wrongGrowth, real_growth: realGrowth,
  configs,
  suggest_k: sg.k, suggest_n: sg.n, suggest_pct: Math.round(sg.rate * 100),
  generator: bench.dataset.generation.model, byType: bench.dataset.by_type_all,
  live: {
    events: live.length, recorded_at: meta.recorded_at, seconds: done.seconds, request_to_last_event_s: meta.request_to_last_event_s,
    model_cost_usd: Math.round(usage * 1e6) / 1e6, claims: done.claims.length, sources: done.sources.length, counts,
    times: { extractStart: stageAt('extract'), claims: t('claims')[0], fetch: stageAt('fetch'), firstSource: Math.min(...t('source')), lastSource: Math.max(...t('source')), judge: stageAt('judge'), firstVerdict: Math.min(...verdictTimes), lastVerdict: Math.max(...verdictTimes), suggest: stageAt('suggest'), firstSuggestion: Math.min(...t('suggestion')), lastSuggestion: Math.max(...t('suggestion')), done: stageAt('done') },
    suggestions: Object.fromEntries(Object.entries(liveSuggestions).map(([id, x]) => [id, { status: x.status, title: x.title, url: x.url }])),
  },
  fixExample: { claim: claim(ai, 'c6').claim, title: verdict2(ai, 'c6')?.title, quote: verdict2(ai, 'c6')?.quote, url: verdict2(ai, 'c6')?.url },
  mlSample: { claims: ml.claims.length, gpt3: { claim: claim(ml, 'c2').claim, quote: verdict(ml, 'c2').quote }, bert: { claim: claim(ml, 'c3').claim, quote: verdict(ml, 'c3').quote } },
};
mkdirSync(join(HERE, 'work'), { recursive: true });
writeFileSync(join(HERE, 'work/facts.json'), JSON.stringify(facts, null, 1));
console.log(JSON.stringify({ ...facts, configs: facts.configs.map(c => `${c.id} ${c.caught.k}/${c.caught.n} fa ${c.falseAlarms.k}/${c.falseAlarms.n} macro ${c.macro} $${c.cost100.toFixed(3)}/100`) }, null, 1));
