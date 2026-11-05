function loadSavedViews() {
  try {
    return JSON.parse(localStorage.getItem('livestock_saved_views') || '{}');
  } catch { return {}; }
}

function saveSavedViews(views) {
  localStorage.setItem('livestock_saved_views', JSON.stringify(views));
}

function renderSavedViews() {
  const list = document.getElementById('saved-views-list');
  if (!list) return;
  const views = loadSavedViews();
  const names = Object.keys(views);
  if (!names.length) {
    list.innerHTML = '<p style="font-size:12px;color:var(--text-secondary)">No saved views yet</p>';
    fitSavedViewsList();
    return;
  }
  list.innerHTML = '';
  names.forEach(name => {
    const item = document.createElement('div');
    item.className = 'saved-view-item';

    const labelSpan = document.createElement('span');
    labelSpan.textContent = name;

    const actionSpan = document.createElement('span');

    const loadBtn = document.createElement('button');
    loadBtn.type = 'button';
    loadBtn.className = 'btn btn-sm btn-secondary';
    loadBtn.title = 'Load';
    loadBtn.setAttribute('aria-label', `Load view ${name}`);
    loadBtn.innerHTML = '<i class="fa fa-play" aria-hidden="true"></i>';
    loadBtn.addEventListener('click', () => loadView(name));

    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.className = 'btn btn-sm btn-danger';
    deleteBtn.title = 'Delete';
    deleteBtn.setAttribute('aria-label', `Delete view ${name}`);
    deleteBtn.innerHTML = '<i class="fa fa-trash" aria-hidden="true"></i>';
    deleteBtn.addEventListener('click', () => deleteView(name));

    actionSpan.appendChild(loadBtn);
    actionSpan.appendChild(document.createTextNode(' '));
    actionSpan.appendChild(deleteBtn);

    item.appendChild(labelSpan);
    item.appendChild(actionSpan);
    list.appendChild(item);
  });
  fitSavedViewsList();
}

function saveView() {
  const input = document.getElementById('view-name');
  const name = input?.value?.trim();
  if (!name) { showToast('Enter a view name', 'error'); return; }
  const views = loadSavedViews();
  const state = getFilterState();
  const user = getUser();
  views[name] = {
    ...state,
    timestamp: new Date().toISOString(),
  };
  if (!(typeof canViewEid === 'function' ? canViewEid() : user?.is_admin)) delete views[name].eid;
  saveSavedViews(views);
  if (input) input.value = '';
  renderSavedViews();
  showToast(`Saved view "${name}"`, 'success');
}

function loadView(name) {
  const views = loadSavedViews();
  if (views[name]) {
    setFilterState(views[name]);
    showToast(`Loaded view "${name}"`, 'success');
  }
}

function deleteView(name) {
  const views = loadSavedViews();
  delete views[name];
  saveSavedViews(views);
  renderSavedViews();
  showToast(`Deleted view "${name}"`, 'success');
}

// Dynamically adjust saved views list max-height to fill available viewport space
// on large displays while enforcing a safe clamp (max 40% viewport, min 120px)
// to prevent the sticky dock from occluding sidebar filter controls.
function fitSavedViewsList() {
  const list = document.getElementById('saved-views-list');
  const dock = list?.closest('.sidebar-sticky-dock');
  if (!list || !dock) return;

  const rect = list.getBoundingClientRect();
  const spaceToBottom = window.innerHeight - rect.top - 16;
  const maxSafeCap = Math.floor(window.innerHeight * 0.40);
  const targetMaxHeight = Math.max(120, Math.min(spaceToBottom, maxSafeCap));

  list.style.maxHeight = `${targetMaxHeight}px`;
}

function initSavedViews() {
  renderSavedViews();
  document.getElementById('save-view')?.addEventListener('click', saveView);
  document.getElementById('clear-all-views')?.addEventListener('click', () => {
    const names = Object.keys(loadSavedViews());
    if (!names.length) {
      showToast('No saved views to clear', 'info');
      return;
    }
    if (!confirm(`Delete all ${names.length} saved views?`)) return;
    saveSavedViews({});
    renderSavedViews();
    showToast(`Cleared ${names.length} saved view${names.length === 1 ? '' : 's'}`, 'success');
  });

  let resizeScheduled = false;
  window.addEventListener('resize', () => {
    if (!resizeScheduled) {
      resizeScheduled = true;
      requestAnimationFrame(() => {
        fitSavedViewsList();
        resizeScheduled = false;
      });
    }
  });
}

window.initSavedViews = initSavedViews;
