window.flightDateRange = {
  periodStart(range) {
    if (!range || range === 'all') return null;
    const now = new Date();
    const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    const n = parseInt(range, 10), unit = range.slice(-1);
    if (unit === 'd') start.setUTCDate(start.getUTCDate() - n + 1);
    else if (unit === 'm') {
      start.setUTCMonth(start.getUTCMonth() - n);
      start.setUTCDate(start.getUTCDate() + 1);
    } else {
      start.setUTCFullYear(start.getUTCFullYear() - n);
      start.setUTCDate(start.getUTCDate() + 1);
    }
    return start.toISOString().slice(0, 10);
  },
  sync() {
    const el = id => document.getElementById(id);
    const custom = el('specificRange').checked;
    el('customRange').hidden = !custom;
    el('period').disabled = custom;
    if (custom && !el('fromDate').value && !el('toDate').value) {
      const start = this.periodStart(el('period').value);
      if (start) el('fromDate').value = start;
      el('toDate').value = new Date().toISOString().slice(0, 10);
    }
  },
  includes(f) {
    const el = id => document.getElementById(id);
    const custom = el('specificRange').checked;
    const from = custom ? el('fromDate').value : this.periodStart(el('period').value) || '';
    const to = custom ? el('toDate').value : '';
    if (!from && !to) return true;
    const day = (f.date_ok ? f.takeoff_utc || f.date_override || '' : f.date_override || '').slice(0, 10);
    return !!day && (!from || day >= from) && (!to || day <= to);
  },
};

(function () {
  const icon = document.createElement('link');
  icon.rel = 'icon';
  icon.type = 'image/svg+xml';
  icon.href = '/favicon.svg';
  document.head.appendChild(icon);

  try {
    if (localStorage.getItem('logdbNight') === '1')
      document.documentElement.classList.add('night');
  } catch (e) {}
  window.toggleNight = function () {
    const root = document.documentElement;
    root.classList.toggle('night');
    try { localStorage.setItem('logdbNight', root.classList.contains('night') ? '1' : '0'); } catch (e) {}
    window.dispatchEvent(new Event('nightchange'));
  };

  const ADMIN_KEY = 'logdbAdmin';
  const EDIT_KEY = 'logdbEdit';
  window.editMode = function () {
    try { return localStorage.getItem(EDIT_KEY) === '1'; } catch (e) { return false; }
  };
  window.toggleEditMode = function () {
    try { localStorage.setItem(EDIT_KEY, window.editMode() ? '0' : '1'); } catch (e) {}
    paintEdit();
    window.dispatchEvent(new Event('editchange'));
  };
  const TABS = [
    ['/', 'Logbook'],
    ['/uploads', 'Uploads'],
    ['/stats', 'Statistics'],
    ['/aircraft', 'Aircraft'],
    ['/people', 'People'],
    ['/projects', 'Projects'],
    ['/locations', 'Locations'],
  ];

  function cachedAdmin() {
    try { return localStorage.getItem(ADMIN_KEY) === '1'; } catch (e) { return false; }
  }
  function setCachedAdmin(on) {
    try { localStorage.setItem(ADMIN_KEY, on ? '1' : '0'); } catch (e) {}
  }
  function currentTab() {
    const p = location.pathname;
    if (p === '/logbook' || p === '/attachments' || p.startsWith('/flight/') || p.startsWith('/historical/')) return '/';
    if (p === '/uploads') return '/uploads';
    if (p === '/stats') return '/stats';
    for (const tab of ['/people', '/projects', '/aircraft', '/locations'])
      if (p === tab || p.startsWith(tab + '/')) return tab;
    if (p === '/' || p === '') return '/';
    return '';
  }
  function paintNav() {
    const nav = document.getElementById('nav');
    if (!nav) return;
    const here = currentTab();
    nav.innerHTML = TABS.map(([href, label]) =>
      `<a href="${href}"${href === here ? ' class="cur" aria-current="page"' : ''}>${label}</a>`).join('');
  }

  // Pages opt in with <body data-edit="all"> or <body data-edit="admin">.
  function paintEdit() {
    const night = document.getElementById('nightBtn');
    const kind = document.body && document.body.dataset.edit;
    if (!night || !kind) return;
    let btn = document.getElementById('editBtn');
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.id = 'editBtn';
      btn.textContent = 'Edit';
      btn.title = 'Edit mode: show the forms and menus to change things on the pages that have them';
      btn.onclick = window.toggleEditMode;
      night.before(btn, ' ');
    }
    btn.hidden = kind === 'admin' && !admin;
    btn.setAttribute('aria-pressed', window.editMode() ? 'true' : 'false');
  }

  window.logout = async function () {
    try { localStorage.removeItem(ADMIN_KEY); } catch (e) {}
    await fetch('/logout', {method: 'POST'});
    location.href = '/login';
  };

  let admin = cachedAdmin();
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  ready(() => {
    paintNav();
    paintEdit();
    const who = document.getElementById('who');
    if (who && !/^\/(login|setup)/.test(location.pathname)) {
      who.style.cursor = 'pointer';
      who.style.textDecoration = 'underline';
      who.title = 'Your account, passwords and API keys';
      who.onclick = () => { location.href = '/account'; };
    }
  });

  if (!/^\/(login|setup)/.test(location.pathname)) {
    fetch('/api/me').then(async r => {
      if (r.status === 401) {
        try { localStorage.removeItem(ADMIN_KEY); } catch (e) {}
        return;
      }
      const d = await r.json().catch(() => ({}));
      admin = !!(d.user && d.user.is_admin);
      setCachedAdmin(admin);
      paintEdit();
    }).catch(() => {});
  }
})();
