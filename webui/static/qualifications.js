/* Private qualification management shared by My account and the admin People page. */
window.QualificationManager = class {
  constructor(element, userId, admin, changed = () => {}) {
    this.element = element;
    this.userId = userId;
    this.admin = admin;
    this.changed = changed;
    this.editing = true;
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
  render() {
    const q = this.data, esc = v => this.esc(v);
    const claim = item => `<div style="margin:12px 0">
      <strong>${esc(item.category)}</strong> · ${esc(item.status)} · ${esc(item.valid_until || 'expiry not recorded')}${item.expired ? ' (expired)' : ''}
      <div class="mute">Submitted ${esc(item.submitted_at)}${item.reviewed_at
        ? ` · ${item.status === 'withdrawn' ? 'Withdrawn' : 'Reviewed'} by ${esc(item.reviewer || 'former user')} ${esc(item.reviewed_at)}` : ''}</div>
      ${item.has_evidence ? `<a href="/api/qualification-submissions/${item.id}/evidence">Download</a>`
        : `<span class="mute">${item.evidence_deleted_at ? 'Evidence deleted by admin' : 'No evidence attached'}</span>`}
      ${this.editing && item.status === 'pending' ? `${this.admin
        ? `<button type="button" data-review="approve" data-id="${item.id}">Approve</button>
           <button type="button" data-review="reject" data-id="${item.id}">Reject</button>` : ''}
        <button type="button" data-review="withdraw" data-id="${item.id}">Withdraw</button>` : ''}
      ${this.admin && this.editing ? `${item.has_evidence ? `<button type="button" data-delete="evidence" data-id="${item.id}">Delete evidence</button>` : ''}
        <button type="button" data-delete="record" data-id="${item.id}">Delete record</button>` : ''}</div>`;
    const current = q.items.filter(i => ['pending','approved'].includes(i.status));
    const previous = q.items.filter(i => !['pending','approved'].includes(i.status));
    const types = q.types.filter(t => t.active);
    this.element.innerHTML = `<div data-error role="alert" style="color:var(--err);overflow-wrap:anywhere"></div>
      <div class="section-heading"><h3>Current qualifications</h3>
      <details><summary>Help</summary><p>Approved qualifications and pending submissions.
      Only the linked person and administrators can see these records and files.</p></details></div>
      ${current.map(claim).join('') || '<p class="mute">None recorded.</p>'}
      ${previous.length ? `<details class="help-text"><summary>${previous.length} previous submissions</summary>${previous.map(claim).join('')}</details>` : ''}
      ${this.editing ? `<div class="section-heading"><h3>${this.admin ? 'Upload for this person' : 'Submit a qualification'}</h3>
      <details><summary>Help</summary><p>Every submission needs admin approval. Your current approval remains active during renewal review.
      Blank expiry means unknown or not applicable. Use this private upload, not flight attachments, for licence evidence.</p></details></div>
      ${types.length ? `<form data-submit>
        <p><label>Qualification <select name="type_id" required>${types.map(t =>
          `<option value="${t.id}">${esc(t.name)}</option>`).join('')}</select></label></p>
        <p><label><span data-expiry-label>Expiry</span> <input type="date" name="valid_until"></label></p>
        <p><label>Evidence <input name="evidence" type="file" accept=".pdf,.jpg,.jpeg,.png" ${types[0].requires_evidence ? 'required' : ''}></label></p>
        <p class="mute" data-proof-hint></p><button type="submit">Submit for approval</button>
      </form>` : '<p class="mute">An admin must first add a qualification type on People.</p>'}`
        : '<p class="mute">Turn on Edit to upload, review or delete qualifications for this person.</p>'}
      ${this.admin ? `<details class="help-text"><summary>Qualification history (admins only)</summary>
        <p class="mute">An audit of submission, review and deletion actions, separate from the previous submissions and files above.</p>
        <button type="button" data-history>Show latest 100 events</button>
        <pre data-events style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>` : ''}`;
    this.element.querySelectorAll('[data-review]').forEach(button => {
      button.onclick = () => this.review(+button.dataset.id, button.dataset.review);
    });
    this.element.querySelectorAll('[data-delete]').forEach(button => {
      button.onclick = () => this.remove(+button.dataset.id, button.dataset.delete === 'evidence');
    });
    const form = this.element.querySelector('[data-submit]');
    if (form) {
      const hint = () => {
        const type = types.find(t => t.id === +form.elements.type_id.value);
        form.elements.evidence.required = !!type.requires_evidence;
        form.elements.valid_until.required = !!type.requires_expiry;
        this.element.querySelector('[data-expiry-label]').textContent = type.requires_expiry ? 'Expiry (required)' : 'Expiry (optional)';
        this.element.querySelector('[data-proof-hint]').textContent =
          (type.requires_evidence ? 'Evidence is required. ' : 'Evidence is optional. ') + 'One PDF, JPEG or PNG, up to 20 MiB.';
      };
      form.elements.type_id.onchange = hint;
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
          return `${e.created_at} · ${e.actor || 'former user'} · ${e.action.replaceAll('-', ' ')}`
            + `\n${detail.category || 'Qualification'} · expiry ${detail.valid_until || 'not recorded'}`
            + (typeof detail.has_evidence === 'boolean' ? ` · ${detail.has_evidence ? 'evidence attached' : 'no evidence attached'}` : '')
            + (detail.status ? ` · previously ${detail.status}` : '');
        }).join('\n\n') || 'No events.';
      } catch (error) { this.error(error.message); }
    };
  }
  error(message) {
    this.element.querySelector('[data-error]').textContent = message;
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
