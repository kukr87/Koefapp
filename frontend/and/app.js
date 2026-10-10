/**
 * Koefii Mini App — Frontend + Full Admin Panel
 * Dark Football Theme
 */

const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor('#0a0e17');
  tg.setBackgroundColor('#0a0e17');
}

const API = '/api';
let currentUser = null;
let currentPage = 'home';
let pageHistory = [];
let parserLeagues = [];

function getInitData() {
  return tg?.initData || '';
}

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
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
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
    return `<img class="${sizeClass}" src="${escapeHtml(url)}" alt="" onerror="this.outerHTML=this.dataset.fallback" data-fallback="<div class='${sizeClass}-placeholder'>${(name || '?')[0].toUpperCase()}</div>" />`;
  }
  const letter = (name || '?')[0].toUpperCase();
  return `<div class="${sizeClass}-placeholder">${letter}</div>`;
}

function navigate(page, data = null, push = true) {
  if (push && currentPage !== page) {
    pageHistory.push({ page: currentPage, data: null });
  }
  currentPage = page;
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.page === page);
  });
  render(page, data);
}

function goBack() {
  if (pageHistory.length) {
    const prev = pageHistory.pop();
    currentPage = prev.page;
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
      case 'support': await renderSupport(); break;
      case 'support-form': await renderSupportForm(data); break;
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
      case 'admin-support': await renderAdminSupport(); break;
      case 'admin-support-thread': await renderAdminSupportThread(data); break;
      case 'admin-logs': await renderAdminLogs(); break;
      default: await renderHome();
    }
  } catch (e) {
    content.innerHTML = `<div class="empty-state"><div class="icon">⚠️</div><p>${escapeHtml(e.message)}</p>
      <button class="btn btn-secondary" onclick="navigate('home')" style="margin-top:16px">На главную</button></div>`;
  }
}

async function renderHome() {
  const me = currentUser;
  let bonusHtml = '';
  if (me.bonus) {
    bonusHtml = `
      <div class="bonus-banner">
        <h3>${escapeHtml(me.bonus.text)}</h3>
        <p style="color:var(--text-secondary);font-size:13px;margin-top:6px">📅 Premium до: ${escapeHtml(me.bonus.end_date)}</p>
      </div>`;
  }
  let adminBtn = '';
  if (me.user.is_admin) {
    adminBtn = `<div class="menu-item" onclick="navigate('admin')" style="border-color:var(--gold)">
      <span class="icon">🔧</span><span class="label">Админ-панель</span></div>`;
  }
  content.innerHTML = `
    <div class="welcome-banner">
      <div class="welcome-title">${greeting()}, ${escapeHtml(me.user.first_name)}!</div>
      <p class="welcome-text">Я 🤖 твой помощник в мире футбола — собираю полную статистику, анализирую и предоставляю более вероятные исходы на события.</p>
    </div>
    ${bonusHtml}
    <div class="menu-grid">
      <div class="menu-item" onclick="navigate('matches')"><span class="icon">⚽</span><span class="label">Матчи</span></div>
      <div class="menu-item" onclick="navigate('express')"><span class="icon">🚆</span><span class="label">Экспресс дня</span></div>
      <div class="menu-item" onclick="navigate('account')"><span class="icon">👤</span><span class="label">Аккаунт</span></div>
      <div class="menu-item" onclick="navigate('support')"><span class="icon">✉️</span><span class="label">Техподдержка</span></div>
      ${adminBtn}
    </div>
    <div style="margin-top:16px">
      <a href="https://t.me/koefchat" target="_blank" class="btn btn-secondary" style="text-decoration:none">💬 Чат канала</a>
    </div>`;
}

