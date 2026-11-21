// "Open in Flight Plotter ▾" (flight page and expanded Logbook row): opens a blackbox log (Betaflight/INDIFlight)
// in the external INDIFlight web app, Flight Plotter by default, Blackbox Viewer from the menu. The app is opened
// with ?openlog=plotter|viewer, says {type: "indiflight-ready"} to its opener, and this page answers with
// {type: "indiflight-log", name, data: ArrayBuffer}. The browser downloads the log here, so the app never needs
// to reach this server. See TECHNICAL.md, "Open in Flight Plotter".
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const TYPES = ['betaflight', 'indiflight', 'blackbox'];
  const TOOLS = {plotter: 'Flight Plotter', viewer: 'Blackbox Viewer'};
  // Another app origin for testing: localStorage.setItem('logdbIndiflightApp', 'http://localhost:8080')
  const app = () => {
    try { return new URL(localStorage.getItem('logdbIndiflightApp') || 'https://app.indiflight.org').origin; }
    catch (e) { return 'https://app.indiflight.org'; }
  };
  const ICON = '<svg class="ext-icon" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M9 2.5h4.5V7M13.5 2.5 7 9'
    + 'M12 9.5v3a1 1 0 0 1-1 1H3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  let menu = null, anchor = null;

  function button(sources) {
    const seen = new Set();
    const logs = (sources || []).filter(s => s.log_id && TYPES.includes(s.log_type) && !seen.has(s.log_id) && seen.add(s.log_id));
    return logs.map(s => {
      const name = logs.length > 1 && s.log_name ? ` (${esc(s.log_name)})` : '';
      const data = `data-log="${s.log_id}" data-name="${esc(s.log_name || '')}"`;
      return `<span class="ext-split"><button type="button" class="btn sec" data-indiflight-open="plotter" ${data}
          title="Open ${esc(s.log_name || 'this log')} in the INDIFlight Flight Plotter (${esc(new URL(app()).host)}, opens in a new tab)">Open in Flight Plotter${name} ${ICON}</button><button
          type="button" class="btn sec ext-caret" data-indiflight-menu ${data} aria-haspopup="menu" aria-expanded="false"
          aria-label="More ways to open this log">▾</button></span>`;
    }).join('');
  }

  function close() {
    if (!menu) return;
    menu.remove();
    anchor.setAttribute('aria-expanded', 'false');
    menu = anchor = null;
  }

  function openMenu(toggle) {
    close();
    anchor = toggle;
    menu = document.createElement('div');
    menu.className = 'matlab-pop ext-pop';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `<button type="button" class="matlab-item" role="menuitem" data-indiflight-open="viewer"
      data-log="${toggle.dataset.log}" data-name="${esc(toggle.dataset.name)}">Open in Blackbox Viewer ${ICON}</button>`;
    document.body.append(menu);
    const r = toggle.getBoundingClientRect();
    menu.style.top = `${r.bottom + window.scrollY + 6}px`;
    menu.style.left = `${Math.max(8, Math.min(r.right + window.scrollX - menu.offsetWidth, window.scrollX + innerWidth - menu.offsetWidth - 8))}px`;
    toggle.setAttribute('aria-expanded', 'true');
  }

  // The window is opened in the click itself (popup blockers), the log is fetched meanwhile and sent when the app
  // says it is ready. Only that window's messages from the app origin count.
  function launch(tool, logId, name) {
    const origin = app();
    const win = window.open(`${origin}/?openlog=${tool}`, '_blank');
    if (!win) { alert('The browser blocked the new tab. Allow pop-ups for this site and try again.'); return; }
    const log = fetch(`/api/logs/${logId}/download/data`).then(r => r.ok ? r.arrayBuffer()
      : Promise.reject(new Error(`Download failed (HTTP ${r.status}).`)));
    log.catch(() => {});
    const done = () => { removeEventListener('message', onMessage); clearTimeout(timer); };
    const timer = setTimeout(done, 120000);
    function onMessage(event) {
      if (event.source !== win || event.origin !== origin || event.data?.type !== 'indiflight-ready') return;
      done();
      log.then(data => win.postMessage({type: 'indiflight-log', name: name || `log-${logId}.bfl`, data}, origin, [data]),
        err => { win.close(); alert(`Could not open the log in ${TOOLS[tool]}: ${err.message}`); });
    }
    addEventListener('message', onMessage);
  }

  document.addEventListener('click', event => {
    const toggle = event.target.closest('[data-indiflight-menu]');
    if (toggle) { event.stopPropagation(); toggle === anchor ? close() : openMenu(toggle); return; }
    const go = event.target.closest('[data-indiflight-open]');
    if (go) { event.stopPropagation(); close(); launch(go.dataset.indiflightOpen, go.dataset.log, go.dataset.name); return; }
    if (menu && !menu.contains(event.target)) close();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && menu) { const a = anchor; close(); a.focus(); } });

  window.IndiflightOpen = {button};
})();
