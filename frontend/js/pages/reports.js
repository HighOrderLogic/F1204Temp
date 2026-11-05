function formatSendTime(value) {
  if (!value) return '—';
  const raw = String(value);
  // "09:00:00" or "09:00"
  const m = raw.match(/^(\d{1,2}):(\d{2})/);
  if (m) {
    const h = Number(m[1]);
    const min = m[2];
    const ampm = h >= 12 ? 'PM' : 'AM';
    const h12 = ((h + 11) % 12) + 1;
    return `${h12}:${min} ${ampm}`;
  }
  return raw;
}

function formatNextSend(value) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString('en-AU', {
      timeZone: 'Australia/Sydney',
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  } catch (_) {
    return String(value);
  }
}

async function renderReports(container) {
  const farmId = getFarmId();
  const defaultFilename = `${farmId.toLowerCase()}_report`;

  container.innerHTML = `
    <div class="module-header">
      <div>
        <h2>Reports & Email</h2>
        <p>Export consolidated reports or configure automated delivery schedules.</p>
      </div>
    </div>

    <div class="reports-grid">
      <!-- Card 1: On-Demand Dispatch -->
      <div class="card reports-card">
        <div class="reports-card-header">
          <h3><i class="fa fa-paper-plane" aria-hidden="true"></i> On-Demand Dispatch</h3>
          <span class="badge-primary"><i class="fa fa-bolt" aria-hidden="true"></i> Instant</span>
        </div>

        <div class="form-group">
          <label>Charts to Include</label>
          <div class="pill-toggle-group">
            <label class="pill-toggle">
              <input type="checkbox" class="report-chart" value="Summary Statistics" checked>
              <span class="pill-toggle-label"><i class="fa fa-chart-pie" aria-hidden="true"></i> Summary Stats</span>
            </label>
            <label class="pill-toggle">
              <input type="checkbox" class="report-chart" value="Time Series" checked>
              <span class="pill-toggle-label"><i class="fa fa-chart-line" aria-hidden="true"></i> Time Series</span>
            </label>
            <label class="pill-toggle">
              <input type="checkbox" class="report-chart" value="Distribution" checked>
              <span class="pill-toggle-label"><i class="fa fa-chart-bar" aria-hidden="true"></i> Distribution</span>
            </label>
            <label class="pill-toggle">
              <input type="checkbox" class="report-chart" value="Cohorts">
              <span class="pill-toggle-label"><i class="fa fa-layer-group" aria-hidden="true"></i> Cohorts</span>
            </label>
          </div>
        </div>

        <div class="grid-2">
          <div class="form-group">
            <label for="report-filename">Filename</label>
            <input type="text" id="report-filename" value="${defaultFilename}" class="form-control">
          </div>
          <div class="form-group">
            <label for="picker-report-format">Format</label>
            <select id="report-format" class="hidden"><option value="PDF" selected>PDF</option><option value="HTML">HTML</option></select>
            <div id="picker-report-format"></div>
          </div>
        </div>

        <div class="reports-mode-toggle" role="tablist" aria-label="Dispatch mode">
          <button type="button" class="reports-mode-btn active" id="mode-btn-export" role="tab" aria-selected="true">
            <i class="fa fa-file-arrow-down" aria-hidden="true"></i> File Export
          </button>
          <button type="button" class="reports-mode-btn" id="mode-btn-email" role="tab" aria-selected="false">
            <i class="fa fa-envelope" aria-hidden="true"></i> Direct Email
          </button>
        </div>

        <div id="report-email-fields" class="reports-email-section hidden">
          <div class="form-group">
            <label for="email-to">Recipient Email</label>
            <input type="email" id="email-to" placeholder="manager@farm.com" class="form-control">
          </div>
          <div class="form-group">
            <label for="email-subject">Subject</label>
            <input type="text" id="email-subject" value="Automated Livestock Report" class="form-control">
          </div>
          <div class="form-group">
            <label for="email-body">Message</label>
            <textarea id="email-body" class="form-control" rows="2">Please find your livestock performance report attached.</textarea>
          </div>
        </div>

        <div class="reports-card-actions">
          <button class="btn btn-primary" id="generate-report"><i class="fa fa-download" aria-hidden="true"></i> Generate Report</button>
          <button class="btn btn-secondary hidden" id="send-now"><i class="fa fa-paper-plane" aria-hidden="true"></i> Send Email Now</button>
        </div>
      </div>

      <!-- Card 2: Automated Schedules -->
      <div class="card reports-card">
        <div class="reports-card-header">
          <h3><i class="fa fa-calendar-check" aria-hidden="true"></i> Automated Schedules</h3>
          <span class="badge-primary"><i class="fa fa-clock" aria-hidden="true"></i> Recurring</span>
        </div>

        <div class="grid-2">
          <div class="form-group">
            <label for="sched-name">Schedule Name</label>
            <input type="text" id="sched-name" placeholder="Weekly Summary" class="form-control">
          </div>
          <div class="form-group">
            <label for="sched-email">Recipient Email</label>
            <input type="email" id="sched-email" placeholder="manager@farm.com" class="form-control">
          </div>
        </div>

        <div class="grid-2">
          <div class="form-group">
            <label for="picker-sched-freq">Frequency</label>
            <select id="sched-freq" class="hidden">
              <option value="daily">Daily</option>
              <option value="weekly" selected>Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="once">Once</option>
            </select>
            <div id="picker-sched-freq"></div>
          </div>
          <div class="form-group">
            <label for="sched-time">Send Time (Sydney)</label>
            <input type="time" id="sched-time" value="09:00" class="form-control">
          </div>
        </div>

        <div class="grid-2">
          <div class="form-group" id="sched-dow-group">
            <label for="picker-sched-dow">Day of Week</label>
            <select id="sched-dow" class="hidden">
              <option value="1" selected>Monday</option>
              <option value="2">Tuesday</option>
              <option value="3">Wednesday</option>
              <option value="4">Thursday</option>
              <option value="5">Friday</option>
              <option value="6">Saturday</option>
              <option value="7">Sunday</option>
            </select>
            <div id="picker-sched-dow"></div>
          </div>
          <div class="form-group hidden" id="sched-dom-group">
            <label for="sched-dom">Day of Month</label>
            <input type="number" id="sched-dom" class="form-control" min="1" max="28" value="1">
          </div>
        </div>

        <div class="form-actions" style="margin-top:auto;padding-top:16px;">
          <button class="btn btn-primary btn-block" id="create-schedule"><i class="fa fa-plus" aria-hidden="true"></i> Create Schedule</button>
        </div>
      </div>
    </div>

    <!-- Card 3: Active Schedules Telemetry -->
    <div class="card" style="margin-top:16px;">
      <div class="reports-card-header">
        <div>
          <h3><i class="fa fa-clock-rotate-left" aria-hidden="true"></i> Active Schedules</h3>
          <p style="font-size:var(--text-base);color:var(--text-secondary);margin:4px 0 0">
            Showing automated delivery schedules configured across accessible farms.
          </p>
        </div>
      </div>
      <div id="schedules-list" class="table-wrap" style="margin-top:12px;"></div>
    </div>`;

  // Initialize custom dropdowns
  if (typeof createCustomDropdown === 'function') {
    createCustomDropdown({
      container: 'picker-report-format',
      field: 'report_format',
      options: [
        { value: 'PDF', label: 'PDF Document' },
        { value: 'HTML', label: 'HTML Report' },
      ],
      value: 'PDF',
      onChange: (val) => {
        const select = document.getElementById('report-format');
        if (select) select.value = val;
      },
    });

    createCustomDropdown({
      container: 'picker-sched-freq',
      field: 'sched_freq',
      options: [
        { value: 'daily', label: 'Daily' },
        { value: 'weekly', label: 'Weekly' },
        { value: 'monthly', label: 'Monthly' },
        { value: 'once', label: 'Once' },
      ],
      value: 'weekly',
      onChange: (val) => {
        const select = document.getElementById('sched-freq');
        if (select) select.value = val;
        syncScheduleFrequencyFields();
      },
    });

    createCustomDropdown({
      container: 'picker-sched-dow',
      field: 'sched_dow',
      options: [
        { value: '1', label: 'Monday' },
        { value: '2', label: 'Tuesday' },
        { value: '3', label: 'Wednesday' },
        { value: '4', label: 'Thursday' },
        { value: '5', label: 'Friday' },
        { value: '6', label: 'Saturday' },
        { value: '7', label: 'Sunday' },
      ],
      value: '1',
      onChange: (val) => {
        const select = document.getElementById('sched-dow');
        if (select) select.value = val;
      },
    });
  }

  // Dispatch mode switcher
  const modeBtnExport = document.getElementById('mode-btn-export');
  const modeBtnEmail = document.getElementById('mode-btn-email');
  const emailFields = document.getElementById('report-email-fields');
  const btnGenerate = document.getElementById('generate-report');
  const btnSendNow = document.getElementById('send-now');

  const switchMode = (mode) => {
    const isEmail = mode === 'email';
    modeBtnExport?.classList.toggle('active', !isEmail);
    modeBtnExport?.setAttribute('aria-selected', !isEmail ? 'true' : 'false');
    modeBtnEmail?.classList.toggle('active', isEmail);
    modeBtnEmail?.setAttribute('aria-selected', isEmail ? 'true' : 'false');
    emailFields?.classList.toggle('hidden', !isEmail);
    btnGenerate?.classList.toggle('hidden', isEmail);
    btnSendNow?.classList.toggle('hidden', !isEmail);
  };

  modeBtnExport?.addEventListener('click', () => switchMode('export'));
  modeBtnEmail?.addEventListener('click', () => switchMode('email'));

  document.getElementById('generate-report').onclick = generateReport;
  document.getElementById('send-now').onclick = sendNow;
  document.getElementById('create-schedule').onclick = createSchedule;
  syncScheduleFrequencyFields();
  loadSchedules();
}

