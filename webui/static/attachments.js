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
  // Returns the loaded payload so the page can show a count and, in Edit mode, a manage link.
  async function mount(element, kind, id) {
    try {
      const data = await api(`/api/attachments?kind=${encodeURIComponent(kind)}&id=${id}`);
      list(element, data.files, data.categories);
      return data;
    } catch (error) { element.textContent = `Unable to load files: ${error.message}`; return null; }
  }
  window.AttachmentFiles = {api, size, list, mount, groups, esc};
})();
