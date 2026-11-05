// Prediction page: 182-day FinalPweight forecast for one animal (rows refreshed by the server after each upload).
// Admins pick real EIDs; everyone else picks masked IDs, which the server resolves to the real animal.

const predictionState = { farmId: null, animal: null };

const PREDICTION_COLORS = {
  actual: '#1B4332', // --core-pine
  tested: '#059669', // --core-emerald
  projection: '#D97706', // --core-amber
};

function escapePredictionText(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const PREDICTION_HEADER = `
  <div class="module-header">
    <div>
      <h2>Weight Prediction</h2>
      <p>FinalPweight forecast for the next 6 months (182 days) from each animal's last recorded day. Sidebar filters do not apply to this page.</p>
    </div>
  </div>`;

async function refreshPredictionForecasts(farmId, container) {
  showLoading(container, 'Generating forecasts…');
  try {
    await apiFetch(`/admin/farms/${encodeURIComponent(farmId)}/forecasts/refresh`, {
      method: 'POST',
    });
    await renderPrediction(container);
  } catch (err) {
    container.innerHTML = `${PREDICTION_HEADER}<div class="alert alert-warning">Failed to refresh forecasts: ${escapePredictionText(err.message)}</div>
      <div style="margin-top:12px"><button id="btn-refresh-forecasts" class="btn btn-secondary btn-sm" type="button">Retry</button></div>`;
    const btn = document.getElementById('btn-refresh-forecasts');
    if (btn) btn.onclick = () => refreshPredictionForecasts(farmId, container);
  }
}

async function downloadPredictionForecasts(farmId, btn) {
  btn.disabled = true;
  try {
    await apiDownload(`/forecasts/export.csv?farm_id=${encodeURIComponent(farmId)}`, `${farmId}_forecasts.csv`);
  } catch (err) {
    showToast(`Download failed: ${err.message}`, 'warning');
  } finally {
    btn.disabled = false;
  }
}

async function renderPrediction(container) {
  const farmId = getFarmId();
  const user = typeof getUser === 'function' ? getUser() : null;
  const isAdmin = Boolean(user && user.is_admin);

  showLoading(container, 'Loading forecasts…');
  let list;
  try {
    list = await apiFetch(`/forecasts/animals?farm_id=${encodeURIComponent(farmId)}`);
  } catch (err) {
    const adminAction = isAdmin
      ? `<div style="margin-top:12px"><button id="btn-refresh-forecasts" class="btn btn-secondary btn-sm" type="button">Generate forecasts</button></div>`
      : '';
    container.innerHTML = `${PREDICTION_HEADER}<div class="alert alert-warning">${escapePredictionText(err.message)}</div>${adminAction}`;
    const btn = document.getElementById('btn-refresh-forecasts');
    if (btn) btn.onclick = () => refreshPredictionForecasts(farmId, container);
    return;
  }

  const animals = list.animals || [];
  if (!animals.length) {
    const adminAction = isAdmin
      ? `<div style="margin-top:12px"><button id="btn-refresh-forecasts" class="btn btn-secondary btn-sm" type="button">Generate forecasts</button></div>`
      : '';
    container.innerHTML = `${PREDICTION_HEADER}<div class="alert alert-muted">${escapePredictionText(list.message)}</div>${adminAction}`;
    const btn = document.getElementById('btn-refresh-forecasts');
    if (btn) btn.onclick = () => refreshPredictionForecasts(farmId, container);
    return;
  }
  if (predictionState.farmId !== farmId || !animals.some((a) => a.id === predictionState.animal)) {
    predictionState.farmId = farmId;
    predictionState.animal = animals[0].id;
  }

  const label = list.masked ? 'Animal (masked ID)' : 'Animal (EID)';
  // Admins and owners see real EIDs, so they can download the whole farm's forecasts.
  const canDownload = Boolean(typeof canExportRaw === 'function' ? canExportRaw() : isAdmin);
  const downloadBtnHtml = canDownload
    ? `<button id="btn-download-forecasts" class="btn btn-secondary btn-sm" type="button"><i class="fa fa-download" aria-hidden="true"></i> Download forecasts (CSV)</button>`
    : '';
  const refreshBtnHtml = isAdmin
    ? `<button id="btn-refresh-forecasts" class="btn btn-secondary btn-sm" type="button">Refresh forecasts</button>`
    : '';
  const actionsHtml = downloadBtnHtml || refreshBtnHtml
    ? `<div style="margin-left:auto;display:flex;gap:8px">${downloadBtnHtml}${refreshBtnHtml}</div>`
    : '';

  container.innerHTML = `
    ${PREDICTION_HEADER}
    <div class="controls-bar">
      <span>${label}</span>
      <div id="picker-prediction-animal" style="width:260px"></div>
      <span class="badge-primary">${animals.length} animals forecast</span>
      ${actionsHtml}
    </div>
    <div id="prediction-body"></div>`;

  const refreshBtn = document.getElementById('btn-refresh-forecasts');
  if (refreshBtn) {
    refreshBtn.onclick = () => refreshPredictionForecasts(farmId, container);
  }
  const downloadBtn = document.getElementById('btn-download-forecasts');
  if (downloadBtn) {
    downloadBtn.onclick = () => downloadPredictionForecasts(farmId, downloadBtn);
  }

  // The app's searchable picker: opens the full list, and its search box filters as you type.
  createCustomDropdown({
    container: 'picker-prediction-animal',
    field: 'prediction-animal',
    options: animals.map((a) => ({
      value: escapePredictionText(a.id),
      label: escapePredictionText(a.tier === 'short' ? `${a.id} · short history` : a.id),
    })),
    value: escapePredictionText(predictionState.animal),
    withSearch: true,
    onChange: (chosen) => {
      predictionState.animal = chosen;
      loadPredictionSeries(farmId, chosen);
    },
  });
  loadPredictionSeries(farmId, predictionState.animal);
}

async function loadPredictionSeries(farmId, animal) {
  const body = document.getElementById('prediction-body');
  if (!body) return;
  showLoading(body, 'Loading forecast…');
  try {
    const series = await apiFetch(
      `/forecasts/series?farm_id=${encodeURIComponent(farmId)}&animal=${encodeURIComponent(animal)}`
    );
    if (predictionState.animal !== animal) return; // a newer selection is loading
    body.innerHTML = predictionBodyHtml(series);
    drawPrediction(series);
  } catch (err) {
    body.innerHTML = `<div class="alert alert-warning">${escapePredictionText(err.message)}</div>`;
  }
}

function predictionBodyHtml(series) {
  const last = series.history.length ? series.history[series.history.length - 1].weight : null;
  const end = series.forecast[series.forecast.length - 1];
  const change = last != null ? end.weight - last : null;
  const summary = last != null
    ? `${last.toFixed(1)} kg on ${series.data_through} → ${end.weight.toFixed(1)} kg on ${end.date}
       (${change >= 0 ? '+' : ''}${change.toFixed(1)} kg)`
    : `${end.weight.toFixed(1)} kg on ${end.date}`;
  const shortNote = series.tier === 'short'
    ? `<div class="alert alert-warning">Short history: fewer than 14 consecutive days of recent data
         (${series.history_days} day${series.history_days === 1 ? '' : 's'} on record), so this forecast
         leans more on the farm's typical growth and is less reliable.</div>`
    : '';
  return `
    ${shortNote}
    <div class="chart-card">
      <h3>${escapePredictionText(series.animal)}: ${escapePredictionText(summary)}</h3>
      <div id="prediction-chart" class="chart-canvas"></div>
    </div>
    <div class="alert alert-muted">
      Days 1–${series.validated_days} are within the range the model was tested on. After day ${series.validated_days}
      the animal follows the farm's typical daily gain for its weight, which slows as it gets heavier: an
      unvalidated projection, not a measurement.
      Forecast from data up to ${escapePredictionText(series.data_through)}; generated
      ${escapePredictionText(String(series.generated_at).replace('T', ' ').slice(0, 16))} UTC
      (model ${escapePredictionText(series.model_version)}).
    </div>`;
}

function drawPrediction(series) {
  const el = document.getElementById('prediction-chart');
  if (!el || !window.Plotly) return;
  const cut = series.validated_days;
  const tested = series.forecast.filter((f) => f.horizon <= cut);
  const projected = series.forecast.filter((f) => f.horizon >= cut);
  const anchor = series.history.length ? [series.history[series.history.length - 1]] : [];
  const line = (points, name, color, dash) => ({
    x: points.map((p) => p.date),
    y: points.map((p) => p.weight),
    name,
    type: 'scatter',
    mode: 'lines',
    line: { color, width: 2, dash },
    hovertemplate: '%{y:.1f} kg<extra>' + name + '</extra>',
  });
  const traces = [
    line(series.history, 'Actual', PREDICTION_COLORS.actual, 'solid'),
    line([...anchor, ...tested], `Forecast (days 1–${cut}, tested)`, PREDICTION_COLORS.tested, 'dash'),
    line(projected, 'Projection (unvalidated)', PREDICTION_COLORS.projection, 'dot'),
  ];
  const projectionStart = projected.length ? projected[0].date : null;
  const shapes = [
    {
      type: 'line', x0: series.data_through, x1: series.data_through, y0: 0, y1: 1, yref: 'paper',
      line: { color: '#475569', width: 1, dash: 'dot' },
    },
  ];
  const annotations = [
    { x: series.data_through, y: 1, yref: 'paper', text: 'Last record', showarrow: false, yanchor: 'bottom', font: { size: 11, color: '#475569' } },
  ];
  if (projectionStart) {
    shapes.push({
      type: 'rect', x0: projectionStart, x1: series.forecast[series.forecast.length - 1].date, y0: 0, y1: 1,
      yref: 'paper', fillcolor: 'rgba(217, 119, 6, 0.06)', line: { width: 0 }, layer: 'below',
    });
    annotations.push({
      x: projectionStart, y: 1, yref: 'paper', text: 'Unvalidated projection', showarrow: false,
      xanchor: 'left', yanchor: 'bottom', font: { size: 11, color: PREDICTION_COLORS.projection },
    });
  }
  Plotly.newPlot(el, traces, {
    xaxis: { type: 'date', title: 'Date' },
    yaxis: { title: 'FinalPweight (kg)' },
    hovermode: 'x unified',
    legend: { orientation: 'h', y: -0.2 },
    shapes,
    annotations,
    margin: { t: 28, r: 20, b: 40, l: 60 },
  }, { responsive: true, displaylogo: false });
}

window.renderPrediction = renderPrediction;
