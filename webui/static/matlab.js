// MATLAB export menu (flight page and logbook). MatlabExport.button(sources) returns a "MATLAB ▾" button for the
// SD/GCS recordings of a flight; the menu starts an export per recording (this flight, all flights of that log or
// the whole log), polls its progress and offers the download when ready. See TECHNICAL.md, "MATLAB export".
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const TYPES = {onboard_sd: 'SD log', gcs: 'GCS log'};
  const SCOPES = [
    ['flight', 'This flight', 'Only this flight, from 10 s before takeoff until 10 s after landing'],
    ['flights', 'All flights', 'Every detected flight of this log (ground records left out), each with 10 s margins'],
    ['log', 'Whole log', 'The complete log: every aircraft and message, with the original log timestamps']];
  const jobs = {};  // "<log>:<scope>:<flight>" -> last status
  let menu = null, anchor = null;
  const key = (source, scope) => `${source.log_id}:${scope}:${scope === 'flight' ? source.flight_id : ''}`;
  const usable = sources => (sources || []).filter(s => TYPES[s.log_type]);
  const size = bytes => `${(bytes / 1048576).toFixed(bytes < 10485760 ? 1 : 0)} MB`;

  function item(source, [scope, label, tip]) {
    const job = jobs[key(source, scope)];
    let state = '';
    if (job?.status === 'ready') {
      return `<a class="matlab-item" href="${esc(job.download)}" title="${esc(tip)}">${label}<span class="matlab-state">⬇ ${size(job.size_bytes)}</span></a>`;
    }
    if (job?.status === 'queued') state = 'waiting';
    else if (job?.status === 'running') state = `preparing ${Math.round(job.progress * 100)}%`;
    else if (job?.status === 'failed') state = `<span class="warn">${esc(job.error)}</span>`;
    const busy = job?.status === 'queued' || job?.status === 'running';
    return `<button type="button" class="matlab-item" data-matlab-start="${scope}" data-log="${source.log_id}"
      data-flight="${source.flight_id}" title="${esc(tip)}"${busy ? ' disabled' : ''}>${label}<span class="matlab-state">${state}</span></button>`;
  }

  function render() {
    if (!menu) return;
    const sources = JSON.parse(anchor.dataset.matlabSources);
    menu.innerHTML = sources.map(source => `<div class="matlab-group"><div class="matlab-head">${TYPES[source.log_type]}
        <span class="mute">${esc(source.log_name || '')}</span></div>${SCOPES.map(s => item(source, s)).join('')}</div>`).join('')
      + '<p class="mute matlab-note">Prepared on the server and kept two weeks. Load with <code>s = load(file).s</code>.</p>';
  }

  function close() {
    if (!menu) return;
    menu.remove();
    anchor.setAttribute('aria-expanded', 'false');
    menu = anchor = null;
  }

  function open(button) {
    close();
    anchor = button;
    menu = document.createElement('div');
    menu.className = 'matlab-pop';
    menu.setAttribute('role', 'menu');
    document.body.append(menu);
    render();
    const r = button.getBoundingClientRect();
    menu.style.top = `${r.bottom + window.scrollY + 6}px`;
    menu.style.left = `${Math.max(8, Math.min(r.left + window.scrollX, window.scrollX + innerWidth - menu.offsetWidth - 8))}px`;
    button.setAttribute('aria-expanded', 'true');
  }

  async function poll(k) {
    while (['queued', 'running'].includes(jobs[k]?.status)) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      const r = await fetch(`/api/matlab-exports/${jobs[k].id}`);
      jobs[k] = r.ok ? await r.json() : {status: 'failed', error: 'The export was lost (server restart?); try again.'};
      render();
    }
  }

  async function start(button) {
    const source = {log_id: +button.dataset.log, flight_id: +button.dataset.flight};
    const scope = button.dataset.matlabStart, k = key(source, scope);
    jobs[k] = {status: 'queued', progress: 0};
    render();
    const r = await fetch(`/api/logs/${source.log_id}/matlab`, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scope, flight_id: source.flight_id})});
    const d = await r.json().catch(() => ({}));
    jobs[k] = r.ok ? d : {status: 'failed', error: d.error || `Export failed (HTTP ${r.status}).`};
    render();
    poll(k);
  }

  document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-matlab-sources]');
    if (toggle) {
      event.stopPropagation();
      toggle === anchor ? close() : open(toggle);
      return;
    }
    const begin = event.target.closest('[data-matlab-start]');
    if (begin) { start(begin); return; }
    if (menu && !event.target.closest('.matlab-pop')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu) { const a = anchor; close(); a.focus(); }
  });
  addEventListener('resize', close);

  window.MatlabExport = {
    available: sources => usable(sources).length > 0,
    button(sources, extraClass = '') {
      const list = usable(sources).map(s => ({log_id: s.log_id, flight_id: s.flight_id, log_type: s.log_type, log_name: s.log_name}));
      if (!list.length) return '';
      return `<button type="button" class="matlab-button ${extraClass}" aria-haspopup="menu" aria-expanded="false"
        title="Export this flight or its log for MATLAB (.mat)" data-matlab-sources="${esc(JSON.stringify(list))}">MATLAB ▾</button>`;
    },
  };
})();
