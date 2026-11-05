let tsPointSize = 3;
let tsShowTrend = false;
let tsRequestId = 0;
let tsAbort = null;
let tsShellReady = false;
let tsLastData = null;
/** Active grain entry: { key, grain, y_label, record_count } */
let tsGrainCache = null;
/** farm|year|measure → grain entry (month/day filtered client-side) */
const tsGrainCacheMap = Object.create(null);
let tsPrefetchPromise = null;
let tsGrainInflight = null; // { key, promise }
let tsMeasurePrefetchTimer = null;

const TS_MONTH_NAMES = [
  'All', 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

/** Active extra filters only, in a stable order; the server applies them to the grain. */
function tsExtraFilters(filters) {
  const out = {};
  Object.keys(filters.extra_filters || {}).sort().forEach((col) => {
    const vals = filters.extra_filters[col] || [];
    if (vals.length && !vals.includes('Overall')) out[col] = [...vals].sort();
  });
  return out;
}

/** Cache key is year×measure×active×extra-filter scope — month/day are applied in the browser. */
function tsScopeKey(filters) {
  return [
    filters.farm_id,
    filters.year,
    filters.measure,
    filters.active_only ? '1' : '0',
    filters.active_days || 30,
    filters.active_reference_mode || 'auto',
    JSON.stringify(tsExtraFilters(filters)),
  ].join('|');
}

function filterGrainByMonthDay(grain, filters) {
  const month = filters.month || 'All';
  const day = filters.day == null ? 'All' : String(filters.day);
  if (month === 'All' && day === 'All') return grain;
  const monthNum = month === 'All' ? null : TS_MONTH_NAMES.indexOf(month);
  const dayNum = day === 'All' ? null : parseInt(day, 10);
  return grain.filter((r) => {
    const mm = parseInt(String(r.d).slice(5, 7), 10);
    const dd = parseInt(String(r.d).slice(8, 10), 10);
    if (monthNum != null && monthNum > 0 && mm !== monthNum) return false;
    if (dayNum != null && !Number.isNaN(dayNum) && dd !== dayNum) return false;
    return true;
  });
}

function expandDim(selected) {
  selected = selected && selected.length ? selected : ['Overall'];
  const hasAll = selected.includes('Overall');
  const specifics = selected.filter(v => v !== 'Overall');
  if (hasAll && !specifics.length) return ['Overall'];
  if (hasAll && specifics.length) return ['Overall', ...specifics];
  if (specifics.length) return specifics;
  return ['Overall'];
}

function friendlyLabel(col) {
  if (col === 'treatment') return 'Treatment';
  if (col === 'eid') return 'EID';
  if (col === 'sex') return 'Sex';
  if (col === 'breed') return 'Breed';
  if (col === 'mob') return 'Mob';
  return col;
}

function labelFromCombo(combo, varying, allOverall) {
  if (allOverall || !varying.length) {
    if (Object.values(combo).every(v => v === 'Overall')) return 'Overall Average';
    const parts = ['sex', 'treatment', 'breed', 'mob', 'eid']
      .filter(d => combo[d] && combo[d] !== 'Overall')
      .map(d => `${friendlyLabel(d)}: ${combo[d]}`);
    return parts.length ? parts.join(' | ') : 'Overall Average';
  }
  return varying.map(d => `${friendlyLabel(d)}: ${combo[d]}`).join(' | ');
}

function rowMatchesCombo(row, combo) {
  if (combo.sex !== 'Overall' && row.s !== combo.sex) return false;
  if (combo.treatment !== 'Overall' && row.t !== combo.treatment) return false;
  if (combo.breed !== 'Overall' && row.b !== combo.breed) return false;
  if (combo.mob !== 'Overall' && row.m !== combo.mob) return false;
  return true;
}

function smoothSeries(series) {
  const byGroup = {};
  for (const row of series) {
    if (String(row.group).includes('(trend)')) continue;
    (byGroup[row.group] ||= []).push(row);
  }
  const extra = [];
  for (const [group, pts] of Object.entries(byGroup)) {
    pts.sort((a, b) => a.date.localeCompare(b.date));
    if (pts.length < 3) continue;
    const window = Math.max(3, Math.min(15, Math.floor(pts.length / 5) || 3));
    const values = pts.map(p => p.value);
    pts.forEach((p, i) => {
      const lo = Math.max(0, i - Math.floor(window / 2));
      const hi = Math.min(values.length, i + Math.floor(window / 2) + 1);
      const chunk = values.slice(lo, hi);
      const avg = chunk.reduce((a, b) => a + b, 0) / chunk.length;
      extra.push({ date: p.date, group: `${group} (trend)`, value: Math.round(avg * 100) / 100, count: p.count });
    });
  }
  return series.concat(extra);
}

function assembleTimeseriesFromGrain(grain, filters, yLabel, showSmooth) {
  grain = filterGrainByMonthDay(grain, filters);
  const dims = {
    sex: expandDim(filters.sex),
    treatment: expandDim(filters.treatment),
    breed: expandDim(filters.breed),
    mob: expandDim(filters.mob),
    eid: ['Overall'],
  };
  const varying = Object.keys(dims).filter(d => dims[d].length > 1);
  const allOverall = Object.values(dims).every(v => v.length === 1 && v[0] === 'Overall');
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

  const series = [];
  const present = [];
  const missing = [];
  let recordCount = 0;

  for (const combo of combos) {
    const label = labelFromCombo(combo, varying, allOverall);
    const buckets = new Map();
    for (const row of grain) {
      if (!rowMatchesCombo(row, combo)) continue;
      const c = row.c | 0;
      const v = +row.v;
      recordCount += c;
      const cur = buckets.get(row.d) || { w: 0, c: 0 };
      cur.w += v * c;
      cur.c += c;
      buckets.set(row.d, cur);
    }
    if (!buckets.size) {
      missing.push(label);
      continue;
    }
    present.push(label);
    for (const d of [...buckets.keys()].sort()) {
      const b = buckets.get(d);
      series.push({
        date: d,
        group: label,
        value: b.c ? Math.round((b.w / b.c) * 100) / 100 : 0,
        count: b.c,
      });
    }
  }

  // recordCount above double-counts when Overall+specifics overlap; use grain total instead
  const grainCount = grain.reduce((sum, r) => sum + (r.c | 0), 0);

  let outSeries = series;
  if (showSmooth) outSeries = smoothSeries(series);

  let note = null;
  if (present.length > 1 && varying.length) {
    note = varying.length === 1
      ? `Comparing groups by ${friendlyLabel(varying[0])} only`
      : `Comparing groups by ${varying.map(friendlyLabel).join(', ')}`;
  }

  return {
    series: outSeries,
    y_label: yLabel,
    combo_coverage: {
      expected: combos.length,
      present: present.length,
      missing: missing.length,
      present_groups: present,
      missing_groups: missing,
    },
    common_filters_note: note,
    record_count: grainCount,
  };
}

function ensureTimeseriesShell(container, data) {
  const filters = getFilters();
  const measures = (filters.measures && filters.measures.length) ? filters.measures : [filters.measure || 'finalpweight'];
  const allChartsPresent = measures.every(m => container.querySelector(`#ts-chart-${m}`));
  if (tsShellReady && allChartsPresent) {
    const note = container.querySelector('#ts-filters-note');
    const cov = container.querySelector('#ts-coverage');
    if (note) {
      note.classList.toggle('hidden', !data.common_filters_note);
      note.textContent = data.common_filters_note || '';
    }
    if (cov) {
      const c = data.combo_coverage;
      if (c && c.expected > 1 && c.missing > 0) {
        const preview = (c.missing_groups || []).slice(0, 6).map(g => `<li>${g}</li>`).join('');
        const more = c.missing > 6 ? `<li>…and ${c.missing - 6} more</li>` : '';
        cov.className = 'alert alert-warning';
        cov.innerHTML = `Showing <strong>${c.present}</strong> of <strong>${c.expected}</strong> selected combinations
          (${c.missing} have no matching records in the data).
          <details><summary>Combinations with no data</summary>
            <ul>${preview}${more}</ul>
          </details>`;
        cov.classList.remove('hidden');
      } else {
        cov.classList.add('hidden');
        cov.innerHTML = '';
      }
    }
    return;
  }

  const stackedCardsHtml = measures.map((m) => {
    const label = typeof getMeasureLabel === 'function' ? getMeasureLabel(m) : m;
    return `
      <div class="chart-card stacked-chart-card" id="ts-card-${m}">
        <h3>${label} — Time Series</h3>
        <div id="ts-chart-${m}" class="chart-canvas" style="min-height: 380px;"></div>
      </div>
    `;
  }).join('');

  container.innerHTML = `
    <div class="module-header">
      <div>
        <h2>Time Series Analysis</h2>
        <p>Longitudinal trajectory aggregated by active dimensions. Interactive legend toggles individual group series.</p>
      </div>
    </div>
    <div id="ts-filters-note" class="alert alert-muted ${data.common_filters_note ? '' : 'hidden'}">${data.common_filters_note || ''}</div>
    <div id="ts-coverage" class="hidden"></div>
    <div class="controls-bar">
      <label>Point Size: <input type="range" id="ts-point-size" min="1" max="5" value="${tsPointSize}"> <span id="ts-point-val">${tsPointSize}</span></label>
      <label><input type="checkbox" id="ts-trend" ${tsShowTrend ? 'checked' : ''}> Show Trend Line</label>
    </div>
    <div class="stacked-card-wrapper">
      ${stackedCardsHtml}
    </div>`;
  tsShellReady = true;

  const covEl = container.querySelector('#ts-coverage');
  if (covEl) {
    const c = data.combo_coverage;
    if (c && c.expected > 1 && c.missing > 0) {
      covEl.classList.remove('hidden');
      const preview = (c.missing_groups || []).slice(0, 6).map(g => `<li>${g}</li>`).join('');
      const more = c.missing > 6 ? `<li>…and ${c.missing - 6} more</li>` : '';
      covEl.className = 'alert alert-warning';
      covEl.innerHTML = `Showing <strong>${c.present}</strong> of <strong>${c.expected}</strong> selected combinations
        (${c.missing} have no matching records in the data).
        <details><summary>Combinations with no data</summary>
          <ul>${preview}${more}</ul>
        </details>`;
    } else {
      covEl.classList.add('hidden');
      covEl.innerHTML = '';
    }
  }

  document.getElementById('ts-point-size').oninput = (e) => {
    tsPointSize = parseInt(e.target.value);
    document.getElementById('ts-point-val').textContent = tsPointSize;
    if (tsLastData) drawTimeseries(tsLastData);
  };
  document.getElementById('ts-trend').onchange = (e) => {
    tsShowTrend = e.target.checked;
    applyTimeseriesFromCache(container);
  };
}

function applyTimeseriesFromCache(container) {
  const filters = getFilters();
  const measures = (filters.measures && filters.measures.length) ? filters.measures : [filters.measure || 'finalpweight'];
  const allCached = measures.every(m => {
    const k = tsScopeKey({ ...filters, measure: m });
    const e = tsGrainCacheMap[k];
    return e && e.grain;
  });
  if (!allCached) return false;

  const key = tsScopeKey(filters);
  const entry = tsGrainCacheMap[key] || (tsGrainCache && tsGrainCache.key === key ? tsGrainCache : null);
  if (!entry) return false;
  tsGrainCache = entry;
  const data = assembleTimeseriesFromGrain(
    entry.grain,
    filters,
    entry.y_label,
    tsShowTrend,
  );
  if (typeof updateRecordCountBadge === 'function') {
    updateRecordCountBadge(data.record_count);
  }
  if (!data.series.length) {
    tsShellReady = false;
    showEmptyState(container, filters);
    return true;
  }
  tsLastData = data;
  ensureTimeseriesShell(container, data);
  drawTimeseries(data);
  return true;
}

async function ensureTimeseriesGrain(filters, signal) {
  const key = tsScopeKey(filters);
  if (tsGrainCacheMap[key]) {
    tsGrainCache = tsGrainCacheMap[key];
    return tsGrainCache;
  }
  if (tsGrainCache && tsGrainCache.key === key) return tsGrainCache;

  // Seed from login boot cache when present (avoids a second cold grain fetch).
  try {
    const bootRaw = sessionStorage.getItem(typeof BOOT_CACHE_KEY !== 'undefined' ? BOOT_CACHE_KEY : 'boot_cache');
    if (bootRaw) {
      const boot = JSON.parse(bootRaw);
      if (boot?.grain_key === key && boot.grain?.grain) {
        const entry = {
          key,
          grain: boot.grain.grain || [],
          y_label: boot.grain.y_label,
          record_count: boot.grain.record_count,
        };
        tsGrainCacheMap[key] = entry;
        tsGrainCache = entry;
        return entry;
      }
    }
  } catch (_) { /* ignore */ }

  // EID comparisons still need the server timeseries path (grain omits eid).
  const eid = filters.eid || ['Overall'];
  if ((typeof canViewEid === 'function' ? canViewEid() : getUser()?.is_admin) && eid.some(v => v !== 'Overall')) {
    return null;
  }

  if (tsGrainInflight && tsGrainInflight.key === key) {
    return tsGrainInflight.promise;
  }

  const promise = (async () => {
    // Always fetch full year grain; month/day filtered in assemble*.
    const payload = await apiFetch('/charts/timeseries-grain', {
      method: 'POST',
      body: JSON.stringify({
        farm_id: filters.farm_id,
        year: filters.year,
        month: 'All',
        day: 'All',
        measure: filters.measure,
        sex: ['Overall'],
        treatment: ['Overall'],
        breed: ['Overall'],
        mob: ['Overall'],
        eid: ['Overall'],
        extra_filters: tsExtraFilters(filters),
        active_only: Boolean(filters.active_only),
        active_days: filters.active_days || 30,
        active_reference_mode: filters.active_reference_mode || 'auto',
      }),
      signal,
    });
    const entry = {
      key,
      grain: payload.grain || [],
      y_label: payload.y_label,
      record_count: payload.record_count,
    };
    tsGrainCacheMap[key] = entry;
    tsGrainCache = entry;
    return entry;
  })();

  tsGrainInflight = { key, promise };
  try {
    return await promise;
  } finally {
    if (tsGrainInflight && tsGrainInflight.promise === promise) tsGrainInflight = null;
  }
}

function scheduleMeasureGrainPrefetch() {
  clearTimeout(tsMeasurePrefetchTimer);
  tsMeasurePrefetchTimer = setTimeout(() => {
    prefetchOtherMeasureGrains().catch(() => {});
  }, 1200);
}

async function prefetchOtherMeasureGrains() {
  const filters = getFilters();
  if (!filters.year) return;
  const measures = (typeof choices !== 'undefined' && choices?.measures)
    ? choices.measures.map((m) => m.key)
    : ['finalpweight', 'finalgrowthpbs', 'methane', 'animalvalue', 'animalprod', 'carcassweight', 'feedintakekgd'];
  for (const measure of measures) {
    if (measure === filters.measure) continue;
    const key = tsScopeKey({ ...filters, measure });
    if (tsGrainCacheMap[key]) continue;
    try {
      await ensureTimeseriesGrain({ ...filters, measure, month: 'All', day: 'All' });
    } catch {
      /* ignore background prefetch errors */
    }
  }
}

async function prefetchTimeseriesGrain() {
  const filters = getFilters();
  if (!filters.year) return;
  try {
    tsPrefetchPromise = ensureTimeseriesGrain(filters);
    await tsPrefetchPromise;
    scheduleMeasureGrainPrefetch();
  } catch {
    /* ignore prefetch errors */
  } finally {
    tsPrefetchPromise = null;
  }
}

async function renderTimeseries(container, options = {}) {
  const dimOnly = !!options.dimOnly;
  let activeFilters = getFilters();
  if (!activeFilters.year) {
    const choicesObj = typeof choices !== 'undefined' ? choices : null;
    if (choicesObj?.max_year) {
      if (typeof filterState !== 'undefined') {
        filterState.year = choicesObj.max_year;
      }
      activeFilters = getFilters();
    } else {
      container.innerHTML = '<div class="alert alert-muted">Loading filters…</div>';
      tsShellReady = false;
      return;
    }
  }

  // Checkbox / dim-only changes: recompute locally — target << 1.5s (usually <100ms).
  const eidActive = !!((typeof canViewEid === 'function' ? canViewEid() : getUser()?.is_admin) && (activeFilters.eid || ['Overall']).some(v => v !== 'Overall'));
  if (dimOnly && !eidActive && applyTimeseriesFromCache(container)) {
    return;
  }
  if (!eidActive && tsGrainCache && tsGrainCache.key === tsScopeKey(activeFilters) && applyTimeseriesFromCache(container)) {
    return;
  }

  const reqId = ++tsRequestId;
  if (tsAbort) tsAbort.abort();
  tsAbort = new AbortController();

  if (!tsShellReady) showLoading(container);
  else {
    const chart = container.querySelector('#ts-chart');
    if (chart) {
      chart.style.opacity = '0.55';
      chart.dataset.loading = '1';
    }
  }

  try {
    const filters = getFilters();
    const eid = filters.eid || ['Overall'];
    const needsEid = !!((typeof canViewEid === 'function' ? canViewEid() : getUser()?.is_admin) && eid.some(v => v !== 'Overall'));

    let data;
    if (needsEid) {
      data = await apiFetch('/charts/timeseries', {
        method: 'POST',
        body: JSON.stringify({ ...filters, point_size: tsPointSize, show_smooth: tsShowTrend }),
        signal: tsAbort.signal,
      });
    } else {
      if (tsPrefetchPromise) await tsPrefetchPromise;
      const measures = (filters.measures && filters.measures.length) ? filters.measures : [filters.measure || 'finalpweight'];
      await Promise.all(measures.map(m => ensureTimeseriesGrain({ ...filters, measure: m }, tsAbort.signal)));
      if (reqId !== tsRequestId) return;
      scheduleMeasureGrainPrefetch();
      const primaryKey = tsScopeKey(filters);
      const primaryEntry = tsGrainCacheMap[primaryKey] || tsGrainCache;
      data = assembleTimeseriesFromGrain(
        primaryEntry ? primaryEntry.grain : [],
        filters,
        primaryEntry ? primaryEntry.y_label : '',
        tsShowTrend,
      );
    }
    if (reqId !== tsRequestId) return;

    if (typeof updateRecordCountBadge === 'function' && data.record_count != null) {
      updateRecordCountBadge(data.record_count);
    }
    if (!data.series || data.series.length === 0) {
      tsShellReady = false;
      showEmptyState(container, filters);
      return;
    }

    tsLastData = data;
    ensureTimeseriesShell(container, data);
    drawTimeseries(data);
    container.querySelectorAll('.chart-canvas').forEach(chart => {
      chart.style.opacity = '1';
      delete chart.dataset.loading;
    });
  } catch (err) {
    if (err.name === 'AbortError' || reqId !== tsRequestId) return;
    tsShellReady = false;
    container.innerHTML = `<div class="alert alert-warning">${err.message}</div>`;
  }
}

function buildTimeseriesTraces(data) {
  const groups = [...new Set(data.series.map(s => s.group))];
  return groups.map(g => {
    const pts = data.series
      .filter(s => s.group === g)
      .slice()
      .sort((a, b) => a.date.localeCompare(b.date));
    const isTrend = g.includes('(trend)');
    const vals = pts.map(p => p.value);
    const span = vals.length ? Math.max(...vals) - Math.min(...vals) : 0;
    return {
      x: pts.map(p => p.date),
      y: vals,
      customdata: pts.map(p => p.count),
      name: g,
      type: 'scatter',
      mode: isTrend ? 'lines' : 'lines+markers',
      marker: { size: Math.max(tsPointSize, span < 30 ? 5 : tsPointSize) },
      line: { dash: isTrend ? 'dash' : 'solid', width: isTrend ? 2 : 2.5, shape: 'linear' },
      connectgaps: false,
      hovertemplate:
        '%{fullData.name}<br>Date: %{x}<br>Avg: %{y:.2f}<br>Records: %{customdata}<extra></extra>',
    };
  });
}

function drawTimeseries(data) {
  const filters = getFilters();
  const measures = (filters.measures && filters.measures.length) ? filters.measures : [filters.measure || 'finalpweight'];

  for (const m of measures) {
    const el = document.getElementById(`ts-chart-${m}`) || document.getElementById('ts-chart');
    if (!el) continue;

    const key = tsScopeKey({ ...filters, measure: m });
    const entry = tsGrainCacheMap[key];
    let measureData = data;
    if (entry && entry.grain) {
      measureData = assembleTimeseriesFromGrain(entry.grain, filters, entry.y_label, tsShowTrend);
    } else if (data?.charts && Array.isArray(data.charts)) {
      const found = data.charts.find(c => c.measure === m);
      if (found) {
        measureData = found;
      }
    }

    const traces = buildTimeseriesTraces(measureData);
    const allY = measureData.series.filter(s => !String(s.group).includes('(trend)')).map(s => s.value);
    let yRange;
    if (allY.length) {
      const ymin = Math.min(...allY);
      const ymax = Math.max(...allY);
      const pad = Math.max((ymax - ymin) * 0.08, 5);
      yRange = [ymin - pad, ymax + pad];
    }

    const layout = {
      xaxis: { title: 'Date', type: 'date' },
      yaxis: {
        title: measureData.y_label || (typeof getMeasureLabel === 'function' ? getMeasureLabel(m) : m),
        rangemode: 'normal',
        ...(yRange ? { range: yRange } : {}),
      },
      legend: { title: { text: 'Animal Group' } },
      margin: { t: 16, r: 20, b: 40, l: 52 },
      hovermode: 'closest',
    };
    const opts = { responsive: true, displayModeBar: true };
    if (el.data) Plotly.react(el, traces, layout, opts);
    else Plotly.newPlot(el, traces, layout, opts);
  }
}

function resetTimeseriesShell() {
  tsShellReady = false;
  tsLastData = null;
  tsRequestId += 1;
  if (tsAbort) tsAbort.abort();
}

window.renderTimeseries = renderTimeseries;
window.resetTimeseriesShell = resetTimeseriesShell;
window.prefetchTimeseriesGrain = prefetchTimeseriesGrain;
window.ensureScopeGrain = ensureTimeseriesGrain;
window.getScopeGrainCache = () => {
  try {
    const filters = getFilters();
    const key = tsScopeKey(filters);
    return tsGrainCacheMap[key] || (tsGrainCache && tsGrainCache.key === key ? tsGrainCache : null);
  } catch {
    return tsGrainCache;
  }
};
window.getScopeGrainCacheMap = () => tsGrainCacheMap;
window.tsScopeKey = tsScopeKey;
window.filterGrainByMonthDay = filterGrainByMonthDay;
window.scheduleMeasureGrainPrefetch = scheduleMeasureGrainPrefetch;
window.expandFilterDim = expandDim;
window.grainRowMatchesCombo = rowMatchesCombo;
window.grainFriendlyLabel = friendlyLabel;
window.grainLabelFromCombo = labelFromCombo;
