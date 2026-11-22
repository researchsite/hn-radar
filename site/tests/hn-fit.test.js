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
  assert.ok(golden.profile_cases.length >= 10 && golden.score_cases.length >= 14 && golden.top_cases.length >= 8);
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
