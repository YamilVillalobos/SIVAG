"""
SIVAG — core/permissions.py
============================
Clases de permisos personalizados basados en roles (RBAC).
Implementan el requisito RNF-SEG-02: Control de Acceso Basado en Roles.

Jerarquía de roles:
  ADMIN > INVESTIGADOR > NORMAL

Permisos disponibles:
  IsAdmin              — Solo administradores
  IsInvestigador       — Solo investigadores
  IsNormalUser         — Solo usuarios normales
  IsInvestigadorOrAdmin — Investigadores o administradores
  IsOwnerOrAdmin       — El propio dueño del objeto O un administrador
  IsActiveUser         — Cualquier usuario autenticado y activo (base)
  ReadOnly             — Permite GET/HEAD/OPTIONS sin autenticación
"""

from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import Rol


# ─────────────────────────────────────────────────────────
# Helper interno
# ─────────────────────────────────────────────────────────

def _is_authenticated_and_active(request) -> bool:
    """Verifica que el request tenga un usuario autenticado y activo."""
    return (
        request.user is not None
        and request.user.is_authenticated
        and request.user.is_active
    )


# ─────────────────────────────────────────────────────────
# 1. IsActiveUser — base para todos los demás
# ─────────────────────────────────────────────────────────

class IsActiveUser(BasePermission):
    """
    Permite el acceso a cualquier usuario autenticado con cuenta activa.
    Bloquea usuarios suspendidos aunque tengan un token JWT válido.

    Uso: como permiso base en vistas que solo requieren estar logueado.
    """

    message = "Tu cuenta está suspendida. Contacta al administrador."

    def has_permission(self, request, view):
        return _is_authenticated_and_active(request)


# ─────────────────────────────────────────────────────────
# 2. IsAdmin
# ─────────────────────────────────────────────────────────

class IsAdmin(BasePermission):
    """
    Permite el acceso ÚNICAMENTE a usuarios con rol ADMIN.

    Uso: panel de gestión de cuentas, logs de auditoría,
         moderación de contenido, monitoreo de infraestructura.
    """

    message = "Acceso restringido al perfil Administrador."

    def has_permission(self, request, view):
        return (
            _is_authenticated_and_active(request)
            and request.user.rol == Rol.ADMIN
        )


# ─────────────────────────────────────────────────────────
# 3. IsInvestigador
# ─────────────────────────────────────────────────────────

class IsInvestigador(BasePermission):
    """
    Permite el acceso ÚNICAMENTE a usuarios con rol INVESTIGADOR.

    Uso: módulo de ingesta Drag & Drop, validación geoespacial,
         edición de atributos, control de publicación de dashboards.
    """

    message = "Esta acción está disponible solo para el perfil Investigador."

    def has_permission(self, request, view):
        return (
            _is_authenticated_and_active(request)
            and request.user.rol == Rol.INVESTIGADOR
        )


# ─────────────────────────────────────────────────────────
# 4. IsNormalUser
# ─────────────────────────────────────────────────────────

class IsNormalUser(BasePermission):
    """
    Permite el acceso ÚNICAMENTE a usuarios con rol NORMAL.

    Uso: historial de consultas, exportaciones propias.
    (En la mayoría de vistas públicas se usa ReadOnly en su lugar.)
    """

    message = "Esta acción está disponible para usuarios registrados."

    def has_permission(self, request, view):
        return (
            _is_authenticated_and_active(request)
            and request.user.rol == Rol.NORMAL
        )


# ─────────────────────────────────────────────────────────
# 5. IsInvestigadorOrAdmin
# ─────────────────────────────────────────────────────────

class IsInvestigadorOrAdmin(BasePermission):
    """
    Permite acceso a INVESTIGADOR o ADMIN.

    Uso: vistas donde el admin necesita actuar sobre recursos
         de investigadores (ej: moderación de proyectos, forzar
         ocultación de una capa).
    """

    message = "Esta acción requiere perfil Investigador o Administrador."

    def has_permission(self, request, view):
        return (
            _is_authenticated_and_active(request)
            and request.user.rol in (Rol.INVESTIGADOR, Rol.ADMIN)
        )


