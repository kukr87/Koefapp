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

function getInitData() { return tg?.initData || ''; }

async function api(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Telegram-Init-Data': getInitData(),
    ...(options.headers || {}),
  };
  const res = await fetch(API + path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.message || 'Ошибка сервера');
  }
  return res.json();
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
  chart: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5"/><path d="M4 19h16"/><path d="M8 16v-5"/><path d="M12 16V8"/><path d="M16 16v-3"/></svg>',
  star: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2.5 6.5L21 10l-5 4.5L17.5 21 12 17.5 6.5 21 8 14.5 3 10l6.5-.5L12 3z"/></svg>',
  lock: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/></svg>',
  ball: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a12 12 0 010 18"/><path d="M12 3a12 12 0 000 18"/><path d="M3 12h18"/></svg>',
  heart: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-7-4.5-7-10a4.5 4.5 0 019 0 4.5 4.5 0 019 0c0 5.5-7 10-7 10z"/></svg>',
  refresh: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 11-2.6-6.3"/><path d="M21 3v6h-6"/></svg>',
  warning: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3L2 20h20L12 3z"/><path d="M12 10v4"/><path d="M12 17h.01"/></svg>',
  clock: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  medical: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20"/><path d="M2 12h20"/><rect x="4" y="4" width="16" height="16" rx="3"/></svg>',
  swords: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 17.5L3 6V3h3l11.5 11.5"/><path d="M13 19l6-6"/><path d="M16 16l4 4"/><path d="M9.5 6.5L21 18v3h-3L6.5 9.5"/><path d="M11 5L5 11"/><path d="M8 8L4 4"/></svg>',
  trophy: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4h8v5a4 4 0 01-8 0V4z"/><path d="M8 4H6a2 2 0 00-2 2v1a3 3 0 003 3"/><path d="M16 4h2a2 2 0 012 2v1a3 3 0 01-3 3"/><path d="M12 13v3"/><path d="M9 20h6"/><path d="M10 17h4v3h-4z"/></svg>',
  free: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12h8"/><path d="M12 8v8"/></svg>',
  bot: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="7" width="14" height="12" rx="3"/><path d="M9 11h.01"/><path d="M15 11h.01"/><path d="M9 15h6"/><path d="M12 3v4"/><path d="M8 3h8"/></svg>',
  chat: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6a3 3 0 013-3h10a3 3 0 013 3v8a3 3 0 01-3 3H9l-5 4V6z"/></svg>',
  calendar: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/></svg>',
  user: '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20c1.5-3.5 4-5 8-5s6.5 1.5 8 5"/></svg>',
};

