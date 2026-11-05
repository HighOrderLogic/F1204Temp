// ============================================================
// Anomalies Page Module (Rate-of-Change Weight Anomaly Engine)
// ============================================================

(function () {
  let cachedAnalysis = null;
  let selectedAnimalId = null;
  let activeAbortController = null;
  let isFetching = false;
  let currentMeasure = 'weight';
  let currentSensitivity = 'Medium';
  let currentSeverityFilter = 'Flagged';
  let currentTypeFilter = 'All';
  /** Per-payload analyze cache so farm switches can reuse a prior warm result. */
  const anomalyAnalyzeCache = Object.create(null);
  const anomalyAnalyzeInflight = Object.create(null);

  const SEVERITY_COLOURS = {
    High: '#7B241C',
    Medium: '#B08968',
    Low: '#95A5A6',
    Normal: '#2D6A4F',
    'Not enough data': '#64748B',
  };

  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatClinicalReason(raw) {
    if (!raw) return 'No anomaly flagged for this animal.';
    let s = escapeHtml(raw);

    // Bold category headings
    s = s.replace(/^(Falling behind the herd:)/gm, '<strong>$1</strong>');
    s = s.replace(/^(Low growth compared with similar animals:)/gm, '<strong>$1</strong>');
    s = s.replace(/^(Single unusual reading:)/gm, '<strong>$1</strong>');
    s = s.replace(/^(Not enough data:)/gm, '<strong>$1</strong>');

    // Bold dates and days
    s = s.replace(/\b(\d{4}-\d{2}-\d{2})\b/g, '<strong>$1</strong>');
    s = s.replace(/\b(Day\s+\d+)\b/g, '<strong>$1</strong>');

    // Bold quantities with units (e.g. 18.0 kg, -0.2 kg/day, 11 days, 28 days, ±2.5 kg, 0.5%)
    s = s.replace(/((?:±\s*)?-?\d+(?:\.\d+)?)\s*(kg\/day|kg|days|%)\b/g, '<strong>$1 $2</strong>');

    // Bold counts with items (e.g. 15 similar animals, 10 readings)
    s = s.replace(/\b(\d+)\s+(similar animals|readings|eligible peers|peers)\b/g, '<strong>$1 $2</strong>');
    s = s.replace(/\b(\d+)\s+(in the first two weeks)\b/g, '<strong>$1</strong> $2');

    // Bold demographic group definitions (e.g. Treatment: T1, Breed: Angus)
    s = s.replace(/\((Treatment:[^)]+)\)/g, '(<strong>$1</strong>)');

    return s;
  }

  function getActiveFilters() {
    const raw = typeof getFilters === 'function' ? getFilters() : {};
    return {
      farm_id: raw.farm_id || getFarmId(),
      year: raw.year || 2026,
      month: raw.month || 'All',
      day: raw.day || 'All',
      sex: raw.sex || ['Overall'],
      treatment: raw.treatment || ['Overall'],
      breed: raw.breed || ['Overall'],
      mob: raw.mob || ['Overall'],
      eid: raw.eid || ['Overall'],
      extra_filters: raw.extra_filters || {},
      active_only: Boolean(raw.active_only),
      active_days: raw.active_days || 30,
      active_reference_mode: raw.active_reference_mode || 'auto',
    };
  }

  function buildAnalyzePayload(overrides = {}) {
    return {
      ...getActiveFilters(),
      weight_col: currentMeasure,
      sensitivity: currentSensitivity,
      ...overrides,
    };
  }

  /** Default post-switch filters for another farm (Overall dims + that farm's year). */
  function payloadForFarm(farmId, yearHint) {
    const boot = typeof getFarmBootCache === 'function' ? getFarmBootCache(farmId) : null;
    const year = yearHint || boot?.choices?.max_year || getActiveFilters().year || 2026;
    return buildAnalyzePayload({
      farm_id: farmId,
      year,
      month: 'All',
      day: 'All',
      sex: ['Overall'],
      treatment: ['Overall'],
      breed: ['Overall'],
      mob: ['Overall'],
      eid: ['Overall'],
      active_only: false,
    });
  }

  async function resolveFarmYear(farmId) {
    const boot = typeof getFarmBootCache === 'function' ? getFarmBootCache(farmId) : null;
    if (boot?.choices?.max_year != null) return boot.choices.max_year;
    if (typeof fetchFarmBoot === 'function') {
      try {
        const measureHint = localStorage.getItem('livestock_last_measure') || 'finalpweight';
        const fresh = await fetchFarmBoot(farmId, measureHint);
        if (fresh?.choices?.max_year != null) return fresh.choices.max_year;
      } catch (_) { /* fall through */ }
    }
    try {
      const res = await apiFetch(
        `/filters/bootstrap?farm_id=${encodeURIComponent(farmId)}&measure=finalpweight`
      );
      const choices = res?.choices || res;
      return choices?.max_year ?? null;
    } catch (_) {
      return null;
    }
  }

  function analysisCacheKey(payload) {
    try {
      return JSON.stringify(payload);
    } catch (_) {
      return String(Date.now());
    }
  }

  function rememberAnalyze(key, data) {
    if (!key || !data) return;
    anomalyAnalyzeCache[key] = data;
    delete anomalyAnalyzeInflight[key];
  }

  function isSnapshotEligiblePayload(payload) {
    if (!payload || payload.active_only) return false;
    if (String(payload.month || 'All') !== 'All' || String(payload.day || 'All') !== 'All') return false;
    const overall = (vals) => {
      if (!Array.isArray(vals) || !vals.length) return true;
      return vals.every((v) => v === 'Overall' || v === 'All');
    };
    return (
      overall(payload.sex) &&
      overall(payload.treatment) &&
      overall(payload.breed) &&
      overall(payload.mob) &&
      overall(payload.eid)
    );
  }

  function anomalyApiBase() {
    if (window.API_URL) return window.API_URL.replace(/\/?$/, '');
    const host = window.location.hostname;
    const port = window.location.port;
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
    if (isLocal && port && port !== '8000') {
      const apiHost = host === '[::1]' ? 'localhost' : host;
      return `http://${apiHost}:8000/api`;
    }
    return '/api';
  }

  /** Prefer light-tier snapshot (browser-cached GET); fall back to heavy /analyze. */
  async function fetchAnomalyAnalyze(payload, signal) {
    if (isSnapshotEligiblePayload(payload)) {
      try {
        const token = sessionStorage.getItem('access_token');
        const headers = {};
        if (token) headers.Authorization = `Bearer ${token}`;
        const qs = new URLSearchParams({
          farm_id: String(payload.farm_id),
          year: String(payload.year),
          month: String(payload.month || 'All'),
          day: String(payload.day || 'All'),
          weight_col: String(payload.weight_col || 'weight'),
          sensitivity: String(payload.sensitivity || 'Medium'),
        });
        const res = await fetch(`${anomalyApiBase()}/anomalies/snapshot?${qs}`, {
          method: 'GET',
          headers,
          signal,
          // Allow the private Cache-Control from the API to speed farm switches.
          cache: 'default',
        });
        if (res.ok) return await res.json();
      } catch (err) {
        if (err && err.name === 'AbortError') throw err;
      }
    }
    return apiFetch('/anomalies/analyze', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal,
    });
  }

  /** Warm analyze for the current filters (or a specific farm) and cache the result. */
  async function prefetchAnomalies(options = {}) {
    if (typeof apiFetch !== 'function' || !sessionStorage.getItem('access_token')) return null;
    const farmId = options.farm_id;
    let payload;
    if (farmId) {
      const year = options.year != null ? options.year : await resolveFarmYear(farmId);
      // KF is 2023 and WOW is 2026 — never prefetch with the other farm's year.
      if (year == null) return null;
      payload = payloadForFarm(farmId, year);
    } else {
      payload = buildAnalyzePayload();
    }
    const key = analysisCacheKey(payload);
    if (anomalyAnalyzeCache[key]) return anomalyAnalyzeCache[key];
    if (anomalyAnalyzeInflight[key]) return anomalyAnalyzeInflight[key];

    anomalyAnalyzeInflight[key] = fetchAnomalyAnalyze(payload)
      .then((data) => {
        rememberAnalyze(key, data);
        return data;
      })
      .catch(() => {
        delete anomalyAnalyzeInflight[key];
        return null;
      });
    return anomalyAnalyzeInflight[key];
  }

  function prefetchAnomaliesForFarm(farmId) {
    if (!farmId) return null;
    return prefetchAnomalies({ farm_id: farmId });
  }

  /** After current farm is warm, prefetch the other farm(s) in the background. */
  function prefetchOtherFarmAnomalies(currentFarmId) {
    const farms = (typeof getUser === 'function' ? getUser()?.farms : null) || [];
    for (const farm of farms) {
      const farmId = farm?.farm_id;
      if (!farmId || farmId === currentFarmId) continue;
      prefetchAnomaliesForFarm(farmId);
    }
  }

  const ANOMALIES_HEADER = `
    <div class="module-header">
      <div>
        <h2>Weight Anomaly Detection</h2>
        <p>Rate-of-change telemetry evaluating <strong>28-day</strong> individual body weight trajectories against demographic peers, herd day shifts, and scale measurement noise.</p>
      </div>
    </div>`;

  function setLoadingState(isLoading, message) {
    const countsStrip = document.getElementById('anomaly-counts-strip');
    const tbody = document.getElementById('anomaly-table-body');
    const chartLoading = document.getElementById('anomaly-charts-loading');
    const spinnerBadge = document.getElementById('anomaly-filter-spinner');

    if (isLoading) {
      if (countsStrip) {
        countsStrip.style.opacity = '0.45';
        countsStrip.style.pointerEvents = 'none';
      }
      ['count-high', 'count-medium', 'count-low', 'count-normal', 'count-insufficient'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.textContent = '…';
      });
      if (tbody) {
        tbody.innerHTML = `
          <tr class="table-loading-row">
            <td colspan="5" style="padding: 32px 16px; text-align: center; color: var(--text-secondary);">
              <div class="loading-indicator" style="display: inline-flex; justify-content: center; align-items: center; gap: 8px;">
                <div class="loading-spinner"></div>
                <span class="loading-text">${escapeHtml(message || 'Updating weight anomalies for selected filters…')}</span>
              </div>
            </td>
          </tr>`;
      }
      if (chartLoading) {
        chartLoading.style.display = 'flex';
      }
      if (spinnerBadge) {
        spinnerBadge.style.display = 'inline-flex';
      }
    } else {
      if (countsStrip) {
        countsStrip.style.opacity = '1';
        countsStrip.style.pointerEvents = 'auto';
      }
      if (chartLoading) {
        chartLoading.style.display = 'none';
      }
      if (spinnerBadge) {
        spinnerBadge.style.display = 'none';
      }
    }
  }

  async function renderAnomalies(container) {
    if (!container) return;

    const user = typeof getUser === 'function' ? getUser() : null;
    const isAdmin = Boolean(typeof canExportRaw === 'function' ? canExportRaw() : user?.is_admin);

    const hasShell = Boolean(document.getElementById('anomaly-table-body'));
    const payload = buildAnalyzePayload();
    const key = analysisCacheKey(payload);
    const warmHit = Boolean(anomalyAnalyzeCache[key]);
    const inflight = anomalyAnalyzeInflight[key];

    if (!hasShell && !warmHit) {
      if (typeof showLoading === 'function') {
        showLoading(container, 'Analyzing weight trajectories and noise calibration…');
      } else {
        container.innerHTML = `<div class="loading-indicator"><div class="loading-spinner"></div><span class="loading-text">Analyzing weight trajectories…</span></div>`;
      }
    } else if (hasShell && !warmHit) {
      setLoadingState(true, 'Updating weight anomalies for selected filters…');
    }

    if (isFetching && activeAbortController) {
      activeAbortController.abort();
    }
    activeAbortController = new AbortController();
    isFetching = true;

    let data;
    try {
      if (warmHit) {
        data = anomalyAnalyzeCache[key];
      } else if (inflight) {
        data = await inflight;
        if (!data) {
          data = await fetchAnomalyAnalyze(payload, activeAbortController.signal);
        }
      } else {
        data = await fetchAnomalyAnalyze(payload, activeAbortController.signal);
      }
      cachedAnalysis = data;
      rememberAnalyze(key, data);
      prefetchOtherFarmAnomalies(payload.farm_id);
    } catch (err) {
      if (err.name === 'AbortError') return;
      setLoadingState(false);
      container.innerHTML = `
        ${ANOMALIES_HEADER}
        <div class="alert alert-warning" style="margin-top: 16px;">
          Failed to load anomaly detection: ${escapeHtml(err.message)}
        </div>`;
      if (typeof showToast === 'function') showToast(err.message, 'error');
      isFetching = false;
      return;
    } finally {
      isFetching = false;
    }

    if (!data.available) {
      setLoadingState(false);
      container.innerHTML = `
        ${ANOMALIES_HEADER}
        <div class="alert alert-muted" style="margin-top: 16px;">
          ${escapeHtml(data.message || 'Anomaly detection is not available for the selected filters.')}
        </div>`;
      return;
    }

    if (hasShell) {
      setLoadingState(false);
      updateHeaderBanners(data);
      updateCountsStrip(data.counts || {});
      applyClientFilters();
      return;
    }

    container.innerHTML = `
      ${ANOMALIES_HEADER}

      <!-- Controls Bar with Custom Pickers -->
      <div class="controls-bar" style="margin-bottom: 16px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <label for="picker-btn-anomaly_weight_measure">Measure</label>
          <div id="picker-anomaly-weight-measure" style="min-width: 250px;"></div>
        </div>

        <div style="display: flex; align-items: center; gap: 8px;">
          <label for="picker-btn-anomaly_sensitivity">Sensitivity</label>
          <div id="picker-anomaly-sensitivity" style="min-width: 140px;"></div>
        </div>

        <div style="display: flex; align-items: center; gap: 8px;">
          <label for="picker-btn-anomaly_severity_filter">Show</label>
          <div id="picker-anomaly-severity-filter" style="min-width: 230px;"></div>
        </div>

        <div style="display: flex; align-items: center; gap: 8px;">
          <label for="picker-btn-anomaly_type_filter">Type</label>
          <div id="picker-anomaly-type-filter" style="min-width: 200px;"></div>
        </div>

        <span id="anomaly-filter-spinner" style="display: none; align-items: center; gap: 6px; font-size: 12px; color: var(--text-secondary); margin-left: 8px;">
          <div class="loading-spinner" style="width: 14px; height: 14px; border-width: 2px;"></div>
          <span>Updating…</span>
        </span>

        <div style="margin-left: auto;">
          ${
            isAdmin
              ? `<button type="button" id="anomaly-export-btn" class="btn btn-secondary btn-sm" style="display: inline-flex; align-items: center; gap: 6px;">
                  <i class="fa fa-download" aria-hidden="true"></i> Download CSV
                 </button>`
              : ''
          }
        </div>
      </div>

      <!-- Diagnostic & Warning Banners -->
      <div id="anomaly-window-banner" style="display: none; padding: 10px 14px; margin-bottom: 12px; font-size: 13px; color: var(--text-primary); background: var(--bg-subtle, #f1f5f9); border: 1px solid var(--border); border-radius: var(--radius, 6px);"></div>
      <div id="anomaly-smoothing-banner" style="display: none; padding: 10px 14px; margin-bottom: 12px; font-size: 13px; color: #78350f; background: #fef3c7; border: 1px solid #fde68a; border-radius: var(--radius, 6px);"></div>
      <div id="anomaly-herd-banner" style="display: none; padding: 10px 14px; margin-bottom: 12px; font-size: 13px; color: #1e3a8a; background: #dbeafe; border: 1px solid #bfdbfe; border-radius: var(--radius, 6px);"></div>

      <!-- KPI Summary Cards Strip -->
      <div id="anomaly-counts-strip" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px;">
        <div class="card" style="padding: 12px; text-align: center; border-radius: var(--radius, 6px); border: 1px solid var(--border); background: var(--card-bg, #ffffff);">
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">High severity</div>
          <div id="count-high" style="font-size: 24px; font-weight: 700; color: #7B241C;">-</div>
        </div>
        <div class="card" style="padding: 12px; text-align: center; border-radius: var(--radius, 6px); border: 1px solid var(--border); background: var(--card-bg, #ffffff);">
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">Medium severity</div>
          <div id="count-medium" style="font-size: 24px; font-weight: 700; color: #B08968;">-</div>
        </div>
        <div class="card" style="padding: 12px; text-align: center; border-radius: var(--radius, 6px); border: 1px solid var(--border); background: var(--card-bg, #ffffff);">
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">Low severity</div>
          <div id="count-low" style="font-size: 24px; font-weight: 700; color: #95A5A6;">-</div>
        </div>
        <div class="card" style="padding: 12px; text-align: center; border-radius: var(--radius, 6px); border: 1px solid var(--border); background: var(--card-bg, #ffffff);">
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">Normal</div>
          <div id="count-normal" style="font-size: 24px; font-weight: 700; color: #2D6A4F;">-</div>
        </div>
        <div class="card" style="padding: 12px; text-align: center; border-radius: var(--radius, 6px); border: 1px solid var(--border); background: var(--card-bg, #ffffff);">
          <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 4px;">Not enough data</div>
          <div id="count-insufficient" style="font-size: 24px; font-weight: 700; color: #64748B;">-</div>
        </div>
      </div>

      <!-- Main Layout: Table Card + Detail Card -->
      <div style="display: grid; grid-template-columns: 1fr; gap: 16px;">
        <!-- Animals Table Card -->
        <div class="chart-card" id="anomaly-table-card">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <h3 style="margin: 0;">Ranked Anomaly Table</h3>
            <span id="anomaly-table-subtitle" style="font-size: 12px; color: var(--text-secondary);"></span>
          </div>

          <div class="table-wrap" style="overflow-x: auto; max-height: 380px; border: 1px solid var(--border); border-radius: var(--radius, 6px);">
            <table class="data-table">
              <thead style="position: sticky; top: 0; z-index: 10;">
                <tr>
                  <th>Animal</th>
                  <th>Severity</th>
                  <th>Flagged for</th>
                  <th>7-day change</th>
                  <th>Readings</th>
                </tr>
              </thead>
              <tbody id="anomaly-table-body"></tbody>
            </table>
          </div>

          <details style="margin-top: 14px; font-size: 12px; color: var(--text-secondary); background: var(--bg-subtle, #f8fafc); border: 1px solid var(--border); border-radius: var(--radius, 6px); padding: 8px 12px;">
            <summary style="cursor: pointer; font-weight: 500; outline: none; user-select: none;">How severity is calculated</summary>
            <div style="margin-top: 8px; line-height: 1.5;">
              <p style="margin: 0 0 6px 0;"><strong>High (Sustained weight loss):</strong> Animal weight falls below its <strong>first-two-week baseline</strong> for monitored days in a row, with downward CUSUM exceeding the <strong>upload-calibrated limit</strong> (<strong>&alpha; &le; 0.02</strong>).</p>
              <p style="margin: 0 0 6px 0;"><strong>Medium (Below-peer growth):</strong> Month-long least-squares growth rate is significantly below demographic peers (matched hierarchically by <strong>treatment &rarr; mob &rarr; breed &rarr; sex</strong>; robust <strong>Z &lt; -2.5</strong>).</p>
              <p style="margin: 0 0 6px 0;"><strong>Low (Isolated scale drop):</strong> One daily reading falls into the lowest empirical noise quantiles (<strong>q &le; 0.001</strong>), while month-long growth stayed within normal range.</p>
              <p style="margin: 0 0 4px 0;"><strong>Normal:</strong> Animal meets the <strong>&ge; 10 readings</strong> / <strong>&ge; 21 days</strong> criteria and triggered none of the three thresholds.</p>
              <p style="margin: 0;"><strong>Not enough data:</strong> Fewer than <strong>10 readings</strong> or span under <strong>21 days</strong> across the 28-day window.</p>
            </div>
          </details>
        </div>

        <!-- Selected Animal Detail Card -->
        <div id="anomaly-detail-card" class="chart-card">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <h3 id="anomaly-detail-title" style="margin: 0;">Selected Animal Detail</h3>
                <span id="anomaly-detail-badge" class="anomaly-badge" style="display: none;"></span>
              </div>
              <div id="anomaly-detail-reason" style="margin-top: 6px; font-size: 13px; color: var(--text-primary); line-height: 1.45; white-space: pre-line;"></div>
            </div>
          </div>

          <!-- Charts Container with Loading State Wrapper -->
          <div id="anomaly-charts-wrapper" style="position: relative;">
            <div id="anomaly-charts-loading" style="display: none; position: absolute; inset: 0; background: rgba(255, 255, 255, 0.75); z-index: 5; align-items: center; justify-content: center;">
              <div class="loading-indicator"><div class="loading-spinner"></div><span class="loading-text">Loading animal trajectory…</span></div>
            </div>

            <div style="display: grid; grid-template-columns: 1fr; gap: 16px;">
              <div class="anomaly-chart-panel">
                <div style="font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px;">Weight Trajectory & Baseline</div>
                <div id="anomaly-weight-chart" class="chart-canvas" style="min-height: 280px; width: 100%;"></div>
              </div>
              <div class="anomaly-chart-panel">
                <div style="font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px;">Downward CUSUM (Weeks 3–4 Monitoring)</div>
                <div id="anomaly-cusum-chart" class="chart-canvas" style="min-height: 220px; width: 100%;"></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    initCustomDropdowns();
    updateHeaderBanners(data);
    updateCountsStrip(data.counts || {});
    applyClientFilters();

    const exportBtn = document.getElementById('anomaly-export-btn');
    exportBtn?.addEventListener('click', handleExportCsv);
  }

  function initCustomDropdowns() {
    if (typeof createCustomDropdown !== 'function') {
      console.warn('createCustomDropdown helper is not available.');
      return;
    }

    createCustomDropdown({
      container: document.getElementById('picker-anomaly-weight-measure'),
      field: 'anomaly_weight_measure',
      options: [
        { value: 'weight', label: 'Observed weight (recommended)' },
        { value: 'pweight', label: 'Smoothed weight (pweight)' },
      ],
      value: currentMeasure,
      onChange: (val) => {
        if (!val || val === currentMeasure) return;
        currentMeasure = val;
        reanalyzeWithLoader();
      },
    });

    createCustomDropdown({
      container: document.getElementById('picker-anomaly-sensitivity'),
      field: 'anomaly_sensitivity',
      options: [
        { value: 'Medium', label: 'Medium' },
        { value: 'High', label: 'High' },
        { value: 'Low', label: 'Low' },
      ],
      value: currentSensitivity,
      onChange: (val) => {
        if (!val || val === currentSensitivity) return;
        currentSensitivity = val;
        reanalyzeWithLoader();
      },
    });

    createCustomDropdown({
      container: document.getElementById('picker-anomaly-severity-filter'),
      field: 'anomaly_severity_filter',
      options: [
        { value: 'Flagged', label: 'Flagged (High, Medium, Low)' },
        { value: 'High', label: 'High only' },
        { value: 'Medium', label: 'Medium only' },
        { value: 'Low', label: 'Low only' },
        { value: 'Normal', label: 'Normal' },
        { value: 'Not enough data', label: 'Not enough data' },
        { value: 'All', label: 'All animals' },
      ],
      value: currentSeverityFilter,
      onChange: (val) => {
        currentSeverityFilter = val || 'Flagged';
        applyClientFilters();
      },
    });

    createCustomDropdown({
      container: document.getElementById('picker-anomaly-type-filter'),
      field: 'anomaly_type_filter',
      options: [
        { value: 'All', label: 'All anomaly types' },
        { value: 'Sustained decline', label: 'Sustained decline' },
        { value: 'Low growth vs peers', label: 'Low growth vs peers' },
        { value: 'Single unusual reading', label: 'Single unusual reading' },
      ],
      value: currentTypeFilter,
      onChange: (val) => {
        currentTypeFilter = val || 'All';
        applyClientFilters();
      },
    });
  }

  async function reanalyzeWithLoader() {
    setLoadingState(true, `Re-evaluating anomaly detection for ${currentMeasure} (${currentSensitivity})…`);

    const filters = getActiveFilters();
    const payload = {
      ...filters,
      weight_col: currentMeasure,
      sensitivity: currentSensitivity,
    };

    if (isFetching && activeAbortController) {
      activeAbortController.abort();
    }
    activeAbortController = new AbortController();
    isFetching = true;

    try {
      const data = await fetchAnomalyAnalyze(payload, activeAbortController.signal);

      cachedAnalysis = data;
      rememberAnalyze(analysisCacheKey(payload), data);
      prefetchOtherFarmAnomalies(payload.farm_id);
      setLoadingState(false);
      updateHeaderBanners(data);
      updateCountsStrip(data.counts || {});
      applyClientFilters();
    } catch (err) {
      if (err.name === 'AbortError') return;
      setLoadingState(false);
      const tbody = document.getElementById('anomaly-table-body');
      if (tbody) {
        tbody.innerHTML = `<tr><td colspan="5" style="padding: 24px; text-align: center; color: #dc2626;">Failed: ${escapeHtml(err.message)}</td></tr>`;
      }
      if (typeof showToast === 'function') showToast(err.message, 'error');
    } finally {
      isFetching = false;
      setLoadingState(false);
    }
  }

  async function handleExportCsv() {
    const filters = getActiveFilters();
    const payload = {
      ...filters,
      weight_col: currentMeasure,
      sensitivity: currentSensitivity,
    };

    const filename = `anomalies_${filters.farm_id}_${currentMeasure}_${currentSensitivity.toLowerCase()}.csv`;
    try {
      if (typeof apiPostDownload === 'function') {
        await apiPostDownload('/anomalies/export.csv', payload, filename);
      }
    } catch (err) {
      if (typeof showToast === 'function') showToast(err.message, 'error');
    }
  }

  function updateHeaderBanners(data) {
    const windowBanner = document.getElementById('anomaly-window-banner');
    const smoothingBanner = document.getElementById('anomaly-smoothing-banner');
    const herdBanner = document.getElementById('anomaly-herd-banner');

    if (windowBanner) {
      if (data.available && data.window_start && data.window_end) {
        const scopeText = data.scope === 'selected' ? 'all selected animals' : `whole farm (${data.n_reference} animals)`;
        const sigmaText = data.sigma != null ? ` &bull; <strong>Day-to-day noise:</strong> <strong>±${data.sigma} kg</strong>` : '';
        windowBanner.innerHTML = `<strong>28-day window:</strong> <strong>${escapeHtml(data.window_start)}</strong> to <strong>${escapeHtml(data.window_end)}</strong> (<strong>${escapeHtml(data.period_label || 'Current Period')}</strong>) &bull; <strong>Reference cohort:</strong> <strong>${escapeHtml(scopeText)}</strong>${sigmaText}`;
        windowBanner.style.display = 'block';
      } else {
        windowBanner.style.display = 'none';
      }
    }

    if (smoothingBanner) {
      if (currentMeasure === 'pweight') {
        smoothingBanner.innerHTML =
          '<strong>Smoothed weight (pweight):</strong> pweight is smoothed by the data provider. The sensitivity settings were calibrated on <strong>observed weight</strong>; smoothed values are correlated from day to day, so expect noticeably more animals flagged <strong>High</strong> than the chosen sensitivity suggests, and single-reading flags mean little. Use <strong>observed weight</strong> for calibrated telemetry.';
        smoothingBanner.style.display = 'block';
      } else {
        smoothingBanner.style.display = 'none';
      }
    }

    if (herdBanner) {
      if (data.herd_shift_note) {
        herdBanner.innerHTML = formatClinicalReason(data.herd_shift_note);
        herdBanner.style.display = 'block';
      } else {
        herdBanner.style.display = 'none';
      }
    }
  }

  function updateCountsStrip(counts) {
    const ch = document.getElementById('count-high');
    const cm = document.getElementById('count-medium');
    const cl = document.getElementById('count-low');
    const cn = document.getElementById('count-normal');
    const ci = document.getElementById('count-insufficient');

    if (ch) ch.textContent = counts.high != null ? Number(counts.high).toLocaleString() : '0';
    if (cm) cm.textContent = counts.medium != null ? Number(counts.medium).toLocaleString() : '0';
    if (cl) cl.textContent = counts.low != null ? Number(counts.low).toLocaleString() : '0';
    if (cn) cn.textContent = counts.normal != null ? Number(counts.normal).toLocaleString() : '0';
    if (ci) ci.textContent = counts.insufficient != null ? Number(counts.insufficient).toLocaleString() : '0';
  }

  function applyClientFilters() {
    if (!cachedAnalysis || !Array.isArray(cachedAnalysis.animals)) return;

    const allAnimals = cachedAnalysis.animals;

    const filtered = allAnimals.filter((a) => {
      // Severity filter
      if (currentSeverityFilter === 'Flagged' && !['High', 'Medium', 'Low'].includes(a.severity)) {
        return false;
      }
      if (['High', 'Medium', 'Low', 'Normal', 'Not enough data'].includes(currentSeverityFilter) && a.severity !== currentSeverityFilter) {
        return false;
      }

      // Type filter
      if (currentTypeFilter !== 'All') {
        const types = String(a.anomaly_types || '');
        if (!types.includes(currentTypeFilter)) return false;
      }

      return true;
    });

    const subEl = document.getElementById('anomaly-table-subtitle');
    if (subEl) {
      const total = allAnimals.length;
      const count = filtered.length;
      subEl.textContent = `Showing ${count.toLocaleString()} of ${total.toLocaleString()} animals (${currentSeverityFilter})`;
    }

    renderTableBody(filtered);

    // Auto-select first matching animal or clear detail
    if (filtered.length > 0) {
      const match = filtered.find((a) => a.eid === selectedAnimalId);
      const targetEid = match ? match.eid : filtered[0].eid;
      selectAnimal(targetEid, filtered);
    } else {
      clearDetailView();
    }
  }

  function renderTableBody(animals) {
    const tbody = document.getElementById('anomaly-table-body');
    if (!tbody) return;

    if (!animals || animals.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="5" style="text-align: center; padding: 28px; color: var(--text-secondary);">
            No animals match the selected filters.
          </td>
        </tr>`;
      return;
    }

    tbody.innerHTML = animals
      .map((a) => {
        const sevClass = SEVERITY_COLOURS[a.severity] || '#64748B';
        const isSelected = a.eid === selectedAnimalId;
        const rowBg = isSelected ? 'background: var(--bg-subtle, #f1f5f9);' : '';

        const chg7d =
          a.change_kg_7d != null
            ? `${a.change_kg_7d > 0 ? '+' : ''}${a.change_kg_7d.toFixed(1)} kg (${a.change_pct_7d > 0 ? '+' : ''}${a.change_pct_7d.toFixed(1)}%)`
            : '-';

        return `
          <tr data-eid="${escapeHtml(a.eid)}" style="cursor: pointer; ${rowBg}">
            <td style="font-weight: 600;">${escapeHtml(a.eid)}</td>
            <td>
              <span class="anomaly-badge" style="background: ${sevClass};">
                ${escapeHtml(a.severity)}
              </span>
            </td>
            <td>${escapeHtml(a.anomaly_types || 'None')}</td>
            <td>${escapeHtml(chg7d)}</td>
            <td>${escapeHtml(a.n_readings ?? '-')}</td>
          </tr>
        `;
      })
      .join('');

    tbody.querySelectorAll('tr[data-eid]').forEach((row) => {
      row.addEventListener('click', () => {
        const eid = row.dataset.eid;
        selectAnimal(eid, animals);
      });
    });
  }

  async function selectAnimal(eid, animalsList) {
    selectedAnimalId = eid;

    // Highlight selected row
    const tbody = document.getElementById('anomaly-table-body');
    tbody?.querySelectorAll('tr[data-eid]').forEach((row) => {
      row.style.background = row.dataset.eid === eid ? 'var(--bg-subtle, #f1f5f9)' : 'transparent';
    });

    const animalData = (animalsList || cachedAnalysis?.animals || []).find((a) => a.eid === eid);
    const titleEl = document.getElementById('anomaly-detail-title');
    const badgeEl = document.getElementById('anomaly-detail-badge');
    const reasonEl = document.getElementById('anomaly-detail-reason');
    const chartLoading = document.getElementById('anomaly-charts-loading');

    if (titleEl) titleEl.textContent = `Animal Detail: ${eid}`;
    if (badgeEl && animalData) {
      badgeEl.textContent = animalData.severity;
      badgeEl.style.background = SEVERITY_COLOURS[animalData.severity] || '#64748B';
      badgeEl.style.display = 'inline-block';
    }
    if (reasonEl && animalData) {
      reasonEl.innerHTML = formatClinicalReason(animalData.reason || 'No anomaly flagged for this animal.');
    }

    if (chartLoading) chartLoading.style.display = 'flex';

    const filters = getActiveFilters();
    const payload = {
      ...filters,
      animal: eid,
      weight_col: currentMeasure,
      sensitivity: currentSensitivity,
    };

    try {
      const detail = await apiFetch('/anomalies/detail', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      renderDetailCharts(detail, animalData);
    } catch (err) {
      console.warn('Failed to load detail for animal', eid, err);
    } finally {
      if (chartLoading) chartLoading.style.display = 'none';
    }
  }

  function clearDetailView() {
    selectedAnimalId = null;
    const titleEl = document.getElementById('anomaly-detail-title');
    const badgeEl = document.getElementById('anomaly-detail-badge');
    const reasonEl = document.getElementById('anomaly-detail-reason');

    if (titleEl) titleEl.textContent = 'Selected Animal Detail';
    if (badgeEl) badgeEl.style.display = 'none';
    if (reasonEl) reasonEl.textContent = 'No animal selected.';

    if (window.Plotly) {
      window.Plotly.purge('anomaly-weight-chart');
      window.Plotly.purge('anomaly-cusum-chart');
    }
  }

  function renderDetailCharts(detail, animalData) {
    if (!window.Plotly || !detail || !detail.daily) return;

    const daily = detail.daily;
    const dates = daily.map((d) => d.date);
    const weights = daily.map((d) => d.weight);
    const adjs = daily.map((d) => d.adj);
    const avg7ds = daily.map((d) => d.avg_7d);
    const expected = daily.map((d) => d.expected);

    // Panel 1: Weight Trajectory Traces
    const traces1 = [
      {
        x: dates,
        y: weights,
        mode: 'lines+markers',
        name: 'Observed weight',
        line: { color: '#94a3b8', dash: 'dot', width: 1.5 },
        marker: { size: 5, color: '#94a3b8' },
      },
      {
        x: dates,
        y: adjs,
        mode: 'lines+markers',
        name: 'Herd-adjusted weight',
        line: { color: '#1e293b', width: 2 },
        marker: { size: 5, color: '#1e293b' },
      },
    ];

    if (avg7ds.some((v) => v != null)) {
      traces1.push({
        x: dates,
        y: avg7ds,
        mode: 'lines',
        name: '7-day rolling median',
        line: { color: '#64748b', dash: 'dash', width: 1.5 },
      });
    }

    if (expected.some((v) => v != null)) {
      traces1.push({
        x: dates,
        y: expected,
        mode: 'lines',
        name: 'Baseline / Expected',
        line: { color: '#2D6A4F', dash: 'dashdot', width: 1.5 },
      });
    }

    if (detail.point_drop && detail.point_drop.date) {
      traces1.push({
        x: [detail.point_drop.date],
        y: [detail.point_drop.weight],
        mode: 'markers',
        name: 'Unusual low reading',
        marker: { size: 10, color: '#B08968', symbol: 'diamond' },
      });
    }

    const shapes1 = [];
    if (detail.baseline_weeks != null && detail.baseline_weeks.start && detail.baseline_weeks.end) {
      shapes1.push({
        type: 'rect',
        xref: 'x',
        yref: 'paper',
        x0: detail.baseline_weeks.start,
        x1: detail.baseline_weeks.end,
        y0: 0,
        y1: 1,
        fillcolor: 'rgba(45, 106, 79, 0.08)',
        line: { width: 0 },
      });
    }

    if (detail.run_bounds != null && detail.run_bounds.start && detail.run_bounds.end) {
      shapes1.push({
        type: 'rect',
        xref: 'x',
        yref: 'paper',
        x0: detail.run_bounds.start,
        x1: detail.run_bounds.end,
        y0: 0,
        y1: 1,
        fillcolor: 'rgba(123, 36, 28, 0.12)',
        line: { width: 0 },
      });
    }

    const layout1 = {
      margin: { l: 50, r: 25, t: 20, b: 35 },
      legend: { orientation: 'h', y: -0.25, x: 0 },
      xaxis: { type: 'date', title: 'Date', showgrid: true, gridcolor: '#f1f5f9', zeroline: false },
      yaxis: { title: 'Weight (kg)', showgrid: true, gridcolor: '#f1f5f9', zeroline: false },
      shapes: shapes1,
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      modebar: {
        bgcolor: 'transparent',
        color: '#64748B',
        activecolor: '#1B4332',
      },
    };

    // Clean floating Plotly toolbar on hover without solid gray box, matching prediction/cohorts
    const plotlyConfig = {
      responsive: true,
      displayModeBar: 'hover',
      displaylogo: false,
    };

    window.Plotly.newPlot('anomaly-weight-chart', traces1, layout1, plotlyConfig);

    // Panel 2: CUSUM Trajectory Chart
    const cusumData = detail.cusum_path || [];
    const cDates = cusumData.map((c) => c.date);
    const cVals = cusumData.map((c) => c.cusum);
    const threshold = detail.cusum_threshold;

    const barColors = cVals.map((v) => (threshold != null && v > threshold ? '#7B241C' : '#64748B'));

    const traces2 = [
      {
        x: cDates,
        y: cVals,
        type: 'bar',
        name: 'CUSUM score',
        marker: { color: barColors },
      },
    ];

    const shapes2 = [];
    if (threshold != null) {
      shapes2.push({
        type: 'line',
        xref: 'paper',
        yref: 'y',
        x0: 0,
        x1: 1,
        y0: threshold,
        y1: threshold,
        line: { color: '#7B241C', dash: 'dash', width: 2 },
      });
    }

    const layout2 = {
      margin: { l: 50, r: 25, t: 20, b: 35 },
      xaxis: { type: 'date', title: 'Date', showgrid: true, gridcolor: '#f1f5f9' },
      yaxis: { title: 'CUSUM (S_t)', showgrid: true, gridcolor: '#f1f5f9' },
      shapes: shapes2,
      paper_bgcolor: 'transparent',
      plot_bgcolor: 'transparent',
      modebar: {
        bgcolor: 'transparent',
        color: '#64748B',
        activecolor: '#1B4332',
      },
    };

    window.Plotly.newPlot('anomaly-cusum-chart', traces2, layout2, plotlyConfig);
  }

  window.renderAnomalies = renderAnomalies;
  window.prefetchAnomalies = prefetchAnomalies;
  window.prefetchAnomaliesForFarm = prefetchAnomaliesForFarm;
  window.prefetchOtherFarmAnomalies = prefetchOtherFarmAnomalies;
})();
