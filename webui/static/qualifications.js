/* Private qualification management shared by My account and the admin People page. */
window.QualificationManager = class {
  constructor(element, userId, admin, changed = () => {}) {
    this.element = element;
    this.userId = userId;
    this.admin = admin;
    this.changed = changed;
    this.editing = true;
    this.view = 'current';
  }
  esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  async request(url, options) {
    const r = await fetch(url, options);
    if (r.status === 401) { location.href = '/login'; throw new Error('Please sign in again.'); }
    const data = await r.json().catch(() => { throw new Error(`Invalid server response (${r.status}). Reload before retrying.`); });
    if (!r.ok) throw new Error(data.error || 'Qualification request failed.');
    return data;
  }
  async load() {
    try {
      this.data = await this.request(`/api/users/${this.userId}/qualifications`);
      this.render();
    } catch (error) { this.element.textContent = error.message; }
  }
  dateBits(item) {
    const bits = [];
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
  render() {
    const q = this.data, esc = v => this.esc(v);
    const claim = item => {
      const dates = this.dateBits(item);
      const tip = [...dates, ...this.metaTip(item)].map(line => this.esc(line)).join('<br>');
      const name = `<strong>${esc(item.category)}</strong>`;
      return `<div style="margin:12px 0">
      <span title="${esc(dates.join(' · '))}">${item.has_evidence ? fileLink({name, viewable: item.viewable, view: `/api/qualification-submissions/${item.id}/evidence/view`, download: `/api/qualification-submissions/${item.id}/evidence`}) : name}</span> · ${esc(item.status === 'superseded' ? 'archived' : item.status)}${item.internal ? ' · Internal' : ''}${item.expired ? ' (expired)' : ''}
      <span class="info-tip"><button type="button" aria-label="Submission details">i</button><span class="tip" role="tooltip">${tip}</span></span>
      <div>${item.has_evidence ? `<span class="mute">${esc(item.filename || 'Evidence')}</span>`
        : `<span class="mute">${item.evidence_deleted_at ? 'Evidence deleted by admin' : 'No evidence attached'}</span>`}
      ${this.editing && item.status === 'pending' ? `${this.admin
        ? `<button type="button" data-review="approve" data-id="${item.id}">Approve</button>
           <button type="button" data-review="reject" data-id="${item.id}">Reject</button>` : ''}
        <button type="button" data-review="withdraw" data-id="${item.id}">Withdraw</button>` : ''}
      ${this.admin && this.editing ? `${item.has_evidence ? `<button type="button" data-delete="evidence" data-id="${item.id}">Delete evidence</button>` : ''}
        <button type="button" data-delete="record" data-id="${item.id}">Delete record</button>` : ''}</div></div>`;
    };
    const archived = i => i.status === 'superseded' || (i.status === 'approved' && i.expired);
    const current = q.items.filter(i => ['pending','approved'].includes(i.status) && !archived(i));
    const archive = q.items.filter(archived)
      .sort((a, b) => String(b.valid_until || '9999').localeCompare(String(a.valid_until || '9999')));
    const previous = q.items.filter(i => ['rejected','withdrawn'].includes(i.status));
    const types = q.types.filter(t => t.active);
    const uploadTitle = this.admin ? 'Upload for this person' : 'Submit a qualification';
    const tab = (view, label) => `<button type="button" role="tab" data-view="${view}"
      aria-selected="${this.view === view}" tabindex="${this.view === view ? 0 : -1}">${label}</button>`;
    this.element.innerHTML = `<div data-error role="alert" style="color:var(--err);overflow-wrap:anywhere"></div>
      <div class="section-heading"><h3>Qualifications</h3>
      <details><summary>Help</summary><p>Current shows valid approvals and pending submissions.
      Archive shows expired and earlier licences, newest first, so past flights can be checked.
      Only the linked person and administrators can see these records and files.
      Hover over a qualification for recorded validity dates, or open Submission details.
      Click its name to download available evidence.</p></details></div>
      <div class="qual-tabs" role="tablist" aria-label="Qualification views">${tab('current', 'Current')}${tab('archive', `Archive (${archive.length})`)}</div>
      ${this.view === 'archive'
        ? archive.map(claim).join('') || '<p class="mute">No archived licences.</p>'
        : `${current.map(claim).join('') || '<p class="mute">None recorded.</p>'}
      ${previous.length ? `<details class="help-text"><summary>${previous.length} rejected or withdrawn submissions</summary>${previous.map(claim).join('')}</details>` : ''}`}
      ${this.editing ? (types.length ? `<p><button type="button" data-open-upload>${uploadTitle}</button></p>
      <dialog class="entry-dialog" data-upload-dialog aria-labelledby="uploadTitle-${this.userId}">
        <div class="dialog-header"><h2 id="uploadTitle-${this.userId}">${uploadTitle}</h2>
          <button type="button" data-close-upload aria-label="Close">Close</button></div>
        <p class="dialog-error" data-dialog-error hidden role="alert"></p>
        <p class="mute">Every submission needs admin approval. Your current approval remains active during renewal review.
        Only the fields required by the chosen type are shown. Past validity periods can be recorded; flight eligibility is not checked.</p>
        <form data-submit>
          <label class="field">Qualification <select name="type_id" required>${types.map(t =>
            `<option value="${t.id}">${esc(t.name)}${t.internal ? ' (internal)' : ''}</option>`).join('')}</select></label>
          <label class="field" data-field="valid_from">Valid from <input type="date" name="valid_from"></label>
          <label class="field" data-field="valid_until">Valid until <input type="date" name="valid_until"></label>
          <label class="field" data-field="evidence">Evidence <input name="evidence" type="file" accept=".pdf,.jpg,.jpeg,.png"></label>
          <p class="mute" data-proof-hint></p>
          <button type="submit">Submit for approval</button>
        </form>
      </dialog>` : '<p class="mute">An admin must first add a qualification type on People.</p>')
        : '<p class="mute">Turn on Edit to upload, review or delete qualifications for this person.</p>'}
      ${this.admin ? `<details class="help-text"><summary>Qualification history (admins only)</summary>
        <p class="mute">An audit of submission, review and deletion actions, separate from the records and files above.</p>
        <button type="button" data-history>Show latest 100 events</button>
        <pre data-events style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>` : ''}`;
    this.element.querySelectorAll('[data-view]').forEach(button => {
      button.onclick = () => { this.view = button.dataset.view; this.render(); };
    });
    this.element.querySelectorAll('[data-review]').forEach(button => {
      button.onclick = () => this.review(+button.dataset.id, button.dataset.review);
    });
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
        show('evidence', true);
        form.elements.evidence.required = !!type.requires_evidence;
        this.element.querySelector('[data-proof-hint]').textContent =
          (type.requires_evidence ? 'Evidence is required. ' : 'Evidence is optional. ') + 'One PDF, JPEG or PNG, up to 20 MiB.';
      };
      form.elements.type_id.onchange = hint;
      form.elements.valid_from.onchange = () => { form.elements.valid_until.min = form.elements.valid_from.value; };
      form.elements.valid_until.onchange = () => { form.elements.valid_from.max = form.elements.valid_until.value; };
      hint();
      form.onsubmit = event => {
        event.preventDefault();
        const file = form.elements.evidence.files[0];
        if (file && file.size > q.max_evidence_bytes) {
          this.error('Evidence must be at most 20 MiB.'); return;
        }
        this.mutate(`/api/users/${this.userId}/qualifications`, {method:'POST', body:new FormData(form)});
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
          return `${e.created_at} · ${e.actor || 'former user'} · ${e.action.replaceAll('-', ' ')}`
            + `\n${detail.category || 'Qualification'}${dates.length ? ' · ' + dates.join(' · ') : ''}`
            + (typeof detail.has_evidence === 'boolean' ? ` · ${detail.has_evidence ? 'evidence attached' : 'no evidence attached'}` : '')
            + (detail.status ? ` · previously ${detail.status}` : '');
        }).join('\n\n') || 'No events.';
      } catch (error) { this.error(error.message); }
    };
  }
  error(message) {
    const box = this.element.querySelector('[data-error]');
    if (box) box.textContent = message;
    const dialogErr = this.element.querySelector('[data-dialog-error]');
    if (dialogErr) {
      dialogErr.textContent = message;
      dialogErr.hidden = !message;
    }
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
    if (!confirm(action === 'approve' ? 'Approve this exact submission after checking its evidence (if required)?'
      : action === 'reject' ? 'Reject this submission? Evidence is retained.' : 'Withdraw this pending submission?')) return;
    return this.mutate(`/api/qualification-submissions/${id}/review`,
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action})});
  }
  remove(id, evidenceOnly) {
    if (!confirm(evidenceOnly ? 'Permanently delete this evidence file? The record and any approval remain.'
      : 'Permanently delete this record and its evidence? A minimal audit event remains.')) return;
    return this.mutate(`/api/qualification-submissions/${id}${evidenceOnly ? '/evidence' : ''}`, {method:'DELETE'});
  }
};
