// Run: node --test site/tests/hn-github.test.js
// Every failure of the GitHub request, with a fake fetch (AGENTS.md site rules S2, S3, S4).
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const G = require('../careers/hn-github.js');

function res(status, body, headers) {
  const h = headers || {};
  return {
    status, ok: status >= 200 && status < 300,
    headers: { get: (n) => (h[n.toLowerCase()] === undefined ? null : String(h[n.toLowerCase()])) },
    json: async () => { if (body === undefined) throw new SyntaxError('not json'); return body; }
  };
}
function repos(n, from) { return Array.from({ length: n }, (_, i) => ({ name: 'r' + ((from || 0) + i) })); }
function fake(responses) {  // each call gets the next response (a value, or a function of the call)
  const calls = [];
  const f = async (url, init) => {
    calls.push({ url, init });
    const r = responses[Math.min(calls.length - 1, responses.length - 1)];
    return typeof r === 'function' ? r(url, init) : r;
  };
  f.calls = calls;
  return f;
}
const code = (c) => (e) => e instanceof Error && e.name === 'GitHubError' && e.code === c;

test('one short page is the whole list', async () => {
  const f = fake([res(200, repos(3))]);
  assert.equal((await G.fetchRepos('octo', { fetch: f })).length, 3);
  assert.equal(f.calls.length, 1);
});

test('pages are followed until a page is short', async () => {
  const f = fake([res(200, repos(100, 0)), res(200, repos(100, 100)), res(200, repos(7, 200))]);
  const out = await G.fetchRepos('octo', { fetch: f });
  assert.equal(out.length, 207);
  assert.deepEqual(f.calls.map((c) => /[?&]page=(\d+)/.exec(c.url)[1]), ['1', '2', '3']);
});

test('a full page followed by an empty one ends the list', async () => {
  const f = fake([res(200, repos(100)), res(200, [])]);
  assert.equal((await G.fetchRepos('octo', { fetch: f })).length, 100);
});

test('the cap fails loudly: 20 full pages is "2000 or more", never a partial list (exactly 2000 is refused too, like github.py)', async () => {
  const f = fake([res(200, repos(100))]);
  await assert.rejects(G.fetchRepos('octo', { fetch: f }), (e) => e.code === 'too_many_repos' && e.message.includes('2000 or more'));
  assert.equal(f.calls.length, G.MAX_PAGES);
});

test('one repo under the cap is fine (1999 repos)', async () => {
  const pages = Array.from({ length: 19 }, () => res(200, repos(100))).concat([res(200, repos(99))]);
  assert.equal((await G.fetchRepos('octo', { fetch: fake(pages) })).length, 1999);
});

test('an unknown user is named', async () => {
  await assert.rejects(G.fetchRepos('nobody-here', { fetch: fake([res(404, { message: 'Not Found' })]) }),
    (e) => e.code === 'not_found' && e.message.includes('nobody-here'));
});

test('the rate limit says so and carries the reset time', async () => {
  const f = fake([res(403, { message: 'rate limit' }, { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '1800000000' })]);
  await assert.rejects(G.fetchRepos('octo', { fetch: f }), (e) => e.code === 'rate_limit' && e.resetAt === 1800000000000 && /60 requests an hour/.test(e.message));
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(429, {}, { 'x-ratelimit-remaining': '0' })]) }), (e) => e.code === 'rate_limit' && e.resetAt === null);
});

test('a 403 that is not the rate limit is a plain error, not a rate-limit claim', async () => {
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(403, {}, { 'x-ratelimit-remaining': '12' })]) }), (e) => e.code === 'http' && e.status === 403);
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(500, {})]) }), (e) => e.code === 'http' && e.status === 500);
});

test('a failure on a later page is a failure, not the repos so far', async () => {
  const f = fake([res(200, repos(100)), res(500, {})]);
  await assert.rejects(G.fetchRepos('octo', { fetch: f }), code('http'));
});

