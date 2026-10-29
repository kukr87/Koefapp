/**
 * Koefii Mini App
 */
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  try { tg.setHeaderColor('#0a0e17'); tg.setBackgroundColor('#0a0e17'); } catch (_) {}
  try { tg.disableVerticalSwipes?.(); } catch (_) {}
  try { tg.isVerticalSwipesEnabled = false; } catch (_) {}
}

const API = '/api';
let currentUser = null;
let currentPage = 'home';
let pageHistory = [];
let lastPageData = null;
let parserLeagues = [];
let aiCycleTimer = null;
/** Короткий кэш GET — страницы не ждут сеть повторно */
const _apiCache = new Map();
const CACHE_TTL = {
  '/me': 45000,
  '/matches': 60000,
  '/express?page=0': 60000,
  '/favorites': 30000,
};
const _inflight = new Map();

function getInitData() { return tg?.initData || ''; }

function _cacheKey(path, options) {
  const method = (options && options.method) || 'GET';
  if (method !== 'GET' && method !== 'get') return null;
  return path;
}

function invalidateApiCache(prefix) {
  if (!prefix) { _apiCache.clear(); return; }
  for (const k of [..._apiCache.keys()]) {
    if (k === prefix || k.startsWith(prefix)) _apiCache.delete(k);
  }
}

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const key = _cacheKey(path, options);
  const skipCache = options.noCache || options.refresh;
  if (key && !skipCache) {
    const hit = _apiCache.get(key);
    if (hit && Date.now() - hit.ts < (CACHE_TTL[key] || 20000)) {
      return hit.data;
    }
    if (_inflight.has(key)) return _inflight.get(key);
  }
  const headers = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': getInitData(),
    ...(options.headers || {}),
  };
  const ctrl = new AbortController();
  const ms = options.timeoutMs || 12000;
  const timer = setTimeout(() => ctrl.abort(), ms);
  const run = (async () => {
    try {
      const res = await fetch(API + path, { ...options, headers, signal: ctrl.signal });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || err.message || 'Ошибка сервера');
      }
      const data = await res.json();
      if (key) {
        _apiCache.set(key, { ts: Date.now(), data });
      }
      // после мутаций сбрасываем связанные кэши
      if (method !== 'GET') {
        invalidateApiCache('/me');
        invalidateApiCache('/matches');
        invalidateApiCache('/favorites');
        invalidateApiCache('/express');
      }
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw new Error('Сервер не отвечает. Попробуйте позже');
      throw e;
    } finally {
      clearTimeout(timer);
      if (key) _inflight.delete(key);
    }
  })();
  if (key && !skipCache) _inflight.set(key, run);
  return run;
}

function toast(msg, duration = 2500) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/** Modern line icons (not Telegram emoji style) */
const ICONS = {
  shirt: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 5l-3 2v3l3-1v10h8V9l3 1V7l-3-2"/><path d="M12 5v2"/><path d="M9.5 5.5c.5-1.2 1.2-1.8 2.5-1.8s2 .6 2.5 1.8"/></svg>',
  chart: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 16v-5"/><path d="M12 16V8"/><path d="M16 16v-3"/></svg>',
  trend: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h6v6"/></svg>',
  pistol: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h11l2-2h4v3h-3l-1 2H8"/><path d="M8 15v3h-2"/><path d="M3 12V9h2v3"/><path d="M5 9h3"/></svg>',
  duel: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a12 12 0 010 18"/><path d="M12 3a12 12 0 000 18"/><path d="M3 12h18"/><path d="M5 5l14 14"/></svg>',
  coef: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v10"/><path d="M9 10.5c.5-1 1.5-1.5 3-1.5s2.5.6 2.5 1.8c0 2.2-5 1.4-5 4 0 1.2 1.1 2 2.5 2s2.3-.5 2.8-1.3"/></svg>',
  star: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.5 6.5L21 10l-5 4.5L17.5 21 12 17.5 6.5 21 8 14.5 3 10l6.5-.5L12 3z"/></svg>',
  lock: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>',
  channel: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4"/><path d="M8 8v8"/><path d="M12 5v14"/><path d="M16 8v8"/><path d="M20 10v4"/></svg>',
  hammer: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3l6 6"/><path d="M14 4l6 6-3.5 1.5L12.5 7.5 14 4z"/><path d="M12.5 7.5L5 19l-1.5-1.5L11 6"/><path d="M4 20l2-1"/></svg>',
  tools: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 015 5L12 19l-4-1-1-4 7.7-7.7z"/><path d="M12 19l-3 3"/><path d="M4 20l6-6"/><path d="M8.5 12.5l3 3"/><path d="M5 17l-2 2 2 1 1-2z"/></svg>',
  wrench: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 015 5L12 19l-4-1-1-4 7.7-7.7z"/><path d="M12 19l-3 3"/><path d="M4 20l6-6"/><path d="M8.5 12.5l3 3"/><path d="M5 17l-2 2 2 1 1-2z"/></svg>',
  maintenance: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 015 5L12 19l-4-1-1-4 7.7-7.7z"/><path d="M12 19l-3 3"/><path d="M4 20l6-6"/><circle cx="7.5" cy="7.5" r="2.2"/><path d="M7.5 4.8V3.2M7.5 10.2v1.6M4.8 7.5H3.2M10.2 7.5h1.6"/></svg>',
  repair: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a3.5 3.5 0 014.9 4.9L12 19l-3.5-.5L8 15l6.7-8.7z"/><path d="M11.5 18.5L9 21"/><path d="M5 16l3 3"/><path d="M3.5 19.5l2 1 1-2"/><path d="M4 8l2-2M6 4h2l1 2-3 3-2-1V6z"/></svg>',
  ball: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a12 12 0 010 18"/><path d="M12 3a12 12 0 000 18"/><path d="M3 12h18"/><path d="M5.5 7.5c2 1.5 4.5 2.5 6.5 2.5s4.5-1 6.5-2.5"/><path d="M5.5 16.5c2-1.5 4.5-2.5 6.5-2.5s4.5 1 6.5 2.5"/></svg>',
  heart: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-4.5-7-10a4.5 4.5 0 019 0 4.5 4.5 0 019 0c0 5.5-7 10-7 10z"/></svg>',
  refresh: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.6-6.3"/><path d="M21 3v6h-6"/></svg>',
  warning: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3L2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17h.01"/></svg>',
  clock: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  live: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3" fill="currentColor"/><path d="M5.5 5.5a9 9 0 000 13"/><path d="M18.5 5.5a9 9 0 010 13"/><path d="M8.5 8.5a5 5 0 000 7"/><path d="M15.5 8.5a5 5 0 010 7"/></svg>',
  impulse: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h3l2-6 4 12 3-8 2 2h4"/></svg>',
  medical: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><path d="M12 8v8"/><path d="M8 12h8"/></svg>',
  swords: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3l5 5-7 7-3-1 1-3 7-7z"/><path d="M16 3l-5 5 7 7 3-1-1-3-7-7z"/><path d="M5 19l2-2"/><path d="M17 19l-2-2"/><path d="M9.5 14.5l-2 2"/><path d="M14.5 14.5l2 2"/></svg>',
  trophy: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4h8v5a4 4 0 01-8 0V4z"/><path d="M8 4H6a2 2 0 00-2 2v1a3 3 0 003 3"/><path d="M16 4h2a2 2 0 012 2v1a3 3 0 01-3 3"/><path d="M12 13v3"/><path d="M9 20h6"/><path d="M10 17h4v3h-4z"/></svg>',
  free: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12h8"/><path d="M12 8v8"/></svg>',
  bot: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="7" width="14" height="12" rx="3"/><path d="M9 11h.01"/><path d="M15 11h.01"/><path d="M9 15h6"/><path d="M12 3v4"/><path d="M8 3h8"/></svg>',
  field: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M3 12h18"/><circle cx="12" cy="12" r="2.5"/><path d="M3 9v6"/><path d="M21 9v6"/><path d="M8 5v14"/><path d="M16 5v14"/></svg>',
  chat: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6a3 3 0 013-3h10a3 3 0 013 3v8a3 3 0 01-3 3H9l-5 4V6z"/></svg>',
  calendar: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>',
  user: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4-5 8-5s6.5 1.5 8 5"/></svg>',
  squad: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="7" r="3"/><circle cx="17" cy="8" r="2.5"/><path d="M3 19c1-3.5 3.5-5 6-5s5 1.5 6 5"/><path d="M14 19c.5-2 1.8-3.2 3.5-3.5 1.5.2 2.7 1.2 3.5 3.5"/></svg>',
  coach: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2 4h4l-3 3 1 5-4-2.5L8 15l1-5-3-3h4z"/><path d="M5 20h14"/></svg>',
  half: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M12 12a9 9 0 019 0"/></svg>',
  target: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/><path d="M12 3v2"/><path d="M12 19v2"/><path d="M3 12h2"/><path d="M19 12h2"/></svg>',
  pred: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5a1 1 0 011-1h6l2 3h7a1 1 0 011 1v11a1 1 0 01-1 1H5a1 1 0 01-1-1z"/><path d="M9 14l2 2 4-4"/></svg>',
  stats: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19V11"/></svg>',
  table: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M9 10v10"/><path d="M15 10v10"/></svg>',
  link: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 007.07 0l1.41-1.41a5 5 0 00-7.07-7.07L10 5.93"/><path d="M14 11a5 5 0 00-7.07 0L5.52 12.41a5 5 0 007.07 7.07L14 18.07"/></svg>',
  support: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 16v-1"/><path d="M12 8a2.5 2.5 0 012.2 3.7c-.5.8-1.2 1.1-1.7 1.5-.4.3-.5.5-.5.8"/></svg>',
  users: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 19c1-3.5 3.5-5 6.5-5s5.5 1.5 6.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 19c.6-2 1.8-3.2 3.5-3.5 1.3.2 2.4 1 3 2.5"/></svg>',
  bug: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 9h8v7a4 4 0 01-8 0V9z"/><path d="M12 5v4"/><path d="M9 5h6"/><path d="M5 12H3"/><path d="M21 12h-2"/><path d="M6 8l-2-2"/><path d="M18 8l2-2"/><path d="M6 17l-2 2"/><path d="M18 17l2 2"/></svg>',
  idea: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6"/><path d="M10 21h4"/><path d="M12 3a5.5 5.5 0 00-3 10c.6.5 1 1.2 1 2h4c0-.8.4-1.5 1-2a5.5 5.5 0 00-3-10z"/></svg>',
  card: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/><path d="M6 15h4"/></svg>',
  question: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 014.5 1.5c0 1.5-2 2-2 3.5"/><path d="M12 17h.01"/></svg>',
  fist: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3.5a1.5 1.5 0 011.5 1.5v5"/><path d="M10.5 4a1.5 1.5 0 011.5 1.5v5"/><path d="M8 5.5A1.5 1.5 0 019.5 7v4"/><path d="M5.5 8A1.5 1.5 0 017 9.5V13"/><path d="M15.5 8.5v3.2c0 3.2-2 5.8-5.5 6.3-3.2.4-5.5-1.5-5.5-4.5V12"/><path d="M15.5 10.5c1.2 0 2 .8 2 2v1c0 2.5-1.5 4.5-4 5"/></svg>',
  behavior: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-2a8 8 0 0116 0v2"/><path d="M4 14a2 2 0 104 0"/><path d="M16 14a2 2 0 104 0"/><path d="M8 14v2a4 4 0 008 0v-2"/><path d="M12 6v2"/></svg>',
  bolt: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4 14h7l-1 8 10-13h-7l1-7z"/></svg>',
  train: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14"/><circle cx="9" cy="14.5" r="1.2"/><circle cx="15" cy="14.5" r="1.2"/><path d="M9 17l-2 3"/><path d="M15 17l2 3"/><path d="M8 3V2"/><path d="M16 3V2"/><path d="M10 7h4"/></svg>',
  check: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 13l4 4L19 7"/></svg>',
  x: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12"/><path d="M18 6L6 18"/></svg>',
};

function ico(name, cls = '') {
  const svg = ICONS[name] || '';
  return cls ? svg.replace('class="ico"', `class="ico ${cls}"`) : svg;
}

/** Switch main match tab */
function switchMatchTab(btn, tabId) {
  const root = btn.closest('.match-tabs-root') || document;
  root.querySelectorAll('.match-tab').forEach(b => b.classList.toggle('active', b === btn));
  root.querySelectorAll('.match-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === tabId));
}

/** Switch sub-tab inside a match panel */
function switchSubTab(btn, subId) {
  const panel = btn.closest('.match-tab-panel') || document;
  panel.querySelectorAll('.sub-tab').forEach(b => b.classList.toggle('active', b === btn));
  panel.querySelectorAll('.sub-panel').forEach(p => p.classList.toggle('active', p.dataset.sub === subId));
}

function greeting() {
  const h = new Date().getHours();
  if (h >= 6 && h < 12) return 'Доброе утро';
  if (h >= 12 && h < 18) return 'Привет';
  if (h >= 18 && h < 23) return 'Вечер добрый';
  return 'Доброй ночи';
}

function avatarHtml(url, name, sizeClass = 'avatar') {
  if (url) {
    return `<img class="${sizeClass}" src="${escapeHtml(url)}" alt="" onerror="this.style.display='none'" />`;
  }
  const letter = (name || '?')[0].toUpperCase();
  return `<div class="${sizeClass}-placeholder">${letter}</div>`;
}

/** Бейдж тарифа в шапке (ближе к аватару) */
function updateHeaderUser(u) {
  if (!u) return;
  const hu = document.getElementById('header-user');
  if (!hu) return;
  const isPrem = u.subscription === 'Premium';
  const badge = isPrem
    ? `<span class="header-tariff header-tariff-premium">Premium</span>`
    : `<span class="header-tariff header-tariff-free">Free</span>`;
  hu.innerHTML = `${badge}${avatarHtml(u.photo_url, u.first_name, 'avatar')}`;
}

function logosHtml(home, away) {
  let h = '';
  if (home) h += `<img class="team-logo" src="${escapeHtml(home)}" alt="" onerror="this.style.display='none'" />`;
  if (away) h += `<img class="team-logo" src="${escapeHtml(away)}" alt="" onerror="this.style.display='none'" />`;
  return h ? `<div class="team-logos">${h}</div>` : '';
}

function navigate(page, data = null, push = true) {
  if (push && currentPage !== page) {
    pageHistory.push({ page: currentPage, data: lastPageData });
  }
  lastPageData = data;
  currentPage = page;
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.page === page);
  });
  render(page, data);
  window.scrollTo(0, 0); const c = document.getElementById('content');
  if (c) c.scrollTop = 0;
}

function goBack() {
  if (pageHistory.length) {
    const prev = pageHistory.pop();
    currentPage = prev.page;
    lastPageData = prev.data;
    document.querySelectorAll('.nav-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.page === prev.page);
    });
    render(prev.page, prev.data);
  } else {
    navigate('home', null, false);
  }
}

function bindNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.onclick = () => {
      pageHistory = [];
      navigate(btn.dataset.page, null, false);
    };
  });
}

const content = document.getElementById('content');