function syncScheduleFrequencyFields() {
  const freq = document.getElementById('sched-freq')?.value;
  const dow = document.getElementById('sched-dow-group');
  const dom = document.getElementById('sched-dom-group');
  if (dow) dow.style.display = freq === 'weekly' ? 'block' : 'none';
  if (dom) dom.classList.toggle('hidden', freq !== 'monthly');
}

async function generateReport() {
  const farmId = getFarmId();
  const defaultFilename = `${farmId.toLowerCase()}_report`;
  const charts = [...document.querySelectorAll('.report-chart:checked')].map(c => c.value);
  const formatEl = document.getElementById('report-format');
  const body = {
    farm_id: farmId,
    filters: getFilters(),
    filename: document.getElementById('report-filename')?.value || defaultFilename,
    format: formatEl ? formatEl.value : 'PDF',
    charts: charts.length ? charts : ['Summary Statistics'],
  };
  const btn = document.getElementById('generate-report');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<div class="loading-spinner" style="width:14px;height:14px;border-width:2px;border-color:rgba(255,255,255,0.3);border-top-color:#fff;" aria-hidden="true"></div> Generating…';
  }
  try {
    const ext = body.format === 'HTML' ? 'html' : 'pdf';
    await apiPostDownload('/reports/generate', body, `${body.filename}.${ext}`);
    showToast('Report generated', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa fa-download" aria-hidden="true"></i> Generate Report';
    }
  }
}

