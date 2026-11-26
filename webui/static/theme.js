// Page data at once. Pages ask for /api/me first and for their own data only after it came back; that chain made them
// slow to show. Here the first data requests of each page start as theme.js loads (admin or not as last seen in this
// browser), and the page's own request later gets that answer. /api/me is asked for once per page (theme.js and the
// page both use it). A guess that is not used (for example after admin rights changed) only costs one request.
(function () {
  const realFetch = window.fetch.bind(window);
  let admin = false, training = false;
  try {
    admin = localStorage.getItem('logdbAdmin') === '1';
    training = localStorage.getItem('logdbTraining') === '1';
  } catch (e) {}
  const path = location.pathname;
  const early = {
    // Home starts /api/home itself; Needs your attention and the Training card use the trainings (the qualification
    // and document cards, off by default, ask for /api/documents only when they are on)
    '/home': training ? ['/api/trainings'] : [],
    '/aircraft': ['/api/drones'], '/documents': ['/api/documents'], '/review': ['/api/review'],
    '/training': ['/api/trainings'], '/safety': ['/api/safety'], '/safety/emergency': ['/api/safety'], '/logs': ['/api/projects'],
    '/projects': admin ? ['/api/admin/projects', '/api/admin/people'] : ['/api/projects'],
    '/people': admin ? ['/api/admin/people', '/api/admin/qualification-types'] : ['/api/people-directory'],
  }[path] || (/^\/flight\/\d+$/.test(path) ? ['/api/projects', '/api/people'] : []);
  const waiting = new Map();
  if (!/^\/(login|setup|cli-login|profile|welcome)/.test(path)) {
    for (const url of ['/api/me', ...early]) {
      const answer = realFetch(url);
      answer.catch(() => {});
      waiting.set(url, answer);
    }
  }
  window.fetch = (input, init) => {
    if (typeof input === 'string' && waiting.has(input) && !(init && init.method && init.method !== 'GET')) {
      const answer = waiting.get(input);
      if (input !== '/api/me') waiting.delete(input);  // page data: only the first request; later ones ask again
      return answer.then(r => r.clone());
    }
    return realFetch(input, init);
  };
})();

// Site name, logos and footer text; the server puts the configured values (LOGDB_SITE_*, LOGDB_FOOTER_*) before this file.
window.SITE = Object.assign({name: 'Flight logbook', logo: null, footer_text: '', footer_logos: []}, window.SITE || {});

window.estimatedDateBadge = function (record) {
  return record.date_estimated ? ' <span class="badge date-unknown" title="GPS time-of-week plus firmware commit; the calendar week is uncertain">estimated date</span>' : '';
};

// A checklist answer as logged by the GCS ("[PFC] key: value"): checkboxes as a check mark or cross, an optional item
// left empty as a dash and a required one as a warning (optional is undefined when the airframe does not define the
// item); older GCS versions logged a confirmed item without any value.
window.checklistAnswerHTML = function (answer, logged, optional) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const value = String(answer ?? '').trim();
  if (/^true$/i.test(value)) return '<span style="color:var(--ok);font-weight:600" title="Checked">✓</span>';
  if (/^false$/i.test(value)) return '<span style="color:var(--err);font-weight:600" title="Not checked">✗</span>';
  if (value) return escape(value);
  if (!/:\s*$/.test(logged || ''))
    return '<span style="color:var(--ok);font-weight:600" title="Confirmed without an answer">✓</span>';
  if (optional === undefined) return '<span class="mute" title="Left empty">–</span>';  // no airframe definition
  return optional ? '<span class="mute" title="Optional item, left empty">–</span>'
    : '<span style="color:var(--warn);font-weight:600" title="Required, but logged without an answer">!</span>';
};

window.preflightCheckHTML = function (message) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  if (!message.checklist_label) return escape(message.text);
  const help = message.checklist_help ? message.checklist_help + '\n\n' : '';
  return `<span tabindex="0" title="${escape(help)}Logged: ${escape(message.text)}">${escape(message.checklist_label)}: ${
    window.checklistAnswerHTML(message.checklist_value, message.text, !!message.checklist_optional)}</span>`;
};

// Whether the pre-flight inspection of a logbook entry is recorded (#315; app.py apply_preflight): {short, long, tip, ok}
// for a logbook cell (short), the flight page (long) and their tooltip; ok is false when it is missing.
window.preflightStatus = function (entry) {
  const at = (entry.preflight_at || '').slice(0, 10);
  const indoor = entry.simulation && entry.simulation.detected ? 'a simulation' : 'an indoor flight';
  switch (entry.preflight) {
    case 'log': return {short: '✓', long: '✓ Checklist in the log', ok: true,
      tip: 'The pre-flight checklist ([PFC]) is in the log; one checklist covers every flight of the aircraft in that log'};
    case 'confirmed': return {short: '✓', long: `✓ Confirmed by ${entry.preflight_by || 'a former user'}`, ok: true,
      tip: `Confirmed by hand by ${entry.preflight_by || 'a former user'}${at ? ' on ' + at : ''}`};
    case 'paper': return {short: '✓', long: '✓ On the paper record', ok: true, tip: 'Ticked on the paper record'};
    case 'not_needed': return {short: '–', long: 'Not needed', ok: true, tip: `Not needed for ${indoor}`};
    default: return {short: 'Not recorded', long: 'Not recorded', ok: false,
      tip: 'No pre-flight checklist in the log and not confirmed'};
  }
};

// How an operator is shown everywhere, and where a logbook entry's operator comes from (operators.py).
window.operatorLabel = o => o.name + (o.registration ? ` (${o.registration})` : '');
window.OPERATOR_SOURCE = {flight: 'set on this flight', project: 'from the project', place: 'from the takeoff place', pic: 'the operator the PIC flies for', default: 'own operator (default)'};
// Operation category and authorisation (categories.py): one select whose values are "cat:<key>" or "auth:<id>".
window.CATEGORY_SOURCE = {flight: 'set on this flight', indoor: 'indoor flight', project: 'from the project', place: 'from the takeoff place', date: 'from the date and our authorisations'};
window.CATEGORY_LABELS = {national: 'National rules (ROC / ROC-light)', open: 'OPEN', specific: 'SPECIFIC', luc: 'LUC', indoor: 'Indoor'};
window.authorisationLabel = a => a.name + (a.valid_from || a.valid_until ? ` (${a.valid_from || '…'} – ${a.valid_until || '…'})` : '');
window.categoryOptions = function (authorisations, selected) {
  const opt = (value, label) => `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`;
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  return Object.entries(window.CATEGORY_LABELS).map(([key, label]) => opt('cat:' + key, label)
    + authorisations.filter(a => a.category === key).map(a => opt('auth:' + a.id, '&nbsp;&nbsp;' + esc(label + ' · ' + window.authorisationLabel(a)))).join('')).join('');
};
window.categoryValue = o => o.authorisation_id ? 'auth:' + o.authorisation_id : o.category ? 'cat:' + o.category : '';
window.categoryChoice = value => value.startsWith('auth:') ? {category: null, authorisation_id: +value.slice(5)}
  : {category: value.startsWith('cat:') ? value.slice(4) : null, authorisation_id: null};
