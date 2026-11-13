// CommerceCrew customer chat: the store's customer-facing demo channel.
// It talks only to the public support routes and renders only the customer-safe fields they
// return (PublicConversation and PublicReply in app/agents/support.py). Merchant notes,
// evidence and review items never reach this page.
//
//   POST /api/support/conversations                {customerName, productId}  -> PublicConversation
//   GET  /api/support/conversations/{id}                                       -> PublicConversation
//   POST /api/support/conversations/{id}/messages   {text, clientMessageId}   -> PublicReply
//
// Open it from a product page as chat/?product=<productId>&name=<first name>; both are optional.
// Same-origin by default; set window.CC_API_BASE to point elsewhere, as in the cockpit.
(function () {
  'use strict';

  const ID = /^[A-Za-z0-9_-]{1,64}$/;
  const SAFE_URL = /^https?:\/\/\S+$/i;
  const MAX_CHARS = 2000;
  const COUNTER_FROM = 1800;
  const POLL_MS = 4000;
  const MAX_POLL_MS = 30000;
  const PATH = '/api/support/conversations';

  const $ = id => document.getElementById(id);
  const els = {
    connection: $('connection'), product: $('product'), productTitle: $('product-title'),
    productLink: $('product-link'), log: $('log'), messages: $('messages'), typing: $('typing'),
    delayed: $('delayed'), unavailable: $('unavailable'), reconnect: $('reconnect'),
    waiting: $('waiting'), waitingText: $('waiting-text'), form: $('composer'), input: $('input'),
    counter: $('counter'), send: $('send'),
  };

  const storeName = document.body.dataset.store || 'Shop team';
  const params = new URLSearchParams(location.search);
  const productId = ID.test(params.get('product') || '') ? params.get('product') : null;
  const customerName = (params.get('name') || '').replace(/\s+/g, ' ').trim().slice(0, 80) || 'Guest';
  const storageKey = 'commercecrew-chat:' + (productId || 'general');
  const clock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' });

  const state = {
    phase: 'connecting', // connecting | ready | unavailable
    conversation: null, // the latest PublicConversation
    expired: false, // the service no longer knows this chat; the next message starts a new one
    pending: [], // customer messages the service has not confirmed yet
    inFlight: 0,
    shopCount: 0, // replies seen, to clear the "on its way" note when one arrives
    delayed: false,
    failures: 0,
    pollDelay: POLL_MS,
    pollTimer: 0,
    refreshing: null,
  };

  // ── Service ──────────────────────────────────────────────────────────

  class ServiceError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }

  async function call(method, path, payload) {
    let res;
    try {
      res = await fetch((window.CC_API_BASE || '').replace(/\/$/, '') + path, {
        method,
        headers: Object.assign({ Accept: 'application/json' }, payload ? { 'Content-Type': 'application/json' } : {}),
        body: payload ? JSON.stringify(payload) : undefined,
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch (e) {
      throw new ServiceError(0, '');
    }
    let body = null;
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new ServiceError(res.status, detailText(body));
    return body;
  }

  function detailText(body) {
    const d = body && body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(x => (x && x.msg) || '').filter(Boolean).join('; ');
    return '';
  }

  const isConversation = c => !!c && typeof c.conversationId === 'string' && ID.test(c.conversationId) && Array.isArray(c.messages);
  const conversationPath = id => PATH + '/' + encodeURIComponent(id);

  // The conversation id is the chat's access token: kept in this browser only.
  function remember(id) { try { localStorage.setItem(storageKey, id); } catch (e) { /* storage blocked */ } }
  function forget() { try { localStorage.removeItem(storageKey); } catch (e) { /* storage blocked */ } }
  function recall() {
    try { const id = localStorage.getItem(storageKey); return id && ID.test(id) ? id : null; } catch (e) { return null; }
  }

  async function create() {
    const convo = await call('POST', PATH, { customerName, productId });
    if (!isConversation(convo)) throw new ServiceError(-1, '');
    remember(convo.conversationId);
    return convo;
  }

  function accept(convo) {
    if (!isConversation(convo)) throw new ServiceError(-1, '');
    state.conversation = convo;
    state.expired = false;
    // A message the service stored is shown from the service's copy, even after a lost reply.
    const stored = new Set(convo.messages.filter(m => m.author === 'customer' && m.clientMessageId).map(m => m.clientMessageId));
    state.pending = state.pending.filter(p => !stored.has(p.clientMessageId));
    const replies = convo.messages.filter(m => m.author !== 'customer').length;
    if (replies > state.shopCount) state.delayed = false;
    state.shopCount = replies;
  }

  async function connect() {
    clearTimeout(state.pollTimer);
    state.phase = 'connecting';
    render();
    try {
      let convo = null;
      const saved = recall();
      if (saved) {
        try { convo = await call('GET', conversationPath(saved)); } catch (e) {
          if (e.status !== 404 && e.status !== 403) throw e;
          forget();
        }
      }
      accept(convo || await create());
      state.phase = 'ready';
      state.failures = 0;
    } catch (e) {
      state.phase = 'unavailable';
    }
    render(true);
    schedulePoll();
  }

  function refresh() {
    if (!state.conversation || state.expired) return Promise.resolve();
    if (state.refreshing) return state.refreshing;
    const id = state.conversation.conversationId;
    state.refreshing = (async () => {
      try {
        const convo = await call('GET', conversationPath(id));
        if (state.conversation && state.conversation.conversationId === id) accept(convo);
        state.failures = 0;
        state.pollDelay = POLL_MS;
      } catch (e) {
        if (e.status === 404 || e.status === 403) {
          state.expired = true;
          forget();
        } else {
          state.failures += 1;
          state.pollDelay = Math.min(state.pollDelay * 2, MAX_POLL_MS);
        }
      } finally {
        state.refreshing = null;
        render();
      }
    })();
    return state.refreshing;
  }

  // New replies from the shop team arrive by polling; paused while the tab is hidden.
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (state.phase !== 'ready' || state.expired || !state.conversation) return;
    state.pollTimer = setTimeout(async () => {
      if (document.visibilityState === 'visible' && state.inFlight === 0) await refresh();
      schedulePoll();
    }, state.pollDelay);
  }

  // ── Sending ──────────────────────────────────────────────────────────

  function newId() {
    const random = window.crypto && crypto.randomUUID ? crypto.randomUUID()
      : Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
    return 'c-' + random;
  }

  function submit(event) {
    event.preventDefault();
    const text = els.input.value.trim();
    if (!text || text.length > MAX_CHARS || state.phase !== 'ready') return;
    const entry = { clientMessageId: newId(), text, status: 'sending', error: '', editable: false, createdAt: new Date().toISOString() };
    state.pending.push(entry);
    els.input.value = '';
    fitInput();
    deliver(entry);
  }

  // The same clientMessageId on every attempt: a retry after a lost reply never sends twice.
  async function deliver(entry) {
    entry.status = 'sending';
    entry.error = '';
    entry.editable = false;
    state.inFlight += 1;
    render(true);
    let stored = false;
    try {
      if (!state.conversation || state.expired) accept(await create());
      const reply = await call('POST', conversationPath(state.conversation.conversationId) + '/messages',
        { text: entry.text, clientMessageId: entry.clientMessageId });
      entry.status = 'stored';
      stored = true;
      if (reply && reply.status === 'delivery_failed') state.delayed = true;
    } catch (e) {
      fail(entry, e);
    } finally {
      state.inFlight -= 1;
    }
    if (stored) await refresh();
    render(stored);
    schedulePoll();
  }

  function fail(entry, e) {
    entry.status = 'failed';
    if (e.status === 404 || e.status === 403) {
      state.expired = true;
      forget();
      entry.error = 'Not sent. This chat was reset; retry to send it in a new chat.';
    } else if (e.status === 400 || e.status === 413 || e.status === 422) {
      entry.editable = true;
      entry.error = 'Not sent. ' + (e.message ? sentence(e.message) : 'The shop could not accept this message.');
    } else if (e.status === 0) {
      entry.error = 'Not sent. Check your connection, then retry.';
    } else {
      entry.error = 'Not sent. Something went wrong on our side; please retry.';
    }
  }

  function edit(entry) {
    state.pending = state.pending.filter(p => p !== entry);
    els.input.value = entry.text;
    fitInput();
    render();
    els.input.focus();
  }

  const sentence = text => { const t = String(text).trim(); return t.charAt(0).toUpperCase() + t.slice(1) + (/[.!?]$/.test(t) ? '' : '.'); };

  // ── Rendering ────────────────────────────────────────────────────────

  const nodes = new Map();
  const AUTHOR = { customer: 'customer', assistant: 'assistant', shop_team: 'shop' };

  function items() {
    const list = (state.conversation ? state.conversation.messages : []).map(m => ({
      key: m.author === 'customer' && m.clientMessageId ? 'c:' + m.clientMessageId : 'm:' + m.messageId,
      kind: AUTHOR[m.author] || 'assistant',
      text: String(m.text || ''),
      links: Array.isArray(m.links) ? m.links : [],
      createdAt: m.createdAt,
      status: m.author === 'customer' ? 'sent' : '',
    }));
    for (const p of state.pending) {
      list.push({ key: 'c:' + p.clientMessageId, kind: 'customer', text: p.text, links: [], createdAt: p.createdAt,
        status: p.status === 'stored' ? 'sent' : p.status, error: p.error, editable: p.editable, entry: p });
    }
    return list;
  }

  function renderMessages() {
    const list = items();
    const customer = list.filter(i => i.kind === 'customer');
    const lastKey = customer.length ? customer[customer.length - 1].key : '';
    const wanted = new Set(list.map(i => i.key));
    for (const [key, node] of nodes) if (!wanted.has(key)) { node.remove(); nodes.delete(key); }
    let cursor = els.messages.firstChild;
    for (const item of list) {
      let node = nodes.get(item.key);
      if (!node) { node = document.createElement('li'); nodes.set(item.key, node); }
      const last = item.key === lastKey;
      const sig = JSON.stringify([item.kind, item.text, item.status, item.error, item.editable, last, item.createdAt,
        item.links.map(l => [l.label, l.url])]);
      if (node.dataset.sig !== sig) { fill(node, item, last); node.dataset.sig = sig; }
      if (node === cursor) cursor = cursor.nextSibling; else els.messages.insertBefore(node, cursor);
    }
  }

  function fill(node, item, last) {
    node.className = 'msg msg--' + item.kind + (item.status === 'sending' ? ' is-pending' : '') + (item.status === 'failed' ? ' is-failed' : '');
    node.replaceChildren();

    const meta = el('div', 'msg-meta');
    meta.append(el('span', 'msg-author', item.kind === 'customer' ? 'You' : item.kind === 'shop' ? storeName : 'Assistant'));
    if (item.kind === 'assistant') meta.append(el('span', 'msg-tag', 'Automated'));
    const at = new Date(item.createdAt);
    if (!isNaN(at)) {
      const time = el('time', '', clock.format(at));
      time.dateTime = at.toISOString();
      meta.append(time);
    }
    node.append(meta, el('p', 'bubble', item.text));

    const links = item.links.filter(l => l && SAFE_URL.test(String(l.url || '')));
    if (links.length) {
      const row = el('div', 'msg-links');
      for (const l of links) {
        const a = el('a', 'link-btn', String(l.label || 'Open link'));
        a.href = l.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.append(icon('M15 3h6v6|M10 14 21 3|M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6'));
        row.append(a);
      }
      node.append(row);
    }

    if (item.kind !== 'customer') return;
    if (item.status === 'sending') node.append(el('div', 'msg-status', 'Sending…'));
    else if (item.status === 'failed') {
      const status = el('div', 'msg-status is-failed');
      status.append(el('span', '', item.error));
      if (!item.editable) status.append(button('Retry', () => deliver(item.entry)));
      status.append(button('Edit', () => edit(item.entry)));
      node.append(status);
    } else if (last) node.append(el('div', 'msg-status', 'Sent'));
  }

  function renderChrome() {
    const c = state.conversation;
    const offline = state.phase === 'unavailable' || (state.phase === 'ready' && state.failures >= 2);
    els.connection.textContent = state.phase === 'connecting' ? 'Connecting…'
      : state.phase === 'unavailable' ? 'Not connected'
      : state.failures >= 2 ? 'Reconnecting…' : 'Assistant and shop team';
    els.connection.classList.toggle('is-offline', offline);

    const product = c && c.product && c.product.title ? c.product : null;
    els.product.hidden = !product;
    if (product) {
      els.productTitle.textContent = product.title;
      const url = SAFE_URL.test(String(product.url || '')) ? product.url : '';
      els.productLink.hidden = !url;
      if (url) els.productLink.href = url;
    }

    els.unavailable.hidden = state.phase !== 'unavailable';
    // The assistant does not answer while the shop team is handling the chat, so no typing dots then.
    els.typing.hidden = !(state.inFlight > 0 && !(c && c.waitingForMerchant) && !state.expired);
    els.delayed.hidden = !state.delayed;

    let waiting = '';
    if (state.expired) waiting = 'This chat was reset. Your next message starts a new one.';
    else if (c && c.waitingForMerchant) waiting = c.notice || 'The shop team is looking at this conversation and will reply here.';
    els.waiting.hidden = !waiting;
    els.waitingText.textContent = waiting;

    const ready = state.phase === 'ready';
    const length = els.input.value.length;
    els.input.disabled = !ready;
    els.send.disabled = !ready || !els.input.value.trim() || length > MAX_CHARS;
    els.counter.hidden = length < COUNTER_FROM;
    els.counter.textContent = length + ' / ' + MAX_CHARS;
    els.counter.classList.toggle('is-over', length > MAX_CHARS);
  }

  function render(stick) {
    const log = els.log;
    const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 96;
    renderMessages();
    renderChrome();
    if (stick || nearBottom) log.scrollTop = log.scrollHeight;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text; // never innerHTML: messages are untrusted text
    return node;
  }

  function button(label, onClick) {
    const b = el('button', 'text-btn', label);
    b.type = 'button';
    b.addEventListener('click', onClick);
    return b;
  }

  function icon(paths) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('width', '14');
    svg.setAttribute('height', '14');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('aria-hidden', 'true');
    for (const d of paths.split('|')) {
      const path = document.createElementNS(ns, 'path');
      path.setAttribute('d', d);
      svg.append(path);
    }
    return svg;
  }

  function fitInput() {
    els.input.style.height = 'auto';
    els.input.style.height = Math.min(els.input.scrollHeight, 160) + 'px';
    renderChrome();
  }

  // ── Wiring ───────────────────────────────────────────────────────────

  els.form.addEventListener('submit', submit);
  els.input.addEventListener('input', fitInput);
  els.input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!els.send.disabled) els.form.requestSubmit ? els.form.requestSubmit() : submit(e);
    }
  });
  els.reconnect.addEventListener('click', connect);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && state.inFlight === 0) refresh().then(schedulePoll);
  });
  window.addEventListener('online', () => { if (state.phase === 'unavailable') connect(); else refresh(); });

  render();
  connect();
})();
