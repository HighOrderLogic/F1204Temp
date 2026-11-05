let summaryRequestId = 0;

function fullGroupLabel(combo, isAdmin) {
  const parts = ['sex', 'treatment', 'breed', 'mob'].map(
    (col) => `${grainFriendlyLabel(col)}: ${combo[col] || 'Overall'}`
  );
  parts.push(`EID: ${isAdmin ? (combo.eid || 'Overall') : '*****'}`);
  return parts.join(', ');
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

function formatDayLabel(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return `${pad2(d)}/${pad2(m)}/${y}`;
}

function addDaysIso(isoDate, deltaDays) {
  const dt = new Date(`${isoDate}T00:00:00Z`);
  dt.setUTCDate(dt.getUTCDate() + deltaDays);
  return dt.toISOString().slice(0, 10);
}

function kpiFromRows(rows, label, unit) {
  if (!rows.length) {
    return { mean: 0, min: 0, max: 0, median: 0, count: 0, unit, label };
  }
  let totalC = 0;
  let wsum = 0;
  let minV = null;
  let maxV = null;
  const dailyMeans = [];
  for (const r of rows) {
    const c = r.c | 0;
    const v = +r.v;
    totalC += c;
    wsum += v * c;
    dailyMeans.push(v);
    const mn = r.mn != null ? +r.mn : v;
    const mx = r.mx != null ? +r.mx : v;
    minV = minV == null ? mn : Math.min(minV, mn);
    maxV = maxV == null ? mx : Math.max(maxV, mx);
  }
  dailyMeans.sort((a, b) => a - b);
  const mid = Math.floor(dailyMeans.length / 2);
  let med = 0;
  if (dailyMeans.length) {
    med = dailyMeans.length % 2
      ? dailyMeans[mid]
      : (dailyMeans[mid - 1] + dailyMeans[mid]) / 2;
  }
  return {
    mean: totalC ? Math.round((wsum / totalC) * 100) / 100 : 0,
    min: Math.round((minV || 0) * 100) / 100,
    max: Math.round((maxV || 0) * 100) / 100,
    median: Math.round(med * 100) / 100,
    count: totalC,
    unit,
    label,
  };
}

function assembleSummaryFromGrain(grain, filters) {
  if (typeof filterGrainByMonthDay === 'function') {
    grain = filterGrainByMonthDay(grain, filters);
  }
  const isAdmin = !!(typeof canViewEid === 'function' ? canViewEid() : getUser()?.is_admin);
  const dims = {
    sex: expandFilterDim(filters.sex),
    treatment: expandFilterDim(filters.treatment),
    breed: expandFilterDim(filters.breed),
    mob: expandFilterDim(filters.mob),
    eid: ['Overall'],
  };
  const allOverall = Object.values(dims).every((v) => v.length === 1 && v[0] === 'Overall');
  const combos = [];
  for (const sex of dims.sex) {
    for (const treatment of dims.treatment) {
      for (const breed of dims.breed) {
        for (const mob of dims.mob) {
          combos.push({ sex, treatment, breed, mob, eid: 'Overall' });
        }
      }
    }
  }

  const groups = [];
  for (const combo of combos) {
    const matched = grain.filter((row) => grainRowMatchesCombo(row, combo));
    if (!matched.length) continue;
    const maxD = matched.reduce((m, r) => (r.d > m ? r.d : m), matched[0].d);
    const start15 = addDaysIso(maxD, -14);
    const start31 = addDaysIso(maxD, -30);
    const lastDay = matched.filter((r) => r.d === maxD);
    const last15 = matched.filter((r) => r.d >= start15);
    const last31 = matched.filter((r) => r.d >= start31);
    groups.push({
      full_group: fullGroupLabel(combo, isAdmin),
      windows: {
        last_day: kpiFromRows(lastDay, `Last Day (${formatDayLabel(maxD)})`, ''),
        last_15_days: kpiFromRows(last15, 'Last 15 Days', ''),
        last_month: kpiFromRows(last31, 'Last Month', ''),
        overall: kpiFromRows(matched, 'Overall', ''),
      },
    });
  }

  if (groups.length === 1 && allOverall) {
    groups[0].full_group = fullGroupLabel(
      { sex: 'Overall', treatment: 'Overall', breed: 'Overall', mob: 'Overall', eid: 'Overall' },
      isAdmin,
    );
  }

  const recordCount = grain.reduce((sum, r) => sum + (r.c | 0), 0);
  return { groups, record_count: recordCount };
}

function paintSummary(container, data) {
  if (typeof updateRecordCountBadge === 'function' && data.record_count != null) {
    updateRecordCountBadge(data.record_count);
  }
  if (!data.groups || !data.groups.length || data.record_count === 0) {
    showEmptyState(container, getFilters());
    return;
  }
  const filters = getFilters();
  const measures = (filters.measures && filters.measures.length) ? filters.measures : [filters.measure || 'finalpweight'];
  const activeMeasure = (window.__activeSummaryMeasure && measures.includes(window.__activeSummaryMeasure))
    ? window.__activeSummaryMeasure
    : measures[0];
  window.__activeSummaryMeasure = activeMeasure;

  let subTabsHtml = '';
  if (measures.length > 1) {
    subTabsHtml = `
      <div class="measure-sub-tabs" role="tablist" aria-label="Measure sub-tabs">
        ${measures.map(m => `
          <button type="button" class="measure-sub-tab ${m === activeMeasure ? 'active' : ''}" data-measure="${m}" role="tab" aria-selected="${m === activeMeasure ? 'true' : 'false'}">
            ${typeof getMeasureLabel === 'function' ? getMeasureLabel(m) : m}
          </button>
        `).join('')}
      </div>`;
  }

  let html = `
    <div class="module-header">
      <div>
        <h2>Summary Statistics</h2>
        <p>Key performance indicators and parametric summary metrics partitioned by active cohort filters.</p>
      </div>
    </div>
    ${subTabsHtml}`;
  for (const group of data.groups) {
    html += `<h3 class="group-header">${group.full_group}</h3><div class="kpi-row">`;
    for (const w of Object.values(group.windows)) {
      html += `
        <div class="kpi-card">
          <div class="kpi-label">${w.label}</div>
          <div class="kpi-value">${formatValue(w.mean, activeMeasure)}</div>
          <div class="kpi-sublabel">Mean</div>
          <hr>
          <div class="kpi-stats">
            Min: ${formatValue(w.min, activeMeasure)}<br>
            Max: ${formatValue(w.max, activeMeasure)}<br>
            Median: ${formatValue(w.median, activeMeasure)}<br>
            Count: ${w.count}
          </div>
        </div>`;
    }
    html += '</div>';
  }
  container.innerHTML = html;

  container.querySelectorAll('.measure-sub-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const m = btn.dataset.measure;
      window.__activeSummaryMeasure = m;
      renderSummary(container, { targetMeasure: m });
    });
  });
}