function ico(name, cls = '') {
  const svg = ICONS[name] || '';
  return cls ? svg.replace('class="ico"', `class="ico ${cls}"`) : svg;
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
  content.innerHTML = `<div class="empty-state"><div class="icon">${ico('clock')}</div><p>Загрузка...</p></div>`;
  try {
    switch (page) {
      case 'home': await renderHome(); break;
      case 'matches': await renderMatches(); break;
      case 'match-group': await renderMatchGroup(data); break;
      case 'match-detail': await renderMatchDetail(data); break;
      case 'express': await renderExpress(); break;
      case 'account': await renderAccount(); break;
      case 'favorites': await renderFavorites(); break;
      case 'premium': await renderPremium(); break;
      case 'ref': await renderRef(); break;
      case 'admin': await renderAdmin(); break;
      case 'admin-parser': await renderAdminParser(); break;
      case 'admin-parser-leagues': await renderAdminParserLeagues(data); break;
      case 'admin-parser-matches': await renderAdminParserMatches(data); break;
      case 'admin-manual': await renderAdminManual(); break;
      case 'admin-texts': await renderAdminTexts(); break;
      case 'admin-text-edit': await renderAdminTextEdit(data); break;
      case 'admin-delete': await renderAdminDelete(); break;
      case 'admin-express': await renderAdminExpress(); break;
      case 'admin-express-add': await renderAdminExpressAdd(); break;
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

/* ---------- HOME ---------- */
async function renderHome() {
  const me = currentUser || await api('/me');
  currentUser = me;
  const u = me.user;
  const s = me.stats || {};

  let bonusHtml = '';
  if (me.bonus) {
    bonusHtml = `<div class="bonus-banner">
      <h3>${escapeHtml(me.bonus.text)}</h3>
      <p style="color:var(--text-secondary);font-size:13px;margin-top:6px">${ico('calendar')} Premium до: ${escapeHtml(me.bonus.end_date)}</p>
    </div>`;
  }

  let expressStats = { total: 0, wins: 0, losses: 0, winrate: 0, roi: 0, profit: 0 };
  let todayExpress = null;
  try {
    const ex = await api('/express?page=0');
    if (ex.stats) expressStats = ex.stats;
    if (ex.is_premium && ex.picks && ex.picks.length) {
      todayExpress = { count: ex.picks.length, total_coef: ex.total_coef };
    }
  } catch (_) {}

  const subLabel = u.subscription === 'Premium'
    ? `${ico('star')} Premium${u.end_date ? ' до ' + escapeHtml(u.end_date) : ''}`
    : `${ico('free')} Free`;
  const matchesLeft = u.matches_left === '∞' || u.matches_left === Infinity
    ? '∞'
    : String(u.matches_left ?? 0);

  const wrColor = expressStats.winrate >= 55 ? 'var(--accent)' : (expressStats.winrate >= 45 ? '#c9a227' : 'var(--red)');
  const roiSign = expressStats.roi >= 0 ? '+' : '';

  content.innerHTML = `
    <div class="welcome-banner">
      <div class="welcome-title">${greeting()}, ${escapeHtml(u.first_name)}!</div>
      <p class="welcome-text">Я ${ico('bot')} твой помощник в мире футбола — статистика, анализ и наиболее вероятные исходы.</p>
    </div>
    ${bonusHtml}

    <div class="section-title">${ico('chart')} Сегодня</div>
    <div class="stats-grid home-stats">
      <div class="stat-box">
        <div class="val">${matchesLeft}</div>
        <div class="lbl">Матчей доступно</div>
      </div>
      <div class="stat-box">
        <div class="val" style="font-size:14px">${subLabel}</div>
        <div class="lbl">Ваш тариф</div>
      </div>
      <div class="stat-box">
        <div class="val">${s.total_matches || 0}</div>
        <div class="lbl">Матчей в базе</div>
      </div>
      <div class="stat-box">
        <div class="val">${s.fav_count || 0}</div>
        <div class="lbl">В избранном</div>
      </div>
    </div>

    <div class="section-title">🚆 Экспресс дня</div>
    <div class="card" style="margin-bottom:12px">
      <div class="stats-grid" style="margin:0">
        <div class="stat-box">
          <div class="val">${expressStats.total}</div>
          <div class="lbl">Всего</div>
        </div>
        <div class="stat-box">
          <div class="val" style="color:${wrColor}">${Number(expressStats.winrate || 0).toFixed(1)}%</div>
          <div class="lbl">Винрейт</div>
        </div>
        <div class="stat-box">
          <div class="val">${expressStats.wins || 0}</div>
          <div class="lbl">✅ Заходов</div>
        </div>
        <div class="stat-box">
          <div class="val">${roiSign}${Number(expressStats.roi || 0).toFixed(1)}%</div>
          <div class="lbl">ROI</div>
        </div>
      </div>
      ${todayExpress ? `<p style="margin-top:12px;font-size:13px;color:var(--text-secondary);text-align:center">
        Сегодня: <b style="color:#fff">${todayExpress.count}</b> событий · кф <b style="color:#fff">${todayExpress.total_coef}</b>
        <br><button class="btn btn-primary" style="margin-top:8px" onclick="navigate('express')">Открыть экспресс</button>
      </p>` : `<p style="margin-top:10px;font-size:13px;color:var(--text-muted);text-align:center">
        <button class="btn btn-secondary" style="margin-top:4px" onclick="navigate('express')">Смотреть экспресс</button>
      </p>`}
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
  let html = `<div class="section-title">📋 Выберите (страна — лига)</div>`;
  slice.forEach((g, idx) => {
    const realIdx = page * PER_PAGE + idx;
    html += `
      <div class="group-card" data-gidx="${realIdx}">
        <div class="group-left">
          <span class="group-flag">${g.flag || '🏳️'}</span>
          <div class="group-info">
            <div class="country">${escapeHtml(g.country)}</div>
            <div class="league">${escapeHtml(g.league)}</div>
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
  let html = `
    <button class="btn-back" onclick="navigate('matches',null,false)">← Назад</button>
    <div class="card-title">${group.flag || ''} ${escapeHtml(group.country || '')} — ${escapeHtml(group.league || '')}</div>`;
  group.matches.forEach(m => {
    const time = m.match_time ? `<span class="match-time">${escapeHtml(m.match_time)}</span>` : '<span class="match-time">—</span>';
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
  const hasAny = s && (
    (s.h2h && (s.h2h.total || (s.h2h.recent && s.h2h.recent.length))) ||
    (s.form && ((s.form.home && s.form.home.form) || (s.form.away && s.form.away.form))) ||
    (s.last5 && ((s.last5.home && s.last5.home.length) || (s.last5.away && s.last5.away.length))) ||
    (s.injuries && ((s.injuries.home && s.injuries.home.length) || (s.injuries.away && s.injuries.away.length))) ||
    (s.coaches && ((s.coaches.home && s.coaches.home.name) || (s.coaches.away && s.coaches.away.name))) ||
    (s.lineups && (((s.lineups.home || {}).players || []).length || ((s.lineups.away || {}).players || []).length)) ||
    (s.standings && (s.standings.table || []).length) ||
    hasHalves || hasGoalSeg ||
    (s.match_stats && (((s.match_stats.home || {}).matches > 0) || ((s.match_stats.away || {}).matches > 0)))
  );
  if (!hasAny) return '';

  const teams = (m.name || '').split(/—|–|-/).map(x => x.trim()).filter(Boolean);
  const homeName = teams[0] || 'Хозяева';
  const awayName = teams[1] || 'Гости';
  let html = '<div class="stats-section">';

  // Coaches
  const ch = (s.coaches || {}).home || {};
  const ca = (s.coaches || {}).away || {};
  if (ch.name || ca.name) {
    html += `<div class="card stats-card"><div class="card-title">👔 Тренеры</div>
      <div class="coaches-row">
        <div class="coach-side"><div class="side-label">${escapeHtml(homeName)}</div>${personChip(ch) || '<span class="muted">—</span>'}</div>
        <div class="coach-side"><div class="side-label">${escapeHtml(awayName)}</div>${personChip(ca) || '<span class="muted">—</span>'}</div>
      </div></div>`;
  }

  // Lineups (predicted / confirmed) — below coaches
  const lu = s.lineups || {};
  const luHome = lu.home || {};
  const luAway = lu.away || {};
  const luHomePl = luHome.players || [];
  const luAwayPl = luAway.players || [];
  if (luHomePl.length || luAwayPl.length) {
    const statusLabel = lu.status === 'confirmed' ? '✅ Официальный' : (lu.status === 'predicted' ? '🔮 Возможный' : '');
    const sideLineup = (side, label) => {
      const pl = side.players || [];
      if (!pl.length) return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div><span class="muted">Нет данных</span></div>`;
      const form = side.formation ? `<span class="lineup-formation">${escapeHtml(side.formation)}</span>` : '';
      const rows = pl.map(p => {
        const num = p.number != null ? `<span class="lineup-num">${p.number}</span>` : '';
        const pos = p.position ? `<span class="lineup-pos">${escapeHtml(p.position)}</span>` : '';
        const photo = p.photo
          ? `<img class="person-photo lineup-photo" src="${escapeHtml(p.photo)}" alt="" onerror="this.style.display='none'" />`
          : `<span class="person-photo person-photo-fallback lineup-photo">👤</span>`;
        return `<div class="lineup-row">${photo}${num}<span class="lineup-name">${escapeHtml(p.name || '')}</span>${pos}</div>`;
      }).join('');
      return `<div class="form-side"><div class="side-label">${escapeHtml(label)} ${form}</div>${rows}</div>`;
    };
    html += `<div class="card stats-card"><div class="card-title">📋 Состав ${statusLabel}</div>
      <div class="form-sides">
        ${sideLineup(luHome, homeName)}
        ${sideLineup(luAway, awayName)}
      </div></div>`;
  }

  // League table — below lineups
  const st = s.standings || {};
  const table = st.table || [];
  if (table.length) {
    const season = st.season ? ` · ${escapeHtml(String(st.season))}` : '';
    let rows = table.map(r => {
      const hl = r.highlight ? ' standings-hl' : '';
      return `<tr class="${hl}">
        <td class="st-pos">${r.pos ?? ''}</td>
        <td class="st-team">${escapeHtml(r.team || '')}</td>
        <td>${r.played ?? 0}</td>
        <td>${r.won ?? 0}</td>
        <td>${r.drawn ?? 0}</td>
        <td>${r.lost ?? 0}</td>
        <td>${r.gf ?? 0}:${r.ga ?? 0}</td>
        <td class="st-pts">${r.pts ?? 0}</td>
      </tr>`;
    }).join('');
    html += `<div class="card stats-card"><div class="card-title">🏆 Таблица${season}</div>
      <div class="standings-wrap">
        <table class="standings-table">
          <thead><tr>
            <th>#</th><th>Команда</th><th>И</th><th>В</th><th>Н</th><th>П</th><th>Мячи</th><th>О</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div></div>`;
  }

  
  // Матч — средние за полную игру
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
      <div class="meter-top">
        <span class="meter-k">${label}</span>
        <span class="meter-v">${fmtN(val)}</span>
      </div>
      <div class="meter-track"><div class="meter-fill" style="width:${pct}%;background:rgba(${color},0.85)"></div></div>
    </div>`;
  };
  const statRows = (m) => {
    if (!m) return '';
    return `<div class="meter-list">${STAT_KEYS.map(it => meterRow(it.label, m[it.key], it.max, it.color)).join('')}</div>`;
  };
  if ((Number(msH.matches) > 0) || (Number(msA.matches) > 0)) {
    const matchSide = (m, label) => {
      if (!m || !Number(m.matches)) {
        return `<div class="form-side"><div class="side-label">${escapeHtml(label)}</div><span class="muted">Нет данных</span></div>`;
      }
      return `<div class="form-side meter-card">
        <div class="side-label">${escapeHtml(label)} <span class="form-meta">· ${m.matches} матч.</span></div>
        ${statRows(m)}
      </div>`;
    };
    html += `<div class="card stats-card"><div class="card-title">${ico('ball')} Матч</div>
      <div class="gs-hint">Средние · последние матчи</div>
      <div class="form-sides form-sides-split">
        ${matchSide(msH, homeName)}
        ${matchSide(msA, awayName)}
      </div></div>`;
  }
  // Таймы — средняя статистика по 1-му и 2-му тайму
  const halvesH = (s.halves || {}).home || {};
  const halvesA = (s.halves || {}).away || {};
  const hasHalfData = (h) => {
    const fh = h.first_half || {};
    const sh = h.second_half || {};
    return (fh.matches || sh.matches);
  };
  if (hasHalfData(halvesH) || hasHalfData(halvesA)) {
    // half averages are smaller — slightly lower scale caps
    const HALF_KEYS = [
      { key: 'goals', label: 'Голы', max: 2, color: '76, 175, 80' },
      { key: 'corners', label: 'Угловые', max: 6, color: '66, 165, 245' },
      { key: 'fouls', label: 'Фолы', max: 10, color: '255, 167, 38' },
      { key: 'yellow', label: 'Жёлтые', max: 2.5, color: '255, 213, 79' },
      { key: 'shots', label: 'Удары', max: 10, color: '171, 71, 188' },
      { key: 'shots_on', label: 'В створ', max: 5, color: '38, 198, 218' },
    ];
    const halfRows = (m) => {
      if (!m) return '';
      return `<div class="meter-list">${HALF_KEYS.map(it => meterRow(it.label, m[it.key], it.max, it.color)).join('')}</div>`;
    };
    const halfBlock = (half, title) => {
      if (!half || !half.matches) return `<div class="half-block"><div class="half-title">${title}</div><span class="muted">Нет данных</span></div>`;
      return `<div class="half-block">
        <div class="half-title">${title} <span class="form-meta">· ${half.matches}</span></div>
        ${halfRows(half)}
      </div>`;
    };
    const sideHalves = (h, label) => `<div class="form-side meter-card">
      <div class="side-label">${escapeHtml(label)}</div>
      ${halfBlock(h.first_half, '1 тайм')}
      ${halfBlock(h.second_half, '2 тайм')}
    </div>`;
    html += `<div class="card stats-card"><div class="card-title">⏱️ Таймы</div>
      <div class="form-sides form-sides-split">
        ${sideHalves(halvesH, homeName)}
        ${sideHalves(halvesA, awayName)}
      </div></div>`;
  }

  // Голевые отрезки (0-18 … 73-90) за последние 15 матчей
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
      intervals.forEach(lab => {
        mx = Math.max(mx, Number(sc[lab] || 0), Number(cd[lab] || 0));
      });
      return mx;
    };
    const intensity = (val, max) => {
      const t = Math.min(1, Number(val || 0) / max);
      // stronger fill when more goals
      const alpha = 0.12 + t * 0.78;
      return alpha;
    };
    const scaleRow = (map, max, colorRgb, withLabels) => {
      return `<div class="gs-scale">${intervals.map(lab => {
        const v = Number((map || {})[lab] || 0);
        const a = intensity(v, max);
        const labHtml = withLabels
          ? `<span class="gs-interval-lab">${lab}</span>`
          : '';
        return `<div class="gs-col" title="${lab}: ${v}">
          <div class="gs-cell" style="background:rgba(${colorRgb},${a})">
            <span class="gs-num">${v}</span>
          </div>
          ${labHtml}
        </div>`;
      }).join('')}</div>`;
    };
    const sideGs = (g, label) => {
      const sc = g.scored || {};
      const cd = g.conceded || {};
      const mx = maxVal(g);
      const matchesNote = g.matches ? `<span class="form-meta"> · ${g.matches} матч.</span>` : '';
      return `<div class="gs-side">
        <div class="side-label">${escapeHtml(label)}${matchesNote}</div>
        <div class="gs-section-label gs-scored">⚽️ Забитые</div>
        ${scaleRow(sc, mx, '34,197,94', false)}
        <div class="gs-section-label gs-conceded">⚽️ Пропущенные</div>
        ${scaleRow(cd, mx, '239,68,68', true)}
      </div>`;
    };
    html += `<div class="card stats-card"><div class="card-title">🎯 Голевые отрезки</div>
      <div class="gs-hint">Интервалы по 18 мин · последние 15 матчей</div>
      <div class="gs-sides">
        ${sideGs(gsH, homeName)}
        ${sideGs(gsA, awayName)}
      </div></div>`;
  }

  function last5Rows(list, teamName) {
    const t = (teamName || '').toLowerCase().trim();
    if (!list || !list.length) {
      return '<div class="muted" style="font-size:11px">Нет сыгранных матчей</div>';
    }
    return list.map(g => {
      const h = String(g.home || '');
      const a = String(g.away || '');
      const hl = h.toLowerCase();
      const al = a.toLowerCase();
      let isHome = true;
      if (t) {
        const homeMatch = hl.includes(t) || t.includes(hl) || (t.split(/\s+/)[0] && hl.includes(t.split(/\s+/)[0]));
        const awayMatch = al.includes(t) || t.includes(al) || (t.split(/\s+/)[0] && al.includes(t.split(/\s+/)[0]));
        if (awayMatch && !homeMatch) isHome = false;
        else if (homeMatch) isHome = true;
      }
      const opponent = isHome ? a : h;
      let scoreView = String(g.score || '—');
      if (g.home_score != null && g.away_score != null) {
        scoreView = isHome
          ? `${g.home_score}:${g.away_score}`
          : `${g.away_score}:${g.home_score}`;
      } else {
        const parts = scoreView.split(/[-:]/);
        if (parts.length === 2 && !isHome) {
          scoreView = parts[1].trim() + ':' + parts[0].trim();
        } else if (parts.length === 2) {
          scoreView = parts[0].trim() + ':' + parts[1].trim();
        }
      }
      const date = g.date ? String(g.date).slice(5) : '';
      return `<div class="last5-row">
        <span class="last5-opp">${date ? escapeHtml(date) + ' · ' : ''}${escapeHtml(opponent || '—')}</span>
        <span class="last5-score">${escapeHtml(scoreView)}</span>
      </div>`;
    }).join('');
  }

  const fh = (s.form || {}).home || {};
  const fa = (s.form || {}).away || {};
  const l5h = ((s.last5 || {}).home || []).slice(0, 5);
  const l5a = ((s.last5 || {}).away || []).slice(0, 5);
  if (fh.form || fa.form || l5h.length || l5a.length) {
    html += `<div class="card stats-card"><div class="card-title">${ico('chart')} Форма (последние 5)</div>
      <div class="form-sides">
        <div class="form-side">
          <div class="side-label">${escapeHtml(homeName)}</div>
          <div class="form-row">${formBadge(fh.form) || '—'}</div>
          <div class="form-meta">${fh.won != null ? `${fh.won}В ${fh.drawn || 0}Н ${fh.lost || 0}П · ${fh.goals_for || 0}:${fh.goals_against || 0}` : ''}</div>
          <div class="last5-list">${last5Rows(l5h, homeName)}</div>
        </div>
        <div class="form-side">
          <div class="side-label">${escapeHtml(awayName)}</div>
          <div class="form-row">${formBadge(fa.form) || '—'}</div>
          <div class="form-meta">${fa.won != null ? `${fa.won}В ${fa.drawn || 0}Н ${fa.lost || 0}П · ${fa.goals_for || 0}:${fa.goals_against || 0}` : ''}</div>
          <div class="last5-list">${last5Rows(l5a, awayName)}</div>
        </div>
      </div></div>`;
  }

  // H2H
  const h2h = s.h2h || {};
  if (h2h.total || (h2h.recent && h2h.recent.length)) {
    html += `<div class="card stats-card"><div class="card-title">${ico('swords')} Очные встречи</div>
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

  // Injuries — photo + name + position (как в блоке состава)
  const ih = ((s.injuries || {}).home || []);
  const ia = ((s.injuries || {}).away || []);
  if (ih.length || ia.length) {
    const injList = (arr) => arr.length
      ? arr.map(p => {
          const pos = p.position ? `<span class="lineup-pos">${escapeHtml(p.position)}</span>` : '';
          const photo = p.photo
            ? `<img class="person-photo lineup-photo" src="${escapeHtml(p.photo)}" alt="" onerror="this.style.display='none'" />`
            : `<span class="person-photo person-photo-fallback lineup-photo">👤</span>`;
          return `<div class="lineup-row">${photo}<span class="lineup-name">${escapeHtml(p.name || '')}</span>${pos}</div>`;
        }).join('')
      : '<span class="muted">Нет данных</span>';
    html += `<div class="card stats-card"><div class="card-title">${ico('medical')} Травмы / дисквалификации</div>
      <div class="form-sides">
        <div class="form-side">
          <div class="side-label">${escapeHtml(homeName)}</div>
          ${injList(ih)}
        </div>
        <div class="form-side">
          <div class="side-label">${escapeHtml(awayName)}</div>
          ${injList(ia)}
        </div>
      </div></div>`;
  }

  html += '</div>';
  return html;
}

async function renderMatchDetail(matchId, forceRefresh = false) {
  content.innerHTML = `<button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="empty-state" style="padding:40px 0"><div class="icon">${ico('clock')}</div><p>Загружаем статистику...</p></div>`;
  try {
    const m = await api(`/matches/${matchId}?refresh=${forceRefresh ? 1 : 0}`);
    const favBtn = m.is_favorite
      ? `<button class="btn btn-danger" onclick="toggleFav(${m.id})">💔 Убрать из избранного</button>`
      : `<button class="btn btn-secondary" onclick="toggleFav(${m.id})">⭐ В избранное</button>`;
    const statsHtml = renderStatsBlock(m);
    const noStats = !statsHtml
      ? `<div class="card stats-card"><p style="color:var(--text-muted);font-size:13px;text-align:center">
          Статистика пока недоступна для этого матча.
          <br><button class="btn btn-secondary" style="margin-top:10px" onclick="renderMatchDetail(${m.id}, true)">🔄 Обновить</button>
        </p></div>`
      : '';
    content.innerHTML = `
      <button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="match-detail-header">
        <div style="display:flex;justify-content:center;gap:12px;margin-bottom:8px">${logosHtml(m.logo_home, m.logo_away)}</div>
        <div class="match-detail-name">⚽ ${escapeHtml(m.name)}</div>
        <div class="match-meta">
          ${m.match_time ? `<span class="meta-tag match-time">${escapeHtml(m.match_time)}</span>` : ''}
          <span class="meta-tag">${m.flag || ''} ${escapeHtml(m.country)}</span>
          <span class="meta-tag">🏆 ${escapeHtml(m.league)}</span>
        </div>
      </div>
      <div class="match-text">${escapeHtml(m.text) || '<span style="color:var(--text-muted)">Текст прогноза пока не добавлен</span>'}</div>
      ${statsHtml || noStats}
      ${favBtn}`;
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
async function renderExpress(page = 0) {
  let data;
  try {
    data = await api(`/express?page=${page}`);
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="icon">${ico('warning')}</div>
      <p>${escapeHtml(e.message || 'Ошибка загрузки')}</p>
      <button class="btn btn-secondary" onclick="navigate('home',null,false)" style="margin-top:16px">На главную</button>
      <button class="btn btn-primary" onclick="renderExpress(0)" style="margin-top:8px">Повторить</button></div>`;
    return;
  }
  const picks = data.picks || [];
  const s = data.stats || { total: 0, wins: 0, losses: 0, winrate: 0, roi: 0, profit: 0 };
  const results = data.results || [];
  let picksHtml = '';
  if (!data.is_premium) {
    picksHtml = `<div class="express-card"><div class="lock-overlay">
      <div class="icon">🔒</div>
      <h3 style="margin-bottom:8px">🚆 Экспресс дня</h3>
      <p style="color:var(--text-secondary);margin-bottom:16px">Полный экспресс доступен только по Premium!</p>
      <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
    </div></div>`;
  } else if (picks.length) {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>`;
    picks.forEach(p => {
      const time = p.match_time ? `<span class="match-time">${escapeHtml(p.match_time)}</span>` : '';
      picksHtml += `<div class="express-pick">
        <div class="express-pick-header">
          ${time}
          ${logosHtml(p.logo_home, p.logo_away)}
          <div class="name" style="flex:1">${escapeHtml(p.name || '')}</div>
        </div>
        <div class="meta">${p.flag || ''} ${escapeHtml(p.country || '')} — ${escapeHtml(p.league || '')}</div>
        <div class="pred">⚡ Прогноз: ${escapeHtml(p.prediction || '')}</div>
        <div class="pred">🎯 Коэффициент: ${escapeHtml(String(p.coef || ''))}</div>
      </div>`;
    });
    picksHtml += `<div class="total-coef">Итоговый коэффициент: ${data.total_coef ?? '—'}</div></div>`;
  } else {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>
      <p style="color:var(--text-secondary);padding:12px 0">Экспресс пока не сформирован.</p></div>`;
  }
  const wr = Number(s.winrate || 0);
  const roi = Number(s.roi || 0);
  const profit = Number(s.profit || 0);
  const statsHtml = `<div class="card"><div class="card-title">📊 Статистика экспрессов</div>
    <div class="stats-grid">
      <div class="stat-box"><div class="val">${s.total || 0}</div><div class="lbl">Всего</div></div>
      <div class="stat-box"><div class="val">${s.wins || 0}</div><div class="lbl">✅ Заходов</div></div>
      <div class="stat-box"><div class="val">${s.losses || 0}</div><div class="lbl">❌ Мимо</div></div>
      <div class="stat-box"><div class="val">${wr.toFixed(1)}%</div><div class="lbl">Винрейт</div></div>
      <div class="stat-box"><div class="val">${roi>=0?'+':''}${roi.toFixed(1)}%</div><div class="lbl">ROI</div></div>
      <div class="stat-box"><div class="val">${profit>=0?'+':''}${profit.toFixed(2)}</div><div class="lbl">Прибыль</div></div>
    </div></div>`;
  let resultsHtml = `<div class="card"><div class="card-title">📋 Прошедшие прогнозы</div>`;
  if (!results.length) {
    resultsHtml += `<p style="color:var(--text-muted);padding:8px 0">Прошедших прогнозов пока нет.</p>`;
  } else {
    results.forEach(r => {
      resultsHtml += `<div class="result-item"><span>${r.result_emoji || ''}</span>
        <span class="result-text" style="flex:1">🚆 ${escapeHtml(r.created_at || '')} | ${r.type_label || ''} | кф. ${Number(r.coef || 0).toFixed(2)}</span></div>`;
    });
    const totalPages = data.total_pages || 1;
    if (totalPages > 1) {
      resultsHtml += `<div class="pagination">
        <button class="page-btn" ${page<=0?'disabled':''} onclick="renderExpress(${page-1})">←</button>
        <span class="page-info">${page+1}/${totalPages}</span>
        <button class="page-btn" ${page>=totalPages-1?'disabled':''} onclick="renderExpress(${page+1})">→</button>
      </div>`;
    }
  }
  resultsHtml += `</div>`;
  content.innerHTML = picksHtml + statsHtml + resultsHtml;
}

/* ---------- ACCOUNT ---------- */
async function renderAccount() {
  const me = await api('/me');
  currentUser = me;
  const u = me.user;
  const s = me.stats;
  const subBadge = u.subscription === 'Premium'
    ? `<span class="sub-badge premium">⭐ Premium</span>`
    : `<span class="sub-badge free">🆓 Free</span>`;
  let premiumBtn = u.subscription === 'Free'
    ? `<button class="btn btn-gold" onclick="navigate('premium')">Купить Premium (300 ₽)</button>`
    : `<div class="card" style="margin-top:12px"><ul class="info-list">
        <li><span class="label">📅 Premium до</span><span class="value">${escapeHtml(u.end_date || '—')}</span></li>
      </ul></div>`;

  content.innerHTML = `
    <div class="profile-card">
      ${avatarHtml(u.photo_url, u.first_name, 'profile-avatar')}
      <div class="profile-name">${escapeHtml(u.first_name)}</div>
      <div class="profile-id">ID: ${u.id}</div>
      ${subBadge}
      <div class="stats-row">
        <div class="stat-item"><div class="stat-value">${u.matches_left}</div><div class="stat-label">Матчей сегодня</div></div>
        <div class="stat-item"><div class="stat-value">${u.days_in_project}</div><div class="stat-label">Дней в проекте</div></div>
        <div class="stat-item"><div class="stat-value">${s.fav_count}</div><div class="stat-label">Избранное</div></div>
      </div>
    </div>
    <div class="card"><ul class="info-list">
      <li><span class="label">📅 С нами с</span><span class="value">${escapeHtml(u.reg_date || '—')}</span></li>
      <li><span class="label">🏆 Матчей в базе</span><span class="value">${s.total_matches}</span></li>
      <li><span class="label">👥 Всего пользователей</span><span class="value">${s.total_users}</span></li>
      <li><span class="label">⭐ Premium-пользователей</span><span class="value">${s.premium_users}</span></li>
    </ul></div>
    <button class="btn btn-secondary" onclick="navigate('favorites')">⭐ Избранное (${s.fav_count})</button>
    <button class="btn btn-secondary" onclick="navigate('ref')">🔗 Реферальная ссылка (приглашено: ${s.ref_count})</button>
    <button class="btn btn-secondary" onclick="navigate('support')">🛟 Техподдержка</button>
    <a class="btn btn-secondary" href="https://t.me/koefchat" target="_blank" rel="noopener" style="text-decoration:none;display:flex;align-items:center;justify-content:center">💬 Живой чат</a>
    ${premiumBtn}
    ${u.is_admin ? `<button class="btn btn-gold" onclick="navigate('admin')" style="margin-top:8px">🔧 Админ-панель</button>` : ''}`;
  document.getElementById('header-user').innerHTML = avatarHtml(u.photo_url, u.first_name, 'avatar');
}

async function renderFavorites() {
  const data = await api('/favorites');
  let html = `<button class="btn-back" onclick="goBack()">← Назад</button><div class="card-title">⭐ Избранные матчи</div>`;
  if (!data.favorites.length) {
    html += `<div class="empty-state"><div class="icon">⭐</div><p>У вас пока нет избранных матчей.</p></div>`;
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
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card">
      <div class="card-title">🔗 Ваша реферальная ссылка</div>
      <p style="font-family:monospace;font-size:13px;word-break:break-all;background:var(--bg-elevated);padding:12px;border-radius:8px;margin:12px 0">${escapeHtml(me.ref_link)}</p>
      <p style="color:var(--text-secondary);font-size:14px">🎉 За каждого приглашённого — 3 дня Premium!<br>Приглашено: <b>${me.stats.ref_count}</b></p>
      <button class="btn btn-primary" onclick="navigator.clipboard?.writeText('${me.ref_link}').then(()=>toast('Скопировано!'))">📋 Скопировать</button>
    </div>`;
}

async function renderPremium() {
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="premium-card">
      <h2>⭐ Premium</h2>
      <ul class="premium-features"><li>✅ Безлимитные матчи</li><li>✅ Экспрессы</li><li>✅ Полная статистика</li></ul>
      <div class="premium-price">300 ₽ / месяц</div>
      <button class="btn btn-gold" id="pay-btn" onclick="createPayment()">💳 Оплатить 300 ₽</button>
    </div><div id="pay-status"></div>`;
}

async function createPayment() {
  const btn = document.getElementById('pay-btn');
  btn.disabled = true; btn.textContent = 'Создаём...';
  try {
    const res = await api('/premium/create', { method: 'POST' });
    if (res.confirmation_url) {
      document.getElementById('pay-status').innerHTML = `<div class="card" style="margin-top:12px">
        <a href="${res.confirmation_url}" target="_blank" class="btn btn-primary" style="text-decoration:none">💳 Перейти к оплате</a>
        <button class="btn btn-secondary" style="margin-top:8px" onclick="checkPayment('${res.payment_id}')">✅ Я оплатил</button>
      </div>`;
    }
  } catch (e) { toast(e.message); }
  finally { btn.disabled = false; btn.textContent = '💳 Оплатить 300 ₽'; }
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

async function renderAdmin() {
  const stats = await api('/admin/stats');
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('account',null,false)">← Назад</button>
    <div class="card-title">🔧 Админ-панель</div>
    <div class="stats-grid" style="margin-bottom:16px">
      <div class="stat-box"><div class="val">${stats.total_users}</div><div class="lbl">Пользователей</div></div>
      <div class="stat-box"><div class="val">${stats.premium_users}</div><div class="lbl">Premium</div></div>
      <div class="stat-box"><div class="val">${stats.matches}</div><div class="lbl">Матчей</div></div>
    </div>
    <button class="cat-btn" onclick="navigate('admin-parser')">🔍 Парсер матчей</button>
    <button class="cat-btn" onclick="refreshAllStats()">📡 Обновить статистику матчей</button>
    <button class="cat-btn" onclick="navigate('admin-manual')">✍️ Добавить матч вручную</button>
    <button class="cat-btn" onclick="navigate('admin-texts')">📝 Тексты матчей</button>
    <button class="cat-btn" onclick="navigate('admin-delete')">🗑 Удалить матчи</button>
    <button class="cat-btn" onclick="navigate('admin-express')">🚆 Экспресс дня</button>
    <button class="cat-btn" onclick="navigate('admin-premium')">⭐ Premium</button>
    <button class="cat-btn" onclick="navigate('admin-support')">🛟 Техподдержка</button>
    <button class="cat-btn" onclick="navigate('admin-logs')">📋 Логи</button>`;
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
  let html = `<button class="btn-back" onclick="navigate('admin-parser')">← Назад</button>
    <div class="card-title">📋 Найдено: ${total}</div>
    <button class="btn btn-primary" onclick="parserAddAll()" style="margin-bottom:12px">✅ Добавить ВСЕ (до 80)</button>`;
  leagues.forEach(l => {
    html += `<div class="group-card" onclick='navigate("admin-parser-matches", ${JSON.stringify({league_id:l.league_id,league:l.league,country:l.country,flag:l.flag})})'>
      <div class="group-left"><span class="group-flag">${l.flag||''}</span>
        <div class="group-info"><div class="country">P${l.priority} ${escapeHtml(l.country)}</div>
        <div class="league">${escapeHtml(l.league)}</div></div></div>
      <span class="group-count">${l.count}</span></div>`;
  });
  content.innerHTML = html;
}

async function renderAdminParserMatches(data) {
  if (!data) { navigate('admin-parser'); return; }
  const res = await api(`/admin/parser/matches?league_id=${data.league_id}&page=0`);
  let html = `<button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">${data.flag||''} ${escapeHtml(data.country||'')} — ${escapeHtml(data.league||'')}</div>
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

async function parserAddAll() {
  try { toast((await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'all',limit:80})})).message); }
  catch(e){ toast(e.message); }
}
async function parserAddLeague(id) {
  try { toast((await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'league',league_id:id})})).message); }
  catch(e){ toast(e.message); }
}
async function parserAddSingle(id, btn) {
  try {
    const res = await api('/admin/parser/add',{method:'POST',body:JSON.stringify({mode:'single',parsed_id:id})});
    toast(res.message);
    if (res.ok && btn) { btn.textContent='✅'; btn.disabled=true; }
  } catch(e){ toast(e.message); }
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
    <div class="card-title">🚆 Экспресс</div><div class="express-card">${picks}</div>
    <button class="btn btn-primary" onclick="navigate('admin-express-add')">➕ Добавить</button>
    <button class="btn btn-danger" onclick="if(confirm('Очистить?'))api('/admin/express',{method:'DELETE'}).then(r=>{toast(r.message);renderAdminExpress()})">🗑 Очистить</button>
    <button class="btn btn-secondary" onclick="navigate('admin-result-add')">📝 Результат</button>
    <button class="btn btn-secondary" onclick="navigate('admin-results')">Результаты</button>`;
}

async function renderAdminExpressAdd() {
  const data = await api('/admin/matches');
  let html = `<button class="btn-back" onclick="navigate('admin-express')">← Назад</button><div class="card-title">Выберите матч</div>`;
  data.matches.forEach(m => {
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
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('account',null,false)">← Назад</button>
    <div class="card-title">🛟 Техподдержка</div>
    <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px">Выберите тему обращения:</p>
    <div class="category-grid">
      <button class="category-btn" data-cat="bug">🐛 Баг / Ошибка</button>
      <button class="category-btn" data-cat="idea">💡 Предложение</button>
      <button class="category-btn" data-cat="pay">💳 Оплата</button>
      <button class="category-btn" data-cat="q">❓ Вопрос</button>
    </div>`;
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
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Опишите проблему или вопрос</p>
      <textarea class="textarea" id="support-text" placeholder="Ваше сообщение..."></textarea>
      <button class="btn btn-primary" id="support-send">📤 Отправить</button>
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
      setTimeout(() => navigate('account'), 1500);
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
    <div class="card-title">🛟 Техподдержка</div>`;
  if (!data.threads.length) {
    html += `<div class="empty-state"><div class="icon">💬</div><p>Обращений пока нет</p></div>`;
  } else {
    data.threads.forEach(t => {
      html += `<div class="cat-btn" data-tid="${t.thread_id}">
        <div style="flex:1">
          <div style="font-weight:600">${escapeHtml(t.user_name)} · ${escapeHtml(t.category_label || t.category)}</div>
          <div style="font-size:11px;color:var(--text-muted)">${escapeHtml(t.created_at || '')}</div>
        </div>
        <span style="font-size:12px;color:var(--text-muted)">›</span>
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
    res.messages.forEach(m => {
      const cls = m.sender === 'admin' ? 'admin' : 'user';
      const who = m.sender === 'admin' ? 'Админ' : 'Пользователь';
      html += `<div class="support-msg ${cls}">
        <div class="meta">${who} · ${escapeHtml(m.created_at || '')}</div>
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

async function init() {
  try {
    currentUser = await api('/me');
    const u = currentUser.user;
    document.getElementById('header-user').innerHTML = avatarHtml(u.photo_url, u.first_name, 'avatar');
    bindNav();
    document.getElementById('loader').classList.add('hidden');
    document.getElementById('main').classList.remove('hidden');
    navigate('home', null, false);
  } catch (e) {
    document.getElementById('loader').innerHTML = `<div style="text-align:center;padding:40px">
      <div style="font-size:48px">⚠️</div>
      <p style="color:var(--text-secondary)">${escapeHtml(e.message)}</p>
      <p style="color:var(--text-muted);font-size:13px;margin-top:12px">Откройте через Telegram</p></div>`;
  }
}
init();
