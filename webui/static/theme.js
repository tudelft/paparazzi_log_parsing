(function () {
  const KEY = 'logdbFlightFilters';
  const PERIODS = {'30d': 'Last 30 days', '3m': 'Last 3 months', '1y': 'Last year',
    '2y': 'Last 2 years', '3y': 'Last 3 years', '5y': 'Last 5 years', all: 'All time', custom: 'Custom dates'};
  const defaults = () => ({period: '3y', from: '', to: '', indoor: false, nonflights: false, unknownDates: false});
  let storageWarning = '';
  const validDay = s => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s)
    && !Number.isNaN(Date.parse(s)) && new Date(s).toISOString().slice(0, 10) === s;
  function read() {
    storageWarning = '';
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return defaults();
      const value = JSON.parse(raw);
      if (!value || !Object.hasOwn(PERIODS, value.period)
          || !['from', 'to'].every(k => value[k] === '' || validDay(value[k]))
          || typeof value.indoor !== 'boolean' || typeof value.nonflights !== 'boolean'
          || (value.unknownDates !== undefined && typeof value.unknownDates !== 'boolean')
          || (value.from && value.to && value.from > value.to)) {
        throw new Error('Invalid saved flight filters');
      }
      return {period: value.period, from: value.from, to: value.to,
        indoor: value.indoor, nonflights: value.nonflights, unknownDates: value.unknownDates ?? false};
    } catch (error) {
      console.warn('Could not restore flight filters:', error);
      storageWarning = 'Saved filters could not be restored. Using the default selection.';
      return defaults();
    }
  }
  let state = read();
  const api = window.flightFilters = {
  get state() { return {...state}; },
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
  bounds() {
    return state.period === 'custom' ? {from: state.from, to: state.to}
      : {from: this.periodStart(state.period) || '', to: ''};
  },
  day(f) {
    return (f.date_ok ? f.takeoff_utc || f.date_override || '' : f.date_override || '').slice(0, 10);
  },
  includesDate(f) {
    const {from, to} = this.bounds();
    const day = this.day(f);
    return day ? (!from || day >= from) && (!to || day <= to) : state.unknownDates;
  },
  includes(f) {
    return (state.indoor || !f.indoor)
      && (state.nonflights || (!f.simulation?.detected && !['ground', 'uncertain'].includes(f.verdict)))
      && this.includesDate(f);
  },
  logs(logs, allFlights, shownFlights) {
    const ids = new Set(shownFlights.flatMap(f => f.log_ids || []));
    const flightIds = new Set(allFlights.flatMap(f => f.log_ids || []));
    return logs.filter(l => ids.has(l.id) || (state.nonflights && l.status === 'done'
      && l.num_flights === 0 && !flightIds.has(l.id)
      && this.includesDate({takeoff_utc: l.start_utc, date_ok: l.date_ok, date_override: l.date_override})));
  },
  params() {
    const {from, to} = this.bounds();
    return new URLSearchParams({from, to, indoor: state.indoor ? '1' : '0',
      nonflights: state.nonflights ? '1' : '0', unknown_dates: state.unknownDates ? '1' : '0'});
  },
  periodLabel() {
    return state.period === 'custom' ? `${state.from || 'Any date'} – ${state.to || 'Any date'}`
      : PERIODS[state.period];
  },
  summary() {
    return `${this.periodLabel()} · Indoor/no-GPS ${state.indoor ? 'shown' : 'hidden'} · Non-flights ${state.nonflights ? 'shown' : 'hidden'} · Unknown dates ${state.unknownDates ? 'shown' : 'hidden'}`;
  },
};

  function changed() {
    paint();
    window.dispatchEvent(new Event('flightfilterschange'));
  }
  function paint() {
    const button = document.getElementById('flightFiltersButton');
    if (!button) return;
    const extra = Number(state.indoor) + Number(state.nonflights) + Number(state.unknownDates);
    button.textContent = `Flight filters · ${api.periodLabel()}${extra ? ' · +' + extra : ''} ▾`;
    button.title = api.summary();
    document.querySelectorAll('[data-flight-filter-summary]').forEach(el => { el.textContent = api.summary(); });
    document.getElementById('flightFiltersNotice').textContent = storageWarning;
  }
  function mount() {
    const nav = document.getElementById('nav');
    if (!nav || /^\/(login|setup)/.test(location.pathname)) return;
    const wrapper = document.createElement('div');
    wrapper.id = 'flightFiltersMenu';
    wrapper.innerHTML = `<button type="button" id="flightFiltersButton" aria-expanded="false" aria-controls="flightFiltersPanel"></button>
      <form id="flightFiltersPanel" hidden aria-label="Flight filters">
        <div class="filter-heading"><strong>Flight filters</strong><button type="button" id="flightFiltersReset">Reset</button></div>
        <label class="filter-field">Period (UTC)<select name="period">${Object.entries(PERIODS).map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select></label>
        <div class="filter-dates" hidden>
          <label class="filter-field">From<input type="date" name="from"></label>
          <label class="filter-field">To<input type="date" name="to"></label>
        </div>
        <div class="filter-options">
          <label><input type="checkbox" name="indoor"> Show indoor / no-GPS flights</label>
          <label><input type="checkbox" name="nonflights"> Show non-flights</label>
          <p>Non-flights include simulations, uncertain flights, ground records and zero-flight log files.</p>
          <label><input type="checkbox" name="unknownDates"> Show unknown dates</label>
          <p>Include records without a known date alongside the selected period. Other visibility filters still apply.</p>
        </div>
        <p>Shared across flight overviews in this browser. Changing pages keeps these settings.</p>
        <p id="flightFiltersNotice" role="status"></p>
        <div class="filter-actions"><button type="button" id="flightFiltersCancel">Cancel</button><button type="submit">Apply filters</button></div>
      </form>`;
    nav.after(wrapper);
    document.body.querySelector('header').classList.add('has-flight-filters');
    const button = wrapper.querySelector('#flightFiltersButton'), form = wrapper.querySelector('form');
    const fields = form.elements;
    function syncDates() {
      const custom = fields.period.value === 'custom';
      form.querySelector('.filter-dates').hidden = !custom;
      fields.from.disabled = fields.to.disabled = !custom;
      fields.to.setCustomValidity('');
    }
    function fill(value) {
      for (const key of ['period', 'from', 'to']) fields[key].value = value[key];
      for (const key of ['indoor', 'nonflights', 'unknownDates']) fields[key].checked = value[key];
      syncDates();
    }
    function close(focus = false) {
      form.hidden = true;
      button.setAttribute('aria-expanded', 'false');
      if (focus) button.focus();
    }
    button.onclick = () => {
      if (!form.hidden) { close(); return; }
      fill(state);
      form.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      const rect = button.getBoundingClientRect();
      form.style.left = `${Math.max(12, Math.min(rect.right - 338, innerWidth - 350))}px`;
      form.style.top = `${rect.bottom + 8}px`;
      form.style.maxHeight = `${Math.max(120, innerHeight - rect.bottom - 20)}px`;
      fields.period.focus();
    };
    fields.period.onchange = () => {
      if (fields.period.value === 'custom' && !fields.from.value && !fields.to.value) {
        const bounds = api.bounds();
        fields.from.value = bounds.from;
        fields.to.value = bounds.to || new Date().toISOString().slice(0, 10);
      }
      syncDates();
    };
    fields.from.oninput = fields.to.oninput = () => fields.to.setCustomValidity('');
    wrapper.querySelector('#flightFiltersReset').onclick = () => fill(defaults());
    wrapper.querySelector('#flightFiltersCancel').onclick = () => close(true);
    form.onsubmit = event => {
      event.preventDefault();
      const custom = fields.period.value === 'custom';
      if (custom && fields.from.value && fields.to.value && fields.from.value > fields.to.value) {
        fields.to.setCustomValidity('The end date must be on or after the start date.');
        fields.to.reportValidity();
        return;
      }
      state = {period: fields.period.value, from: custom ? fields.from.value : '', to: custom ? fields.to.value : '',
        indoor: fields.indoor.checked, nonflights: fields.nonflights.checked, unknownDates: fields.unknownDates.checked};
      try { localStorage.setItem(KEY, JSON.stringify(state)); storageWarning = ''; }
      catch (error) {
        console.warn('Could not save flight filters:', error);
        storageWarning = 'Filters are active here but could not be saved for other pages in this browser.';
      }
      changed();
      if (!storageWarning) close(true);
    };
    document.addEventListener('click', event => { if (!wrapper.contains(event.target)) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !form.hidden) close(true); });
    window.addEventListener('resize', () => close());
    window.addEventListener('storage', event => {
      if (event.key === KEY || event.key === null) { state = read(); close(); changed(); }
    });
    paint();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();

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

  function paintVersion(version) {
    let footer = document.getElementById('webuiVersion');
    if (!footer) {
      footer = document.createElement('footer');
      footer.id = 'webuiVersion';
      document.body.appendChild(footer);
    }
    const link = document.createElement('a');
    const repo = 'https://github.com/tudelft/paparazzi_log_parsing';
    link.href = version?.commit ? `${repo}/commit/${version.commit}` : repo;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    const release = version?.tag ? version.tag.replace(/^webui-/, '') : '';
    const ahead = version?.commits_since_tag
      ? ` + ${version.commits_since_tag} commit${version.commits_since_tag === 1 ? '' : 's'}` : '';
    link.textContent = version?.commit
      ? `Web UI · ${release ? release + ahead + ' · ' : ''}${version.commit.slice(0, 8)}${version.modified ? ' (modified)' : ''}`
      : 'GitHub · Web UI version unavailable';
    link.title = version?.commit
      ? `Running revision: ${version.commit}${version.modified ? ' with local tracked changes' : ''}. Recorded at server startup. View on GitHub.`
      : 'The running revision could not be determined. Open the project on GitHub.';
    footer.replaceChildren(link);
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
    if (!/^\/(login|setup)/.test(location.pathname)) paintVersion(null);
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
      if (!r.ok) throw new Error(`Could not load account and Web UI version (${r.status})`);
      const d = await r.json();
      ready(() => paintVersion(d.webui_version));
      admin = !!(d.user && d.user.is_admin);
      setCachedAdmin(admin);
      paintEdit();
    }).catch(error => { console.warn('Could not load account and Web UI version:', error); });
  }
})();
