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
  function render(element, files, reload, onshare) {
    element.innerHTML = files.length ? files.map(file => `<div style="padding:12px 0;border-bottom:1px solid var(--line)">
      <a href="/api/attachments/${file.id}/download" style="overflow-wrap:anywhere">${esc(file.filename)}</a>
      ${file.viewable ? `<a href="/api/attachments/${file.id}/view" target="_blank" rel="noopener"
        title="View in a new tab" aria-label="View ${esc(file.filename)} in a new tab" style="text-decoration:none">&#128065;</a>` : ''}
      <span class="mute">${size(file.size_bytes)}${file.compressed ?
        ` · stored ${size(file.stored_bytes)} (${Math.round(100 * (1 - file.stored_bytes / file.size_bytes))}% saved)` : ' · original stored'}</span>
      ${file.description ? `<p style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(file.description)}</p>` : ''}
      <details style="margin-top:6px"><summary>Linked flights / logs</summary>
        <ul>${file.links.map(link => `<li style="margin:6px 0"><a href="${esc(link.url)}">${esc(link.label)}</a>
          ${link.can_edit && onshare ? `<button type="button" data-file="${file.id}" data-unlink="${link.link_id}">Remove link</button>` : ''}</li>`).join('')}</ul>
        <small class="mute">Only links you can access are shown. Removing the last link deletes the stored file.</small>
      </details>
      ${file.can_share && onshare ? `<button type="button" data-share="${file.id}" style="margin-top:8px">Link to selected flights / logs</button>` : ''}
    </div>`).join('') : '<p class="mute">No additional files.</p>';
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
  async function mount(element, kind, id) {
    try {
      const data = await api(`/api/attachments?kind=${encodeURIComponent(kind)}&id=${id}`);
      element.replaceChildren();
      const link = document.createElement('a');
      link.href = `/attachments?kind=${encodeURIComponent(kind)}&id=${id}`;
      link.textContent = data.target.can_edit ? 'Add / manage files' : 'Open files';
      element.append(link);
      const list = document.createElement('div');
      element.append(list);
      render(list, data.files);
    } catch (error) { element.textContent = `Unable to load files: ${error.message}`; }
  }
  window.AttachmentFiles = {api, size, render, mount};
})();
