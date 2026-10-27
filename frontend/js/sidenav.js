/**
 * SIVAG — js/sidenav.js
 * ─────────────────────
 * Inyecta la barra de navegación lateral izquierda en cualquier página.
 * Uso:
 *   import { renderSidenav } from './js/sidenav.js';
 *   renderSidenav('panel');  // pasa el id de la página activa
 *
 * IDs válidos: 'panel' | 'explorador' | 'perfil' | 'ajustes'
 */

import { Auth } from './auth.js';

// ── CSS del sidenav — se inyecta una sola vez en <head> ──────────────
const SIDENAV_CSS = `
  .sv-sidenav {
    width: 220px;
    flex-shrink: 0;
    background: var(--bg-dark);
    border-right: 1px solid var(--border-dim);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    z-index: 200;
    transition: width 0.28s cubic-bezier(.4,0,.2,1);
    position: relative;
  }

  .sv-sidenav.collapsed { width: 58px; }

  /* Logo */
  .sv-sidenav-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 18px 14px 14px;
    border-bottom: 1px solid var(--border-dim);
    text-decoration: none;
    flex-shrink: 0;
    overflow: hidden;
  }

  .sv-sidenav-logo-mark {
    width: 30px; height: 30px;
    border-radius: var(--radius-md);
    background: linear-gradient(135deg, var(--accent-cyan), var(--accent-teal));
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 900;
    color: var(--bg-deep);
    flex-shrink: 0;
  }

  .sv-sidenav-logo-text {
    font-family: var(--font-display);
    font-size: 15px; font-weight: 900;
    color: var(--text-primary);
    letter-spacing: 2px;
    white-space: nowrap;
  }

  .sv-sidenav-logo-sub {
    font-family: var(--font-mono);
    font-size: 7px; letter-spacing: 1.5px;
    text-transform: uppercase;
    color: var(--text-muted);
    white-space: nowrap;
  }

  /* Usuario */
  .sv-sidenav-user {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border-dim);
    overflow: hidden; flex-shrink: 0;
  }

  .sv-sidenav-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: linear-gradient(135deg, var(--accent-blue), var(--accent-cyan));
    display: flex; align-items: center; justify-content: center;
    font-family: var(--font-display); font-size: 13px; font-weight: 700;
    color: #fff; flex-shrink: 0;
  }

  .sv-sidenav-user-info { overflow: hidden; }

  .sv-sidenav-user-name {
    font-family: var(--font-display); font-size: 12px; font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .sv-sidenav-user-role {
    font-family: var(--font-mono); font-size: 9px;
    letter-spacing: 1px; text-transform: uppercase;
    color: var(--accent-cyan); margin-top: 1px;
  }

  /* Nav body */
  .sv-sidenav-nav {
    flex: 1; overflow-y: auto; overflow-x: hidden;
    padding: 8px; scrollbar-width: none;
  }

  .sv-sidenav-section {
    font-family: var(--font-mono); font-size: 8px;
    letter-spacing: 2px; text-transform: uppercase;
    color: var(--text-muted);
    padding: 8px 8px 3px; white-space: nowrap; overflow: hidden;
  }

  .sv-nav-item {
    display: flex; align-items: center; gap: 9px;
    padding: 8px 10px;
    border-radius: var(--radius-md);
    text-decoration: none;
    color: var(--text-secondary);
    font-family: var(--font-display); font-size: 13px; font-weight: 500;
    transition: var(--transition);
    border: 1px solid transparent;
    margin-bottom: 2px;
    white-space: nowrap; overflow: hidden;
    cursor: pointer;
  }

  .sv-nav-item:hover {
    background: var(--bg-glass);
    color: var(--text-primary);
    border-color: var(--border-dim);
  }

  .sv-nav-item.active {
    background: rgba(0,212,255,0.1);
    color: var(--accent-cyan);
    border-color: rgba(0,212,255,0.2);
  }

  .sv-nav-icon { font-size: 14px; flex-shrink: 0; width: 18px; text-align: center; }
  .sv-nav-label { flex: 1; overflow: hidden; text-overflow: ellipsis; }

  /* Footer */
  .sv-sidenav-footer {
    padding: 8px;
    border-top: 1px solid var(--border-dim);
    flex-shrink: 0;
  }

  .sv-sidenav-footer-btn {
    display: flex; align-items: center; gap: 8px;
    width: 100%; padding: 7px 10px;
    background: none; border: 1px solid var(--border-dim);
    border-radius: var(--radius-md);
    color: var(--text-muted);
    font-family: var(--font-mono); font-size: 10px;
    cursor: pointer; transition: var(--transition);
    white-space: nowrap; overflow: hidden;
    margin-bottom: 4px;
  }

  .sv-sidenav-footer-btn:hover { color: var(--accent-cyan); border-color: rgba(0,212,255,0.3); }
  .sv-sidenav-footer-btn.logout:hover { color: var(--accent-red); border-color: rgba(255,68,102,0.3); }

  /* Collapsed */
  .sv-sidenav.collapsed .sv-sidenav-logo-text,
  .sv-sidenav.collapsed .sv-sidenav-logo-sub,
  .sv-sidenav.collapsed .sv-sidenav-user-info,
  .sv-sidenav.collapsed .sv-nav-label,
  .sv-sidenav.collapsed .sv-sidenav-section,
  .sv-sidenav.collapsed .sv-sidenav-footer-btn span:last-child { display: none; }

  .sv-sidenav.collapsed .sv-nav-item   { justify-content: center; padding: 9px; }
  .sv-sidenav.collapsed .sv-sidenav-user { justify-content: center; }
  .sv-sidenav.collapsed .sv-sidenav-logo { justify-content: center; padding: 18px 0 14px; }
  .sv-sidenav.collapsed .sv-sidenav-footer-btn { justify-content: center; }
`;

