// Medicare emergency dashboard.
// All data lives on the Flask backend (/api/*). The browser only holds the
// signed-in identity, a CSRF token, and a cache of the last dashboard response.
'use strict';

const $ = (s) => document.querySelector(s);
const levels = ['', 'Critical', 'High', 'Medium', 'Low'];
const VITALS = ['spo2', 'pulse', 'respiratoryRate', 'systolic', 'temperature'];
const CLINICAL_FIELDS = new Set([
  'symptoms', 'age', 'spo2', 'pulse', 'respiratoryRate', 'systolic',
  'temperature', 'warning', 'warningsReviewed',
]);

const state = {
  me: null,          // {id, username, name, role}
  csrf: '',
  patients: [],
  doctors: [],
  beds: [],
  tab: 'all',
  draftId: null,     // server-side assessment draft for the open registration
  assessment: null,  // last server assessment {severity, emergency, reasons, ...}
  assessSeq: 0,
};
let toastTimer;
let pollTimer;

const esc = (v) =>
  String(v ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const canWrite = () => state.me && ['nurse', 'physician'].includes(state.me.role);
const isAdmin = () => state.me && state.me.role === 'admin';

// ---------------------------------------------------------------- API client

async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'X-Pulse-Client': 'dashboard' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['X-CSRF-Token'] = state.csrf;

  let response;
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error('Cannot reach the server. Check that the backend is running.');
  }

  let data = {};
  try { data = await response.json(); } catch { /* non-JSON error body */ }

  if (!response.ok) {
    if (response.status === 401 && state.me) showLogin('Your session expired. Please sign in again.');
    const error = new Error(data.error || `Request failed (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 5000);
}

// ---------------------------------------------------------------- Auth / views

function showLogin(message) {
  clearInterval(pollTimer);
  state.me = null;
  state.csrf = '';
  $('#dashboard').hidden = true;
  $('#login-section').hidden = false;
  $('#staff-name').hidden = true;
  $('#logout').hidden = true;
  $('#department-label').hidden = false;
  $('#login-password').value = '';
  const err = $('#login-error');
  err.textContent = message || '';
  err.hidden = !message;
}

async function showDashboard(identity) {
  state.me = identity;
  state.csrf = identity.csrfToken;
  $('#login-section').hidden = true;
  $('#dashboard').hidden = false;
  $('#staff-name').textContent = `${identity.name} (${identity.role})`;
  $('#staff-name').hidden = false;
  $('#logout').hidden = false;
  $('#department-label').hidden = true;

  $('#register').hidden = !canWrite();
  $('#assign').hidden = !canWrite();
  $('#admin-panel').hidden = !isAdmin();
  $('#reviewer-note').textContent =
    `Reviewer: ${identity.name} (${identity.role}) — recorded automatically from your login.`;

  await refresh();
  if (isAdmin()) await loadStaff();
  clearInterval(pollTimer);
  pollTimer = setInterval(() => refresh().catch(() => {}), 15000);
}

$('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = $('#login-button');
  const err = $('#login-error');
  err.hidden = true;
  button.disabled = true;
  button.textContent = 'Logging in…';
  try {
    const identity = await api('/api/auth/login', {
      method: 'POST',
      body: { username: $('#login-username').value.trim(), password: $('#login-password').value },
    });
    await showDashboard(identity);
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  } finally {
    button.disabled = false;
    button.textContent = 'Login';
  }
});

$('#logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST', body: {} }); } catch { /* ignore */ }
  showLogin();
});

// ---------------------------------------------------------------- Dashboard data

async function refresh() {
  const data = await api('/api/dashboard');
  state.patients = data.patients;
  state.doctors = data.doctors;
  state.beds = data.beds;
  render();
}

const order = (list) =>
  [...list].sort((a, b) => a.severity - b.severity || a.arrival - b.arrival || a.id - b.id);

const waitMinutes = (p) =>
  p.status === 'waiting' ? Math.max(0, Math.floor((Date.now() - p.arrival) / 60000)) : p.wait || 0;

function doctorBadge(d) {
  return `<span class="badge ${d.active ? '' : 'off'}">${!d.active ? 'Disabled' : d.busy ? 'In treatment' : 'Available'}</span>`;
}

function render() {
  const { patients, doctors, beds } = state;
  const waiting = order(patients.filter((p) => p.status === 'waiting'));
  const treating = patients.filter((p) => p.status === 'treatment');
  const positions = new Map(waiting.map((p, i) => [p.id, i + 1]));
  const freeBeds = beds.filter((b) => b.active && !b.busy).length;

  $('#stats').innerHTML = [
    ['Waiting', waiting.length],
    ['In treatment', treating.length],
    ['Available beds', freeBeds],
    ['Average wait', `${waiting.length
      ? Math.round(waiting.reduce((n, p) => n + waitMinutes(p), 0) / waiting.length) : 0} min`],
  ].map(([l, v]) => `<div class="stat">${l}<strong>${v}</strong></div>`).join('');

  const q = $('#search').value.toLowerCase();
  const sev = $('#filter').value;
  const doctorName = (id) => (doctors.find((d) => d.id === id) || {}).name || `Doctor ${id}`;

  const visible = order(patients.filter((p) =>
    (state.tab === 'all' ? p.status !== 'completed' : p.status === state.tab) &&
    (sev === 'all' || p.severity === Number(sev)) &&
    `${p.name} ER-${p.id} ${p.condition} ${p.disease || ''}`.toLowerCase().includes(q),
  )).sort((a, b) => (a.status === 'waiting' ? 0 : 1) - (b.status === 'waiting' ? 0 : 1));

  $('#patients').innerHTML = visible.map((p) => `<tr>
    <td>${positions.has(p.id) ? '#' + positions.get(p.id) : '—'}</td>
    <td><strong>${esc(p.name)}</strong><small>ER-${p.id} · ${p.age} yrs · ${esc(p.gender)}</small>
      <small>${esc(p.condition)}</small>
      ${p.disease ? `<small>Known condition: ${esc(p.disease)}</small>` : ''}</td>
    <td><span class="badge s${p.severity}">${levels[p.severity]}</span></td>
    <td>${waitMinutes(p)} min<small>${p.status === 'waiting' ? 'waiting' : 'before treatment'}</small></td>
    <td>${p.status === 'waiting' ? 'Waiting' : p.status === 'treatment' ? 'In treatment' : 'Discharged'}</td>
    <td>${p.doctor != null ? esc(doctorName(p.doctor)) + `<small>Bed ${p.bed}</small>` : 'Awaiting assignment'}</td>
    <td>${p.status === 'treatment' && canWrite()
      ? `<button data-discharge="${p.id}">Discharge</button>`
      : p.status === 'waiting' ? 'In priority queue' : p.status === 'treatment' ? 'In treatment' : 'Completed'}</td>
  </tr>`).join('') || '<tr><td colspan="7">No patients match this view.</td></tr>';

  $('#count').textContent = `Showing ${visible.length} patients`;

  $('#doctors').innerHTML = doctors.map((d) => `<div class="resource"><span>${esc(d.name)}</span>
    <span>${doctorBadge(d)}${isAdmin() ? ` <button type="button" class="secondary" data-resource="doctors" data-id="${d.id}" data-active="${d.active}">${d.active ? 'Disable' : 'Enable'}</button>` : ''}</span></div>`).join('');

  $('#beds').innerHTML = beds.map((b) => `<div class="bed ${b.busy || !b.active ? 'busy' : ''}">Bed ${b.id}<br>
    <small>${!b.active ? 'Disabled' : b.busy ? 'Occupied' : 'Available'}</small>
    ${isAdmin() ? `<br><button type="button" class="secondary" data-resource="beds" data-id="${b.id}" data-active="${b.active}">${b.active ? 'Disable' : 'Enable'}</button>` : ''}</div>`).join('');
}

// ---------------------------------------------------------------- Actions

$('#assign').onclick = async () => {
  try {
    const p = await api('/api/queue/assign-next', { method: 'POST', body: {} });
    await refresh();
    const d = state.doctors.find((x) => x.id === p.doctor);
    toast(`${p.name} assigned to ${d ? d.name : 'a doctor'}, bed ${p.bed}.`);
  } catch (e) { toast(e.message); await refresh().catch(() => {}); }
};

$('#patients').onclick = async (event) => {
  const b = event.target.closest('[data-discharge]');
  if (!b) return;
  b.disabled = true;
  try {
    const p = await api(`/api/patients/${b.dataset.discharge}/discharge`, { method: 'POST', body: {} });
    await refresh();
    toast(`${p.name} discharged. Doctor and bed released.`);
  } catch (e) { toast(e.message); b.disabled = false; }
};

$('#search').oninput = render;
$('#filter').onchange = render;
document.querySelectorAll('[data-tab]').forEach((b) => {
  b.onclick = () => {
    state.tab = b.dataset.tab;
    document.querySelectorAll('[data-tab]').forEach((t) => t.classList.toggle('selected', t === b));
    render();
  };
});

// ---------------------------------------------------------------- Admin

async function loadStaff() {
  const { items } = await api('/api/staff');
  $('#staff-list').innerHTML = items.map((s) => `<div class="resource">
    <span>${esc(s.name)} <small>${esc(s.username)} · ${esc(s.role)}</small></span>
    <span><span class="badge ${s.active ? '' : 'off'}">${s.active ? 'Active' : 'Disabled'}</span>
    ${s.id === state.me.id ? '' : `<button type="button" class="secondary" data-staff="${s.id}" data-active="${s.active}">${s.active ? 'Disable' : 'Enable'}</button>`}
    </span></div>`).join('');
}

async function toggleResource(event) {
  const b = event.target.closest('[data-resource]');
  if (!b) return;
  try {
    await api(`/api/resources/${b.dataset.resource}/${b.dataset.id}`, {
      method: 'PATCH', body: { active: b.dataset.active !== 'true' },
    });
    await refresh();
  } catch (e) { toast(e.message); }
}
$('#doctors').addEventListener('click', toggleResource);
$('#beds').addEventListener('click', toggleResource);

$('#staff-list').addEventListener('click', async (event) => {
  const b = event.target.closest('[data-staff]');
  if (!b) return;
  try {
    await api(`/api/staff/${b.dataset.staff}`, { method: 'PATCH', body: { active: b.dataset.active !== 'true' } });
    await loadStaff();
  } catch (e) { toast(e.message); }
});

$('#add-doctor').onclick = async () => {
  const input = $('#new-doctor-name');
  try {
    await api('/api/resources/doctors', { method: 'POST', body: { name: input.value } });
    input.value = '';
    await refresh();
    toast('Doctor added.');
  } catch (e) { toast(e.message); }
};

$('#add-bed').onclick = async () => {
  try {
    await api('/api/resources/beds', { method: 'POST', body: {} });
    await refresh();
    toast('Bed added.');
  } catch (e) { toast(e.message); }
};

// ---------------------------------------------------------------- Registration

const form = $('#form');

$('#register').onclick = () => { resetRegistration(); $('#dialog').showModal(); };
$('#cancel').onclick = () => $('#dialog').close();

function showError(message, field) {
  $('#error').textContent = message;
  $('#error').hidden = false;
  if (field) field.focus();
  $('#error').scrollIntoView({ block: 'nearest' });
}

function readClinical() {
  const f = form.elements;
  const vitals = {};
  for (const name of VITALS) {
    const raw = f.namedItem(name).value;
    if (raw !== '') vitals[name] = Number(raw);
  }
  return {
    symptoms: f.symptoms.value.trim(),
    age: f.age.value === '' ? null : Number(f.age.value),
    warnings: [...form.querySelectorAll('[name="warning"]:checked')].map((i) => i.value),
    warningsReviewed: f.warningsReviewed.checked,
    vitals,
  };
}

function resetRegistration() {
  clearTimeout(assessTimer);
  state.assessSeq++;
  form.reset();
  state.draftId = null;
  state.assessment = null;
  $('#error').hidden = true;
  previewAssessment();
}

function renderAssessment(result, pending) {
  $('#assessment').innerHTML =
    `<strong class="badge s${result.severity}">Suggested: ${levels[result.severity]}</strong>` +
    `${pending ? ' <small>checking with server…</small>' : ''}` +
    `<ul>${result.reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` +
    (result.errors || []).map((e) => `<p class="error">${esc(e)}</p>`).join('');
  const latched = !!(result.emergencyLatched || result.emergency);
  for (const option of form.elements.severity.options) option.disabled = latched && option.value !== '1';
}

// Instant local preview (assessment.js) so the form feels live; the server
// assessment below is the one that is stored and enforced.
function previewAssessment() {
  const c = readClinical();
  const local = assessSeverity({
    symptoms: c.symptoms, age: c.age ?? '', vitals: c.vitals,
    warnings: c.warnings, warningsReviewed: c.warningsReviewed,
  });
  if (state.assessment && state.assessment.emergencyLatched && !local.emergency) {
    local.reasons.unshift('An earlier emergency warning remains active for this registration. Critical priority retained.');
    local.severity = 1;
    local.emergency = true;
  }
  renderAssessment(local, serverReady(c));
  form.elements.severity.value = String(local.severity);
  return local;
}

function serverReady(c) {
  return c.symptoms !== '' && Number.isInteger(c.age) && c.age >= 0 && c.age <= 120;
}

async function serverAssess() {
  const c = readClinical();
  if (!serverReady(c)) return null;
  const seq = ++state.assessSeq;
  const send = () => api('/api/assessments', { method: 'POST', body: { ...c, draftId: state.draftId || undefined } });
  let result;
  try {
    result = await send();
  } catch (e) {
    if (e.status === 409) { state.draftId = null; result = await send(); } // stale draft: start a new one
    else throw e;
  }
  if (seq !== state.assessSeq) return null; // a newer edit superseded this one
  state.draftId = result.draftId;
  state.assessment = result;
  renderAssessment(result, false);
  form.elements.severity.value = String(result.severity);
  return result;
}

let assessTimer;
function onClinicalChange(event) {
  $('#error').hidden = true;
  if (event.target.name !== 'review') form.elements.review.checked = false;
  if (!CLINICAL_FIELDS.has(event.target.name)) return;
  previewAssessment();
  clearTimeout(assessTimer);
  assessTimer = setTimeout(() => serverAssess().catch((e) => showError(e.message)), 400);
}
form.addEventListener('input', onClinicalChange);
form.addEventListener('change', onClinicalChange);
form.addEventListener('reset', () => queueMicrotask(previewAssessment));

form.onsubmit = async (event) => {
  event.preventDefault();
  const f = form.elements;
  const name = f.patientName.value.trim();
  const c = readClinical();

  if (!name) return showError('Enter the patient’s full name.', f.patientName);
  if (!Number.isInteger(c.age) || c.age < 0 || c.age > 120) return showError('Enter an age between 0 and 120.', f.age);
  if (!c.symptoms) return showError('Describe the presenting symptoms.', f.symptoms);

  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    // Always reassess right before saving so the draft matches what is submitted.
    clearTimeout(assessTimer);
    const chosen = Number(f.severity.value);
    const result = await serverAssess();
    if (!result) return;
    f.severity.value = String(chosen); // keep the reviewer's choice, not the refreshed suggestion
    if (![1, 2, 3, 4].includes(chosen)) return showError('Select a valid confirmed severity.', f.severity);
    if (result.emergencyLatched && chosen !== 1) {
      return showError('An emergency warning was identified. Critical priority cannot be downgraded.', f.severity);
    }
    if (chosen !== result.severity && !f.overrideReason.value.trim()) {
      return showError('Enter a reason for overriding the suggested severity.', f.overrideReason);
    }
    if (!f.review.checked) return showError('Tick the review checkbox after confirming severity.', f.review);

    const patient = await api('/api/patients', {
      method: 'POST',
      body: {
        ...c,
        name,
        gender: f.gender.value,
        disease: f.disease.value.trim(),
        severity: chosen,
        review: true,
        overrideReason: f.overrideReason.value.trim(),
        draftId: state.draftId,
      },
    });
    state.tab = 'waiting';
    $('#search').value = '';
    $('#filter').value = 'all';
    document.querySelectorAll('[data-tab]').forEach((b) => b.classList.toggle('selected', b.dataset.tab === 'waiting'));
    resetRegistration();
    $('#dialog').close();
    await refresh();
    toast(`${patient.name} added to queue.`);
  } catch (e) {
    showError(e.message);
  } finally {
    submit.disabled = false;
  }
};

// ---------------------------------------------------------------- Startup

(async function init() {
  previewAssessment();
  try {
    const identity = await api('/api/auth/me'); // resume an existing session
    await showDashboard(identity);
  } catch {
    showLogin();
  }
  setInterval(() => { if (state.me) render(); }, 30000);
})();