async function renderMatches() {
  const data = await api('/matches');
  if (!data.groups.length) {
    content.innerHTML = `<button class="btn-back" onclick="goBack()">← Назад</button>
      <div class="empty-state"><div class="icon">⚽</div><p>Матчей пока нет</p></div>`;
    return;
  }
  let html = `<div class="section-title">📋 Выберите (страна — лига)</div>`;
  data.groups.forEach(g => {
    const gJson = JSON.stringify(g).replace(/'/g, "&#39;");
    html += `
      <div class="group-card" onclick='navigate("match-group", ${gJson})'>
        <div class="group-left">
          <span class="group-flag">${g.flag}</span>
          <div class="group-info">
            <div class="country">${escapeHtml(g.country)}</div>
            <div class="league">${escapeHtml(g.league)}</div>
          </div>
        </div>
        <span class="group-count">${g.count}</span>
      </div>`;
  });
  content.innerHTML = html;
}

async function renderMatchGroup(group) {
  let html = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">${group.flag} ${escapeHtml(group.country)} — ${escapeHtml(group.league)}</div>`;
  group.matches.forEach(m => {
    const time = m.match_time ? `<span class="match-time">🕒 ${escapeHtml(m.match_time)}</span>` : '';
    html += `
      <div class="match-item" onclick="navigate('match-detail', ${m.id})">
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
        <div class="match-detail-name">⚽ ${escapeHtml(m.name)}</div>
        <div class="match-meta">
          ${m.match_time ? `<span class="meta-tag">🕒 ${escapeHtml(m.match_time)}</span>` : ''}
          <span class="meta-tag">${m.flag} ${escapeHtml(m.country)}</span>
          <span class="meta-tag">🏆 ${escapeHtml(m.league)}</span>
        </div>
      </div>
      <div class="match-text">${escapeHtml(m.text) || '<span style="color:var(--text-muted)">Текст прогноза пока не добавлен</span>'}</div>
      ${favBtn}`;
  } catch (e) {
    if (e.message.includes('Лимит')) {
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

async function renderExpress(page = 0) {
  const data = await api(`/express?page=${page}`);
  let picksHtml = '';
  if (!data.is_premium) {
    picksHtml = `
      <div class="express-card"><div class="lock-overlay">
        <div class="icon">🔒</div>
        <h3 style="margin-bottom:8px">🚆 Экспресс дня</h3>
        <p style="color:var(--text-secondary);margin-bottom:16px">Полный экспресс доступен только по Premium!<br>
        ⭐ Купите Premium и получите доступ ко всем экспрессам и матчам без ограничений.</p>
        <button class="btn btn-gold" onclick="navigate('premium')">Купить Premium</button>
      </div></div>`;
  } else if (data.picks.length) {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>`;
    data.picks.forEach(p => {
      picksHtml += `
        <div class="express-pick">
          <div class="name">⚽ ${escapeHtml(p.name)}</div>
          <div class="meta">${p.flag} ${escapeHtml(p.country)} — ${escapeHtml(p.league)}</div>
          <div class="pred">⚡ Прогноз: ${escapeHtml(p.prediction)}</div>
          <div class="pred">🎯 Коэффициент: ${escapeHtml(p.coef)}</div>
        </div>`;
    });
    picksHtml += `<div class="total-coef">👑 Общий коэффициент: ${data.total_coef}</div></div>`;
  } else {
    picksHtml = `<div class="express-card"><div class="card-title">🚆 Экспресс дня</div>
      <p style="color:var(--text-secondary);padding:12px 0">Экспресс пока не сформирован. Загляните позже!</p></div>`;
  }
  const s = data.stats;
  const statsHtml = `
    <div class="card"><div class="card-title">📊 Статистика экспрессов</div>
    <div class="stats-grid">
      <div class="stat-box"><div class="val">${s.total}</div><div class="lbl">Всего</div></div>
      <div class="stat-box"><div class="val">${s.wins}</div><div class="lbl">✅ Заходов</div></div>
      <div class="stat-box"><div class="val">${s.losses}</div><div class="lbl">❌ Мимо</div></div>
      <div class="stat-box"><div class="val">${s.winrate.toFixed(1)}%</div><div class="lbl">Винрейт</div></div>
      <div class="stat-box"><div class="val">${s.roi >= 0 ? '+' : ''}${s.roi.toFixed(1)}%</div><div class="lbl">ROI</div></div>
      <div class="stat-box"><div class="val">${s.profit >= 0 ? '+' : ''}${s.profit.toFixed(2)}</div><div class="lbl">Прибыль</div></div>
    </div></div>`;
  let resultsHtml = `<div class="card"><div class="card-title">📋 Прошедшие прогнозы</div>`;
  if (!data.results.length) {
    resultsHtml += `<p style="color:var(--text-muted);padding:8px 0">Прошедших прогнозов пока нет.</p>`;
  } else {
    data.results.forEach(r => {
      resultsHtml += `<div class="result-item"><span>${r.result_emoji}</span>
        <span style="flex:1">🚆 ${escapeHtml(r.created_at)} | ${r.type_label} | кф. ${Number(r.coef).toFixed(2)}</span></div>`;
    });
    if (data.total_pages > 1) {
      resultsHtml += `<div class="pagination">
        <button class="page-btn" ${page <= 0 ? 'disabled' : ''} onclick="renderExpress(${page - 1})">←</button>
        <span class="page-info">${page + 1}/${data.total_pages}</span>
        <button class="page-btn" ${page >= data.total_pages - 1 ? 'disabled' : ''} onclick="renderExpress(${page + 1})">→</button>
      </div>`;
    }
  }
  resultsHtml += `</div>`;
  content.innerHTML = picksHtml + statsHtml + resultsHtml;
}

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
    ${premiumBtn}
    ${u.is_admin ? `<button class="btn btn-gold" onclick="navigate('admin')" style="margin-top:8px">🔧 Админ-панель</button>` : ''}`;
  document.getElementById('header-user').innerHTML = avatarHtml(u.photo_url, u.first_name, 'avatar');
}

async function renderFavorites() {
  const data = await api('/favorites');
  let html = `<button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">⭐ Избранные матчи</div>`;
  if (!data.favorites.length) {
    html += `<div class="empty-state"><div class="icon">⭐</div><p>У вас пока нет избранных матчей.</p></div>`;
  } else {
    data.favorites.forEach(m => {
      const time = m.match_time ? `<span class="match-time">🕒 ${escapeHtml(m.match_time)}</span>` : '';
      html += `<div class="match-item" onclick="navigate('match-detail', ${m.id})">
        ${time}<span class="match-name">${m.flag} ${escapeHtml(m.name)}</span></div>`;
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
      <p style="font-family:monospace;font-size:13px;word-break:break-all;background:var(--bg-elevated);padding:12px;border-radius:8px;margin:12px 0">
        ${escapeHtml(me.ref_link)}</p>
      <p style="color:var(--text-secondary);font-size:14px">
        🎉 За каждого приглашённого — 3 дня Premium бесплатно!<br>
        Приглашено: <b>${me.stats.ref_count}</b> чел.</p>
      <button class="btn btn-primary" onclick="copyRef('${me.ref_link}')">📋 Скопировать</button>
    </div>`;
}

function copyRef(link) {
  navigator.clipboard?.writeText(link).then(() => toast('Ссылка скопирована!')).catch(() => tg?.showAlert(link));
}

async function renderPremium() {
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="premium-card">
      <h2>⭐ Premium</h2>
      <p style="color:var(--text-secondary)">Полный доступ</p>
      <ul class="premium-features">
        <li>✅ Безлимитные матчи</li>
        <li>✅ Доступ к экспрессам</li>
        <li>✅ Полная статистика</li>
      </ul>
      <div class="premium-price">300 ₽ / месяц</div>
      <button class="btn btn-gold" id="pay-btn" onclick="createPayment()">💳 Оплатить 300 ₽</button>
    </div>
    <div id="pay-status"></div>`;
}

async function createPayment() {
  const btn = document.getElementById('pay-btn');
  btn.disabled = true;
  btn.textContent = 'Создаём платёж...';
  try {
    const res = await api('/premium/create', { method: 'POST' });
    if (res.confirmation_url) {
      document.getElementById('pay-status').innerHTML = `
        <div class="card" style="margin-top:12px">
          <p style="margin-bottom:12px">Нажмите кнопку ниже для оплаты:</p>
          <a href="${res.confirmation_url}" target="_blank" class="btn btn-primary" style="text-decoration:none">💳 Перейти к оплате</a>
          <button class="btn btn-secondary" style="margin-top:8px" onclick="checkPayment('${res.payment_id}')">✅ Я оплатил — проверить</button>
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
      setTimeout(() => navigate('account'), 1500);
    }
  } catch (e) { toast(e.message); }
}

async function renderSupport() {
  const cats = [
    { id: 'bug', label: '🐛 Баг / ошибка' },
    { id: 'pay', label: '💳 Оплата' },
    { id: 'idea', label: '💡 Предложение' },
    { id: 'q', label: '❓ Вопрос' },
  ];
  let html = `<div class="card-title">✉️ Техподдержка</div>
    <p style="color:var(--text-secondary);margin-bottom:16px">Выберите категорию обращения:</p>`;
  cats.forEach(c => {
    html += `<button class="cat-btn" onclick="navigate('support-form', '${c.id}')">${c.label}</button>`;
  });
  content.innerHTML = html;
}

async function renderSupportForm(cat) {
  const labels = { bug: '🐛 Баг / ошибка', pay: '💳 Оплата', idea: '💡 Предложение', q: '❓ Вопрос' };
  content.innerHTML = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">${labels[cat] || 'Вопрос'}</div>
    <p style="color:var(--text-secondary);margin-bottom:12px">Напишите ваше сообщение (одним сообщением):</p>
    <textarea class="textarea" id="support-text" placeholder="Опишите ваш вопрос или проблему..."></textarea>
    <button class="btn btn-primary" onclick="sendSupport('${cat}')">Отправить</button>`;
}

async function sendSupport(cat) {
  const text = document.getElementById('support-text').value.trim();
  if (!text) { toast('Введите сообщение'); return; }
  try {
    const res = await api('/support', { method: 'POST', body: JSON.stringify({ category: cat, text }) });
    toast(res.message);
    setTimeout(() => navigate('home'), 1500);
  } catch (e) { toast(e.message); }
}

/* ====================== ADMIN ====================== */

async function renderAdmin() {
  const stats = await api('/admin/stats');
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('home')">← Назад</button>
    <div class="card-title">🔧 Админ-панель</div>
    <div class="stats-grid" style="margin-bottom:16px">
      <div class="stat-box"><div class="val">${stats.total_users}</div><div class="lbl">Пользователей</div></div>
      <div class="stat-box"><div class="val">${stats.premium_users}</div><div class="lbl">Premium</div></div>
      <div class="stat-box"><div class="val">${stats.matches}</div><div class="lbl">Матчей</div></div>
    </div>
    <button class="cat-btn" onclick="navigate('admin-parser')">🔍 Парсер матчей</button>
    <button class="cat-btn" onclick="navigate('admin-manual')">✍️ Добавить матч вручную</button>
    <button class="cat-btn" onclick="navigate('admin-texts')">📝 Тексты матчей (прогнозы)</button>
    <button class="cat-btn" onclick="navigate('admin-delete')">🗑 Удалить матчи</button>
    <button class="cat-btn" onclick="navigate('admin-express')">🚆 Экспресс дня</button>
    <button class="cat-btn" onclick="navigate('admin-premium')">⭐ Управление Premium</button>
    <button class="cat-btn" onclick="navigate('admin-support')">✉️ Сообщения поддержки</button>
    <button class="cat-btn" onclick="navigate('admin-logs')">📋 Логи админа</button>`;
}

async function renderAdminParser() {
  const data = await api('/admin/parser/dates');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🔍 Парсер матчей</div>`;
  if (!data.has_api_key) {
    html += `<div class="empty-state"><div class="icon">⚠️</div>
      <p>BSD_API_KEY не задан в .env</p>
      <p style="font-size:13px;color:var(--text-muted)">Добавьте ключ и перезапустите сервер</p></div>`;
  } else {
    html += `<p style="color:var(--text-secondary);margin-bottom:16px">Выберите дату — матчи загрузятся с API:</p>`;
    data.dates.forEach(d => {
      html += `<button class="cat-btn" onclick="parserLoad('${d.value}')">${d.label}</button>`;
    });
  }
  content.innerHTML = html;
}

async function parserLoad(dateStr) {
  content.innerHTML = `<div class="empty-state"><div class="icon">⏳</div><p>Загружаю матчи с API...</p></div>`;
  try {
    const res = await api('/admin/parser/load', {
      method: 'POST',
      body: JSON.stringify({ date: dateStr }),
    });
    toast(res.message);
    if (res.total === 0) {
      content.innerHTML = `
        <button class="btn-back" onclick="navigate('admin-parser')">← Назад</button>
        <div class="empty-state"><div class="icon">❌</div>
          <p>${escapeHtml(res.message)}</p>
          <p style="font-size:13px;color:var(--text-muted)">Попробуйте выходные — там больше матчей</p>
        </div>`;
      return;
    }
    parserLeagues = res.leagues;
    navigate('admin-parser-leagues', { total: res.total, leagues: res.leagues }, false);
  } catch (e) {
    toast(e.message);
    navigate('admin-parser', null, false);
  }
}

async function renderAdminParserLeagues(data) {
  const leagues = data?.leagues || parserLeagues;
  const total = data?.total || leagues.reduce((s, l) => s + l.count, 0);
  let html = `
    <button class="btn-back" onclick="navigate('admin-parser')">← Назад</button>
    <div class="card-title">📋 Найдено: ${total} матчей</div>
    <p style="color:var(--text-secondary);font-size:13px;margin-bottom:12px">Выберите лигу или добавьте всё:</p>
    <button class="btn btn-primary" onclick="parserAddAll()" style="margin-bottom:12px">✅ Добавить ВСЕ (до 80 матчей)</button>
    <div class="section-title">По лигам (приоритет)</div>`;
  leagues.forEach(l => {
    html += `
      <div class="group-card" onclick="navigate('admin-parser-matches', {league_id: ${l.league_id}, league: '${escapeHtml(l.league).replace(/'/g, "\\'")}', country: '${escapeHtml(l.country).replace(/'/g, "\\'")}', flag: '${l.flag}'})">
        <div class="group-left">
          <span class="group-flag">${l.flag}</span>
          <div class="group-info">
            <div class="country">P${l.priority} ${escapeHtml(l.country)}</div>
            <div class="league">${escapeHtml(l.league)}</div>
          </div>
        </div>
        <span class="group-count">${l.count}</span>
      </div>`;
  });
  content.innerHTML = html;
}

async function renderAdminParserMatches(data) {
  const leagueId = data.league_id;
  const res = await api(`/admin/parser/matches?league_id=${leagueId}&page=0`);
  let html = `
    <button class="btn-back" onclick="goBack()">← Назад</button>
    <div class="card-title">${data.flag || ''} ${escapeHtml(data.country || '')} — ${escapeHtml(data.league || '')}</div>
    <button class="btn btn-primary" onclick="parserAddLeague(${leagueId})" style="margin-bottom:12px">✅ Добавить ВСЮ ЛИГУ</button>`;
  if (!res.matches.length) {
    html += `<div class="empty-state"><p>Матчей нет</p></div>`;
  } else {
    res.matches.forEach(m => {
      const time = m.match_time ? `🕒 ${escapeHtml(m.match_time)} | ` : '';
      html += `
        <div class="match-item" style="justify-content:space-between">
          <span class="match-name">${time}${escapeHtml(m.name)}</span>
          <button class="btn btn-secondary" style="width:auto;margin:0;padding:6px 12px;font-size:12px"
            onclick="parserAddSingle(${m.id}, this)">➕</button>
        </div>`;
    });
  }
  content.innerHTML = html;
}

async function parserAddAll() {
  try {
    const res = await api('/admin/parser/add', { method: 'POST', body: JSON.stringify({ mode: 'all', limit: 80 }) });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function parserAddLeague(leagueId) {
  try {
    const res = await api('/admin/parser/add', { method: 'POST', body: JSON.stringify({ mode: 'league', league_id: leagueId }) });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function parserAddSingle(parsedId, btn) {
  try {
    const res = await api('/admin/parser/add', { method: 'POST', body: JSON.stringify({ mode: 'single', parsed_id: parsedId }) });
    toast(res.message);
    if (res.ok && btn) { btn.textContent = '✅'; btn.disabled = true; }
  } catch (e) { toast(e.message); }
}

async function renderAdminManual() {
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">✍️ Добавить матч вручную</div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Время (ЧЧ:ММ или -)</p>
      <input class="textarea" id="m-time" style="min-height:auto;height:44px" placeholder="18:30" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Команды</p>
      <input class="textarea" id="m-name" style="min-height:auto;height:44px" placeholder="Спартак — Зенит" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Страна</p>
      <input class="textarea" id="m-country" style="min-height:auto;height:44px" placeholder="Russia" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Лига</p>
      <input class="textarea" id="m-league" style="min-height:auto;height:44px" placeholder="Russian Premier League" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Текст прогноза (опционально)</p>
      <textarea class="textarea" id="m-text" placeholder="Прогноз..."></textarea>
      <button class="btn btn-primary" onclick="manualAddMatch()">✅ Добавить</button>
    </div>`;
}

async function manualAddMatch() {
  const name = document.getElementById('m-name').value.trim();
  const country = document.getElementById('m-country').value.trim();
  const league = document.getElementById('m-league').value.trim();
  let match_time = document.getElementById('m-time').value.trim();
  if (match_time === '-') match_time = '';
  const text = document.getElementById('m-text').value.trim();
  if (!name || !country || !league) { toast('Заполните название, страну и лигу'); return; }
  try {
    const res = await api('/admin/matches/manual', {
      method: 'POST',
      body: JSON.stringify({ name, country, league, match_time, text }),
    });
    toast(res.message);
    navigate('admin');
  } catch (e) { toast(e.message); }
}

async function renderAdminTexts() {
  const data = await api('/admin/matches/no-text');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">📝 Тексты матчей</div>`;
  if (!data.matches.length) {
    html += `<div class="empty-state"><div class="icon">✅</div><p>Все матчи имеют текст прогноза!</p></div>`;
  } else {
    html += `<p style="color:var(--text-secondary);margin-bottom:12px">${data.matches.length} без текста:</p>`;
    data.matches.forEach(m => {
      const time = m.match_time ? `🕒 ${escapeHtml(m.match_time)} | ` : '';
      html += `
        <div class="match-item" onclick="navigate('admin-text-edit', ${m.id})">
          <span class="match-name">📝 ${m.flag} ${time}${escapeHtml(m.name)}</span>
        </div>`;
    });
  }
  content.innerHTML = html;
}

async function renderAdminTextEdit(matchId) {
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin-texts')">← Назад</button>
    <div class="card-title">📝 Текст прогноза</div>
    <p style="color:var(--text-secondary);margin-bottom:8px">Матч ID: ${matchId}</p>
    <textarea class="textarea" id="edit-text" placeholder="Введите текст прогноза..."></textarea>
    <button class="btn btn-primary" onclick="saveMatchText(${matchId})">💾 Сохранить</button>`;
}

async function saveMatchText(matchId) {
  const text = document.getElementById('edit-text').value.trim();
  if (!text) { toast('Введите текст'); return; }
  try {
    const res = await api(`/admin/matches/${matchId}/text`, {
      method: 'PUT',
      body: JSON.stringify({ match_id: matchId, text }),
    });
    toast(res.message);
    navigate('admin-texts');
  } catch (e) { toast(e.message); }
}

async function renderAdminDelete() {
  const data = await api('/admin/matches');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🗑 Удалить матчи</div>
    <button class="btn btn-danger" onclick="deleteAllMatches()" style="margin-bottom:12px">🗑 Очистить все</button>`;
  if (!data.matches.length) {
    html += `<div class="empty-state"><p>Матчей нет</p></div>`;
  } else {
    data.matches.forEach(m => {
      const time = m.match_time ? `🕒 ${escapeHtml(m.match_time)} | ` : '';
      html += `
        <div class="match-item" style="justify-content:space-between">
          <span class="match-name">${m.flag} ${time}${escapeHtml(m.name)}</span>
          <button class="btn btn-danger" style="width:auto;margin:0;padding:6px 12px;font-size:12px"
            onclick="deleteOneMatch(${m.id})">🗑</button>
        </div>`;
    });
  }
  content.innerHTML = html;
}

async function deleteOneMatch(id) {
  if (!confirm('Удалить матч?')) return;
  try {
    const res = await api(`/admin/matches/${id}`, { method: 'DELETE' });
    toast(res.message);
    renderAdminDelete();
  } catch (e) { toast(e.message); }
}

async function deleteAllMatches() {
  if (!confirm('Удалить ВСЕ матчи?')) return;
  try {
    const res = await api('/admin/matches', { method: 'DELETE' });
    toast(res.message);
    renderAdminDelete();
  } catch (e) { toast(e.message); }
}

async function renderAdminExpress() {
  const data = await api('/admin/express');
  let picksHtml = '';
  if (data.picks.length) {
    data.picks.forEach(p => {
      picksHtml += `
        <div class="express-pick">
          <div class="name">⚽ ${escapeHtml(p.name)}</div>
          <div class="meta">${p.flag} ${escapeHtml(p.country)} — ${escapeHtml(p.league)}</div>
          <div class="pred">⚡ ${escapeHtml(p.prediction)} | 🎯 ${escapeHtml(p.coef)}</div>
        </div>`;
    });
  } else {
    picksHtml = `<p style="color:var(--text-muted);padding:8px 0">Экспресс пуст</p>`;
  }
  const s = data.stats;
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">🚆 Управление экспрессом</div>
    <div class="express-card">${picksHtml}</div>
    <div class="card">
      <div class="card-title">📊 Статистика</div>
      <div class="stats-grid">
        <div class="stat-box"><div class="val">${s.total}</div><div class="lbl">Всего</div></div>
        <div class="stat-box"><div class="val">${s.winrate.toFixed(1)}%</div><div class="lbl">Винрейт</div></div>
        <div class="stat-box"><div class="val">${s.roi >= 0 ? '+' : ''}${s.roi.toFixed(1)}%</div><div class="lbl">ROI</div></div>
        <div class="stat-box"><div class="val">${s.profit >= 0 ? '+' : ''}${s.profit.toFixed(2)}</div><div class="lbl">Прибыль</div></div>
      </div>
    </div>
    <button class="btn btn-primary" onclick="navigate('admin-express-add')">➕ Добавить матч в экспресс</button>
    <button class="btn btn-danger" onclick="clearExpress()">🗑 Очистить экспресс</button>
    <button class="btn btn-secondary" onclick="navigate('admin-result-add')">📝 Ввести результат</button>
    <button class="btn btn-secondary" onclick="navigate('admin-results')">🗑 Удалить результаты</button>`;
}

async function clearExpress() {
  if (!confirm('Очистить экспресс?')) return;
  try {
    const res = await api('/admin/express', { method: 'DELETE' });
    toast(res.message);
    renderAdminExpress();
  } catch (e) { toast(e.message); }
}

async function renderAdminExpressAdd() {
  const data = await api('/admin/matches');
  let html = `
    <button class="btn-back" onclick="navigate('admin-express')">← Назад</button>
    <div class="card-title">➕ Выберите матч для экспресса</div>`;
  if (!data.matches.length) {
    html += `<div class="empty-state"><p>Сначала добавьте матчи</p></div>`;
  } else {
    data.matches.forEach(m => {
      const time = m.match_time ? `🕒 ${escapeHtml(m.match_time)} | ` : '';
      const safeName = escapeHtml(m.name).replace(/'/g, "\\'");
      html += `
        <div class="match-item" onclick="showExpressForm(${m.id}, '${safeName}')">
          <span class="match-name">${m.flag} ${time}${escapeHtml(m.name)}</span>
        </div>`;
    });
  }
  content.innerHTML = html;
}

function showExpressForm(matchId, name) {
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin-express-add')">← Назад</button>
    <div class="card-title">Матч: ${name}</div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Прогноз (например: П1 или ТБ 2.5)</p>
      <input class="textarea" id="ex-pred" style="min-height:auto;height:44px" placeholder="П1" />
      <p style="font-size:13px;color:var(--text-secondary);margin:12px 0 8px">Коэффициент</p>
      <input class="textarea" id="ex-coef" style="min-height:auto;height:44px" placeholder="1.85" />
      <button class="btn btn-primary" onclick="addExpressPick(${matchId})">✅ Добавить</button>
    </div>`;
}

async function addExpressPick(matchId) {
  const prediction = document.getElementById('ex-pred').value.trim();
  const coef = document.getElementById('ex-coef').value.trim();
  if (!prediction || !coef) { toast('Заполните прогноз и коэффициент'); return; }
  try {
    const res = await api('/admin/express', {
      method: 'POST',
      body: JSON.stringify({ match_id: matchId, prediction, coef }),
    });
    toast(res.message);
    navigate('admin-express');
  } catch (e) { toast(e.message); }
}

async function renderAdminResultAdd() {
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin-express')">← Назад</button>
    <div class="card-title">📝 Результат экспресса</div>
    <p style="color:var(--text-secondary);margin-bottom:12px">Выберите тип:</p>
    <button class="cat-btn" onclick="showResultCoefs('double')">🎲 Двойник (2 матча)</button>
    <button class="cat-btn" onclick="showResultCoefs('triple')">🎲 Тройник (3 матча)</button>`;
}

function showResultCoefs(type) {
  const count = type === 'double' ? 2 : 3;
  const example = type === 'double' ? '1.85 2.10' : '1.85 2.10 1.65';
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin-result-add')">← Назад</button>
    <div class="card-title">${type === 'double' ? '🎲 Двойник' : '🎲 Тройник'}</div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">
        Введите ${count} коэффициента через пробел (например: ${example})</p>
      <input class="textarea" id="res-coefs" style="min-height:auto;height:44px" placeholder="${example}" />
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn btn-primary" style="flex:1" onclick="saveResult('${type}', 'win')">✅ Зашёл</button>
        <button class="btn btn-danger" style="flex:1" onclick="saveResult('${type}', 'lose')">❌ Не зашёл</button>
      </div>
    </div>`;
}

async function saveResult(type, result) {
  const parts = document.getElementById('res-coefs').value.trim().split(/\s+/);
  const expected = type === 'double' ? 2 : 3;
  if (parts.length !== expected) { toast(`Нужно ровно ${expected} коэффициента`); return; }
  const coefs = parts.map(p => parseFloat(p.replace(',', '.')));
  if (coefs.some(c => isNaN(c) || c <= 1)) { toast('Коэффициенты должны быть числами > 1'); return; }
  try {
    const res = await api('/admin/results', {
      method: 'POST',
      body: JSON.stringify({ type, coefs, result }),
    });
    toast(res.message);
    navigate('admin-express');
  } catch (e) { toast(e.message); }
}

async function renderAdminResults() {
  const data = await api('/admin/results');
  let html = `
    <button class="btn-back" onclick="navigate('admin-express')">← Назад</button>
    <div class="card-title">🗑 Результаты</div>
    <button class="btn btn-danger" onclick="deleteAllResults()" style="margin-bottom:12px">🗑 Очистить все</button>`;
  if (!data.results.length) {
    html += `<div class="empty-state"><p>Результатов нет</p></div>`;
  } else {
    data.results.forEach(r => {
      html += `
        <div class="match-item" style="justify-content:space-between">
          <span class="match-name">${r.result_emoji} ${r.type_label} | кф ${Number(r.coef).toFixed(2)} | ${escapeHtml((r.created_at || '').slice(0, 10))}</span>
          <button class="btn btn-danger" style="width:auto;margin:0;padding:6px 12px;font-size:12px"
            onclick="deleteOneResult(${r.id})">🗑</button>
        </div>`;
    });
  }
  content.innerHTML = html;
}

async function deleteOneResult(id) {
  try {
    const res = await api(`/admin/results/${id}`, { method: 'DELETE' });
    toast(res.message);
    renderAdminResults();
  } catch (e) { toast(e.message); }
}

async function deleteAllResults() {
  if (!confirm('Удалить все результаты?')) return;
  try {
    const res = await api('/admin/results', { method: 'DELETE' });
    toast(res.message);
    renderAdminResults();
  } catch (e) { toast(e.message); }
}

async function renderAdminPremium() {
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">⭐ Управление Premium</div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Выдать Premium по ID</p>
      <input class="textarea" id="prem-id" style="min-height:auto;height:44px" placeholder="Telegram ID" />
      <button class="btn btn-primary" onclick="givePremium()" style="margin-top:8px">⭐ Выдать на 30 дней</button>
    </div>
    <div class="card">
      <p style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">Снять Premium по ID</p>
      <input class="textarea" id="prem-rm-id" style="min-height:auto;height:44px" placeholder="Telegram ID" />
      <button class="btn btn-danger" onclick="removePremium()" style="margin-top:8px">🚫 Снять</button>
    </div>
    <div class="section-title">Выдать всем Free</div>
    <button class="cat-btn" onclick="giveAllPremium(30)">1 месяц (30 дней)</button>
    <button class="cat-btn" onclick="giveAllPremium(90)">3 месяца (90 дней)</button>
    <button class="cat-btn" onclick="giveAllPremium(180)">6 месяцев</button>
    <button class="cat-btn" onclick="giveAllPremium(365)">1 год</button>
    <button class="cat-btn" onclick="giveAllPremium('forever')">♾ Навсегда</button>
    <button class="btn btn-danger" onclick="removeAllPremium()" style="margin-top:12px">🚫 Снять со всех Premium</button>`;
}

async function givePremium() {
  const id = parseInt(document.getElementById('prem-id').value.trim());
  if (!id) { toast('Введите ID'); return; }
  try {
    const res = await api(`/admin/premium/${id}?days=30`, { method: 'POST' });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function removePremium() {
  const id = parseInt(document.getElementById('prem-rm-id').value.trim());
  if (!id) { toast('Введите ID'); return; }
  try {
    const res = await api(`/admin/premium/${id}`, { method: 'DELETE' });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function giveAllPremium(days) {
  if (!confirm('Выдать Premium всем Free (' + days + ')?')) return;
  try {
    const res = await api('/admin/premium/all', { method: 'POST', body: JSON.stringify({ days }) });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function removeAllPremium() {
  if (!confirm('Снять Premium со ВСЕХ?')) return;
  try {
    const res = await api('/admin/premium/all', { method: 'DELETE' });
    toast(res.message);
  } catch (e) { toast(e.message); }
}

async function renderAdminSupport() {
  const data = await api('/admin/support');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">✉️ Сообщения поддержки</div>
    <button class="btn btn-danger" onclick="clearAllSupport()" style="margin-bottom:12px">🗑 Очистить все</button>`;
  if (!data.threads.length) {
    html += `<div class="empty-state"><p>Сообщений нет</p></div>`;
  } else {
    data.threads.forEach(t => {
      html += `
        <div class="group-card" onclick="navigate('admin-support-thread', ${t.thread_id})">
          <div class="group-left">
            <div class="group-info">
              <div class="country">${escapeHtml(t.category_label)} | ${escapeHtml(t.user_name)}</div>
              <div class="league">${escapeHtml((t.created_at || '').slice(0, 10))} · ID ${t.thread_id}</div>
            </div>
          </div>
        </div>`;
    });
  }
  content.innerHTML = html;
}

async function renderAdminSupportThread(threadId) {
  const data = await api(`/admin/support/${threadId}`);
  let msgs = '';
  data.messages.forEach(m => {
    const label = m.sender === 'user' ? '👤 Пользователь' : '🏢 Админ';
    msgs += `<div style="padding:10px 0;border-bottom:1px solid var(--border)">
      <div style="font-size:12px;color:var(--text-muted)">[${escapeHtml(m.created_at)}] ${label}</div>
      <div style="margin-top:4px">${escapeHtml(m.text)}</div>
    </div>`;
  });
  content.innerHTML = `
    <button class="btn-back" onclick="navigate('admin-support')">← Назад</button>
    <div class="card-title">Переписка #${threadId}</div>
    <div class="card">${msgs || '<p style="color:var(--text-muted)">Пусто</p>'}</div>
    <textarea class="textarea" id="reply-text" placeholder="Ответ пользователю..."></textarea>
    <button class="btn btn-primary" onclick="replySupport(${threadId})">💬 Ответить</button>
    <button class="btn btn-danger" onclick="clearThread(${threadId})">🗑 Очистить</button>`;
}

async function replySupport(threadId) {
  const text = document.getElementById('reply-text').value.trim();
  if (!text) { toast('Введите ответ'); return; }
  try {
    const res = await api(`/admin/support/${threadId}/reply`, {
      method: 'POST',
      body: JSON.stringify({ text }),
    });
    toast(res.message);
    renderAdminSupportThread(threadId);
  } catch (e) { toast(e.message); }
}

async function clearThread(threadId) {
  if (!confirm('Очистить переписку?')) return;
  try {
    const res = await api(`/admin/support/${threadId}`, { method: 'DELETE' });
    toast(res.message);
    navigate('admin-support');
  } catch (e) { toast(e.message); }
}

async function clearAllSupport() {
  if (!confirm('Очистить ВСЕ переписки?')) return;
  try {
    const res = await api('/admin/support', { method: 'DELETE' });
    toast(res.message);
    renderAdminSupport();
  } catch (e) { toast(e.message); }
}

async function renderAdminLogs() {
  const data = await api('/admin/logs');
  let html = `
    <button class="btn-back" onclick="navigate('admin')">← Назад</button>
    <div class="card-title">📋 Логи админа</div>`;
  if (!data.logs.length) {
    html += `<div class="empty-state"><p>Логов пока нет</p></div>`;
  } else {
    html += `<div class="card">`;
    data.logs.forEach(l => {
      html += `<div style="padding:8px 0;border-bottom:1px solid var(--border);font-size:13px">
        <span style="color:var(--text-muted)">[${escapeHtml(l.created_at)}]</span>
        <b>${escapeHtml(l.action)}</b> (${l.admin_id}): ${escapeHtml(l.details)}
      </div>`;
    });
    html += `</div>`;
  }
  content.innerHTML = html;
}

async function init() {
  try {
    currentUser = await api('/me');
    const u = currentUser.user;
    document.getElementById('header-user').innerHTML =
      avatarHtml(u.photo_url, u.first_name, 'avatar');
    if (u.is_admin) {
      const nav = document.getElementById('bottom-nav');
      const btn = document.createElement('button');
      btn.className = 'nav-btn';
      btn.dataset.page = 'admin';
      btn.innerHTML = `<span class="nav-icon">🔧</span><span class="nav-label">Админ</span>`;
      nav.appendChild(btn);
    }
    bindNav();
    document.getElementById('loader').classList.add('hidden');
    document.getElementById('main').classList.remove('hidden');
    navigate('home', null, false);
  } catch (e) {
    document.getElementById('loader').innerHTML = `
      <div style="text-align:center;padding:40px">
        <div style="font-size:48px;margin-bottom:16px">⚠️</div>
        <p style="color:var(--text-secondary)">${escapeHtml(e.message)}</p>
        <p style="color:var(--text-muted);font-size:13px;margin-top:12px">Откройте приложение через Telegram</p>
      </div>`;
  }
}

init();
