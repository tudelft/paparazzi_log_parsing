// Upload dialog, opened by "Upload logs" in the header (theme.js) or by dropping files on a page. Logs are sent in
// batches to /api/upload; fingerprinting and parsing then run on the server. "Your recent uploads" follows their
// parsing. When a batch is sent, a 'logsuploaded' event lets the open page refresh (the Logs list, the Logbook).
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const MAV_SINGLE = /\.(ulg|bin|tlog|bfl|bbl)$/i;
  const UPLOAD_CONCURRENCY = 3;
  const PAIRS_PER_POST = 20;
  const MAX_POST_BYTES = 64 * 1024 * 1024;
  let sendTotal = 0, sendDone = 0, sendActive = 0, recentTimer = null, back = null;
  const $ = id => document.getElementById(id);

  function build() {
    back = document.createElement('div');
    back.className = 'upload-back';
    back.hidden = true;
    back.innerHTML = `<div class="upload-dialog" role="dialog" aria-modal="true" aria-labelledby="uploadTitle">
      <div class="dialog-header"><h2 id="uploadTitle">Upload logs</h2><button type="button" id="uploadClose">Close</button></div>
      <div class="upload-zone" id="uploadZone">
        <strong>Drop log files, zips or folders here</strong>
        Paparazzi .data + .log, PX4 .ulg, ArduPilot .bin / .log, MAVLink .tlog, Betaflight/INDIFlight .bfl / .bbl
        <div class="upload-buttons"><button type="button" class="primary" id="uploadPickFiles">Select files</button>
          <button type="button" id="uploadPickDir">Select folder</button></div>
        <input id="uploadFiles" type="file" multiple hidden accept=".data,.log,.zip,.ulg,.bin,.tlog,.bfl,.bbl">
        <input id="uploadDir" type="file" webkitdirectory hidden>
      </div>
      <details><summary>Help</summary>
        <p>Paparazzi .data and .log files must come as a pair with the same name; the other formats are uploaded on
        their own. Zips and folders may mix all of those. If a folder is named after one fleet aircraft, logs in its
        dated subfolders attach to it. Files are accepted quickly; fingerprinting, duplicates and parsing run in the
        background, so you can close this dialog once the files are sent. For hundreds of logs, drop a zip or a folder:
        picking thousands of files one by one can freeze the browser.</p>
        <p>The project is set from the aircraft (then the uploader, GCS operator or PIC). All logs you can see are
        listed under Directory ▾ → Logs.</p>
      </details>
      <div id="uploadBatch" hidden></div>
      <div id="uploadNotes"></div>
      <div class="upload-recent"><h3>Your recent uploads</h3><div id="uploadRecent" class="mute">Loading…</div></div>
    </div>`;
    document.body.appendChild(back);
    $('uploadClose').onclick = close;
    back.addEventListener('click', e => { if (e.target === back) close(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !back.hidden) close(); });
    const zone = $('uploadZone');
    // The whole dialog (and its backdrop) takes drops
    ['dragenter', 'dragover'].forEach(ev => back.addEventListener(ev, e => { e.preventDefault(); zone.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => back.addEventListener(ev, e => { e.preventDefault(); zone.classList.remove('over'); }));
    back.addEventListener('drop', e => { e.stopPropagation(); fromDrop(e); });
    $('uploadPickFiles').onclick = () => $('uploadFiles').click();
    $('uploadFiles').onchange = async e => {
      const list = e.target.files;
      if (!list || !list.length) return;
      const items = await itemsFromFileList(list, f => f.name);
      e.target.value = '';
      await uploadItems(items);
    };
    $('uploadPickDir').onclick = async () => {
      if (window.showDirectoryPicker) {
        try {
          const dir = await window.showDirectoryPicker();
          const items = [];
          paintPrepare(0);
          await walkDirHandle(dir, dir.name + '/', items);
          await uploadItems(items);
        } catch (err) {
          if (err && err.name !== 'AbortError')
            note(`<div><span class="badge error">error</span> <span class="mute">${esc(err.message || err)}</span></div>`);
        }
        return;
      }
      $('uploadDir').click();
    };
    $('uploadDir').onchange = async e => {
      const list = e.target.files;
      if (!list || !list.length) return;
      const items = await itemsFromFileList(list, f => f.webkitRelativePath || f.name);
      e.target.value = '';
      await uploadItems(items);
    };
  }

  function open(dropEvent) {
    if (!back) build();
    back.hidden = false;
    loadRecent();
    if (dropEvent) fromDrop(dropEvent);
    else $('uploadPickFiles').focus();
  }
  function close() {
    if (sendActive && !confirm('Logs are still being sent. Close anyway? The upload stops if you leave the page.')) return;
    back.hidden = true;
    clearTimeout(recentTimer);
  }

  // Your recent uploads with their parsing status, refreshed while something is still being checked or parsed
  async function loadRecent() {
    clearTimeout(recentTimer);
    if (!back || back.hidden) return;
    let rows = [];
    try {
      const r = await fetch('/api/logs?mine=1&limit=8');
      rows = r.ok ? (await r.json()).logs || [] : [];
    } catch (e) { rows = []; }
    let waiting = false;
    $('uploadRecent').innerHTML = rows.length ? rows.map(l => {
      const busy = l.status === 'queued' || l.status === 'parsing';
      waiting ||= busy;
      const state = l.status === 'error' ? `<span class="bad">failed</span>`
        : busy ? `<span class="busy">${l.status === 'parsing' ? 'parsing…' : 'waiting'}</span>`
        : `<span class="ok">parsed · ${l.num_flights || 0} flight${l.num_flights === 1 ? '' : 's'}</span>`;
      const href = l.first_flight_id && l.flight_rows === 1 ? `/flight/${l.first_flight_id}` : `/logs#log${l.id}`;
      return `<div class="row"><a href="${href}" title="${esc(l.name)}">${esc(l.name)}</a>${state}</div>`;
    }).join('') : '<span class="mute">None yet.</span>';
    if (waiting || sendActive) recentTimer = setTimeout(loadRecent, 3000);
  }

  // Groups: every zip or PX4/ArduPilot/tlog/Blackbox file alone, Paparazzi .data/.log paired by directory + name.
  function groupFiles(items) {
    const groups = new Map(), skipped = [];
    for (const {file, rel} of items) {
      const lower = rel.toLowerCase();
      if (lower.endsWith('.zip') || MAV_SINGLE.test(lower)) { groups.set(rel, [{file, rel}]); continue; }
      const m = rel.match(/^(.*)\.(data|log)$/i);
      if (!m) { skipped.push(rel); continue; }
      if (!groups.has(m[1])) groups.set(m[1], []);
      groups.get(m[1]).push({file, rel});
    }
    return {groups: [...groups.values()], skipped};
  }

  function yieldToUi() { return new Promise(r => setTimeout(r, 0)); }

  function paintPrepare(seen, total) {
    const el = $('uploadBatch');
    el.hidden = false;
    el.textContent = total != null ? `Preparing ${seen} / ${total} files…` : `Preparing ${seen} files…`;
  }

  function paintBatch() {
    const el = $('uploadBatch');
    if (!sendTotal && !sendActive) { el.hidden = true; return; }
    el.hidden = false;
    if (sendActive)
      el.innerHTML = `<strong>Uploading… ${sendDone} of ${sendTotal} logs sent</strong> <progress max="${sendTotal}" value="${sendDone}"></progress>
        <span class="mute">Keep this page open until it is finished.</span>`;
    else
      el.innerHTML = `<strong>Upload finished: ${sendDone} of ${sendTotal} logs sent.</strong>
        <span class="mute">Checking and parsing continue on the server; you can close this dialog.</span>`
        + (sendDone < sendTotal ? ` <span class="badge error">${sendTotal - sendDone} failed — see above</span>` : '');
  }

  function note(html) {
    const box = $('uploadNotes');
    box.insertAdjacentHTML('beforeend', html);
    while (box.children.length > 50) box.removeChild(box.firstChild);
  }

  function packJobs(jobs) {
    const packs = [];
    let cur = [], bytes = 0;
    const flush = () => { if (cur.length) { packs.push(cur); cur = []; bytes = 0; } };
    for (const g of jobs) {
      const isZip = g.length === 1 && g[0].rel.toLowerCase().endsWith('.zip');
      const gBytes = g.reduce((a, x) => a + x.file.size, 0);
      if (isZip) { flush(); packs.push([g]); continue; }
      if (cur.length && (cur.length >= PAIRS_PER_POST || bytes + gBytes > MAX_POST_BYTES)) flush();
      cur.push(g);
      bytes += gBytes;
    }
    flush();
    return packs;
  }

  function postBatch(pack) {
    sendActive++;
    paintBatch();
    const fd = new FormData();
    for (const g of pack)
      for (const {file, rel} of g) { fd.append('files', file, file.name); fd.append('names', rel); }
    const n = pack.length;
    const names = pack.map(g => g[0].rel.replace(/\.(data|log)$/i, ''));
    const failAll = why => note(`<div><span class="badge error">failed</span> <span>${esc(why)}:</span> <span class="mute">${names.map(esc).join(', ')}</span></div>`);
    let arrived = 0;
    return new Promise(resolve => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload');
      xhr.onload = () => {
        if (xhr.status === 401) { location.href = '/login'; return; }
        try {
          const body = JSON.parse(xhr.responseText);
          if (body.results) {
            arrived = n;
            for (const r of body.results) {
              if (r.status === 'rejected' || r.status === 'error')
                note(`<div><span class="badge error">${esc(r.status)}</span> <span class="mute">${esc(r.name)}${r.reason ? ' — ' + esc(r.reason) : ''}</span></div>`);
            }
          } else failAll(body.error || ('server error ' + xhr.status));
        } catch { failAll(xhr.status === 413 ? 'too large for the server' : 'server error ' + xhr.status); }
        resolve();
      };
      xhr.onerror = () => { failAll('connection lost'); resolve(); };
      xhr.send(fd);
    }).finally(() => { sendActive--; sendDone += arrived; paintBatch(); });
  }

  async function itemsFromFileList(list, relFn) {
    const items = [], n = list.length;
    paintPrepare(0, n);
    for (let i = 0; i < n; i++) {
      items.push({file: list[i], rel: relFn(list[i])});
      if ((i & 31) === 0) { paintPrepare(i + 1, n); await yieldToUi(); }
    }
    return items;
  }

  async function uploadItems(items) {
    paintPrepare(items.length, items.length);
    await yieldToUi();
    const {groups, skipped} = groupFiles(items);
    if (skipped.length)
      note(`<div class="mute">${skipped.length} ignored (not a recognised log or zip)</div>`);
    const jobs = [];
    let incomplete = 0;
    for (const g of groups) {
      const one = g.length === 1 ? g[0].rel.toLowerCase() : '';
      const isZip = one.endsWith('.zip');
      const isMav = MAV_SINGLE.test(one);
      const isLoneLog = /\.log$/.test(one);
      if (!isZip && !isMav && !isLoneLog && !(g.some(x => /\.data$/i.test(x.rel)) && g.some(x => /\.log$/i.test(x.rel)))) {
        incomplete++;
        continue;
      }
      jobs.push(g);
    }
    if (incomplete)
      note(`<div><span class="badge error">rejected</span> <span class="mute">${incomplete} skipped — Paparazzi logs need both a .data and a matching .log file</span></div>`);
    if (!jobs.length) { paintBatch(); return; }
    const packs = packJobs(jobs);
    sendTotal += jobs.length;
    paintBatch();
    let next = 0;
    async function run() {
      while (next < packs.length) {
        const pack = packs[next++];
        await postBatch(pack);
        await yieldToUi();
      }
    }
    await Promise.all(Array.from({length: Math.min(UPLOAD_CONCURRENCY, packs.length)}, run));
    window.dispatchEvent(new Event('logsuploaded'));
    loadRecent();
  }

  async function readEntry(entry, path, out) {
    if (entry.isFile) {
      const file = await new Promise((ok, no) => entry.file(ok, no));
      out.push({file, rel: path + entry.name});
      if (out.length % 40 === 0) { paintPrepare(out.length); await yieldToUi(); }
      return;
    }
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((ok, no) => reader.readEntries(ok, no));
      if (!batch.length) break;
      for (const e of batch) await readEntry(e, path + entry.name + '/', out);
    }
  }

  async function walkDirHandle(handle, prefix, items) {
    for await (const child of handle.values()) {
      const rel = prefix + child.name;
      if (child.kind === 'directory') await walkDirHandle(child, rel + '/', items);
      else if (child.kind === 'file') items.push({file: await child.getFile(), rel});
      if (items.length && items.length % 40 === 0) { paintPrepare(items.length); await yieldToUi(); }
    }
  }

  // Dropped files: read the entries first (the browser drops them after this event)
  async function fromDrop(e) {
    const entries = [...(e.dataTransfer?.items || [])].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    const plain = entries.length ? [] : [...(e.dataTransfer?.files || [])];
    const items = [];
    paintPrepare(0);
    for (const en of entries) await readEntry(en, '', items);
    for (const file of plain) items.push({file, rel: file.name});
    await uploadItems(items);
  }

  window.LogUpload = {open};
})();
