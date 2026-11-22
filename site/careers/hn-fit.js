/* hn-fit.js: a person's public repos -> a profile -> the best matching jobs. No DOM, no network, no storage.
 *
 * This is a port of src/hnradar/fingerprint.py and src/hnradar/match.py, which are the reference (AGENTS.md site
 * rule S6). The cases in tests/fixtures/fit_golden.json are made by the Python code (site/tools/make_fit_golden.py)
 * and must give the same answer here (site/tests/hn-fit.test.js). Change both sides and the cases together.
 *
 * Two known differences, both outside the golden cases on purpose:
 *  - match.py also drops posts that are a person's own profile (parse.is_candidate_profile). The port does not repeat
 *    the parser; it relies on the jobs file holding openings only. That is enforced where the file is made and used:
 *    `hnradar fetch-hiring` drops them, tests/test_jobs_file.py fails on the committed file if one is left, and
 *    site/tools/build_careers_index.py refuses a file that holds one.
 *  - strings are ordered by UTF-16 code unit here and by code point in Python. They differ only for characters
 *    outside the Basic Multilingual Plane; GitHub language, topic and repo names are ASCII.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HNFit = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var TOP_LANGUAGES = 5, TOP_TOPICS = 10, TOP_REPOS = 5;
  var TOP_MATCHES = 3, LANGUAGE_POINTS = 20, TOPIC_POINTS = 10, REMOTE_POINTS = 5, MAX_SCORE = 100;

  // Names GitHub topics and job posts use for the same thing (keys and values are already normalised).
  var ALIASES = {
    postgres: 'postgresql', psql: 'postgresql', k8s: 'kubernetes', golang: 'go',
    js: 'javascript', ts: 'typescript', node: 'nodejs', reactjs: 'react', vuejs: 'vue'
  };

  function norm(term) {
    // 'Next.js' and 'nextjs' are the same term; 'C++' and 'C#' keep their symbol; 'k8s' is 'kubernetes'.
    var key = String(term).toLowerCase().replace(/[^a-z0-9+#]/g, '');
    return Object.prototype.hasOwnProperty.call(ALIASES, key) ? ALIASES[key] : key;
  }

  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  function isObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
  function count(n) { return typeof n === 'number' && isFinite(n) ? n : 0; }

  // Forks are excluded everywhere: they describe someone else's work.
  function buildProfile(username, repos) {
    if (!Array.isArray(repos)) throw new TypeError('repos must be a list');
    // A response that is not a list of named repos is not a list of repos: fail like the Python code does, never
    // build a profile from a part of it (site rule S3).
    repos.forEach(function (r, i) {
      if (!isObject(r) || typeof r.name !== 'string') throw new TypeError('repo ' + i + ' is not an object with a name');
    });
    var own = repos.filter(function (r) { return !r.fork; });

    var sizes = new Map();
    own.forEach(function (r) {
      if (typeof r.language === 'string' && r.language) sizes.set(r.language, (sizes.get(r.language) || 0) + count(r.size));
    });
    var languages = Array.from(sizes.entries())
      .sort(function (a, b) { return (b[1] - a[1]) || cmp(a[0], b[0]); })
      .slice(0, TOP_LANGUAGES)
      .map(function (kv) { return { name: kv[0], size_kb: kv[1] }; });

    var topicCounts = new Map();
    own.forEach(function (r) {
      (Array.isArray(r.topics) ? r.topics : []).forEach(function (t) {
        if (typeof t === 'string') topicCounts.set(t, (topicCounts.get(t) || 0) + 1);
      });
    });
    var topics = Array.from(topicCounts.entries())
      .sort(function (a, b) { return (b[1] - a[1]) || cmp(a[0], b[0]); })
      .slice(0, TOP_TOPICS)
      .map(function (kv) { return kv[0]; });

    var notable = own.slice()
      .sort(function (a, b) { return (count(b.stargazers_count) - count(a.stargazers_count)) || cmp(a.name, b.name); })
      .slice(0, TOP_REPOS)
      .map(function (r) {
        return {
          name: r.name,
          description: typeof r.description === 'string' ? r.description : null,
          stars: count(r.stargazers_count),
          language: typeof r.language === 'string' && r.language ? r.language : null
        };
      });

    return { username: username, languages: languages, topics: topics, notable_repos: notable };
  }

  // null when the job's stack shares nothing with the profile. Each stack term counts once
  // (a language wins over a topic of the same name).
  function scoreJob(profile, job) {
    var languages = new Map(), topics = new Map();
    profile.languages.forEach(function (l) { var k = norm(l.name); if (k) languages.set(k, l.name); });
    profile.topics.forEach(function (t) { var k = norm(t); if (k) topics.set(k, t); });

    var seen = new Set(), matchedLanguages = [], matchedTopics = [];
    (Array.isArray(job.stack) ? job.stack : []).forEach(function (term) {
      var key = norm(term);
      if (!key || seen.has(key)) return;
      seen.add(key);
      if (languages.has(key)) matchedLanguages.push(languages.get(key));
      else if (topics.has(key)) matchedTopics.push(topics.get(key));
    });
    if (!matchedLanguages.length && !matchedTopics.length) return null;

    var reasons = matchedLanguages.map(function (name) {
      return { kind: 'language', text: 'Your ' + name + " work matches this job's stack", evidence: name };
    });
    var repo = null;
    for (var i = 0; i < profile.notable_repos.length; i++) {
      var r = profile.notable_repos[i];
      if (r.language && matchedLanguages.indexOf(r.language) !== -1) { repo = r; break; }
    }
    if (repo) reasons.push({ kind: 'repo', text: 'Your repo ' + repo.name + ' is written in ' + repo.language, evidence: repo.name });
    matchedTopics.forEach(function (name) {
      reasons.push({ kind: 'topic', text: 'Your repos use the topic ' + name, evidence: name });
    });

    var score = LANGUAGE_POINTS * matchedLanguages.length + TOPIC_POINTS * matchedTopics.length;
    if (job.remote === true) {
      score += REMOTE_POINTS;
      // Not a reason of its own: every reason must name something from the profile, and "remote" names nothing.
      reasons[0] = { kind: reasons[0].kind, text: reasons[0].text + ' (the post says remote)', evidence: reasons[0].evidence };
    }

    return {
      rank: 0,
      score: Math.min(score, MAX_SCORE),
      job: {
        comment_id: job.comment_id, company: job.company,
        role: job.role === undefined ? null : job.role,
        remote: job.remote === undefined ? null : job.remote,
        url: job.url === undefined ? null : job.url
      },
      reasons: reasons
    };
  }

  // Best first; ties go to the newer post (higher comment_id). Fewer than `limit` is fine, and an empty list
  // means "no strong matches": never a filler top 3.
  function topMatches(profile, jobs, limit) {
    if (!Array.isArray(jobs)) throw new TypeError('jobs must be a list');
    var n = limit === undefined ? TOP_MATCHES : limit;
    if (!(n >= 0)) throw new RangeError('limit must be 0 or more');
    var scored = [];
    jobs.forEach(function (job) { var m = isObject(job) ? scoreJob(profile, job) : null; if (m) scored.push(m); });
    scored.sort(function (a, b) { return (b.score - a.score) || (b.job.comment_id - a.job.comment_id); });
    return scored.slice(0, n).map(function (m, i) { m.rank = i + 1; return m; });
  }

  // The jobs file is data the page trusts (it holds openings only, see the header). An empty list or a record that is
  // not a job means the file is wrong, and "no matches" must not be shown for it: say so instead (site rule S3).
  function checkJobs(jobs) {
    if (!Array.isArray(jobs)) throw new TypeError('the list of jobs is not a list');
    if (!jobs.length) throw new TypeError('the list of jobs is empty');
    jobs.forEach(function (j, i) {
      if (!isObject(j) || typeof j.comment_id !== 'number' || !isFinite(j.comment_id) || Math.floor(j.comment_id) !== j.comment_id || j.comment_id <= 0 ||
          typeof j.company !== 'string' || !Array.isArray(j.stack) || !j.stack.every(function (t) { return typeof t === 'string'; })) {
        throw new TypeError('record ' + i + ' of the list of jobs is not a job');
      }
    });
    return jobs;
  }

  return {
    TOP_MATCHES: TOP_MATCHES, norm: norm, buildProfile: buildProfile, scoreJob: scoreJob, topMatches: topMatches, checkJobs: checkJobs
  };
});
