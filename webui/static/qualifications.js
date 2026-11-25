/* Private qualification management shared by My account and the admin People page. */
window.QualificationManager = class {
  constructor(element, userId, admin, changed = () => {}) {
    this.element = element;
    this.userId = userId;
    this.admin = admin;
    this.changed = changed;
    this.editing = true;
  }
  // "Show older" is kept in the URL (qual_older=1) so Back returns to the same view.
  showOlder(on) {
    const p = new URLSearchParams(location.search);
    if (on === undefined) return p.get('qual_older') === '1';
    if (on) p.set('qual_older', '1'); else p.delete('qual_older');
    history.replaceState(history.state, '', location.pathname + (p.toString() ? '?' + p : '') + location.hash);
    return on;
  }
  esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  async request(url, options) {
    const r = await fetch(url, options);
    if (r.status === 401) { location.href = '/login'; throw new Error('Please sign in again.'); }
    const data = await r.json().catch(() => { throw new Error(`Invalid server response (${r.status}). Reload before retrying.`); });
    if (!r.ok) throw Object.assign(new Error(data.error || 'Qualification request failed.'), {status: r.status});
    return data;
  }
  async load() {
    try {
      this.data = await this.request(`/api/users/${this.userId}/qualifications`);
      this.render();
    } catch (error) { this.element.textContent = error.message; }
  }
  // What the QR code on the evidence says (administrators only; read by the server, see licence_qr.py). A link of the
  // issuing authority is offered, never opened automatically.
  qrLine(item) {
    const q = item.qr;
    if (!q) return '';
    const esc = s => this.esc(s);
    const who = q.authority;
    const label = {pending: 'QR code not read yet (Check QR codes… under Qualification types)',
      match: 'QR code matches', differs: 'QR code differs', none: 'No QR code found',
      link: `Verification link: ${who ? who.name : ''}`, generic: `QR code links to the ${who ? who.name : ''} website, not to this licence: look the number up there`,
      read: 'QR code with an unknown link or text'}[q.status];
    const tone = q.status === 'differs' ? 'color:var(--err)' : ['match', 'link'].includes(q.status) ? 'color:var(--ok)'
      : q.status === 'none' || q.status === 'generic' ? 'color:var(--warn, #9a6700)' : 'color:var(--mute)';
    const open = (u, text) => ` · <a href="${esc(u)}" target="_blank" rel="noopener noreferrer" title="Opens the issuing authority's page: ${esc(u)}">${text}</a>`;
    const links = who ? open(who.link, who.per_certificate ? 'check this licence' : 'website')
      : (q.links || []).map(u => { let host = u; try { host = new URL(u).host; } catch (e) {} return open(u, 'open ' + esc(host)); }).join('');
    const detail = q.codes && q.codes.length ? `<details style="display:inline"><summary class="mute" style="display:inline;cursor:pointer"> content</summary>
      <pre style="white-space:pre-wrap;font-size:12px;margin:4px 0">${esc(q.codes.join('\n\n'))}</pre></details>` : '';
    return `<div style="font-size:12px"><span style="${tone}" title="Read ${esc((q.checked_at || '').replace('T', ' ').replace('Z', ' UTC'))}">${label}</span>`
      + (q.issues || []).map(i => ` · <span style="color:var(--err)">${esc(i)}</span>`).join('')
      + (q.status === 'match' && q.number ? ` · ${esc(q.number)}` : '') + links + (detail ? ' ·' + detail : '') + '</div>';
  }
  dateBits(item) {
    const bits = [];
    if (item.number) bits.push(`Number ${item.number}`);
    if (item.valid_from) bits.push(`Valid from ${item.valid_from}`);
    if (item.valid_until) bits.push(`Valid until ${item.valid_until}`);
    return bits;
  }
  when(value) {
    return String(value || '').replace('T', ' ').replace(/Z$/, ' UTC');
  }
  metaTip(item) {
    const lines = [`Submitted ${this.when(item.submitted_at)}`];
    if (item.reviewed_at) {
      const verb = item.status === 'withdrawn' ? 'Withdrawn'
        : (['approved','superseded'].includes(item.status) ? 'Approved' : 'Reviewed');
      lines.push(`${verb} by ${item.reviewer || 'former user'} ${this.when(item.reviewed_at)}`);
    }
    return lines;
  }
  // One line per qualification: name (opens the evidence), number and end date in muted text, a status chip and the
  // actions; valid from, file name, submission and QR details in tooltips (the QR details also unfold on a click).
  qrChip(item) {
    const q = item.qr;
    // nothing to say about a missing or unread code on a certificate that carries no number (e.g. first aid)
    if (!q || (['none', 'pending'].includes(q.status) && !item.requires_number && !item.number)) return '';
    const text = {pending: 'QR not read yet', match: 'QR matches', differs: 'QR differs', none: 'No QR code',
      link: 'QR link', generic: 'QR: website', read: 'QR read'}[q.status] || 'QR';
    const tone = q.status === 'differs' ? 'bad' : ['match', 'link'].includes(q.status) ? 'ok'
      : ['none', 'generic'].includes(q.status) ? 'warn' : '';
    const tip = [text, ...(q.issues || [])].join(' · ') + ' (click for details)';
    return `<button type="button" class="qual-chip qr ${tone}" data-qr="${item.id}" title="${this.esc(tip)}">${this.esc(text)}</button>`;
  }
  render() {
    const q = this.data, esc = v => this.esc(v);
    const statusChip = item => {
      const label = item.status === 'superseded' ? 'archived' : item.status === 'approved' && item.expired ? 'expired' : item.status;
      const tone = {approved: 'ok', pending: 'warn', rejected: 'bad', expired: 'bad'}[label] || '';
      return `<span class="qual-chip ${tone}"${item.review_note ? ` title="${esc(item.review_note)}"` : ''}>${esc(label)}</span>`;
    };
    const claim = item => {
      const dates = this.dateBits(item);
      const tip = [...dates, item.has_evidence ? `File: ${item.filename || 'evidence'}` : '', ...this.metaTip(item),
        item.review_note ? `Reason: ${item.review_note}` : '', item.internal ? 'Internal' : ''].filter(Boolean).join('\n');
      const name = `<strong>${esc(item.category)}</strong>`;
      const meta = [item.number || (item.requires_number && ['pending','approved'].includes(item.status) ? 'number missing' : ''),
        item.valid_until ? `until ${item.valid_until}` : ''].filter(Boolean).join(' · ');
      const note = !item.has_evidence && item.note ? (item.training_id
        ? `<a class="mute" href="/training?id=${item.training_id}${this.admin && item.training_user_id ? `&pass_user=${item.training_user_id}` : ''}">${esc(item.note)}</a>`
        : esc(item.note)) : '';
      // Review actions: Approve and Reject for a pending one; the rest in a small menu.
      const pending = this.editing && item.status === 'pending';
      const more = [];
      if (pending) more.push(`<button type="button" data-review="withdraw" data-id="${item.id}">Withdraw</button>`);
      if (this.admin && this.editing) {
        more.push(`<button type="button" data-dates="${item.id}">Edit number and dates</button>`);
        if (item.has_evidence) more.push(`<button type="button" class="danger" data-delete="evidence" data-id="${item.id}">Delete evidence</button>`);
        more.push(`<button type="button" class="danger" data-delete="record" data-id="${item.id}">Delete record</button>`);
      }
      const actions = (pending && this.admin ? `<button type="button" class="small" data-review="approve" data-id="${item.id}">Approve</button>
          <button type="button" class="small sec" data-review="reject" data-id="${item.id}">Reject</button>` : '')
        + (more.length === 1 && !this.admin ? more[0].replace('type="button"', 'type="button" class="small sec"')
          : more.length ? `<details class="qual-more"><summary class="small sec" aria-label="More actions" title="More actions">⋯</summary>
            <div class="qual-menu">${more.join('')}</div></details>` : '');
      const search = [item.category, item.number || '', item.filename || ''].join(' ').toLowerCase();
      return `<div class="qual-row" data-row data-search="${esc(search)}" title="${esc(tip)}">
        <span class="qual-name">${item.has_evidence ? fileLink({name, viewable: item.viewable, filename: item.filename || '',
          view: `/api/qualification-submissions/${item.id}/evidence/view`, download: `/api/qualification-submissions/${item.id}/evidence`}) : name}</span>
        <span class="qual-meta mute">${esc(meta)}${note ? (meta ? ' · ' : '') + note : ''}${!item.has_evidence && !note && !meta ? 'no evidence' : ''}</span>
        ${this.qrChip(item)}${statusChip(item)}
        <span class="qual-actions">${actions}</span>
        ${item.qr ? `<div class="qual-qr" data-qr-detail="${item.id}" hidden>${this.qrLine(item)}</div>` : ''}</div>`;
    };
    const old = i => i.status === 'superseded' || (i.status === 'approved' && i.expired) || ['rejected','withdrawn'].includes(i.status);
    const current = q.items.filter(i => !old(i));
    const older = q.items.filter(old)
      .sort((a, b) => String(b.valid_until || b.submitted_at || '').localeCompare(String(a.valid_until || a.submitted_at || '')));
    const types = q.types.filter(t => t.active);
    const forOther = this.admin && this.editing && this.name;
    const uploadTitle = forOther ? `Add qualification for ${this.name}` : 'Add qualification';
    const showOld = this.showOlder();
    const canAdd = this.editing && types.length;
    this.element.innerHTML = `<div data-error role="alert" style="color:var(--err);overflow-wrap:anywhere"></div>
      <div class="qual-head"><h3>${this.title || 'Qualifications'} <span class="count">${current.length}</span></h3>
        ${q.items.length > 6 ? `<input type="search" data-qual-search placeholder="Search" aria-label="Search qualifications" value="${esc(this.query || '')}">` : ''}
        <span class="qual-head-fill"></span>
        ${canAdd ? `<button type="button" data-open-upload>Add qualification</button>` : ''}
        <details class="qual-help"><summary>Help</summary><p>Current approvals and pending submissions come first;
        expired, earlier, rejected and withdrawn ones are under Show older, newest first, so past flights can be checked.
        Only the linked person and administrators can see these records and files.
        Hover over a qualification for its dates, file and review; click its name to open the evidence.</p></details></div>
      <div class="qual-list" data-list>${window.inTypeGroups(current,
        (name, list) => `<div class="qual-group-head" data-group-head>${esc(name)} <span class="count">${list.length}</span></div>`,
        list => list.map(claim).join('')) || '<p class="mute">None recorded.</p>'}</div>
      ${older.length ? `<button type="button" class="link-button" data-older>${showOld ? 'Hide' : 'Show'} ${older.length} older</button>
        <div class="qual-list" data-list data-older-list${showOld ? '' : ' hidden'}>${older.map(claim).join('')}</div>` : ''}
      ${this.editing && !types.length ? '<p class="mute">An admin must first add a qualification type on People.</p>' : ''}
      ${!this.editing ? '<p class="mute">Turn on Edit to upload, review or delete qualifications for this person.</p>' : ''}
      ${canAdd ? `<dialog class="entry-dialog" data-upload-dialog aria-labelledby="uploadTitle-${this.userId}">
        <div class="dialog-header"><h2 id="uploadTitle-${this.userId}">${uploadTitle}</h2>
          <button type="button" data-close-upload aria-label="Close">Close</button></div>
        <p class="dialog-error" data-dialog-error hidden role="alert"></p>
        <p class="mute">Every submission needs admin approval. Your current approval remains active during renewal review.
        Only the fields required by the chosen type are shown. Past validity periods can be recorded; flight eligibility is not checked.</p>
        <form data-submit>
          <label class="field">Qualification <select name="type_id" required>${window.typeOptions(types, t =>
            `<option value="${t.id}">${esc(t.name)}${t.internal ? ' (internal)' : ''}</option>`)}</select></label>
          <p class="mute" data-type-desc style="margin:-4px 0 0;font-size:12px"></p>
          <label class="field" data-field="valid_from">Valid from <input type="date" name="valid_from"></label>
          <label class="field" data-field="valid_until">Valid until <input type="date" name="valid_until"></label>
          <label class="field" data-field="number">Number <input name="number" maxlength="40" placeholder="e.g. NLD-RP-… (read from the evidence when empty)"></label>
          <label class="field" data-field="evidence">Evidence <input name="evidence" type="file" accept=".pdf,.jpg,.jpeg,.png"></label>
          <p class="mute" data-proof-hint></p>
          <div data-read-result style="font-size:12px" hidden aria-live="polite"></div>
          <div class="field covers" data-covers>Also covers <span class="mute" style="font-size:12px">(optional: one record per type, same file, number and dates)</span>
            <div class="covers-chips" data-covers-chips></div>
            <div class="covers-combo"><input type="search" data-covers-search placeholder="Add another qualification…" aria-label="Find a qualification this certificate also covers" autocomplete="off">
              <div class="covers-pop" data-covers-pop hidden role="listbox"></div></div>
            <p class="mute" data-covers-note style="margin:4px 0 0;font-size:12px" hidden>Suggested from the certificate's text and from types issued under the same number.</p>
          </div>
          <button type="submit" data-submit-button>Submit for approval</button>
        </form>
      </dialog>` : ''}
      ${this.admin && this.editing ? `<dialog class="entry-dialog" data-dates-dialog aria-labelledby="datesTitle-${this.userId}">
        <div class="dialog-header"><h2 id="datesTitle-${this.userId}">Edit</h2>
          <button type="button" data-close-dates aria-label="Close">Close</button></div>
        <p class="dialog-error" data-dialog-error hidden role="alert"></p>
        <form data-dates-form>
          <label class="field">Valid from <input type="date" name="valid_from"></label>
          <label class="field">Valid until <input type="date" name="valid_until"></label>
          <label class="field">Number <input name="number" maxlength="40"></label>
          <button type="submit">Save</button>
        </form>
      </dialog>` : ''}
      ${this.admin ? `<details class="help-text"><summary>Qualification history (admins only)</summary>
        <p class="mute">An audit of submission, review, date correction and deletion actions, separate from the records and files above.</p>
        <button type="button" data-history>Show latest 100 events</button>
        <pre data-events style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>` : ''}`;
    const olderButton = this.element.querySelector('[data-older]');
    if (olderButton) olderButton.onclick = () => { this.showOlder(!this.showOlder()); this.render(); };
    this.element.querySelectorAll('[data-qr]').forEach(button => {
      button.onclick = () => { const d = this.element.querySelector(`[data-qr-detail="${button.dataset.qr}"]`); d.hidden = !d.hidden; };
    });
    const search = this.element.querySelector('[data-qual-search]');
    const filter = () => {
      const words = (this.query || '').toLowerCase().split(/\s+/).filter(Boolean);
      this.element.querySelectorAll('[data-row]').forEach(row => {
        row.hidden = !words.every(w => row.dataset.search.includes(w));
      });
      // a group heading only while one of its rows shows
      this.element.querySelectorAll('[data-group-head]').forEach(head => {
        let row = head.nextElementSibling, shown = false;
        for (; row && !row.matches('[data-group-head]'); row = row.nextElementSibling) shown ||= !row.hidden;
        head.hidden = !shown;
      });
      if (words.length) this.element.querySelectorAll('[data-older-list]').forEach(list => { list.hidden = false; });
    };
    if (search) search.oninput = () => { this.query = search.value; filter(); };
    filter();
    this.element.querySelectorAll('[data-review]').forEach(button => {
      button.onclick = () => this.review(+button.dataset.id, button.dataset.review);
    });
    this.element.querySelectorAll('[data-dates]').forEach(button => {
      button.onclick = () => this.editDates(q.items.find(i => i.id === +button.dataset.dates));
    });
    const datesDialog = this.element.querySelector('[data-dates-dialog]');
    if (datesDialog) {
      this.element.querySelector('[data-close-dates]').onclick = () => datesDialog.close();
      this.element.querySelector('[data-dates-form]').onsubmit = event => {
        event.preventDefault();
        const f = event.target.elements;
        this.mutate(`/api/qualification-submissions/${datesDialog.dataset.id}`, {method:'PUT',
          headers:{'Content-Type':'application/json'}, body:JSON.stringify({valid_from: f.valid_from.value, valid_until: f.valid_until.value, number: f.number.value})});
      };
    }
    this.element.querySelectorAll('[data-delete]').forEach(button => {
      button.onclick = () => this.remove(+button.dataset.id, button.dataset.delete === 'evidence');
    });
    const dialog = this.element.querySelector('[data-upload-dialog]');
    const openUpload = this.element.querySelector('[data-open-upload]');
    if (openUpload && dialog) {
      openUpload.onclick = () => dialog.showModal();
      this.element.querySelector('[data-close-upload]').onclick = () => dialog.close();
    }
    const form = this.element.querySelector('[data-submit]');
    if (form) {
      const hint = () => {
        const type = types.find(t => t.id === +form.elements.type_id.value);
        const show = (name, on) => {
          form.querySelector(`[data-field="${name}"]`).hidden = !on;
          form.elements[name].required = !!on;
          if (!on) form.elements[name].value = '';
        };
        show('valid_from', type.requires_start);
        show('valid_until', type.requires_expiry);
        form.querySelector('[data-field="number"]').hidden = !type.requires_number;
        if (!type.requires_number) form.elements.number.value = '';
        show('evidence', true);
        form.elements.evidence.required = !!type.requires_evidence;
        const desc = this.element.querySelector('[data-type-desc]');
        desc.textContent = type.description || '';
        desc.hidden = !type.description;
        this.element.querySelector('[data-proof-hint]').textContent =
          (type.requires_evidence ? 'Evidence is required. ' : 'Evidence is optional. ') + 'One PDF, JPEG or PNG, up to 20 MiB.';
      };
      // Empty fields may stay empty when evidence is chosen: the server reads them from it on submit.
      const required = () => {
        const type = types.find(t => t.id === +form.elements.type_id.value);
        const file = form.elements.evidence.files.length > 0;
        form.elements.valid_from.required = !!type.requires_start && !file;
        form.elements.valid_until.required = !!type.requires_expiry && !file;
      };
      form.alsoTypes = new Set();  // other type ids this certificate covers (one record each)
      form.suggested = [];        // [{type_id, reason}] from the read
      form.elements.type_id.onchange = () => { hint(); required(); form.alsoTypes.delete(+form.elements.type_id.value); this.paintCovers(form); };
      const coverSearch = form.querySelector('[data-covers-search]'), pop = form.querySelector('[data-covers-pop]');
      coverSearch.oninput = coverSearch.onfocus = () => this.paintCovers(form, true);
      coverSearch.onkeydown = event => { if (event.key === 'Escape' && !pop.hidden) { event.preventDefault(); event.stopPropagation(); pop.hidden = true; } };
      form.addEventListener('focusout', () => setTimeout(() => {
        if (!form.querySelector('.covers-combo').contains(document.activeElement)) pop.hidden = true;
      }, 0));
      this.paintCovers(form);
      form.elements.evidence.onchange = () => { required(); this.readEvidence(form); };
      form.elements.valid_from.onchange = () => { form.elements.valid_until.min = form.elements.valid_from.value; };
      form.elements.valid_until.onchange = () => { form.elements.valid_from.max = form.elements.valid_until.value; };
      hint();
      form.onsubmit = event => {
        event.preventDefault();
        const file = form.elements.evidence.files[0];
        if (file && file.size > q.max_evidence_bytes) {
          this.error('Evidence must be at most 20 MiB.'); return;
        }
        const body = new FormData(form);
        const claims = this.readClaims(form);  // values the dialog filled from the evidence, still unchanged
        if (Object.keys(claims).length) body.append('read_from', JSON.stringify(claims));
        if (form.alsoTypes.size) body.append('also_type_ids', JSON.stringify([...form.alsoTypes]));
        // uploading and checking the evidence can take a while: say so on the button
        const button = form.querySelector('button[type="submit"], button:not([type])');
        const label = button && button.textContent;
        if (button) button.textContent = file ? 'Uploading and checking…' : 'Submitting…';
        this.mutate(`/api/users/${this.userId}/qualifications`, {method:'POST', body})
          .finally(() => { if (button && button.isConnected) button.textContent = label; });
      };
    }
    const history = this.element.querySelector('[data-history]');
    if (history) history.onclick = async () => {
      try {
        const d = await this.request(`/api/users/${this.userId}/qualification-audit`);
        this.element.querySelector('[data-events]').textContent = d.events.map(e => {
          const detail = JSON.parse(e.details_json);
          const dates = [];
          if (detail.valid_from) dates.push(`Valid from ${detail.valid_from}`);
          if (detail.valid_until) dates.push(`Valid until ${detail.valid_until}`);
          if (detail.number) dates.push(`Number ${detail.number}`);
          const sources = {qr: 'QR code', text: 'PDF text', ocr: 'OCR'};
          if (detail.read_from) dates.push('read from the evidence: ' + Object.entries(detail.read_from)
            .map(([field, source]) => `${field.replace('_', ' ')} (${sources[source] || source})`).join(', '));
          if (detail.read_unchecked) dates.push('the upload dialog reports it read: ' + Object.entries(detail.read_unchecked)
            .map(([field, source]) => `${field.replace('_', ' ')} (${sources[source] || source})`).join(', ')
            + ', not confirmed on submit');
          if (detail.differs) dates.push('differs from the evidence: ' + detail.differs.join('; '));
          return `${e.created_at} · ${e.actor || 'former user'} · ${e.action.replaceAll('-', ' ')}`
            + `\n${detail.category || 'Qualification'}${dates.length ? ' · ' + dates.join(' · ') : ''}`
            + (typeof detail.has_evidence === 'boolean' ? ` · ${detail.has_evidence ? 'evidence attached' : 'no evidence attached'}` : '')
            + (detail.status ? ` · previously ${detail.status}` : '')
            + ('old_valid_until' in detail ? ` · was ${detail.old_valid_from || '…'} to ${detail.old_valid_until || '…'}` : '');
        }).join('\n\n') || 'No events.';
      } catch (error) { this.error(error.message); }
    };
  }
  // {field: source} of the values this dialog filled from the evidence that the person has not changed since.
  readClaims(form) {
    const filled = form.readFilled || {};
    return Object.fromEntries(Object.entries(filled)
      .filter(([name, hit]) => form.elements[name].value && form.elements[name].value === hit.value)
      .map(([name, hit]) => [name, hit.source]));
  }
  // Clear the values the dialog filled from an earlier file, unless the person changed them since.
  // "Also covers": chips for the added types (✕ removes), one-click suggestions, and a search over all other active
  // types with checkboxes. The submit button counts the records it makes.
  paintCovers(form, open = false) {
    const types = this.data.types.filter(t => t.active), main = +form.elements.type_id.value;
    const byId = new Map(types.map(t => [t.id, t]));
    const needs = t => [t.requires_evidence ? 'needs evidence' : '', t.requires_number ? 'needs a number' : '']
      .filter(Boolean).join(', ');
    const chips = [...form.alsoTypes].filter(id => byId.has(id)).map(id =>
      `<span class="covers-chip">${this.esc(byId.get(id).name)}<button type="button" data-covers-remove="${id}" aria-label="Remove ${this.esc(byId.get(id).name)}">✕</button></span>`);
    const suggested = (form.suggested || []).filter(s => byId.has(s.type_id) && s.type_id !== main && !form.alsoTypes.has(s.type_id));
    const sugs = suggested.map(s => `<button type="button" class="covers-sug" data-covers-add="${s.type_id}"
      title="${s.reason === 'number' ? 'Records of this type carry the same certificate number' : 'Mentioned in the certificate'}">+ ${this.esc(byId.get(s.type_id).name)}</button>`);
    form.querySelector('[data-covers-chips]').innerHTML = chips.join('') + sugs.join('');
    form.querySelector('[data-covers-note]').hidden = !suggested.length;
    const search = form.querySelector('[data-covers-search]'), pop = form.querySelector('[data-covers-pop]');
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    // a word matches the type's name or its group ("drone" lists the whole Drone group)
    const shown = types.filter(t => t.id !== main && words.every(w => `${t.name} ${t.grp || ''}`.toLowerCase().includes(w)));
    const option = t => `<label class="covers-opt"><input type="checkbox" data-covers-toggle="${t.id}"${form.alsoTypes.has(t.id) ? ' checked' : ''}>
      <span>${this.esc(t.name)}</span><small>${form.alsoTypes.has(t.id) ? 'added' : this.esc(needs(t))}</small></label>`;
    // in the type groups (alphabetical inside each) once types have groups; otherwise one alphabetical list
    const grouped = shown.some(t => (t.grp || '').trim());
    pop.innerHTML = (grouped ? window.typeGroups(shown).map(g => `<div class="type-group-head">${this.esc(g.name)}</div>`
      + [...g.types].sort((a, b) => a.name.localeCompare(b.name)).map(option).join('')).join('')
      : [...shown].sort((a, b) => a.name.localeCompare(b.name)).map(option).join(''))
      || '<p class="mute" style="margin:6px 10px">No other qualification matches.</p>';
    if (open) pop.hidden = false;
    const button = form.querySelector('[data-submit-button]');
    if (button && !button.disabled) button.textContent = form.alsoTypes.size ? `Submit ${form.alsoTypes.size + 1} for approval` : 'Submit for approval';
    const change = (id, on) => { if (on) form.alsoTypes.add(id); else form.alsoTypes.delete(id); this.paintCovers(form, !pop.hidden); };
    form.querySelectorAll('[data-covers-remove]').forEach(b => { b.onclick = () => change(+b.dataset.coversRemove, false); });
    form.querySelectorAll('[data-covers-add]').forEach(b => { b.onclick = () => change(+b.dataset.coversAdd, true); });
    pop.querySelectorAll('[data-covers-toggle]').forEach(box => { box.onchange = () => change(+box.dataset.coversToggle, box.checked); });
  }
  clearFilled(form) {
    const filled = form.readFilled || {};
    for (const [name, hit] of Object.entries(filled)) {
      if (form.elements[name].value === hit.value) form.elements[name].value = '';
    }
    form.readFilled = {};
  }
  // Read the chosen evidence (PDF text, QR code, OCR) and fill the fields: empty ones, and ones filled from an
  // earlier file and not changed since. Typed values stay.
  async readEvidence(form) {
    const box = form.querySelector('[data-read-result]');
    const file = form.elements.evidence.files[0];
    const token = this.reading = (this.reading || 0) + 1;
    box.hidden = !file;
    if (!file) { this.clearFilled(form); return; }
    box.innerHTML = '<span class="mute">Reading the evidence…</span>';
    // The server reads one file per person at a time: a file chosen meanwhile is read when the running read is done.
    if (this.readBusy) { this.readAgain = true; return; }
    this.readBusy = true;
    const data = new FormData();
    const claims = this.readClaims(form);  // filled from an earlier file: not compared with this one
    ['valid_from', 'valid_until', 'number'].forEach(name => {
      if (form.elements[name].value && !claims[name]) data.append(name, form.elements[name].value);
    });
    if (form.elements.type_id.value) data.append('type_id', form.elements.type_id.value);
    data.append('evidence', file);
    let r;
    try {
      for (let tries = 0; ; tries++) {
        try {
          r = await this.request(`/api/users/${this.userId}/qualifications/read`, {method: 'POST', body: data});
          break;
        } catch (error) {  // 429: a read of this person is still running (e.g. in another tab); wait and try again
          if (error.status !== 429 || tries >= 10 || token !== this.reading) throw error;
          await new Promise(resolve => setTimeout(resolve, 3000));
        }
      }
    } catch (error) {
      if (token === this.reading) {
        this.clearFilled(form);  // values read from the previous file do not belong to this one
        box.innerHTML = `<span class="mute">Could not read the evidence: ${this.esc(error.message)}</span>`;
      }
      return;
    } finally {
      this.readBusy = false;
      if (this.readAgain) {
        this.readAgain = false;
        if (token !== this.reading) this.readEvidence(form);  // a newer file was chosen while this one was read
      }
    }
    if (token !== this.reading || form.elements.evidence.files[0] !== file) return;  // another file was chosen
    const sources = {qr: 'QR code', text: 'PDF text', ocr: 'OCR'};
    const labels = {number: 'Number', valid_from: 'Valid from', valid_until: 'Valid until'};
    const bits = [];
    this.clearFilled(form);  // values of the previous file go, unless the person changed them
    for (const [name, label] of Object.entries(labels)) {
      const hit = r.found[name];
      const input = form.elements[name];
      if (!hit) continue;
      const shown = !input.closest('[data-field]').hidden;
      if (shown && !input.value) {
        input.value = hit.value;
        form.readFilled[name] = {value: hit.value, source: hit.source};
      }
      if (shown) bits.push(`${label} ${this.esc(hit.value)} <span class="mute">(${sources[hit.source] || hit.source})</span>`);
    }
    for (const name of ['valid_from', 'valid_until']) {  // keep the date pickers' min/max in step with the values
      if (form.elements[name].onchange) form.elements[name].onchange();
    }
    form.suggested = r.suggestions || [];
    this.paintCovers(form);
    const q = r.qr;
    const who = q && q.authority ? ` · QR code of ${this.esc(q.authority.name)}` : q && q.status === 'none' ? ' · no QR code found' : '';
    box.innerHTML = (bits.length ? 'Read: ' + bits.join(' · ') : '<span class="mute">Nothing recognised in the evidence; fill in the fields.</span>')
      + who + (r.notes || []).map(n => `<div class="mute">${this.esc(n)}</div>`).join('')
      + (r.issues || []).map(i => `<div style="color:var(--err)">${this.esc(i)}</div>`).join('')
      + '<div class="mute">Check the values: an admin confirms them on approval.</div>';
  }
  error(message) {
    const box = this.element.querySelector('[data-error]');
    if (box) box.textContent = message;
    this.element.querySelectorAll('[data-dialog-error]').forEach(dialogErr => {
      dialogErr.textContent = message;
      dialogErr.hidden = !message;
    });
  }
  async mutate(url, options) {
    const buttons = this.element.querySelectorAll('button');
    buttons.forEach(b => { b.disabled = true; });
    this.error('');
    try {
      await this.request(url, options);
      this.data = await this.request(`/api/users/${this.userId}/qualifications`);
      this.render();
      await this.changed();
    } catch (error) {
      this.error(error instanceof TypeError ? 'Network error. Reload the submission list before retrying.' : error.message);
    } finally { buttons.forEach(b => { b.disabled = false; }); }
  }
  review(id, action) {
    let reason = '';
    if (action === 'reject') {
      // the optional reason is mailed to the person and shown next to the rejected item
      reason = prompt('Reject this submission? Evidence is retained.\nOptional reason for the person:', '');
      if (reason === null) return;
    } else if (!confirm(action === 'approve' ? 'Approve this exact submission after checking its evidence (if required)?'
      : 'Withdraw this pending submission?')) return;
    return this.mutate(`/api/qualification-submissions/${id}/review`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(reason ? {action, reason} : {action})});
  }
  // Small dialog with the dates and the number; an empty field clears it (the type may require it).
  editDates(item) {
    const dialog = this.element.querySelector('[data-dates-dialog]');
    if (!dialog || !item) return;
    dialog.dataset.id = item.id;
    dialog.querySelector('h2').textContent = `Edit · ${item.category}`;
    const f = dialog.querySelector('[data-dates-form]').elements;
    f.valid_from.value = item.valid_from || '';
    f.valid_until.value = item.valid_until || '';
    f.number.value = item.number || '';
    this.error('');
    dialog.showModal();
  }
  remove(id, evidenceOnly) {
    if (!confirm(evidenceOnly ? 'Permanently delete this evidence file? The record and any approval remain.'
      : 'Permanently delete this record and its evidence? A minimal audit event remains.')) return;
    return this.mutate(`/api/qualification-submissions/${id}${evidenceOnly ? '/evidence' : ''}`, {method:'DELETE'});
  }
};
// Close an open actions menu (⋯) when clicking elsewhere.
document.addEventListener('click', e => {
  document.querySelectorAll('details.qual-more[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});
