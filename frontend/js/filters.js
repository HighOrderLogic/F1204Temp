const SAVED_VIEWS_KEY = 'livestock_saved_views';

const defaultFilters = () => ({
  year: null,
  month: 'All',
  day: 'All',
  sex: ['Overall'],
  treatment: ['Overall'],
  breed: ['Overall'],
  mob: ['Overall'],
  eid: ['Overall'],
  // Admin-enabled categorical columns beyond the built-in four: column → selected values.
  extra_filters: {},
  measure: 'finalpweight',
  measures: ['finalpweight'],
  cohort_percentile: 10,
  active_only: false,
  active_days: 30,
  active_reference_mode: 'auto',
});

// Built-in filters have their own sidebar markup and FilterState fields; other admin-enabled
// filter columns are rendered into #extra-filters and sent as extra_filters.
const BUILTIN_FILTER_FIELDS = ['sex', 'treatment', 'breed', 'mob'];
const BUILTIN_CHOICE_KEYS = { sex: 'sexes', treatment: 'treatments', breed: 'breeds', mob: 'mobs', eid: 'eids' };
const EXTRA_FIELD_PREFIX = 'extra:';

function isExtraField(field) { return String(field).startsWith(EXTRA_FIELD_PREFIX); }
function extraColumn(field) { return String(field).slice(EXTRA_FIELD_PREFIX.length); }

function getFieldSelection(field) {
  if (isExtraField(field)) return filterState.extra_filters?.[extraColumn(field)] || ['Overall'];
  return filterState[field] || ['Overall'];
}

function setFieldSelection(field, selected) {
  if (isExtraField(field)) {
    filterState.extra_filters = { ...(filterState.extra_filters || {}), [extraColumn(field)]: selected };
  } else {
    filterState[field] = selected;
  }
}

function getFieldChoices(field) {
  if (isExtraField(field)) return choices?.dynamic_filters?.[extraColumn(field)] || ['Overall'];
  return choices?.[BUILTIN_CHOICE_KEYS[field]] || [];
}

// Extra filters aren't part of the cached timeseries grain, so changing one needs a server round trip.
function filterChangeOptions(field) {
  return { dimOnly: field !== 'eid' && !isExtraField(field) };
}

function getActiveFilterColumns() {
  return choices?.active_filter_columns || BUILTIN_FILTER_FIELDS;
}

function getMeasureKeys() {
  return (choices?.measures || []).map(m => m.key || m.value || m);
}

function getDefaultMeasure() {
  const keys = getMeasureKeys();
  if (choices?.default_measure && keys.includes(choices.default_measure)) return choices.default_measure;
  return keys.includes('finalpweight') || !keys.length ? 'finalpweight' : keys[0];
}

function hasActiveExtraFilters(filters) {
  return Object.values(filters?.extra_filters || {}).some(vals => (vals || []).some(v => v !== 'Overall'));
}

/** Drop selections the farm's admin config no longer offers (saved views, other farms, stale storage). */
function sanitizeFilterStateForChoices() {
  if (!choices) return;
  const active = new Set(getActiveFilterColumns());
  BUILTIN_FILTER_FIELDS.forEach(f => {
    if (!active.has(f)) filterState[f] = ['Overall'];
  });
  const extras = {};
  Object.entries(filterState.extra_filters || {}).forEach(([col, vals]) => {
    if (active.has(col) && !BUILTIN_FILTER_FIELDS.includes(col)) extras[col] = vals;
  });
  filterState.extra_filters = extras;

  const keys = getMeasureKeys();
  if (keys.length) {
    const kept = (filterState.measures || []).filter(m => keys.includes(m));
    filterState.measures = kept.length ? kept : [getDefaultMeasure()];
    filterState.measure = filterState.measures[0];
  }
}

let filterState = defaultFilters();
let choices = null;
let queryResult = null;
let debounceTimer = null;
let onFilterChange = null;
let choicesRequest = 0;
/** farm_id → { choices, summary, measure } — keeps farm switches warm */
const farmBootCache = Object.create(null);
/** farm_id → Promise — coalesce concurrent bootstrap fetches */
const farmBootInflight = Object.create(null);

function yearStorageKey(farmId) {
  return `livestock_year_${farmId || 'KF'}`;
}

function readFarmYear(farmId) {
  try {
    const stored = Number(localStorage.getItem(yearStorageKey(farmId)));
    if (Number.isFinite(stored) && stored > 0) return stored;
  } catch (_) { /* ignore */ }
  return null;
}

function writeFarmYear(farmId, year) {
  if (!farmId || year == null) return;
  try {
    localStorage.setItem(yearStorageKey(farmId), String(year));
    localStorage.setItem('livestock_last_year', String(year));
  } catch (_) { /* ignore */ }
}

function debounce(fn, ms = 300) {
  return (...args) => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => fn(...args), ms);
  };
}

function getMeasureLabel(key) {
  if (typeof choices !== 'undefined' && choices?.measures) {
    const found = choices.measures.find(m => (m.key || m.value) === key);
    if (found) return found.label || found.name || key;
  }
  const fallback = {
    finalpweight: 'Final processed weight (kg)',
    finalgrowthpbs: 'Final growth PBS',
    finaldailygrowth: 'Final daily growth',
    feedintakekgd: 'Feed intake (kg/d)',
    feedintakepct: 'Feed intake (%)',
    methane: 'Methane (g/d)',
    animalvalue: 'Animal value ($)',
    animalprod: 'Animal prod',
    carcassweight: 'Carcass weight (kg)',
    dressedcarcass: 'Dressed carcass (%)',
  };
  return fallback[key] || key;
}

function getFilterPayload() {
  const user = getUser();
  const measures = filterState.measures && filterState.measures.length ? filterState.measures : [filterState.measure || 'finalpweight'];
  return {
    ...filterState,
    farm_id: getFarmId(),
    measures,
    measure: measures[0] || 'finalpweight',
    eid: (typeof canViewEid === 'function' ? canViewEid() : user?.is_admin) ? filterState.eid : ['Overall'],
  };
}

function getActiveHerdCountKey() {
  const mode = filterState.active_reference_mode || 'auto';
  const days = filterState.active_days || 30;
  return `${mode}_${days}`;
}

function resolveActiveHerdCount() {
  const key = getActiveHerdCountKey();
  if (choices?.active_counts && choices.active_counts[key] != null) {
    return choices.active_counts[key];
  }
  return choices?.active_animal_count;
}