window.categoryText = f => (f.category === 'national' ? 'National rules' : window.CATEGORY_LABELS[f.category] || 'Unknown') + (f.authorisation_name ? ' · ' + f.authorisation_name : '');

// Qualification types in groups (the type's group, set on People): groups in the order of their first type, types
// without a group last under "Other". typeOptions gives <option>s, in <optgroup>s once any type has a group.
window.typeGroups = types => {
  const groups = new Map();
  for (const t of types) {
    const name = (t.grp || '').trim();
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(t);
  }
  return [...groups].sort(([a], [b]) => (a === '') - (b === '')).map(([name, list]) => ({name: name || 'Other', types: list}));
};
// Items of qualification types (a person's qualifications, each with its type's grp, in type order) under their
// group headings: head(name, items) gives a heading, body(items) the rows. Without any group: body(all items).
window.inTypeGroups = (items, head, body) => !items.some(i => (i.grp || '').trim()) ? body(items)
  : window.typeGroups(items).map(g => head(g.name, g.types) + body(g.types)).join('');
window.typeOptions = (types, option) => {
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  if (!types.some(t => (t.grp || '').trim())) return types.map(option).join('');
  return window.typeGroups(types).map(g => `<optgroup label="${esc(g.name)}">${g.types.map(option).join('')}</optgroup>`).join('');
};

// Short name of an uploaded log by type; its download always contains every file of that log (.data + .log).
window.logLabel = t => ({onboard_sd: 'SD log', gcs: 'GCS log', px4_ulog: 'PX4 log', ardupilot: 'ArduPilot log',
  mavlink_tlog: 'MAVLink log', betaflight: 'Betaflight log', indiflight: 'INDIFlight log',
  blackbox: 'Blackbox log'}[t] || 'log');

// Download buttons for the logs of a flight that the MATLAB export does not cover (PX4, ArduPilot, MAVLink,
// Betaflight/INDIFlight): one per log, next to the MATLAB menu on the flight page and in the logbook.
window.logDownloadButtons = function (sources, extraClass = '') {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const seen = new Set();
  return (sources || []).filter(s => s.log_id && !['onboard_sd', 'gcs'].includes(s.log_type) && !seen.has(s.log_id)
      && seen.add(s.log_id))
    .map(s => `<a class="btn sec log-download ${extraClass}" href="/api/logs/${s.log_id}/download" download
      title="Download the original ${esc(window.logLabel(s.log_type))}${s.log_name ? ' (' + esc(s.log_name) + ')' : ''}">Download ${esc(window.logLabel(s.log_type))}</a>`)
    .join('');
};

// A stored file: its name opens it in the browser when it can be viewed there (else downloads it), with a
// small download button beside a viewable one. `attrs` adds attributes such as a class or title to the name.
// Photos, videos and sound (by `filename`) open in the media viewer on the page instead of a new tab.
window.fileLink = function ({name, view, download, viewable, attrs = '', filename = ''}) {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  if (!viewable) return `<a ${attrs} href="${esc(download)}" title="Download">${name}</a>`;
  const media = viewable && window.mediaKind(filename);
  return (media
    ? `<a ${attrs} href="${esc(view)}" ${window.mediaAttrs({filename, download})} title="Open ${esc(filename)}">${name}</a>`
    : `<a ${attrs} href="${esc(view)}" target="_blank" rel="noopener" title="View in a new tab">${name}</a>`)
    + `<a class="file-dl" href="${esc(download)}" title="Download" aria-label="Download">`
    + '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M8 2v8m-3.5-3.5L8 10l3.5-3.5M3 13h10" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></a>';
};

// ---- Media viewer: a link with data-media (see mediaAttrs) opens its photo, video or sound in an overlay on the page;
// the arrows (or ← →) step through the other media links in the same [data-media-group] (else the same parent).
const MEDIA_KINDS = {jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image', mp4: 'video', m4v: 'video',
  mov: 'video', webm: 'video', mp3: 'audio', m4a: 'audio', wav: 'audio', ogg: 'audio'};
window.mediaKind = name => MEDIA_KINDS[String(name || '').split('.').pop().toLowerCase()] || '';
window.mediaAttrs = function ({filename, download = '', open = '', openLabel = ''}) {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  return `data-media="${window.mediaKind(filename)}" data-name="${esc(filename)}"`
    + (download ? ` data-download="${esc(download)}"` : '') + (open ? ` data-open="${esc(open)}" data-open-label="${esc(openLabel)}"` : '');
};
(function () {
  let dialog, items = [], index = 0;
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  function make() {
    dialog = document.createElement('dialog');
    dialog.className = 'media-viewer';
    dialog.setAttribute('aria-label', 'Media viewer');
    dialog.innerHTML = `<div class="mv-bar"><span class="mv-title"></span><span class="mv-count"></span><span class="mv-links"></span>
      <button type="button" class="mv-close" aria-label="Close">Close</button></div>
      <div class="mv-stage"></div>
      <button type="button" class="mv-nav mv-prev" aria-label="Previous">‹</button><button type="button" class="mv-nav mv-next" aria-label="Next">›</button>`;
    document.body.appendChild(dialog);
    dialog.querySelector('.mv-close').onclick = () => dialog.close();
    dialog.querySelector('.mv-prev').onclick = () => show(index - 1);
    dialog.querySelector('.mv-next').onclick = () => show(index + 1);
    dialog.addEventListener('click', event => { if (event.target === dialog || event.target.classList.contains('mv-stage')) dialog.close(); });
    dialog.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft') { event.preventDefault(); show(index - 1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); show(index + 1); }
    });
    dialog.addEventListener('close', () => { dialog.querySelector('.mv-stage').replaceChildren(); });
  }
  function show(i) {
    if (i < 0 || i >= items.length) return;
    index = i;
    const a = items[i], kind = a.dataset.media, src = a.getAttribute('href'), name = a.dataset.name || a.textContent.trim();
    const stage = dialog.querySelector('.mv-stage');
    stage.innerHTML = kind === 'image' ? `<img src="${esc(src)}" alt="${esc(name)}">`
      : kind === 'video' ? `<video src="${esc(src)}" controls autoplay playsinline></video>`
      : `<audio src="${esc(src)}" controls autoplay></audio>`;
    const title = dialog.querySelector('.mv-title');
    title.textContent = name;
    title.title = a.title && a.title !== 'Open ' + name ? a.title : name;
    dialog.querySelector('.mv-count').textContent = items.length > 1 ? `${i + 1} / ${items.length}` : '';
    dialog.querySelector('.mv-links').innerHTML = (a.dataset.open
      ? `<a href="${esc(a.dataset.open)}">${esc(a.dataset.openLabel ? 'Open ' + a.dataset.openLabel : 'Open')}</a>` : '')
      + (a.dataset.download ? `<a href="${esc(a.dataset.download)}">Download</a>` : '');
    dialog.querySelector('.mv-prev').hidden = i === 0;
    dialog.querySelector('.mv-next').hidden = i === items.length - 1;
  }
  window.openMedia = function (link) {
    if (!dialog) make();
    const group = link.closest('[data-media-group]') || link.parentElement;
    const seen = new Set();
    items = [...group.querySelectorAll('a[data-media]')].filter(a => {
      const key = a.getAttribute('href');
      return !seen.has(key) && seen.add(key);
    });
    if (!items.includes(link)) items = [link];
    if (!dialog.open) dialog.showModal();
    show(items.findIndex(a => a.getAttribute('href') === link.getAttribute('href')));
    dialog.querySelector('.mv-close').focus();
  };
  document.addEventListener('click', event => {
    const link = event.target.closest && event.target.closest('a[data-media]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.openMedia(link);
  });
})();

