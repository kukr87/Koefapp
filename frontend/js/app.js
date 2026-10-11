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

function greeting() {
  const h = new Date().getHours();
  if (h >= 6 && h < 12) return '☀️ Доброе утро';
  if (h >= 12 && h < 18) return '🏙 Привет';
  if (h >= 18 && h < 23) return '🌙 Вечер добрый';
  return '🌙 Доброй ночи';
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
  content.innerHTML = '<div class="empty-state"><div class="icon">⏳</div><p>Загрузка...</p></div>';
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
      case 'admin-news': await renderAdminNews(); break;
      case 'admin-logs': await renderAdminLogs(); break;
      case 'admin-support': await renderAdminSupport(); break;
      case 'admin-support-thread': await renderAdminSupportThread(data); break;
      case 'support': await renderSupport(); break;
      case 'support-form': await renderSupportForm(data); break;
      default: await renderHome();
    }
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>${escapeHtml(e.message)}</p>
      <button class="btn btn-secondary" onclick="navigate('home',null,false)" style="margin-top:16px">На главную</button></div>`;
  }
}

/* ---------- HOME + NEWS ---------- */
async function renderHome(newsPage = 0) {
  const me = currentUser;
  let bonusHtml = '';
  if (me.bonus) {
    bonusHtml = `<div class="bonus-banner">
      <h3>${escapeHtml(me.bonus.text)}</h3>
      <p style="color:var(--text-secondary);font-size:13px;margin-top:6px">📅 Premium до: ${escapeHtml(me.bonus.end_date)}</p>
    </div>`;
  }

  let newsHtml = '<div class="section-title">📰 Новости</div>';
  try {
    const nd = await api(`/news?page=${newsPage}`);
    if (!nd.news.length) {
      newsHtml += `<div class="empty-state" style="padding:20px"><p style="color:var(--text-muted)">Новостей пока нет</p></div>`;
    } else {
      nd.news.forEach(n => {
        newsHtml += `<div class="news-card">
          <div class="news-num">Новость №${n.number}</div>
          ${n.title ? `<div class="news-title">${escapeHtml(n.title)}</div>` : ''}
          <div class="news-text">${escapeHtml(n.text)}</div>
          <div class="news-author">Администрация проекта</div>
        </div>`;
      });
      if (nd.total_pages > 1) {
        newsHtml += `<div class="pagination">
          <button class="page-btn" ${newsPage<=0?'disabled':''} onclick="renderHome(${newsPage-1})">←</button>
          <span class="page-info">${newsPage+1}/${nd.total_pages}</span>
          <button class="page-btn" ${newsPage>=nd.total_pages-1?'disabled':''} onclick="renderHome(${newsPage+1})">→</button>
        </div>`;
      }
    }
  } catch (e) {
    newsHtml += `<p style="color:var(--text-muted)">${escapeHtml(e.message)}</p>`;
  }

  content.innerHTML = `
    <div class="welcome-banner">
      <div class="welcome-title">${greeting()}, ${escapeHtml(me.user.first_name)}!</div>
      <p class="welcome-text">Я 🤖 твой помощник в мире футбола — собираю полную статистику, анализирую и предоставляю более вероятные исходы на события.</p>
    </div>
    ${bonusHtml}
    ${newsHtml}`;
}

/* ---------- MATCHES ---------- */
async function renderMatches(page = 0) {
  const data = await api('/matches');
  if (!data.groups.length) {
    content.innerHTML = `<div class="empty-state"><div class="icon">⚽</div><p>Матчей пока нет</p></div>`;
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
    const time = m.match_time ? `<span class="match-time">🕒 ${escapeHtml(m.match_time)}</span>` : '';
    html += `
      <div class="match-item" onclick="navigate('match-detail', ${m.id})">
        ${logosHtml(m.logo_home, m.logo_away)}
        ${time}<span class="match-name">${escapeHtml(m.name)}</span>
      </div>`;
  });
  content.innerHTML = html;
}

async function renderMatchDetail(matchId) {
  try {
    const m = await api(`/matches/${matchId}`);
    const favBtn = m.is_favorite
      ? `<button class="btn btn-danger" onclick="toggleFav(${m.id})">💔 Убрать из избранного</button>`
      : `<button class="btn btn-secondary" onclick="toggleFav(${m.id})">⭐ В избранное</button>`;
    content.innerHTML = `
      <button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="match-detail-header">
        <div style="display:flex;justify-content:center;gap:12px;margin-bottom:8px">${logosHtml(m.logo_home, m.logo_away)}</div>
        <div class="match-detail-name">⚽ ${escapeHtml(m.name)}</div>
        <div class="match-meta">
          ${m.match_time ? `<span class="meta-tag">🕒 ${escapeHtml(m.match_time)}</span>` : ''}
          <span class="meta-tag">${m.flag || ''} ${escapeHtml(m.country)}</span>
          <span class="meta-tag">🏆 ${escapeHtml(m.league)}</span>
        </div>
      </div>
      <div class="match-text">${escapeHtml(m.text) || '<span style="color:var(--text-muted)">Текст прогноза пока не добавлен</span>'}</div>
      ${favBtn}`;
  } catch (e) {
    if (String(e.message).includes('Лимит')) {
      content.innerHTML = `
        <button class="btn-back" onclick="goBack()">← Назад</button>
        <div class="empty-state"><div class="icon">🔒</div><p>${escapeHtml(e.message)}</p>
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
  const data = await api(`/express?page=${page}`);
  let picksHtml = '';
  if (!data.is_premium) {
    picksHtml = `<div class="express-card"><div class="lock-overlay">
      <div class="icon">🔒</div>
      <h3 style="margin-bottom:8px">🚆 Экспресс дня</h3>
      <p style="color:var(--text-secondary);margin-bottom:16px">Полный экспресс доступен только по Premium!</p>
      <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
    </div></div>`;
  } else if (data.picks.length) {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>`;
    data.picks.forEach(p => {
      picksHtml += `<div class="express-pick">
        <div class="name">⚽ ${escapeHtml(p.name)}</div>
        <div class="meta">${p.flag || ''} ${escapeHtml(p.country)} — ${escapeHtml(p.league)}</div>
        <div class="pred">⚡ Прогноз: ${escapeHtml(p.prediction)}</div>
        <div class="pred">🎯 Коэффициент: ${escapeHtml(p.coef)}</div>
      </div>`;
    });
    picksHtml += `<div class="total-coef">Итоговый коэффициент: ${data.total_coef}</div></div>`;
  } else {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>
      <p style="color:var(--text-secondary);padding:12px 0">Экспресс пока не сформирован.</p></div>`;
  }
  const s = data.stats;
  const statsHtml = `<div class="card"><div class="card-title">📊 Статистика экспрессов</div>
    <div class="stats-grid">
      <div class="stat-box"><div class="val">${s.total}</div><div class="lbl">Всего</div></div>
      <div class="stat-box"><div class="val">${s.wins}</div><div class="lbl">✅ Заходов</div></div>
      <div class="stat-box"><div class="val">${s.losses}</div><div class="lbl">❌ Мимо</div></div>
      <div class="stat-box"><div class="val">${s.winrate.toFixed(1)}%</div><div class="lbl">Винрейт</div></div>
      <div class="stat-box"><div class="val">${s.roi>=0?'+':''}${s.roi.toFixed(1)}%</div><div class="lbl">ROI</div></div>
      <div class="stat-box"><div class="val">${s.profit>=0?'+':''}${s.profit.toFixed(2)}</div><div class="lbl">Прибыль</div></div>
    </div></div>`;
  let resultsHtml = `<div class="card"><div class="card-title">📋 Прошедшие прогнозы</div>`;
  if (!data.results.length) {
    resultsHtml += `<p style="color:var(--text-muted);padding:8px 0">Прошедших прогнозов пока нет.</p>`;
  } else {
    data.results.forEach(r => {
      resultsHtml += `<div class="result-item"><span>${r.result_emoji}</span>
        <span class="result-text" style="flex:1">🚆 ${escapeHtml(r.created_at)} | ${r.type_label} | кф. ${Number(r.coef).toFixed(2)}</span></div>`;
    });
    if (data.total_pages > 1) {
      resultsHtml += `<div class="pagination">
        <button class="page-btn" ${page<=0?'disabled':''} onclick="renderExpress(${page-1})">←</button>
        <span class="page-info">${page+1}/${data.total_pages}</span>
        <button class="page-btn" ${page>=data.total_pages-1?'disabled':''} onclick="renderExpress(${page+1})">→</button>
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
      const time = m.match_time ? `<span class="match-time">🕒 ${escapeHtml(m.match_time)}</span>` : '';
      html += `<div class="match-item" onclick="navigate('match-detail', ${m.id})">
        ${time}<span class="match-name">${m.flag || ''} ${escapeHtml(m.name)}</span></div>`;
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
    <button class="cat-btn" onclick="navigate('admin-manual')">✍️ Добавить матч вручную</button>
    <button class="cat-btn" onclick="navigate('admin-texts')">📝 Тексты матчей</button>
    <button class="cat-btn" onclick="navigate('admin-delete')">🗑 Удалить матчи</button>
    <button class="cat-btn" onclick="navigate('admin-express')">🚆 Экспресс дня</button>
    <button class="cat-btn" onclick="navigate('admin-news')">📰 Новости</button>
    <button class="cat-btn" onclick="navigate('admin-premium')">⭐ Premium</button>
    <button class="cat-btn" onclick="navigate('admin-support')">🛟 Техподдержка</button>
    <button class="cat-btn" onclick="navigate('admin-logs')">📋 Логи</button>`;
}

async function renderAdminNews() {
  const data = await api('/admin/news');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">📰 Новости</div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Заголовок (необязательно)</p>
      <input class="textarea" id="n-title" style="min-height:auto;height:44px" placeholder="Заголовок" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Текст новости</p>
      <textarea class="textarea" id="n-text" placeholder="Текст..."></textarea>
      <button class="btn btn-primary" onclick="addNews()">✅ Опубликовать</button>
    </div>
    <div class="section-title">Все новости</div>`;
  if (!data.news.length) html += `<p style="color:var(--text-muted)">Пусто</p>`;
  else data.news.forEach(n => {
    html += `<div class="news-card">
      <div class="news-text">${n.title ? '<b>'+escapeHtml(n.title)+'</b><br>' : ''}${escapeHtml(n.text)}</div>
      <div class="news-author">${escapeHtml(n.created_at)}</div>
      <button class="btn btn-danger" style="margin-top:8px" onclick="deleteNews(${n.id})">🗑 Удалить</button>
    </div>`;
  });
  content.innerHTML = html;
}

async function addNews() {
  const title = document.getElementById('n-title').value.trim();
  const text = document.getElementById('n-text').value.trim();
  if (!text) { toast('Введите текст'); return; }
  try {
    const res = await api('/admin/news', { method: 'POST', body: JSON.stringify({ title, text }) });
    toast(res.message);
    renderAdminNews();
  } catch (e) { toast(e.message); }
}

async function deleteNews(id) {
  if (!confirm('Удалить новость?')) return;
  try {
    const res = await api(`/admin/news/${id}`, { method: 'DELETE' });
    toast(res.message);
    renderAdminNews();
  } catch (e) { toast(e.message); }
}

/* parser + rest admin - same as before simplified */
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
    const time = m.match_time ? `🕒 ${escapeHtml(m.match_time)} | ` : '';
    html += `<div class="match-item" style="justify-content:space-between">
      ${logosHtml(m.logo_home, m.logo_away)}
      <span class="match-name">${time}${escapeHtml(m.name)}</span>
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
  let picks = data.picks.length ? data.picks.map(p=>`<div class="express-pick"><div class="name">${escapeHtml(p.name)}</div>
    <div class="pred">${escapeHtml(p.prediction)} | ${escapeHtml(p.coef)}</div></div>`).join('') : '<p style="color:var(--text-muted)">Пусто</p>';
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
    html += `<div class="match-item" onclick="showExpressForm(${m.id},'${escapeHtml(m.name).replace(/'/g,"\\'")}')">
      <span class="match-name">${escapeHtml(m.name)}</span></div>`;
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