async function render(page, data) {
  // Не блокируем UI «Загрузка…», если данные уже в кэше
  const hasCache =
    (page === 'home' && _apiCache.has('/me')) ||
    (page === 'matches' && _apiCache.has('/matches')) ||
    (page === 'impulse') ||
    (page === 'account' && currentUser);
  if (!hasCache) {
    content.innerHTML = `<div class="empty-state"><div class="icon">${ico('clock')}</div><p>Загрузка...</p></div>`;
  }
  try {
    switch (page) {
      case 'home': await renderHome(); break;
      case 'matches': await renderMatches(); break;
      case 'match-group': await renderMatchGroup(data); break;
      case 'match-detail': await renderMatchDetail(data); break;
      case 'best-matches': await renderBestMatches(); break;
      case 'impulse': await renderImpulsePage(); break;
      case 'express': await renderImpulsePage(); break; // legacy alias
      case 'account': await renderAccount(); break;
      case 'favorites': await renderFavorites(); break;
      case 'premium': await renderPremium(); break;
      case 'ref': await renderRef(); break;
      case 'admin': await renderAdmin(); break;
      case 'admin-impulse-report': await renderAdminImpulseReport(); break;
      case 'admin-impulse-clear': await renderAdminImpulseClear(); break;
      case 'admin-auto': await renderAdminAuto(); break;
      case 'admin-ban': await renderAdminBan(data); break;
      case 'admin-parser': await renderAdminParser(); break;
      case 'admin-parser-leagues': await renderAdminParserLeagues(data); break;
      case 'admin-parser-matches': await renderAdminParserMatches(data); break;
      case 'admin-manual': await renderAdminManual(); break;
      case 'admin-texts': await renderAdminTexts(); break;
      case 'admin-text-edit': await renderAdminTextEdit(data); break;
      case 'admin-delete': await renderAdminDelete(); break;
      case 'admin-express': await renderAdminExpress(); break;
      case 'admin-express-add': await renderAdminExpressAdd(data); break;
      case 'admin-result-add': await renderAdminResultAdd(); break;
      case 'admin-results': await renderAdminResults(); break;
      case 'admin-premium': await renderAdminPremium(); break;
      case 'admin-logs': await renderAdminLogs(); break;
      case 'admin-support': await renderAdminSupport(); break;
      case 'admin-support-thread': await renderAdminSupportThread(data); break;
      case 'support': await renderSupport(); break;
      case 'support-form': await renderSupportForm(data); break;
      default: await renderHome();
    }
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="icon">${ico('warning')}</div><p>${escapeHtml(e.message)}</p>
      <button class="btn btn-secondary" onclick="navigate('home',null,false)" style="margin-top:16px">На главную</button></div>`;
  }
}


function startHomeAiAnim() {
  document.body.classList.add('ai-anim-on');
  if (window._aiFloatTimer) clearInterval(window._aiFloatTimer);
  const left = ['xG Σ', '2.5×', 'edge', 'ROI', '1.85'];
  const right = ['62%', '77%', '58%', '71%', '64%'];
  const bot = ['P ≥ 0.62', 'ТБ 2.5', 'ОЗ', 'score ↑', 'ИТБ 1.5'];
  let i = 0;
  window._aiFloatTimer = setInterval(() => {
    i = (i + 1) % left.length;
    const a = document.getElementById('ai-float-left');
    const b = document.getElementById('ai-float-right');
    const c = document.getElementById('ai-float-bottom');
    if (a) a.textContent = left[i];
    if (b) b.textContent = right[i];
    if (c) c.textContent = bot[i];
  }, 2200);
}

/* ---------- HOME ---------- */
function _impulseSignalRow(s, locked) {
  const mid = s.match_id != null ? s.match_id : 0;
  const val = Math.round(Number(s.value) || 0);
  const minLabel = (Number(s.minute) > 10) ? `${Number(s.minute)}'` : '—';
  const score = (s.score && String(s.score).trim()) ? String(s.score).trim() : '—';
  const onClick = locked
    ? `onclick="toast('🔒 Для перехода в матч нужен Premium')"`
    : `onclick="navigate('match-detail', { id: ${mid}, tab: 'impulse' })"`;
  const leagueLogo = s.league_logo
    ? `<img class="imp-league-logo" src="${escapeHtml(s.league_logo)}" alt="" onerror="this.style.display='none'" />`
    : '';
  return `
    <button type="button" class="imp-row" ${onClick}>
      <div class="imp-top">
        <span class="imp-min" title="Минута сигнала">${minLabel}</span>
        <span class="imp-score-pill" title="Счёт">${escapeHtml(score)}</span>
        <span class="imp-flag">${s.flag || ''}</span>
        ${leagueLogo}
        <span class="imp-cl">${escapeHtml(s.country || '')} · ${escapeHtml(s.league || '')}</span>
        <span class="imp-val" title="Импульс">${val}</span>
      </div>
      <div class="imp-match">
        <div class="imp-team-logos">
          ${s.logo_home ? `<img class="imp-team-logo" src="${escapeHtml(s.logo_home)}" alt="" onerror="this.style.display='none'" />` : ''}
          ${s.logo_away ? `<img class="imp-team-logo" src="${escapeHtml(s.logo_away)}" alt="" onerror="this.style.display='none'" />` : ''}
        </div>
        <span class="imp-teams">${escapeHtml(s.match_name || '')}</span>
        <span class="imp-team-tag" title="Команда с импульсом">${escapeHtml(s.team || '')}</span>
        <span class="bl-chevron" aria-hidden="true">›</span>
      </div>
      <div class="imp-bar-wrap"><div class="imp-bar" style="width:${Math.min(100, val)}%"></div></div>
    </button>`;
}

async function renderHome() {
  const prevBonus = currentUser && currentUser.bonus;
  const [meRaw, bestLeagues, impulseRecent] = await Promise.all([
    api('/me'),
    api('/best-leagues').catch(() => null),
    api('/impulse/recent?limit=3').catch(() => null),
  ]);
  const me = meRaw;
  if (prevBonus && !me.bonus) me.bonus = prevBonus;
  currentUser = me;
  if (me.banned || (me.user && me.user.banned)) {
    renderBannedScreen(me.ban_label || (me.user && me.user.ban_label) || 'навсегда');
    return;
  }
  if (me.maintenance) {
    renderMaintenanceScreen();
    return;
  }
  const u = me.user;

  let bonusHtml = '';
  if (me.bonus) {
    bonusHtml = `<div class="bonus-banner">
      <h3>${escapeHtml(me.bonus.text)}</h3>
      <p style="color:var(--text-secondary);font-size:13px;margin-top:6px">${ico('calendar')} Premium до: ${escapeHtml(me.bonus.end_date || '')}</p>
    </div>`;
    try {
      api('/bonus/seen', { method: 'POST', body: JSON.stringify({}) }).catch(() => {});
      me.bonus = null;
      currentUser = me;
    } catch (_) {}
  }

  // Блок «Матч дня» — только топ-3, без кнопки «лучшие 10»
  let bestLeagueHtml = '';
  const bestList = (bestLeagues && (bestLeagues.matches || bestLeagues.leagues))
    ? (bestLeagues.matches || bestLeagues.leagues)
    : [];
  const isPremiumUser = u.subscription === 'Premium';
  if (bestList.length) {
    const visible = bestList.slice(0, 3);
    const makeRow = (L, locked) => {
      const m = L.match || {};
      const rankCls = L.rank === 1 ? 'bl-rank-1'
        : (L.rank === 2 ? 'bl-rank-2'
        : (L.rank === 3 ? 'bl-rank-3'
        : (L.rank === 4 ? 'bl-rank-4' : 'bl-rank-5')));
      const leagueLogo = L.league_logo
        ? `<img class="league-logo" src="${escapeHtml(L.league_logo)}" alt="" onerror="this.style.display='none'" />`
        : '';
      const time = m.match_time ? escapeHtml(m.match_time) : '—';
      const mid = m.id != null ? m.id : 0;
      const onClick = locked
        ? `onclick="toast('🔒 Для быстрого перехода в матчи нужен Premium')"`
        : `onclick="navigate('match-detail', ${mid})"`;
      return `
        <button type="button" class="bl-row bl-row-btn" ${onClick}>
          <div class="bl-league-line">
            <span class="bl-rank ${rankCls}">${L.rank}</span>
            <span class="bl-flag">${L.flag || ''}</span>
            <span class="bl-country">${escapeHtml(L.country || '')}</span>
            ${leagueLogo}
            <span class="bl-league-name">${escapeHtml(L.league || '')}</span>
          </div>
          <div class="bl-match-line">
            <span class="bl-time">${time}</span>
            ${logosHtml(m.logo_home, m.logo_away)}
            <span class="bl-teams">${escapeHtml(m.name || '')}</span>
            <span class="bl-chevron" aria-hidden="true">›</span>
          </div>
        </button>`;
    };
    const rows = visible.map(L => makeRow(L, !isPremiumUser)).join('');
    window._bestListFull = bestList;
    bestLeagueHtml = `
      <div class="section-title">${ico('trophy', 'ico-white')} Матч дня</div>
      <div class="card bl-card" id="best-matches-card">
        <div class="bl-subtitle">Матчи с более высоким процентом статистики</div>
        <div id="best-matches-rows">${rows}</div>
      </div>`;
  }

  // Импульс дня на главной — только 3 сигнала; Free видит, клик как у Матч дня
  const impSignals = ((impulseRecent && impulseRecent.signals) ? impulseRecent.signals : []).slice(0, 3);
  const impThr = (impulseRecent && impulseRecent.threshold) != null ? Number(impulseRecent.threshold) : 65;
  let impBody;
  if (impSignals.length) {
    impBody = impSignals.map(s => _impulseSignalRow(s, !isPremiumUser)).join('');
  } else {
    impBody = `<div class="imp-hist-empty">Пока нет сигналов ≥ ${impThr}</div>`;
  }
  const impulseHtml = `
      <div class="section-title">${ico('impulse', 'ico-white')} Импульс дня</div>
      <div class="card bl-card imp-card">
        <div class="bl-subtitle">Матчи с наиболее опасными голевыми моментами</div>
        <div class="imp-rows">${impBody}</div>
      </div>`;

  content.innerHTML = `
    <div class="welcome-banner welcome-banner-compact">
      <div class="welcome-title">${greeting()}, ${escapeHtml(u.first_name)}!</div>
      <div class="welcome-helper">
        <img class="welcome-logo-img" src="/static/assets/koef-logo.png" width="96" height="96" alt="" />
        <div class="welcome-helper-text">
          <div class="welcome-helper-sub">Статистика, анализ и наиболее вероятные исходы на события.</div>
          <div class="welcome-helper-sub welcome-impulse-hint">Импульс — инструмент который уведомляет о самых опасных голевых моментах матча.</div>
        </div>
      </div>
    </div>
    ${bonusHtml}
    ${bestLeagueHtml}
    ${impulseHtml}`;
}

/** Топ-10 матчей дня — только Premium (из Аккаунта) */
async function renderBestMatches() {
  const me = currentUser || await api('/me').catch(() => null);
  const isPrem = me && me.user && me.user.subscription === 'Premium';
  if (!isPrem) {
    content.innerHTML = `<button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="express-card"><div class="lock-overlay">
        <div class="icon">${ico('lock')}</div>
        <h3 style="margin-bottom:8px">${ico('trophy', 'ico-white')} Матч дня (топ-10)</h3>
        <p style="color:var(--text-secondary);margin-bottom:16px">Доступно только по Premium</p>
        <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
      </div></div>`;
    return;
  }
  let list = window._bestListFull || [];
  if (!list.length) {
    try {
      const bl = await api('/best-leagues');
      list = (bl && (bl.matches || bl.leagues)) || [];
      window._bestListFull = list;
    } catch (_) {}
  }
  const makeRow = (L) => {
    const m = L.match || {};
    const rankCls = L.rank === 1 ? 'bl-rank-1'
      : (L.rank === 2 ? 'bl-rank-2'
      : (L.rank === 3 ? 'bl-rank-3'
      : (L.rank === 4 ? 'bl-rank-4' : 'bl-rank-5')));
    const leagueLogo = L.league_logo
      ? `<img class="league-logo" src="${escapeHtml(L.league_logo)}" alt="" onerror="this.style.display='none'" />`
      : '';
    const time = m.match_time ? escapeHtml(m.match_time) : '—';
    const mid = m.id != null ? m.id : 0;
    return `
      <button type="button" class="bl-row bl-row-btn" onclick="navigate('match-detail', ${mid})">
        <div class="bl-league-line">
          <span class="bl-rank ${rankCls}">${L.rank}</span>
          <span class="bl-flag">${L.flag || ''}</span>
          <span class="bl-country">${escapeHtml(L.country || '')}</span>
          ${leagueLogo}
          <span class="bl-league-name">${escapeHtml(L.league || '')}</span>
        </div>
        <div class="bl-match-line">
          <span class="bl-time">${time}</span>
          ${logosHtml(m.logo_home, m.logo_away)}
          <span class="bl-teams">${escapeHtml(m.name || '')}</span>
          <span class="bl-chevron" aria-hidden="true">›</span>
        </div>
      </button>`;
  };
  if (!list.length) {
    content.innerHTML = `
      <button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="empty-state"><div class="icon">${ico('trophy')}</div>
        <p>Список лучших матчей пока пуст.</p></div>`;
    return;
  }
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="section-title">${ico('trophy', 'ico-white')} Матч дня · топ-10</div>
    <div class="card bl-card">
      <div class="bl-subtitle">Матчи с более высоким процентом статистики</div>
      <div id="best-matches-rows">${list.slice(0, 10).map(makeRow).join('')}</div>
    </div>`;
}

/* ---------- MATCHES ---------- */
async function renderMatches(page = 0) {
  const data = await api('/matches');
  if (!data.groups.length) {
    content.innerHTML = `<div class="empty-state"><div class="icon">${ico('ball')}</div><p>Матчей пока нет</p></div>`;
    return;
  }
  const PER_PAGE = 7;
  const totalPages = Math.max(1, Math.ceil(data.groups.length / PER_PAGE));
  page = Math.max(0, Math.min(page, totalPages - 1));
  const slice = data.groups.slice(page * PER_PAGE, (page + 1) * PER_PAGE);
  let html = `<div class="section-title">${ico('field', 'ico-white')} Выберите (страна — лига)</div>`;
  slice.forEach((g, idx) => {
    const realIdx = page * PER_PAGE + idx;
    const leagueLogo = g.league_logo
      ? `<img class="league-logo" src="${escapeHtml(g.league_logo)}" alt="" onerror="this.style.display='none'" />`
      : '';
    html += `
      <div class="group-card" data-gidx="${realIdx}">
        <div class="group-left">
          <span class="group-flag">${g.flag || '🏳️'}</span>
          <div class="group-info">
            <div class="country-league">
              <span class="cl-country">${escapeHtml(g.country)}</span>
              <span class="cl-sep">·</span>
              ${leagueLogo}
              <span class="cl-league">${escapeHtml(g.league)}</span>
            </div>
          </div>
        </div>
        <span class="group-count">${g.count}</span>
      </div>`;
  });
  if (totalPages > 1) {
    html += `<div class="pagination">
      <button class="page-btn" id="matches-prev" ${page === 0 ? 'disabled' : ''}>‹</button>
      <span class="page-info">${page + 1} / ${totalPages}</span>
      <button class="page-btn" id="matches-next" ${page >= totalPages - 1 ? 'disabled' : ''}>›</button>
    </div>`;
  }
  content.innerHTML = html;
  content.querySelectorAll('.group-card').forEach(el => {
    el.onclick = () => {
      const g = data.groups[parseInt(el.dataset.gidx)];
      if (g) navigate('match-group', g);
    };
  });
  const prev = document.getElementById('matches-prev');
  const next = document.getElementById('matches-next');
  if (prev) prev.onclick = () => renderMatches(page - 1);
  if (next) next.onclick = () => renderMatches(page + 1);
}

async function renderMatchGroup(group) {
  if (!group || !group.matches) {
    content.innerHTML = `<button class="btn-back" onclick="navigate('matches',null,false)">← Назад</button>
      <div class="empty-state"><p>Группа не найдена</p></div>`;
    return;
  }
  const groupLogo = group.league_logo
    ? `<img class="league-logo league-logo-title" src="${escapeHtml(group.league_logo)}" alt="" onerror="this.style.display='none'" />`
    : '';
  let html = `
    <button class="btn-back" onclick="navigate('matches',null,false)">← Назад</button>
    <div class="card-title">${group.flag || ''} ${escapeHtml(group.country || '')} · ${groupLogo}${escapeHtml(group.league || '')}</div>`;
  group.matches.forEach(m => {
    const time = m.match_time
      ? `<span class="match-time">${escapeHtml(m.match_time)}</span>`
      : '<span class="match-time">—</span>';
    html += `
      <div class="match-item" onclick="navigate('match-detail', ${m.id})">
        ${time}
        ${logosHtml(m.logo_home, m.logo_away)}
        <span class="match-name">${escapeHtml(m.name)}</span>
      </div>`;
  });
  content.innerHTML = html;
}

function personChip(p) {
  if (!p || !p.name) return '';
  const img = p.photo
    ? `<img class="person-photo" src="${escapeHtml(p.photo)}" alt="" onerror="this.style.display='none'" />`
    : `<span class="person-photo person-photo-fallback">👤</span>`;
  return `<div class="person-chip">${img}<span>${escapeHtml(p.name)}</span></div>`;
}

function formBadge(formStr) {
  if (!formStr) return '';
  return String(formStr).slice(0, 5).split('').map(c => {
    const cls = c === 'W' ? 'form-w' : (c === 'D' ? 'form-d' : 'form-l');
    const label = c === 'W' ? 'В' : (c === 'D' ? 'Н' : 'П');
    return `<span class="form-pill ${cls}">${label}</span>`;
  }).join('');
}

/** Умная подсказка: мало игр в сезоне (≤4) → статистика нестабильна */
function sampleWarningHtml(...counts) {
  const low = counts.some(n => {
    const v = Number(n);
    return !isNaN(v) && v > 0 && v <= 4;
  });
  if (!low) return '';
  return `<div class="stats-sample-warn">${ico('warning')} Мало игр в сезоне · статистика нестабильна</div>`;
}

function renderStatsBlock(m) {
  const s = m.stats || {};
  const hasHalves = s.halves && (
    ((s.halves.home || {}).first_half || {}).matches ||
    ((s.halves.home || {}).second_half || {}).matches ||
    ((s.halves.away || {}).first_half || {}).matches ||
    ((s.halves.away || {}).second_half || {}).matches
  );
  const hasGoalSeg = (() => {
    const gs = s.goal_segments || {};
    for (const side of ['home', 'away']) {
      const g = gs[side] || {};
      if (g.matches) return true;
      const sc = g.scored || {};
      const cd = g.conceded || {};
      if (Object.values(sc).some(v => v > 0) || Object.values(cd).some(v => v > 0)) return true;
    }
    return false;
  })();

  const teams = (m.name || '').split(/—|–|-/).map(x => x.trim()).filter(Boolean);
  const homeName = teams[0] || 'Хозяева';
  const awayName = teams[1] || 'Гости';

  const panels = {
    squad: '', injuries: '', standings: '', match: '', halves: '', goals: '', form: '', h2h: '', edge: '', potential: ''
  };

  // --- Coaches + Lineups (squad panel) ---
  const ch = (s.coaches || {}).home || {};
  const ca = (s.coaches || {}).away || {};
  const lu = s.lineups || {};
  const luHome = lu.home || {};
  const luAway = lu.away || {};
  const luHomePl = luHome.players || [];
  const luAwayPl = luAway.players || [];

  let squadInner = '';
  if (ch.name || ca.name) {
    squadInner += `<div class="coaches-inline">
      <div class="coach-side"><div class="side-label">${escapeHtml(homeName)}</div>${personChip(ch) || '<span class="muted">—</span>'}</div>
      <div class="coach-side"><div class="side-label">${escapeHtml(awayName)}</div>${personChip(ca) || '<span class="muted">—</span>'}</div>
    </div>`;
  }
  if (luHomePl.length || luAwayPl.length) {
    const statusLabel = lu.status === 'confirmed' ? 'Официальный' : (lu.status === 'predicted' ? 'Возможный' : '');
    const sideLineup = (side, label) => {
      const pl = side.players || [];
      if (!pl.length) return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div><span class="muted">Нет данных</span></div>`;
      const form = side.formation
        ? `<div class="lineup-formation-row">${ico('field', 'ico-white')} <span class="lineup-formation">${escapeHtml(side.formation)}</span></div>`
        : '';
      const rows = pl.map(p => {
        const num = p.number != null ? `<span class="lineup-num">${p.number}</span>` : '';
        const pos = p.position ? `<span class="lineup-pos">${escapeHtml(p.position)}</span>` : '';
        const photo = p.photo
          ? `<img class="person-photo lineup-photo" src="${escapeHtml(p.photo)}" alt="" onerror="this.style.display='none'" />`
          : `<span class="person-photo person-photo-fallback lineup-photo"></span>`;
        return `<div class="lineup-row">${photo}${num}<span class="lineup-name">${escapeHtml(p.name || '')}</span>${pos}</div>`;
      }).join('');
      return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div>${form}${rows}</div>`;
    };
    squadInner += `${statusLabel ? `<div class="lineup-status-badge" style="color:#fff">${escapeHtml(statusLabel)}</div>` : ''}
      <div class="form-sides">
        ${sideLineup(luHome, homeName)}
        ${sideLineup(luAway, awayName)}
      </div>`;
  }
  if (squadInner) {
    panels.squad = `<div class="card stats-card">
      <div class="card-title">${ico('squad', 'ico-white')} Состав${(ch.name || ca.name) ? ` <span class="card-title-sub">${ico('coach', 'ico-white')} тренеры</span>` : ''}</div>
      ${squadInner}
    </div>`;
  }

  // --- Injuries ---
  const ih = ((s.injuries || {}).home || []);
  const ia = ((s.injuries || {}).away || []);
  if (ih.length || ia.length) {
    const injList = (arr) => arr.length
      ? arr.map(p => {
          const pos = p.position ? `<span class="lineup-pos">${escapeHtml(p.position)}</span>` : '';
          const photo = p.photo
            ? `<img class="person-photo lineup-photo" src="${escapeHtml(p.photo)}" alt="" onerror="this.style.display='none'" />`
            : `<span class="person-photo person-photo-fallback lineup-photo"></span>`;
          return `<div class="lineup-row">${photo}<span class="lineup-name">${escapeHtml(p.name || '')}</span>${pos}</div>`;
        }).join('')
      : '<span class="muted">Нет данных</span>';
    panels.injuries = `<div class="card stats-card">
      <div class="card-title">${ico('medical', 'ico-white')} Травмы / дисквалификации</div>
      <div class="form-sides">
        <div class="form-side"><div class="side-label">${escapeHtml(homeName)}</div>${injList(ih)}</div>
        <div class="form-side"><div class="side-label">${escapeHtml(awayName)}</div>${injList(ia)}</div>
      </div>
    </div>`;
  }

  // --- Standings ---
  const st = s.standings || {};
  const table = st.table || [];
  if (table.length) {
    const season = st.season ? ` · ${escapeHtml(String(st.season))}` : '';
    let rows = table.map(r => {
      const hl = r.highlight ? ' standings-hl' : '';
      return `<tr class="${hl}">
        <td class="st-pos">${r.pos ?? ''}</td>
        <td class="st-team">${escapeHtml(r.team || '')}</td>
        <td>${r.played ?? ''}</td>
        <td>${r.won ?? ''}</td>
        <td>${r.drawn ?? ''}</td>
        <td>${r.lost ?? ''}</td>
        <td>${r.gf ?? ''}:${r.ga ?? ''}</td>
        <td class="st-pts">${r.pts ?? ''}</td>
      </tr>`;
    }).join('');
    panels.standings = `<div class="card stats-card"><div class="card-title">${ico('table', 'ico-white')} Таблица${season}</div>
      <div class="standings-wrap">
        <table class="standings-table">
          <thead><tr>
            <th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th><th>П</th><th>Мячи</th><th>О</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div></div>`;
  }

  // --- Match averages ---
  const msH = (s.match_stats || {}).home || {};
  const msA = (s.match_stats || {}).away || {};
  const fmtN = (v) => v != null && !isNaN(Number(v)) ? Number(v).toFixed(1) : '—';
  const STAT_KEYS = [
    { key: 'goals', label: 'Голы', max: 3.5, color: '76, 175, 80' },
    { key: 'corners', label: 'Угловые', max: 10, color: '66, 165, 245' },
    { key: 'fouls', label: 'Фолы', max: 18, color: '255, 167, 38' },
    { key: 'yellow', label: 'Жёлтые', max: 4, color: '255, 213, 79' },
    { key: 'shots', label: 'Удары', max: 18, color: '171, 71, 188' },
    { key: 'shots_on', label: 'В створ', max: 8, color: '38, 198, 218' },
  ];
  const meterRow = (label, val, max, color) => {
    const n = (val != null && !isNaN(Number(val))) ? Number(val) : null;
    const pct = n == null ? 0 : Math.min(100, Math.round((n / max) * 100));
    return `<div class="meter-row">
      <div class="meter-top"><span class="meter-k">${label}</span><span class="meter-v">${fmtN(val)}</span></div>
      <div class="meter-track"><div class="meter-fill" style="width:${pct}%;background:rgba(${color},0.85)"></div></div>
    </div>`;
  };
  const statRows = (mm) => {
    if (!mm) return '';
    return `<div class="meter-list">${STAT_KEYS.map(it => meterRow(it.label, mm[it.key], it.max, it.color)).join('')}</div>`;
  };
  if ((Number(msH.matches) > 0) || (Number(msA.matches) > 0)) {
    const matchSide = (mm, label) => {
      if (!mm || !Number(mm.matches)) {
        return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div><span class="muted">Нет данных</span></div>`;
      }
      return `<div class="form-side meter-card">
        <div class="side-label">${escapeHtml(label)}</div>
        ${statRows(mm)}
      </div>`;
    };
    panels.match = `<div class="card stats-card"><div class="card-title">${ico('ball', 'ico-white')} Матч</div>
      <div class="gs-hint">Средние · за сезон</div>
      <div class="form-sides form-sides-split">
        ${matchSide(msH, homeName)}
        ${matchSide(msA, awayName)}
      </div></div>`;
  }

  // --- Halves ---
  const halvesH = (s.halves || {}).home || {};
  const halvesA = (s.halves || {}).away || {};
  if (hasHalves) {
    const halfBlock = (half, title) => {
      if (!half || !Number(half.matches)) return '';
      return `<div class="half-block">
        <div class="half-title">${title}</div>
        ${statRows(half)}
      </div>`;
    };
    const sideHalves = (h, label) => {
      const fh = h.first_half || {};
      const sh = h.second_half || {};
      if (!Number(fh.matches) && !Number(sh.matches)) {
        return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div><span class="muted">Нет данных</span></div>`;
      }
      return `<div class="form-side meter-card">
        <div class="side-label">${escapeHtml(label)}</div>
        ${halfBlock(fh, '1-й тайм')}
        ${halfBlock(sh, '2-й тайм')}
      </div>`;
    };
    const halfMatches = (h) => Math.max(
      Number((h.first_half || {}).matches) || 0,
      Number((h.second_half || {}).matches) || 0
    );
    panels.halves = `<div class="card stats-card"><div class="card-title">${ico('half', 'ico-white')} Таймы</div>
      <div class="gs-hint">Средние по таймам · за сезон</div>
      <div class="form-sides form-sides-split">
        ${sideHalves(halvesH, homeName)}
        ${sideHalves(halvesA, awayName)}
      </div></div>`;
  }

  // --- Goal segments ---
  const gsH = (s.goal_segments || {}).home || {};
  const gsA = (s.goal_segments || {}).away || {};
  const defaultIntervals = ['0-18', '19-36', '37-54', '55-72', '73-90'];
  const intervals = (gsH.intervals || gsA.intervals || defaultIntervals);
  const hasGsData = (g) => {
    if (!g || typeof g !== 'object') return false;
    if (Number(g.matches) > 0) return true;
    const sc = g.scored || {};
    const cd = g.conceded || {};
    return Object.keys(sc).length > 0 || Object.keys(cd).length > 0 ||
      Object.values(sc).some(v => Number(v) > 0) || Object.values(cd).some(v => Number(v) > 0);
  };
  if (hasGsData(gsH) || hasGsData(gsA)) {
    const maxVal = (g) => {
      const sc = g.scored || {};
      const cd = g.conceded || {};
      let mx = 1;
      intervals.forEach(lab => { mx = Math.max(mx, Number(sc[lab] || 0), Number(cd[lab] || 0)); });
      return mx;
    };
    const intensity = (val, max) => {
      const t = Math.min(1, Number(val || 0) / max);
      return 0.12 + t * 0.78;
    };
    const scaleRow = (map, max, colorRgb, withLabels) => {
      return `<div class="gs-scale">${intervals.map(lab => {
        const v = Number((map || {})[lab] || 0);
        const a = intensity(v, max);
        const labHtml = withLabels ? `<span class="gs-interval-lab">${lab}</span>` : '';
        return `<div class="gs-col" title="${lab}: ${v}">
          <div class="gs-cell" style="background:rgba(${colorRgb},${a})"><span class="gs-num">${v}</span></div>
          ${labHtml}
        </div>`;
      }).join('')}</div>`;
    };
    const sideGs = (g, label) => {
      const sc = g.scored || {};
      const cd = g.conceded || {};
      const mx = maxVal(g);
      return `<div class="gs-side">
        <div class="side-label">${escapeHtml(label)}</div>
        <div class="gs-section-label gs-scored">Забитые</div>
        ${scaleRow(sc, mx, '34,197,94', false)}
        <div class="gs-section-label gs-conceded">Пропущенные</div>
        ${scaleRow(cd, mx, '239,68,68', true)}
      </div>`;
    };
    panels.goals = `<div class="card stats-card"><div class="card-title">${ico('target', 'ico-white')} Интервал</div>
      <div class="gs-hint">Интервалы по 18 мин · за сезон</div>
      <div class="gs-sides">
        ${sideGs(gsH, homeName)}
        ${sideGs(gsA, awayName)}
      </div></div>`;
  }

  // --- Form: горизонтальные В/Н/П + 5 матчей (плашка ВНП + счёт + соперник) ---
  function last5Rows(list, teamLabel) {
    return (list || []).map(g => {
      const h = g.home || '';
      const a = g.away || '';
      const hl = (h || '').toLowerCase();
      const al = (a || '').toLowerCase();
      const t = (teamLabel || '').toLowerCase();
      let isHome = true;
      if (t) {
        const homeMatch = hl.includes(t) || t.includes(hl) || (t.split(/\s+/)[0] && hl.includes(t.split(/\s+/)[0]));
        const awayMatch = al.includes(t) || t.includes(al) || (t.split(/\s+/)[0] && al.includes(t.split(/\s+/)[0]));
        if (awayMatch && !homeMatch) isHome = false;
        else if (homeMatch) isHome = true;
      }
      const opponent = isHome ? a : h;
      let gf = null, ga = null;
      if (g.home_score != null && g.away_score != null) {
        gf = isHome ? Number(g.home_score) : Number(g.away_score);
        ga = isHome ? Number(g.away_score) : Number(g.home_score);
      }
      let scoreView = String(g.score || '—');
      if (gf != null && ga != null) {
        scoreView = `${gf}:${ga}`;
      } else {
        const parts = scoreView.split(/[-:]/);
        if (parts.length === 2 && !isHome) scoreView = parts[1].trim() + ':' + parts[0].trim();
        else if (parts.length === 2) scoreView = parts[0].trim() + ':' + parts[1].trim();
      }
      let resPill = '';
      if (gf != null && ga != null && !isNaN(gf) && !isNaN(ga)) {
        const letter = gf > ga ? 'В' : (gf === ga ? 'Н' : 'П');
        const cls = gf > ga ? 'form-w' : (gf === ga ? 'form-d' : 'form-l');
        resPill = `<span class="form-pill last5-pill ${cls}">${letter}</span>`;
      }
      return `<div class="last5-row">
        ${resPill}
        <span class="last5-score">${escapeHtml(scoreView)}</span>
        <span class="last5-opp">${escapeHtml(opponent || '—')}</span>
      </div>`;
    }).join('');
  }
  const fh = (s.form || {}).home || {};
  const fa = (s.form || {}).away || {};
  const l5h = ((s.last5 || {}).home || []).slice(0, 5);
  const l5a = ((s.last5 || {}).away || []).slice(0, 5);
  if (fh.form || fa.form || l5h.length || l5a.length) {
    panels.form = `<div class="card stats-card"><div class="card-title">${ico('shirt', 'ico-white')} Форма</div>
      <div class="form-sides">
        <div class="form-side">
          <div class="side-label">${escapeHtml(homeName)}</div>
          <div class="form-row">${formBadge(fh.form) || '—'}</div>
          <div class="last5-list">${last5Rows(l5h, homeName)}</div>
        </div>
        <div class="form-side">
          <div class="side-label">${escapeHtml(awayName)}</div>
          <div class="form-row">${formBadge(fa.form) || '—'}</div>
          <div class="last5-list">${last5Rows(l5a, awayName)}</div>
        </div>
      </div></div>`;
  }

  // --- Потенциал ---
  const fhPot = (s.form || {}).home || {};
  const faPot = (s.form || {}).away || {};
  const msHpot = (s.match_stats || {}).home || {};
  const msApot = (s.match_stats || {}).away || {};
  const hasPot = (
    fhPot.xg_avg != null || faPot.xg_avg != null ||
    fhPot.xga_avg != null || faPot.xga_avg != null ||
    fhPot.possession != null || faPot.possession != null ||
    fhPot.possession_final_third != null || faPot.possession_final_third != null ||
    fhPot.gpg != null || faPot.gpg != null ||
    fhPot.gapg != null || faPot.gapg != null ||
    Number(fhPot.matches_count) > 0 || Number(faPot.matches_count) > 0 ||
    Number(msHpot.matches) > 0 || Number(msApot.matches) > 0 ||
    fhPot.form || faPot.form ||
    (fhPot.home && (fhPot.home.gpg != null || fhPot.home.matches_count)) ||
    (faPot.home && (faPot.home.gpg != null || faPot.home.matches_count)) ||
    (fhPot.away && (fhPot.away.gpg != null || fhPot.away.matches_count)) ||
    (faPot.away && (faPot.away.gpg != null || faPot.away.matches_count))
  );
  if (hasPot) {
    const num = (v, d=1) => (v != null && !isNaN(Number(v))) ? Number(Number(v).toFixed(d)) : null;
    const pick = (f, m, key, fromMatch=false) => {
      if (fromMatch && m && m[key] != null) return num(m[key], key === 'xg_avg' || key === 'xga_avg' || key === 'gpg' || key === 'gapg' ? 2 : 1);
      if (f && f[key] != null) return num(f[key], ['xg_avg','xga_avg','gpg','gapg'].includes(key) ? 2 : (key === 'possession' || key === 'possession_final_third' ? 0 : 1));
      if (m && m[key] != null) return num(m[key], 1);
      return null;
    };
    const potRowsDef = [
      { key: 'xg_avg', label: 'xG', max: 2.5, color: '0, 230, 180', d: 2 },
      { key: 'xga_avg', label: 'xGA', max: 2.5, color: '244, 67, 54', d: 2, invert: true },
      { key: 'gpg', label: 'Голы заб.', max: 3, color: '76, 175, 80', d: 2 },
      { key: 'gapg', label: 'Голы проп.', max: 3, color: '239, 83, 80', d: 2, invert: true },
      { key: 'possession', label: 'Владение %', max: 70, color: '66, 165, 245', d: 0 },
      { key: 'possession_final_third', label: 'Опасные атаки', max: 45, color: '255, 152, 0', d: 0 },
      { key: 'shots', label: 'Удары', max: 18, color: '171, 71, 188', d: 1, match: true },
      { key: 'shots_on', label: 'Уд. в створ', max: 8, color: '38, 198, 218', d: 1, match: true },
      { key: 'corners', label: 'Угловые', max: 10, color: '100, 181, 246', d: 1, match: true },
      { key: 'fouls', label: 'Фолы', max: 18, color: '255, 183, 77', d: 1, match: true },
      { key: 'yellow', label: 'Жёлтые', max: 4, color: '255, 213, 79', d: 1, match: true },
    ];
    const meterList = (f, m) => {
      const rows = potRowsDef.map(it => {
        let val = pick(f, m, it.key, !!it.match);
        if (val == null && it.match) val = pick(f, m, it.key, false);
        if (val == null) return '';
        const n = Number(val);
        let pct = isNaN(n) ? 0 : Math.min(100, Math.round((n / it.max) * 100));
        if (it.invert) pct = Math.min(100, Math.max(0, 100 - pct));
        return `<div class="meter-row">
          <div class="meter-top"><span class="meter-k">${it.label}</span><span class="meter-v">${val}</span></div>
          <div class="meter-track"><div class="meter-fill" style="width:${pct}%;background:rgba(${it.color},0.85)"></div></div>
        </div>`;
      }).filter(Boolean).join('');
      return rows ? `<div class="meter-list">${rows}</div>` : '<span class="muted">Нет данных</span>';
    };
    const powerOf = (f, m) => {
      const xg = Number(f && f.xg_avg); const xga = Number(f && f.xga_avg);
      const pos = Number(f && f.possession); const sot = Number((m && m.shots_on) ?? (f && f.shots_on));
      const gpg = Number(f && f.gpg); const gapg = Number(f && f.gapg);
      const ft = Number(f && f.possession_final_third);
      let parts = [];
      if (!isNaN(xg)) parts.push(Math.min(100, xg / 2.2 * 100));
      if (!isNaN(xga)) parts.push(Math.min(100, Math.max(0, (2.2 - xga) / 2.2 * 100)));
      if (!isNaN(pos)) parts.push(Math.min(100, pos / 60 * 100));
      if (!isNaN(ft)) parts.push(Math.min(100, ft / 40 * 100));
      if (!isNaN(sot)) parts.push(Math.min(100, sot / 6 * 100));
      if (!isNaN(gpg)) parts.push(Math.min(100, gpg / 2.5 * 100));
      if (!isNaN(gapg)) parts.push(Math.min(100, Math.max(0, (2.5 - gapg) / 2.5 * 100)));
      return parts.length ? Math.round(parts.reduce((a,b)=>a+b,0)/parts.length) : null;
    };
    const cmp = (label, vh, va) => {
      const a = Number(vh), b = Number(va);
      if (isNaN(a) && isNaN(b)) return '';
      const sa = isNaN(a) ? 0 : a, sb = isNaN(b) ? 0 : b;
      const sum = Math.abs(sa) + Math.abs(sb) || 1;
      const lp = Math.round(Math.abs(sa) / sum * 100), rp = 100 - lp;
      return `<div class="pot-cmp">
        <div class="pot-cmp-lab">${label}</div>
        <div class="pot-cmp-bar"><span style="width:${lp}%"></span><span style="width:${rp}%"></span></div>
        <div class="pot-cmp-vals"><span>${isNaN(a)?'—':a}</span><span>${isNaN(b)?'—':b}</span></div>
      </div>`;
    };
    const sideBlock = (f, m, title) => {
      const power = powerOf(f || {}, m || {});
      return `<div class="form-side meter-card">
        <div class="side-label">${escapeHtml(title)}</div>
        ${power != null ? `<div class="pot-power">Потенциал <b>${power}</b>/100</div>` : ''}
        ${meterList(f || {}, m || {})}
      </div>`;
    };
    const section = (title, left, right) => `<div class="pot-section">
      <div class="pot-section-title">${title}</div>
      <div class="form-sides form-sides-split">${left}${right}</div>
    </div>`;

    const fhHome = fhPot.home || {};
    const fhAway = fhPot.away || {};
    const faHome = faPot.home || {};
    const faAway = faPot.away || {};

    panels.potential = `<div class="card stats-card"><div class="card-title">${ico('chart', 'ico-white')} Потенциал</div>
      <div class="gs-hint">Сила команд · сезон лиги</div>
      <div class="pot-compare">
        ${cmp('xG', num(fhPot.xg_avg,2), num(faPot.xg_avg,2))}
        ${cmp('xGA', num(fhPot.xga_avg,2), num(faPot.xga_avg,2))}
        ${cmp('Голы заб.', num(fhPot.gpg,2), num(faPot.gpg,2))}
        ${cmp('Голы проп.', num(fhPot.gapg,2), num(faPot.gapg,2))}
        ${cmp('Владение %', num(fhPot.possession,0), num(faPot.possession,0))}
        ${cmp('Опасные атаки', num(fhPot.possession_final_third,0), num(faPot.possession_final_third,0))}
        ${cmp('Уд. в створ', num(msHpot.shots_on ?? fhPot.shots_on,1), num(msApot.shots_on ?? faPot.shots_on,1))}
        ${cmp('Удары', num(msHpot.shots ?? fhPot.shots,1), num(msApot.shots ?? faPot.shots,1))}
        ${cmp('Угловые', num(msHpot.corners ?? fhPot.corners,1), num(msApot.corners ?? faPot.corners,1))}
      </div>
      ${section('Общая', sideBlock(fhPot, msHpot, homeName), sideBlock(faPot, msApot, awayName))}
      ${section('Дома', sideBlock(fhHome, fhHome, homeName + ' · дом'), sideBlock(faHome, faHome, awayName + ' · дом'))}
      ${section('Выезд', sideBlock(fhAway, fhAway, homeName + ' · выезд'), sideBlock(faAway, faAway, awayName + ' · выезд'))}
    </div>`;
  }

  // --- Тренды (бывший Edge) ---
  const betH = ((s.betting || {}).home) || {};
  const betA = ((s.betting || {}).away) || {};
  const hasBet = (betH.matches || betA.matches);
  if (hasBet) {
    const meter = (label, valH, valA, unit = '%') => {
      if (valH == null && valA == null) return '';
      const fmt = (v) => {
        if (v == null) return '—';
        if (unit === '%') return `${Number(v).toFixed(0)}%`;
        if (unit === 'int') return String(Math.round(Number(v)));
        return Number(v).toFixed(2);
      };
      return `<div class="edge-row">
        <span class="edge-val">${fmt(valH)}</span>
        <span class="edge-lab">${escapeHtml(label)}</span>
        <span class="edge-val">${fmt(valA)}</span>
      </div>`;
    };
    const sampleNote = `<div class="gs-hint">За сезон</div>`;
    const msH = (s.match_stats || {}).home || {};
    const msA = (s.match_stats || {}).away || {};
    const htH = betH.ht || {};
    const htA = betA.ht || {};

    // Пик голов: отрезок + забитые/пропущенные цифрами
    const gsH = (s.goal_segments || {}).home || {};
    const gsA = (s.goal_segments || {}).away || {};
    const intervals = gsH.intervals || gsA.intervals || ['0-18', '19-36', '37-54', '55-72', '73-90'];
    let peakLab = null, peakVal = 0;
    let peakHsc = 0, peakHcd = 0, peakAsc = 0, peakAcd = 0;
    intervals.forEach(lab => {
      const hSc = Number((gsH.scored || {})[lab] || 0);
      const hCd = Number((gsH.conceded || {})[lab] || 0);
      const aSc = Number((gsA.scored || {})[lab] || 0);
      const aCd = Number((gsA.conceded || {})[lab] || 0);
      const sum = hSc + hCd + aSc + aCd;
      if (sum > peakVal) {
        peakVal = sum;
        peakLab = lab;
        peakHsc = hSc; peakHcd = hCd;
        peakAsc = aSc; peakAcd = aCd;
      }
    });
    let peakRow = '';
    if (peakLab && peakVal > 0) {
      peakRow = `<div class="edge-row peak-row">
        <span class="edge-val peak-side"><span class="peak-scored">${peakHsc}</span><span class="peak-sep"></span><span class="peak-conceded">${peakHcd}</span></span>
        <span class="edge-lab">Пик ${escapeHtml(peakLab)} мин.</span>
        <span class="edge-val peak-side"><span class="peak-scored">${peakAsc}</span><span class="peak-sep"></span><span class="peak-conceded">${peakAcd}</span></span>
      </div>
      <div class="gs-hint"><span class="peak-scored">зелёный</span> — забитые · <span class="peak-conceded">красный</span> — пропущенные</div>`;
    }

    panels.edge = `<div class="card stats-card"><div class="card-title">${ico('trend', 'ico-white')} Тренды</div>
      ${sampleNote}
      <div class="edge-header"><span>${escapeHtml(homeName)}</span><span></span><span>${escapeHtml(awayName)}</span></div>
      ${meter('ТБ 2.5', betH.over_25_pct, betA.over_25_pct)}
      ${meter('ТБ 1.5', betH.over_15_pct, betA.over_15_pct)}
      ${meter('ТБ 3.5', betH.over_35_pct, betA.over_35_pct)}
      ${meter('ОЗ', betH.btts_pct, betA.btts_pct)}
      ${(() => {
        // ИТБ всегда показываем (даже если ещё нет в кэше — «—»)
        const vH = betH.team_over_15_pct, vA = betA.team_over_15_pct;
        const fmt = (v) => v == null ? '—' : `${Number(v).toFixed(0)}%`;
        return `<div class="edge-row">
          <span class="edge-val">${fmt(vH)}</span>
          <span class="edge-lab">ИТБ 1.5</span>
          <span class="edge-val">${fmt(vA)}</span>
        </div>`;
      })()}
      ${meter('Сухие', betH.clean_sheet_pct, betA.clean_sheet_pct)}
      ${meter('Не забивали', betH.failed_to_score_pct, betA.failed_to_score_pct)}
      ${meter('Ср. голы (всего)', betH.avg_total_goals, betA.avg_total_goals, '')}
      ${meter('Ср. забитых', betH.avg_goals_for, betA.avg_goals_for, '')}
      ${meter('Ср. пропущенных', betH.avg_goals_against, betA.avg_goals_against, '')}
      ${meter('Угловые (ср.)', msH.corners, msA.corners, '')}
      ${meter('Отдых (дни)', betH.rest_days, betA.rest_days, 'int')}
      ${meter('ТБ 0.5 (1 тайм)', htH.ht_over_05_pct, htA.ht_over_05_pct)}
      ${meter('ТБ 1.5 (1 тайм)', htH.ht_over_15_pct, htA.ht_over_15_pct)}
      ${peakRow}
    </div>`;
  }

  // --- H2H ---
  const h2h = s.h2h || {};
  if (h2h.total || (h2h.recent && h2h.recent.length)) {
    panels.h2h = `<div class="card stats-card"><div class="card-title">${ico('field', 'ico-white')} Дуэль</div>
      <div class="h2h-summary">
        <span>${h2h.home_wins || 0} побед ${escapeHtml(homeName)}</span>
        <span>${h2h.draws || 0} ничьих</span>
        <span>${h2h.away_wins || 0} побед ${escapeHtml(awayName)}</span>
      </div>
      ${h2h.avg_goals != null ? `<div class="form-meta" style="text-align:center;margin-bottom:8px">Ср. голов: ${Number(h2h.avg_goals).toFixed(1)}</div>` : ''}
      ${(h2h.recent || []).map(g => {
        const sc = String(g.score || '—').replace(/-/g, ':');
        return `<div class="last5-row"><span class="last5-opp">${escapeHtml((g.date || '').slice(5))} · ${escapeHtml(g.home || '')} — ${escapeHtml(g.away || '')}</span><span class="last5-score">${escapeHtml(sc)}</span></div>`;
      }).join('')}
    </div>`;
  }

  return panels;
}

function buildMatchTabsHtml(m, panels) {
  const fc = ((m.stats || {}).forecast) || m.forecast || {};
  const singles = Array.isArray(fc.singles) ? fc.singles : [];
  const combos = Array.isArray(fc.combos) ? fc.combos : [];
  const analysis = fc.analysis || '';
  const pot = fc.potential || {};
  // Предупреждение «мало игр» только в блоке Прогноз (≤4 игр в лиге)
  const _s = m.stats || {};
  const _msH = (_s.match_stats || {}).home || {};
  const _msA = (_s.match_stats || {}).away || {};
  const _betH = (_s.betting || {}).home || {};
  const _betA = (_s.betting || {}).away || {};
  const _fh = (_s.form || {}).home || {};
  const _fa = (_s.form || {}).away || {};
  const sampleWarnPred = sampleWarningHtml(
    _msH.matches, _msA.matches,
    _betH.matches, _betA.matches,
    _fh.matches, _fa.matches,
    pot.matches_count, (pot.home || {}).matches_count, (pot.away || {}).matches_count
  );
  const pickCard = (p, kind, isCombo = false) => {
    if (!p) return '';
    const whyLines = String(p.why || '').split(/\n+/).map(l => l
      .replace(/\s*\(?кф\s*[\d.,]+\)?/gi,'')
      .replace(/Кэф ориентировочный[^\n.]*/gi,'')
      .replace(/\b\d{1,3}%/g,'')
      .replace(/Для более уверенной ставки можно брать ТМ 4\.5[^\n.]*/gi, '')
      .replace(/ТМ 4\.5 в матче \(комбинированный вариант\)\.?/gi, '')
      .replace(/Лучший вариант\s*ТМ\s*4\.5[^\n.]*/gi, '')
      .replace(/Наиболее лучший вариант\s*ТМ\s*4\.5[^\n.]*/gi, '')
      .trim()
    ).filter(Boolean).slice(0, 5);
    const why = whyLines.length
      ? `<div class="fc-why">${whyLines.map(l => `<p>${escapeHtml(l)}</p>`).join('')}</div>`
      : '';
    const isU35 = isCombo && (
      p.key === 'under_35'
      || (Array.isArray(p.keys) && p.keys.includes('under_35'))
      || /ТМ\s*3\.5/i.test(String(p.pick || ''))
    );
    const u35Hint = isU35
      ? `<div class="fc-alt-hint">Лучший вариант ТМ 4.5</div>`
      : '';
    return `<div class="fc-card fc-card-sm">
      <div class="fc-card-top">
        <span class="fc-kind">${kind}</span>
      </div>
      <div class="fc-pick">${escapeHtml(p.pick || '')}</div>
      ${u35Hint}
      ${why}
    </div>`;
  };
  let predPanelHtml = `<div class="card stats-card"><div class="card-title">${ico('pred', 'ico-white')} Прогноз</div>`;
  // Предупреждение «мало игр» — сразу ниже заголовка «Прогноз»
  if (sampleWarnPred) predPanelHtml += sampleWarnPred;
  if (analysis || singles.length || combos.length) {
    if (pot.home_final != null || pot.away_final != null) {
      const oh = Number(pot.home_overall != null ? pot.home_overall : pot.home_final);
      const oa = Number(pot.away_overall != null ? pot.away_overall : pot.away_final);
      const overallDiff = (!isNaN(oh) && !isNaN(oa)) ? Math.abs(oh - oa) : 0;
      const homeLead = !isNaN(oh) && !isNaN(oa) && oh > oa;
      const awayLead = !isNaN(oh) && !isNaN(oa) && oa > oh;
      // зелёная плашка: «общий» + цифра разницы
      const badge = (show) => show && overallDiff > 0
        ? `<div class="fc-pot-badge-wrap"><span class="fc-pot-badge">Общий +${Math.round(overallDiff)}</span></div>`
        : '<div class="fc-pot-badge-wrap fc-pot-badge-empty"></div>';
      // Звёзды и текст ВСЕГДА от реальной разницы дом/выезд (цифры на экране)
      // ≤7=0, 8–15=1, 16–22=2, 23–28=3, 29–33=4, ≥34=5
      const hf = Number(pot.home_final);
      const af = Number(pot.away_final);
      const d = (!isNaN(hf) && !isNaN(af))
        ? Math.abs(hf - af)
        : Number(pot.diff != null ? pot.diff : 0);
      let potStars = 0;
      let potClass = 'Соперники равны';
      const strongerName = (!isNaN(hf) && !isNaN(af) && af > hf)
        ? (pot.stronger || 'Гости')
        : (pot.stronger || 'Хозяева');
      if (d <= 7) {
        potStars = 0;
        potClass = 'Соперники равны';
      } else if (d <= 15) {
        potStars = 1;
        potClass = `${strongerName} чуть сильнее`;
      } else if (d <= 22) {
        potStars = 2;
        potClass = `${strongerName} сильнее`;
      } else if (d <= 28) {
        potStars = 3;
        potClass = `${strongerName} на много сильнее`;
      } else if (d <= 33) {
        potStars = 4;
        potClass = `${strongerName} выше классом`;
      } else {
        // d >= 34 → 5 звёзд
        potStars = 5;
        potClass = `${strongerName} гораздо выше классом`;
      }
      potStars = Math.min(5, Math.max(0, potStars | 0));
      const starsHtml = potStars > 0
        ? `<div class="fc-pot-stars" aria-hidden="true">${'★'.repeat(potStars)}</div>`
        : '';
      predPanelHtml += `<div class="fc-pot-caption">Потенциал силы команды <span class="fc-pot-paren">(дом/выезд)</span></div>`;
      predPanelHtml += `<div class="fc-pot-row">
        <div class="fc-pot-side">
          <span class="fc-pot-lab">Хозяева · дом</span>
          <div class="fc-pot-val"><b>${pot.home_final ?? '—'}</b><span class="fc-pot-max">/100</span></div>
          ${homeLead ? badge(true) : badge(false)}
        </div>
        <div class="fc-pot-mid">${starsHtml}<div class="fc-pot-class">${escapeHtml(potClass)}</div></div>
        <div class="fc-pot-side">
          <span class="fc-pot-lab">Гости · выезд</span>
          <div class="fc-pot-val"><b>${pot.away_final ?? '—'}</b><span class="fc-pot-max">/100</span></div>
          ${awayLead ? badge(true) : badge(false)}
        </div>
      </div>`;
    }
    // --- Мотивация (ниже потенциала) ---
    const mot = fc.motivation || {};
    const motH = mot.home || {};
    const motA = mot.away || {};
    if (motH.label || motA.label || mot.note) {
      const levelClass = (lv) => {
        const k = String(lv || 'mid').toLowerCase();
        if (k === 'max') return 'mot-max';
        if (k === 'high') return 'mot-high';
        if (k === 'low' || k === 'none') return 'mot-low';
        return 'mot-mid';
      };
      const cell = (side) => {
        const pos = side.pos != null ? `#${side.pos}` : '—';
        const pts = side.pts != null ? `${side.pts} очк.` : '';
        const meta = [pos, pts].filter(Boolean).join(' · ');
        return `<div class="fc-mot-side">
          <div class="fc-mot-name">${escapeHtml(side.name || '—')}</div>
          <div class="fc-mot-badge ${levelClass(side.level)}">${escapeHtml(side.label || '—')}</div>
          <div class="fc-mot-meta">${escapeHtml(meta)}</div>
        </div>`;
      };
      predPanelHtml += `<div class="fc-mot-block">
        <div class="fc-mot-caption">Мотивация</div>
        <div class="fc-mot-row">
          ${cell(motH)}
          <div class="fc-mot-vs">vs</div>
          ${cell(motA)}
        </div>
      </div>`;
    }
    const injuryRows = Array.isArray(fc.injury_rows) ? fc.injury_rows : [];
    if (injuryRows.length) {
      predPanelHtml += `<div class="fc-inj-block"><div class="fc-inj-title">Травмы</div>`;
      injuryRows.forEach(line => {
        // Выделяем название команды жирным, остальной текст серым
        const raw = String(line || '');
        const colon = raw.indexOf(':');
        let htmlLine;
        if (colon > 0) {
          const team = raw.slice(0, colon).trim();
          const rest = raw.slice(colon + 1).trim();
          htmlLine = `<span class="fc-inj-team">${escapeHtml(team)}</span><span class="fc-inj-rest">: ${escapeHtml(rest)}</span>`;
        } else {
          htmlLine = `<span class="fc-inj-rest">${escapeHtml(raw)}</span>`;
        }
        predPanelHtml += `<div class="fc-inj-line">${htmlLine}</div>`;
      });
      predPanelHtml += `</div>`;
    }
    if (singles.length) {
      predPanelHtml += `<div class="fc-section-title fc-section-white">Одиночный вариант</div>`;
      singles.slice(0, 4).forEach((p, i) => { predPanelHtml += pickCard(p, `Вариант ${i + 1}`, false); });
    }
    if (combos.length) {
      predPanelHtml += `<div class="fc-section-title fc-section-white">Комбинированный вариант</div>`;
      combos.slice(0, 2).forEach((p, i) => { predPanelHtml += pickCard(p, `Вариант ${i + 1}`, true); });
    }
    
  } else if (m.text) {
    predPanelHtml += `<div class="match-text">${escapeHtml(m.text)}</div>`;
  } else {
    predPanelHtml += `<span class="muted">Прогноз формируется… Откройте матч ещё раз или обновите статистику</span>`;
  }
  predPanelHtml += `</div>`;

  // Squad tab sub-panels
  const squadSubs = [];
  if (panels.squad) squadSubs.push({ id: 'lineup', label: `${ico('squad', 'ico-white')} Состав`, html: panels.squad });
  if (panels.injuries) squadSubs.push({ id: 'inj', label: `${ico('medical', 'ico-white')} Травмы`, html: panels.injuries });
  if (!squadSubs.length) {
    squadSubs.push({ id: 'lineup', label: `${ico('squad', 'ico-white')} Состав`, html: '<div class="card stats-card"><p class="muted" style="text-align:center">Нет данных о составе</p></div>' });
  }

  // Stats tab sub-panels
  const statsSubs = [];
  const statsOrder = [
    ['impulse', 'impulse', 'Импульс'],
    ['standings', 'table', 'Таблица'],
    ['edge', 'trend', 'Тренды'],
    ['match', 'ball', 'Матч'],
    ['halves', 'half', 'Таймы'],
    ['goals', 'target', 'Интервал'],
    ['potential', 'chart', 'Потенциал'],
    ['form', 'shirt', 'Форма'],
    ['h2h', 'field', 'Дуэль'],
  ];
  for (const [key, icon, label] of statsOrder) {
    if (panels[key]) statsSubs.push({ id: key, label: `${ico(icon, 'ico-white')} ${label}`, html: panels[key] });
  }
  if (!statsSubs.length) {
    const seasonEmpty = ((s.form || {}).home || {}).season_empty || ((s.form || {}).away || {}).season_empty
      || ((s.form || {}).home || {}).season_message || ((s.form || {}).away || {}).season_message;
    const emptyMsg = seasonEmpty
      ? 'Статистики ещё нет — сезон не стартовал'
      : 'Статистика пока недоступна';
    statsSubs.push({ id: 'empty', label: `${ico('stats', 'ico-white')} Статистика`, html: `<div class="card stats-card"><p class="muted" style="text-align:center">${emptyMsg}</p></div>` });
  }

  const makeSubTabs = (subs) => {
    const tabs = subs.map((s, i) =>
      `<button type="button" class="sub-tab${i === 0 ? ' active' : ''}" onclick="switchSubTab(this,'${s.id}')">${s.label}</button>`
    ).join('');
    const panelsHtml = subs.map((s, i) =>
      `<div class="sub-panel${i === 0 ? ' active' : ''}" data-sub="${s.id}">${s.html}</div>`
    ).join('');
    return `<div class="sub-tabs">${tabs}</div><div class="sub-panels">${panelsHtml}</div>`;
  };

  return `
  <div class="match-tabs-root">
    <div class="match-tabs">
      <button type="button" class="match-tab active" onclick="switchMatchTab(this,'squad')">
        ${ico('squad', 'ico-white')}<span>Состав</span>
      </button>
      <button type="button" class="match-tab" onclick="switchMatchTab(this,'stats')">
        ${ico('stats', 'ico-white')}<span>Статистика</span>
      </button>
      <button type="button" class="match-tab" onclick="switchMatchTab(this,'pred')">
        ${ico('pred', 'ico-white')}<span>Прогноз</span>
      </button>
    </div>
    <div class="match-tab-panels">
      <div class="match-tab-panel active" data-panel="squad">
        ${makeSubTabs(squadSubs)}
      </div>
      <div class="match-tab-panel" data-panel="stats">
        ${makeSubTabs(statsSubs)}
      </div>
      <div class="match-tab-panel" data-panel="pred">
        ${predPanelHtml}
      </div>
    </div>
  </div>`;
}

