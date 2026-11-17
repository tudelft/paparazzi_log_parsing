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
  const link = (file, attrs) => fileLink({name: esc(file.filename), attrs, viewable: file.viewable,
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
  <fieldset id="afSelection">
    <label class="field" for="afScope">Upload to</label>
    <select id="afScope"></select>
    <p class="mute af-note" id="afScopeNote"></p>
    <div id="afExtra"></div>
    <p class="mute af-note" id="afNoTarget" hidden>You have read-only access here. Find a flight / log where you have full access.</p>
    <form id="afFind" class="search-row"><input id="afFindQuery" type="search" required aria-label="Find more flights / logs"
      placeholder="Find more: aircraft, 2026-10-21, log filename or ID"><button type="submit">Search</button></form>
    <div id="afFindResults" class="af-results"></div>
  </fieldset>
  <form id="afUploadForm">
    <fieldset id="afUploadFields" disabled>
      <label class="field">Files<input id="afFiles" type="file" multiple required></label>
      <label class="field">Category<select id="afCategory"></select></label>
      <p class="mute af-note" id="afGuess" hidden>Guessed from the file name; change it if it does not fit.</p>
      <label class="field">Description (optional, applies to these files)<textarea id="afDescription" maxlength="1000" rows="2"></textarea></label>
      <p class="mute af-note" id="afLimit"></p>
      <button type="submit">Upload</button>
    </fieldset>
  </form>
  <progress id="afProgress" hidden max="100" value="0" aria-label="File upload progress"></progress>
  <p id="afStatus" class="af-status" role="status" aria-live="polite"></p>
</dialog>
<dialog class="entry-dialog af-dialog" id="afEdit" aria-labelledby="afEditTitle">
  <div class="dialog-header"><h2 id="afEditTitle">Edit file</h2><button type="button" id="afEditClose" aria-label="Close Edit file">Close</button></div>
  <p class="mute af-note" id="afEditName"></p>
  <p class="dialog-error" id="afEditError" role="alert"></p>
  <form id="afEditForm">
    <label class="field">Category<select id="afEditCategory"></select></label>
    <label class="field">Description<textarea id="afEditDescription" maxlength="1000" rows="3"></textarea></label>
    <p class="mute af-note">The file is shared: this changes it on every flight / log it is linked to.</p>
    <button type="submit">Save</button>
  </form>
  <h3 class="af-sub">Linked flights / logs</h3>
  <ul id="afLinks" class="af-links"></ul>
  <p class="mute af-note">Only links you can access are shown. Removing the last link deletes the stored file.</p>
  <div id="afLinkMore">
    <h3 class="af-sub">Link to more flights / logs</h3>
    <form id="afLinkFind" class="search-row"><input id="afLinkQuery" type="search" required aria-label="Find flights / logs to link"
      placeholder="Aircraft, 2026-10-21, log filename or ID"><button type="submit">Search</button></form>
    <div id="afLinkResults" class="af-results"></div>
  </div>
</dialog>`);
    byId('afUploadClose').onclick = () => { if (!busy) byId('afUpload').close(); };
    byId('afUpload').addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    byId('afEditClose').onclick = () => byId('afEdit').close();
    byId('afScope').onchange = syncScope;
    byId('afFind').onsubmit = event => {
      event.preventDefault();
      findTargets(byId('afFindQuery').value, byId('afFindResults'), 'Add', t => addExtra(t));
    };
    byId('afUploadForm').onsubmit = uploadAll;
    byId('afCategory').onchange = () => { chosenCategory = true; byId('afGuess').hidden = true; };
    byId('afFiles').onchange = guessCategory;
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
  let chosenCategory = false;
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
    const names = [...byId('afFiles').files].map(file => file.name);
    const guesses = new Set(names.map(guessFor));
    const guess = guesses.size === 1 ? [...guesses][0] : null;
    const listed = guess && active.categories.find(name => name.toLowerCase() === guess.toLowerCase());
    byId('afCategory').value = listed || '';
    byId('afGuess').hidden = !listed;
  }
  function chosen() {
    const scope = scopes.find(s => s.value === byId('afScope').value);
    const all = [...(scope ? scope.targets : []),
      ...[...byId('afExtra').querySelectorAll('input:checked')].map(input => extras.get(input.value))];
    return [...new Map(all.map(t => [tkey(t), t])).values()];
  }
  function syncUpload() { byId('afUploadFields').disabled = busy || !chosen().length; }
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
    if (t.can_edit) found.push({value: 'self', text: word, note: t.label, targets: [t]});
    if (data.parent?.can_edit) found.push({value: 'log', text: 'All flights of this log', note: data.parent.label,
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
  function openUpload(m) {
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
    chosenCategory = false; byId('afGuess').hidden = true;
    byId('afLimit').textContent = `Maximum ${size(m.data.max_bytes)} per file. Files upload one at a time; keep this dialog open until they are done.`;
    syncScope();
    byId('afUpload').showModal();
  }
  function uploadOne(file, selected) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/attachments');
      const metadata = encodeURIComponent(JSON.stringify({filename: file.name, description: byId('afDescription').value,
        category: byId('afCategory').value || null, targets: selected.map(t => ({kind: t.kind, id: t.id}))}));
      if (metadata.length > 7000) { reject(Error('Upload metadata is too long; shorten the description or select fewer entries.')); return; }
      xhr.setRequestHeader('X-Attachment-Metadata', metadata);
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = event => {
        if (event.lengthComputable) {
          byId('afProgress').value = 100 * event.loaded / event.total;
          byId('afStatus').textContent = `${file.name}: ${Math.round(byId('afProgress').value)}% uploaded; saving/compressing...`;
        }
      };
      xhr.onerror = () => reject(Error('Network error. Upload may have completed; refresh the file list before retrying.'));
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
  async function uploadAll(event) {
    event.preventDefault();
    const m = active, selected = chosen(), files = [...byId('afFiles').files], status = byId('afStatus');
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
    busy = true; syncUpload(); byId('afSelection').disabled = true; byId('afProgress').hidden = false;
    byId('afUploadClose').disabled = true;
    let completed = 0, linked = 0;
    try {
      for (const file of files) {
        byId('afProgress').value = 0; status.textContent = `Uploading ${file.name}...`;
        if ((await uploadOne(file, selected)).duplicate) linked++; else completed++;
      }
      byId('afFiles').value = '';
      status.textContent = `${completed} file(s) saved and linked.` +
        (linked ? ` ${linked} identical file(s) were already stored and are now linked.` : '');
    } catch (error) { status.textContent = `${completed} file(s) saved. ${error.message}`; }
    finally {
      busy = false; syncUpload(); byId('afSelection').disabled = false; byId('afProgress').hidden = true;
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
      link.can_edit ? `<button type="button" data-unlink="${link.link_id}">Remove link</button>` : ''}</li>`).join('');
    for (const button of byId('afLinks').querySelectorAll('[data-unlink]')) button.onclick = async () => {
      if (!confirm('Remove this link? Other links stay intact. If this is the last link, the stored file is deleted.')) return;
      button.disabled = true;
      try {
        await api(`/api/attachments/${file.id}/links/${button.dataset.unlink}`, {method: 'DELETE'});
        byId('afEdit').close();
        await m.reload();
      } catch (error) { byId('afEditError').textContent = error.message; button.disabled = false; }
    };
    byId('afLinkMore').hidden = !file.can_share;  // sharing further needs full access to every current link
    byId('afLinkQuery').value = ''; byId('afLinkResults').replaceChildren();
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
    m.ready = m.reload().catch(error => { m.list.textContent = `Unable to load files: ${error.message}`; return null; });
    return m;
  }
  window.AttachmentFiles = {api, size, list, manage, groups, esc};
})();