async function sendNow() {
  const recipient = (document.getElementById('email-to').value || '').trim();
  if (!recipient || !recipient.includes('@')) {
    showToast('Enter a valid recipient email', 'error');
    return;
  }
  const farmId = getFarmId();
  const charts = [...document.querySelectorAll('.report-chart:checked')].map(c => c.value);
  const body = {
    farm_id: farmId,
    recipient_email: recipient,
    email_subject: document.getElementById('email-subject')?.value || `Automated Livestock Report - ${farmNameFor(farmId)}`,
    email_body: document.getElementById('email-body')?.value,
    report_filters: getFilters(),
    report_charts: charts.length ? charts : ['Summary Statistics', 'Distribution'],
    report_format: 'PDF',
  };
  const btn = document.getElementById('send-now');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<div class="loading-spinner" style="width:14px;height:14px;border-width:2px;border-color:rgba(255,255,255,0.3);border-top-color:#fff;" aria-hidden="true"></div> Sending…';
  }
  try {
    const res = await apiFetch('/email/send-now', { method: 'POST', body: JSON.stringify(body) });
    if (res.dry_run) {
      showToast(res.detail || 'Dry-run only — email was not sent', 'warning');
    } else {
      showToast(res.detail || 'Email sent', 'success');
    }
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa fa-paper-plane" aria-hidden="true"></i> Send Email Now';
    }
  }
}