/** Блок Импульс + опасные отрезки 15 мин — вкладка Статистика, только Premium */
function buildMatchImpulsePanel(m, isPremium) {
  if (!isPremium) {
    return `<div class="card stats-card imp-detail-compact">
      <div class="imp-detail-title">${ico('impulse', 'ico-white')} Импульс</div>
      <div class="lock-overlay" style="position:relative;padding:20px 8px">
        <div class="icon">${ico('lock')}</div>
        <p style="color:var(--text-secondary);margin:8px 0 12px">Доступно только по Premium</p>
        <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
      </div>
    </div>`;
  }

  const thr = Number(m.impulse_threshold) || 89;
  const sigs = (m.impulse_signals || []).filter(s => {
    const mn = Number(s.minute);
    return Number(s.value) >= thr && mn > 10 && !(mn >= 46 && mn <= 47) && mn <= 89;
  });
  sigs.sort((a, b) => {
    const ma = Number(a.minute) || 0, mb = Number(b.minute) || 0;
    if (mb !== ma) return mb - ma;
    return (Number(b.value) || 0) - (Number(a.value) || 0);
  });

  const teamsSplit = (m.name || '').split(/—|–|-/).map(x => x.trim()).filter(Boolean);
  const homeName = teamsSplit[0] || 'Хозяева';
  const awayName = teamsSplit[1] || 'Гости';
  const isHome = (s) => {
    const side = String(s.side || '').toLowerCase();
    if (side === 'home' || side === 'h') return true;
    if (side === 'away' || side === 'a') return false;
    const t = String(s.team || '').toLowerCase();
    if (t && homeName.toLowerCase().includes(t)) return true;
    if (t && awayName.toLowerCase().includes(t)) return false;
    return t && homeName.toLowerCase().startsWith(t.slice(0, 4));
  };
  const homeSigs = sigs.filter(isHome);
  const awaySigs = sigs.filter(s => !isHome(s));

  const rowHtml = (s) => {
    const sm = Number(s.minute);
    const smLabel = (Number.isFinite(sm) && sm > 0) ? `${sm}'` : '—';
    const val = Math.round(Number(s.value) || 0);
    return `<div class="imp-split-row">
      <span class="imp-td-min">${smLabel}</span>
      <span class="imp-td-score">${escapeHtml(s.score || '—')}</span>
      <span class="imp-td-val">${val}</span>
    </div>`;
  };

  const headHtml = `<div class="imp-split-head">
    <span class="imp-th-min">Мин</span>
    <span class="imp-th-score">Счёт</span>
    <span class="imp-th-val">${ico('impulse')}</span>
  </div>`;

  const colHtml = (name, list, limit, withHead) => {
    const top = (limit != null) ? list.slice(0, limit) : list;
    const body = top.length
      ? top.map(rowHtml).join('')
      : `<div class="imp-split-empty">—</div>`;
    return `<div class="imp-split-col">
      <div class="imp-split-team" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
      ${withHead ? headHtml : ''}
      <div class="imp-split-rows">${body}</div>
    </div>`;
  };

  let signalsInner;
  if (sigs.length) {
    signalsInner = `
      <div class="imp-split">
        ${colHtml(homeName, homeSigs, 3, true)}
        <div class="imp-split-divider"></div>
        ${colHtml(awayName, awaySigs, 3, true)}
      </div>
      <details class="imp-spoiler">
        <summary>
          <span class="imp-spoiler-btn" aria-label="Все сигналы">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
          </span>
        </summary>
        <div class="imp-split imp-split-full-wrap">
          ${colHtml(homeName, homeSigs, null, true)}
          <div class="imp-split-divider"></div>
          ${colHtml(awayName, awaySigs, null, true)}
        </div>
      </details>`;
  } else {
    signalsInner = `<div class="imp-empty-live">Сигналы появятся после начала матча и live-статистики</div>`;
  }

  // Опасные отрезки 10 мин — компактная таблица
  const live = m.impulse_live || null;
  let dangerInner = '';
  let ph = null, pa = null;
  if (live && (Number(live.press_home) > 0 || Number(live.press_away) > 0 || Number(live.impulse_home) > 0 || Number(live.impulse_away) > 0)) {
    ph = Number(live.press_home);
    pa = Number(live.press_away);
    if (!(ph > 0 || pa > 0)) {
      const ih = Number(live.impulse_home) || 0;
      const ia = Number(live.impulse_away) || 0;
      const t = ih + ia;
      ph = t > 0 ? Math.round(ih / t * 1000) / 10 : 50;
      pa = t > 0 ? Math.round((1000 - ph * 10)) / 10 : 50;
    }
    ph = Math.max(0, Math.min(100, ph));
    pa = Math.max(0, Math.min(100, pa));
    dangerInner = `
      <div class="danger-table-wrap">
        <div class="danger-table-title">Опасные отрезки · 15 мин</div>
        <table class="danger-table">
          <thead><tr><th>Команда</th><th>Давление</th><th></th></tr></thead>
          <tbody>
            <tr>
              <td class="danger-td-name">${escapeHtml(homeName)}</td>
              <td class="danger-td-bar"><div class="danger-bar-track"><div class="danger-bar-fill danger-bar-home" style="width:${ph}%"></div></div></td>
              <td class="danger-td-pct">${ph}%</td>
            </tr>
            <tr>
              <td class="danger-td-name">${escapeHtml(awayName)}</td>
              <td class="danger-td-bar"><div class="danger-bar-track"><div class="danger-bar-fill danger-bar-away" style="width:${pa}%"></div></div></td>
              <td class="danger-td-pct">${pa}%</td>
            </tr>
          </tbody>
        </table>
      </div>`;
  } else {
    dangerInner = `
      <div class="danger-table-wrap danger-table-empty">
        <div class="danger-table-title">Опасные отрезки · 15 мин</div>
        <div class="imp-empty-live">Данные появятся в live</div>
      </div>`;
  }

  // Статистика именно этой лиги · как в отчёте (шкалы, таймы, звёзды)
  let leagueStatsInner = '';
  const ls = m.impulse_league_stats || null;
  const leagueName = (ls && ls.league) || m.league || '';
  const countryName = (ls && ls.country) || m.country || '';
  if (leagueName || countryName) {
    const pctCol = (p) => {
      p = Math.max(0, Math.min(100, Number(p) || 0));
      if (p >= 50) return '#00e6b4';
      if (p >= 35) return '#fbbf24';
      if (p > 0) return '#f87171';
      return '#64748b';
    };
    const bar = (pct, label, sub) => {
      const p = Math.max(0, Math.min(100, Number(pct) || 0));
      const col = pctCol(p);
      return `<div class="imp-hit-row">
        <div class="imp-hit-meta">
          <span>${label}${sub ? ` <span class="muted">${sub}</span>` : ''}</span>
          <span class="imp-hit-pct" style="color:${col}">${p}%</span>
        </div>
        <div class="imp-hit-track"><div class="imp-hit-fill" style="width:${p}%;background:${col}"></div></div>
      </div>`;
    };
    const logo = (ls && ls.league_logo) || m.league_logo || '';
    const flag = (ls && ls.flag) || m.flag || '';
    const count = (ls && ls.count) || 0;
    const withGoal = (ls && ls.with_goal) || 0;
    const win = (ls && ls.goal_window) || 20;
    const pct = (ls && ls.hit_pct) || 0;
    const halves = (ls && ls.halves) || {};
    const h1 = halves[1] || halves['1'] || {};
    const h2 = halves[2] || halves['2'] || {};
    const starLab = {1: '★ 77–82', 2: '★★ 83–89', 3: '★★★ 90+'};
    const starBars = [1, 2, 3].map(k => {
      const s = ((ls && ls.by_stars) || {})[k] || ((ls && ls.by_stars) || {})[String(k)] || {};
      const c = s.count || 0;
      if (!c) return '';
      return bar(s.hit_pct, starLab[k], `(${s.with_goal || 0}/${c})`);
    }).join('');
    const halfCell = (block, sk) => {
      const b = (block || {})[sk] || (block || {})[String(sk)] || {};
      const c = b.count || 0;
      const w = b.with_goal || 0;
      const p = b.hit_pct != null ? b.hit_pct : (c ? Math.round(1000 * w / c) / 10 : 0);
      if (!c) return `<span class="muted">—</span>`;
      return `<span style="color:${pctCol(p)};font-weight:700">${p}%</span><div class="muted" style="font-size:10px">${w}/${c}</div>`;
    };
    const halfRows = [1, 2, 3].map(sk => {
      const lab = sk === 3 ? '★★★' : (sk === 2 ? '★★' : '★');
      return `<tr>
        <td>${lab}</td>
        <td style="text-align:center">${halfCell(h1, sk)}</td>
        <td style="text-align:center">${halfCell(h2, sk)}</td>
      </tr>`;
    }).join('');
    const h1all = (h1.all || h1['all'] || {});
    const h2all = (h2.all || h2['all'] || {});
    leagueStatsInner = `
      <div class="danger-table-wrap imp-league-stats">
        <div class="imp-geo-head" style="margin-bottom:8px">
          <span>${flag}</span>
          ${logo ? `<img class="imp-league-logo" src="${escapeHtml(logo)}" alt="" onerror="this.style.display='none'" />` : ''}
          <span>${escapeHtml(countryName)}${leagueName ? ' · ' + escapeHtml(leagueName) : ''}</span>
        </div>
        ${count > 0 ? `
        ${bar(pct, 'Проходимость лиги', `(${withGoal}/${count})`)}
        <div class="muted" style="font-size:10px;margin-top:6px;color:rgba(148,163,184,.9)">Интервал 20 мин после сигнала</div>
        ` : `<div class="imp-empty-live">По этой лиге пока нет сигналов</div>`}
      </div>`;
  }

  return `<div class="card stats-card imp-detail-compact">
    <div class="imp-detail-title">${ico('impulse', 'ico-white')} Импульс</div>
    ${signalsInner}
    ${leagueStatsInner}
    ${dangerInner}
  </div>`;
}

