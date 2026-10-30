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
  function versions(files, show) {
    const current = files.filter(file => !file.superseded), earlier = files.filter(file => file.superseded);
    return show(current) + (earlier.length ? `<details class="earlier-versions"><summary class="mute">Show earlier versions (${
      earlier.length})</summary>${show(earlier)}</details>` : '');
  }
  // Compact read-only list (flight / paper record pages): one row per file; links and storage on hover.
  function list(element, files) {
    element.innerHTML = files.length ? versions(files, rows => rows.length ? `<table class="file-list"><tbody>${rows.map(file => {
      const others = file.links.map(l => l.label).join('\n');
      return `<tr><td class="file-name">${link(file, '')}${file.description
        ? `<div class="mute file-desc" title="${esc(file.description)}">${esc(file.description)}</div>` : ''}</td>
        <td class="mute" title="${esc(stored(file))}">${size(file.size_bytes)}</td>
        <td class="mute" title="${esc(others)}">${file.links.length === 1 ? '1 link' : file.links.length + ' links'}</td>${
        file.superseded ? `<td class="mute">${replaced(file)}</td>` : ''}</tr>`;
    }).join('')}</tbody></table>` : '') : '<p class="mute">No additional files.</p>';
  }
  function render(element, files, reload, onshare) {
    element.innerHTML = files.length ? versions(files, rows => rows.map(file => `<div style="padding:12px 0;border-bottom:1px solid var(--line)">
      ${link(file, 'style="overflow-wrap:anywhere"')}${file.superseded ? ` <span class="mute">${replaced(file)} ·</span>` : ''}
      <span class="mute">${size(file.size_bytes)}${file.compressed ?
        ` · stored ${size(file.stored_bytes)} (${Math.round(100 * (1 - file.stored_bytes / file.size_bytes))}% saved)` : ' · original stored'}</span>
      ${file.description ? `<p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(file.description)}</p>` : ''}
      <details style="margin-top:6px"><summary>Linked flights / logs</summary>
        <ul>${file.links.map(link => `<li style="margin:6px 0"><a href="${esc(link.url)}">${esc(link.label)}</a>
          ${link.can_edit && onshare ? `<button type="button" data-file="${file.id}" data-unlink="${link.link_id}">Remove link</button>` : ''}</li>`).join('')}</ul>
        <small class="mute">Only links you can access are shown. Removing the last link deletes the stored file.</small>
      </details>
      ${file.can_share && onshare ? `<button type="button" data-share="${file.id}" style="margin-top:8px">Link to selected flights / logs</button>` : ''}
    </div>`).join('')) : '<p class="mute">No additional files.</p>';
    for (const button of element.querySelectorAll('[data-unlink]')) button.onclick = async () => {
      if (!confirm('Remove this link? Other links stay intact. If this is the last link, the stored file is deleted.')) return;
      button.disabled = true;
      try {
        await api(`/api/attachments/${button.dataset.file}/links/${button.dataset.unlink}`, {method:'DELETE'});
        await reload();
      } catch (error) { alert(error.message); button.disabled = false; }
    };
    for (const button of element.querySelectorAll('[data-share]')) button.onclick = () => onshare(+button.dataset.share);
  }
  // Returns the loaded payload so the page can show a count and, in Edit mode, a manage link.
  async function mount(element, kind, id) {
    try {
      const data = await api(`/api/attachments?kind=${encodeURIComponent(kind)}&id=${id}`);
      list(element, data.files);
      return data;
    } catch (error) { element.textContent = `Unable to load files: ${error.message}`; return null; }
  }
  window.AttachmentFiles = {api, size, render, list, mount};
})();