async function syncActiveHerdBadgeLive() {
  const key = getActiveHerdCountKey();
  if (choices?.active_counts && choices.active_counts[key] != null) return;
  try {
    const farmId = getFarmId();
    const mode = filterState.active_reference_mode || 'auto';
    const days = filterState.active_days || 30;
    const res = await apiFetch(`/filters/active-count?farm_id=${encodeURIComponent(farmId)}&active_days=${days}&active_reference_mode=${encodeURIComponent(mode)}`);
    if (res && res.active_animal_count != null) {
      if (!choices) choices = {};
      if (!choices.active_counts) choices.active_counts = {};
      choices.active_counts[key] = res.active_animal_count;
      if (res.total_animal_count != null) choices.total_animal_count = res.total_animal_count;
      updateRecordCountBadge();
    }
  } catch (_) {}
}

function updateRecordCountBadge(recordCount, totalRecords, activeAnimalCount, totalAnimalCount) {
  const countEl = document.getElementById('record-count');
  if (countEl && recordCount != null) {
    const total = totalRecords ?? choices?.total_records ?? recordCount;
    countEl.textContent = `Showing ${Number(recordCount).toLocaleString()} of ${Number(total).toLocaleString()} records`;
  }
  const badgeEl = document.getElementById('active-herd-badge');
  if (badgeEl) {
    const active = activeAnimalCount ?? resolveActiveHerdCount();
    const totalHead = totalAnimalCount ?? choices?.total_animal_count;
    if (active != null && totalHead != null) {
      badgeEl.textContent = `Active: ${Number(active).toLocaleString()} / ${Number(totalHead).toLocaleString()} head`;
    }
  }
}

async function loadChoices(preloaded) {
  const farmId = getFarmId();
  if (preloaded && preloaded.farm_id === farmId && preloaded.choices) {
    choices = preloaded.choices;
  } else {
    choices = await apiFetch(`/filters/choices?farm_id=${farmId}`);
  }
  if (!filterState.year && choices.max_year) {
    filterState.year = choices.max_year;
  }
  renderFilterUI();
}

function updatePickerDropup(container) {
  if (!container) return;
  const rect = container.getBoundingClientRect();
  const spaceBelow = window.innerHeight - rect.bottom;
  const dock = document.querySelector('.sidebar-sticky-dock');
  const dockTop = dock ? dock.getBoundingClientRect().top : window.innerHeight;
  const effectiveSpaceBelow = Math.min(spaceBelow, dockTop - rect.bottom);
  if (effectiveSpaceBelow < 240 && rect.top > 200) {
    container.classList.add('dropup');
  } else {
    container.classList.remove('dropup');
  }
}

function renderSearchablePicker(containerId, field, options) {
  const container = document.getElementById(containerId) || document.getElementById(`picker-${field}`);
  if (!container) return;

  const currentSelected = getFieldSelection(field);

  let summaryText = 'Overall';
  if (currentSelected.length === 1) {
    summaryText = currentSelected[0];
  } else if (currentSelected.length > 1) {
    summaryText = `${currentSelected.length} selected`;
  }

  // Approach 1: Tag pills with dismiss button when multiple items selected
  const tagsHtml = (currentSelected.length > 1)
    ? currentSelected.map(opt => pickerTagHtml(opt)).join('')
    : '';

  const optionsHtml = options.map(opt => {
    const isChecked = currentSelected.includes(opt);
    const safe = escapeEmptyText(opt);
    return `<label><input type="checkbox" data-field="${escapeEmptyText(field)}" value="${safe}" ${isChecked ? 'checked' : ''}> <span>${safe}</span></label>`;
  }).join('');

  container.innerHTML = `
    <button type="button" class="picker-trigger" id="picker-btn-${escapeEmptyText(field)}" aria-haspopup="listbox" aria-expanded="false">
      <span class="picker-trigger-text">${escapeEmptyText(summaryText)}</span>
      <i class="fa fa-chevron-down picker-chevron" aria-hidden="true"></i>
    </button>
    <div class="picker-tags-wrap" id="picker-tags-${field}">${tagsHtml}</div>
    <div class="picker-menu" role="listbox" id="picker-menu-${field}">
      <div class="picker-search-wrap">
        <input type="text" class="picker-search-input" placeholder="Search options..." aria-label="Search ${escapeEmptyText(field)}">
      </div>
      <div class="picker-options-list">
        ${optionsHtml}
        <div class="picker-no-results" style="display:none;">No matching options</div>
      </div>
    </div>
  `;

  const trigger = container.querySelector('.picker-trigger');
  const menu = container.querySelector('.picker-menu');
  const searchInput = container.querySelector('.picker-search-input');
  const optionsList = container.querySelector('.picker-options-list');
  const noResults = container.querySelector('.picker-no-results');

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (trigger.disabled || container.classList.contains('disabled')) return;
    const isOpen = container.classList.contains('open');
    document.querySelectorAll('.custom-picker.open').forEach(p => {
      if (p !== container) {
        p.classList.remove('open', 'dropup');
        p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
      }
    });
    if (!isOpen) {
      updatePickerDropup(container);
    } else {
      container.classList.remove('dropup');
    }
    container.classList.toggle('open', !isOpen);
    trigger.setAttribute('aria-expanded', !isOpen ? 'true' : 'false');
    if (!isOpen && searchInput) {
      searchInput.value = '';
      optionsList.querySelectorAll('label').forEach(lbl => lbl.style.display = 'flex');
      if (noResults) noResults.style.display = 'none';
      setTimeout(() => searchInput.focus(), 50);
    }
  });

  menu.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  searchInput.addEventListener('input', () => {
    if (trigger.disabled || container.classList.contains('disabled')) return;
    const q = searchInput.value.toLowerCase().trim();
    let visibleCount = 0;
    optionsList.querySelectorAll('label').forEach(lbl => {
      const txt = lbl.textContent.toLowerCase();
      const match = txt.includes(q);
      lbl.style.display = match ? 'flex' : 'none';
      if (match) visibleCount++;
    });
    if (noResults) {
      noResults.style.display = visibleCount === 0 ? 'block' : 'none';
    }
  });

  optionsList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (trigger.disabled || container.classList.contains('disabled')) return;
      let selected = [...optionsList.querySelectorAll('input:checked')].map(i => i.value);

      // Only default to 'Overall' if none selected
      if (!selected.length) {
        const overallCb = optionsList.querySelector('input[value="Overall"]');
        if (overallCb) {
          overallCb.checked = true;
          selected = ['Overall'];
        }
      }

      setFieldSelection(field, selected);
      updatePickerUI(container, field);
      debouncedNotifyFilterChange(filterChangeOptions(field));
    });
  });

  const tagsWrap = container.querySelector('.picker-tags-wrap');
  tagsWrap.addEventListener('click', (e) => {
    const removeBtn = e.target.closest('.picker-tag-remove');
    if (!removeBtn) return;
    if (trigger.disabled || container.classList.contains('disabled')) return;
    e.stopPropagation();
    const valToRemove = removeBtn.dataset.remove;
    const targetCb = [...optionsList.querySelectorAll('input[type="checkbox"]')].find(i => i.value === valToRemove);
    if (targetCb) {
      targetCb.checked = false;
    }
    let selected = [...optionsList.querySelectorAll('input:checked')].map(i => i.value);
    if (!selected.length) {
      const overallCb = optionsList.querySelector('input[value="Overall"]');
      if (overallCb) {
        overallCb.checked = true;
        selected = ['Overall'];
      }
    }
    setFieldSelection(field, selected);
    updatePickerUI(container, field);
    debouncedNotifyFilterChange(filterChangeOptions(field));
  });
}