/** Вариант A+: метрики + одиночный/комбо прогноз по таблице, голам, отрезкам, таймам, H2H, травмам
 *  data: number | { id, tab?: 'impulse' }
 */
async function renderMatchDetail(data, forceRefresh = false) {
  const matchId = (data && typeof data === 'object') ? data.id : data;
  const openTab = (data && typeof data === 'object') ? (data.tab || null) : null;
  content.innerHTML = `<button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="empty-state" style="padding:40px 0"><div class="icon">${ico('clock')}</div><p>Загружаем статистику...</p></div>`;
  try {
    const m = await api(`/matches/${matchId}?refresh=${forceRefresh ? 1 : 0}`);
    const favBtn = m.is_favorite
      ? `<button class="btn btn-danger" onclick="toggleFav(${m.id})">${ico('heart')} Убрать из избранного</button>`
      : `<button class="btn btn-secondary" onclick="toggleFav(${m.id})">${ico('star')} В избранное</button>`;
    const panels = renderStatsBlock(m);
    const me = currentUser || await api('/me').catch(() => null);
    const isPrem = !!(me && me.user && me.user.subscription === 'Premium');
    panels.impulse = buildMatchImpulsePanel(m, isPrem);
    const tabsHtml = buildMatchTabsHtml(m, panels);

    const liveMeta = m.match_time
      ? `<span class="meta-tag match-time">${escapeHtml(m.match_time)}</span>`
      : '';
    content.innerHTML = `
      <button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="match-detail-header">
        <div style="display:flex;justify-content:center;gap:12px;margin-bottom:8px">${logosHtml(m.logo_home, m.logo_away)}</div>
        <div class="match-detail-name">${escapeHtml(m.name)}</div>
        <div class="match-meta">
          ${liveMeta}
          <span class="meta-tag">${m.flag || ''} ${escapeHtml(m.country)}</span>
          <span class="meta-tag">${ico('trophy')} ${escapeHtml(m.league)}</span>
        </div>
      </div>
      ${tabsHtml}
      <div style="margin-top:16px">${favBtn}</div>`;

    // Deep-link из уведомления импульса → вкладка Статистика → Импульс
    if (openTab === 'impulse') {
      const root = content.querySelector('.match-tabs-root');
      if (root) {
        root.querySelectorAll('.match-tab').forEach(b => {
          const isStats = (b.getAttribute('onclick') || '').includes("'stats'");
          b.classList.toggle('active', isStats);
        });
        root.querySelectorAll('.match-tab-panel').forEach(p => {
          p.classList.toggle('active', p.dataset.panel === 'stats');
        });
        const statsPanel = root.querySelector('.match-tab-panel[data-panel="stats"]');
        if (statsPanel) {
          statsPanel.querySelectorAll('.sub-tab').forEach(b => {
            const isImp = (b.getAttribute('onclick') || '').includes("'impulse'");
            b.classList.toggle('active', isImp);
          });
          statsPanel.querySelectorAll('.sub-panel').forEach(p => {
            p.classList.toggle('active', p.dataset.sub === 'impulse');
          });
        }
      }
    }
  } catch (e) {
    if (String(e.message).includes('Лимит')) {
      content.innerHTML = `
        <button class="btn-back" onclick="goBack()">← Назад</button>
        <div class="empty-state"><div class="icon">${ico('lock')}</div><p>${escapeHtml(e.message)}</p>
        <button class="btn btn-gold" onclick="navigate('premium')" style="margin-top:16px">Купить Premium</button></div>`;
    } else throw e;
  }
}