test('malformed answers are errors', async () => {
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(200, undefined)]) }), code('bad_response'));
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(200, { message: 'x' })]) }), code('bad_response'));
  await assert.rejects(G.fetchRepos('octo', { fetch: fake([res(200, 'text')]) }), code('bad_response'));
});

test('offline is a network error with a sentence', async () => {
  const f = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(G.fetchRepos('octo', { fetch: f }), (e) => e.code === 'network' && /could not reach GitHub/.test(e.message));
});

test('a fetch that throws at once, without returning a promise, is still a named error', async () => {
  const f = () => { throw new Error('boom'); };
  await assert.rejects(G.fetchRepos('octo', { fetch: f }), code('network'));
});

test('a request that never answers times out', async () => {
  const f = (url, init) => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))));
  await assert.rejects(G.fetchRepos('octo', { fetch: f, timeoutMs: 30 }), code('timeout'));
});

test('a body that stalls until the timer cuts it is a timeout, not bad JSON', async () => {
  const f = async (url, init) => ({
    status: 200, ok: true, headers: { get: () => null },
    json: () => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))))
  });
  await assert.rejects(G.fetchRepos('octo', { fetch: f, timeoutMs: 30 }), code('timeout'));
});

test('cancel stops the run: no later request, no result', async () => {
  const ctl = new AbortController();
  const f = fake([() => { ctl.abort(); return res(200, repos(100)); }, res(200, repos(5))]);
  await assert.rejects(G.fetchRepos('octo', { fetch: f, signal: ctl.signal }), code('aborted'));
  assert.equal(f.calls.length, 1);
});

test('cancel while a request is in flight rejects at once', async () => {
  const ctl = new AbortController();
  const f = (url, init) => new Promise((_, reject) => init.signal.addEventListener('abort', () => reject(new Error('aborted'))));
  const p = G.fetchRepos('octo', { fetch: f, signal: ctl.signal, timeoutMs: 0 });
  setTimeout(() => ctl.abort(), 10);
  await assert.rejects(p, code('aborted'));
});

test('already cancelled: no request at all', async () => {
  const ctl = new AbortController(); ctl.abort();
  const f = fake([res(200, repos(1))]);
  await assert.rejects(G.fetchRepos('octo', { fetch: f, signal: ctl.signal }), code('aborted'));
  assert.equal(f.calls.length, 0);
});

test('a bad username never reaches the network', async () => {
  for (const bad of ['', ' ', 'a b', '-a', 'a-', 'a--b', 'x'.repeat(40), '../etc', 'a/b', 'a?b=1', 'ünï', null, undefined, 5]) {
    const f = fake([res(200, [])]);
    await assert.rejects(G.fetchRepos(bad, { fetch: f }), code('invalid_username'), String(bad));
    assert.equal(f.calls.length, 0);
  }
  assert.equal((await G.fetchRepos('a'.repeat(39), { fetch: fake([res(200, [])]) })).length, 0);  // exactly 39 is allowed
});

test('the request goes only to api.github.com, without cookies or referrer', async () => {
  const f = fake([res(200, [])]);
  await G.fetchRepos('octo-cat', { fetch: f });
  const { url, init } = f.calls[0];
  assert.equal(new URL(url).origin, 'https://api.github.com');
  assert.equal(new URL(url).pathname, '/users/octo-cat/repos');
  assert.equal(init.credentials, 'omit');
  assert.equal(init.referrerPolicy, 'no-referrer');
  assert.deepEqual(Object.keys(init.headers), ['Accept']);  // no custom header: no CORS preflight
});

test('the module stores nothing and touches no page API', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'careers', 'hn-github.js'), 'utf8');
  for (const word of ['localStorage', 'sessionStorage', 'indexedDB', 'document.', 'innerHTML', 'eval(', 'XMLHttpRequest', 'sendBeacon']) {
    assert.equal(src.includes(word), false, 'hn-github.js must not use ' + word);
  }
});