function pickerTagHtml(opt) {
  const safe = escapeEmptyText(opt);
  return `<span class="picker-tag" data-val="${safe}">${safe} <button type="button" class="picker-tag-remove" data-remove="${safe}" aria-label="Remove ${safe}">&times;</button></span>`;
}

function updatePickerUI(container, field) {
  const currentSelected = getFieldSelection(field);

  const triggerText = container.querySelector('.picker-trigger-text');
  if (triggerText) {
    if (currentSelected.length === 1) {
      triggerText.textContent = currentSelected[0];
    } else if (currentSelected.length > 1) {
      triggerText.textContent = `${currentSelected.length} selected`;
    } else {
      triggerText.textContent = 'Overall';
    }
  }

  const tagsWrap = container.querySelector('.picker-tags-wrap');
  if (tagsWrap) {
    if (currentSelected.length > 1) {
      tagsWrap.innerHTML = currentSelected.map(opt => pickerTagHtml(opt)).join('');
    } else {
      tagsWrap.innerHTML = '';
    }
  }
}

function renderMeasureMultiPicker(containerId, rawOptions) {
  const container = document.getElementById(containerId) || document.getElementById('picker-measure');
  if (!container || !rawOptions) return;

  const options = rawOptions.map(opt => {
    if (typeof opt === 'object' && opt !== null) {
      const val = opt.key !== undefined ? opt.key : opt.value;
      const lbl = opt.label !== undefined ? opt.label : opt.name || String(val);
      return { value: val, label: lbl };
    }
    return { value: opt, label: String(opt) };
  });

  const currentSelected = (filterState.measures && filterState.measures.length)
    ? filterState.measures
    : [filterState.measure || getDefaultMeasure()];
  filterState.measures = currentSelected;
  filterState.measure = currentSelected[0] || getDefaultMeasure();

  let summaryText = getMeasureLabel(getDefaultMeasure());
  if (currentSelected.length === 1) {
    summaryText = getMeasureLabel(currentSelected[0]);
  } else if (currentSelected.length > 1) {
    summaryText = `${currentSelected.length} measures selected`;
  }

  const tagsHtml = (currentSelected.length > 1)
    ? currentSelected.map(opt => `<span class="picker-tag" data-val="${opt}">${getMeasureLabel(opt)} <button type="button" class="picker-tag-remove" data-remove="${opt}" aria-label="Remove ${getMeasureLabel(opt)}">&times;</button></span>`).join('')
    : '';

  const optionsHtml = options.map(opt => {
    const isChecked = currentSelected.includes(opt.value);
    return `<label><input type="checkbox" data-field="measure" value="${opt.value}" ${isChecked ? 'checked' : ''}> <span>${opt.label}</span></label>`;
  }).join('');

  container.innerHTML = `
    <button type="button" class="picker-trigger" id="picker-btn-measure" aria-haspopup="listbox" aria-expanded="false">
      <span class="picker-trigger-text">${summaryText}</span>
      <i class="fa fa-chevron-down picker-chevron" aria-hidden="true"></i>
    </button>
    <div class="picker-tags-wrap" id="picker-tags-measure">${tagsHtml}</div>
    <div class="picker-menu" role="listbox" id="picker-menu-measure">
      <div class="picker-search-wrap">
        <input type="text" class="picker-search-input" placeholder="Search measures..." aria-label="Search measure">
      </div>
      <div class="picker-options-list">
        ${optionsHtml}
        <div class="picker-no-results" style="display:none;">No matching measures</div>
      </div>
    </div>
  `;

  const trigger = container.querySelector('.picker-trigger');
  const menu = container.querySelector('.picker-menu');
  const searchInput = container.querySelector('.picker-search-input');
  const optionsList = container.querySelector('.picker-options-list');
  const noResults = container.querySelector('.picker-no-results');

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (trigger.disabled || container.classList.contains('disabled') || currentActiveModule === 'admin' || currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
    const isOpen = container.classList.contains('open');
    document.querySelectorAll('.custom-picker.open').forEach(p => {
      if (p !== container) {
        p.classList.remove('open', 'dropup');
        p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
      }
    });
    if (!isOpen) {
      updatePickerDropup(container);
    } else {
      container.classList.remove('dropup');
    }
    container.classList.toggle('open', !isOpen);
    trigger.setAttribute('aria-expanded', !isOpen ? 'true' : 'false');
    if (!isOpen && searchInput) {
      searchInput.value = '';
      optionsList.querySelectorAll('label').forEach(lbl => lbl.style.display = 'flex');
      if (noResults) noResults.style.display = 'none';
      setTimeout(() => searchInput.focus(), 50);
    }
  });

  menu.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  searchInput.addEventListener('input', () => {
    if (trigger.disabled || container.classList.contains('disabled') || currentActiveModule === 'admin' || currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
    const q = searchInput.value.toLowerCase().trim();
    let visibleCount = 0;
    optionsList.querySelectorAll('label').forEach(lbl => {
      const txt = lbl.textContent.toLowerCase();
      const match = txt.includes(q);
      lbl.style.display = match ? 'flex' : 'none';
      if (match) visibleCount++;
    });
    if (noResults) {
      noResults.style.display = visibleCount === 0 ? 'block' : 'none';
    }
  });

  optionsList.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (trigger.disabled || container.classList.contains('disabled') || currentActiveModule === 'admin' || currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
      let selected = [...optionsList.querySelectorAll('input:checked')].map(i => i.value);
      if (!selected.length) {
        const defaultCb = optionsList.querySelector(`input[value="${getDefaultMeasure()}"]`);
        if (defaultCb) {
          defaultCb.checked = true;
          selected = [getDefaultMeasure()];
        }
      }
      filterState.measures = selected;
      filterState.measure = selected[0] || getDefaultMeasure();
      try {
        localStorage.setItem('livestock_last_measures', JSON.stringify(selected));
        localStorage.setItem('livestock_last_measure', filterState.measure);
      } catch (_) {}
      updateMeasurePickerUI(container);
      debouncedNotifyFilterChange({ dimOnly: false, measureChanged: true });
    });
  });

  const tagsWrap = container.querySelector('.picker-tags-wrap');
  tagsWrap.addEventListener('click', (e) => {
    const removeBtn = e.target.closest('.picker-tag-remove');
    if (!removeBtn) return;
    if (trigger.disabled || container.classList.contains('disabled') || currentActiveModule === 'admin' || currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
    e.stopPropagation();
    const valToRemove = removeBtn.dataset.remove;
    const targetCb = optionsList.querySelector(`input[value="${valToRemove}"]`);
    if (targetCb) {
      targetCb.checked = false;
    }
    let selected = [...optionsList.querySelectorAll('input:checked')].map(i => i.value);
    if (!selected.length) {
      const defaultCb = optionsList.querySelector(`input[value="${getDefaultMeasure()}"]`);
      if (defaultCb) {
        defaultCb.checked = true;
        selected = [getDefaultMeasure()];
      }
    }
    filterState.measures = selected;
    filterState.measure = selected[0] || getDefaultMeasure();
    try {
      localStorage.setItem('livestock_last_measures', JSON.stringify(selected));
      localStorage.setItem('livestock_last_measure', filterState.measure);
    } catch (_) {}
    updateMeasurePickerUI(container);
    debouncedNotifyFilterChange({ dimOnly: false, measureChanged: true });
  });
}