async function createSchedule() {
  const frequency = document.getElementById('sched-freq')?.value || 'weekly';
  const body = {
    farm_id: getFarmId(),
    schedule_name: document.getElementById('sched-name').value || 'Weekly Summary',
    recipient_email: (document.getElementById('sched-email').value || '').trim(),
    frequency,
    send_time: document.getElementById('sched-time').value || '09:00',
    day_of_week: frequency === 'weekly' ? parseInt(document.getElementById('sched-dow').value, 10) : null,
    day_of_month: frequency === 'monthly' ? parseInt(document.getElementById('sched-dom').value, 10) || 1 : null,
    email_subject: 'Automated Livestock Report',
    report_filters: getFilters(),
    report_charts: ['Distribution', 'Summary Statistics'],
    report_format: 'PDF',
  };
  if (!body.recipient_email || !body.recipient_email.includes('@')) {
    showToast('Enter a valid recipient email', 'error');
    return;
  }
  const btn = document.getElementById('create-schedule');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<div class="loading-spinner" style="width:14px;height:14px;border-width:2px;border-color:rgba(255,255,255,0.3);border-top-color:#fff;" aria-hidden="true"></div> Creating…';
  }
  try {
    await apiFetch('/email/schedules', { method: 'POST', body: JSON.stringify(body) });
    showToast('Schedule created', 'success');
    loadSchedules();
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<i class="fa fa-plus" aria-hidden="true"></i> Create Schedule';
    }
  }
}

function farmNameFor(farmId) {
  const farms = getUser()?.farms || [];
  const found = farms.find((f) => f.farm_id === farmId);
  return found?.farm_name || farmId || '—';
}

async function loadSchedules() {
  const list = document.getElementById('schedules-list');
  if (!list) return;
  list.innerHTML = `
    <div class="loading-indicator" style="min-height:90px;padding:16px 0;background:transparent;border:none;">
      <div class="loading-spinner" aria-hidden="true"></div>
      <span class="loading-text" style="font-size:12px;">Loading automated schedules…</span>
    </div>`;
  try {
    // Omit farm_id so admins/owners see schedules for every farm they can access.
    const schedules = await apiFetch('/email/schedules');
    if (!schedules.length) {
      list.innerHTML = '<p style="font-size:var(--text-base);color:var(--text-secondary);margin:8px 0"><i class="fa fa-info-circle" aria-hidden="true"></i> No scheduled emails configured</p>';
      return;
    }
    list.innerHTML = `<table class="data-table"><thead><tr>
      <th>Name</th><th>Farm</th><th>Recipient</th><th>Frequency</th><th>Send Time</th><th>Next Send (Sydney)</th><th>Status</th><th></th>
    </tr></thead>
      <tbody>${schedules.map(s => `<tr>
        <td><strong>${s.schedule_name || '—'}</strong></td>
        <td>${farmNameFor(s.farm_id)}</td>
        <td>${s.recipient_email}</td>
        <td style="text-transform: capitalize;">${s.frequency}</td>
        <td>${formatSendTime(s.send_time)}</td>
        <td>${formatNextSend(s.next_send_at)}</td>
        <td><span class="badge ${s.is_active ? 'badge-active' : 'badge-inactive'}">${s.is_active ? 'Active' : 'Inactive'}</span></td>
        <td>
          <button class="btn btn-sm btn-secondary" onclick="toggleSchedule(${s.id},${!s.is_active})">${s.is_active ? '<i class="fa fa-pause" aria-hidden="true"></i> Pause' : '<i class="fa fa-play" aria-hidden="true"></i> Activate'}</button>
          <button class="btn btn-sm btn-danger" onclick="deleteSchedule(${s.id})" title="Delete"><i class="fa fa-trash" aria-hidden="true"></i></button>
        </td></tr>`).join('')}</tbody></table>`;
  } catch (err) {
    list.innerHTML = `<p class="alert alert-warning">${err.message}</p>`;
  }
}

async function toggleSchedule(id, active) {
  await apiFetch(`/email/schedules/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: active }) });
  loadSchedules();
}

async function deleteSchedule(id) {
  await apiFetch(`/email/schedules/${id}`, { method: 'DELETE' });
  showToast('Schedule deleted', 'success');
  loadSchedules();
}

window.renderReports = renderReports;
window.toggleSchedule = toggleSchedule;
window.deleteSchedule = deleteSchedule;
