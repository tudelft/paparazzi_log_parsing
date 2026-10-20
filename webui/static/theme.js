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
  const TABS = [
    ['/', 'Uploads'],
    ['/logbook', 'Logbook'],
    ['/stats', 'Statistics'],
    ['/admin', 'Admin'],
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
    if (p === '/admin' || p === '/projects' || p === '/aircraft') return '/admin';
    if (p === '/' || p === '') return '/';
    return '';
  }
  function paintNav(admin) {
    const nav = document.getElementById('nav');
    if (!nav) return;
    const here = currentTab();
    const items = TABS.filter(([href]) => href !== '/admin' || admin);
    nav.innerHTML = items.map(([href, label], i) => {
      const on = href === here;
      return (i ? ' · ' : '') + `<a href="${href}" class="${on ? 'cur' : ''}">${label}</a>`;
    }).join('');
  }

  window.logout = async function () {
    try { localStorage.removeItem(ADMIN_KEY); } catch (e) {}
    await fetch('/logout', {method: 'POST'});
    location.href = '/login';
  };

  let admin = cachedAdmin() || currentTab() === '/admin';
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  ready(() => {
    paintNav(admin);
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
    }).catch(() => {});
  }
})();