// ── Nav items: id, href, icon, label ────────────────────────────────
const NAV_ITEMS = [
  { section: 'Principal' },
  { id: 'panel',      href: 'panel.html',      icon: '◈',  label: 'Panel principal' },
  { id: 'explorador', href: 'explorador.html',  icon: '🌐', label: 'Explorador' },
  { section: 'Cuenta' },
  { id: 'perfil',     href: '#',               icon: '👤', label: 'Mi perfil' },
  { id: 'ajustes',    href: '#',               icon: '⚙',  label: 'Ajustes' },
];

// ── Inyectar estilos una sola vez ────────────────────────────────────
function injectStyles() {
  if (document.getElementById('sv-sidenav-styles')) return;
  const style = document.createElement('style');
  style.id = 'sv-sidenav-styles';
  style.textContent = SIDENAV_CSS;
  document.head.appendChild(style);
}

// ── Render principal ─────────────────────────────────────────────────
export function renderSidenav(activeId = '') {
  injectStyles();

  const user   = Auth.getUser();
  const nombre = user?.nombre_completo || user?.username || 'Usuario';
  const rol    = user?.rol === 'ADMIN' ? 'Administrador' : 'Investigador';
  const avatar = nombre.charAt(0).toUpperCase();

  // Construir HTML de los nav items
  const itemsHTML = NAV_ITEMS.map(item => {
    if (item.section) {
      return `<div class="sv-sidenav-section">${item.section}</div>`;
    }
    const isActive = item.id === activeId;
    return `
      <a href="${item.href}" class="sv-nav-item ${isActive ? 'active' : ''}" data-nav="${item.id}">
        <span class="sv-nav-icon">${item.icon}</span>
        <span class="sv-nav-label">${item.label}</span>
      </a>`;
  }).join('');

  const html = `
    <!-- Logo -->
    <a href="panel.html" class="sv-sidenav-logo">
      <div class="sv-sidenav-logo-mark">S</div>
      <div>
        <div class="sv-sidenav-logo-text">SIVAG</div>
        <div class="sv-sidenav-logo-sub">Geoespacial</div>
      </div>
    </a>

    <!-- Usuario -->
    <div class="sv-sidenav-user">
      <div class="sv-sidenav-avatar">${avatar}</div>
      <div class="sv-sidenav-user-info">
        <div class="sv-sidenav-user-name">${nombre}</div>
        <div class="sv-sidenav-user-role">${rol}</div>
      </div>
    </div>

    <!-- Navegación -->
    <nav class="sv-sidenav-nav">${itemsHTML}</nav>

    <!-- Footer -->
    <div class="sv-sidenav-footer">
      <button class="sv-sidenav-footer-btn" id="sv-sidenav-collapse-btn">
        <span style="font-size:12px;" id="sv-sidenav-collapse-icon">◁</span>
        <span>Colapsar</span>
      </button>
      <button class="sv-sidenav-footer-btn logout" id="sv-sidenav-logout-btn">
        <span style="font-size:12px;">⎋</span>
        <span>Cerrar sesión</span>
      </button>
    </div>`;

  // Buscar o crear el contenedor del sidenav
  let sidenav = document.getElementById('sv-sidenav');
  if (!sidenav) {
    sidenav = document.createElement('aside');
    sidenav.id = 'sv-sidenav';
    sidenav.className = 'sv-sidenav';
    // Insertar como primer hijo del sv-app-shell si existe
    const shell = document.getElementById('sv-app-shell');
    if (shell) {
      shell.insertBefore(sidenav, shell.firstChild);
    } else {
      document.body.insertBefore(sidenav, document.body.firstChild);
    }
  }

  sidenav.innerHTML = html;

  // Colapsar / expandir
  let collapsed = false;
  document.getElementById('sv-sidenav-collapse-btn')?.addEventListener('click', () => {
    collapsed = !collapsed;
    sidenav.classList.toggle('collapsed', collapsed);
    const icon = document.getElementById('sv-sidenav-collapse-icon');
    if (icon) icon.textContent = collapsed ? '▷' : '◁';
  });

  // Cerrar sesión
  document.getElementById('sv-sidenav-logout-btn')?.addEventListener('click', () => {
    Auth.logout();
    window.location.href = 'login.html';
  });

  return sidenav;
}