function updateMeasurePickerUI(container) {
  const currentSelected = filterState.measures || [getDefaultMeasure()];
  const triggerText = container.querySelector('.picker-trigger-text');
  if (triggerText) {
    if (currentSelected.length === 1) {
      triggerText.textContent = getMeasureLabel(currentSelected[0]);
    } else if (currentSelected.length > 1) {
      triggerText.textContent = `${currentSelected.length} measures selected`;
    } else {
      triggerText.textContent = getMeasureLabel(getDefaultMeasure());
    }
  }

  const tagsWrap = container.querySelector('.picker-tags-wrap');
  if (tagsWrap) {
    if (currentSelected.length > 1) {
      tagsWrap.innerHTML = currentSelected.map(opt => `<span class="picker-tag" data-val="${opt}">${getMeasureLabel(opt)} <button type="button" class="picker-tag-remove" data-remove="${opt}" aria-label="Remove ${getMeasureLabel(opt)}">&times;</button></span>`).join('');
    } else {
      tagsWrap.innerHTML = '';
    }
  }
}


if (!window.__pickerGlobalListenerInit) {
  window.__pickerGlobalListenerInit = true;
  document.addEventListener('click', (e) => {
    const selectAllBtn = e.target.closest('[data-select-all]');
    if (selectAllBtn) {
      if (currentActiveModule === 'admin' || selectAllBtn.disabled || selectAllBtn.closest('#sidebar-filters-body')?.style.pointerEvents === 'none') {
        return;
      }
      e.preventDefault();
      selectAll(selectAllBtn.dataset.selectAll);
      return;
    }
    const invertBtn = e.target.closest('[data-invert]');
    if (invertBtn) {
      if (currentActiveModule === 'admin' || invertBtn.disabled || invertBtn.closest('#sidebar-filters-body')?.style.pointerEvents === 'none') {
        return;
      }
      e.preventDefault();
      invertSelection(invertBtn.dataset.invert);
      return;
    }
    if (!e.target.closest('.custom-picker')) {
      document.querySelectorAll('.custom-picker.open').forEach(p => {
        p.classList.remove('open', 'dropup');
        p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
      });
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.custom-picker.open').forEach(p => {
        p.classList.remove('open', 'dropup');
        p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
      });
    }
  });
}

function renderSingleSelectPicker(containerId, field, rawOptions, currentValue, onSelect, withSearch = false) {
  const container = (typeof containerId === 'string' ? document.getElementById(containerId) : containerId) || document.getElementById(`picker-${field}`);
  if (!container) return;

  const options = rawOptions.map(opt => {
    if (typeof opt === 'object' && opt !== null) {
      const val = opt.key !== undefined ? opt.key : opt.value;
      const lbl = opt.label !== undefined ? opt.label : opt.name || String(val);
      return { value: val, label: lbl };
    }
    return { value: opt, label: String(opt) };
  });

  const activeOpt = options.find(o => String(o.value) === String(currentValue)) || options[0] || { value: '', label: '' };
  const currentLabel = activeOpt.label;

  const searchHtml = withSearch ? `
    <div class="picker-search-wrap">
      <input type="text" class="picker-search-input" placeholder="Search options..." aria-label="Search ${field}">
    </div>
  ` : '';

  const optionsHtml = options.map(opt => {
    const isSelected = String(opt.value) === String(currentValue);
    const checkmarkHtml = isSelected ? '<i class="fa fa-check picker-option-check" aria-hidden="true"></i>' : '';
    return `<div class="picker-option-item ${isSelected ? 'selected' : ''}" data-value="${opt.value}" role="option" aria-selected="${isSelected}"><span>${opt.label}</span>${checkmarkHtml}</div>`;
  }).join('');

  container.innerHTML = `
    <button type="button" class="picker-trigger" id="picker-btn-${field}" aria-haspopup="listbox" aria-expanded="false">
      <span class="picker-trigger-text">${currentLabel}</span>
      <i class="fa fa-chevron-down picker-chevron" aria-hidden="true"></i>
    </button>
    <div class="picker-menu" role="listbox" id="picker-menu-${field}">
      ${searchHtml}
      <div class="picker-options-list">
        ${optionsHtml}
        <div class="picker-no-results" style="display:none;">No matching options</div>
      </div>
    </div>
  `;

  const trigger = container.querySelector('.picker-trigger');
  const menu = container.querySelector('.picker-menu');
  const searchInput = container.querySelector('.picker-search-input');
  const optionsList = container.querySelector('.picker-options-list');
  const noResults = container.querySelector('.picker-no-results');

  trigger.addEventListener('click', (e) => {
    e.stopPropagation();
    if (container.classList.contains('disabled') || trigger.disabled) return;
    const isOpen = container.classList.contains('open');
    document.querySelectorAll('.custom-picker.open').forEach(p => {
      if (p !== container) {
        p.classList.remove('open', 'dropup');
        p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
      }
    });
    if (!isOpen) {
      updatePickerDropup(container);
    } else {
      container.classList.remove('dropup');
    }
    container.classList.toggle('open', !isOpen);
    trigger.setAttribute('aria-expanded', !isOpen ? 'true' : 'false');
    if (!isOpen && searchInput) {
      searchInput.value = '';
      optionsList.querySelectorAll('.picker-option-item').forEach(item => item.style.display = 'flex');
      if (noResults) noResults.style.display = 'none';
      setTimeout(() => searchInput.focus(), 50);
    }
  });

  menu.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  if (searchInput) {
    searchInput.addEventListener('input', () => {
      const q = searchInput.value.toLowerCase().trim();
      let visibleCount = 0;
      optionsList.querySelectorAll('.picker-option-item').forEach(item => {
        const txt = item.textContent.toLowerCase();
        const match = txt.includes(q);
        item.style.display = match ? 'flex' : 'none';
        if (match) visibleCount++;
      });
      if (noResults) {
        noResults.style.display = visibleCount === 0 ? 'block' : 'none';
      }
    });
  }

  optionsList.querySelectorAll('.picker-option-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      if (container.classList.contains('disabled') || trigger.disabled) return;
      const val = item.dataset.value;
      const selectedOpt = options.find(o => String(o.value) === String(val));

      // Update trigger label
      const triggerText = trigger.querySelector('.picker-trigger-text');
      if (triggerText && selectedOpt) {
        triggerText.textContent = selectedOpt.label;
      }

      // Sync active classes and checkmark icons
      optionsList.querySelectorAll('.picker-option-item').forEach(optEl => {
        const isSelected = String(optEl.dataset.value) === String(val);
        optEl.classList.toggle('selected', isSelected);
        optEl.setAttribute('aria-selected', isSelected ? 'true' : 'false');
        const existingCheck = optEl.querySelector('.picker-option-check');
        if (isSelected && !existingCheck) {
          optEl.insertAdjacentHTML('beforeend', '<i class="fa fa-check picker-option-check" aria-hidden="true"></i>');
        } else if (!isSelected && existingCheck) {
          existingCheck.remove();
        }
      });

      container.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');

      if (searchInput) {
        searchInput.value = '';
        optionsList.querySelectorAll('.picker-option-item').forEach(it => it.style.display = 'flex');
        if (noResults) noResults.style.display = 'none';
      }

      if (onSelect) {
        onSelect(val);
      }
    });
  });
}