# ─────────────────────────────────────────────────────────
# 6. IsOwnerOrAdmin  (permiso a nivel de objeto)
# ─────────────────────────────────────────────────────────

class IsOwnerOrAdmin(BasePermission):
    """
    Permiso a NIVEL DE OBJETO.

    Permite la acción si:
      a) El usuario es ADMIN (control total), O
      b) El objeto le pertenece al usuario autenticado.

    Detecta automáticamente el campo de propiedad buscando en orden:
      1. obj.usuario       (ExportacionReporte, HistorialConsulta)
      2. obj.investigador  (Proyecto)
      3. obj              (el propio CustomUser — endpoint /me)

    Implementa RNF-SEG-02:
      "un investigador solo puede editar o borrar sus propios activos,
       a menos que el administrador intervenga por moderación."

    Uso en views:
      permission_classes = [IsActiveUser, IsOwnerOrAdmin]
      # Llamar has_object_permission() en retrieve/update/destroy
    """

    message = "No tienes permiso para acceder a este recurso."

    def has_permission(self, request, view):
        return _is_authenticated_and_active(request)

    def has_object_permission(self, request, view, obj):
        # Admin siempre puede
        if request.user.rol == Rol.ADMIN:
            return True

        # Detectar campo de propiedad
        if hasattr(obj, "usuario"):
            return obj.usuario == request.user
        if hasattr(obj, "investigador"):
            return obj.investigador == request.user
        # Caso: el objeto ES el usuario (perfil propio)
        return obj == request.user


# ─────────────────────────────────────────────────────────
# 7. IsOwnerInvestigadorOrAdmin  (variante estricta)
# ─────────────────────────────────────────────────────────

class IsOwnerInvestigadorOrAdmin(BasePermission):
    """
    Igual que IsOwnerOrAdmin pero además exige que el dueño
    sea específicamente un INVESTIGADOR (no un usuario NORMAL).

    Uso: edición de capas y proyectos — un usuario Normal
         nunca debe poder editar aunque fuera "dueño" por error.
    """

    message = "Solo el investigador propietario o el administrador pueden realizar esta acción."

    def has_permission(self, request, view):
        return (
            _is_authenticated_and_active(request)
            and request.user.rol in (Rol.INVESTIGADOR, Rol.ADMIN)
        )

    def has_object_permission(self, request, view, obj):
        if request.user.rol == Rol.ADMIN:
            return True

        owner = getattr(obj, "investigador", getattr(obj, "usuario", None))
        return owner == request.user


# ─────────────────────────────────────────────────────────
# 8. ReadOnly — acceso público de solo lectura
# ─────────────────────────────────────────────────────────

class ReadOnly(BasePermission):
    """
    Permite GET, HEAD y OPTIONS sin autenticación.
    Rechaza cualquier método que modifique datos (POST, PUT, PATCH, DELETE).

    Uso: explorador de proyectos públicos — un visitante sin cuenta
         puede ver dashboards públicos pero no puede crear ni modificar.
    """

    message = "Esta operación requiere autenticación."

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS


# ─────────────────────────────────────────────────────────
# 9. IsOwnerOrReadOnly — combinación frecuente
# ─────────────────────────────────────────────────────────

class IsOwnerOrReadOnly(BasePermission):
    """
    Lectura pública + escritura solo para el dueño o admin.

    Útil para endpoints donde un visitante puede ver
    pero solo el creador (o admin) puede modificar.

    Ejemplo: detalle de un proyecto público — cualquiera lo ve,
             solo el investigador dueño lo edita.
    """

    message = "Solo el propietario puede modificar este recurso."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return _is_authenticated_and_active(request)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True

        if request.user.rol == Rol.ADMIN:
            return True

        owner = getattr(obj, "investigador", getattr(obj, "usuario", obj))
        return owner == request.user