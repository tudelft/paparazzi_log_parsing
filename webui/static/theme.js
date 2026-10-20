(function () {
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
    ['/', 'Uploads'],
    ['/logbook', 'Logbook'],
    ['/stats', 'Statistics'],
    ['/people', 'People'],
    ['/projects', 'Projects'],
    ['/aircraft', 'Aircraft'],
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
    if (p === '/logbook' || p.startsWith('/flight/')) return '/logbook';
    if (p === '/stats') return '/stats';
    if (p === '/people') return '/people';
    if (p === '/projects') return '/projects';
    if (p === '/aircraft') return '/aircraft';
    if (p === '/locations') return '/locations';
    if (p === '/' || p === '') return '/';
    return '';
  }
  function paintNav(admin) {
    const nav = document.getElementById('nav');
    if (!nav) return;
    const here = currentTab();
    const items = TABS.filter(([href]) => href !== '/locations' || admin);
    nav.innerHTML = items.map(([href, label]) =>
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

  let admin = cachedAdmin() || currentTab() === '/locations';
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  ready(() => {
    paintNav(admin);
    paintEdit();
    const who = document.getElementById('who');
    if (who && !/^\/(login|setup)/.test(location.pathname)) {
      who.style.cursor = 'pointer';
      who.style.textDecoration = 'underline';
      who.title = 'Your account and API keys';
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
      paintNav(admin);
      paintEdit();
    }).catch(() => {});
  }
})();