// Anyone who flies can add the operator of a new partner; resolves to the new operator, or null when cancelled.
window.newOperatorDialog = function () {
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'entry-dialog';
    dialog.innerHTML = `<form><div class="dialog-header"><h2>New operator</h2></div>
      <p class="mute">The organisation whose operator registration the flights were flown under, for example a partner.</p>
      <label class="field">Name<input name="name" required maxlength="80"></label>
      <label class="field">Registration number<input name="registration" maxlength="40" placeholder="as on their registration"></label>
      <label class="field">Country<input name="country" maxlength="40"></label>
      <label class="field">Note<input name="note" maxlength="400" placeholder="e.g. authorisation flown under"></label>
      <p class="dialog-error" role="alert"></p>
      <div class="row"><button type="submit">Add operator</button> <button type="button" class="sec">Cancel</button></div></form>`;
    const form = dialog.querySelector('form');
    dialog.querySelector('button.sec').onclick = () => dialog.close();
    dialog.addEventListener('close', () => { dialog.remove(); resolve(null); });
    form.onsubmit = async event => {
      event.preventDefault();
      const body = Object.fromEntries(new FormData(form));
      const r = await fetch('/api/operators', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { form.querySelector('.dialog-error').textContent = data.error || `Could not add the operator (${r.status}).`; return; }
      resolve({...body, id: data.id, own: 0});
      dialog.close();
    };
    document.body.append(dialog);
    dialog.showModal();
  });
};
// For an operator select whose last choice is "New operator…" (value __new__): add one and select it.
window.NEW_OPERATOR_OPTION = '<option value="__new__">New operator…</option>';
window.pickNewOperator = async function (select, operators, refill, fallback = '') {
  if (select.value !== '__new__') return;
  const added = await window.newOperatorDialog();
  if (!added) { select.value = fallback; return; }
  operators.push(added);
  refill();
  select.value = String(added.id);
};

window.timingEvidenceHTML = function (timing) {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  if (!timing.status) return '';
  const gps = (timing.gps_evidence || []).map(e =>
    `<div>AC ${escape(e.ac_id)}: ${escape(e.samples)} GPS samples, ${escape(e.accepted_samples)} absolute-clock samples;
    weeks ${escape(e.weeks.map(w => w.value + ' (' + w.samples + ')').join(', ') || 'missing')};
    fixes ${escape(e.fixes.map(f => f.value + ' (' + f.samples + ')').join(', ') || 'missing')};
    accepted span ${escape(e.accepted_span_s ?? 'unknown')} s, offset spread ${escape(e.offset_spread_s ?? 'unknown')} s;
    week-zero span ${escape(e.week_zero_span_s ?? 'unknown')} s, spread ${escape(e.week_zero_offset_spread_s ?? 'unknown')} s.</div>`).join('');
  const versions = (timing.firmware || []).map(v =>
    `<div>AC ${escape(v.ac_id)}: <code>${escape(v.raw)}</code>${v.dirty ? ' (local changes)' : ''}<br>
    ${escape(v.resolution)}${v.commit ? ' · ' + escape(v.commit) : ''}
    ${v.repository ? ' · ' + escape(v.repository) : ''}
    ${v.committed_at ? ' · firmware committed ' + escape(v.committed_at) : ''}
    ${v.lookup_warning ? '<div>' + escape(v.lookup_warning) + ' Re-parse this log to retry after resolving the issue/cache expiry.</div>' : ''}</div>`).join('');
  return `<br><strong>Date status: ${escape(timing.status)}</strong>
    ${timing.estimated && timing.estimate ? '<div>' + escape(timing.estimate.warning) + '</div>' : ''}
    ${timing.firmware_warning ? '<div>' + escape(timing.firmware_warning) + '</div>' : ''}
    <details><summary>Clock and firmware evidence</summary>
    <div>${escape((timing.reasons || []).join(', '))}</div>${gps}${versions}</details>`;
};

