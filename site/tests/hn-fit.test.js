// Run: node --test site/tests/hn-fit.test.js        (Node 20 or newer; no packages)
// The expected answers are made by the Python reference (site/tools/make_fit_golden.py); this file only checks
// that hn-fit.js gives the same ones, plus the input checks the browser needs (AGENTS.md site rules S3, S6).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const Fit = require('../careers/hn-fit.js');

const golden = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'tests', 'fixtures', 'fit_golden.json'), 'utf8'));

for (const c of golden.profile_cases) {
  test('profile: ' + c.name, () => assert.deepEqual(Fit.buildProfile(c.username, c.repos), c.profile));
}
for (const c of golden.score_cases) {
  test('score: ' + c.name, () => assert.deepEqual(Fit.scoreJob(c.profile, c.job), c.expected));
}
for (const c of golden.top_cases) {
  test('top: ' + c.name, () => assert.deepEqual(Fit.topMatches(c.profile, c.jobs, c.limit), c.expected));
}

test('the golden file has cases of every kind', () => {
  assert.ok(golden.profile_cases.length >= 10 && golden.score_cases.length >= 14 && golden.top_cases.length >= 8 && golden.signals_cases.length >= 25);
});

test('a profile that is not a list of repos is an error, not an empty profile', () => {
  assert.throws(() => Fit.buildProfile('octo', null), TypeError);
  assert.throws(() => Fit.buildProfile('octo', { length: 0 }), TypeError);
});

test('jobs that are not a list are an error', () => {
  const p = Fit.buildProfile('octo', []);
  assert.throws(() => Fit.topMatches(p, null), TypeError);
  assert.throws(() => Fit.topMatches(p, [], -1), RangeError);
});

test('a repo entry that is not an object with a name is an error, not a smaller profile', () => {
  for (const bad of [null, 3, 'x', [], {}, { name: 5 }]) {
    assert.throws(() => Fit.buildProfile('octo', [{ name: 'ok', language: 'Python', size: 5 }, bad]), TypeError);
  }
});

test('a job entry that is not an object is skipped', () => {
  const p = Fit.buildProfile('octo', [{ name: 'ok', language: 'Python', size: 5 }]);
  assert.deepEqual(Fit.topMatches(p, [null, 7, { comment_id: 1, company: 'A', stack: ['Python'] }]).map((m) => m.job.company), ['A']);
});

test('text from a repo is carried as data and never interpreted', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const p = Fit.buildProfile('octo', [{ name: evil, language: 'Python', description: evil, stargazers_count: 1 }]);
  assert.equal(p.notable_repos[0].name, evil);
  assert.equal(JSON.stringify(Fit.topMatches(p, [{ comment_id: 1, company: 'A', stack: ['Python'] }])).includes('onerror=alert(1)'), true);
});

test('the module touches no browser or network API', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'careers', 'hn-fit.js'), 'utf8');
  for (const word of ['fetch(', 'XMLHttpRequest', 'localStorage', 'sessionStorage', 'document.', 'innerHTML', 'eval(', 'cookie']) {
    assert.equal(src.includes(word), false, 'hn-fit.js must not use ' + word);
  }
});

test('checkJobs: a good list passes, an empty or malformed one is an error', () => {
  const job = { comment_id: 1, company: 'A', stack: ['Python'] };
  assert.deepEqual(Fit.checkJobs([job]), [job]);
  assert.throws(() => Fit.checkJobs([]), /empty/);
  assert.throws(() => Fit.checkJobs(null), TypeError);
  assert.throws(() => Fit.checkJobs({}), TypeError);
  for (const bad of [null, 3, [], {}, { ...job, comment_id: 0 }, { ...job, comment_id: -4 }, { ...job, comment_id: 1.5 }, { ...job, comment_id: '1' },
    { ...job, company: 5 }, { ...job, stack: 'Python' }, { ...job, stack: [null, 'Python'] }, { ...job, stack: [3] }, { comment_id: 2, company: 'B' }]) {
    assert.throws(() => Fit.checkJobs([job, bad]), /record 1 of the list of jobs is not a job/);
  }
});

// ---- signals.json: the Python model decides which files are valid, the browser must agree ----
for (const c of golden.signals_cases) {
  test('signals: ' + c.name + (c.valid ? ' is accepted' : ' is refused'), () => {
    if (c.valid) assert.equal(Fit.checkSignals(c.data), c.data);
    else assert.throws(() => Fit.checkSignals(c.data), TypeError);
  });
}

test('companyFromMatch: no signal means "not checked", never "no Show HN"', () => {
  const match = Fit.topMatches(Fit.buildProfile('octo', [{ name: 'r', language: 'Python', size: 5 }]), [{ comment_id: 7, company: 'A', role: 'Dev', url: 'https://a.com', stack: ['Python'] }])[0];
  for (const none of [undefined, null]) {
    const c = Fit.companyFromMatch(match, none);
    assert.equal(c.pulse_status, 'not_checked');
    assert.equal(c.show_hn, null); assert.equal(c.problem, null); assert.equal(c.note, null);
  }
  const c = Fit.companyFromMatch(match, null);
  assert.deepEqual([c.rank, c.company, c.role, c.job_comment_id, c.job_url, c.fit_score], [1, 'A', 'Dev', 7, 'https://a.com', 20]);
  assert.deepEqual(c.fit_reasons, ["Your Python work matches this job's stack", 'Your repo r is written in Python']);
  assert.equal(c.draft, null); assert.equal(c.draft_status, 'skipped');
});

test('companyFromMatch: each signal status carries over, and only ok has a problem and evidence', () => {
  const sigs = golden.signals_cases.find((c) => c.name === 'every_status_valid').data.jobs;
  const match = { rank: 2, score: 40, job: { comment_id: 9, company: 'B', role: null, url: null }, reasons: [{ text: 'why' }] };
  const a = Fit.companyFromMatch(match, sigs.a);
  assert.deepEqual([a.pulse_status, a.show_hn, a.problem, a.note], ['no_show_hn', null, null, 'no domain in the job link']);
  for (const k of ['b', 'c', 'd']) {
    const c = Fit.companyFromMatch(match, sigs[k]);
    assert.equal(c.pulse_status, sigs[k].status); assert.equal(c.show_hn.story_id, 900); assert.equal(c.problem, null); assert.deepEqual(c.evidence, []);
  }
  const e = Fit.companyFromMatch(match, sigs.e);
  assert.equal(e.pulse_status, 'ok');
  assert.deepEqual(e.problem, { summary: 'It crashes on start', comment_ids: [901, 902] });
  assert.deepEqual(e.evidence.map((x) => x.comment_id), [901, 902]);
  e.problem.comment_ids.push(1); e.evidence.pop();
  assert.deepEqual(sigs.e.problem.comment_ids, [901, 902]); assert.equal(sigs.e.evidence.length, 2);  // a copy: the loaded file is not changed
});