function updateSingleSelectPickerValue(field, value) {
  const container = document.getElementById(`picker-${field}`);
  if (!container) return;
  const triggerText = container.querySelector('.picker-trigger-text');
  const optionsList = container.querySelector('.picker-options-list');
  if (!optionsList) return;

  const targetItem = optionsList.querySelector(`.picker-option-item[data-value="${value}"]`);
  if (triggerText && targetItem) {
    const labelSpan = targetItem.querySelector('span');
    if (labelSpan) triggerText.textContent = labelSpan.textContent;
  }

  optionsList.querySelectorAll('.picker-option-item').forEach(optEl => {
    const isSelected = String(optEl.dataset.value) === String(value);
    optEl.classList.toggle('selected', isSelected);
    optEl.setAttribute('aria-selected', isSelected ? 'true' : 'false');
    const existingCheck = optEl.querySelector('.picker-option-check');
    if (isSelected && !existingCheck) {
      optEl.insertAdjacentHTML('beforeend', '<i class="fa fa-check picker-option-check" aria-hidden="true"></i>');
    } else if (!isSelected && existingCheck) {
      existingCheck.remove();
    }
  });
}

function renderFilterUI() {
  if (!choices) return;

  if (choices.years && choices.years.length) {
    renderSingleSelectPicker('picker-year', 'year', choices.years, filterState.year, (val) => {
      filterState.year = parseInt(val);
      writeFarmYear(getFarmId(), filterState.year);
      debouncedQuery();
    }, false);
  }

  if (choices.months && choices.months.length) {
    renderSingleSelectPicker('picker-month', 'month', choices.months, filterState.month, (val) => {
      filterState.month = val;
      notifyFilterChange({ dimOnly: true });
    }, false);
  }

  if (choices.days && choices.days.length) {
    renderSingleSelectPicker('picker-day', 'day', choices.days, filterState.day, (val) => {
      filterState.day = val === 'All' ? 'All' : (isNaN(val) ? val : parseInt(val));
      notifyFilterChange({ dimOnly: true });
    }, true);
  }

  sanitizeFilterStateForChoices();
  renderDimensionFilters();

  const user = getUser();
  const allowEid = typeof canViewEid === 'function' ? canViewEid() : Boolean(user?.is_admin);
  const eidSection = document.getElementById('eid-section');
  if (eidSection) {
    eidSection.classList.toggle('hidden', !allowEid);
    if (allowEid && choices.eids) {
      renderSearchablePicker('picker-eid', 'eid', choices.eids);
    }
  }

  if (choices.measures && choices.measures.length) {
    renderMeasureMultiPicker('picker-measure', choices.measures);
  }

  renderSingleSelectPicker('picker-cohort-percentile', 'cohort_percentile', [
    { value: 10, label: '10%' },
    { value: 15, label: '15%' },
    { value: 20, label: '20%' },
  ], filterState.cohort_percentile || 10, (val) => {
    filterState.cohort_percentile = parseInt(val);
    notifyFilterChange({ dimOnly: false });
  }, false);

  // Active Herd On/Off toggle button and sub-pickers
  const activeToggleBtn = document.getElementById('btn-active-herd-toggle');
  const activeSuboptions = document.querySelectorAll('.active-herd-suboptions');
  if (activeToggleBtn) {
    const syncActiveUI = (isActive) => {
      activeToggleBtn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
      activeToggleBtn.classList.toggle('active', isActive);
      activeToggleBtn.textContent = isActive ? 'On' : 'Off';
      activeToggleBtn.title = isActive ? 'Active herd filter enabled (click to disable)' : 'Active herd filter disabled (click to enable)';
      activeSuboptions.forEach(el => el.classList.toggle('disabled', !isActive));
    };

    syncActiveUI(Boolean(filterState.active_only));

    activeToggleBtn.onclick = () => {
      if (currentActiveModule === 'admin' || activeToggleBtn.disabled) return;
      filterState.active_only = !filterState.active_only;
      syncActiveUI(Boolean(filterState.active_only));
      updateRecordCountBadge();
      notifyFilterChange({ dimOnly: false });
    };
  }

  renderSingleSelectPicker('picker-active-days', 'active_days', [
    { value: 7, label: '7d' },
    { value: 14, label: '14d' },
    { value: 30, label: '30d' },
  ], filterState.active_days || 30, (val) => {
    filterState.active_days = parseInt(val, 10) || 30;
    updateRecordCountBadge();
    syncActiveHerdBadgeLive();
    notifyFilterChange({ dimOnly: false });
  }, false);

  renderSingleSelectPicker('picker-active-ref', 'active_reference_mode', [
    { value: 'auto', label: 'Auto' },
    { value: 'max', label: 'Latest' },
    { value: 'today', label: 'Today' },
  ], filterState.active_reference_mode || 'auto', (val) => {
    filterState.active_reference_mode = val || 'auto';
    updateRecordCountBadge();
    syncActiveHerdBadgeLive();
    notifyFilterChange({ dimOnly: false });
  }, false);

  updateRecordCountBadge();
  syncActiveHerdBadgeLive();
  updateCohortPercentileModuleState();
  updateMeasureModuleState();
  updateAdminSidebarState();
}