async function renderSummary(container, options = {}) {
  const dimOnly = !!options.dimOnly;
  let baseFilters = getFilters();
  if (!baseFilters.year) {
    const choicesObj = typeof choices !== 'undefined' ? choices : null;
    if (choicesObj?.max_year) {
      if (typeof filterState !== 'undefined') {
        filterState.year = choicesObj.max_year;
      }
      baseFilters = getFilters();
    } else {
      container.innerHTML = '<div class="alert alert-muted">Loading filters…</div>';
      return;
    }
  }

  const measures = (baseFilters.measures && baseFilters.measures.length) ? baseFilters.measures : [baseFilters.measure || 'finalpweight'];
  const targetMeasure = options.targetMeasure || (window.__activeSummaryMeasure && measures.includes(window.__activeSummaryMeasure) ? window.__activeSummaryMeasure : measures[0]);
  window.__activeSummaryMeasure = targetMeasure;

  const filters = { ...baseFilters, measure: targetMeasure };

  const eidActive = !!((typeof canViewEid === 'function' ? canViewEid() : getUser()?.is_admin) && (filters.eid || ['Overall']).some((v) => v !== 'Overall'));
  const cacheMap = typeof getScopeGrainCacheMap === 'function' ? getScopeGrainCacheMap() : null;
  const targetKey = typeof tsScopeKey === 'function' ? tsScopeKey(filters) : null;
  const cache = (cacheMap && targetKey) ? cacheMap[targetKey] : (typeof getScopeGrainCache === 'function' ? getScopeGrainCache() : null);
  const scopeOk = cache && cache.key === targetKey;

  // Checkbox / cached grain path.
  if (!eidActive && (dimOnly || scopeOk) && cache && scopeOk) {
    paintSummary(container, assembleSummaryFromGrain(cache.grain, filters));
    return;
  }

  // Instant first paint from /filters/bootstrap summary (no grain wait) if target measure matches boot.
  const boot = window.__bootSummary;
  const bootOk = !eidActive && !dimOnly && !filters.active_only && !hasActiveExtraFilters(filters) && boot && boot.groups &&
    filters.measure === (localStorage.getItem('livestock_last_measure') || 'finalpweight') &&
    filters.month === 'All' && String(filters.day) === 'All' &&
    (filters.sex || ['Overall']).every((v) => v === 'Overall') &&
    (filters.treatment || ['Overall']).every((v) => v === 'Overall') &&
    (filters.breed || ['Overall']).every((v) => v === 'Overall') &&
    (filters.mob || ['Overall']).every((v) => v === 'Overall');
  if (bootOk) {
    paintSummary(container, boot);
    window.__bootSummary = null; // one-shot
    if (typeof ensureScopeGrain === 'function') ensureScopeGrain(filters).catch(() => {});
    return;
  }

  const reqId = ++summaryRequestId;
  if (!dimOnly || !container.querySelector('.kpi-card')) {
    showLoading(container);
  }

  try {
    let data;
    if (eidActive) {
      data = await apiFetch('/summary/stats', { method: 'POST', body: JSON.stringify(filters) });
    } else {
      await ensureScopeGrain(filters);
      if (reqId !== summaryRequestId) return;
      if (typeof scheduleMeasureGrainPrefetch === 'function') scheduleMeasureGrainPrefetch();
      const freshMap = typeof getScopeGrainCacheMap === 'function' ? getScopeGrainCacheMap() : null;
      const fresh = (freshMap && targetKey && freshMap[targetKey]) || (typeof getScopeGrainCache === 'function' ? getScopeGrainCache() : null);
      data = assembleSummaryFromGrain(fresh ? fresh.grain : [], filters);
    }
    if (reqId !== summaryRequestId) return;
    paintSummary(container, data);
  } catch (err) {
    if (reqId !== summaryRequestId) return;
    container.innerHTML = `<div class="alert alert-warning">${err.message}</div>`;
  }
}

window.renderSummary = renderSummary;