async function toggleFav(matchId) {
  try {
    const res = await api(`/favorites/${matchId}`, { method: 'POST' });
    toast(res.message);
    renderMatchDetail(matchId);
  } catch (e) { toast(e.message); }
}

/* ---------- EXPRESS ---------- */
/** Страница Импульс — только Premium, 7 сигналов на страницу + пагинация */
async function renderImpulsePage(page = 0) {
  const me = currentUser || await api('/me').catch(() => null);
  const isPrem = me && me.user && me.user.subscription === 'Premium';
  if (!isPrem) {
    content.innerHTML = `<div class="express-card"><div class="lock-overlay">
      <div class="icon">${ico('lock')}</div>
      <h3 style="margin-bottom:8px">${ico('impulse', 'ico-white')} Импульс</h3>
      <p style="color:var(--text-secondary);margin-bottom:16px">Страница и полный список сигналов доступны только по Premium</p>
      <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
    </div></div>`;
    return;
  }
  const PER = 7;
  let data;
  try {
    // берём с запасом для пагинации на клиенте
    data = await api('/impulse/recent?limit=250');
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="icon">${ico('warning')}</div>
      <p>${escapeHtml(e.message || 'Ошибка загрузки')}</p>
      <button class="btn btn-primary" onclick="renderImpulsePage(0)" style="margin-top:12px">Повторить</button></div>`;
    return;
  }
  const all = (data.signals || []).filter(s => {
    const mn = Number(s.minute);
    return mn > 10 && !(mn >= 46 && mn <= 47) && mn <= 89;
  });
  const thr = data.threshold != null ? Number(data.threshold) : 65;
  const totalPages = Math.max(1, Math.ceil(all.length / PER));
  page = Math.max(0, Math.min(page, totalPages - 1));
  const slice = all.slice(page * PER, (page + 1) * PER);
  let rows = slice.length
    ? slice.map(s => _impulseSignalRow(s, false)).join('')
    : `<div class="imp-hist-empty">Пока нет сигналов ≥ ${thr}</div>`;
  let pag = '';
  if (totalPages > 1) {
    pag = `<div class="pagination">
      <button class="page-btn" ${page <= 0 ? 'disabled' : ''} onclick="renderImpulsePage(${page - 1})">‹</button>
      <span class="page-info">${page + 1} / ${totalPages}</span>
      <button class="page-btn" ${page >= totalPages - 1 ? 'disabled' : ''} onclick="renderImpulsePage(${page + 1})">›</button>
    </div>`;
  }
  content.innerHTML = `
    <div class="section-title">${ico('impulse', 'ico-white')} Импульс</div>
    <div class="card bl-card imp-card">
      <div class="bl-subtitle">Матчи с наиболее опасными голевыми моментами</div>
      <div class="imp-rows">${rows}</div>
      ${pag}
    </div>`;
}

/* ---------- ACCOUNT ---------- */
function renderBannedScreen(banLabel) {
  content.innerHTML = `
    <div class="center-block-screen">
      <div class="center-block-icon">${ico('lock', 'ico-white')}</div>
      <div class="center-block-title">Доступ закрыт</div>
      <p class="center-block-text">
        Вам закрыт доступ к приложению<br>
        <b>${escapeHtml(banLabel || 'навсегда')}</b>
      </p>
    </div>`;
  const nav = document.querySelector('.bottom-nav');
  if (nav) nav.style.display = 'none';
}

function renderMaintenanceScreen() {
  content.innerHTML = `
    <div class="center-block-screen">
      <div class="center-block-icon center-block-icon-maint">${ico('clock', 'ico-white')}</div>
      <div class="center-block-title">Технические работы</div>
      <p class="center-block-text">
        Приносим свои извинения,<br>ведутся технические работы
      </p>
    </div>`;
  const nav = document.querySelector('.bottom-nav');
  if (nav) nav.style.display = 'none';
}

async function renderAccount() {
  const me = await api('/me');
  currentUser = me;
  if (me.banned || (me.user && me.user.banned)) {
    renderBannedScreen(me.ban_label || me.user?.ban_label || 'навсегда');
    return;
  }
  if (me.maintenance) {
    renderMaintenanceScreen();
    return;
  }
  let supportUnread = 0;
  try {
    const ur = await api('/support/unread');
    supportUnread = Number(ur.unread || 0);
  } catch (_) {}
  const u = me.user;
  const s = me.stats;
  const isPrem = u.subscription === 'Premium';
  const subBadge = isPrem
    ? `<span class="sub-badge premium premium-gold">${ico('star')} Premium</span>`
    : `<span class="sub-badge free">${ico('free', 'ico-white')} Free</span>`;
  let premiumBtn = u.subscription === 'Free'
    ? `<button class="btn btn-gold" onclick="navigate('premium')">${ico('star', 'ico-white')} Купить Premium</button>`
    : `<div class="card" style="margin-top:12px"><ul class="info-list">
        <li><span class="label">${ico('calendar', 'ico-white')} Premium до</span><span class="value">${escapeHtml(u.end_date || '—')}</span></li>
      </ul></div>`;
  const nameCls = 'profile-name';
  const unameHtml = u.username
    ? `<div class="profile-username">@${escapeHtml(u.username)}</div>`
    : '';

  content.innerHTML = `
    <div class="profile-card">
      ${avatarHtml(u.photo_url, u.first_name, 'profile-avatar')}
      <div class="${nameCls}">${escapeHtml(u.first_name)}</div>
      ${unameHtml}
      <div class="profile-id">ID: ${u.id}</div>
      ${subBadge}
      <div class="stats-row">
        <div class="stat-item"><div class="stat-value">${u.matches_left}</div><div class="stat-label">Матчей сегодня</div></div>
        <div class="stat-item"><div class="stat-value">${u.days_in_project}</div><div class="stat-label">Дней в проекте</div></div>
        <div class="stat-item"><div class="stat-value">${s.fav_count}</div><div class="stat-label">Избранное</div></div>
      </div>
    </div>
    <div class="card"><ul class="info-list">
      <li><span class="label">${ico('calendar', 'ico-white')} С нами с</span><span class="value">${escapeHtml(u.reg_date || '—')}</span></li>
      <li><span class="label">${ico('check', 'ico-white')} Матчей в базе</span><span class="value">${s.total_matches}</span></li>
      <li><span class="label">${ico('users', 'ico-white')} Всего пользователей</span><span class="value">${s.total_users}</span></li>
      <li><span class="label">${ico('star', 'ico-white')} Premium-пользователей</span><span class="value">${s.premium_users}</span></li>
    </ul></div>
    ${isPrem ? `<button class="btn btn-secondary" onclick="navigate('best-matches')">${ico('trophy', 'ico-white')} Матч дня (топ-10)</button>` : ''}
    <button class="btn btn-secondary" onclick="navigate('favorites')">${ico('star', 'ico-white')} Избранное (${s.fav_count})</button>
    <button class="btn btn-secondary" onclick="navigate('ref')">${ico('link', 'ico-white')} Реферальная ссылка (приглашено: ${s.ref_count})</button>
    <button class="btn btn-secondary" onclick="navigate('support')">${ico('support', 'ico-white')} Техподдержка${supportUnread > 0 ? ` <span class="admin-badge">${supportUnread}</span>` : ''}</button>
    <a class="btn btn-secondary" href="https://t.me/koefchat" target="_blank" rel="noopener" style="text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px">${ico('chat', 'ico-white')} Живой чат</a>
    <a class="btn btn-secondary" href="https://t.me/koefrus" target="_blank" rel="noopener" style="text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px">${ico('channel', 'ico-white')} Канал проекта</a>
    ${premiumBtn}
    ${u.is_admin ? `<button class="btn btn-gold" onclick="navigate('admin')" style="margin-top:8px">${ico('stats', 'ico-white')} Админ-панель</button>` : ''}`;
  updateHeaderUser(u);
}

async function renderFavorites() {
  const data = await api('/favorites');
  let html = `<button class="btn-back" onclick="goBack()">← Назад</button><div class="card-title">${ico('star', 'ico-white')} Избранные матчи</div>`;
  if (!data.favorites.length) {
    html += `<div class="empty-state"><div class="icon">${ico('star')}</div><p>У вас пока нет избранных матчей.</p></div>`;
  } else {
    data.favorites.forEach(m => {
      const time = m.match_time ? `<span class="match-time">${escapeHtml(m.match_time)}</span>` : '<span class="match-time">—</span>';
      html += `<div class="match-item" onclick="navigate('match-detail', ${m.id})">
        ${time}
        ${logosHtml(m.logo_home, m.logo_away)}
        <span class="match-name">${m.flag || ''} ${escapeHtml(m.name)}</span></div>`;
    });
  }
  content.innerHTML = html;
}

async function renderRef() {
  const me = currentUser || await api('/me');
  const link = me.ref_link || '';
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card">
      <div class="card-title">${ico('link', 'ico-white')} Ваша реферальная ссылка</div>
      <p style="font-family:monospace;font-size:13px;word-break:break-all;background:var(--bg-elevated);padding:12px;border-radius:8px;margin:12px 0">${escapeHtml(link)}</p>
      <p style="color:var(--text-secondary);font-size:14px">🎉 За каждого приглашённого — 3 дня Premium!<br>Приглашено: <b>${me.stats?.ref_count ?? 0}</b></p>
      <button class="btn btn-outline-white" id="ref-copy">📋 Скопировать</button>
      <button class="btn btn-outline-white" id="ref-share" style="margin-top:8px">📤 Поделиться в Telegram</button>
    </div>`;
  const copyBtn = document.getElementById('ref-copy');
  if (copyBtn) copyBtn.onclick = () => {
    navigator.clipboard?.writeText(link).then(() => toast('Скопировано!')).catch(() => toast(link));
  };
  const shareBtn = document.getElementById('ref-share');
  if (shareBtn) shareBtn.onclick = () => {
    const text = 'Футбольные прогнозы — открой приложение:';
    try {
      if (tg?.openTelegramLink) {
        tg.openTelegramLink('https://t.me/share/url?url=' + encodeURIComponent(link) + '&text=' + encodeURIComponent(text));
      } else if (tg?.shareURL) {
        tg.shareURL(link, text);
      } else {
        window.open('https://t.me/share/url?url=' + encodeURIComponent(link) + '&text=' + encodeURIComponent(text), '_blank');
      }
    } catch (_) {
      navigator.clipboard?.writeText(link).then(() => toast('Скопировано!'));
    }
  };
}

async function renderPremium() {
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="premium-card premium-card-compact">
      <h2>${ico('star', 'ico-white')} Premium</h2>
      <div class="premium-list">
        <div class="premium-item">
          <div class="premium-row-head">${ico('check', 'ico-white')} Безлимит</div>
          <div class="premium-row-desc">Ежедневно от 20 до 150 матчей</div>
        </div>
        <div class="premium-item">
          <div class="premium-row-head">${ico('check', 'ico-white')} Импульс</div>
          <div class="premium-row-desc">Сигналы опасных голевых моментов и уведомление в Телеграм.</div>
        </div>
        <div class="premium-item">
          <div class="premium-row-head">${ico('check', 'ico-white')} Статистика</div>
          <div class="premium-row-desc">Таблицы, составы, тренды, травмы, матча, таймы, голевые интервалы, потенциал и многое другое</div>
        </div>
        <div class="premium-item">
          <div class="premium-row-head">${ico('check', 'ico-white')} Аналитика</div>
          <div class="premium-row-desc">Ежедневный разбор матчей по всем статистическим данным с пояснением и более вероятным исходом на игру с коэффициентами и процентами вероятности</div>
        </div>
      </div>
      <div class="premium-pay-block">
        <div class="premium-plans">
          <button type="button" class="premium-plan-chip on" data-plan="1m" data-price="300" onclick="selectPremiumPlan(this)">1 мес<br><b>300 ₽</b></button>
          <button type="button" class="premium-plan-chip" data-plan="3m" data-price="750" onclick="selectPremiumPlan(this)">3 мес<br><b>750 ₽</b></button>
          <button type="button" class="premium-plan-chip" data-plan="6m" data-price="1300" onclick="selectPremiumPlan(this)">6 мес<br><b>1300 ₽</b></button>
          <button type="button" class="premium-plan-chip" data-plan="12m" data-price="2200" onclick="selectPremiumPlan(this)">1 год<br><b>2200 ₽</b></button>
        </div>
        <button class="btn btn-gold" id="pay-btn" onclick="createPayment()">${ico('card', 'ico-white')} Оплатить 300 ₽</button>
      </div>
    </div><div id="pay-status"></div>`;
}

function selectPremiumPlan(btn) {
  document.querySelectorAll('.premium-plan-chip').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  const price = btn.dataset.price;
  const plan = btn.dataset.plan;
  const labels = { '1m': '1 месяц', '3m': '3 месяца', '6m': '6 месяцев', '12m': '1 год' };
  const el = document.getElementById('premium-price-label');
  if (el) el.textContent = `${price} ₽ / ${labels[plan] || ''}`;
  const pay = document.getElementById('pay-btn');
  if (pay) pay.innerHTML = `${ico('card', 'ico-white')} Оплатить ${price} ₽`;
}

async function createPayment() {
  const btn = document.getElementById('pay-btn');
  const sel = document.querySelector('.premium-plan-chip.on');
  const plan = sel ? sel.dataset.plan : '1m';
  const price = sel ? sel.dataset.price : '300';
  btn.disabled = true;
  btn.innerHTML = `${ico('refresh', 'ico-white')} Создаём...`;
  try {
    const res = await api('/premium/create', { method: 'POST', body: JSON.stringify({ plan }) });
    if (res.confirmation_url) {
      document.getElementById('pay-status').innerHTML = `<div class="card premium-pay-next" style="margin-top:12px">
        <a href="${res.confirmation_url}" target="_blank" class="btn btn-primary" style="text-decoration:none;display:flex;align-items:center;justify-content:center;gap:6px">${ico('card', 'ico-white')} Перейти к оплате</a>
        <button class="btn btn-secondary" style="margin-top:8px;display:flex;align-items:center;justify-content:center;gap:6px;width:100%" onclick="checkPayment('${res.payment_id}')">${ico('check', 'ico-white')} Я оплатил</button>
      </div>`;
    }
  } catch (e) { toast(e.message); }
  finally {
    btn.disabled = false;
    btn.innerHTML = `${ico('card', 'ico-white')} Оплатить ${price} ₽`;
  }
}

async function checkPayment(pid) {
  try {
    const res = await api(`/premium/check/${pid}`, { method: 'POST' });
    toast(res.message);
    if (res.status === 'succeeded') {
      currentUser = await api('/me');
      setTimeout(() => navigate('account'), 1200);
    }
  } catch (e) { toast(e.message); }
}

/* ====================== ADMIN ====================== */
async function refreshAllStats() {
  toast('Загружаю статистику...');
  try {
    const res = await api('/admin/matches/refresh-stats', { method: 'POST', body: JSON.stringify({}) });
    toast(res.message || 'Готово');
  } catch (e) { toast(e.message); }
}


async function toggleAutoDaily(enable) {
  try {
    const res = await api('/admin/auto-daily', {
      method: 'POST',
      body: JSON.stringify({ enabled: !!enable }),
    });
    toast(res.message || (enable ? 'Авто включено' : 'Авто выключено'));
    await renderAdmin();
  } catch (e) { toast(e.message); }
}

async function saveAutoDailySettings() {
  try {
    const clearTime = document.getElementById('auto-clear-time')?.value || '06:00';
    const parseTime = document.getElementById('auto-parse-time')?.value || '08:00';
    const maxMatches = parseInt(document.getElementById('auto-max-matches')?.value || '80', 10);
    const ranks = [...document.querySelectorAll('.auto-rank-chip.on')].map(el => parseInt(el.dataset.rank, 10)).filter(Boolean);
    const keyLeagues = Array.isArray(window._autoKeyLeagues) ? window._autoKeyLeagues.slice() : [];
    const enabled = document.getElementById('auto-switch')?.classList.contains('on');
    const res = await api('/admin/auto-daily', {
      method: 'POST',
      body: JSON.stringify({
        enabled: !!enabled,
        clear_time: clearTime,
        parse_time: parseTime,
        ranks,
        key_leagues: keyLeagues,
        max_matches: maxMatches,
      }),
    });
    toast(res.message || 'Сохранено');
    await renderAdminAuto();
  } catch (e) { toast(e.message); }
}

function toggleAutoSwitch() {
  const el = document.getElementById('auto-switch');
  if (!el) return;
  el.classList.toggle('on');
}

function toggleAutoChip(el) {
  if (!el) return;
  el.classList.toggle('on');
}

async function renderAdmin() {
  const stats = await api('/admin/stats');
  let unread = 0;
  let autoOn = false;
  try {
    const sup = await api('/admin/support');
    unread = Number(sup.unread_total || 0);
  } catch (_) {}
  try {
    const auto = await api('/admin/auto-daily');
    autoOn = !!auto.enabled;
  } catch (_) {}
  let maintOn = false;
  try {
    const mt = await api('/admin/maintenance');
    maintOn = !!mt.enabled;
  } catch (_) {}
  const supportLabel = unread > 0
    ? `🛟 Техподдержка <span class="admin-badge">${unread}</span>`
    : '🛟 Техподдержка';
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('account',null,false)">← Назад</button>
    <div class="card-title">🔧 Админ-панель</div>
    <div class="stats-grid" style="margin-bottom:16px">
      <div class="stat-box"><div class="val">${stats.total_users}</div><div class="lbl">Пользователей</div></div>
      <div class="stat-box"><div class="val">${stats.premium_users}</div><div class="lbl">Premium</div></div>
      <div class="stat-box"><div class="val">${stats.matches}</div><div class="lbl">Матчей</div></div>
    </div>
    <button class="cat-btn" onclick="navigate('admin-parser')">🔍 Парсер матчей</button>
    <button class="cat-btn" onclick="navigate('admin-auto')">⏰ Авто каждый день${autoOn ? ' · вкл' : ''}</button>
    <button class="cat-btn" onclick="navigate('admin-delete')">🗑 Удалить матчи</button>
    <button class="cat-btn" onclick="navigate('admin-impulse-clear')">${ico('impulse', 'ico-white')} Стереть Импульс</button>
    <button class="cat-btn" onclick="navigate('admin-impulse-report')">${ico('impulse', 'ico-white')} Отчёт: сигнал → гол</button>
    <button class="cat-btn" onclick="navigate('admin-ban')">🚫 Бан</button>
    <button class="cat-btn" id="admin-maint-btn" onclick="toggleMaintenance()">${ico('tools', 'ico-white')} Технические работы${maintOn ? ' · <b>ВКЛ</b>' : ''}</button>
    <button class="cat-btn" onclick="navigate('admin-express')">${ico('bolt', 'ico-white')} Экспресс дня</button>
    <button class="cat-btn" onclick="navigate('admin-premium')">⭐ Premium</button>
    <button class="cat-btn" onclick="navigate('admin-support')">${supportLabel}</button>
    <button class="cat-btn" onclick="navigate('admin-logs')">📋 Логи</button>`;
}

async function renderAdminImpulseClear() {
  let cfg = {
    enabled: false, time: '06:00', last_clear: null,
    weekly_rollup: true, last_rollup_week: null,
  };
  try {
    cfg = { ...cfg, ...(await api('/admin/impulse-clear', { noCache: true })) };
  } catch (_) {}
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="section-title">${ico('impulse', 'ico-white')} Стереть Импульс</div>
    <div class="card stats-card" style="margin-bottom:12px">
      <div class="card-title">Сейчас</div>
      <p class="muted" style="font-size:12px;margin:0 0 12px;line-height:1.4">Удалить только текущие сигналы. Накопленная статистика по лигам <b>не</b> сбрасывается.</p>
      <button class="btn btn-danger" id="imp-clear-now">Стереть сигналы сейчас</button>
      <p class="muted" style="font-size:12px;margin:16px 0 12px;line-height:1.4">Сбросить <b>накопленную</b> статистику (недельный rollup / % по лигам) и текущие сигналы.</p>
      <button class="btn btn-danger" id="imp-clear-stats" style="opacity:.95">Стереть статистику полностью</button>
    </div>
    <div class="card stats-card" style="margin-bottom:12px">
      <div class="card-title">Недельный rollup</div>
      <p class="muted" style="font-size:12px;margin:0 0 12px;line-height:1.4">
        Каждый <b>понедельник</b> в указанное время (МСК): итоги недели → в накопленную статистику → очистка сигналов.
        % в отчёте и в блоке лиги обновляется постоянно.
      </p>
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;margin-bottom:12px;cursor:pointer">
        <input type="checkbox" id="imp-weekly-on" ${cfg.weekly_rollup !== false ? 'checked' : ''} />
        Включить недельный rollup
      </label>
      <label style="font-size:12px;color:var(--text-secondary);display:block;margin-bottom:12px">Время (МСК)
        <input type="time" id="imp-clear-time" value="${escapeHtml((cfg.time || '06:00').slice(0, 5))}"
          style="display:block;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.25);color:#fff;width:140px" />
      </label>
      <div class="muted" style="font-size:11px;margin-bottom:12px">
        Последний rollup: <b>${cfg.last_rollup_week ? escapeHtml(String(cfg.last_rollup_week)) : '—'}</b>
        · очистка: <b>${cfg.last_clear ? escapeHtml(String(cfg.last_clear)) : '—'}</b>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px">
        <button class="btn btn-primary" id="imp-clear-save">Сохранить</button>
        <button class="btn btn-secondary" id="imp-rollup-now">Сохранить неделю сейчас</button>
      </div>
    </div>
    <div class="card stats-card">
      <div class="card-title">Только очистка (без сохранения)</div>
      <p class="muted" style="font-size:12px;margin:0 0 12px;line-height:1.4">Ежедневно в то же время, если недельный rollup выключен.</p>
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="imp-clear-on" ${cfg.enabled ? 'checked' : ''} />
        Ежедневная очистка без rollup
      </label>
    </div>`;

  const btnNow = document.getElementById('imp-clear-now');
  if (btnNow) {
    btnNow.onclick = async () => {
      if (!confirm('Стереть текущие сигналы? Накопленная статистика останется.')) return;
      try {
        const r = await api('/admin/impulse', { method: 'DELETE' });
        toast(r.message || 'Очищено');
      } catch (e) {
        toast(e.message || 'Ошибка');
      }
    };
  }
  const btnStats = document.getElementById('imp-clear-stats');
  if (btnStats) {
    btnStats.onclick = async () => {
      if (!confirm('Стереть ВСЮ накопленную статистику импульса и текущие сигналы? Это нельзя отменить.')) return;
      try {
        const r = await api('/admin/impulse?clear_stats=1', { method: 'DELETE' });
        toast(r.message || 'Статистика сброшена');
        renderAdminImpulseClear();
      } catch (e) {
        toast(e.message || 'Ошибка');
      }
    };
  }
  const btnSave = document.getElementById('imp-clear-save');
  if (btnSave) {
    btnSave.onclick = async () => {
      const enabled = !!(document.getElementById('imp-clear-on') || {}).checked;
      const weekly_rollup = !!(document.getElementById('imp-weekly-on') || {}).checked;
      let time = ((document.getElementById('imp-clear-time') || {}).value || '06:00').trim();
      if (time.length >= 5) time = time.slice(0, 5);
      else time = '06:00';
      try {
        const r = await api('/admin/impulse-clear', {
          method: 'POST',
          body: JSON.stringify({ enabled, weekly_rollup, time }),
          noCache: true,
        });
        toast(r.message || 'Сохранено');
        renderAdminImpulseClear();
      } catch (e) {
        toast(e.message || 'Ошибка');
      }
    };
  }
  const btnRoll = document.getElementById('imp-rollup-now');
  if (btnRoll) {
    btnRoll.onclick = async () => {
      if (!confirm('Сохранить текущие сигналы в статистику и очистить таблицу?')) return;
      try {
        const r = await api('/admin/impulse-rollup', { method: 'POST', noCache: true, timeoutMs: 120000 });
        toast(r.message || 'Готово');
        renderAdminImpulseClear();
      } catch (e) {
        toast(e.message || 'Ошибка');
      }
    };
  }
}

async function renderAdminImpulseReport() {
  const yesterday = (() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  })();
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="section-title">${ico('impulse', 'ico-white')} Отчёт: сигнал → гол</div>
    <div class="card stats-card" style="margin-bottom:12px">
      <div class="imp-rep-periods" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">
        <button type="button" class="btn btn-secondary imp-rep-period on" data-period="day" style="padding:6px 12px;font-size:12px">День</button>
        <button type="button" class="btn btn-secondary imp-rep-period" data-period="week" style="padding:6px 12px;font-size:12px">Неделя</button>
        <button type="button" class="btn btn-secondary imp-rep-period" data-period="month" style="padding:6px 12px;font-size:12px">Месяц</button>
        <button type="button" class="btn btn-secondary imp-rep-period" data-period="all" style="padding:6px 12px;font-size:12px">Всё время</button>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end">
        <label style="font-size:12px;color:var(--text-secondary)">Дата (конец периода)
          <input type="date" id="imp-rep-date" value="${yesterday}" style="display:block;margin-top:4px;padding:6px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.12);background:rgba(0,0,0,.25);color:#fff" />
        </label>
        <button class="btn btn-primary" id="imp-rep-run">Считать</button>
      </div>
      <p class="muted" style="font-size:11px;margin-top:10px;line-height:1.4">Страна → лиги/кубки. % по ★ / ★★ / ★★★ и по таймам. Гол в окне 20′ = плюс; сигналы до гола не считаются; без гола = минус. 45′ и 46′ не сигналятся.</p>
    </div>
    <div id="imp-rep-body"><div class="empty-state" style="padding:24px 0"><p class="muted">Нажмите «Считать»</p></div></div>`;

  let selectedPeriod = 'day';
  content.querySelectorAll('.imp-rep-period').forEach(btn => {
    btn.onclick = () => {
      selectedPeriod = btn.getAttribute('data-period') || 'day';
      content.querySelectorAll('.imp-rep-period').forEach(b => {
        const on = b === btn;
        b.classList.toggle('on', on);
        b.classList.toggle('btn-primary', on);
        b.classList.toggle('btn-secondary', !on);
      });
    };
  });

  const run = async () => {
    const body = document.getElementById('imp-rep-body');
    const date = (document.getElementById('imp-rep-date') || {}).value || '';
    body.innerHTML = `<div class="empty-state" style="padding:24px 0"><div class="icon">${ico('clock')}</div><p>Считаем…</p></div>`;
    try {
      const q = new URLSearchParams({ period: selectedPeriod, date });
      const rep = await api('/admin/impulse-report?' + q.toString(), { noCache: true, timeoutMs: 180000 });
      const win = rep.goal_window != null ? rep.goal_window : 20;
      const pctCol = (p) => {
        p = Math.max(0, Math.min(100, Number(p) || 0));
        if (p >= 50) return '#00e6b4';
        if (p >= 35) return '#fbbf24';
        if (p > 0) return '#f87171';
        return '#64748b';
      };
      const hitBar = (pct, label) => {
        const p = Math.max(0, Math.min(100, Number(pct) || 0));
        const col = pctCol(p);
        return `<div class="imp-hit-row">
          <div class="imp-hit-meta"><span>${label}</span><span class="imp-hit-pct" style="color:${col}">${p}%</span></div>
          <div class="imp-hit-track"><div class="imp-hit-fill" style="width:${p}%;background:${col}"></div></div>
        </div>`;
      };
      const halfTable = (halves) => {
        const h = halves || {};
        const h1 = h[1] || h['1'] || {};
        const h2 = h[2] || h['2'] || {};
        const cell = (block) => {
          const b = (block || {}).all || (block || {})['all'] || {};
          const c = b.count || 0;
          const w = b.with_goal || 0;
          const p = b.hit_pct != null ? b.hit_pct : (c ? Math.round(1000 * w / c) / 10 : 0);
          if (!c) return `<span class="muted">—</span>`;
          return `<span style="color:${pctCol(p)};font-weight:700">${p}%</span> <span class="muted">(${w}/${c})</span>`;
        };
        return `<table class="danger-table imp-rep-half-table">
          <thead><tr><th></th><th style="text-align:center">1 тайм</th><th style="text-align:center">2 тайм</th></tr></thead>
          <tbody><tr>
            <td>Проход.</td>
            <td style="text-align:center">${cell(h1)}</td>
            <td style="text-align:center">${cell(h2)}</td>
          </tr></tbody>
        </table>`;
      };
      const treeHtml = (rep.countries || []).length
        ? (rep.countries || []).map(c => {
            const leagues = (c.leagues || []).map(L => `
              <div class="imp-geo-block imp-league-under">
                <div class="imp-geo-head">
                  ${L.league_logo ? `<img class="imp-league-logo" src="${escapeHtml(L.league_logo)}" alt="" onerror="this.style.display='none'" />` : ''}
                  <span>${escapeHtml(L.league || '')}</span>
                  <span class="muted">· ${L.count || 0}</span>
                </div>
                ${hitBar(L.hit_pct, 'Лига')}
                
              </div>`).join('');
            return `<div class="card stats-card" style="margin-bottom:12px">
              <div class="imp-geo-head" style="margin-bottom:8px">
                ${c.flag || ''} <b>${escapeHtml(c.country || '')}</b>
                <span class="muted">· ${c.count || 0} сигн.</span>
              </div>
              ${hitBar(c.hit_pct, 'Страна')}
              
              <div class="danger-table-title" style="margin-top:10px">Лиги / кубки</div>
              ${leagues || '<div class="muted" style="font-size:12px">Нет лиг</div>'}
            </div>`;
          }).join('')
        : `<div class="empty-state"><p class="muted">Нет данных за период</p></div>`;
      body.innerHTML = `
        <div class="card stats-card" style="margin-bottom:12px;font-size:12px;line-height:1.45">
          <div><b>${escapeHtml(rep.period_label || selectedPeriod)}</b>: ${escapeHtml(rep.start_day || '')} → ${escapeHtml(rep.end_day || '')}</div>
          <div>Сигналов: <b>${rep.signals_total || 0}</b> · голов в окне: <b>${rep.credited_goals || 0}</b> · окно <b>${win}′</b></div>
          <div class="muted" style="margin-top:6px;font-size:11px">+ гол ≤${win}′ в том же тайме · сигналы до гола после успеха не считаются · без гола = минус · 45/46′ не сигналим</div>
        </div>
        ${treeHtml}`;
    } catch (e) {
      body.innerHTML = `<div class="empty-state"><div class="icon">${ico('warning')}</div><p>${escapeHtml(e.message || 'Ошибка')}</p></div>`;
    }
  };
  const btn = document.getElementById('imp-rep-run');
  if (btn) btn.onclick = run;
  run();
}

async function renderAdminAuto() {
  let auto = {
    enabled: false, clear_time: '06:00', parse_time: '08:00',
    ranks: [1,2,3,4,5,6,7,8], key_leagues: [], league_options: [],
    max_matches: 80, last_clear: null, last_parse: null,
  };
  try { auto = { ...auto, ...(await api('/admin/auto-daily')) }; } catch (_) {}
  const autoOn = !!auto.enabled;
  const ranksSet = new Set((auto.ranks || []).map(Number));
  const rankChips = [1,2,3,4,5,6,7,8,9].map(r => {
    // P9 = Кубок: национальные кубки чемпионатов (FA Cup, Copa del Rey, Кубок России и т.д.)
    const label = r === 9 ? '🏆 Кубок' : `P${r}`;
    return `<span class="auto-rank-chip ${ranksSet.has(r) ? 'on' : ''}" data-rank="${r}" onclick="toggleAutoChip(this)" title="${r === 9 ? 'Кубки чемпионатов стран' : 'Ранг ' + r}">${label}</span>`;
  }).join('');

  // Лиги рангов 7–8: группировка по стране
  const leagueOpts = Array.isArray(auto.league_options) ? auto.league_options : [];
  window._autoLeagueOpts = leagueOpts;
  const byCountry = {};
  leagueOpts.forEach(opt => {
    const country = (opt && opt.country) ? String(opt.country) : 'Прочее';
    if (!byCountry[country]) byCountry[country] = [];
    byCountry[country].push(opt);
  });
  const countries = Object.keys(byCountry).sort((a, b) => a.localeCompare(b, 'ru'));
  window._autoLeaguesByCountry = byCountry;

  const selectedKeys = (auto.key_leagues || []).map(s => String(s));
  window._autoKeyLeagues = selectedKeys.slice();

  const countryOptions = countries.map(c =>
    `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`
  ).join('');

  const selectedHtml = selectedKeys.map(name => {
    const opt = leagueOpts.find(o => String(o.name || o).toLowerCase() === String(name).toLowerCase());
    const label = opt ? (opt.label || opt.name || name) : name;
    return `<span class="auto-key-chip" data-league="${escapeHtml(name)}"><span>${escapeHtml(label)}</span><button type="button" onclick="removeAutoKeyLeague(this)" aria-label="Удалить">×</button></span>`;
  }).join('');

  const metaBits = [];
  if (auto.last_clear) metaBits.push('очистка: ' + auto.last_clear);
  if (auto.last_parse) metaBits.push('парсер: ' + auto.last_parse);

  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin',null,false)">← Назад</button>
    <div class="card-title">⏰ Авто каждый день</div>
    <div class="auto-settings-card">
      <div class="switch-row">
        <div class="auto-title" style="margin:0">Включено (МСК)</div>
        <div id="auto-switch" class="switch-toggle ${autoOn ? 'on' : ''}" onclick="toggleAutoSwitch()" title="Вкл/Выкл"></div>
      </div>
      <div class="auto-grid" style="margin-top:8px">
        <div class="auto-field">
          <label>Очистка</label>
          <input type="time" id="auto-clear-time" value="${escapeHtml(auto.clear_time || '06:00')}" />
        </div>
        <div class="auto-field">
          <label>Парсер</label>
          <input type="time" id="auto-parse-time" value="${escapeHtml(auto.parse_time || '08:00')}" />
        </div>
        <div class="auto-field" style="grid-column:1/-1">
          <label>Макс. матчей</label>
          <input type="number" id="auto-max-matches" min="1" max="300" value="${Number(auto.max_matches) || 80}" />
        </div>
      </div>
      <label style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.03em">Ранги</label>
      <div class="auto-ranks">${rankChips}</div>
      <label style="font-size:10px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.03em">Ключевые лиги (ранг 7–8)</label>
      <div class="auto-key-leagues-block">
        <div class="auto-key-row">
          <div class="auto-field">
            <label>Страна</label>
            <select id="auto-key-country" onchange="onAutoKeyCountryChange()">
              <option value="">— страна —</option>
              ${countryOptions}
            </select>
          </div>
          <div class="auto-field">
            <label>Лига</label>
            <select id="auto-key-league">
              <option value="">— лига —</option>
            </select>
          </div>
          <button type="button" class="btn btn-outline-white auto-key-add" onclick="addAutoKeyLeague()">+</button>
        </div>
        <div class="auto-key-selected" id="auto-key-selected">${selectedHtml || '<span style="font-size:11px;color:var(--text-muted)">Не выбрано</span>'}</div>
      </div>
      <button class="btn btn-primary" style="width:100%;margin:0" onclick="saveAutoDailySettings()">💾 Сохранить</button>
      <div class="auto-meta">${metaBits.length ? escapeHtml(metaBits.join(' · ')) : 'Ещё не запускалось'}</div>
    </div>`;
}

function onAutoKeyCountryChange() {
  const sel = document.getElementById('auto-key-country');
  const leagueSel = document.getElementById('auto-key-league');
  if (!sel || !leagueSel) return;
  const country = sel.value;
  const list = (window._autoLeaguesByCountry && window._autoLeaguesByCountry[country]) || [];
  leagueSel.innerHTML = '<option value="">— лига —</option>' + list.map(opt => {
    const name = opt.name || '';
    const label = opt.name || opt.label || name;
    return `<option value="${escapeHtml(name)}">${escapeHtml(label)}</option>`;
  }).join('');
}

function addAutoKeyLeague() {
  const leagueSel = document.getElementById('auto-key-league');
  if (!leagueSel || !leagueSel.value) return;
  const name = leagueSel.value;
  if (!window._autoKeyLeagues) window._autoKeyLeagues = [];
  const low = name.toLowerCase();
  if (window._autoKeyLeagues.some(k => String(k).toLowerCase() === low)) return;
  window._autoKeyLeagues.push(name);
  _refreshAutoKeySelected();
}

function removeAutoKeyLeague(btn) {
  const chip = btn && btn.closest ? btn.closest('.auto-key-chip') : null;
  if (!chip) return;
  const name = chip.dataset.league || '';
  if (!window._autoKeyLeagues) return;
  window._autoKeyLeagues = window._autoKeyLeagues.filter(k => String(k).toLowerCase() !== String(name).toLowerCase());
  _refreshAutoKeySelected();
}

function _refreshAutoKeySelected() {
  const box = document.getElementById('auto-key-selected');
  if (!box) return;
  const opts = window._autoLeagueOpts || [];
  const keys = window._autoKeyLeagues || [];
  if (!keys.length) {
    box.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">Не выбрано</span>';
    return;
  }
  box.innerHTML = keys.map(name => {
    const opt = opts.find(o => String(o.name || o).toLowerCase() === String(name).toLowerCase());
    const label = opt ? (opt.label || opt.name || name) : name;
    return `<span class="auto-key-chip" data-league="${escapeHtml(name)}"><span>${escapeHtml(label)}</span><button type="button" onclick="removeAutoKeyLeague(this)" aria-label="Удалить">×</button></span>`;
  }).join('');
}


async function toggleMaintenance() {
  try {
    const cur = await api('/admin/maintenance');
    const res = await api('/admin/maintenance', {
      method: 'POST',
      body: JSON.stringify({ enabled: !cur.enabled }),
    });
    toast(res.message);
    await renderAdmin();
  } catch (e) { toast(e.message); }
}

async function renderAdminBan(data) {
  const page = (data && data.page != null) ? Number(data.page) : 0;
  const q = (data && data.q) ? String(data.q) : '';
  const qs = q ? '&q=' + encodeURIComponent(q) : '';
  const res = await api('/admin/users?page=' + page + qs);
  const users = res.users || [];
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🚫 Бан пользователей</div>
    <div class="card" style="margin-bottom:10px">
      <input class="textarea" id="ban-search" style="min-height:auto;height:42px;margin-bottom:8px" placeholder="@username или ID" value="${escapeHtml(q)}" />
      <button class="btn btn-primary" style="width:100%" id="ban-search-btn">🔍 Найти</button>
    </div>
    <div class="card" style="padding:0;overflow:hidden">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0;font-size:11px;color:var(--text-muted);padding:8px 12px;border-bottom:1px solid rgba(255,255,255,0.06)">
        <span>Username</span><span>Имя / статус</span>
      </div>`;
  if (!users.length) {
    html += `<div style="padding:16px;text-align:center;color:var(--text-muted);font-size:13px">Никого не найдено</div>`;
  }
  users.forEach(u => {
    const un = u.username ? '@' + u.username : 'ID ' + u.user_id;
    const banTag = u.banned ? ` <span style="color:#f87171;font-size:10px">бан ${escapeHtml(u.ban_label || '')}</span>` : '';
    html += `<div class="match-item ban-user-row" data-uid="${u.user_id}" data-label="${escapeHtml(un)}" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;cursor:pointer">
      <span style="font-weight:600">${escapeHtml(un)}</span>
      <span style="font-size:12px;color:var(--text-secondary)">${escapeHtml(u.first_name || '—')}${banTag}</span>
    </div>`;
  });
  html += `</div>`;
  const tp = res.total_pages || 1;
  if (tp > 1 || q) {
    html += `<div class="pagination" style="margin-top:12px">
      <button class="page-btn" ${page<=0?'disabled':''} onclick="navigate('admin-ban',{page:${page-1},q:${JSON.stringify(q)}})">←</button>
      <span class="page-info">${page+1}/${tp}</span>
      <button class="page-btn" ${page>=tp-1?'disabled':''} onclick="navigate('admin-ban',{page:${page+1},q:${JSON.stringify(q)}})">→</button>
    </div>`;
  }
  content.innerHTML = html;
  const goSearch = () => {
    const v = (document.getElementById('ban-search')?.value || '').trim();
    navigate('admin-ban', { page: 0, q: v });
  };
  document.getElementById('ban-search-btn').onclick = goSearch;
  document.getElementById('ban-search').onkeydown = (e) => { if (e.key === 'Enter') goSearch(); };
  content.querySelectorAll('.ban-user-row').forEach(el => {
    el.onclick = () => showBanOptions(parseInt(el.dataset.uid, 10), el.dataset.label);
  });
}

function showBanOptions(userId, label) {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin-ban')">← Назад</button>
    <div class="card-title">Бан: ${escapeHtml(label)}</div>
    <button class="cat-btn" onclick="doBan(${userId},'1d')">На сутки</button>
    <button class="cat-btn" onclick="doBan(${userId},'7d')">На неделю</button>
    <button class="cat-btn" onclick="doBan(${userId},'30d')">На месяц</button>
    <button class="cat-btn" onclick="doBan(${userId},'90d')">На три месяца</button>
    <button class="cat-btn" onclick="doBan(${userId},'180d')">На полгода</button>
    <button class="cat-btn" onclick="doBan(${userId},'365d')">На год</button>
    <button class="cat-btn" onclick="doBan(${userId},'forever')">Навсегда</button>
    <button class="btn btn-secondary" style="margin-top:8px;width:100%" onclick="doUnban(${userId})">Снять бан</button>`;
}

async function doBan(userId, duration) {
  try {
    const r = await api('/admin/users/ban', { method: 'POST', body: JSON.stringify({ user_id: userId, duration }) });
    toast(r.message);
    navigate('admin-ban');
  } catch (e) { toast(e.message); }
}
async function doUnban(userId) {
  try {
    const r = await api('/admin/users/unban', { method: 'POST', body: JSON.stringify({ user_id: userId }) });
    toast(r.message);
    navigate('admin-ban');
  } catch (e) { toast(e.message); }
}

async function renderAdminParser() {
  const data = await api('/admin/parser/dates');
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🔍 Парсер матчей</div>`;
  if (!data.has_api_key) {
    html += `<div class="empty-state"><div class="icon">⚠️</div><p>BSD_API_KEY не задан</p></div>`;
  } else {
    data.dates.forEach(d => {
      html += `<button class="cat-btn" onclick="parserLoad('${d.value}')">${d.label}</button>`;
    });
  }
  content.innerHTML = html;
}

async function parserLoad(dateStr) {
  content.innerHTML = `<div class="empty-state"><div class="icon">⏳</div><p>Загружаю...</p></div>`;
  try {
    const res = await api('/admin/parser/load', { method: 'POST', body: JSON.stringify({ date: dateStr }) });
    toast(res.message);
    if (!res.total) {
      content.innerHTML = `<button class="btn-back" onclick="navigate('admin-parser')">← Назад</button>
        <div class="empty-state"><p>${escapeHtml(res.message)}</p></div>`;
      return;
    }
    parserLeagues = res.leagues;
    navigate('admin-parser-leagues', { total: res.total, leagues: res.leagues }, false);
  } catch (e) { toast(e.message); navigate('admin-parser', null, false); }
}

async function renderAdminParserLeagues(data) {
  const leagues = data?.leagues || parserLeagues;
  const total = data?.total || 0;
  const rankBtns = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(r =>
    `<button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRank(${r})">${r === 9 ? 'Кубок' : 'P' + r}</button>`
  ).join('');
  let html = `<button class="btn-back" onclick="navigate('admin-parser')">← Назад</button>
    <div class="card-title">📋 Найдено: ${total}</div>
    <div class="parser-rank-bar">
      <div class="parser-rank-label">Добавить по рангу</div>
      <div class="parser-rank-row">${rankBtns}</div>
      <div class="parser-rank-row parser-rank-packs">
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(1,3)">1–3</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(1,5)">1–5</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(1,6)">1–6</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(1,7)">1–7</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(3,5)">3–5</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(3,6)">3–6</button>
        <button type="button" class="btn btn-secondary parser-rank-btn" onclick="parserAddRankRange(3,7)">3–7</button>
        <button type="button" class="btn btn-primary parser-rank-btn" onclick="parserAddAll()">Все ≤80</button>
      </div>
    </div>`;
  leagues.forEach(l => {
    const leagueLogo = l.league_logo
      ? `<img class="league-logo" src="${escapeHtml(l.league_logo)}" alt="" onerror="this.style.display='none'" />`
      : '';
    const navData = JSON.stringify({league_id:l.league_id,league:l.league,country:l.country,flag:l.flag,league_logo:l.league_logo||''});
    html += `<div class="group-card" onclick='navigate("admin-parser-matches", ${navData})'>
      <div class="group-left">
        <span class="group-flag">${l.flag||''}</span>
        <div class="group-info">
          <div class="country-league">
            <span class="cl-country">P${l.priority} ${escapeHtml(l.country)}</span>
            <span class="cl-sep">·</span>
            ${leagueLogo}
            <span class="cl-league">${escapeHtml(l.league)}</span>
          </div>
        </div>
      </div>
      <span class="group-count">${l.count}</span></div>`;
  });
  content.innerHTML = html;
}

async function renderAdminParserMatches(data) {
  if (!data) { navigate('admin-parser'); return; }
  const res = await api(`/admin/parser/matches?league_id=${data.league_id}&page=0`);
  const pLogo = data.league_logo
    ? `<img class="league-logo league-logo-title" src="${escapeHtml(data.league_logo)}" alt="" onerror="this.style.display='none'" />`
    : '';
  let html = `<button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">${data.flag||''} ${escapeHtml(data.country||'')} · ${pLogo}${escapeHtml(data.league||'')}</div>
    <button class="btn btn-primary" onclick="parserAddLeague(${data.league_id})" style="margin-bottom:12px">✅ Всю лигу</button>`;
  res.matches.forEach(m => {
    const time = m.match_time ? `<span class="match-time">${escapeHtml(m.match_time)}</span>` : '<span class="match-time">—</span>';
    html += `<div class="match-item" style="justify-content:space-between">
      ${time}
      ${logosHtml(m.logo_home, m.logo_away)}
      <span class="match-name" style="flex:1">${escapeHtml(m.name)}</span>
      <button class="btn btn-secondary" style="width:auto;margin:0;padding:6px 12px;font-size:12px" onclick="parserAddSingle(${m.id},this)">➕</button>
    </div>`;
  });
  content.innerHTML = html;
}

async function refreshMatchStatsCache() {
  try {
    currentUser = await api('/me', { timeoutMs: 10000 });
  } catch (_) {}
}

async function parserAddAll() {
  try {
    toast('Добавляю матчи…');
    const res = await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'all',limit:80}), timeoutMs: 180000});
    await refreshMatchStatsCache();
    toast(res.message || 'Готово');
  } catch(e){ toast(e.message); }
}
async function parserAddRank(rank) {
  try {
    toast(`Добавляю ранг ${rank}…`);
    const res = await api('/admin/parser/add', {
      method: 'POST',
      body: JSON.stringify({ mode: 'priority', priority: rank }),
      timeoutMs: 180000,
    });
    await refreshMatchStatsCache();
    toast(res.message || `Ранг ${rank}: готово`);
  } catch (e) { toast(e.message); }
}
async function parserAddRankRange(fromRank, toRank) {
  try {
    toast(`Добавляю ранги ${fromRank}–${toRank}…`);
    const res = await api('/admin/parser/add', {
      method: 'POST',
      body: JSON.stringify({ mode: 'priority', from_rank: fromRank, to_rank: toRank }),
      timeoutMs: 180000,
    });
    await refreshMatchStatsCache();
    toast(res.message || `Ранги ${fromRank}–${toRank}: готово`);
  } catch (e) { toast(e.message); }
}
async function parserAddLeague(id) {
  try {
    toast('Добавляю лигу…');
    const res = await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'league',league_id:id}), timeoutMs: 180000});
    await refreshMatchStatsCache();
    toast(res.message || 'Готово');
  } catch(e){ toast(e.message); }
}
async function parserAddSingle(id, btn) {
  try {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    const res = await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'single',parsed_id:id}), timeoutMs: 90000});
    await refreshMatchStatsCache();
    toast(res.message || 'Готово');
    if (res.ok && btn) { btn.textContent='✅'; btn.disabled=true; }
    else if (btn) { btn.disabled=false; btn.textContent='➕'; }
  } catch(e){
    toast(e.message);
    if (btn) { btn.disabled=false; btn.textContent='➕'; }
  }
}