/** Show the built-in filters the farm's admin enabled and render pickers for its extra filter columns. */
function renderDimensionFilters() {
  const active = getActiveFilterColumns();
  BUILTIN_FILTER_FIELDS.forEach(field => {
    const picker = document.getElementById(`picker-${field}`);
    const section = picker?.closest('.form-group');
    const enabled = active.includes(field);
    if (section) section.classList.toggle('hidden', !enabled);
    if (enabled) renderSearchablePicker(`picker-${field}`, field, getFieldChoices(field));
  });
  document.querySelectorAll('.filter-group-demographics > .filter-row-2:not(#extra-filters)').forEach(row => {
    row.classList.toggle('hidden', [...row.children].every(c => c.classList.contains('hidden')));
  });

  const wrap = document.getElementById('extra-filters');
  if (!wrap) return;
  const extras = active.filter(col => !BUILTIN_FILTER_FIELDS.includes(col));
  wrap.classList.toggle('hidden', !extras.length);
  wrap.innerHTML = extras.map(col => {
    const field = escapeEmptyText(`${EXTRA_FIELD_PREFIX}${col}`);
    return `
      <div class="form-group">
        <div class="picker-header">
          <label for="picker-btn-${field}">${escapeEmptyText(friendlyColumnLabel(col))}</label>
          <div class="filter-actions">
            <button type="button" class="filter-action-btn" data-select-all="${field}">All</button>
            <span class="action-divider">·</span>
            <button type="button" class="filter-action-btn" data-invert="${field}">Invert</button>
          </div>
        </div>
        <div class="custom-picker" id="${escapeEmptyText(`picker-extra-${col}`)}"></div>
      </div>`;
  }).join('');
  extras.forEach(col => {
    const field = `${EXTRA_FIELD_PREFIX}${col}`;
    renderSearchablePicker(`picker-extra-${col}`, field, getFieldChoices(field));
  });
}

