/* hn-github.js: a person's public repositories from the GitHub API, asked for by the browser.
 *
 * A port of src/hnradar/sources/github.py (fetch_repos), minus its disk cache: nothing is stored here
 * (AGENTS.md site rule S2). The only request this file makes is a GET to api.github.com/users/<name>/repos, sent
 * without cookies and without a referrer. Every failure ends in a GitHubError with a `code` and a sentence a person
 * can read (S3), and a run that is cancelled stops at once and never returns a result (S4).
 *
 * `fetch` is passed in, so the Node tests (site/tests/hn-github.test.js) can drive every case without a network.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HNGitHub = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var API = 'https://api.github.com';
  var PER_PAGE = 100;
  var MAX_PAGES = 20;               // 2000 repos; beyond this we fail loudly instead of returning a partial list
  var TIMEOUT_MS = 20000;           // for one request
  // GitHub usernames: 1-39 chars, alphanumeric or single hyphens, no leading/trailing hyphen (same as github.py).
  var USERNAME_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;

  // code: invalid_username | not_found | rate_limit | too_many_repos | network | timeout | aborted | http | bad_response
  function GitHubError(code, message, extra) {
    var e = new Error(message);
    e.name = 'GitHubError';
    e.code = code;
    if (extra) Object.keys(extra).forEach(function (k) { e[k] = extra[k]; });
    return e;
  }

  function header(res, name) {
    try { return res.headers && typeof res.headers.get === 'function' ? res.headers.get(name) : null; } catch (e) { return null; }
  }

  // opts: { fetch, signal, timeoutMs, maxPages, perPage, base }
  function fetchRepos(username, opts) {
    opts = opts || {};
    var doFetch = opts.fetch;
    var perPage = opts.perPage || PER_PAGE, maxPages = opts.maxPages || MAX_PAGES;
    var timeoutMs = opts.timeoutMs === undefined ? TIMEOUT_MS : opts.timeoutMs, base = opts.base || API;
    var outer = opts.signal;

    if (typeof username !== 'string' || !USERNAME_RE.test(username)) {
      return Promise.reject(GitHubError('invalid_username', 'That is not a GitHub username. A username has 1 to 39 letters, digits or single hyphens, and does not start or end with a hyphen.'));
    }
    if (typeof doFetch !== 'function') return Promise.reject(new TypeError('fetch is required'));

    function timeoutError() { return GitHubError('timeout', 'GitHub did not answer within ' + Math.round(timeoutMs / 1000) + ' seconds. Try again in a moment.'); }

    function once(page) {
      if (outer && outer.aborted) return Promise.reject(GitHubError('aborted', 'Stopped.'));
      var ctl = new AbortController(), timedOut = false, timer = null;
      var onAbort = function () { ctl.abort(); };
      if (outer) outer.addEventListener('abort', onAbort, { once: true });
      if (timeoutMs > 0) timer = setTimeout(function () { timedOut = true; ctl.abort(); }, timeoutMs);
      var done = function () { if (timer) clearTimeout(timer); if (outer) outer.removeEventListener('abort', onAbort); };

      var url = base + '/users/' + encodeURIComponent(username) + '/repos?type=owner&sort=pushed&per_page=' + perPage + '&page=' + page;
      return Promise.resolve().then(function () {
        return doFetch(url, { method: 'GET', headers: { Accept: 'application/vnd.github+json' }, credentials: 'omit', referrerPolicy: 'no-referrer', signal: ctl.signal });
      })
        .catch(function () {
          done();
          if (outer && outer.aborted) throw GitHubError('aborted', 'Stopped.');
          if (timedOut) throw timeoutError();
          throw GitHubError('network', 'The browser could not reach GitHub. Check your connection, then try again.');
        })
        .then(function (res) {
          if (outer && outer.aborted) { done(); throw GitHubError('aborted', 'Stopped.'); }
          if (res.status === 404) { done(); throw GitHubError('not_found', 'GitHub has no user named ' + username + '.'); }
          if ((res.status === 403 || res.status === 429) && header(res, 'x-ratelimit-remaining') === '0') {
            done();
            var reset = Number(header(res, 'x-ratelimit-reset'));
            throw GitHubError('rate_limit', 'GitHub allows 60 requests an hour from one network address without signing in, and that limit is used up. Try again later.',
              { resetAt: isFinite(reset) && reset > 0 ? reset * 1000 : null });
          }
          if (!res.ok) { done(); throw GitHubError('http', 'GitHub answered with an error (' + res.status + '). Try again later.', { status: res.status }); }
          return res.json().then(function (data) {
            done();
            if (!Array.isArray(data)) throw GitHubError('bad_response', 'GitHub answered in a shape this page does not understand, so no profile was made.');
            return data;
          }, function () {
            done();
            if (outer && outer.aborted) throw GitHubError('aborted', 'Stopped.');
            if (timedOut) throw timeoutError();  // the body stalled until the timer cut it: that is a timeout, not bad JSON
            throw GitHubError('bad_response', 'GitHub answered with something that is not JSON, so no profile was made.');
          });
        });
    }

    var repos = [];
    function next(page) {
      return once(page).then(function (batch) {
        for (var i = 0; i < batch.length; i++) repos.push(batch[i]);
        if (batch.length < perPage) return repos;
        if (page >= maxPages) {
          throw GitHubError('too_many_repos', username + ' has ' + (maxPages * perPage) + ' or more public repositories; the page will not build a partial profile.');
        }
        return next(page + 1);
      });
    }
    return next(1);
  }

  return { USERNAME_RE: USERNAME_RE, MAX_PAGES: MAX_PAGES, PER_PAGE: PER_PAGE, GitHubError: GitHubError, fetchRepos: fetchRepos };
});
