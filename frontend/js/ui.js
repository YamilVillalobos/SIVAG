/**
 * SIVAG — js/ui.js
 * Helpers de interfaz de usuario: alertas, loading, navbar, errores de formulario.
 */

import { Auth, Roles, logout } from './auth.js';
import { TokenStore } from './api.js';

// ── Alertas ─────────────────────────────────────────────────────
const ALERT_ICONS = {
  error:   '⚠',
  success: '✓',
  info:    'ℹ',
  warning: '⚡',
};

/**
 * Muestra una alerta en el contenedor especificado.
 * @param {string} containerId - ID del elemento donde mostrar la alerta
 * @param {string} message - Mensaje a mostrar
 * @param {'error'|'success'|'info'|'warning'} type
 * @param {boolean} dismissible - Si puede cerrarse
 */
export function showAlert(containerId, message, type = 'error', dismissible = true) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const alertHtml = `
    <div class="sv-alert sv-alert-${type}" role="alert">
      <span class="sv-alert-icon">${ALERT_ICONS[type]}</span>
      <span>${message}</span>
      ${dismissible ? `<button class="sv-alert-close" onclick="this.parentElement.remove()" aria-label="Cerrar">×</button>` : ''}
    </div>
  `;
  container.innerHTML = alertHtml;
  container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

export function clearAlert(containerId) {
  const container = document.getElementById(containerId);
  if (container) container.innerHTML = '';
}

// ── Loading en botón ─────────────────────────────────────────────
/**
 * Activa/desactiva el estado loading en un botón.
 */
export function setButtonLoading(btn, isLoading, originalText = null) {
  if (!btn) return;
  if (isLoading) {
    btn.dataset.originalText = btn.innerHTML;
    btn.innerHTML = `<span class="sv-spinner"></span> Procesando...`;
    btn.disabled = true;
  } else {
    btn.innerHTML = originalText || btn.dataset.originalText || 'Enviar';
    btn.disabled = false;
  }
}

// ── Loading screen global ─────────────────────────────────────────
export function showPageLoading(message = 'Cargando...') {
  let el = document.getElementById('sv-loading-screen');
  if (!el) {
    el = document.createElement('div');
    el.id = 'sv-loading-screen';
    el.className = 'sv-loading-screen';
    el.innerHTML = `
      <div class="sv-loader"></div>
      <span class="sv-loader-text">${message}</span>
    `;
    document.body.appendChild(el);
  }
}

export function hidePageLoading() {
  const el = document.getElementById('sv-loading-screen');
  if (el) el.remove();
}

// ── Errores de formulario ──────────────────────────────────────────
/**
 * Muestra errores campo por campo desde la respuesta del backend.
 * @param {Object} fieldErrors - { fieldName: "mensaje de error", ... }
 * @param {string} formId - ID del formulario
 */
export function showFieldErrors(fieldErrors, formId = null) {
  // Primero limpiar errores previos
  clearFieldErrors(formId);

  for (const [field, message] of Object.entries(fieldErrors)) {
    // Buscar por id, name o data-field
    const input = document.querySelector(
      `#${field}, [name="${field}"], [data-field="${field}"]`
    );
    const errorEl = document.querySelector(
      `#${field}-error, [data-error="${field}"]`
    );

    if (input) {
      input.classList.add('is-invalid');
    }

    if (errorEl) {
      errorEl.textContent = Array.isArray(message) ? message[0] : message;
      errorEl.classList.add('visible');
    }
  }
}

export function clearFieldErrors(formId = null) {
  const scope = formId ? document.getElementById(formId) : document;
  if (!scope) return;

  scope.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));
  scope.querySelectorAll('.is-valid').forEach(el => el.classList.remove('is-valid'));
  scope.querySelectorAll('.sv-field-error').forEach(el => {
    el.textContent = '';
    el.classList.remove('visible');
  });
}

// ── Navbar dinámica por rol ────────────────────────────────────────
/**
 * Renderiza la navbar según el estado de autenticación y rol.
 * Debe llamarse al cargar cada página que tenga <nav id="sv-navbar-nav">.
 */
