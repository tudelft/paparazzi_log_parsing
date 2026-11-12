// Download dialog, opened by "Download logs" in the header (theme.js): minimal examples for the Python package
// mavlab-logbook-client and the MATLAB tools, with this server filled in. Python is the default tab; the last
// chosen tab is remembered in this browser.
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const TAB_KEY = 'logdbDownloadTab';
  const REPO = 'https://github.com/tudelft/paparazzi_log_parsing';
  // connect() adds https:// to a bare host name; a plain-http server (local testing) needs its full origin
  const server = location.protocol === 'https:' ? location.host : location.origin;
  let back = null, tab = 'python';
  try { if (localStorage.getItem(TAB_KEY) === 'matlab') tab = 'matlab'; } catch (e) {}
  const $ = id => document.getElementById(id);

  // Code lines with their comments in one column: lines('#', [code, comment], [code], ...)
  const lines = (mark, ...rows) => {
    const width = Math.max(...rows.filter(r => r[1]).map(r => r[0].length)) + 2;
    return rows.map(([code, note]) => note ? code.padEnd(width) + mark + ' ' + note : code).join('\n');
  };
  const block = (code, what) => `<div class="download-code">${window.copyButton(code, what)}<pre>${esc(code)}</pre></div>`;

  const PANES = {
    python: () => `
      <p>Install once (Python 3.10+, no dependencies):</p>
      ${block('pip install mavlab-logbook-client', 'the install command')}
      <p>Download by flight number (the <strong>No.</strong> in the Logbook and in /flight/&lt;number&gt;):</p>
      ${block(lines('#', ['import mavlab_logbook_client as ml'], [''],
        [`ml.connect("${server}")`, 'sign in (browser), once a week'],
        ['mats = ml.fetch([1234, 1240])', '.mat (v7.3/HDF5), one per log'],
        ['mats = ml.fetch([1234], scope="log")', 'or "selected", "all", "flight"'],
        ['logs = ml.fetch_raw([1234, 1240])', 'the original log files'],
        ['rows = ml.logbook()', 'the flights you may see']), 'the Python example')}
      <details><summary>Help</summary>
        <p>Scopes: <code>selected</code> the given flights of each log (10 s before takeoff to 10 s after landing),
        <code>all</code> every flight of those logs, <code>log</code> the whole log, <code>flight</code> one file per
        number. Files are cached and reused while they are unchanged. The <code>.mat</code> export exists for Paparazzi
        logs; <code>fetch_raw</code> works for every log type. From a shell:
        <code>mavlab-logbook-client --server ${esc(server)} fetch 1234</code> (or <code>fetch-raw</code>).
        More in the <a href="${REPO}/tree/master/python/mavlab-logbook-client#readme" target="_blank" rel="noopener noreferrer">package README</a>.</p>
      </details>`,
    matlab: () => `
      <p>Get the MATLAB tools once (R2021a or newer), with git or as a
      <a href="${REPO}/archive/refs/heads/master.zip" title="Unpacks to paparazzi_log_parsing-master: use that folder in addpath">zip</a>:</p>
      ${block(`git clone ${REPO}.git`, 'the clone command')}
      <p>Download by flight number (the <strong>No.</strong> in the Logbook and in /flight/&lt;number&gt;):</p>
      ${block(lines('%', ["addpath(genpath('paparazzi_log_parsing/matlab'))", 'savepath keeps it'],
        [`pprz_connect('${server}')`, 'sign in (browser), once a week'],
        ['logs = pprz_fetch([1234 1240]);', '.mat, one per log, loaded'],
        ["logs = pprz_fetch(1234, 'scope', 'log');", "or 'selected', 'all', 'flight'"],
        ['T = pprz_logbook();', 'the flights you may see'],
        ["f = pprz_flight(logs, 'number', 1240);", 'one flight, to plot'],
        ["pprz_plot(f, 'overview')"]), 'the MATLAB example')}
      <details><summary>Help</summary>
        <p>Scopes: <code>selected</code> the given flights of each log (10 s before takeoff to 10 s after landing),
        <code>all</code> every flight of those logs, <code>log</code> the whole log, <code>flight</code> one file per
        number. Files are cached (shared with Python) and reused while they are unchanged; the export exists for
        Paparazzi logs. The original (raw) log files come from Python (<code>ml.fetch_raw</code>) or from
        <em>download</em> on a flight page. More in the
        <a href="${REPO}/tree/master/matlab#readme" target="_blank" rel="noopener noreferrer">MATLAB README</a>.</p>
      </details>`,
  };

  function paint() {
    window.pageTabs($('downloadTabs'), [['python', 'Python'], ['matlab', 'MATLAB']], tab, key => {
      tab = key;
      try { localStorage.setItem(TAB_KEY, key); } catch (e) {}
      paint();
    });
    const pane = $('downloadPane');
    pane.setAttribute('role', 'tabpanel');
    pane.innerHTML = PANES[tab]();
  }

  function build() {
    back = document.createElement('div');
    back.className = 'upload-back';
    back.hidden = true;
    back.innerHTML = `<div class="upload-dialog download-dialog" role="dialog" aria-modal="true" aria-labelledby="downloadTitle">
      <div class="dialog-header"><h2 id="downloadTitle">Download logs</h2><button type="button" id="downloadClose">Close</button></div>
      <div class="seg-tabs" id="downloadTabs" aria-label="Language"></div>
      <div id="downloadPane"></div>
    </div>`;
    document.body.appendChild(back);
    $('downloadClose').onclick = close;
    back.addEventListener('click', e => { if (e.target === back) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !back.hidden) close(); });
  }

  function open() {
    if (!back) build();
    paint();
    back.hidden = false;
    $('downloadTabs').querySelector('[aria-selected="true"]')?.focus();
  }
  function close() { back.hidden = true; }

  window.LogDownload = {open};
})();
