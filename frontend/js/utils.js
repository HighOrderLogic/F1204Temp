function showToast(message, type = 'info') {
  let container = document.querySelector('.toast-container');
  if (!container) {
    container = document.createElement('div');
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function escapeEmptyText(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showEmptyState(container, filters) {
  if (!container) return;
  const f = filters || (typeof getFilters === 'function' ? getFilters() : {}) || {};
  const yearText = f.year != null ? f.year : 'All';
  const monthText = f.month || 'All';
  const dayText = f.day != null ? f.day : 'All';

  const anchorLabel = f.active_reference_mode === 'max'
    ? 'Latest'
    : (f.active_reference_mode === 'today' ? 'Today' : 'Auto');
  const activeHerdStateText = f.active_only
    ? `On (${f.active_days || 30}d, ${anchorLabel})`
    : 'Off';

  const extraFilters = [];
  if (Array.isArray(f.sex) && f.sex.length && !f.sex.includes('Overall')) {
    extraFilters.push(`Sex: ${f.sex.join(', ')}`);
  }
  if (Array.isArray(f.treatment) && f.treatment.length && !f.treatment.includes('Overall')) {
    extraFilters.push(`Treatment: ${f.treatment.join(', ')}`);
  }
  if (Array.isArray(f.breed) && f.breed.length && !f.breed.includes('Overall')) {
    extraFilters.push(`Breed: ${f.breed.join(', ')}`);
  }
  if (Array.isArray(f.mob) && f.mob.length && !f.mob.includes('Overall')) {
    extraFilters.push(`Mob: ${f.mob.join(', ')}`);
  }

  const extrasHtml = extraFilters.length
    ? extraFilters.map((item) => `<div>${escapeEmptyText(item)}</div>`).join('')
    : '';

  container.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon"><i class="fa fa-chart-bar" aria-hidden="true"></i></div>
      <h3>No Data Found</h3>
      <p>No livestock records match your current filter criteria.<br>Try adjusting your filters to see more data.</p>
      <div style="margin:16px 0;font-size:13px;text-align:left;max-width:320px;margin-inline:auto;line-height:1.6">
        <strong>Current filters:</strong><br>
        <div>Year: ${escapeEmptyText(yearText)}</div>
        <div>Month: ${escapeEmptyText(monthText)}</div>
        <div>Day: ${escapeEmptyText(dayText)}</div>
        <div>Active Herd State: ${escapeEmptyText(activeHerdStateText)}</div>
        ${extrasHtml}
      </div>
      <button class="btn btn-primary" onclick="clearAllFilters()">Reset All Filters</button>
    </div>
  `;
}


function formatValue(val, measure) {
  if (measure === 'animalvalue') return `$${Number(val).toLocaleString(undefined, { minimumFractionDigits: 2 })}`;
  const units = { finalpweight: 'kg', finalgrowthpbs: 'kg/day', methane: 'g/day', carcassweight: 'kg', feedintakekgd: 'kg/day', animalprod: 'units' };
  return `${Number(val).toFixed(2)} ${units[measure] || ''}`;
}

function showLoading(container, message = 'Loading telemetry…') {
  if (!container) return;
  container.innerHTML = `
    <div class="loading-indicator" role="status" aria-live="polite">
      <div class="loading-spinner" aria-hidden="true"></div>
      <span class="loading-text">${message}</span>
    </div>`;
}

function createCustomDropdown({
  container,
  field = 'dropdown',
  options = [],
  value = '',
  onChange = null,
  withSearch = false,
  size = 'default',
}) {
  const el = typeof container === 'string' ? document.getElementById(container) : container;
  if (!el) return;
  el.classList.add('custom-picker');
  if (size === 'navbar') el.classList.add('custom-picker-navbar');
  if (typeof window.renderSingleSelectPicker === 'function') {
    window.renderSingleSelectPicker(el, field, options, value, onChange, withSearch);
  }
}

window.showToast = showToast;
window.showEmptyState = showEmptyState;
window.formatValue = formatValue;
window.showLoading = showLoading;
window.createCustomDropdown = createCustomDropdown;
