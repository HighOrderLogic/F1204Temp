function getUser() {
  const raw = sessionStorage.getItem('user');
  return raw ? JSON.parse(raw) : null;
}

function setSession(data) {
  sessionStorage.setItem('access_token', data.access_token);
  sessionStorage.setItem('user', JSON.stringify(data.user));
}

function logout() {
  sessionStorage.removeItem('access_token');
  sessionStorage.removeItem('user');
  sessionStorage.removeItem('boot_cache');
  window.location.href = '/login.html';
}

function requireAuth() {
  const token = sessionStorage.getItem('access_token');
  if (!token) {
    window.location.href = '/login.html';
    return false;
  }
  // Expired token: redirect before the page fires a batch of doomed API calls.
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    if (payload.exp && payload.exp * 1000 < Date.now()) {
      sessionStorage.removeItem('access_token');
      window.location.href = '/login.html';
      return false;
    }
  } catch (_) { /* malformed token: let the API's 401 handling deal with it */ }
  return true;
}

function getFarmId() {
  const params = new URLSearchParams(window.location.search);
  const user = getUser();
  return params.get('farm') || (user?.farms?.[0]?.farm_id) || 'KF';
}

function isOwner() {
  const user = getUser();
  return Boolean(user && (user.role === 'owner' || String(user.username).toLowerCase() === 'owner'));
}

function isAdmin() {
  const user = getUser();
  return Boolean(user && (user.is_admin || user.role === 'admin') && !isOwner());
}

function canViewEid() {
  const user = getUser();
  return Boolean(user && (user.is_admin || user.role === 'admin' || isOwner()));
}

function canExportRaw() {
  const user = getUser();
  return Boolean(user && (user.is_admin || user.role === 'admin' || isOwner()));
}

window.isOwner = isOwner;
window.isAdmin = isAdmin;
window.canViewEid = canViewEid;
window.canExportRaw = canExportRaw;

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('login-form');
  if (form) {
    if (sessionStorage.getItem('access_token')) {
      window.location.href = `/dashboard.html?farm=${getFarmId()}`;
      return;
    }

    // Resolve optional farm-specific logo alongside default partner logos
    const urlParams = new URLSearchParams(window.location.search);
    const farmParam = urlParams.get('farm') || localStorage.getItem('livestock_last_farm');
    if (farmParam) {
      const logosContainer = document.querySelector('.login-brand-logos');
      if (logosContainer && !logosContainer.querySelector('.login-farm-logo')) {
        const farmImg = document.createElement('img');
        farmImg.className = 'login-brand-logo login-farm-logo';
        farmImg.alt = `${farmParam} Farm Logo`;
        farmImg.onerror = () => farmImg.remove();
        farmImg.src = farmParam.toUpperCase() === 'KF' ? '/assets/logos/KF/01_killara.png' : `/assets/logos/${farmParam}/logo.png`;
        logosContainer.prepend(farmImg);
      }
    }
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const errEl = document.getElementById('login-error');
      errEl.classList.add('hidden');
      const btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<div class="loading-spinner" aria-hidden="true"></div> <span>Signing in…</span>';
      }
      try {
        const data = await apiFetch('/auth/login', {
          method: 'POST',
          body: JSON.stringify({
            username: document.getElementById('username').value,
            password: document.getElementById('password').value,
          }),
        });
        setSession(data);
        // Redirect immediately — do not block on dashboard data prefetch.
        const farm = data.user.farms[0]?.farm_id || 'KF';
        // Fire grain on the slim (already warm) function while the browser navigates.
        const apiBase = typeof API_BASE !== 'undefined' ? API_BASE : '/api';
        const year = Number(localStorage.getItem('livestock_last_year')) || 2023;
        const measure = localStorage.getItem('livestock_last_measure') || 'finalpweight';
        fetch(`${apiBase}/charts/timeseries-grain`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${data.access_token}`,
          },
          body: JSON.stringify({
            farm_id: farm,
            year,
            month: 'All',
            day: 'All',
            measure,
            sex: ['Overall'],
            treatment: ['Overall'],
            breed: ['Overall'],
            mob: ['Overall'],
            eid: ['Overall'],
          }),
          keepalive: true,
        }).catch(() => {});
        window.location.href = `/dashboard.html?farm=${farm}`;
      } catch (err) {
        errEl.innerHTML = `<i class="fa fa-circle-exclamation" aria-hidden="true"></i> <span>${err.message || 'Invalid credentials'}</span>`;
        errEl.classList.remove('hidden');
        if (btn) {
          btn.disabled = false;
          btn.innerHTML = '<span>Sign In</span> <i class="fa fa-arrow-right" aria-hidden="true"></i>';
        }
      }
    });
  }

  // Wake serverless function while the user is on the login page.
  const apiBase = typeof API_BASE !== 'undefined' ? API_BASE : '/api';
  const warmHealth = () => fetch(`${apiBase}/health`).then((res) => window.checkConfigError?.(res)).catch(() => {});
  warmHealth();
  setTimeout(warmHealth, 1200);
  const pwd = document.getElementById('password');
  const user = document.getElementById('username');
  const warmOnce = () => { warmHealth(); };
  pwd?.addEventListener('focus', warmOnce, { once: true });
  user?.addEventListener('input', warmOnce, { once: true });

  const logoutBtn = document.getElementById('logout-btn');
  if (logoutBtn) logoutBtn.addEventListener('click', logout);
});

window.getUser = getUser;
window.logout = logout;
window.requireAuth = requireAuth;
window.getFarmId = getFarmId;