(function () {
  const KEY = 'logdbFlightFilters';
  const PERIODS = {'30d': 'Last 30 days', '3m': 'Last 3 months', '1y': 'Last year',
    '2y': 'Last 2 years', '3y': 'Last 3 years', '5y': 'Last 5 years', all: 'All time', custom: 'Custom dates'};
  const OPERATORS = {all: 'All operators', own: 'Own operations only', other: 'Other operators only'};
  const CATEGORIES = {all: 'All categories', national: 'National rules (ROC / ROC-light)', open: 'OPEN', specific: 'SPECIFIC',
    luc: 'LUC', indoor: 'Indoor', unknown: 'Unknown category', check: 'Category to check'};
  // indoor, nonflights, nologs and unknownDates mean "shown"; by default everything is shown (the panel offers Hide
  // options). Saved selections keep their values, so a browser that hid them keeps hiding them. nologs: logs that
  // produced no flight (the Logbook lists them as rows of their own); a selection saved before it existed follows
  // nonflights, which used to include them.
  const defaults = () => ({period: '3y', from: '', to: '', indoor: true, nonflights: true, nologs: true, unknownDates: true, operator: 'all', category: 'all'});
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
          || (value.nologs !== undefined && typeof value.nologs !== 'boolean')
          || (value.operator !== undefined && !Object.hasOwn(OPERATORS, value.operator))
          || (value.category !== undefined && !Object.hasOwn(CATEGORIES, value.category))
          || (value.from && value.to && value.from > value.to)) {
        throw new Error('Invalid saved flight filters');
      }
      return {period: value.period, from: value.from, to: value.to,
        indoor: value.indoor, nonflights: value.nonflights, nologs: value.nologs ?? value.nonflights,
        unknownDates: value.unknownDates ?? true,
        operator: value.operator ?? 'all', category: value.category ?? 'all'};
    } catch (error) {
      console.warn('Could not restore flight filters:', error);
      storageWarning = 'Saved filters could not be restored. Using the default selection.';
      return defaults();
    }
  }
  let state = read();
  const api = window.flightFilters = {
    get state() { return {...state}; },
    restore(value) {
      if (!value || !Object.hasOwn(PERIODS, value.period)
          || !['from', 'to'].every(k => value[k] === '' || validDay(value[k]))
          || ['indoor','nonflights','nologs','unknownDates'].some(k => typeof value[k] !== 'boolean')
          || !Object.hasOwn(OPERATORS, value.operator || 'all')
          || !Object.hasOwn(CATEGORIES, value.category || 'all')
          || (value.from && value.to && value.from > value.to)) return false;
      state = {...value, operator: value.operator || 'all', category: value.category || 'all'};
      paint();
      return true;
    },
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
      && (state.operator === 'all' || (state.operator === 'own') === (f.operator_own !== false))
      && (state.category === 'all' || (state.category === 'check' ? !!f.category_check
        : state.category === 'unknown' ? !f.category : f.category === state.category))
      && this.includesDate(f);
  },
  logs(logs, allFlights, shownFlights) {
    const ids = new Set(shownFlights.flatMap(f => f.log_ids || []));
    const flightIds = new Set(allFlights.flatMap(f => f.log_ids || []));
    return logs.filter(l => ids.has(l.id) || (state.nologs && l.status === 'done'
      && l.num_flights === 0 && !flightIds.has(l.id)
      && this.includesDate({takeoff_utc: l.start_utc, date_ok: l.date_ok, date_override: l.date_override})));
  },
  params() {
    const {from, to} = this.bounds();
    return new URLSearchParams({from, to, indoor: state.indoor ? '1' : '0',
      nonflights: state.nonflights ? '1' : '0', unknown_dates: state.unknownDates ? '1' : '0', operator: state.operator,
      category: state.category});
  },
  periodLabel() {
    return state.period === 'custom' ? `${state.from || 'Any date'} – ${state.to || 'Any date'}`
      : PERIODS[state.period];
  },
  summary() {
    return `${this.periodLabel()} · Indoor/no-GPS ${state.indoor ? 'shown' : 'hidden'} · Non-flights ${state.nonflights ? 'shown' : 'hidden'} · Logs without flights ${state.nologs ? 'shown' : 'hidden'} · Unknown dates ${state.unknownDates ? 'shown' : 'hidden'}`
      + (state.operator === 'all' ? '' : ` · ${OPERATORS[state.operator]}`)
      + (state.category === 'all' ? '' : ` · ${CATEGORIES[state.category]}`);
  },
};

  function changed() {
    paint();
    window.dispatchEvent(new Event('flightfilterschange'));
  }
  function paint() {
    const button = document.getElementById('flightFiltersButton');
    if (!button) return;
    const extra = Number(!state.indoor) + Number(!state.nonflights) + Number(!state.nologs) + Number(!state.unknownDates) + Number(state.operator !== 'all') + Number(state.category !== 'all');
    // a funnel icon, then the period and how many other filters are changed (the full summary is the tooltip)
    button.innerHTML = '<svg class="filter-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 5h18l-7 8v6l-4 2v-8z"/></svg>'
      + `<span class="filter-label">Flight filters · ${api.periodLabel()}${extra ? ' · +' + extra : ''} ▾</span>`;
    button.setAttribute('aria-label', 'Flight filters: ' + api.summary());
    button.title = api.summary();
    document.getElementById('flightFiltersNotice').textContent = storageWarning;
  }
  function mount() {
    const nav = document.getElementById('nav');
    if (!nav || /^\/(login|setup|cli-login|profile|welcome)/.test(location.pathname)) return;
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
        <label class="filter-field">Operator<select name="operator">${Object.entries(OPERATORS).map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select></label>
        <label class="filter-field">Category<select name="category">${Object.entries(CATEGORIES).map(([v, label]) => `<option value="${v}">${label}</option>`).join('')}</select></label>
        <div class="filter-options">
          <label><input type="checkbox" name="indoor"> Hide indoor / no-GPS flights</label>
          <label title="Ground runs, uncertain flights and simulations"><input type="checkbox" name="nonflights"> Hide non-flights <span class="mute">(ground runs, uncertain, simulations)</span></label>
          <label title="Parsed logs that produced no flight"><input type="checkbox" name="nologs"> Hide logs without flights</label>
          <label><input type="checkbox" name="unknownDates"> Hide unknown dates</label>
        </div>
        <details class="filter-help"><summary>Help</summary>
          <p>Non-flights are ground runs, uncertain flights and simulations; uncertain flights are marked (?). Logs that produced no flight have a filter of their own: the Logbook lists them as rows marked "no flight", with their log number. Edit flight on the flight page confirms a flight as a flight or not a flight.</p>
          <p>Records without a known date are shown alongside the selected period unless Hide unknown dates is ticked. Other visibility filters still apply.</p>
          <p>Own operations are flights under our own operator registration: use them for operator reporting. Pilot experience counts all operators.</p>
          <p>Category: the rules a flight was flown under. Before 2021 that was national rules (ROC / ROC-light); since then OPEN or SPECIFIC under an operational authorisation. To check: SPECIFIC without an authorisation, or flown outside its validity.</p>
          <p>Shared across flight overviews in this browser. Changing pages keeps these settings.</p>
        </details>
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
      for (const key of ['period', 'from', 'to', 'operator', 'category']) fields[key].value = value[key];
      for (const key of ['indoor', 'nonflights', 'nologs', 'unknownDates']) fields[key].checked = !value[key];  // ticked = hidden
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
        indoor: !fields.indoor.checked, nonflights: !fields.nonflights.checked, nologs: !fields.nologs.checked, unknownDates: !fields.unknownDates.checked,
        operator: fields.operator.value, category: fields.category.value};
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

// "Edit <item>" on a detail page (/people/<id>, ...) opens the list page with the item and ?edit=1: once its Edit
// button shows (data loaded, panel open), it is clicked once. The parameter is removed so Back does not repeat it.
(function () {
  const url = new URL(location.href);
  if (url.searchParams.get('edit') !== '1') return;
  url.searchParams.delete('edit');
  history.replaceState(history.state, '', url);
  const id = {'/people': 'editPerson', '/projects': 'editProject', '/aircraft': 'editAircraft', '/locations': 'editPlace'}[location.pathname];
  let tries = 0;
  const timer = id && setInterval(() => {
    const button = document.getElementById(id);
    if (button && !button.hidden && button.offsetParent) { clearInterval(timer); button.click(); }
    else if (++tries > 100) clearInterval(timer);
  }, 100);
})();

(function () {
  const filters = window.flightFilters;
  const filterKeys = {period:'period', from:'from', to:'to', indoor:'indoor',
    nonflights:'nonflights', nologs:'nologs', unknownDates:'unknown_dates', operator:'operator', category:'category'};
  // nologs is in the URL only when hidden (nologs=0), so links from before it existed keep showing those logs
  const inverted = new Set(['nologs']);
  const integer = (value, fallback, min = 1) => /^\d+$/.test(value || '')
    && Number.isSafeInteger(+value) && +value >= min ? +value : fallback;
  window.viewState = {
    integer,
    restoring:false,
    setup(read, write, redraw) {
      const api = this;
      let pendingScroll = history.state?.viewScroll;
      const restore = () => {
        const params = new URLSearchParams(location.search);
        if (params.has('period')) {
          const value = {};
          for (const [key, param] of Object.entries(filterKeys)) {
            value[key] = ['period', 'from', 'to', 'operator', 'category'].includes(key) ? params.get(param) || ''
              : inverted.has(key) ? params.get(param) !== '0' : params.get(param) === '1';
          }
          if (!filters.restore(value)) console.warn('Invalid URL flight filters; keeping current filters');
        }
        read(params, history.state?.viewExtra || {});
      };
      const saveScroll = () => history.replaceState({...history.state, viewScroll:[scrollX, scrollY]}, '');
      api.save = (push = false) => {
        if (api.restoring) return;
        const params = new URLSearchParams(location.search);
        const extra = write(params) || {};
        for (const [key, param] of Object.entries(filterKeys)) {
          const value = filters.state[key];
          if (inverted.has(key)) { if (value) params.delete(param); else params.set(param, '0'); continue; }
          if (value === '' || value === false || (['operator', 'category'].includes(key) && value === 'all')) params.delete(param);
          else params.set(param, value === true ? '1' : value);
        }
        const url = location.pathname + (params.size ? '?' + params : '') + location.hash;
        if (push && url !== location.pathname + location.search + location.hash) {
          saveScroll();
          history.pushState({viewExtra:extra}, '', url);
        } else history.replaceState({...history.state, viewExtra:extra}, '', url);
      };
      api.ready = () => {
        api.save();
        if (pendingScroll) {
          const target = pendingScroll;
          pendingScroll = null;
          setTimeout(() => scrollTo(...target), 0);
        }
      };
      restore();
      api.save();
      document.addEventListener('click', event => {
        if (event.target.closest('a[href]')) { api.save(); saveScroll(); }
      }, true);
      window.addEventListener('pagehide', saveScroll);
      window.addEventListener('pageshow', event => {
        if (event.persisted && history.state?.viewScroll) {
          const target = history.state.viewScroll;
          setTimeout(() => scrollTo(...target), 0);
        }
      });
      window.addEventListener('popstate', () => {
        api.restoring = true;
        pendingScroll = history.state?.viewScroll || [0, 0];
        restore();
        redraw();
        api.restoring = false;
        api.save();
      });
      history.scrollRestoration = 'manual';
    },
    set(params, key, value, defaultValue = '') {
      if (String(value) === String(defaultValue)) params.delete(key);
      else params.set(key, String(value));
    },
    pager(el, page, size, total, change) {
      const last = Math.max(1, Math.ceil(total / size));
      const current = Math.min(last, page + 1);
      const signature = `${current}:${size}:${total}`;
      if (el.dataset.pageState === signature && el.contains(document.activeElement)) return;
      el.dataset.pageState = signature;
      const numbers = [...new Set([1, last, ...Array.from({length:5}, (_, i) => current + i - 2)])]
        .filter(n => n >= 1 && n <= last).sort((a,b) => a-b);
      el.innerHTML = `<button type="button" class="page-step" aria-label="Previous page" title="Previous page" data-page="${current-1}"${current === 1 ? ' disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m15 6-6 6 6 6"/></svg></button>` +
        numbers.map((n,i) => `${i && n > numbers[i-1]+1 ? '<span>…</span>' : ''}` +
          (n === current
            ? `<input type="number" aria-label="Current page — type a page number" aria-current="page" title="Current page — type a page number (1–${last})" min="1" max="${last}" value="${current}" required>`
            : `<button type="button" class="page-number" aria-label="Page ${n}" data-page="${n}">${n}</button>`)).join('') +
        `<button type="button" class="page-step" aria-label="Next page" title="Next page" data-page="${current+1}"${current === last ? ' disabled' : ''}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg></button>
        <span class="mute">${total ? (current-1)*size+1 : 0}–${Math.min(current*size,total)} of ${total}</span>`;
      el.querySelectorAll('button').forEach(b => { b.onclick = () => change(+b.dataset.page - 1); });
      const input = el.querySelector('input');
      const choosePage = () => {
        if (input.reportValidity() && +input.value !== current) change(+input.value - 1);
      };
      input.onfocus = () => input.select();
      input.onchange = choosePage;
      input.onkeydown = event => {
        if (event.key === 'Enter') { event.preventDefault(); choosePage(); }
        if (event.key === 'Escape') { input.value = current; input.blur(); }
      };
    },
  };
})();

(function () {
  const icon = document.createElement('link');
  icon.rel = 'icon';
  icon.type = 'image/svg+xml';
  icon.href = '/favicon.svg?v=2';  // bump when the icon changes; browsers cache it hard
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
  const TRAINING_KEY = 'logdbTraining';
  const BOOKINGS_KEY = 'logdbBookings';
  const DOCUMENTS_KEY = 'logdbDocuments';
  const SAFETY_KEY = 'logdbSafety';
  const UPLOAD_KEY = 'logdbCanUpload';
  // There is no Edit mode any more (UI rule): every page offers its changes per item. Forget the old setting.
  try { localStorage.removeItem('logdbEdit'); } catch (e) {}
  // Menu: Home · Logbook · Agenda · Training · Documents · Safety (each when enabled) · Directory ▾ (People … Logs, Statistics); Upload logs and the name menu on the right
  const DIRECTORY = [['/people', 'People'], ['/aircraft', 'Aircraft'], ['/projects', 'Projects'], ['/locations', 'Locations'],
    ['/documents', 'Documents'],  // only when LOGDB_DOCUMENTS is on (see paintNav)
    null, ['/logs', 'Logs'], ['/stats', 'Statistics']];

  function cachedAdmin() {
    try { return localStorage.getItem(ADMIN_KEY) === '1'; } catch (e) { return false; }
  }
  function setCachedAdmin(on) {
    try { localStorage.setItem(ADMIN_KEY, on ? '1' : '0'); } catch (e) {}
  }
  function cachedTraining() {
    try { return localStorage.getItem(TRAINING_KEY) === '1'; } catch (e) { return false; }
  }
  function setCachedTraining(on) {
    try { localStorage.setItem(TRAINING_KEY, on ? '1' : '0'); } catch (e) {}
  }
  function cachedBookings() {
    try { return localStorage.getItem(BOOKINGS_KEY) === '1'; } catch (e) { return false; }
  }
  function cachedDocuments() {
    try { return localStorage.getItem(DOCUMENTS_KEY) === '1'; } catch (e) { return false; }
  }
  function cachedSafety() {
    try { return localStorage.getItem(SAFETY_KEY) === '1'; } catch (e) { return false; }
  }
  function currentTab() {
    const p = location.pathname;
    if (p === '/logbook' || p === '/review' || p.startsWith('/flight/') || p.startsWith('/historical/')) return '/';
    if (p === '/home') return '/home';
    if (p === '/training' || p.startsWith('/training/')) return '/training';
    if (p === '/agenda') return '/agenda';
    if (p === '/documents') return '/documents';
    if (p === '/safety' || p.startsWith('/safety/')) return '/safety';
    for (const [tab] of DIRECTORY.filter(Boolean))
      if (p === tab || p.startsWith(tab + '/')) return tab;
    if (p === '/' || p === '') return '/';
    return '';
  }

  // A small menu panel under its button: fixed position (so a scrolling header does not clip it), closes on a click
  // elsewhere, Escape or a choice.
  let openMenu = null;
  function closeMenu() {
    if (!openMenu) return;
    openMenu.panel.hidden = true;
    openMenu.button.setAttribute('aria-expanded', 'false');
    openMenu = null;
  }
  function bindMenu(button, panel, alignRight) {
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', 'false');
    button.onclick = e => {
      e.stopPropagation();
      if (openMenu && openMenu.panel === panel) { closeMenu(); return; }
      closeMenu();
      const r = button.getBoundingClientRect();
      panel.hidden = false;
      panel.style.top = `${r.bottom + 2}px`;
      panel.style.left = alignRight ? `${Math.max(8, r.right - panel.offsetWidth)}px` : `${Math.max(8, r.left)}px`;
      button.setAttribute('aria-expanded', 'true');
      openMenu = {button, panel};
      panel.querySelector('a, button')?.focus();
    };
    panel.addEventListener('click', e => { if (e.target.closest('a, button')) closeMenu(); });
  }
  document.addEventListener('click', e => { if (openMenu && !openMenu.panel.contains(e.target)) closeMenu(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && openMenu) { const b = openMenu.button; closeMenu(); b.focus(); }
  });
  window.addEventListener('resize', closeMenu);

  function paintNav() {
    const nav = document.getElementById('nav');
    if (!nav) return;
    const here = currentTab();
    const tab = (href, label) => `<a href="${href}"${href === here ? ' class="cur" aria-current="page"' : ''}>${label}</a>`;
    const inDirectory = DIRECTORY.some(item => item && item[0] === here);
    // what people use daily first; the reference lists, Documents, Logs and Statistics under Directory
    nav.innerHTML = tab('/home', 'Home') + tab('/', 'Logbook')
      + (cachedBookings() ? tab('/agenda', 'Agenda') : '')
      + (cachedTraining() ? tab('/training', 'Training') : '')
      + (cachedSafety() ? tab('/safety', 'Safety') : '')
      + `<button type="button" class="nav-drop${inDirectory ? ' cur' : ''}" id="directoryBtn">Directory ▾</button>`;
    let panel = document.getElementById('directoryMenu');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'directoryMenu';
      panel.className = 'menu-panel';
      panel.setAttribute('role', 'menu');
      panel.hidden = true;
      document.body.appendChild(panel);
    }
    panel.innerHTML = DIRECTORY.filter(item => !item || item[0] !== '/documents' || cachedDocuments()).map(item => item
      ? `<a href="${item[0]}" role="menuitem"${item[0] === here ? ' class="cur" aria-current="page"' : ''}>${item[1]}</a>` : '<hr>').join('');
    bindMenu(document.getElementById('directoryBtn'), panel, false);
  }

  // The right side of the header: Download logs, Upload logs, Edit and a name menu (My account, Night mode, Sign out). Pages keep
  // their #who element (they set the name); the old Night and Sign out buttons are folded into the menu.
  function paintActions() {
    const night = document.getElementById('nightBtn');
    const who = document.getElementById('who');
    if (!night || document.getElementById('headerActions')) return;
    const right = night.parentElement;
    const actions = document.createElement('span');
    actions.id = 'headerActions';
    actions.className = 'header-actions';
    actions.innerHTML = `<button type="button" id="downloadBtn" title="Download logs by flight number from Python or MATLAB">⬇<span class="upload-label"> Download logs</span></button>
      <button type="button" id="uploadBtn" title="Upload logs, zips or folders, and photos, videos or documents for your flights (or drop them on the page)">⬆<span class="upload-label"> Upload</span></button>
      <button type="button" id="userMenuBtn"></button>`;
    const nameButton = actions.querySelector('#userMenuBtn');
    if (who) { who.style.cssText = ''; who.onclick = null; nameButton.append(who); }
    nameButton.append(' ▾');
    right.replaceChildren(actions);
    const panel = document.createElement('div');
    panel.id = 'userMenu';
    panel.className = 'menu-panel';
    panel.setAttribute('role', 'menu');
    panel.hidden = true;
    panel.innerHTML = `<a href="/account" role="menuitem">My account</a>
      <button type="button" role="menuitem" id="nightBtn"><span class="when-day">Night mode</span><span class="when-night">Day mode</span></button>
      <hr><button type="button" role="menuitem" id="signOutBtn">Sign out</button>`;
    document.body.appendChild(panel);
    panel.querySelector('#nightBtn').onclick = () => window.toggleNight();
    panel.querySelector('#signOutBtn').onclick = () => window.logout();
    bindMenu(nameButton, panel, true);
    actions.querySelector('#uploadBtn').onclick = () => window.openUpload();
    actions.querySelector('#downloadBtn').onclick = () => window.openDownload();
    paintUpload();
  }
  // Only users who may add logs (admins, project members with add or full access) see Upload logs.
  function canUpload() {
    try { return localStorage.getItem(UPLOAD_KEY) !== '0'; } catch (e) { return true; }
  }
  function paintUpload() {
    const btn = document.getElementById('uploadBtn');
    if (btn) btn.hidden = !canUpload();
  }

  // The upload dialog lives in /upload.js, loaded the first time it is needed (button or files dropped on a page).
  let uploadScript = null;
  window.openUpload = function (dropEvent) {
    if (!uploadScript) uploadScript = new Promise((ok, fail) => {
      const tag = document.createElement('script');
      tag.src = '/upload.js';
      tag.onload = ok;
      tag.onerror = fail;
      document.head.appendChild(tag);
    });
    return uploadScript.then(() => window.LogUpload.open(dropEvent));
  };
  // The download dialog (Python and MATLAB examples) lives in /download.js, loaded the first time it is opened.
  let downloadScript = null;
  window.openDownload = function () {
    if (!downloadScript) downloadScript = new Promise((ok, fail) => {
      const tag = document.createElement('script');
      tag.src = '/download.js';
      tag.onload = ok;
      tag.onerror = () => { downloadScript = null; fail(); };
      document.head.appendChild(tag);
    });
    return downloadScript.then(() => window.LogDownload.open());
  };

  // Copy to the clipboard, also without the clipboard API (plain http): select the text in a temporary field.
  window.copyText = async function (text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (e) {
      const field = Object.assign(document.createElement('textarea'), {value: text});
      document.body.append(field); field.select();
      const ok = document.execCommand('copy'); field.remove();
      return ok;
    }
  };
  // A small copy button: copyButton('1234', 'flight number 1234'). Its click is handled here, before row handlers
  // (a click on it does not expand a row or follow a link); it shows ✓ for a moment.
  const attr = v => String(v).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  window.copyButton = (text, what) => {
    const value = attr(text), label = attr(what || text);
    return `<button type="button" class="copy-btn" data-copy="${value}" title="Copy ${label}" aria-label="Copy ${label}">⧉</button>`;
  };
  document.addEventListener('click', async e => {
    const btn = e.target.closest?.('[data-copy]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const text = btn.dataset.copy;
    if (!await window.copyText(text)) { prompt('Copy:', text); return; }
    btn.classList.add('copied');
    const old = btn.dataset.copyLabel || btn.textContent;
    btn.dataset.copyLabel = old;
    btn.textContent = btn.classList.contains('copy-text') ? 'Copied' : '✓';
    clearTimeout(btn.copyTimer);
    btn.copyTimer = setTimeout(() => { btn.textContent = old; btn.classList.remove('copied'); delete btn.dataset.copyLabel; }, 1500);
  }, true);

  // Dropping files anywhere on a page opens the upload dialog with them (pages with their own drop zones handle theirs).
  const hasFiles = e => [...(e.dataTransfer?.types || [])].includes('Files');
  const uploadShown = () => { const b = document.getElementById('uploadBtn'); return b && !b.hidden; };
  document.addEventListener('dragover', e => {
    if (hasFiles(e) && !e.defaultPrevented && uploadShown()) e.preventDefault();
  });
  document.addEventListener('drop', e => {
    if (!hasFiles(e) || e.defaultPrevented || !uploadShown()) return;
    e.preventDefault();
    window.openUpload(e);
  });

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
    // The configured logos (LOGDB_FOOTER_LOGOS, day and night versions; theme.css shows one) and text, above the version.
    const brand = document.createElement('div');
    brand.className = 'brand';
    for (const item of SITE.footer_logos || []) {
      const holder = document.createElement(item.href ? 'a' : 'span');
      if (item.href) Object.assign(holder, {href: item.href, target: '_blank', rel: 'noopener noreferrer', title: item.href});
      holder.className = 'logo-' + item.name;
      holder.append(logoImg(item.name, 'day'), logoImg(item.night, 'night'));
      brand.append(holder);
    }
    const credit = document.createElement('div');
    credit.textContent = (SITE.footer_text || SITE.name) + ' · ';
    credit.append(link);
    footer.replaceChildren(...(brand.childElementCount ? [brand] : []), credit);
  }

  // A logo at a whole-pixel width (its CSS height times the file's aspect ratio, rounded): a fractional width blurs the
  // edges of a small logo on a standard-resolution screen.
  function logoImg(name, cls) {
    const img = document.createElement('img');
    img.alt = '';
    if (cls) img.className = cls;
    img.addEventListener('load', () => {
      const height = parseFloat(getComputedStyle(img).height);
      if (height && img.naturalWidth && img.naturalHeight)
        img.style.width = Math.round(height * img.naturalWidth / img.naturalHeight) + 'px';
      snapLogos();
    });
    img.src = `/logos/${encodeURIComponent(name)}.svg`;
    return img;
  }

  // Centring can still put the footer logos half a pixel off; shift them onto the screen's pixel grid.
  function snapLogos() {
    const brand = document.querySelector('#webuiVersion .brand');
    if (!brand) return;
    brand.style.transform = '';
    const dpr = window.devicePixelRatio || 1;
    const left = brand.firstElementChild ? brand.firstElementChild.getBoundingClientRect().left * dpr : 0;
    const shift = (Math.round(left) - left) / dpr;
    if (Math.abs(shift) > 0.01) brand.style.transform = `translateX(${shift}px)`;
  }
  window.addEventListener('resize', snapLogos);

  // While an administrator views the site as someone else (LOGDB_VIEW_AS): a band above the header on every page.
  function paintViewAs(by, user) {
    document.getElementById('viewAsBar')?.remove();
    if (!by || !user) return;
    const bar = document.createElement('div');
    bar.id = 'viewAsBar';
    bar.setAttribute('role', 'status');
    const who = document.createElement('span');
    who.innerHTML = 'Viewing as <b></b>: you see and change everything as this person.';
    who.querySelector('b').textContent = user.display_name;
    who.title = `Signed in as ${by.display_name}. Passwords, API keys and script sign-ins stay blocked.`;
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.textContent = 'Stop viewing as';
    stop.onclick = async () => {
      stop.disabled = true;
      await fetch('/api/view-as/stop', {method: 'POST'});
      location.href = `/people/${user.id}`;
    };
    bar.append(who, stop);
    document.body.prepend(bar);
  }

  // The site logo (LOGDB_SITE_LOGO), else its name, at the left of the header; it leads to the Home page. The page's own
  // <h1> stays for screen readers and for pages that change it. The sign-in pages show the logo in their card and add
  // the site name to the tab title.
  function paintBrand() {
    const header = document.querySelector('body > header');
    const h1 = header && header.querySelector('h1');
    if (h1 && !header.querySelector('.site-brand')) {
      const a = document.createElement('a');
      a.className = 'site-brand';
      a.href = '/home';
      a.title = `${SITE.name} · Home`;
      if (SITE.logo) {
        const img = logoImg(SITE.logo.night);  // the header is dark in day and night mode
        img.alt = SITE.name;
        a.append(img);
      } else a.append(Object.assign(document.createElement('span'), {textContent: SITE.name}));
      h1.classList.add('sr-only');
      h1.before(a);
    }
    const heading = document.querySelector('[data-site-heading]');
    if (heading) {
      if (SITE.logo) {
        heading.replaceChildren(logoImg(SITE.logo.name, 'day'), logoImg(SITE.logo.night, 'night'));
        heading.classList.add('site-logo');
        heading.setAttribute('aria-label', SITE.name);
      } else heading.textContent = SITE.name;
    }
    document.querySelectorAll('[data-site-name]').forEach(el => { el.textContent = SITE.name; });
    if (/^\/(login|setup|cli-login|profile|welcome)/.test(location.pathname) && !document.title.endsWith(SITE.name))
      document.title += ' - ' + SITE.name;
  }

  window.logout = async function () {
    try { [ADMIN_KEY, TRAINING_KEY, BOOKINGS_KEY, UPLOAD_KEY].forEach(k => localStorage.removeItem(k)); } catch (e) {}
    await fetch('/logout', {method: 'POST'});
    location.href = '/login';
  };

  let admin = cachedAdmin();
  function ready(fn) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fn);
    else fn();
  }
  ready(() => {
    paintBrand();
    paintNav();
    if (!/^\/(login|setup|cli-login|profile|welcome)/.test(location.pathname)) {
      paintActions();
      paintVersion(null);
    }
  });

  if (!/^\/(login|setup|cli-login|profile|welcome)/.test(location.pathname)) {
    fetch('/api/me').then(async r => {
      if (r.status === 401) {
        try { localStorage.removeItem(ADMIN_KEY); localStorage.removeItem(TRAINING_KEY); localStorage.removeItem(BOOKINGS_KEY); localStorage.removeItem(DOCUMENTS_KEY); localStorage.removeItem(SAFETY_KEY); } catch (e) {}
        return;
      }
      if (!r.ok) throw new Error(`Could not load account and Web UI version (${r.status})`);
      const d = await r.json();
      ready(() => paintVersion(d.webui_version));
      ready(() => paintViewAs(d.viewing_as_by, d.user));
      admin = !!(d.user && d.user.is_admin);
      setCachedAdmin(admin);
      setCachedTraining(!!d.training);
      try { localStorage.setItem(BOOKINGS_KEY, d.bookings ? '1' : '0'); } catch (e) {}
      try { localStorage.setItem(DOCUMENTS_KEY, d.documents ? '1' : '0'); } catch (e) {}
      try { localStorage.setItem(SAFETY_KEY, d.safety ? '1' : '0'); } catch (e) {}
      try { localStorage.setItem(UPLOAD_KEY, d.can_upload === false ? '0' : '1'); } catch (e) {}
      paintUpload();
      paintNav();
    }).catch(error => { console.warn('Could not load account and Web UI version:', error); });
  }
})();

