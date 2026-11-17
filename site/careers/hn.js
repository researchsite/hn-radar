/* HN Radar shared helpers. Plain script, no build. Exposes window.HN.
   Everything that comes from Hacker News (or from a user's file) is untrusted:
   it is only ever written with textContent, never innerHTML. */
(function () {
  'use strict';

  // WHERE FILES LIVE. The only place a page learns the location of data, pictures and videos.
  // Here (dev) they sit inside careers/. On the live site they are shared at the site root; the move script
  // (site/_private/publish-to-site.ps1) rewrites this one line in the copy, or change it by hand: see site/README.md.
  var PATHS = { data: './data/', images: './images/', videos: './videos/' };

  // Control characters, DEL and C1, zero-width and bidirectional/format characters.
  var BAD = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f​-‏‪-‮⁠-⁯﻿]/g;

  // One line: every run of whitespace becomes a single space.
  function clean(text) {
    return String(text == null ? '' : text).replace(BAD, ' ').replace(/\s+/g, ' ').trim();
  }

  // Several lines: keeps line breaks (for pre-wrap), removes the same characters, caps blank runs.
  function cleanBlock(text) {
    return String(text == null ? '' : text).replace(/\r\n?/g, '\n').replace(BAD, ' ')
      .replace(/[ \t]+/g, ' ').replace(/ ?\n ?/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  }

  // HN item id -> link. Returns null for anything that is not a positive integer.
  function hnItemUrl(id) {
    var n = parseInt(id, 10);
    return Number.isFinite(n) && n > 0 && String(n) === String(id).trim() ? 'https://news.ycombinator.com/item?id=' + n : null;
  }

  // Only http(s) links are ever used as href. Returns {href, host} or null.
  function safeUrl(raw) {
    try {
      var u = new URL(String(raw == null ? '' : raw).trim());
      if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
      return { href: u.href, host: u.hostname.replace(/^www\./, '') };
    } catch (e) { return null; }
  }

  // Create an element: el('p', {className, ...attrs}, 'text' | node | [children]). Text is always a text node.
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'className') n.className = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    [].concat(kids == null ? [] : kids).forEach(function (k) {
      n.appendChild(typeof k === 'string' || typeof k === 'number' ? document.createTextNode(String(k)) : k);
    });
    return n;
  }

  function extLink(href, text, label) {
    var a = el('a', { className: 'rw-hn-link', href: href, rel: 'noopener noreferrer', target: '_blank' }, text);
    if (label) a.setAttribute('aria-label', label + ' (opens in a new tab)');
    return a;
  }

  // Citation chip list for HN comment ids. Every claim about what users said must use this.
  function citations(ids) {
    var ul = el('ul', { className: 'rw-hn-chips', 'aria-label': 'Sources on Hacker News' });
    (ids || []).forEach(function (id) {
      var u = hnItemUrl(id);
      if (u) ul.appendChild(el('li', {}, extLink(u, 'HN #' + id, 'Hacker News comment ' + id)));
    });
    return ul;
  }

  // Replace the contents of a container with a designed state: title, text, optional button.
  function state(container, title, text, action) {
    container.textContent = '';
    container.hidden = false;
    container.appendChild(el('strong', {}, title));
    container.appendChild(el('span', {}, text));
    if (action) {
      var b = el('button', { type: 'button', className: 'rw-hn-btn' }, action.label);
      b.addEventListener('click', action.run);
      container.appendChild(el('div', {}, b));
    }
  }

  // When the jobs file was last updated on the server (its Last-Modified header). That is the day the file was deployed,
  // which equals the crawl day only if it is deployed the same day, so the note says "updated", not "crawled".
  // Pages that have <p id="hn-crawled" hidden> get it filled; no header, no note.
  function showCrawled(r) {
    var node = document.getElementById('hn-crawled'), when = new Date(r.headers.get('Last-Modified') || '');
    if (!node || isNaN(when.getTime())) return;
    node.textContent = 'Job posts from Hacker News, data last updated: ' + when.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' }) + '.';
    node.hidden = false;
  }

  // Fetch JSON with a visible failure. validate(data) may throw to reject the shape.
  function loadJSON(path, validate) {
    return fetch(path, { cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        if (/(^|\/)jobs\.json$/.test(path)) showCrawled(r);
        return r.json();
      })
      .then(function (d) { if (validate) validate(d); return d; });
  }

  function jobsList(d) {
    if (!Array.isArray(d)) throw new Error('not a list');
    return d.filter(function (j) { return j && typeof j === 'object' && hnItemUrl(j.comment_id); });
  }

  function remoteKey(j) { return j.remote === true ? 'yes' : j.remote === false ? 'no' : 'unknown'; }
  function remoteLabel(j) { return j.remote === true ? 'Remote' : j.remote === false ? 'Not remote' : 'Remote not stated'; }

  // The page switcher shown on every page. current = file name of this page.
  function pages(current) {
    var items = [['index.html', 'Jobs'], ['insights.html', 'Insights'], ['company.html', 'Companies'], ['journey.html', 'How this was built'], ['check.html', 'Check yours']];
    var ul = el('ul', { className: 'rw-hn-pages', role: 'list', 'aria-label': 'HN Radar pages' });
    items.forEach(function (it) {
      var a = el('a', { className: 'rw-hn-pagelink', href: './' + it[0] }, it[1]);
      if (it[0] === current) a.setAttribute('aria-current', 'page');
      ul.appendChild(el('li', {}, a));
    });
    return ul;
  }

  window.HN = { paths: PATHS, clean: clean, cleanBlock: cleanBlock, hnItemUrl: hnItemUrl, safeUrl: safeUrl, el: el, extLink: extLink,
    citations: citations, state: state, loadJSON: loadJSON, jobsList: jobsList, remoteKey: remoteKey, remoteLabel: remoteLabel, pages: pages };
})();
