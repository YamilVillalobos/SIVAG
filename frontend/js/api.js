/**
 * SIVAG — js/api.js
 * Cliente HTTP centralizado con soporte JWT y refresh automático.
 * 
 * Uso:
 *   import { API } from './api.js';
 *   const data = await API.get('/api/auth/me/');
 *   const result = await API.post('/api/auth/login/', { email, password });
 */

const API_BASE = 'http://localhost:8001';

// ── Claves de localStorage ─────────────────────────────────────
export const STORAGE_KEYS = {
  ACCESS:  'sivag_access',
  REFRESH: 'sivag_refresh',
  USER:    'sivag_user',
};

// ── Token helpers ──────────────────────────────────────────────
export const TokenStore = {
  getAccess:  () => localStorage.getItem(STORAGE_KEYS.ACCESS),
  getRefresh: () => localStorage.getItem(STORAGE_KEYS.REFRESH),
  getUser:    () => {
    const raw = localStorage.getItem(STORAGE_KEYS.USER);
    try { return raw ? JSON.parse(raw) : null; } catch { return null; }
  },

  set(access, refresh, user) {
    localStorage.setItem(STORAGE_KEYS.ACCESS, access);
    localStorage.setItem(STORAGE_KEYS.REFRESH, refresh);
    localStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(user));
  },

  updateAccess(access) {
    localStorage.setItem(STORAGE_KEYS.ACCESS, access);
  },

  clear() {
    Object.values(STORAGE_KEYS).forEach(k => localStorage.removeItem(k));
  },

  isAuthenticated() {
    return !!this.getAccess() && !!this.getUser();
  },
};

// ── Refresh token logic (evita múltiples llamadas simultáneas) ──
let _refreshPromise = null;

async function _doRefresh() {
  const refresh = TokenStore.getRefresh();
  if (!refresh) throw new Error('No refresh token');

  const res = await fetch(`${API_BASE}/api/auth/token/refresh/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh }),
  });

  if (!res.ok) {
    TokenStore.clear();
    // Emitir evento global para que router.js redirija
    window.dispatchEvent(new CustomEvent('sivag:session-expired'));
    throw new Error('Session expired');
  }

  const data = await res.json();
  TokenStore.updateAccess(data.access);
  return data.access;
}

async function _refreshToken() {
  if (!_refreshPromise) {
    _refreshPromise = _doRefresh().finally(() => { _refreshPromise = null; });
  }
  return _refreshPromise;
}

// ── Función base request() ──────────────────────────────────────
async function request(method, path, body = null, options = {}) {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;

  const buildHeaders = (token) => {
    const h = { ...options.headers };
    if (!(body instanceof FormData)) {
      h['Content-Type'] = 'application/json';
    }
    if (token) h['Authorization'] = `Bearer ${token}`;
    return h;
  };

  const buildBody = () => {
    if (!body) return undefined;
    if (body instanceof FormData) return body;
    return JSON.stringify(body);
  };

  // Primera llamada
  let token = TokenStore.getAccess();
  let res = await fetch(url, {
    method,
    headers: buildHeaders(token),
    body: buildBody(),
  });

  // Si 401, intentar refresh una vez
  if (res.status === 401 && TokenStore.getRefresh()) {
    try {
      token = await _refreshToken();
      res = await fetch(url, {
        method,
        headers: buildHeaders(token),
        body: buildBody(),
      });
    } catch {
      throw new ApiError(401, 'Sesión expirada. Por favor inicia sesión nuevamente.');
    }
  }

  // Parsear respuesta
  let data = null;
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    data = await res.json();
  } else {
    data = await res.text();
  }

  if (!res.ok) {
    throw new ApiError(res.status, data);
  }

  return data;
}

// ── ApiError ────────────────────────────────────────────────────
export class ApiError extends Error {
  constructor(status, data) {
    // Extraer mensaje legible
    let message = 'Error desconocido';
    if (typeof data === 'string') {
      message = data;
    } else if (data && typeof data === 'object') {
      if (data.detail) message = data.detail;
      else if (data.non_field_errors) message = data.non_field_errors[0];
      else message = JSON.stringify(data);
    }
    super(message);
    this.status = status;
    this.data = data;
    this.fieldErrors = typeof data === 'object' && data !== null ? data : {};
  }

  /**
   * Devuelve errores por campo para mostrar inline en formularios.
   * Ej: { email: "Ya existe un usuario con este email.", ... }
   */
  getFieldErrors() {
    const errors = {};
    if (!this.data || typeof this.data !== 'object') return errors;
    for (const [field, value] of Object.entries(this.data)) {
      if (field === 'detail' || field === 'non_field_errors') continue;
      if (Array.isArray(value)) {
        errors[field] = value[0];
      } else if (typeof value === 'string') {
        errors[field] = value;
      }
    }
    return errors;
  }
}

// ── API pública ─────────────────────────────────────────────────
export const API = {
  get:    (path, opts)        => request('GET',    path, null, opts),
  post:   (path, body, opts)  => request('POST',   path, body, opts),
  patch:  (path, body, opts)  => request('PATCH',  path, body, opts),
  put:    (path, body, opts)  => request('PUT',    path, body, opts),
  delete: (path, opts)        => request('DELETE', path, null, opts),
};
