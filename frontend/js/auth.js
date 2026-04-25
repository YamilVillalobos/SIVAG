/**
 * SIVAG — js/auth.js
 * Lógica completa de autenticación: login, registro, logout, refresh.
 */

import { API, ApiError, TokenStore } from './api.js';

// ── Roles ───────────────────────────────────────────────────────
export const Roles = {
  ADMIN:         'ADMIN',
  INVESTIGADOR:  'INVESTIGADOR',
  NORMAL:        'NORMAL',
};

// ── Login ────────────────────────────────────────────────────────
/**
 * Autentica al usuario y guarda tokens en localStorage.
 * @returns {Object} { user, access, refresh }
 */
export async function login(email, password) {
  const data = await API.post('/api/auth/login/', { email, password });

  TokenStore.set(data.access, data.refresh, {
    id:             data.user.id,
    email:          data.user.email,
    username:       data.user.username,
    rol:            data.user.rol,
    nombre_completo: data.user.nombre_completo || data.user.username,
    avatar:         data.user.avatar || null,
  });

  return data;
}

// ── Registro Investigador ─────────────────────────────────────────
export async function registroInvestigador(formData) {
  return await API.post('/api/auth/registro/investigador/', formData);
}

// ── Registro Normal ───────────────────────────────────────────────
export async function registroNormal(formData) {
  return await API.post('/api/auth/registro/normal/', formData);
}

// ── Logout ────────────────────────────────────────────────────────
export async function logout() {
  const refresh = TokenStore.getRefresh();
  try {
    if (refresh) {
      await API.post('/api/auth/logout/', { refresh });
    }
  } catch {
    // Si el servidor falla el logout, igual limpiamos local
  } finally {
    TokenStore.clear();
  }
}

// ── Solicitar recuperación de contraseña ─────────────────────────
export async function solicitarRecuperacion(email) {
  return await API.post('/api/auth/recuperar-password/', { email });
}

// ── Confirmar recuperación de contraseña ─────────────────────────
export async function confirmarRecuperacion(token, password_nuevo, password_nuevo2) {
  return await API.post('/api/auth/recuperar-password/confirmar/', {
    token,
    password_nuevo,
    password_nuevo2,
  });
}

// ── Obtener perfil propio ─────────────────────────────────────────
export async function getMiPerfil() {
  return await API.get('/api/auth/me/');
}

// ── Helpers de sesión ─────────────────────────────────────────────
export const Auth = {
  isAuthenticated: () => TokenStore.isAuthenticated(),
  getUser:         () => TokenStore.getUser(),
  getRole:         () => TokenStore.getUser()?.rol || null,
  isAdmin:         () => TokenStore.getUser()?.rol === Roles.ADMIN,
  isInvestigador:  () => TokenStore.getUser()?.rol === Roles.INVESTIGADOR,
  isNormal:        () => TokenStore.getUser()?.rol === Roles.NORMAL,

  /**
   * Retorna la URL de destino post-login según el rol del usuario.
   */
  getPostLoginRedirect() {
    const rol = this.getRole();
    if (rol === Roles.INVESTIGADOR) return 'panel.html';
    if (rol === Roles.ADMIN)        return 'panel.html';
    return 'explorador.html';
  },
};