async function renderAdminManual() {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">✍️ Матч вручную</div><div class="card">
    <input class="textarea" id="m-time" style="min-height:auto;height:44px;margin-bottom:8px" placeholder="Время 18:30 или -" />
    <input class="textarea" id="m-name" style="min-height:auto;height:44px;margin-bottom:8px" placeholder="Спартак — Зенит" />
    <input class="textarea" id="m-country" style="min-height:auto;height:44px;margin-bottom:8px" placeholder="Russia" />
    <input class="textarea" id="m-league" style="min-height:auto;height:44px;margin-bottom:8px" placeholder="Russian Premier League" />
    <textarea class="textarea" id="m-text" placeholder="Текст прогноза"></textarea>
    <button class="btn btn-primary" onclick="manualAddMatch()">✅ Добавить</button></div>`;
}
async function manualAddMatch() {
  const name=document.getElementById('m-name').value.trim();
  const country=document.getElementById('m-country').value.trim();
  const league=document.getElementById('m-league').value.trim();
  let match_time=document.getElementById('m-time').value.trim(); if(match_time==='-') match_time='';
  const text=document.getElementById('m-text').value.trim();
  if(!name||!country||!league){toast('Заполните поля');return;}
  try{ toast((await api('/admin/matches/manual',{method:'POST',body:JSON.stringify({name,country,league,match_time,text})})).message); navigate('admin'); }
  catch(e){toast(e.message);}
}

async function renderAdminTexts() {
  const data = await api('/admin/matches/no-text');
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button><div class="card-title">📝 Тексты</div>`;
  if (!data.matches.length) html += `<div class="empty-state"><p>Все матчи с текстом</p></div>`;
  else data.matches.forEach(m => {
    html += `<div class="match-item" onclick="navigate('admin-text-edit',${m.id})">
      <span class="match-name">📝 ${m.flag||''} ${escapeHtml(m.name)}</span></div>`;
  });
  content.innerHTML = html;
}
async function renderAdminTextEdit(id) {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin-texts')">← Назад</button>
    <div class="card-title">Текст #${id}</div>
    <textarea class="textarea" id="edit-text"></textarea>
    <button class="btn btn-primary" onclick="saveMatchText(${id})">💾 Сохранить</button>`;
}
async function saveMatchText(id) {
  const text = document.getElementById('edit-text').value.trim();
  if (!text) { toast('Пусто'); return; }
  try { toast((await api(`/admin/matches/${id}/text`,{method:'PUT',body:JSON.stringify({match_id:id,text})})).message); navigate('admin-texts'); }
  catch(e){toast(e.message);}
}

async function renderAdminDelete() {
  const data = await api('/admin/matches');
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🗑 Удалить</div>
    <button class="btn btn-danger" onclick="if(confirm('Все?'))api('/admin/matches',{method:'DELETE'}).then(r=>{toast(r.message);renderAdminDelete()})" style="margin-bottom:12px">Очистить все</button>`;
  data.matches.forEach(m => {
    html += `<div class="match-item" style="justify-content:space-between">
      <span class="match-name">${escapeHtml(m.name)}</span>
      <button class="btn btn-danger" style="width:auto;margin:0;padding:6px 10px" onclick="api('/admin/matches/${m.id}',{method:'DELETE'}).then(r=>{toast(r.message);renderAdminDelete()})">🗑</button></div>`;
  });
  content.innerHTML = html;
}

