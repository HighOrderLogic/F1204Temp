let tablePage = 1;
let pageSize = 20;

function computePageSize() {
  // Aim for rows that fit the viewport without crowding.
  const available = Math.max(320, window.innerHeight - 280);
  const rowH = 36;
  const fit = Math.floor(available / rowH);
  return Math.max(10, Math.min(40, fit || 20));
}

function friendlyCol(col) {
  const map = {
    date: 'Date',
    eid: 'EID',
    sex: 'Sex',
    breed: 'Breed',
    treatment: 'Treatment',
    mob: 'Mob',
    weight: 'Weight',
    pweight: 'PWeight',
    finalpweight: 'Final PWeight',
    finalgrowthpbs: 'Final Growth PBS',
    finaldailygrowth: 'Final Daily Growth',
    feedintakekgd: 'Feed Intake',
    methane: 'Methane',
    animalvalue: 'Animal Value',
    animalprod: 'Animal Prod',
    carcassweight: 'Carcass Weight',
  };
  return map[col] || col;
}

function buildPageButtons(page, pageCount) {
  if (!pageCount) return '';
  const buttons = [];
  const windowSize = 5;
  let start = Math.max(1, page - Math.floor(windowSize / 2));
  let end = Math.min(pageCount, start + windowSize - 1);
  start = Math.max(1, end - windowSize + 1);

  if (start > 1) {
    buttons.push(`<button class="btn btn-secondary btn-sm page-btn" data-page="1">1</button>`);
    if (start > 2) buttons.push(`<span class="page-ellipsis">…</span>`);
  }
  for (let p = start; p <= end; p++) {
    buttons.push(
      `<button class="btn btn-sm page-btn ${p === page ? 'btn-primary' : 'btn-secondary'}" data-page="${p}">${p}</button>`
    );
  }
  if (end < pageCount) {
    if (end < pageCount - 1) buttons.push(`<span class="page-ellipsis">…</span>`);
    buttons.push(
      `<button class="btn btn-secondary btn-sm page-btn" data-page="${pageCount}">${pageCount}</button>`
    );
  }
  return buttons.join('');
}

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function renderDataManagement(container) {
  const allowExport = typeof canExportRaw === 'function' ? canExportRaw() : Boolean(getUser()?.is_admin);
  if (!allowExport) {
    container.innerHTML = `
      <div class="alert alert-warning">
        <strong>Restricted access.</strong> Raw data viewing and CSV download are restricted to administrators and farm owners.
      </div>`;
    return;
  }

  const currentFilters = getFilters();
  if (!currentFilters.year) {
    container.innerHTML = '<div class="alert alert-muted">Loading filters…</div>';
    return;
  }

  const currentFarm = currentFilters.farm_id || getFarmId() || 'KF';
  pageSize = computePageSize();
  showLoading(container);

  try {
    const data = await apiFetch('/data/query', {
      method: 'POST',
      body: JSON.stringify({
        ...currentFilters,
        include_rows: true,
        page: tablePage,
        page_size: pageSize,
      }),
    });

    tablePage = data.page || tablePage;
    const pageCount = data.page_count || 0;
    const cols = data.columns?.length
      ? data.columns
      : ['date', 'eid', 'sex', 'breed', 'treatment', 'mob', 'finalpweight'];

    const startIdx = data.record_count ? (tablePage - 1) * pageSize + 1 : 0;
    const endIdx = Math.min(tablePage * pageSize, data.record_count || 0);

    let dataSectionHtml = '';
    if (!data.record_count) {
      dataSectionHtml = `
        <div class="empty-state card">
          <h3>No Data Found</h3>
          <p>No livestock records match your current filter criteria.</p>
        </div>`;
    } else {
      dataSectionHtml = `
        <div class="data-mgmt-header">
          <div>
            <h2>Data Management</h2>
            <p class="data-mgmt-meta">Showing ${startIdx.toLocaleString()}–${endIdx.toLocaleString()} of ${data.record_count.toLocaleString()} filtered records (${pageSize}/page)</p>
          </div>
          <button class="btn btn-primary" id="export-csv"><i class="fa fa-download"></i> Download CSV</button>
        </div>
        <div class="table-wrap card">
          <table class="data-table">
            <thead><tr>${cols.map(c => `<th>${friendlyCol(c)}</th>`).join('')}</tr></thead>
            <tbody>
              ${(data.filtered || []).map(row => `
                <tr>${cols.map(c => {
                  let v = row[c];
                  if (c === 'treatment' && (v == null || v === '')) v = 'No Treatment';
                  return `<td>${v ?? ''}</td>`;
                }).join('')}</tr>
              `).join('')}
            </tbody>
          </table>
        </div>
        <div class="pagination-bar">
          <button class="btn btn-secondary btn-sm" id="prev-page" ${tablePage <= 1 ? 'disabled' : ''}>Previous</button>
          <div class="page-numbers">${buildPageButtons(tablePage, pageCount)}</div>
          <button class="btn btn-secondary btn-sm" id="next-page" ${tablePage >= pageCount ? 'disabled' : ''}>Next</button>
          <label class="page-jump">Go to
            <input type="number" id="jump-page" min="1" max="${Math.max(pageCount, 1)}" value="${tablePage}">
          </label>
          <button class="btn btn-secondary btn-sm" id="jump-go">Go</button>
        </div>`;
    }

    container.innerHTML = dataSectionHtml;

    // Attach Data Table Event Listeners if data present
    if (data.record_count) {
      document.getElementById('export-csv').onclick = async () => {
        const f = getFilters();
        const params = new URLSearchParams({
          farm_id: f.farm_id,
          year: f.year,
          month: f.month,
          day: f.day,
          sex: f.sex.join('|'),
          treatment: f.treatment.join('|'),
          breed: f.breed.join('|'),
          mob: f.mob.join('|'),
          eid: f.eid.join('|'),
          extra_filters: JSON.stringify(f.extra_filters || {}),
        });
        try {
          await apiDownload(`/data/export.csv?${params}`, `${f.farm_id}_export.csv`);
          showToast('CSV downloaded', 'success');
        } catch (err) {
          showToast(err.message || 'Download failed', 'error');
        }
      };

      const goTo = (p) => {
        tablePage = Math.max(1, Math.min(pageCount || 1, p));
        renderDataManagement(container);
      };

      document.getElementById('prev-page')?.addEventListener('click', () => goTo(tablePage - 1));
      document.getElementById('next-page')?.addEventListener('click', () => goTo(tablePage + 1));
      container.querySelectorAll('.page-btn').forEach(btn => {
        btn.addEventListener('click', () => goTo(parseInt(btn.dataset.page, 10)));
      });
      document.getElementById('jump-go')?.addEventListener('click', () => {
        const val = parseInt(document.getElementById('jump-page').value, 10);
        if (!Number.isFinite(val)) return;
        goTo(val);
      });
      document.getElementById('jump-page')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          const val = parseInt(e.target.value, 10);
          if (Number.isFinite(val)) goTo(val);
        }
      });
    }
  } catch (err) {
    container.innerHTML = `<div class="alert alert-warning">${err.message}</div>`;
  }
}

function resetDataManagementPage() {
  tablePage = 1;
}

window.renderDataManagement = renderDataManagement;
window.resetDataManagementPage = resetDataManagementPage;

