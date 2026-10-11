
(function () {
  'use strict';

  /* ------------------------------------------------------------------ *
   * Constants and state
   * ------------------------------------------------------------------ */
  // The commentable region: [data-review-root] if the page marks one, else <main>, else <body>.
  const ROOT = document.querySelector('[data-review-root]') || document.querySelector('main') || document.body;
  // Text that is never commentable: code the browser doesn't render, and the layer's own UI.
  const SKIP = 'script, style, noscript, template, [data-review-ignore]';
  let EMBED = {};
  try { EMBED = JSON.parse(document.getElementById('review-data').textContent) || {}; } catch (e) { EMBED = {}; }
  const SCHEMA = 'niab-review/1';
  const DOC = EMBED.document || decodeURIComponent(location.pathname.split('/').pop());
  const REVISION = Number(EMBED.documentRevision) || 1;
  const FILE_NAME = DOC.replace(/\.html?$/i, '') + '.comments.json';
  // The folder this page lives in. Browsers can't open a file picker at an
  // arbitrary path, so the page copies this for the macOS Go to Folder
  // shortcut (Command-Shift-G), which also reaches hidden folders like .ai.
  const FOLDER = decodeURIComponent(location.pathname).replace(/\/[^/]*$/, '');
  // A review ID is created each time the layer is added to a fresh page. Browser
  // storage, the remembered file, and the comments file are all tied to it, so
  // a regenerated page never inherits comments from an earlier version.
  const REVIEW_ID = typeof EMBED.reviewId === 'string' ? EMBED.reviewId : '';
  const LS_KEY = 'niab-review:' + location.pathname + (REVIEW_ID ? ':' + REVIEW_ID : '');
  const UI_KEY = LS_KEY + ':ui';
  const FS = typeof window.showSaveFilePicker === 'function' && typeof window.showOpenFilePicker === 'function';
  const HL = typeof CSS !== 'undefined' && CSS.highlights && typeof window.Highlight === 'function';
  const PICKER_TYPES = [{ description: 'Review comments', accept: { 'application/json': ['.json'] } }];
  const ABOUT = 'Review comments on ' + DOC + '. anchor.quote is the page text the comment refers to. ' +
    'status: open (waiting on Claude) | question (Claude needs an answer) | addressed (Claude replied or changed the page) | resolved (closed by the reviewer).';

  const TYPES = {
    improve: { label: 'Improve', hint: 'What should change?' },
    delete:  { label: 'Delete',  hint: 'Why remove it? (optional)' },
    explain: { label: 'Explain', hint: 'What needs more explanation?' },
    note:    { label: 'Note',    hint: 'Your comment' }
  };
  const STATUS = { open: 'Open', question: 'Needs your input', addressed: 'Addressed', resolved: 'Resolved' };

  const store = new Map();        // id -> normalized comment
  let anchored = new Map();       // id -> Range currently on the page
  const posOf = new Map();        // id -> normalized text position (for sorting)
  let INDEX = null;

  const ui = { open: false, filter: 'active', active: null, editing: null, replying: null, confirmDelete: null, general: false, drafts: {}, flash: '' };
  const file = { handle: null, perm: 'none', dirty: false, touched: false, savedAt: null, error: '', fileRevision: 0 };
  let pending = null;             // { anchor, range } while composing from a selection

  /* ------------------------------------------------------------------ *
   * Small helpers
   * ------------------------------------------------------------------ */
  const iso = () => new Date().toISOString();
  const uid = (p) => p + '-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  const esc = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s);
  const fmtBody = (s) => esc(s).replace(/`([^`\n]+)`/g, '<code>$1</code>');
  const fmtTime = (t) => { const d = new Date(t); return isNaN(d) ? '' : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); };
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

  /* ------------------------------------------------------------------ *
   * Comment model: normalize, merge
   * ------------------------------------------------------------------ */
  function norm(c) {
    const a = c.anchor;
    const created = c.createdAt || iso();
    const out = {
      id: String(c.id),
      type: TYPES[c.type] ? c.type : 'note',
      body: String(c.body || ''),
      status: STATUS[c.status] ? c.status : 'open',
      anchor: a ? {
        quote: String(a.quote || ''), prefix: String(a.prefix || ''), suffix: String(a.suffix || ''),
        sectionId: a.sectionId || null, sectionTitle: a.sectionTitle || null, position: Number(a.position) || 0
      } : null,
      documentRevision: Number(c.documentRevision) || 1,
      createdAt: created,
      updatedAt: c.updatedAt || created,
      statusAt: c.statusAt || created,
      replies: (Array.isArray(c.replies) ? c.replies : []).filter((r) => r && r.id).map((r) => ({
        id: String(r.id), author: r.author === 'claude' ? 'claude' : 'you', body: String(r.body || ''), at: r.at || created
      }))
    };
    if (c.deleted) { out.deleted = true; out.deletedAt = c.deletedAt || created; }
    return out;
  }

  // Core fields follow the later updatedAt, status follows the later statusAt,
  // replies are a union by id, and a delete is final.
  function mergeComment(a, b) {
    b = norm(b);
    if (!a) return b;
    const core = b.updatedAt > a.updatedAt ? b : a;
    const st = b.statusAt > a.statusAt ? b : a;
    const replies = new Map();
    a.replies.concat(b.replies).forEach((r) => { if (!replies.has(r.id)) replies.set(r.id, r); });
    return norm(Object.assign({}, core, {
      status: st.status, statusAt: st.statusAt,
      replies: [...replies.values()].sort((x, y) => cmp(x.at, y.at)),
      deleted: a.deleted || b.deleted, deletedAt: a.deletedAt || b.deletedAt
    }));
  }

  function mergeList(list) {
    let changed = false;
    (Array.isArray(list) ? list : []).forEach((c) => {
      if (!c || !c.id) return;
      const prev = store.get(String(c.id));
      const next = mergeComment(prev, c);
      if (!prev || JSON.stringify(prev) !== JSON.stringify(next)) { store.set(next.id, next); changed = true; }
    });
    return changed;
  }

  const canonList = (list) => JSON.stringify((list || []).map(norm).sort((x, y) => cmp(x.id, y.id)));

  function buildPayload() {
    const comments = [...store.values()].sort((x, y) => cmp(x.createdAt, y.createdAt) || cmp(x.id, y.id));
    return { schema: SCHEMA, document: DOC, reviewId: REVIEW_ID || undefined, documentRevision: Math.max(REVISION, file.fileRevision || 0), about: ABOUT, savedAt: iso(), comments };
  }

  /* ------------------------------------------------------------------ *
   * Local storage (per browser, per page path)
   * ------------------------------------------------------------------ */
  function readLocal() {
    try { const v = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); return v && Array.isArray(v.comments) ? v.comments : []; }
    catch (e) { return []; }
  }
  function writeLocal() {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ comments: [...store.values()] })); } catch (e) { /* storage unavailable */ }
  }
  function readUi() {
    try { const v = JSON.parse(localStorage.getItem(UI_KEY) || '{}'); ui.open = !!v.open; if (['active', 'resolved', 'all'].includes(v.filter)) ui.filter = v.filter; }
    catch (e) { /* ignore */ }
  }
  function writeUi() {
    try { localStorage.setItem(UI_KEY, JSON.stringify({ open: ui.open, filter: ui.filter })); } catch (e) { /* ignore */ }
  }

  /* ------------------------------------------------------------------ *
   * Text index: every text node under <main>, plus a whitespace-
   * normalized copy so anchors survive re-indentation of the source.
   * ------------------------------------------------------------------ */
  function buildIndex() {
    const nodes = [];
    let raw = '';
    const pos = new Map();
    const walker = document.createTreeWalker(ROOT, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement && n.parentElement.closest(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT)
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) { pos.set(n, nodes.length); nodes.push({ node: n, start: raw.length }); raw += n.data; }
    const r2n = new Array(raw.length + 1);
    const n2r = [];
    let normText = '';
    let prevWs = true;
    for (let i = 0; i < raw.length; i++) {
      const ws = /\s/.test(raw[i]);
      r2n[i] = normText.length;
      if (ws) { if (!prevWs) { n2r.push(i); normText += ' '; } prevWs = true; }
      else { n2r.push(i); normText += raw[i]; prevWs = false; }
    }
    r2n[raw.length] = normText.length;
    n2r.push(raw.length);
    return { nodes, pos, raw, norm: normText, r2n, n2r };
  }

  // Offset of a DOM boundary within the index. Counts only indexed text, so
  // skipped script/style/UI text never shifts anchors.
  function boundaryToRaw(container, offset) {
    if (container.nodeType === 3 && INDEX.pos.has(container)) return INDEX.nodes[INDEX.pos.get(container)].start + offset;
    const r = document.createRange();
    r.setStart(container, offset);
    r.collapse(true);
    for (const n of INDEX.nodes) { if (r.comparePoint(n.node, 0) >= 0) return n.start; }
    return INDEX.raw.length;
  }

  function rawToBoundary(i, preferEnd) {
    const ns = INDEX.nodes;
    if (!ns.length) return null;
    let lo = 0, hi = ns.length - 1, k = 0;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (ns[m].start <= i) { k = m; lo = m + 1; } else hi = m - 1; }
    if (preferEnd) { while (k > 0 && i === ns[k].start) k--; }
    else { while (k + 1 < ns.length && i >= ns[k].start + ns[k].node.data.length) k++; }
    const n = ns[k];
    return { node: n.node, offset: Math.max(0, Math.min(i - n.start, n.node.data.length)) };
  }

  function sectionOf(node) {
    const e = node && (node.nodeType === 1 ? node : node.parentElement);
    const s = e && e.closest('section[id]');
    if (!s || !ROOT.contains(s)) return { id: null, title: null };
    const h = s.querySelector('h1, h2');
    return { id: s.id, title: h ? h.textContent.replace(/\s+/g, ' ').trim() : s.id };
  }

  function anchorFromRange(range) {
    const a = boundaryToRaw(range.startContainer, range.startOffset);
    const b = boundaryToRaw(range.endContainer, range.endOffset);
    let na = INDEX.r2n[a], nb = INDEX.r2n[b];
    while (na < nb && INDEX.norm[na] === ' ') na++;
    while (nb > na && INDEX.norm[nb - 1] === ' ') nb--;
    if (nb <= na) return null;
    const sec = sectionOf(range.startContainer);
    return {
      quote: INDEX.norm.slice(na, nb),
      prefix: INDEX.norm.slice(Math.max(0, na - 48), na),
      suffix: INDEX.norm.slice(nb, nb + 48),
      sectionId: sec.id, sectionTitle: sec.title, position: na
    };
  }

  function commonTail(a, b) { let n = 0; while (n < a.length && n < b.length && a[a.length - 1 - n] === b[b.length - 1 - n]) n++; return n; }
  function commonHead(a, b) { let n = 0; while (n < a.length && n < b.length && a[n] === b[n]) n++; return n; }

  // Finds the best occurrence of the quote: context match, then same
  // section, then nearest to where it originally was.
  function locate(anchor) {
    const q = anchor.quote;
    if (!q) return null;
    const N = INDEX.norm;
    let best = -1, bestScore = -Infinity;
    for (let i = N.indexOf(q); i !== -1; i = N.indexOf(q, i + 1)) {
      let score = commonTail(N.slice(Math.max(0, i - anchor.prefix.length), i), anchor.prefix) +
                  commonHead(N.slice(i + q.length, i + q.length + anchor.suffix.length), anchor.suffix);
      const at = rawToBoundary(INDEX.n2r[i], false);
      if (anchor.sectionId && at && sectionOf(at.node).id === anchor.sectionId) score += 20;
      score -= Math.abs(i - anchor.position) / 100000;
      if (score > bestScore) { bestScore = score; best = i; }
    }
    if (best < 0) return null;
    const s = rawToBoundary(INDEX.n2r[best], false);
    const e = rawToBoundary(INDEX.n2r[best + q.length - 1] + 1, true);
    if (!s || !e) return null;
    const r = document.createRange();
    try { r.setStart(s.node, s.offset); r.setEnd(e.node, e.offset); } catch (err) { return null; }
    return { range: r, pos: best };
  }

  /* ------------------------------------------------------------------ *
   * Highlights (CSS Custom Highlight API: no DOM changes to the page)
   * ------------------------------------------------------------------ */
  function paint() {
    anchored = new Map();
    posOf.clear();
    store.forEach((c) => {
      if (c.deleted || !c.anchor) return;
      const hit = locate(c.anchor);
      if (hit) { anchored.set(c.id, hit.range); posOf.set(c.id, hit.pos); }
    });
    if (!HL) return;
    const groups = { improve: [], delete: [], explain: [], note: [], done: [] };
    anchored.forEach((r, id) => {
      const c = store.get(id);
      if (c.status === 'resolved') return;
      (c.status === 'addressed' ? groups.done : groups[c.type]).push(r);
    });
    Object.keys(groups).forEach((k) => CSS.highlights.set('rv-' + k, new Highlight(...groups[k])));
    paintActive();
  }
  function paintActive() {
    if (!HL) return;
    const r = ui.active && anchored.get(ui.active);
    if (r) { const h = new Highlight(r); h.priority = 10; CSS.highlights.set('rv-active', h); }
    else CSS.highlights.delete('rv-active');
  }
  function paintPending(range) {
    if (!HL) return;
    if (range) { const h = new Highlight(range); h.priority = 5; CSS.highlights.set('rv-pending', h); }
    else CSS.highlights.delete('rv-pending');
  }

  function hitTest(x, y) {
    if (!document.caretRangeFromPoint) return null;
    const p = document.caretRangeFromPoint(x, y);
    if (!p || !ROOT.contains(p.startContainer)) return null;
    let hit = null, len = Infinity;
    anchored.forEach((r, id) => {
      if (store.get(id).status === 'resolved') return;
      try {
        if (r.isPointInRange(p.startContainer, p.startOffset)) {
          const l = r.toString().length;
          if (l < len) { hit = id; len = l; }
        }
      } catch (e) { /* detached range */ }
    });
    return hit;
  }

  /* ------------------------------------------------------------------ *
   * Mutations: every change goes through commit()
   * ------------------------------------------------------------------ */
  function commit() {
    file.dirty = true;
    file.touched = true;
    writeLocal();
    paint();
    render();
    scheduleSync();
  }
  function update(id, fn) {
    const c = store.get(id);
    if (!c) return;
    const next = JSON.parse(JSON.stringify(c));
    fn(next);
    store.set(id, norm(next));
    commit();
  }
  function addComment(type, body, anchor) {
    const now = iso();
    const c = norm({ id: uid('c'), type, body, status: 'open', anchor, documentRevision: REVISION, createdAt: now, updatedAt: now, statusAt: now, replies: [] });
    store.set(c.id, c);
    commit();
    return c.id;
  }

  /* ------------------------------------------------------------------ *
   * Selection toolbar and composer
   * ------------------------------------------------------------------ */
  const pop = document.createElement('div');
  pop.className = 'rv-pop';
  pop.setAttribute('data-review-ignore', '');
  pop.hidden = true;
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Add a review comment');
  document.body.appendChild(pop);
  let composing = false;

  function pickerHtml(key, selected) {
    return '<div class="rv-pick" role="radiogroup" aria-label="Comment type">' +
      Object.keys(TYPES).map((t) => '<button type="button" role="radio" class="rv-t-' + t + '" data-act="pick" data-key="' + esc(key) + '" data-type="' + t + '" aria-pressed="' + (t === selected) + '" aria-checked="' + (t === selected) + '">' + TYPES[t].label + '</button>').join('') +
      '</div>';
  }

  function placePop(rect) {
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const vw = document.documentElement.clientWidth;
    let left = rect.left + window.scrollX;
    left = Math.max(window.scrollX + 12, Math.min(left, window.scrollX + vw - w - 12));
    let top = rect.bottom + window.scrollY + 8;
    if (rect.bottom + h + 16 > window.innerHeight && rect.top - h - 8 > 0) top = rect.top + window.scrollY - h - 8;
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
  }

  function showToolbar() {
    composing = false;
    pop.innerHTML = '<div class="rv-tools"><span class="rv-kbd" style="padding:0 6px 0 4px">Comment</span>' +
      Object.keys(TYPES).map((t) => '<button type="button" data-act="start" data-type="' + t + '"><i style="background:var(--rv-' + t + ')"></i>' + TYPES[t].label + '</button>').join('') +
      '</div>';
    pop.hidden = false;
    placePop(pending.range.getBoundingClientRect());
  }

  function showComposer(type) {
    composing = true;
    ui.drafts.pop = { type, body: (ui.drafts.pop && ui.drafts.pop.body) || '' };
    renderComposer();
    paintPending(pending.range);
    placePop(pending.range.getBoundingClientRect());
    const ta = pop.querySelector('textarea');
    ta.focus();
    window.getSelection().removeAllRanges();
  }

  function renderComposer() {
    const d = ui.drafts.pop;
    pop.innerHTML = '<form class="rv-compose" data-form="pop">' +
      pickerHtml('pop', d.type) +
      '<blockquote class="rv-q">' + esc(trunc(pending.anchor.quote, 160)) + '</blockquote>' +
      '<textarea class="rv-ta" id="rv-pop-body" data-key="pop" aria-label="Comment" placeholder="' + esc(TYPES[d.type].hint) + '">' + esc(d.body) + '</textarea>' +
      '<div class="rv-row"><span class="rv-kbd">⌘↵ to add · Esc to cancel</span>' +
      '<button type="button" class="rv-btn" data-act="pop-cancel">Cancel</button>' +
      '<button type="submit" class="rv-btn rv-primary">Add comment</button></div></form>';
  }

  function hidePop() {
    pop.hidden = true;
    composing = false;
    pending = null;
    delete ui.drafts.pop;
    paintPending(null);
  }

  function submitPop() {
    const d = ui.drafts.pop;
    if (!d || !pending) return;
    const body = d.body.trim();
    if (!body && d.type !== 'delete') { pop.querySelector('textarea').focus(); return; }
    addComment(d.type, body, pending.anchor);
    hidePop();
  }

  function checkSelection() {
    if (composing && ui.drafts.pop && ui.drafts.pop.body.trim()) return;
    const sel = window.getSelection();
    if (!sel.rangeCount || sel.isCollapsed) { if (!composing) hidePop(); return; }
    const r = sel.getRangeAt(0);
    const inUi = (n) => { const e = n.nodeType === 1 ? n : n.parentElement; return !e || !!e.closest(SKIP); };
    if (!ROOT.contains(r.commonAncestorContainer) || inUi(r.startContainer) || inUi(r.endContainer)) { if (!composing) hidePop(); return; }
    const anchor = anchorFromRange(r);
    if (!anchor) return;
    paintPending(null);
    pending = { anchor, range: r.cloneRange() };
    showToolbar();
  }

  pop.addEventListener('mousedown', (e) => { if (e.target.closest('button')) e.preventDefault(); });
  pop.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const act = b.dataset.act;
    if (act === 'start') showComposer(b.dataset.type);
    else if (act === 'pick') { ui.drafts.pop.type = b.dataset.type; renderComposer(); pop.querySelector('textarea').focus(); }
    else if (act === 'pop-cancel') hidePop();
  });
  pop.addEventListener('submit', (e) => { e.preventDefault(); submitPop(); });
  pop.addEventListener('input', (e) => { if (e.target.dataset.key === 'pop') ui.drafts.pop.body = e.target.value; });
  pop.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); hidePop(); }
    else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && composing) { e.preventDefault(); submitPop(); }
  });

  document.addEventListener('mousedown', (e) => {
    if (pop.hidden || pop.contains(e.target)) return;
    if (composing && ui.drafts.pop && ui.drafts.pop.body.trim()) return;   // keep a half-written comment
    if (composing) hidePop();
  });
  document.addEventListener('mouseup', (e) => {
    if (pop.contains(e.target) || panel.contains(e.target) || fab.contains(e.target)) return;
    setTimeout(checkSelection, 0);
  });
  document.addEventListener('keyup', (e) => {
    if (e.shiftKey && /^Arrow|^Home|^End/.test(e.key) && !composing) checkSelection();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !pop.hidden && !pop.contains(document.activeElement)) hidePop();
  });

  ROOT.addEventListener('click', (e) => {
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed) return;
    if (e.target.closest('a')) return;
    const id = hitTest(e.clientX, e.clientY);
    if (id) focusComment(id, { openPanel: true });
  });
  let hoverFrame = 0;
  ROOT.addEventListener('mousemove', (e) => {
    if (hoverFrame) return;
    const x = e.clientX, y = e.clientY;
    hoverFrame = requestAnimationFrame(() => { hoverFrame = 0; ROOT.classList.toggle('rv-hover', !!hitTest(x, y)); });
  });

  /* ------------------------------------------------------------------ *
   * Panel
   * ------------------------------------------------------------------ */
  const panel = document.createElement('aside');
  panel.className = 'rv-panel';
  panel.setAttribute('data-review-ignore', '');
  panel.id = 'rv-panel';
  panel.setAttribute('aria-label', 'Review comments');
  panel.hidden = true;
  document.body.appendChild(panel);

  const fab = document.createElement('button');
  fab.type = 'button';
  fab.className = 'rv-fab';
  fab.setAttribute('data-review-ignore', '');
  fab.setAttribute('aria-controls', 'rv-panel');
  document.body.appendChild(fab);
  fab.addEventListener('click', () => setOpen(true));

  function setOpen(open) {
    ui.open = open;
    panel.hidden = !open;
    document.documentElement.classList.toggle('rv-open', open);
    fab.setAttribute('aria-expanded', String(open));
    writeUi();
    render();
    if (open) { const c = panel.querySelector('.rv-close'); if (c && !panel.contains(document.activeElement)) c.focus({ preventScroll: true }); }
    else fab.focus({ preventScroll: true });
  }

  function focusComment(id, opts) {
    const c = store.get(id);
    if (!c) return;
    ui.active = id;
    if (c.status === 'resolved' && ui.filter === 'active') ui.filter = 'all';
    if (opts && opts.openPanel && !ui.open) setOpen(true); else render();
    paintActive();
    const card = panel.querySelector('.rv-card[data-id="' + CSS.escape(id) + '"]');
    if (card) { card.scrollIntoView({ block: 'nearest' }); card.classList.remove('rv-flash'); void card.offsetWidth; card.classList.add('rv-flash'); }
  }

  function gotoComment(id) {
    const r = anchored.get(id);
    ui.active = id;
    paintActive();
    render();
    if (!r) return;
    if (document.documentElement.clientWidth <= 1100) setOpen(false);
    const rect = r.getBoundingClientRect();
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    window.scrollTo({ top: window.scrollY + rect.top - window.innerHeight / 3, behavior: reduce ? 'auto' : 'smooth' });
  }

  function counts() {
    const n = { open: 0, question: 0, addressed: 0, resolved: 0, total: 0 };
    store.forEach((c) => { if (!c.deleted) { n[c.status]++; n.total++; } });
    return n;
  }

  function visible() {
    const list = [...store.values()].filter((c) => !c.deleted).filter((c) =>
      ui.filter === 'all' ? true : ui.filter === 'resolved' ? c.status === 'resolved' : c.status !== 'resolved');
    const key = (c) => (!c.anchor ? -1 : posOf.has(c.id) ? posOf.get(c.id) : Number.MAX_SAFE_INTEGER);
    return list.sort((a, b) => key(a) - key(b) || cmp(a.createdAt, b.createdAt));
  }

  function draft(key, init) {
    if (!ui.drafts[key]) ui.drafts[key] = init;
    return ui.drafts[key];
  }

  function formHtml(key, withPicker, placeholder, saveAct, cancelAct, saveLabel) {
    const d = ui.drafts[key];
    return '<div class="rv-general" data-form="' + esc(key) + '">' +
      (withPicker ? pickerHtml(key, d.type) : '') +
      '<textarea class="rv-ta" id="rv-ta-' + esc(key.replace(/[^\w-]/g, '_')) + '" data-key="' + esc(key) + '" aria-label="' + esc(placeholder) + '" placeholder="' + esc(placeholder) + '">' + esc(d.body) + '</textarea>' +
      '<div class="rv-row"><span class="rv-kbd">⌘↵ to save</span>' +
      '<button type="button" class="rv-btn" data-act="' + cancelAct + '">Cancel</button>' +
      '<button type="button" class="rv-btn rv-primary" data-act="' + saveAct + '">' + saveLabel + '</button></div></div>';
  }

  function cardHtml(c) {
    const orphan = c.anchor && !anchored.has(c.id);
    let h = '<article class="rv-card' + (ui.active === c.id ? ' rv-current' : '') + '" data-id="' + esc(c.id) + '" data-status="' + c.status + '">';
    h += '<header><span class="rv-type rv-t-' + c.type + '">' + TYPES[c.type].label + '</span>' +
      '<span class="rv-status" data-s="' + c.status + '">' + STATUS[c.status] + '</span>' +
      '<span class="rv-sec">' + esc(c.anchor ? (c.anchor.sectionTitle || '') : 'General comment') + '</span></header>';
    if (c.anchor) {
      h += orphan
        ? '<p class="rv-orphan"><span>No longer on the page:</span> <s>' + esc(trunc(c.anchor.quote, 180)) + '</s></p>'
        : '<button type="button" class="rv-quote" data-act="goto" title="Show on the page">' + esc(trunc(c.anchor.quote, 220)) + '</button>';
    }
    if (ui.editing === c.id) {
      h += formHtml('edit:' + c.id, true, TYPES[ui.drafts['edit:' + c.id].type].hint, 'edit-save', 'edit-cancel', 'Save');
    } else {
      h += '<div class="rv-body">' + (c.body ? fmtBody(c.body) : '<em>' + (c.type === 'delete' ? 'Delete this text.' : 'No note.') + '</em>') + '</div>';
    }
    c.replies.forEach((r) => {
      h += '<div class="rv-reply" data-author="' + r.author + '"><span class="rv-who">' + (r.author === 'claude' ? 'Claude' : 'You') + ' · ' + esc(fmtTime(r.at)) + '</span>' + fmtBody(r.body) + '</div>';
    });
    if (c.status === 'question') h += '<p class="rv-ask">Claude needs your input. Reply to continue.</p>';
    if (ui.replying === c.id) {
      h += formHtml('reply:' + c.id, false, 'Your reply', 'reply-save', 'reply-cancel', c.status === 'open' ? 'Reply' : 'Reply and reopen');
    } else if (ui.confirmDelete === c.id) {
      h += '<div class="rv-confirm"><span>Delete this comment?</span><button type="button" class="rv-btn rv-danger" data-act="delete-yes">Delete</button><button type="button" class="rv-btn" data-act="delete-no">Keep</button></div>';
    } else if (ui.editing !== c.id) {
      h += '<div class="rv-actions">' +
        '<button type="button" class="rv-link" data-act="reply">Reply</button>' +
        (c.status === 'resolved'
          ? '<button type="button" class="rv-link" data-act="reopen">Reopen</button>'
          : '<button type="button" class="rv-link" data-act="resolve">Resolve</button>') +
        '<button type="button" class="rv-link" data-act="edit">Edit</button>' +
        '<button type="button" class="rv-link rv-danger" data-act="delete">Delete</button>' +
        '<span class="rv-meta" style="margin-left:auto">' + esc(fmtTime(c.createdAt)) + '</span></div>';
    }
    return h + '</article>';
  }

  function fileLine() {
    const name = file.handle ? esc(file.handle.name) : esc(FILE_NAME);
    if (!FS) return ['info', 'This browser can’t write files directly. Use <b>Download JSON</b> and save it as <b>' + name + '</b> next to this page.'];
    if (file.error) return ['error', 'Couldn’t save: ' + esc(file.error)];
    if (!file.handle) return ['info', 'Comments are kept in this browser only. To save them next to this page, click <b>Save comments…</b>. ' +
      'The folder path is copied for you: in the save dialog, press <b>⌘⇧G</b>, then <b>⌘V</b>, then <b>Return</b>, and save as <b>' + name + '</b>. ' +
      'You only do this once.<br><code>' + esc(FOLDER) + '</code>'];
    if (file.perm !== 'granted') return ['reconnect', 'Chrome needs permission again to write <b>' + name + '</b>. Click <b>Reconnect file</b>.'];
    if (file.dirty) return ['info', 'Saving to <b>' + name + '</b>…'];
    return ['ok', 'Saved to <b>' + name + '</b>' + (file.savedAt ? ' · ' + esc(fmtTime(file.savedAt)) : '') + '. New comments save automatically.'];
  }

  function render() {
    const n = counts();
    const needs = n.open + n.question;
    fab.innerHTML = 'Comments <span class="rv-badge"' + (needs ? '' : ' data-zero') + '>' + needs + '</span>' +
      (file.touched && file.dirty ? '<span class="rv-dot" title="Not saved to a file yet"></span>' : '');
    fab.setAttribute('aria-label', 'Open review comments, ' + needs + ' waiting');
    fab.setAttribute('aria-expanded', String(ui.open));
    if (!ui.open) return;

    // Keep focus and caret across re-renders.
    const ae = document.activeElement;
    const keep = ae && panel.contains(ae) && ae.id ? { id: ae.id, s: ae.selectionStart, e: ae.selectionEnd } : null;
    const scroll = panel.querySelector('.rv-list') ? panel.querySelector('.rv-list').scrollTop : 0;

    const parts = [];
    if (n.open) parts.push(n.open + ' open');
    if (n.question) parts.push(n.question + ' need your input');
    if (n.addressed) parts.push(n.addressed + ' addressed');
    if (n.resolved) parts.push(n.resolved + ' resolved');
    const active = n.open + n.question + n.addressed;

    let h = '<div class="rv-head"><div><h2>Review comments</h2><p class="rv-sum">' + (parts.join(' · ') || 'No comments yet') + '</p></div>' +
      '<button type="button" class="rv-close" data-act="close" aria-label="Close comments">×</button></div>';
    if (file.fileRevision > REVISION) {
      h += '<div class="rv-banner"><span>Claude updated this page (revision ' + file.fileRevision + '). Reload to see the changes.</span><button type="button" class="rv-btn" data-act="reload">Reload</button></div>';
    }
    h += '<div class="rv-bar"><div class="rv-filters" role="tablist" aria-label="Filter comments">' +
      [['active', 'Active', active], ['resolved', 'Resolved', n.resolved], ['all', 'All', n.total]].map((f) =>
        '<button type="button" role="tab" data-act="filter" data-filter="' + f[0] + '" aria-selected="' + (ui.filter === f[0]) + '">' + f[1] + ' ' + f[2] + '</button>').join('') +
      '</div><button type="button" class="rv-btn" data-act="general">General comment</button></div>';

    h += '<div class="rv-list">';
    if (ui.general) h += formHtml('general', true, TYPES[ui.drafts.general.type].hint, 'general-save', 'general-cancel', 'Add comment');
    const list = visible();
    if (!list.length && !ui.general) {
      h += '<p class="rv-empty">' + (n.total
        ? 'Nothing in this view.'
        : 'Select any text on the page, then choose <b>Improve</b>, <b>Delete</b>, <b>Explain</b>, or <b>Note</b>. Use <b>General comment</b> for feedback about the page as a whole.') + '</p>';
    }
    list.forEach((c) => { h += cardHtml(c); });
    h += '</div>';

    const fl = fileLine();
    const primary = !FS ? '' : !file.handle ? 'Save comments…' : file.perm !== 'granted' ? 'Reconnect file' : 'Save now';
    h += '<div class="rv-foot"><p class="rv-file" data-state="' + fl[0] + '">' + fl[1] + '</p><div class="rv-row">' +
      (primary ? '<button type="button" class="rv-btn rv-primary" data-act="save">' + primary + '</button>' : '') +
      (FS ? '<button type="button" class="rv-btn" data-act="open-file">Open file…</button>' : '') +
      '<button type="button" class="rv-btn" data-act="download">Download JSON</button>' +
      '<button type="button" class="rv-btn" data-act="copy">Copy JSON</button>' +
      (file.handle ? '<button type="button" class="rv-link" data-act="unlink">Unlink file</button>' : '') +
      '</div>' + (ui.flash ? '<p class="rv-file" data-state="ok">' + esc(ui.flash) + '</p>' : '') + '</div>';

    panel.innerHTML = h;
    const listEl = panel.querySelector('.rv-list');
    if (listEl) listEl.scrollTop = scroll;
    if (keep) {
      const el = document.getElementById(keep.id);
      if (el) { el.focus({ preventScroll: true }); if (el.setSelectionRange && keep.s != null) el.setSelectionRange(keep.s, keep.e); }
    }
  }

  function focusField(key) {
    const el = panel.querySelector('textarea[data-key="' + CSS.escape(key) + '"]');
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }

  function flash(msg) {
    ui.flash = msg;
    render();
    clearTimeout(flash.t);
    flash.t = setTimeout(() => { ui.flash = ''; render(); }, 2500);
  }

  panel.addEventListener('input', (e) => {
    const key = e.target.dataset.key;
    if (key && ui.drafts[key]) ui.drafts[key].body = e.target.value;
  });
  panel.addEventListener('keydown', (e) => {
    const key = e.target.dataset && e.target.dataset.key;
    if (e.key === 'Escape') {
      e.preventDefault();
      if (key && key.startsWith('reply:')) { ui.replying = null; render(); }
      else if (key && key.startsWith('edit:')) { ui.editing = null; render(); }
      else if (key === 'general') { ui.general = false; render(); }
      else setOpen(false);
      return;
    }
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && key) {
      e.preventDefault();
      const act = key === 'general' ? 'general-save' : key.startsWith('reply:') ? 'reply-save' : 'edit-save';
      handle(act, key.includes(':') ? key.split(':').slice(1).join(':') : null, null);
    }
  });
  panel.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const card = b.closest('.rv-card');
    handle(b.dataset.act, card ? card.dataset.id : null, b);
  });

  function handle(act, id, btn) {
    const c = id ? store.get(id) : null;
    switch (act) {
      case 'close': setOpen(false); break;
      case 'reload': location.reload(); break;
      case 'filter': ui.filter = btn.dataset.filter; writeUi(); render(); break;
      case 'general':
        ui.general = true; draft('general', { type: 'note', body: '' }); render(); focusField('general'); break;
      case 'general-cancel': ui.general = false; delete ui.drafts.general; render(); break;
      case 'general-save': {
        const d = ui.drafts.general;
        if (!d || !d.body.trim()) { focusField('general'); break; }
        ui.general = false; delete ui.drafts.general;
        const newId = addComment(d.type, d.body.trim(), null);
        ui.active = newId; render();
        break;
      }
      case 'pick': {
        const d = ui.drafts[btn.dataset.key];
        if (d) { d.type = btn.dataset.type; render(); focusField(btn.dataset.key); }
        break;
      }
      case 'goto': gotoComment(id); break;
      case 'reply':
        ui.replying = id; ui.editing = null; ui.confirmDelete = null;
        draft('reply:' + id, { type: 'note', body: '' }); render(); focusField('reply:' + id); break;
      case 'reply-cancel': ui.replying = null; delete ui.drafts['reply:' + id]; render(); break;
      case 'reply-save': {
        const d = ui.drafts['reply:' + id];
        if (!c || !d || !d.body.trim()) { focusField('reply:' + id); break; }
        ui.replying = null; delete ui.drafts['reply:' + id];
        update(id, (x) => {
          const now = iso();
          x.replies.push({ id: uid('r'), author: 'you', body: d.body.trim(), at: now });
          if (x.status !== 'open') { x.status = 'open'; x.statusAt = now; }
        });
        break;
      }
      case 'edit':
        ui.editing = id; ui.replying = null; ui.confirmDelete = null;
        ui.drafts['edit:' + id] = { type: c.type, body: c.body }; render(); focusField('edit:' + id); break;
      case 'edit-cancel': ui.editing = null; delete ui.drafts['edit:' + id]; render(); break;
      case 'edit-save': {
        const d = ui.drafts['edit:' + id];
        if (!c || !d) break;
        if (!d.body.trim() && d.type !== 'delete') { focusField('edit:' + id); break; }
        ui.editing = null; delete ui.drafts['edit:' + id];
        update(id, (x) => { x.type = d.type; x.body = d.body.trim(); x.updatedAt = iso(); });
        break;
      }
      case 'resolve': update(id, (x) => { x.status = 'resolved'; x.statusAt = iso(); }); break;
      case 'reopen': update(id, (x) => { x.status = 'open'; x.statusAt = iso(); }); break;
      case 'delete': ui.confirmDelete = id; render(); break;
      case 'delete-no': ui.confirmDelete = null; render(); break;
      case 'delete-yes':
        ui.confirmDelete = null;
        if (ui.active === id) ui.active = null;
        update(id, (x) => { x.deleted = true; x.deletedAt = iso(); });
        break;
      case 'save': saveClick(); break;
      case 'open-file': openClick(); break;
      case 'download': download(); break;
      case 'copy': copyJson(); break;
      case 'unlink': unlink(); break;
    }
  }

  /* ------------------------------------------------------------------ *
   * File sync (File System Access API, Chrome)
   * The file handle is remembered in IndexedDB. Every write reads the
   * file first and merges it, so edits Claude makes to the JSON survive.
   * ------------------------------------------------------------------ */
  function idbOpen() {
    return new Promise((resolve, reject) => {
      const q = indexedDB.open('niab-review', 1);
      q.onupgradeneeded = () => q.result.createObjectStore('handles');
      q.onsuccess = () => resolve(q.result);
      q.onerror = () => reject(q.error);
    });
  }
  async function idb(mode, fn) {
    const db = await idbOpen();
    return new Promise((resolve, reject) => {
      const t = db.transaction('handles', mode);
      const req = fn(t.objectStore('handles'));
      t.oncomplete = () => resolve(req && req.result);
      t.onerror = () => reject(t.error);
    });
  }
  const idbGet = (k) => idb('readonly', (s) => s.get(k));
  const idbSet = (k, v) => idb('readwrite', (s) => s.put(v, k));
  const idbDel = (k) => idb('readwrite', (s) => s.delete(k));

  async function refreshPerm(request) {
    if (!file.handle) { file.perm = 'none'; return false; }
    const opts = { mode: 'readwrite' };
    try {
      let p = await file.handle.queryPermission(opts);
      if (p !== 'granted' && request) p = await file.handle.requestPermission(opts);
      file.perm = p;
    } catch (e) { file.perm = 'denied'; }
    return file.perm === 'granted';
  }

  function parseFileText(text) {
    let data;
    try { data = JSON.parse(text); } catch (e) { throw new Error('the file isn’t valid JSON.'); }
    if (!data || typeof data !== 'object' || !Array.isArray(data.comments)) throw new Error('the file isn’t a review comments file.');
    if (data.document && data.document !== DOC) throw new Error('that file holds comments for ' + data.document + ', not ' + DOC + '.');
    if (REVIEW_ID && data.reviewId && data.reviewId !== REVIEW_ID) throw new Error('that file holds comments for an earlier version of this page. Save to a new file instead.');
    return data;
  }

  let chain = Promise.resolve();
  function syncFile() {
    const run = chain.then(doSync, doSync);
    chain = run.catch(() => {});
    return run;
  }
  async function doSync() {
    const h = file.handle;
    if (!h || file.perm !== 'granted') return;
    try {
      const text = await (await h.getFile()).text();
      let existing = null;
      if (text.trim()) existing = parseFileText(text);
      if (existing) {
        file.fileRevision = Math.max(file.fileRevision, Number(existing.documentRevision) || 0);
        if (mergeList(existing.comments)) { writeLocal(); paint(); }
      }
      const out = buildPayload();
      const same = existing && canonList(existing.comments) === canonList(out.comments) &&
        (Number(existing.documentRevision) || 0) === out.documentRevision && existing.schema === SCHEMA;
      if (!same) {
        const w = await h.createWritable();
        await w.write(JSON.stringify(out, null, 2) + '\n');
        await w.close();
      }
      file.dirty = false;
      file.savedAt = iso();
      file.error = '';
    } catch (e) {
      if (e && e.name === 'NotAllowedError') file.perm = 'prompt';
      else file.error = (e && e.message) || String(e);
    }
    render();
  }

  let syncTimer = 0;
  function scheduleSync() {
    if (!file.handle || file.perm !== 'granted') return;
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncFile, 600);
  }

  async function adopt(h) {
    file.handle = h;
    file.error = '';
    file.fileRevision = 0;
    try { await idbSet(LS_KEY, h); } catch (e) { /* handle not remembered across visits */ }
  }

  // Not awaited: the picker must open inside the same click, and a clipboard
  // write does not consume the click's user activation.
  function copyFolderPath() {
    try { navigator.clipboard.writeText(FOLDER).catch(() => {}); } catch (e) { /* path is shown in the panel */ }
  }

  async function saveClick() {
    if (!FS) { download(); return; }
    try {
      if (!file.handle) {
        copyFolderPath();
        const h = await window.showSaveFilePicker({ suggestedName: FILE_NAME, types: PICKER_TYPES, id: 'niab-review' });
        await adopt(h);
      }
      if (await refreshPerm(true)) {
        await syncFile();
        if (file.error && /comments for/.test(file.error)) await unlink(true);
      }
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      file.error = (e && e.message) || String(e);
    }
    render();
  }

  async function openClick() {
    try {
      copyFolderPath();
      const [h] = await window.showOpenFilePicker({ types: PICKER_TYPES, id: 'niab-review', multiple: false });
      const data = parseFileText(await (await h.getFile()).text());
      mergeList(data.comments);
      writeLocal();
      paint();
      await adopt(h);
      if (await refreshPerm(true)) await syncFile();
      flash('Loaded ' + data.comments.filter((c) => !c.deleted).length + ' comments from ' + h.name + '.');
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      file.error = (e && e.message) || String(e);
      render();
    }
  }

  async function unlink(keepError) {
    file.handle = null;
    file.perm = 'none';
    if (!keepError) file.error = '';
    try { await idbDel(LS_KEY); } catch (e) { /* ignore */ }
    render();
  }

  function download() {
    const blob = new Blob([JSON.stringify(buildPayload(), null, 2) + '\n'], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = FILE_NAME;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    flash('Downloaded ' + FILE_NAME + '. Move it next to this page.');
  }

  function copyJson() {
    const text = JSON.stringify(buildPayload(), null, 2);
    navigator.clipboard.writeText(text).then(
      () => flash('Copied the comments as JSON.'),
      () => flash('Couldn’t copy. Use Download JSON instead.')
    );
  }

  // Pick up Claude's edits to the JSON when you come back to the tab.
  document.addEventListener('visibilitychange', async () => {
    if (document.visibilityState !== 'visible' || !file.handle) return;
    if (await refreshPerm(false)) syncFile(); else render();
  });

  window.addEventListener('beforeunload', (e) => {
    if (file.touched && file.dirty && !(file.handle && file.perm === 'granted')) { e.preventDefault(); e.returnValue = ''; }
  });

  /* ------------------------------------------------------------------ *
   * Start
   * ------------------------------------------------------------------ */
  async function init() {
    mergeList(EMBED.comments);
    mergeList(readLocal());
    writeLocal();
    readUi();
    INDEX = buildIndex();
    paint();
    panel.hidden = !ui.open;
    document.documentElement.classList.toggle('rv-open', ui.open);
    render();
    if (!FS) return;
    try {
      const h = await idbGet(LS_KEY);
      if (h) {
        file.handle = h;
        if (await refreshPerm(false)) await syncFile();
      }
    } catch (e) { /* IndexedDB unavailable: the file must be picked each visit */ }
    render();
  }
  /* ------------------------------------------------------------------ *
   * Theme: follow the page's own background, not the operating system.
   * ------------------------------------------------------------------ */
  function background(el) {
    const m = (getComputedStyle(el).backgroundColor || '').match(/[\d.]+/g);
    if (!m || (m.length === 4 && Number(m[3]) === 0)) return null;
    return m.slice(0, 3).map(Number);
  }
  function syncTheme() {
    const rgb = background(document.body) || background(document.documentElement) || [255, 255, 255];
    const luminance = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255;
    const theme = luminance < 0.5 ? 'dark' : 'light';
    if (document.documentElement.getAttribute('data-rv-theme') !== theme) document.documentElement.setAttribute('data-rv-theme', theme);
  }
  syncTheme();
  try { window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncTheme); } catch (e) { /* old browser */ }
  new MutationObserver(syncTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class', 'style'] });

  init();
})();
