(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const size = value => value >= 2 ** 30 ? `${(value / 2 ** 30).toFixed(2)} GiB` :
    value >= 2 ** 20 ? `${(value / 2 ** 20).toFixed(1)} MiB` : `${(value / 1024).toFixed(1)} KiB`;
  async function api(url, options) {
    const response = await fetch(url, options);
    if (!(response.headers.get('Content-Type') || '').includes('application/json'))
      throw Error(`Server returned HTTP ${response.status}.`);
    const data = await response.json();
    if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  const link = (file, attrs) => fileLink({name: esc(file.filename), attrs, viewable: file.viewable, filename: file.filename,
    view: `/api/attachments/${file.id}/view`, download: `/api/attachments/${file.id}/download`});
  const stored = file => `Stored ${size(file.stored_bytes)}` + (file.compressed
    ? ` (${Math.round(100 * (1 - file.stored_bytes / file.size_bytes))}% saved)` : ', uncompressed');
  // Files replaced by a newer upload with the same name stay attached, folded away as earlier versions.
  const replaced = file => `Replaced on ${esc(String(file.superseded.at || '').slice(0, 10))}`;
  // Current files grouped by category: the configured order, then categories no longer configured (kept on their
  // files), then uncategorised. Empty groups are left out; with only uncategorised files there is no heading.
  function groups(files, categories = []) {
    const names = [...categories, ...[...new Set(files.map(file => file.category).filter(name =>
      name && !categories.includes(name)))].sort(), null];
    const found = names.map(name => ({name, files: files.filter(file => (file.category || null) === name)}))
      .filter(group => group.files.length);
    return found.length === 1 && found[0].name === null ? [{name: null, files, plain: true}] : found;
  }
  function versions(files, show, categories) {
    const current = files.filter(file => !file.superseded), earlier = files.filter(file => file.superseded);
    return groups(current, categories).map(group => (group.plain ? '' :
      `<h4 class="file-group">${esc(group.name || 'Uncategorised')} <span>(${group.files.length})</span></h4>`) +
      show(group.files)).join('') + (earlier.length ? `<details class="earlier-versions"><summary class="mute">Show earlier versions (${
      earlier.length})</summary>${show(earlier)}</details>` : '');
  }
  // Compact list: one row per file under its category; links and storage on hover. Read-only on the flight / paper
  // record pages; on the manage page (onedit) each file the user may change has an Edit button.
  function list(element, files, categories, onedit) {
    element.dataset.mediaGroup = "";  // the media viewer steps through this list
    element.innerHTML = files.length ? versions(files, rows => rows.length ? `<table class="file-list"><tbody>${rows.map(file => {
      const others = file.links.map(l => l.label).join('\n');
      return `<tr><td class="file-name">${link(file, '')}${file.description
        ? `<div class="mute file-desc" title="${esc(file.description)}">${esc(file.description)}</div>` : ''}</td>
        <td class="mute" title="${esc(stored(file))}">${size(file.size_bytes)}</td>
        <td class="mute file-links" title="${esc(others)}">${file.links.length === 1 ? '1 link' : file.links.length + ' links'}</td>${
        file.superseded ? `<td class="mute">${replaced(file)}</td>` : ''}${onedit
        ? `<td class="file-actions">${file.can_edit ? `<button type="button" data-edit="${file.id}">Edit</button>` : ''}</td>` : ''}</tr>`;
    }).join('')}</tbody></table>` : '', categories) : '<p class="mute">No additional files.</p>';
    if (onedit) for (const button of element.querySelectorAll('[data-edit]')) button.onclick = () =>
      onedit(files.find(file => file.id === +button.dataset.edit));
  }
  // One line per category with the file names (description on hover), for the logbook's opened row. `url(category)`
  // gives the page with all files (filtered to that category for "+N").
  function summary(element, files, categories, url) {
    const current = files.filter(file => !file.superseded);
    if (!current.length) { element.replaceChildren(); return; }
    const name = file => `<a href="/api/attachments/${file.id}/${file.viewable ? 'view' : 'download'}"${file.viewable
      ? (mediaKind(file.filename) ? ' ' + mediaAttrs({filename: file.filename, download: `/api/attachments/${file.id}/download`})
        : ' target="_blank" rel="noopener"') : ''} title="${esc(file.description || file.filename)}">${esc(file.filename)}</a>`;
    element.dataset.mediaGroup = "";
    element.innerHTML = `<h4>Files <span class="mute files-count">· ${current.length}</span>${url
      ? `<a class="files-all" href="${esc(url(''))}">Files tab →</a>` : ''}</h4>` + groups(current, categories).map(group => {
      const shown = group.files.slice(0, 4), more = group.files.length - shown.length;
      return `<div class="files-line"><span class="mute" title="${esc(group.name || 'Uncategorised')}">${
        esc(group.name || 'Uncategorised')} (${group.files.length})</span><span class="files-names">${shown.map(name).join(' · ')}</span>${
        more && url ? `<a class="files-more" href="${esc(url(group.name || UNCATEGORISED))}">+${more} more</a>` : ''}</div>`;
    }).join('');
  }

  // ---- File manager on the flight, paper record and logs pages: the grouped list with search, a category filter and
  // Upload files / Edit dialogs. The dialogs are made once per page and serve the manager that opened them.
  const UNCATEGORISED = '-';  // filter value for files without a category
  const option = (value, text) => Object.assign(document.createElement('option'), {value, textContent: text});
  const tkey = t => `${t.kind}:${t.id}`;
  const byId = id => document.getElementById(id);
  let ready = false, active = null, editing = null, busy = false, scopes = [];
  const extras = new Map();  // flights / logs added with Find more: key -> target

  function dialogs() {
    if (ready) return;
    ready = true;
    document.body.insertAdjacentHTML('beforeend', `
<dialog class="entry-dialog af-dialog" id="afUpload" aria-labelledby="afUploadTitle">
  <div class="dialog-header"><h2 id="afUploadTitle">Upload files</h2><button type="button" id="afUploadClose" aria-label="Close Upload files">Close</button></div>
  <div class="af-drop" id="afDrop" role="button" tabindex="0">
    <input id="afFiles" type="file" multiple hidden>
    <strong>Drop files here</strong> <span class="mute">or</span> <span class="af-pick">choose files</span>
  </div>
  <ul class="af-chips" id="afPicked" aria-label="Chosen files"></ul>
  <fieldset id="afUploadFields">
    <div class="af-pair">
      <label class="field">Category<select id="afCategory"></select></label>
      <label class="field">Description (optional)<textarea id="afDescription" maxlength="1000" rows="1"></textarea></label>
    </div>
  </fieldset>
  <fieldset id="afSelection">
    <label class="field" for="afScope">Upload to</label>
    <select id="afScope"></select>
    <p class="mute af-note" id="afScopeNote"></p>
    <div id="afExtra"></div>
    <p class="mute af-note" id="afNoTarget" hidden>You have read-only access here. Add a flight / log where you have full access.</p>
    <details class="af-more" id="afMore"><summary class="mute">Add other flights / logs</summary>
      <form id="afFind" class="search-row"><input id="afFindQuery" type="search" required aria-label="Find flights / logs"
        placeholder="Aircraft, 2026-10-21, log filename or ID"><button type="submit">Search</button></form>
      <div id="afFindResults" class="af-results"></div></details>
  </fieldset>
  <progress id="afProgress" hidden max="100" value="0" aria-label="File upload progress"></progress>
  <p id="afStatus" class="af-status" role="status" aria-live="polite"></p>
  <div class="af-actions"><details class="af-help"><summary class="mute">Help</summary>
    <p id="afLimit"></p>
    <p>A file with the name of a current file there replaces it; the old one stays under "Show earlier versions".
      A file that is already stored is linked, not stored again. Files can also be dropped on the file list.</p>
    <p><strong>Sharing:</strong> everyone with access to any linked flight / log can download the file.</p></details>
    <span class="af-buttons"><button type="button" id="afCancel" hidden>Cancel upload</button>
      <button type="button" id="afSubmit" disabled>Upload</button><button type="button" id="afDone" hidden>Done</button></span></div>
</dialog>
<dialog class="entry-dialog af-dialog" id="afEdit" aria-labelledby="afEditTitle">
  <div class="dialog-header"><h2 id="afEditTitle">Edit file</h2><button type="button" id="afEditClose" aria-label="Close Edit file">Close</button></div>
  <p class="mute af-note" id="afEditName"></p>
  <p class="dialog-error" id="afEditError" role="alert"></p>
  <form id="afEditForm">
    <label class="field">Category<select id="afEditCategory"></select></label>
    <label class="field">Description<textarea id="afEditDescription" maxlength="1000" rows="2"></textarea></label>
    <div class="af-actions"><details class="af-help"><summary class="mute">Help</summary>
      <p>The file is stored once and shared by all its links: a change shows on every flight / log it is linked to.
        Only links you can access are listed; removing the last link deletes the stored file.</p></details>
      <button type="submit">Save</button></div>
  </form>
  <h3 class="af-sub">Linked to</h3>
  <ul id="afLinks" class="af-links"></ul>
  <details class="af-more" id="afLinkMore"><summary class="mute">Link to more flights / logs</summary>
    <form id="afLinkFind" class="search-row"><input id="afLinkQuery" type="search" required aria-label="Find flights / logs to link"
      placeholder="Aircraft, 2026-10-21, log filename or ID"><button type="submit">Search</button></form>
    <div id="afLinkResults" class="af-results"></div></details>
</dialog>`);
    byId('afUploadClose').onclick = () => { if (!busy) byId('afUpload').close(); };
    byId('afUpload').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    byId('afEditClose').onclick = () => byId('afEdit').close();
    byId('afScope').onchange = syncScope;
    byId('afFind').onsubmit = event => {
      event.preventDefault();
      findTargets(byId('afFindQuery').value, byId('afFindResults'), 'Add', t => addExtra(t));
    };
    byId('afSubmit').onclick = uploadAll;
    byId('afCancel').onclick = cancelUpload;
    byId('afDone').onclick = () => byId('afUpload').close();
    const drop = byId('afDrop'), input = byId('afFiles');
    drop.onclick = () => { if (!busy) input.click(); };
    drop.onkeydown = event => { if ((event.key === 'Enter' || event.key === ' ') && !busy) { event.preventDefault(); input.click(); } };
    input.onchange = () => setPicked([...input.files]);
    dropTarget(byId('afUpload'), files => { if (!busy) setPicked(files); }, drop);
    byId('afCategory').onchange = () => { chosenCategory = true; };

    byId('afEditForm').onsubmit = saveEdit;
    byId('afLinkFind').onsubmit = linkSearch;
  }

  // Flights / logs with full access matching the search, each as a line with one action button.
  async function findTargets(query, element, action, onpick) {
    element.textContent = 'Searching...';
    try {
      const data = await api('/api/attachments/targets?q=' + encodeURIComponent(query));
      element.replaceChildren();
      if (!data.targets.length) element.textContent = 'No matching entries with full access.';
      for (const t of data.targets) {
        const line = document.createElement('div'), button = document.createElement('button'), text = document.createElement('span');
        line.className = 'target-line';
        button.type = 'button'; button.textContent = action; button.onclick = () => onpick(t, button);
        text.textContent = `${t.kind}: ${t.label}`;
        line.append(button, text);
        element.append(line);
      }
    } catch (error) { element.textContent = error.message; }
  }

  // ---- Upload dialog
  // A category suggested from the selected files' names, until the user picks one: only when every file points to the
  // same category and that category is in the configured list.
  let chosenCategory = false, picked = [];
  // The upload in progress can be cancelled: the running request is aborted and no further file is sent. After a run
  // with nothing left to send, "Done" replaces "Upload".
  let currentXhr = null, cancelled = false, finished = false;
  function cancelUpload() {
    cancelled = true;
    if (currentXhr) currentXhr.abort();
  }
  // Files dragged onto `element` (highlighting `mark` while over it) are passed to `onfiles`.
  function dropTarget(element, onfiles, mark = element) {
    let depth = 0;
    const hasFiles = event => [...(event.dataTransfer?.types || [])].includes('Files');
    element.addEventListener('dragenter', event => { if (hasFiles(event)) { depth++; mark.classList.add('af-over'); } });
    element.addEventListener('dragleave', () => { if (--depth <= 0) { depth = 0; mark.classList.remove('af-over'); } });
    element.addEventListener('dragover', event => { if (hasFiles(event)) event.preventDefault(); });
    element.addEventListener('drop', event => {
      if (!hasFiles(event)) return;
      event.preventDefault(); depth = 0; mark.classList.remove('af-over');
      if (event.dataTransfer.files.length) onfiles([...event.dataTransfer.files]);
    });
  }
  function setPicked(files) {
    picked = files;
    if (files.length) finished = false;
    const names = files.map(file => file.name);
    byId('afPicked').innerHTML = files.map((file, i) => `<li><span title="${esc(file.name)}">${esc(file.name)}</span>
      <span class="mute af-size">${size(file.size)}</span><button type="button" class="af-x" data-unpick="${i}"
      aria-label="Remove ${esc(file.name)}" title="Remove">×</button></li>`).join('');
    for (const button of byId('afPicked').querySelectorAll('[data-unpick]')) button.onclick = () => {
      if (!busy) setPicked(picked.filter((_, i) => i !== +button.dataset.unpick));
    };
    byId('afDrop').classList.toggle('af-has', files.length > 0);
    guessCategory();
    syncUpload();
  }
  const GUESSES = [
    [/notam|permit|permission|approval|toestemming|vergunning|risk|sora|briefing|flight[ _-]?plan|\.(kml|kmz|gpx)$/i, 'Preparation & permits'],
    [/report|analysis|analyse|plot/i, 'Analysis'],
    [/\.(jpe?g|png|gif|heic|webp|tiff?|mp4|mov|avi|mkv|m4v|insv|360)$/i, 'Media'],
    [/\.(csv|tsv|mat|h5|hdf5|ulg|tlog|bin|bfl|bbl|log|data|json|npz|parquet)$/i, 'Data'],
    [/\.(txt|md|rtf)$/i, 'Notes'],
    [/\.(zip|7z|tar|gz|tgz|rar)$/i, 'Archive'],
  ];
  function guessFor(name) {
    const hit = GUESSES.find(([pattern]) => pattern.test(name));
    return hit ? hit[1] : null;
  }
  function guessCategory() {
    if (chosenCategory) return;
    const names = picked.map(file => file.name);
    const guesses = new Set(names.map(guessFor));
    const guess = guesses.size === 1 ? [...guesses][0] : null;
    const listed = guess && active.categories.find(name => name.toLowerCase() === guess.toLowerCase());
    byId('afCategory').value = listed || '';
  }
  function chosen() {
    const scope = scopes.find(s => s.value === byId('afScope').value);
    const all = [...(scope ? scope.targets : []),
      ...[...byId('afExtra').querySelectorAll('input:checked')].map(input => extras.get(input.value))];
    return [...new Map(all.map(t => [tkey(t), t])).values()];
  }
  function syncUpload() {
    byId('afUploadFields').disabled = busy;
    byId('afSubmit').disabled = busy || !chosen().length || !picked.length;
    byId('afSubmit').hidden = busy || (finished && !picked.length);
    byId('afCancel').hidden = !busy;
    byId('afDone').hidden = busy || !finished || picked.length > 0;
  }
  function addExtra(t) {
    const existing = [...byId('afExtra').querySelectorAll('input')].find(input => input.value === tkey(t));
    if (existing) { existing.checked = true; syncUpload(); return; }
    extras.set(tkey(t), t);
    byId('afNoTarget').hidden = true;
    const label = document.createElement('label'), input = document.createElement('input');
    input.type = 'checkbox'; input.value = tkey(t); input.checked = true; input.onchange = syncUpload;
    label.append(input, ` ${t.kind}: ${t.label}`);
    byId('afExtra').append(label);
    syncUpload();
  }
  // On a flight: this flight, its log (default; files on a log show on every flight of that log), the flights of its
  // aircraft that day, and all flights that day. Elsewhere only the flight / log / paper record itself.
  function scopesFor(data) {
    const t = data.target, word = {flight: 'This flight only', log: 'This log only', paper: 'This paper record only'}[t.kind];
    const found = [];
    if (t.can_add) found.push({value: 'self', text: word, note: t.label, targets: [t]});
    if (data.parent?.can_add) found.push({value: 'log', text: 'All flights of this log', note: data.parent.label,
      targets: [data.parent]});
    if (data.day) found.push({value: 'aircraft-day', text: `All flights of ${data.day.aircraft} on ${data.day.date} (${
      data.day.flights.length})`, targets: data.day.flights});
    if (data.day_all) found.push({value: 'day', text: `All flights on ${data.day_all.date} (${data.day_all.flights.length})`,
      targets: data.day_all.flights});
    return found;
  }
  function syncScope() {
    const scope = scopes.find(s => s.value === byId('afScope').value);
    byId('afScopeNote').textContent = !scope ? 'Add flights / logs with Find more.' : scope.note ||
      scope.targets.map(t => t.label.replace(/ - flight /, ' #')).slice(0, 3).join(' · ') + (scope.targets.length > 3 ? ' · …' : '');
    byId('afScopeNote').title = scope ? scope.targets.map(t => t.label).join('\n') : '';
    syncUpload();
  }
  function openUpload(m, files) {
    dialogs();
    if (busy) { byId('afUpload').showModal(); return; }
    active = m;
    scopes = scopesFor(m.data);
    byId('afScope').replaceChildren(...scopes.map(s => option(s.value, s.text)), option('', 'Only the flights / logs below'));
    byId('afScope').value = scopes.some(s => s.value === 'log') ? 'log' : scopes[0]?.value || '';
    byId('afNoTarget').hidden = scopes.length > 0;
    extras.clear(); byId('afExtra').replaceChildren(); byId('afFindResults').replaceChildren(); byId('afFindQuery').value = '';
    byId('afCategory').replaceChildren(option('', 'Uncategorised'), ...m.categories.map(name => option(name, name)));
    byId('afDescription').value = ''; byId('afFiles').value = ''; byId('afStatus').textContent = '';
    picked = []; finished = false; cancelled = false; setPicked(files || []);
    chosenCategory = false;
    byId('afLimit').textContent = `At most ${size(m.data.max_bytes)} per file. Files upload one at a time: keep this dialog open until they are done.`;
    byId('afMore').open = false;
    syncScope();
    byId('afUpload').showModal();
  }
  function uploadOne(file, selected, {category, description} = {}, progress = byId('afProgress'), statusEl = byId('afStatus')) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/attachments');
      const metadata = encodeURIComponent(JSON.stringify({filename: file.name, description: description || '',
        category: category || null, targets: selected.map(t => ({kind: t.kind, id: t.id}))}));
      if (metadata.length > 7000) { reject(Error('Upload metadata is too long; shorten the description or select fewer entries.')); return; }
      xhr.setRequestHeader('X-Attachment-Metadata', metadata);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) {
          progress.value = 100 * event.loaded / event.total;
          statusEl.textContent = `${file.name}: ${Math.round(progress.value)}% uploaded; saving/compressing...`;
        }
      };
      xhr.onerror = () => reject(Error('Network error. Upload may have completed; refresh the file list before retrying.'));
      xhr.onabort = () => reject(Object.assign(Error('Cancelled.'), {cancelled: true}));
      xhr.onloadend = () => { if (currentXhr === xhr) currentXhr = null; };
      currentXhr = xhr;
      xhr.onload = () => {
        let data;
        try { data = JSON.parse(xhr.responseText); }
        catch (error) { reject(Error(`Server returned HTTP ${xhr.status}; check proxy upload limits/timeouts.`)); return; }
        if (xhr.status >= 200 && xhr.status < 300) resolve(data);
        else reject(Error(data.error || `HTTP ${xhr.status}`));
      };
      xhr.send(file);
    });
  }
  async function uploadAll() {
    const m = active, selected = chosen(), files = picked, status = byId('afStatus');
    if (!selected.length || selected.length > 100) { status.textContent = 'Select between 1 and 100 entries.'; return; }
    if (files.some(file => !file.size || file.size > m.data.max_bytes)) {
      status.textContent = `Files must be nonempty and at most ${size(m.data.max_bytes)} each.`; return;
    }
    // A file with the name of a current one replaces it on that flight/log; the old file stays as an earlier version.
    const current = m.data.files.filter(file => !file.superseded);
    const replaces = files.flatMap(file => current.filter(old => old.filename.toLowerCase() === file.name.toLowerCase()))
      .map(old => `${old.filename} uploaded on ${old.uploaded_at.slice(0, 10)}`);
    if (replaces.length && !confirm(`This replaces:\n${replaces.join('\n')}\n\nThe old file stays available under` +
      ' "Show earlier versions". Identical files are linked, not stored again.')) return;
    busy = true; cancelled = false; syncUpload(); byId('afSelection').disabled = true; byId('afProgress').hidden = false;
    byId('afUploadClose').disabled = true;
    let completed = 0, linked = 0;
    const left = [...files];  // what was not sent yet stays chosen after a cancel or error
    try {
      for (const file of files) {
        if (cancelled) throw Object.assign(Error('Cancelled.'), {cancelled: true});
        byId('afProgress').value = 0; status.textContent = `Uploading ${file.name}...`;
        const meta = {category: byId('afCategory').value, description: byId('afDescription').value};
        if ((await uploadOne(file, selected, meta)).duplicate) linked++; else completed++;
        left.shift();
      }
      byId('afFiles').value = '';
      status.textContent = `${completed} file(s) saved and linked.` +
        (linked ? ` ${linked} identical file(s) were already stored and are now linked.` : '');
    } catch (error) {
      status.textContent = `${completed + linked} file(s) saved. ` + (error.cancelled
        ? `Cancelled: ${left.length} not uploaded; press Upload to send ${left.length === 1 ? 'it' : 'them'}.` : error.message);
    }
    finally {
      busy = false; finished = true; setPicked(left);  // files left (cancel, error) keep Upload available
      syncUpload(); byId('afSelection').disabled = false; byId('afProgress').hidden = true;
      byId('afUploadClose').disabled = false;
      try { await m.reload(); } catch (error) { status.textContent += ` Unable to refresh: ${error.message}`; }
    }
  }

  // ---- Edit dialog: category, description, links (remove), link to more
  function openEdit(m, file) {
    dialogs();
    active = m; editing = file;
    const names = file.category && !m.categories.includes(file.category) ? [...m.categories, file.category] : m.categories;
    byId('afEditName').textContent = file.filename;
    byId('afEditError').textContent = '';
    byId('afEditCategory').replaceChildren(option('', 'Uncategorised'), ...names.map(name =>
      option(name, m.categories.includes(name) ? name : `${name} (no longer listed)`)));
    byId('afEditCategory').value = file.category || '';
    byId('afEditDescription').value = file.description || '';
    byId('afLinks').innerHTML = file.links.map(link => `<li><a href="${esc(link.url)}" title="${esc(link.label)}">${esc(link.label)}</a>${
      link.can_remove ? `<button type="button" data-unlink="${link.link_id}">Remove link</button>` : ''}</li>`).join('');
    for (const button of byId('afLinks').querySelectorAll('[data-unlink]')) button.onclick = async () => {
      if (!confirm('Remove this link? Other links stay intact. If this is the last link, the stored file is deleted.')) return;
      button.disabled = true;
      try {
        await api(`/api/attachments/${file.id}/links/${button.dataset.unlink}`, {method: 'DELETE'});
        byId('afEdit').close();
        await m.reload();
      } catch (error) { byId('afEditError').textContent = error.message; button.disabled = false; }
    };
    byId('afLinkMore').hidden = !file.can_share;  // sharing further needs upload access to every current link
    byId('afLinkQuery').value = ''; byId('afLinkResults').replaceChildren(); byId('afLinkMore').open = false;
    byId('afEdit').showModal();
  }
  async function saveEdit(event) {
    event.preventDefault();
    const body = {description: byId('afEditDescription').value};
    if (byId('afEditCategory').value !== (editing.category || '')) body.category = byId('afEditCategory').value || null;
    try {
      await api(`/api/attachments/${editing.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)});
      byId('afEdit').close();
      await active.reload();
    } catch (error) { byId('afEditError').textContent = error.message; }
  }
  function linkSearch(event) {
    event.preventDefault();
    const file = editing, m = active, linked = new Set(file.links.map(link => tkey(link)));
    findTargets(byId('afLinkQuery').value, byId('afLinkResults'), 'Link', async (t, button) => {
      if (linked.has(tkey(t))) { byId('afEditError').textContent = 'This file is already linked there.'; return; }
      if (!confirm(`Link this file to ${t.kind} ${t.label}? Its project members will be able to download it.`)) return;
      button.disabled = true;
      try {
        await api(`/api/attachments/${file.id}/links`, {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({targets: [{kind: t.kind, id: t.id}]})});
        m.status.textContent = `${file.filename} linked to ${t.kind} ${t.label}. No extra copy was stored.`;
        byId('afEdit').close();
        await m.reload();
      } catch (error) { byId('afEditError').textContent = error.message; button.disabled = false; }
    });
  }

  // The manager: tools (search, category filter, Upload files) go into `toolbar` (default: above the list). With
  // `url` the search and filter are kept in the page URL (files_q, files_category) so Back returns to the same view.
  // `onload(data, count)` runs after every (re)load, e.g. to show the number of files in a heading or tab.
  function manage(element, kind, id, {toolbar, url = true, onload} = {}) {
    const params = new URLSearchParams(location.search);
    const m = {kind, id, data: null, categories: [], status: document.createElement('p'), list: document.createElement('div')};
    const tools = document.createElement('span');
    tools.className = 'files-tools';
    tools.innerHTML = `<input type="search" class="af-search" placeholder="Search files" aria-label="Search files">
      <select class="af-filter" aria-label="Category"><option value="">All categories</option></select>
      <button type="button" class="af-upload" hidden>Upload files</button>`;
    const [search, filter, upload] = tools.children;
    search.value = url ? params.get('files_q') || '' : '';
    m.status.className = 'af-status';
    m.status.setAttribute('role', 'status');
    m.list.textContent = 'Loading...';
    element.replaceChildren(...(toolbar ? [] : [tools]), m.status, m.list);
    if (toolbar) toolbar.replaceChildren(tools);
    const keep = (name, value) => {
      if (!url) return;
      const address = new URL(location);
      value ? address.searchParams.set(name, value) : address.searchParams.delete(name);
      history.replaceState(history.state, '', address);
    };
    const show = () => {
      const files = m.data.files, picked = filter.value;
      const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      const shown = files.filter(file => (!picked || (file.category || UNCATEGORISED) === picked) && words.every(word =>
        `${file.filename} ${file.description || ''}`.toLowerCase().includes(word)));
      list(m.list, shown, m.categories, file => openEdit(m, file));
      if (files.length && !shown.length) m.list.innerHTML = '<p class="mute">No files match.</p>';
      const count = files.filter(file => !file.superseded).length, matches = shown.filter(file => !file.superseded).length;
      if (onload) onload(m.data, count, matches);
    };
    search.oninput = () => { keep('files_q', search.value.trim()); show(); };
    filter.onchange = () => { keep('files_category', filter.value); show(); };
    upload.onclick = () => openUpload(m);
    dialogs();
    dropTarget(element, files => { if (!upload.hidden) openUpload(m, files); });
    m.reload = async () => {
      m.data = await api(`/api/attachments?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`);
      m.categories = m.data.categories || [];
      const wanted = filter.value || (url ? params.get('files_category') : '') || '';
      const extra = [...new Set(m.data.files.map(file => file.category).filter(name => name && !m.categories.includes(name)))].sort();
      filter.replaceChildren(option('', 'All categories'), ...[...m.categories, ...extra].map(name => option(name, name)),
        option(UNCATEGORISED, 'Uncategorised'));
      filter.value = [...filter.options].some(o => o.value === wanted) ? wanted : '';
      const many = m.data.files.length > 1;  // search and filter only help with more than one file
      search.hidden = filter.hidden = !many;
      upload.hidden = !scopesFor(m.data).length;
      show();
      return m.data;
    };
    document.addEventListener('attachmentsuploaded', () => { if (element.isConnected) m.reload().catch(() => {}); });
    m.ready = m.reload().catch(error => { m.list.textContent = `Unable to load files: ${error.message}`; return null; });
    return m;
  }
  // ---- When was a file made? Photos: EXIF capture time (JPEG, HEIC, ...); videos (MP4/MOV): the metadata creation date
  // with its time zone, else the header's creation time, and the duration; else a date and
  // time in the name; else the file's modified time. Times without a zone are taken in the browser's time zone.
  const localIso = (y, mo, d, h, mi, s) => new Date(+y, +mo - 1, +d, +h, +mi, +s).toISOString();
  // EXIF: in a JPEG its APP1 segment; in other formats (HEIC/HEIF from phones, PNG, WebP, most RAW) the block
  // "Exif\0\0" + TIFF header, looked for in the first and last megabyte.
  async function exifTime(file) {
    const view = new DataView(await file.slice(0, 256 * 1024).arrayBuffer());
    if (view.byteLength >= 12 && view.getUint16(0) === 0xFFD8) {
      for (let p = 2; p + 10 < view.byteLength;) {
        if (view.getUint8(p) !== 0xFF) return null;
        const marker = view.getUint8(p + 1), length = view.getUint16(p + 2);
        if (marker === 0xE1 && view.getUint32(p + 4) === 0x45786966) return tiffTime(view, p + 10);
        if (marker === 0xDA) return null;
        p += 2 + length;
      }
      return null;
    }
    if (/\.(mp4|mov|m4v|insv|lrv|3gp|avi|mkv)$/i.test(file.name)) return null;
    const MB = 1 << 20;
    for (const [from, to] of [[0, Math.min(file.size, MB)], [Math.max(MB, file.size - MB), file.size]]) {
      if (to <= from) continue;
      const bytes = new Uint8Array(await file.slice(from, to).arrayBuffer());
      for (let i = 0; i + 14 < bytes.length; i++) {
        if (bytes[i] !== 0x45 || bytes[i + 1] !== 0x78 || bytes[i + 2] !== 0x69 || bytes[i + 3] !== 0x66
            || bytes[i + 4] !== 0 || bytes[i + 5] !== 0) continue;  // "Exif\0\0"
        const tiff = (bytes[i + 6] === 0x4D && bytes[i + 7] === 0x4D && bytes[i + 9] === 0x2A)
          || (bytes[i + 6] === 0x49 && bytes[i + 7] === 0x49 && bytes[i + 8] === 0x2A);
        if (!tiff) continue;
        const found = tiffTime(new DataView(bytes.buffer, bytes.byteOffset), i + 6);
        if (found) return found;
      }
    }
    return null;
  }
  function tiffTime(view, base) {
    try {
      const le = view.getUint16(base) === 0x4949;
      const u16 = at => view.getUint16(base + at, le), u32 = at => view.getUint32(base + at, le);
      const text = (at, count) => { let out = ''; for (let i = 0; i < count; i++) { const c = view.getUint8(base + at + i); if (!c) break; out += String.fromCharCode(c); } return out; };
      const tags = ifd => {
        const found = {};
        for (let i = 0, n = u16(ifd); i < n; i++) {
          const entry = ifd + 2 + i * 12, count = u32(entry + 4);
          found[u16(entry)] = {count, at: count > 4 ? u32(entry + 8) : entry + 8, value: u32(entry + 8)};
        }
        return found;
      };
      const ifd0 = tags(u32(4)), exif = ifd0[0x8769] ? tags(ifd0[0x8769].value) : {};
      const tag = exif[0x9003] || exif[0x9004] || ifd0[0x0132];  // DateTimeOriginal, DateTimeDigitized, DateTime
      const m = tag && text(tag.at, tag.count).match(/^(\d{4}):(\d\d):(\d\d) (\d\d):(\d\d):(\d\d)/);
      if (!m || +m[1] < 2000) return null;
      const zone = exif[0x9011] ? text(exif[0x9011].at, exif[0x9011].count) : '';  // OffsetTimeOriginal, "+01:00"
      const start = /^[+-]\d\d:\d\d$/.test(zone) ? new Date(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}${zone}`).toISOString()
        : localIso(m[1], m[2], m[3], m[4], m[5], m[6]);
      return {start, source: 'photo time'};
    } catch (error) { return null; }
  }
  const latin1 = bytes => { let out = ''; for (let i = 0; i < bytes.length; i += 32768) out += String.fromCharCode(...bytes.subarray(i, i + 32768)); return out; };
  async function videoTime(file) {
    if (!/\.(mp4|mov|m4v|insv|lrv|3gp)$/i.test(file.name)) return null;
    const MB = 1 << 20;
    for (const [from, to] of [[0, Math.min(file.size, 4 * MB)], [Math.max(0, file.size - 16 * MB), file.size]]) {
      const bytes = new Uint8Array(await file.slice(from, to).arrayBuffer());
      for (let i = 4; i + 40 < bytes.length; i++) {
        if (bytes[i] !== 0x6d || bytes[i + 1] !== 0x76 || bytes[i + 2] !== 0x68 || bytes[i + 3] !== 0x64) continue;  // 'mvhd'
        const v = new DataView(bytes.buffer, bytes.byteOffset + i + 4);
        const wide = v.getUint8(0) === 1;
        const created = wide ? Number(v.getBigUint64(4)) : v.getUint32(4);
        const scale = wide ? v.getUint32(20) : v.getUint32(12);
        const length = wide ? Number(v.getBigUint64(24)) : v.getUint32(16);
        let start = (created - 2082844800) * 1000;  // seconds since 1904, UTC
        if (!created || !scale || start < Date.UTC(2000, 0, 1)) return null;
        const duration = length / scale * 1000;
        // A creation date with a time zone in the metadata (com.apple.quicktime.creationdate, ©day) is the most
        // reliable; it sits in the same 'moov' box as 'mvhd', so near it.
        const near = latin1(bytes.subarray(Math.max(0, i - 4 * 1024 * 1024), Math.min(bytes.length, i + 4 * 1024 * 1024)));
        const tagged = (near.includes('com.apple.quicktime.creationdate') || near.includes('\u00a9day'))
          && near.match(/(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d)/);
        if (tagged) {
          const when = Date.parse(tagged[1] + (tagged[2] === 'Z' ? 'Z' : tagged[2].replace(/^([+-]\d\d)(\d\d)$/, '$1:$2')));
          if (!isNaN(when)) return {start: new Date(when).toISOString(), end: new Date(when + duration).toISOString(),
            source: 'video time'};
        }
        // Otherwise the header time. Some cameras write local time as if it were UTC: the file's modified time (the
        // end of recording) then differs from the end by the time zone offset.
        const shift = new Date(start).getTimezoneOffset() * 60000;
        if (shift && Math.abs(file.lastModified - (start + duration + shift)) < 120000) start += shift;
        return {start: new Date(start).toISOString(), end: new Date(start + duration).toISOString(), source: 'video time'};
      }
    }
    return null;
  }
  function nameTime(name) {
    const m = name.match(/(20\d\d)[-_.]?(\d\d)[-_.]?(\d\d)[ T_-]{0,2}(\d\d)[-_.:h]?(\d\d)[-_.:m]?(\d\d)/)
      || name.match(/(?:^|\D)(\d\d)_(\d\d)_(\d\d)__(\d\d)_(\d\d)_(\d\d)/);
    if (!m) return null;
    const year = m[1].length === 2 ? '20' + m[1] : m[1];
    if (+m[2] > 12 || +m[3] > 31 || +m[4] > 23 || +m[5] > 59 || +m[6] > 59) return null;
    return {start: localIso(year, m[2], m[3], m[4], m[5], m[6]), source: 'time in the name'};
  }
  async function fileTime(file) {
    try {
      return await exifTime(file) || await videoTime(file) || nameTime(file.name)
        || {start: new Date(file.lastModified).toISOString(), source: 'file date'};
    } catch (error) { return {start: new Date(file.lastModified).toISOString(), source: 'file date'}; }
  }

  // ---- Review: files dropped on the general Upload (not logs) are matched to flights by the time they were made.
  // Each row proposes a target (overlapping flights, the flight, all flights that day, or nothing) to check first.
  let reviewing = [];
  function reviewDialog() {
    if (byId('afReview')) return;
    document.body.insertAdjacentHTML('beforeend', `
<dialog class="entry-dialog af-dialog af-wide" id="afReview" aria-labelledby="afReviewTitle">
  <div class="dialog-header"><h2 id="afReviewTitle">Upload files</h2><button type="button" id="afReviewClose" aria-label="Close Upload files">Close</button></div>
  <p class="mute af-note">Matched to your flights by the time each file was made. Check the flight per file; nothing is
    uploaded before you press Upload.</p>
  <div id="afReviewList" class="af-review"></div>
  <label class="field">Description (optional, for all these files)<textarea id="afReviewDescription" maxlength="1000" rows="1"></textarea></label>
  <progress id="afReviewProgress" hidden max="100" value="0" aria-label="File upload progress"></progress>
  <p id="afReviewStatus" class="af-status" role="status" aria-live="polite"></p>
  <div class="af-actions"><details class="af-help"><summary class="mute">Help</summary>
    <p>The time comes from the photo (EXIF) or video (start and length), else from a date and time in the file name, else
      from the file's date, which copying can change. Times without a time zone are taken in this browser's time zone.
      A file matches the flight it overlaps, else the nearest flight up to 1 hour before takeoff or after landing; a video
      over several flights goes to all of them. Only flights you may add files to are offered.</p>
    <p><strong>Sharing:</strong> everyone with access to any linked flight / log can download the file.</p></details>
    <span class="af-buttons"><button type="button" id="afReviewCancel" hidden>Cancel upload</button>
      <button type="button" id="afReviewUpload" disabled>Upload</button><button type="button" id="afReviewDone" hidden>Done</button></span></div>
</dialog>`);
    byId('afReviewClose').onclick = () => { if (!busy) byId('afReview').close(); };
    byId('afReview').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    byId('afReviewUpload').onclick = uploadReviewed;
    byId('afReviewCancel').onclick = cancelUpload;
    byId('afReviewDone').onclick = () => byId('afReview').close();
  }
  const when = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}); };
  // Short names for the review's choices: per aircraft its takeoff times in local time, then the flight numbers,
  // e.g. "Nederdrone 15:05 + 15:30 (#11, #12)"; more than three times: "+ 2 more".
  const hm = iso => { const d = new Date(iso || ''); return isNaN(d) ? '?' : d.toLocaleTimeString(undefined, {hour: '2-digit', minute: '2-digit'}); };
  function flightsText(flights) {
    const byAircraft = new Map();
    for (const f of flights) {
      const name = f.aircraft || 'Flight';
      if (!byAircraft.has(name)) byAircraft.set(name, []);
      byAircraft.get(name).push(hm(f.takeoff_utc));
    }
    const parts = [...byAircraft].map(([name, times]) =>
      `${name} ${times.slice(0, 3).join(' + ')}${times.length > 3 ? ` + ${times.length - 3} more` : ''}`);
    const numbers = flights.slice(0, 3).map(f => '#' + f.id).join(', ') + (flights.length > 3 ? ', …' : '');
    return `${parts.join(', ')} (${numbers})`;
  }
  const dayText = date => new Date(date + 'T12:00:00Z').toLocaleDateString(undefined, {day: 'numeric', month: 'short'});
  async function review(files) {
    reviewDialog();
    const status = byId('afReviewStatus'), list = byId('afReviewList');
    byId('afReviewTitle').textContent = `Upload ${files.length} file${files.length === 1 ? '' : 's'}`;
    byId('afReviewDescription').value = ''; byId('afReviewUpload').disabled = true; status.textContent = '';
    finished = false; cancelled = false;
    list.innerHTML = '<p class="mute">Reading when the files were made…</p>';
    if (!byId('afReview').open) byId('afReview').showModal();
    const times = [];
    for (const file of files) times.push(await fileTime(file));
    let data;
    try {
      data = await api('/api/attachments/match', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({times: times.map(t => t.end ? {start: t.start, end: t.end} : t.start)})});
    } catch (error) { list.innerHTML = `<p class="dialog-error">${esc(error.message)}</p>`; return; }
    const categories = data.categories || [];
    reviewing = files.map((file, i) => {
      const match = data.matches[i] || {}, options = [];
      if (match.flights) options.push({value: 'flights', text: flightsText(match.flights), targets: match.flights});
      if (match.flight) options.push({value: 'flight', text: flightsText([match.flight]), targets: [match.flight]});
      if (match.day) options.push({value: 'day', text: `All ${match.day.flights.length} flight${match.day.flights.length === 1 ? '' : 's'} on ${
        dayText(match.day.date)}`, targets: match.day.flights});
      options.push({value: '', text: match.day ? 'Do not upload' : 'Not uploaded (no flight found)', targets: []});
      const guess = guessFor(file.name), category = guess && categories.find(name => name.toLowerCase() === guess.toLowerCase());
      return {file, time: times[i], match, options, category: category || '', tooBig: !file.size || file.size > data.max_bytes};
    });
    const how = {during: 'during the flight', overlaps: 'overlaps the flight', 'before takeoff': 'before takeoff', 'after landing': 'after landing'};
    list.innerHTML = reviewing.map((row, i) => `<div class="af-row" data-row="${i}">
      <div class="af-row-info"><div class="af-row-name" title="${esc(row.file.name)}">${esc(row.file.name)}</div>
        <div class="mute af-row-time" title="${esc(row.time.source)}">${esc(when(row.time.start))}${row.time.end ? ' – ' + esc(new Date(row.time.end).toLocaleTimeString(undefined, {timeStyle: 'short'})) : ''} · ${
          esc(row.time.source)}${row.match.how ? ' · ' + esc(how[row.match.how]) : ''}${row.tooBig ? ' · <strong>too large or empty</strong>' : ''}</div></div>
      <div class="af-row-pick"><select data-target="${i}" aria-label="Upload ${esc(row.file.name)} to">${row.options.map(o =>
        `<option value="${o.value}" title="${esc(o.targets.map(t => t.label).join('\n'))}">${esc(o.text)}</option>`).join('')}</select>
        <select data-category="${i}" aria-label="Category of ${esc(row.file.name)}"><option value="">Uncategorised</option>${
        categories.map(name => `<option${name === row.category ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
      <button type="button" class="af-x" data-remove="${i}" aria-label="Remove ${esc(row.file.name)}" title="Remove">×</button>
    </div>`).join('');
    for (const select of list.querySelectorAll('[data-target]')) {
      const row = reviewing[+select.dataset.target];
      select.value = row.tooBig ? '' : row.options[0].value;  // the closest match first; no match: not uploaded
      select.disabled = row.tooBig;
      select.onchange = syncReview;
    }
    for (const button of list.querySelectorAll('[data-remove]')) button.onclick = () => {
      if (busy) return;
      button.closest('.af-row').remove();
      syncReview();
    };
    syncReview();
  }
  function syncReview() {
    const rows = byId('afReviewList').querySelectorAll('.af-row:not(.af-done)');
    const count = [...rows].filter(row => row.querySelector('[data-target]').value).length;
    byId('afReviewTitle').textContent = rows.length ? `Upload ${rows.length} file${rows.length === 1 ? '' : 's'}`
      : finished ? 'Files uploaded' : 'Upload files';
    if (!byId('afReviewList').querySelector('.af-row')) byId('afReviewList').innerHTML = '<p class="mute">No files left.</p>';
    byId('afReviewUpload').disabled = busy || !count;
    byId('afReviewUpload').hidden = busy || (finished && !count);
    byId('afReviewCancel').hidden = !busy;
    byId('afReviewDone').hidden = busy || !finished || count > 0;
    byId('afReviewUpload').textContent = count ? `Upload ${count} file${count === 1 ? '' : 's'}` : 'Upload';
  }
  async function uploadReviewed() {
    const status = byId('afReviewStatus'), progress = byId('afReviewProgress'), list = byId('afReviewList');
    const description = byId('afReviewDescription').value;
    const jobs = [...list.querySelectorAll('.af-row:not(.af-done)')].map(card => ({card, row: reviewing[+card.dataset.row],
      pick: card.querySelector('[data-target]'), category: card.querySelector('[data-category]').value})).filter(job => job.pick.value);
    busy = true; cancelled = false; syncReview(); byId('afReviewClose').disabled = true; progress.hidden = false;
    let completed = 0, linked = 0, failed = 0, stopped = 0;
    for (const job of jobs) {
      if (cancelled) { stopped++; continue; }
      const targets = job.row.options.find(o => o.value === job.pick.value).targets;
      progress.value = 0; status.textContent = `Uploading ${job.row.file.name}…`;
      try {
        if ((await uploadOne(job.row.file, targets, {category: job.category, description}, progress, status)).duplicate) linked++;
        else completed++;
        job.card.classList.add('af-done'); job.pick.disabled = true;
        job.card.querySelector('[data-category]').disabled = true;
      } catch (error) {
        if (error.cancelled) stopped++;
        else { failed++; job.card.querySelector('.af-row-time').textContent += ` · failed: ${error.message}`; }
      }
    }
    busy = false; finished = true; byId('afReviewClose').disabled = false; progress.hidden = true; syncReview();
    status.textContent = `${completed} file(s) saved and linked.` + (linked ? ` ${linked} identical file(s) were already stored and are now linked.` : '')
      + (failed ? ` ${failed} failed.` : '') + (stopped ? ` Cancelled: ${stopped} not uploaded; press Upload to send ${stopped === 1 ? 'it' : 'them'}.` : '');
    document.dispatchEvent(new CustomEvent('attachmentsuploaded'));
  }

  window.AttachmentFiles = {api, size, list, manage, groups, esc, summary, review};
})();