export function renderNavbar() {
  const navEl = document.getElementById('sv-navbar-nav');
  if (!navEl) return;

  const user = Auth.getUser();
  const isAuth = Auth.isAuthenticated();

  let navItems = '';

  if (!isAuth) {
    // Sin autenticación
    navItems = `
      <li><a href="explorador.html" class="sv-nav-link">Explorador</a></li>
      <li><a href="login.html" class="sv-nav-link">Iniciar sesión</a></li>
      <li><a href="registro.html" class="sv-nav-link"><span class="sv-btn sv-btn-outline sv-btn-sm">Registrarse</span></a></li>
    `;
  } else if (user?.rol === Roles.INVESTIGADOR) {
    navItems = `
      <li><a href="panel.html" class="sv-nav-link">Mi Panel</a></li>
      <li><a href="explorador.html" class="sv-nav-link">Explorador</a></li>
    `;
  } else if (user?.rol === Roles.NORMAL) {
    navItems = `
      <li><a href="explorador.html" class="sv-nav-link">Explorador</a></li>
    `;
  } else if (user?.rol === Roles.ADMIN) {
    navItems = `
      <li><a href="panel.html" class="sv-nav-link">Panel Admin</a></li>
      <li><a href="explorador.html" class="sv-nav-link">Explorador</a></li>
    `;
  }

  navEl.innerHTML = navItems;

  // User badge
  const badgeEl = document.getElementById('sv-user-badge-wrap');
  if (badgeEl) {
    if (isAuth && user) {
      const rolClass = { INVESTIGADOR: 'inv', NORMAL: 'norm', ADMIN: 'adm' }[user.rol] || 'norm';
      const rolLabel = { INVESTIGADOR: 'Investigador', NORMAL: 'Usuario', ADMIN: 'Admin' }[user.rol] || '';
      const initials = (user.nombre_completo || user.username || 'U').charAt(0).toUpperCase();

      badgeEl.innerHTML = `
        <div class="sv-dropdown">
          <div class="sv-user-badge" id="sv-user-dropdown-toggle">
            <div class="sv-user-avatar">${initials}</div>
            <span class="sv-user-name">${user.username}</span>
            <span class="sv-role-badge ${rolClass}">${rolLabel}</span>
          </div>
          <div class="sv-dropdown-menu" id="sv-user-dropdown-menu">
            <a href="panel.html" class="sv-dropdown-item">⚙ Mi Panel</a>
            <div class="sv-dropdown-divider"></div>
            <button class="sv-dropdown-item danger" id="sv-logout-btn">⏻ Cerrar sesión</button>
          </div>
        </div>
      `;

      // Toggle dropdown
      document.getElementById('sv-user-dropdown-toggle')?.addEventListener('click', (e) => {
        e.stopPropagation();
        document.getElementById('sv-user-dropdown-menu')?.classList.toggle('open');
      });

      // Cerrar al hacer click fuera
      document.addEventListener('click', () => {
        document.getElementById('sv-user-dropdown-menu')?.classList.remove('open');
      });

      // Logout
      document.getElementById('sv-logout-btn')?.addEventListener('click', async () => {
        await logout();
        window.location.replace('login.html');
      });
    } else {
      badgeEl.innerHTML = '';
    }
  }

  // Marcar enlace activo
  const currentPage = window.location.pathname.split('/').pop() || 'index.html';
  navEl.querySelectorAll('.sv-nav-link').forEach(link => {
    const href = link.getAttribute('href')?.split('/').pop();
    if (href === currentPage) link.classList.add('active');
  });

  // Mobile toggle
  const toggler = document.getElementById('sv-navbar-toggler');
  toggler?.addEventListener('click', () => {
    navEl.classList.toggle('open');
  });

  // Scroll effect
  window.addEventListener('scroll', () => {
    const navbar = document.getElementById('sv-main-navbar');
    if (navbar) {
      navbar.classList.toggle('scrolled', window.scrollY > 10);
    }
  });
}

// ── Formato de fechas ────────────────────────────────────────────
export function formatDate(isoString) {
  if (!isoString) return '—';
  return new Date(isoString).toLocaleDateString('es-MX', {
    year: 'numeric', month: 'short', day: 'numeric',
  });
}

export function formatDateRelative(isoString) {
  if (!isoString) return '—';
  const diff = Date.now() - new Date(isoString).getTime();
  const days = Math.floor(diff / 86400000);
  if (days === 0) return 'Hoy';
  if (days === 1) return 'Ayer';
  if (days < 7) return `Hace ${days} días`;
  if (days < 30) return `Hace ${Math.floor(days / 7)} sem`;
  return formatDate(isoString);
}

// ── Número formateado ─────────────────────────────────────────────
export function formatNumber(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toString();
}

// ── Skeletons ─────────────────────────────────────────────────────
export function renderSkeletonCards(container, count = 6) {
  container.innerHTML = Array.from({ length: count }, () => `
    <div class="sv-project-card">
      <div class="sv-skeleton" style="height:140px;"></div>
      <div class="sv-project-body">
        <div class="sv-skeleton" style="height:14px;width:80%;margin-bottom:8px;"></div>
        <div class="sv-skeleton" style="height:12px;width:50%;margin-bottom:12px;"></div>
        <div class="sv-skeleton" style="height:10px;width:95%;margin-bottom:6px;"></div>
        <div class="sv-skeleton" style="height:10px;width:70%;"></div>
      </div>
    </div>
  `).join('');
}