// Page-level tabs: tabs is [[key, label, count?], ...]; renders accessible tab buttons (arrow keys move between them) and
// calls choose(key) on a click. Hidden when there is only one tab.
window.pageTabs = function (el, tabs, current, choose) {
  el.hidden = tabs.length < 2;
  el.setAttribute('role', 'tablist');
  el.innerHTML = tabs.map(([key, label, count]) => `<button type="button" role="tab" data-tab="${key}" aria-selected="${key === current}"
    tabindex="${key === current ? 0 : -1}">${label}${count == null ? '' : `<span class="n">${count}</span>`}</button>`).join('');
  const buttons = [...el.querySelectorAll('[role="tab"]')];
  buttons.forEach((b, i) => {
    b.onclick = () => choose(b.dataset.tab);
    b.onkeydown = e => {
      const step = {ArrowRight: 1, ArrowLeft: -1}[e.key];
      if (!step) return;
      e.preventDefault();
      const next = buttons[(i + step + buttons.length) % buttons.length];
      choose(next.dataset.tab);
      el.querySelector(`[data-tab="${next.dataset.tab}"]`)?.focus();
    };
  });
};

// A person profile (profiles.py) as label/value pairs of a .kv grid; the number only when the viewer may see it.
window.profileHTML = function (p) {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  if (!p) return '<span class="mute">Not filled in yet.</span><span></span>';
  const sups = (p.supervisors || []).map(s => s.id ? `<a href="/people/${s.id}">${esc(s.name)}</a>`
    : `${esc(s.name)} <span class="mute">(external)</span>`).join(' · ') || '<span class="mute">none</span>';
  const ended = !p.indefinite && p.end_date && p.end_date < new Date().toISOString().slice(0, 10);
  const rows = [
    ['Role', esc(p.role) || '<span class="mute">none</span>'],
    ['Department', esc(p.department) + (p.department_group ? ` <span class="mute">(${esc(p.department_group)})</span>` : '')],
    ['Supervisors', sups],
    ['Expected end', p.indefinite ? 'Indefinite' : esc(p.end_date) + (ended ? ' <span class="mute">(passed)</span>' : '')],
  ];
  if ('number' in p) rows.push(['Student / employee no.', esc(p.number)]);
  rows.push(['GitHub', p.github ? `<a href="https://github.com/${encodeURIComponent(p.github)}" target="_blank" rel="noopener">${esc(p.github)}</a>` : '<span class="mute">none</span>']);
  rows.push(['Last confirmed', `<span class="mute">${esc((p.updated_at || '').slice(0, 10))}</span>`]);
  return rows.map(([k, v]) => `<span class="mute">${k}</span><span>${v}</span>`).join('');
};

// Fit the header to the window: when the menu would have to scroll (it hides items such as Directory), first make the
// header compact (shorter site name, icon-only filter/Download/Upload), and if that is not enough give the menu its own
// row. Measured, not fixed breakpoints, because the menu depends on the features that are on and on the user.
function fitHeader() {
  const header = document.querySelector('body > header');
  if (!header) return;
  const fits = () => {
    const nav = document.getElementById('nav');
    return (!nav || nav.scrollWidth <= nav.clientWidth + 1) && header.scrollWidth <= header.clientWidth + 1;
  };
  let queued = false;
  function fit() {
    queued = false;
    header.classList.remove('header-compact', 'header-wrap');
    if (fits()) return;
    header.classList.add('header-compact');
    if (!fits()) header.classList.add('header-wrap');
  }
  const later = () => { if (!queued) { queued = true; requestAnimationFrame(fit); } };
  window.addEventListener('resize', later);
  // the menu, buttons and user name arrive after /api/me; watch for them (not for our own class changes)
  new MutationObserver(later).observe(header, {childList: true, subtree: true, characterData: true});
  if (document.fonts) document.fonts.ready.then(later);
  later();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', fitHeader);
else fitHeader();