async function renderAdminExpress() {
  const data = await api('/admin/express');
  let picks = '<p style="color:var(--text-muted)">Пусто</p>';
  if (data.picks.length) {
    picks = data.picks.map(p => {
      const time = p.match_time ? `<span class="match-time">${escapeHtml(p.match_time)}</span>` : '';
      return `<div class="express-pick">
        <div class="express-pick-header">
          ${time}
          ${logosHtml(p.logo_home, p.logo_away)}
          <div class="name" style="flex:1">${escapeHtml(p.name)}</div>
        </div>
        <div class="pred">${escapeHtml(p.prediction)} | ${escapeHtml(p.coef)}</div>
      </div>`;
    }).join('');
  }
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">${ico('bolt', 'ico-white')} Экспресс</div><div class="express-card">${picks}</div>
    <button class="btn btn-primary" onclick="navigate('admin-express-add')">➕ Добавить</button>
    <button class="btn btn-danger" onclick="if(confirm('Очистить?'))api('/admin/express',{method:'DELETE'}).then(r=>{toast(r.message);renderAdminExpress()})">🗑 Очистить</button>
    <button class="btn btn-secondary" onclick="navigate('admin-result-add')">📝 Результат</button>
    <button class="btn btn-secondary" onclick="navigate('admin-results')">Результаты</button>`;
}

async function renderAdminExpressAdd(data) {
  const res = await api('/admin/matches');
  const matches = res.matches || [];
  const groups = {};
  matches.forEach(m => {
    const key = `${m.country || '—'} · ${m.league || '—'}`;
    if (!groups[key]) groups[key] = [];
    groups[key].push(m);
  });
  const selectedKey = data && data.leagueKey;
  if (!selectedKey) {
    let html = `<button class="btn-back" onclick="navigate('admin-express')">← Назад</button>
      <div class="card-title">Страна · лига</div>`;
    const keys = Object.keys(groups).sort();
    if (!keys.length) {
      html += `<div class="empty-state"><p>Нет матчей в базе</p></div>`;
    } else {
      keys.forEach(k => {
        const n = groups[k].length;
        html += `<button type="button" class="cat-btn express-league-btn" data-league-key="${escapeHtml(k)}">
          ${escapeHtml(k)} <span style="color:var(--text-muted);font-size:12px">(${n})</span>
        </button>`;
      });
    }
    content.innerHTML = html;
    content.querySelectorAll('.express-league-btn').forEach(btn => {
      btn.onclick = () => navigate('admin-express-add', { leagueKey: btn.dataset.leagueKey });
    });
    return;
  }
  const list = groups[selectedKey] || [];
  let html = `<button class="btn-back" onclick="navigate('admin-express-add')">← Назад</button>
    <div class="card-title">${escapeHtml(selectedKey)}</div>`;
  list.forEach(m => {
    const time = m.match_time ? `<span class="match-time">${escapeHtml(m.match_time)}</span>` : '<span class="match-time">—</span>';
    const safeName = escapeHtml(m.name).replace(/'/g, "\\'");
    html += `<div class="match-item" onclick="showExpressForm(${m.id},'${safeName}')">
      ${time}
      ${logosHtml(m.logo_home, m.logo_away)}
      <span class="match-name">${escapeHtml(m.name)}</span>
    </div>`;
  });
  content.innerHTML = html;
}

function showExpressForm(id, name) {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin-express-add')">← Назад</button>
    <div class="card-title">${name}</div><div class="card">
    <input class="textarea" id="ex-pred" style="min-height:auto;height:44px;margin-bottom:8px" placeholder="Прогноз П1" />
    <input class="textarea" id="ex-coef" style="min-height:auto;height:44px" placeholder="1.85" />
    <button class="btn btn-primary" onclick="addExpressPick(${id})">✅</button></div>`;
}
async function addExpressPick(id) {
  const prediction=document.getElementById('ex-pred').value.trim();
  const coef=document.getElementById('ex-coef').value.trim();
  if(!prediction||!coef){toast('Заполните');return;}
  try{toast((await api('/admin/express',{method:'POST',body:JSON.stringify({match_id:id,prediction,coef})})).message);navigate('admin-express');}
  catch(e){toast(e.message);}
}

