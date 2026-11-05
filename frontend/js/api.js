// Local dev: same-origin on :8000, or frontend :3000 → API :8000
function resolveApiBase() {
  if (window.API_URL) return window.API_URL;
  const host = window.location.hostname;
  const port = window.location.port;
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '[::1]';
  if (isLocal && port === '8000') return '/api';
  if (isLocal && port && port !== '8000') {
    const apiHost = host === '[::1]' ? 'localhost' : host;
    return `http://${apiHost}:8000/api`;
  }
  return '/api';
}
const API_BASE = resolveApiBase();

// Prefix same-site API-relative asset URLs (logo bytes served by the API)
function resolveAssetUrl(url) {
  if (!url) return url;
  if (url.startsWith('/api/')) {
    if (API_BASE.startsWith('http://') || API_BASE.startsWith('https://')) {
      const origin = API_BASE.replace(/\/api\/?$/, '');
      return `${origin}${url}`;
    }
    return url;
  }
  return url;
}
window.resolveAssetUrl = resolveAssetUrl;

// Server misconfiguration the user cannot fix (e.g. EID_PSEUDONYM_KEY missing): show the
// server's message in a banner on every page, for users and for whoever deploys the app.
const CONFIG_ERROR_CODES = new Set(['EID_PSEUDONYM_KEY_MISSING']);

function showConfigErrorBanner(message) {
  if (document.getElementById('config-error-banner')) return;
  const banner = document.createElement('div');
  banner.id = 'config-error-banner';
  banner.setAttribute('role', 'alert');
  banner.style.cssText = [
    'position:sticky', 'top:0', 'z-index:10000', 'padding:12px 16px', 'background:#8b1e1e',
    'color:#fff', 'font:14px/1.5 system-ui,sans-serif', 'box-shadow:0 2px 6px rgba(0,0,0,.25)',
  ].join(';');
  const title = document.createElement('strong');
  title.textContent = 'The dashboard is unavailable: the server is misconfigured. Please contact your administrator. ';
  const detail = document.createElement('div');
  detail.style.cssText = 'margin-top:4px;font-family:ui-monospace,monospace;font-size:13px;opacity:.95';
  detail.textContent = `For developers: ${message}`;
  banner.append(title, detail);
  document.body.prepend(banner);
}

async function checkConfigError(res) {
  if (res.status !== 503) return;
  const body = await res.clone().json().catch(() => null);
  if (body && CONFIG_ERROR_CODES.has(body.code)) showConfigErrorBanner(body.detail);
}

async function apiFetch(path, options = {}) {
  const token = sessionStorage.getItem('access_token');
  const headers = { ...options.headers };
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
  }
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const { signal, ...rest } = options;
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...rest, headers, signal });

  } catch (err) {
    if (err && err.name === 'AbortError') throw err;
    throw new Error(
      'Cannot reach the API. Run: cd backend && uvicorn app.main:app --reload --port 8000 then open http://localhost:8000/login.html'
    );
  }
  if (res.status === 401) {
    sessionStorage.removeItem('access_token');
    if (!window.location.pathname.includes('login')) {
      window.location.href = '/login.html';
    }
    throw new Error('Unauthorized');
  }
  if (res.status === 413) {
    throw new Error('File exceeds the 4.5 MB cloud upload limit. Please upload a smaller batch file or use CLI ingestion.');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    if (CONFIG_ERROR_CODES.has(err.code)) showConfigErrorBanner(err.detail);
    const detail = err.detail;
    let message = res.statusText || 'Request failed';
    if (typeof detail === 'string') message = detail;
    else if (Array.isArray(detail)) {
      message = detail.map(e => e.msg || e.message || JSON.stringify(e)).join('; ');
    } else if (detail && typeof detail === 'object') {
      message = detail.msg || detail.message || JSON.stringify(detail);
    }
    throw new Error(message);
  }
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return res;
}

// Single-request upload with real byte progress (fetch has no upload events; XHR does).
function apiUploadSingle(path, file, fields = {}, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE}${path}`);
    const token = sessionStorage.getItem('access_token');
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch (_) { /* non-JSON error body */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new Error(body.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('Upload failed'));
    const fd = new FormData();
    fd.append('file', file);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    xhr.send(fd);
  });
}

async function apiUploadLarge(path, file, fields = {}, onProgress) {
  // Vercel caps serverless request bodies at 4.5 MB, so files above 4 MB are sliced into
  // 4 MB raw chunks staged server-side, then finalized into the normal ingest endpoint.
  const CHUNK = 4 * 1024 * 1024;
  const init = await apiFetch(`${path}/init`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename: file.name }),
  });
  const total = Math.max(1, Math.ceil(file.size / CHUNK));
  onProgress?.(0, file.size);
  for (let i = 0; i < total; i++) {
    const start = i * CHUNK;
    const size = Math.min(CHUNK, file.size - start);
    await apiFetch(`${path}/${init.upload_id}/chunks/${i}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file.slice(start, start + size),
    });
    onProgress?.(Math.min(start + size, file.size), file.size);
  }
  return apiFetch(`${path}/${init.upload_id}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  });
}

async function apiDownload(path, filename) {
  const token = sessionStorage.getItem('access_token');
  const res = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!res.ok) {
    let msg = 'Download failed';
    try {
      const err = await res.json();
      if (err.detail) msg = typeof err.detail === 'string' ? err.detail : msg;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function apiPostDownload(path, body, filename) {
  const token = sessionStorage.getItem('access_token');
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = 'Download failed';
    try {
      const err = await res.json();
      if (err.detail) msg = typeof err.detail === 'string' ? err.detail : msg;
    } catch (_) { /* ignore */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

window.checkConfigError = checkConfigError;
window.apiFetch = apiFetch;
window.apiDownload = apiDownload;
window.apiPostDownload = apiPostDownload;
window.API_BASE = API_BASE;
