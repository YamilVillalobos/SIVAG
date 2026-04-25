/**
 * SIVAG — js/router.js
 * Protección de rutas y redirección basada en roles.
 *
 * Uso en páginas protegidas (primer script en <body>):
 *   import { Router } from './router.js';
 *   Router.requireAuth();                    // Cualquier usuario autenticado
 *   Router.requireRole('INVESTIGADOR');      // Solo investigadores
 *   Router.requireRole(['INVESTIGADOR', 'ADMIN']); // Múltiples roles
 */

import { Auth, Roles } from './auth.js';

// Mapa de rutas y sus requisitos
const ROUTE_CONFIG = {
  'panel.html':    { roles: [Roles.INVESTIGADOR, Roles.ADMIN] },
  'explorador.html': { roles: null, allowGuest: true },
  'login.html':    { public: true, redirectIfAuth: true },
  'registro.html': { public: true, redirectIfAuth: true },
  'recuperar.html': { public: true },
  'recuperar-confirmar.html': { public: true },
};

export const Router = {
  /**
   * Requiere autenticación. Si no hay sesión activa, redirige a login.
   */
  requireAuth(loginUrl = 'login.html') {
    if (!Auth.isAuthenticated()) {
      const current = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.replace(`${loginUrl}?next=${current}`);
      return false;
    }
    return true;
  },

  /**
   * Requiere un rol específico. Si no tiene el rol, redirige.
   * @param {string|string[]} roles - Rol o array de roles permitidos
   */
  requireRole(roles, unauthorizedUrl = 'explorador.html') {
    if (!this.requireAuth()) return false;

    const rolesArray = Array.isArray(roles) ? roles : [roles];
    const userRole = Auth.getRole();

    if (!rolesArray.includes(userRole)) {
      window.location.replace(unauthorizedUrl);
      return false;
    }
    return true;
  },

  /**
   * Si ya está autenticado, redirige al destino según rol.
   * Útil para login.html y registro.html.
   */
  redirectIfAuthenticated() {
    if (Auth.isAuthenticated()) {
      const redirectTo = Auth.getPostLoginRedirect();
      window.location.replace(redirectTo);
      return true;
    }
    return false;
  },

  /**
   * Maneja la redirección post-login (respeta parámetro ?next=)
   */
  postLoginRedirect() {
    const params = new URLSearchParams(window.location.search);
    const next = params.get('next');

    if (next && !next.startsWith('//') && !next.startsWith('http')) {
      window.location.replace(next);
    } else {
      window.location.replace(Auth.getPostLoginRedirect());
    }
  },
};

// ── Escuchar expiración de sesión ──────────────────────────────
window.addEventListener('sivag:session-expired', () => {
  const current = encodeURIComponent(window.location.pathname);
  window.location.replace(`login.html?next=${current}&reason=expired`);
});