async function renderAdminResultAdd() {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin-express')">← Назад</button>
    <div class="card-title">Результат</div>
    <button class="cat-btn" onclick="showResultCoefs('double')">🎲 Двойник</button>
    <button class="cat-btn" onclick="showResultCoefs('triple')">🎲 Тройник</button>`;
}
function showResultCoefs(type) {
  const n = type==='double'?2:3;
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin-result-add')">← Назад</button>
    <div class="card"><input class="textarea" id="res-coefs" style="min-height:auto;height:44px" placeholder="${n} кф через пробел" />
    <div style="display:flex;gap:8px;margin-top:12px">
      <button class="btn btn-primary" style="flex:1" onclick="saveResult('${type}','win')">✅</button>
      <button class="btn btn-danger" style="flex:1" onclick="saveResult('${type}','lose')">❌</button>
    </div></div>`;
}
async function saveResult(type, result) {
  const parts = document.getElementById('res-coefs').value.trim().split(/\s+/);
  const expected = type==='double'?2:3;
  if (parts.length!==expected){toast('Нужно '+expected+' кф');return;}
  const coefs = parts.map(p=>parseFloat(p.replace(',','.')));
  if (coefs.some(c=>isNaN(c)||c<=1)){toast('Кф > 1');return;}
  try{toast((await api('/admin/results',{method:'POST',body:JSON.stringify({type,coefs,result})})).message);navigate('admin-express');}
  catch(e){toast(e.message);}
}

async function renderAdminResults() {
  const data = await api('/admin/results');
  let html = `<button class="btn-back" onclick="navigate('admin-express')">← Назад</button><div class="card-title">Результаты</div>`;
  data.results.forEach(r => {
    html += `<div class="match-item" style="justify-content:space-between">
      <span>${r.result_emoji} ${r.type_label} ${Number(r.coef).toFixed(2)}</span>
      <button class="btn btn-danger" style="width:auto;margin:0;padding:6px 10px" onclick="api('/admin/results/${r.id}',{method:'DELETE'}).then(x=>{toast(x.message);renderAdminResults()})">🗑</button></div>`;
  });
  content.innerHTML = html;
}

async function renderAdminPremium() {
  content.innerHTML = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">⭐ Premium</div>

    <button class="cat-btn" id="prem-all-forever">⭐ Выдать всем Premium (навсегда)</button>
    <button class="btn btn-danger" id="prem-all-remove" style="margin-bottom:12px">Снять со всех Premium</button>

    <div class="card">
      <div class="card-title" style="font-size:14px">Выдать Premium пользователю</div>
      <input class="textarea" id="prem-user" style="min-height:auto;height:40px;margin-bottom:8px" placeholder="@username или Telegram ID" />
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
        <button class="btn btn-primary" style="width:auto;margin:0;padding:8px 12px;font-size:12px" data-days="7">7 дней</button>
        <button class="btn btn-primary" style="width:auto;margin:0;padding:8px 12px;font-size:12px" data-days="30">30 дней</button>
        <button class="btn btn-primary" style="width:auto;margin:0;padding:8px 12px;font-size:12px" data-days="90">90 дней</button>
        <button class="btn btn-gold" style="width:auto;margin:0;padding:8px 12px;font-size:12px" data-days="forever">Навсегда</button>
      </div>
      <p style="color:var(--text-muted);font-size:12px;margin:0">Введите @username или ID и нажмите срок</p>
      <div id="prem-user-result" style="margin-top:8px"></div>
    </div>

    <div class="card">
      <div class="card-title" style="font-size:14px">Снять Premium</div>
      <input class="textarea" id="prem-rm-user" style="min-height:auto;height:40px" placeholder="@username или Telegram ID" />
      <button class="btn btn-danger" id="prem-rm-btn" style="margin-top:8px">Снять Premium</button>
    </div>`;

  document.getElementById('prem-all-forever').onclick = async () => {
    if (!confirm('Выдать Premium навсегда всем Free-пользователям?')) return;
    try {
      const r = await api('/admin/premium/all', { method: 'POST', body: JSON.stringify({ days: 'forever' }) });
      toast(r.message);
    } catch (e) { toast(e.message); }
  };
  document.getElementById('prem-all-remove').onclick = async () => {
    if (!confirm('Снять Premium со всех пользователей?')) return;
    try {
      const r = await api('/admin/premium/all', { method: 'DELETE' });
      toast(r.message);
    } catch (e) { toast(e.message); }
  };

  // Grant by username/ID — one click for days
  content.querySelectorAll('[data-days]').forEach(btn => {
    btn.onclick = async () => {
      const q = document.getElementById('prem-user').value.trim();
      if (!q) { toast('Введите username или ID'); return; }
      const daysVal = btn.dataset.days;
      const days = daysVal === 'forever' ? 'forever' : parseInt(daysVal, 10);
      const box = document.getElementById('prem-user-result');
      box.innerHTML = '<p style="color:var(--text-muted);font-size:13px">Выдаём...</p>';
      try {
        // First try direct by-username endpoint
        const r = await api('/admin/premium/by-username', {
          method: 'POST',
          body: JSON.stringify({ username: q, days })
        });
        box.innerHTML = `<p style="color:var(--accent);font-size:13px;margin-top:6px">${escapeHtml(r.message)}</p>`;
        toast(r.message);
      } catch (e) {
        // Fallback: search then grant by user_id
        try {
          const data = await api('/admin/users/search?q=' + encodeURIComponent(q));
          if (!data.users || !data.users.length) {
            box.innerHTML = `<p style="color:var(--red);font-size:13px;margin-top:6px">Пользователь не найден</p>`;
            toast('Пользователь не найден');
            return;
          }
          const u = data.users[0];
          const daysParam = days === 'forever' ? 0 : days;
          const r2 = await api('/admin/premium/' + u.user_id + '?days=' + daysParam, { method: 'POST' });
          const msg = r2.message || ('✅ Premium выдан ' + (u.username ? '@' + u.username : 'ID ' + u.user_id));
          box.innerHTML = `<p style="color:var(--accent);font-size:13px;margin-top:6px">${escapeHtml(msg)}</p>`;
          toast(msg);
        } catch (e2) {
          box.innerHTML = `<p style="color:var(--red);font-size:13px;margin-top:6px">${escapeHtml(e2.message)}</p>`;
          toast(e2.message);
        }
      }
    };
  });

  document.getElementById('prem-rm-btn').onclick = async () => {
    const username = document.getElementById('prem-rm-user').value.trim();
    if (!username) { toast('Введите username или ID'); return; }
    if (!confirm('Снять Premium с ' + username + '?')) return;
    try {
      const r = await api('/admin/premium/remove-by-username', {
        method: 'POST',
        body: JSON.stringify({ username })
      });
      toast(r.message);
    } catch (e) {
      // fallback via search + delete by id
      try {
        const data = await api('/admin/users/search?q=' + encodeURIComponent(username));
        if (!data.users || !data.users.length) {
          toast('Пользователь не найден');
          return;
        }
        const r2 = await api('/admin/premium/' + data.users[0].user_id, { method: 'DELETE' });
        toast(r2.message || '✅ Premium снят');
      } catch (e2) {
        toast(e2.message);
      }
    }
  };
}

async function renderAdminLogs() {
  const data = await api('/admin/logs');
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button><div class="card-title">📋 Логи</div><div class="card">`;
  data.logs.forEach(l => {
    html += `<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:13px">
      <span style="color:var(--text-muted)">[${escapeHtml(l.created_at)}]</span> <b>${escapeHtml(l.action)}</b>: ${escapeHtml(l.details)}</div>`;
  });
  content.innerHTML = html + '</div>';
}


/* ---------- SUPPORT (user) ---------- */
async function renderSupport() {
  let threadHtml = '';
  try {
    const th = await api('/support/thread');
    const msgs = th.messages || [];
    if (msgs.length) {
      const supportLineIco = `<svg class="support-line-ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h10"/></svg>`;
      threadHtml = `<div class="card" id="support-msgs">`;
      msgs.forEach(m => {
        const cls = m.sender === 'admin' ? 'admin' : 'user';
        const who = m.sender === 'admin' ? 'Поддержка' : 'Вы';
        const line = m.sender === 'user' ? supportLineIco : '';
        threadHtml += `<div class="support-msg ${cls}">
          <div class="meta">${line}<span>${who} · ${escapeHtml(m.created_at || '')}</span></div>
          <div>${escapeHtml(m.text)}</div>
        </div>`;
      });
      threadHtml += `</div>`;
    }
  } catch (_) {}
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('account',null,false)">← Назад</button>
    <div class="card-title">${ico('support', 'ico-white')} Техподдержка</div>
    <p style="color:var(--text-secondary);font-size:13px;margin-bottom:8px">Выберите тему обращения:</p>
    <div class="category-grid" style="margin-bottom:14px">
      <button class="category-btn" data-cat="bug">${ico('bug', 'ico-white')} Баг / Ошибка</button>
      <button class="category-btn" data-cat="idea">${ico('idea', 'ico-white')} Предложение</button>
      <button class="category-btn" data-cat="pay">${ico('card', 'ico-white')} Оплата</button>
      <button class="category-btn" data-cat="q">${ico('question', 'ico-white')} Вопрос</button>
    </div>
    ${threadHtml || '<div class="muted" style="font-size:12px;text-align:center;padding:8px 0">Диалог появится после первого сообщения</div>'}`;
  content.querySelectorAll('.category-btn').forEach(btn => {
    btn.onclick = () => navigate('support-form', { category: btn.dataset.cat, label: btn.textContent.trim() });
  });
}

async function renderSupportForm(data) {
  if (!data || !data.category) { navigate('support'); return; }
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('support',null,false)">← Назад</button>
    <div class="card-title">🛟 ${escapeHtml(data.label || 'Обращение')}</div>
    <div class="card">
      <div class="support-cat-badge">${escapeHtml(data.label || data.category || '')}</div>
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Опишите проблему или вопрос — тема «${escapeHtml(data.label || '')}» будет видна в переписке</p>
      <textarea class="textarea" id="support-text" placeholder="Ваше сообщение..."></textarea>
      <button class="btn btn-outline-white support-send-btn" id="support-send"><svg class="support-send-ico" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#ffffff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h16a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1V7a1 1 0 011-1z"/><path d="M3.5 7.5l8.5 6 8.5-6"/></svg><span>Отправить</span></button>
    </div>`;
  document.getElementById('support-send').onclick = async () => {
    const text = document.getElementById('support-text').value.trim();
    if (!text) { toast('Введите текст сообщения'); return; }
    const btn = document.getElementById('support-send');
    btn.disabled = true;
    try {
      const res = await api('/support', {
        method: 'POST',
        body: JSON.stringify({ category: data.category, text })
      });
      toast(res.message || '✅ Текст отправлен Администрации');
      setTimeout(() => navigate('support'), 800);
    } catch (e) {
      toast(e.message);
      btn.disabled = false;
    }
  };
}

/* ---------- SUPPORT (admin) ---------- */
async function renderAdminSupport() {
  const data = await api('/admin/support');
  let html = `<button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">${ico('support', 'ico-white')} Техподдержка</div>`;
  if (!data.threads.length) {
    html += `<div class="empty-state"><div class="icon">💬</div><p>Обращений пока нет</p></div>`;
  } else {
    data.threads.forEach(t => {
      const badge = (t.unread > 0) ? `<span class="admin-badge">${t.unread}</span>` : '';
      html += `<div class="cat-btn" data-tid="${t.thread_id}">
        <div style="flex:1">
          <div style="font-weight:600">${escapeHtml(t.user_name)} · ${escapeHtml(t.category_label || t.category)}</div>
          <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(t.created_at || '')}</div>
        </div>
        ${badge}<span style="font-size:12px;color:var(--text-muted);margin-left:6px">›</span>
      </div>`;
    });
  }
  content.innerHTML = html;
  content.querySelectorAll('.cat-btn[data-tid]').forEach(el => {
    el.onclick = () => navigate('admin-support-thread', {
      thread_id: parseInt(el.dataset.tid),
      title: el.querySelector('div > div')?.textContent || ''
    });
  });
}

async function renderAdminSupportThread(data) {
  if (!data || !data.thread_id) { navigate('admin-support'); return; }
  const res = await api('/admin/support/' + data.thread_id);
  let html = `<button class="btn-back" onclick="navigate('admin-support',null,false)">← Назад</button>
    <div class="card-title">💬 Переписка #${data.thread_id}</div>
    <div class="card" id="support-msgs">`;
  if (!res.messages.length) {
    html += `<p style="color:var(--text-muted);font-size:13px">Сообщений нет</p>`;
  } else {
    const supportLineIco = `<svg class="support-line-ico" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h10"/></svg>`;
    res.messages.forEach(m => {
      const cls = m.sender === 'admin' ? 'admin' : 'user';
      const who = m.sender === 'admin' ? 'Админ' : 'Пользователь';
      const line = m.sender === 'user' ? supportLineIco : '';
      html += `<div class="support-msg ${cls}">
        <div class="meta">${line}<span>${who} · ${escapeHtml(m.created_at || '')}</span></div>
        <div>${escapeHtml(m.text)}</div>
      </div>`;
    });
  }
  html += `</div>
    <div class="card">
      <textarea class="textarea" id="admin-reply" placeholder="Ответ пользователю..." style="min-height:70px"></textarea>
      <button class="btn btn-primary" id="admin-reply-btn">📤 Ответить</button>
      <button class="btn btn-danger" id="admin-clear-thread">🗑 Удалить переписку</button>
    </div>`;
  content.innerHTML = html;
  document.getElementById('admin-reply-btn').onclick = async () => {
    const text = document.getElementById('admin-reply').value.trim();
    if (!text) { toast('Введите ответ'); return; }
    try {
      const r = await api('/admin/support/' + data.thread_id + '/reply', {
        method: 'POST',
        body: JSON.stringify({ text })
      });
      toast(r.message);
      renderAdminSupportThread(data);
    } catch (e) { toast(e.message); }
  };
  document.getElementById('admin-clear-thread').onclick = async () => {
    if (!confirm('Удалить переписку?')) return;
    try {
      await api('/admin/support/' + data.thread_id, { method: 'DELETE' });
      toast('Удалено');
      navigate('admin-support');
    } catch (e) { toast(e.message); }
  };
}

function getStartParam() {
  let sp = (tg?.initDataUnsafe?.start_param || '').trim();
  if (!sp) {
    try {
      const u = new URL(location.href);
      sp = (u.searchParams.get('tgWebAppStartParam')
        || u.searchParams.get('startapp')
        || u.searchParams.get('start_param')
        || '').trim();
    } catch (_) {}
  }
  return sp;
}

/** Deep-link: match_123 или match_123_impulse → { id, tab } */
function getDeepMatchLink() {
  let id = null;
  let tab = null;
  try {
    const u = new URL(location.href);
    const q = u.searchParams.get('match_id');
    if (q && /^\d+$/.test(q)) id = parseInt(q, 10);
    const t = (u.searchParams.get('tab') || '').trim().toLowerCase();
    if (t === 'impulse') tab = 'impulse';
  } catch (_) {}
  const sp = getStartParam();
  const mImp = /^match_(\d+)_impulse$/i.exec(sp || '');
  if (mImp) {
    id = parseInt(mImp[1], 10);
    tab = 'impulse';
  } else {
    const m = /^match_(\d+)$/i.exec(sp || '');
    if (m) id = parseInt(m[1], 10);
  }
  if (!id) return null;
  return { id, tab };
}
function getDeepMatchId() {
  const link = getDeepMatchLink();
  return link ? link.id : null;
}

async function claimReferralIfNeeded() {
  try {
    const sp = getStartParam();
    if (!sp || /^match_/i.test(sp)) return;
    await api('/referral/claim', {
      method: 'POST',
      body: JSON.stringify({ start_param: sp }),
      noCache: true,
    });
  } catch (_) {}
}

async function init() {
  const loader = document.getElementById('loader');
  const main = document.getElementById('main');
  try {
    if (!window.Telegram?.WebApp?.initData) {
      // allow brief wait for Telegram bridge
      await new Promise(r => setTimeout(r, 400));
    }
    // рефералка: start_param из deep-link → +3 дня пригласившему
    await claimReferralIfNeeded();
    currentUser = await api('/me', { timeoutMs: 15000, noCache: true });
    const u = currentUser.user;
    updateHeaderUser(u);
    bindNav();
    if (loader) loader.classList.add('hidden');
    if (main) main.classList.remove('hidden');
    const deepLink = getDeepMatchLink();
    if (deepLink && deepLink.id) {
      navigate('match-detail', deepLink.tab ? { id: deepLink.id, tab: deepLink.tab } : deepLink.id, false);
    } else {
      navigate('home', null, false);
    }
  } catch (e) {
    console.error('init error', e);
    if (loader) {
      loader.innerHTML = `<div style="text-align:center;padding:40px">
        <div style="font-size:48px;margin-bottom:8px">⚠️</div>
        <p style="color:var(--text-secondary)">${escapeHtml(e.message || 'Ошибка загрузки')}</p>
        <p style="color:var(--text-muted);font-size:13px;margin-top:12px">Откройте через Telegram-бот</p>
        <button class="btn btn-primary" style="margin-top:16px" onclick="location.reload()">Повторить</button>
      </div>`;
      loader.classList.remove('hidden');
    }
    if (main) main.classList.add('hidden');
  }
}
init();