function friendlyColumnLabel(col) {
  const s = String(col).replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

let currentActiveModule = 'summary';

function updateMeasureModuleState(activePage) {
  if (activePage) currentActiveModule = activePage;
  const section = document.getElementById('measure-picker-section');
  const picker = document.getElementById('picker-measure');
  const trigger = document.getElementById('picker-btn-measure');
  const hint = document.getElementById('measure-module-hint');
  const actions = section ? section.querySelector('.filter-actions') : null;
  if (!section) return;

  if (currentActiveModule === 'admin') {
    section.style.opacity = '0.45';
    section.style.pointerEvents = 'none';
    if (picker) {
      picker.classList.add('disabled');
      picker.classList.remove('open', 'dropup');
    }
    if (trigger) {
      trigger.disabled = true;
      trigger.title = 'Data filters apply to analytical views';
    }
    if (actions) actions.style.display = 'none';
    if (hint) hint.style.display = 'none';
  } else if (currentActiveModule === 'anomalies') {
    section.style.opacity = '0.45';
    section.style.pointerEvents = 'none';
    if (picker) {
      picker.classList.add('disabled');
      picker.classList.remove('open');
    }
    if (trigger) {
      trigger.disabled = true;
      trigger.title = 'Anomalies evaluates body weight (configured on page)';
    }
    if (actions) actions.style.display = 'none';
    if (hint) {
      hint.textContent = 'Anomalies uses observed/smoothed body weight (configured on page)';
      hint.style.display = 'block';
    }
  } else if (currentActiveModule === 'prediction') {
    section.style.opacity = '0.45';
    section.style.pointerEvents = 'none';
    if (picker) {
      picker.classList.add('disabled');
      picker.classList.remove('open');
    }
    if (trigger) {
      trigger.disabled = true;
      trigger.title = 'Prediction forecasts FinalPweight';
    }
    if (actions) actions.style.display = 'none';
    if (hint) {
      hint.textContent = 'Prediction forecasts FinalPweight';
      hint.style.display = 'block';
    }
  } else {
    section.style.opacity = '1';
    section.style.pointerEvents = 'auto';
    if (picker) picker.classList.remove('disabled');
    if (trigger) {
      trigger.disabled = false;
      trigger.title = '';
    }
    if (actions) actions.style.display = '';
    if (hint) hint.style.display = 'none';
  }
}

function updateCohortPercentileModuleState(activePage) {
  if (activePage) currentActiveModule = activePage;
  const section = document.getElementById('cohort-percentile-section');
  const picker = document.getElementById('picker-cohort-percentile');
  const trigger = document.getElementById('picker-btn-cohort_percentile');
  const hint = document.getElementById('cohort-percentile-hint');
  if (!section) return;
  const isRelevant = ['cohorts', 'reports'].includes(currentActiveModule);
  if (isRelevant) {
    section.style.opacity = '1';
    section.style.pointerEvents = 'auto';
    if (picker) picker.classList.remove('disabled');
    if (trigger) {
      trigger.disabled = false;
      trigger.title = '';
    }
    if (hint) hint.style.display = 'none';
  } else {
    section.style.opacity = '0.45';
    section.style.pointerEvents = 'none';
    if (picker) {
      picker.classList.add('disabled');
      picker.classList.remove('open');
    }
    if (trigger) {
      trigger.disabled = true;
      trigger.title = 'Applies to Cohorts and Reports modules only';
    }
    if (hint) hint.style.display = 'block';
  }
}

function updateAdminSidebarState(activePage) {
  if (activePage) currentActiveModule = activePage;
  const body = document.getElementById('sidebar-filters-body');
  const hint = document.getElementById('admin-sidebar-hint');
  if (!body) return;
  if (currentActiveModule === 'admin') {
    body.style.opacity = '0.45';
    body.style.pointerEvents = 'none';
    body.querySelectorAll('.custom-picker.open').forEach((p) => {
      p.classList.remove('open', 'dropup');
      p.querySelector('.picker-trigger')?.setAttribute('aria-expanded', 'false');
    });
    // Ensure measure section and child picker explicitly disabled
    const measureSection = document.getElementById('measure-picker-section');
    if (measureSection) {
      measureSection.style.opacity = '0.45';
      measureSection.style.pointerEvents = 'none';
      const measurePicker = document.getElementById('picker-measure');
      if (measurePicker) {
        measurePicker.classList.add('disabled');
        measurePicker.classList.remove('open', 'dropup');
      }
      const measureTrigger = document.getElementById('picker-btn-measure');
      if (measureTrigger) {
        measureTrigger.disabled = true;
      }
      const measureActions = measureSection.querySelector('.filter-actions');
      if (measureActions) measureActions.style.display = 'none';
    }
    // Disable all buttons and inputs in sidebar to block mouse and keyboard interaction
    body.querySelectorAll('button').forEach((b) => {
      b.disabled = true;
    });
    body.querySelectorAll('input').forEach((i) => {
      i.disabled = true;
    });
    if (hint) hint.style.display = 'block';
  } else {
    body.style.opacity = '1';
    body.style.pointerEvents = 'auto';
    body.querySelectorAll('button').forEach((b) => {
      b.disabled = false;
    });
    body.querySelectorAll('input').forEach((i) => {
      i.disabled = false;
    });
    if (hint) hint.style.display = 'none';
    // Re-apply module states
    updateCohortPercentileModuleState(currentActiveModule);
    updateMeasureModuleState(currentActiveModule);
  }
}

async function refreshFarmFilterMetadata(farmId) {
  const targetFarm = farmId || getFarmId();
  const measureHint = localStorage.getItem('livestock_last_measure') || filterState.measure || 'finalpweight';
  try {
    const boot = await fetchFarmBoot(targetFarm, measureHint);
    if (getFarmId() !== targetFarm) return;
    applyFarmBoot(boot, measureHint, targetFarm);
    if (boot?.choices?.total_records != null) {
      updateRecordCountBadge(boot.choices.total_records, boot.choices.total_records);
    }
  } catch (_) { /* ignore silent metadata refresh errors */ }
}

function selectAll(field) {
  if (currentActiveModule === 'admin') return;
  if (!choices) return;
  if (field === 'measure') {
    if (currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
    const all = getMeasureKeys();
    filterState.measures = all.length ? [...all] : [getDefaultMeasure()];
    filterState.measure = filterState.measures[0] || getDefaultMeasure();
    renderFilterUI();
    notifyFilterChange({ dimOnly: false, measureChanged: true });
    return;
  }
  const allChoices = getFieldChoices(field);
  setFieldSelection(field, allChoices.length ? [...allChoices] : ['Overall']);
  renderFilterUI();
  notifyFilterChange(filterChangeOptions(field));
}

function invertSelection(field) {
  if (currentActiveModule === 'admin') return;
  if (!choices) return;
  if (field === 'measure') {
    if (currentActiveModule === 'anomalies' || currentActiveModule === 'prediction') return;
    const all = getMeasureKeys();
    const currentSet = new Set(filterState.measures || [getDefaultMeasure()]);
    let inverted = all.filter(v => !currentSet.has(v));
    if (!inverted.length) inverted = [getDefaultMeasure()];
    filterState.measures = inverted;
    filterState.measure = filterState.measures[0] || getDefaultMeasure();
    renderFilterUI();
    notifyFilterChange({ dimOnly: false, measureChanged: true });
    return;
  }
  const allChoices = getFieldChoices(field);
  const currentSet = new Set(getFieldSelection(field));
  let inverted = allChoices.filter(v => !currentSet.has(v));
  if (!inverted.length) inverted = ['Overall'];
  setFieldSelection(field, inverted);
  renderFilterUI();
  notifyFilterChange(filterChangeOptions(field));
}

function resetFiltersForFarm() {
  filterState = defaultFilters();
  choices = null;
  queryResult = null;
  window.__bootSummary = null;
  updateRecordCountBadge(0, 0, 0, 0);
}

function getFarmBootCache(farmId) {
  return farmId ? farmBootCache[farmId] || null : null;
}

function storeFarmBoot(farmId, boot, measure) {
  if (!farmId || !boot?.choices) return;
  farmBootCache[farmId] = {
    choices: boot.choices,
    summary: boot.summary || null,
    measure: measure || 'finalpweight',
  };
}

function isFarmEmpty() {
  return choices != null && !choices.total_records;
}

function renderFarmEmptyState(container, farmId) {
  if (!container) return;
  const id = farmId || getFarmId();
  container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon"><i class="fa fa-database" aria-hidden="true"></i></div>
        <h3>No Data Available</h3>
        <p>Farm ${id} has no records yet. Once data is uploaded, stats and charts will appear here.</p>
      </div>`;
}

function applyFarmBoot(boot, measureHint, farmId) {
  choices = boot.choices;
  window.__bootSummary = boot.summary || null;
  if (!choices?.total_records) {
    updateRecordCountBadge(0, 0, 0, 0);
    // The admin page is an operational panel for upload, credentials, branding,
    // and farm management; never replace it with an analytical empty state screen.
    if (typeof currentPage === 'undefined' || currentPage !== 'admin') {
      renderFarmEmptyState(document.getElementById('page-content'), farmId);
    }
    return false;
  }
  const preferredYear = readFarmYear(farmId);
  if (preferredYear && Array.isArray(choices.years) && choices.years.includes(preferredYear)) {
    filterState.year = preferredYear;
  } else if (choices?.max_year) {
    filterState.year = choices.max_year;
  }
  if (filterState.year != null) writeFarmYear(farmId, filterState.year);
  try { localStorage.setItem('livestock_last_measure', measureHint); } catch (_) { /* ignore */ }
  filterState.measure = measureHint;
  if (!filterState.measures?.length) filterState.measures = [measureHint];
  renderFilterUI();
  return true;
}

async function fetchFarmBoot(farmId, measureHint) {
  if (farmBootInflight[farmId]) return farmBootInflight[farmId];
  farmBootInflight[farmId] = (async () => {
    const boot = await apiFetch(
      `/filters/bootstrap?farm_id=${encodeURIComponent(farmId)}&measure=${encodeURIComponent(measureHint)}`,
    );
    storeFarmBoot(farmId, boot, measureHint);
    return boot;
  })();
  try {
    return await farmBootInflight[farmId];
  } finally {
    delete farmBootInflight[farmId];
  }
}

function prefetchOtherFarmBoots(currentFarmId, measureHint) {
  const farms = getUser()?.farms || [];
  for (const farm of farms) {
    const farmId = farm?.farm_id;
    if (!farmId || farmId === currentFarmId || farmBootCache[farmId]) continue;
    fetchFarmBoot(farmId, measureHint).then((boot) => {
      const year = boot?.choices?.max_year;
      if (!year || typeof ensureScopeGrain !== 'function') return;
      return ensureScopeGrain({
        farm_id: farmId,
        year,
        month: 'All',
        day: 'All',
        measure: measureHint,
        measures: [measureHint],
        sex: ['Overall'],
        treatment: ['Overall'],
        breed: ['Overall'],
        mob: ['Overall'],
        eid: ['Overall'],
      });
    }).catch(() => {});
  }
}

function clearAllFilters() {
  filterState = defaultFilters();
  if (choices?.max_year) filterState.year = choices.max_year;
  renderFilterUI();
  debouncedQuery();
  showToast('Filters cleared', 'success');
}

let debouncedFilterTimer = null;
function notifyFilterChange(options = {}) {
  clearTimeout(debouncedFilterTimer);
  if (onFilterChange) onFilterChange(options);
}

function debouncedNotifyFilterChange(options = {}, ms = 120) {
  clearTimeout(debouncedFilterTimer);
  debouncedFilterTimer = setTimeout(() => {
    if (onFilterChange) onFilterChange(options);
  }, ms);
}

async function runQuery(options = {}) {
  const { render = true, dimOnly = false } = options;
  try {
    if (render && onFilterChange) {
      onFilterChange({ dimOnly });
      return;
    }
    queryResult = await apiFetch('/data/query', {
      method: 'POST',
      body: JSON.stringify({ ...getFilterPayload(), include_rows: false }),
    });
    updateRecordCountBadge(
      queryResult.record_count,
      queryResult.total_records,
      queryResult.active_animal_count,
      queryResult.total_animal_count
    );
  } catch (err) {
    showToast(err.message, 'error');
  }
}

const debouncedQuery = debounce(() => runQuery({ render: true, dimOnly: false }), 120);

function getFilters() { return getFilterPayload(); }
function getFilterState() { return filterState; }

function setFilterState(state) {
  filterState = { ...defaultFilters(), ...state };
  renderFilterUI();
  debouncedQuery();
}

async function initFilters(callback, bootCache = null) {
  onFilterChange = callback;
  const requestId = ++choicesRequest;

  const farmId = getFarmId();
  const measureHint = localStorage.getItem('livestock_last_measure') || filterState.measure || 'finalpweight';
  const cached = farmBootCache[farmId];

  // Warm path: reuse the last bootstrap for this farm so switches paint immediately.
  if (cached?.choices) {
    if (requestId !== choicesRequest) return;
    if (!applyFarmBoot(cached, measureHint, farmId)) return;
    if (onFilterChange) onFilterChange({ dimOnly: false });
    if (choices?.total_records != null) {
      updateRecordCountBadge(choices.total_records, choices.total_records);
    }
    if (typeof ensureScopeGrain === 'function') {
      ensureScopeGrain(getFilters()).then(() => {
        if (typeof scheduleMeasureGrainPrefetch === 'function') scheduleMeasureGrainPrefetch();
      }).catch(() => {});
    }
    // Refresh quietly; only re-paint if this farm is still selected.
    fetchFarmBoot(farmId, measureHint).then((boot) => {
      if (requestId !== choicesRequest || getFarmId() !== farmId) return;
      const prevTotal = choices?.total_records;
      applyFarmBoot(boot, measureHint, farmId);
      if (boot.choices?.total_records != null && boot.choices.total_records !== prevTotal) {
        updateRecordCountBadge(boot.choices.total_records, boot.choices.total_records);
      }
    }).catch(() => {});
    prefetchOtherFarmBoots(farmId, measureHint);
    const clearBtnWarm = document.getElementById('clear-filters');
    if (clearBtnWarm && !clearBtnWarm.dataset.bound) {
      clearBtnWarm.dataset.bound = '1';
      clearBtnWarm.addEventListener('click', clearAllFilters);
    }
    return;
  }

  try {
    const boot = await fetchFarmBoot(farmId, measureHint);
    if (requestId !== choicesRequest) return;
    if (!applyFarmBoot(boot, measureHint, farmId)) return;
  } catch (_) {
    if (requestId !== choicesRequest) return;
    const yearHint = readFarmYear(farmId) || Number(localStorage.getItem('livestock_last_year')) || 2023;
    if (!filterState.year) filterState.year = yearHint;
    await loadChoices(bootCache);
    if (choices?.max_year) {
      filterState.year = choices.max_year;
      writeFarmYear(farmId, choices.max_year);
    }
    window.__bootSummary = null;
  }

  if (onFilterChange) onFilterChange({ dimOnly: false });
  if (choices?.total_records != null) {
    updateRecordCountBadge(choices.total_records, choices.total_records);
  }
  // Prefetch year grain in background for month/day/measure + timeseries.
  if (typeof ensureScopeGrain === 'function') {
    ensureScopeGrain(getFilters()).then(() => {
      if (typeof scheduleMeasureGrainPrefetch === 'function') scheduleMeasureGrainPrefetch();
    }).catch(() => {});
  }
  prefetchOtherFarmBoots(farmId, measureHint);
  const clearBtn = document.getElementById('clear-filters');
  if (clearBtn && !clearBtn.dataset.bound) {
    clearBtn.dataset.bound = '1';
    clearBtn.addEventListener('click', clearAllFilters);
  }
}

window.initFilters = initFilters;
window.getFilters = getFilters;
window.isFarmEmpty = isFarmEmpty;
window.renderFarmEmptyState = renderFarmEmptyState;
window.getFilterState = getFilterState;
window.setFilterState = setFilterState;
window.clearAllFilters = clearAllFilters;
window.resetFiltersForFarm = resetFiltersForFarm;
window.getFarmBootCache = getFarmBootCache;
window.fetchFarmBoot = fetchFarmBoot;
window.prefetchOtherFarmBoots = prefetchOtherFarmBoots;
window.runQuery = runQuery;
window.updateRecordCountBadge = updateRecordCountBadge;
window.updateCohortPercentileModuleState = updateCohortPercentileModuleState;
window.updateMeasureModuleState = updateMeasureModuleState;
window.updateAdminSidebarState = updateAdminSidebarState;
window.refreshFarmFilterMetadata = refreshFarmFilterMetadata;
window.updateSingleSelectPickerValue = updateSingleSelectPickerValue;
window.renderSingleSelectPicker = renderSingleSelectPicker;
window.hasActiveExtraFilters = hasActiveExtraFilters;
