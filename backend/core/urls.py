"""
SIVAG — core/urls.py
=====================
Rutas de autenticación y gestión de perfiles.

Prefijo base: /api/auth/   (definido en sivag_backend/urls.py)

Tabla de endpoints:
  Método  URL                                     Vista                        Descripción
  ------  --------------------------------------  ---------------------------  --------------------------------
  POST    registro/investigador/                  RegistroInvestigadorView     Registro rol INVESTIGADOR
  POST    registro/normal/                        RegistroNormalView           Registro rol NORMAL
  POST    login/                                  LoginView                    Login → devuelve JWT
  POST    logout/                                 LogoutView                   Blacklist refresh token
  POST    token/refresh/                          TokenRefreshView             Renovar access token
  GET     me/                                     MiPerfilView                 Ver perfil propio
  PATCH   me/                                     MiPerfilView                 Actualizar perfil propio
  POST    me/cambiar-password/                    CambioContrasenaView         Cambiar contraseña (autenticado)
  POST    recuperar-password/                     SolicitudRecuperacionView    Solicitar recuperación
  POST    recuperar-password/confirmar/           ConfirmarRecuperacionView    Confirmar nueva contraseña
"""

from django.urls import path
from .views import (
    RegistroInvestigadorView,
    RegistroNormalView,
    LoginView,
    LogoutView,
    TokenRefreshView,
    MiPerfilView,
    CambioContrasenaView,
    SolicitudRecuperacionView,
    ConfirmarRecuperacionView,
)

app_name = "auth"

urlpatterns = [
    # ── Registro ──────────────────────────────────────────
    path(
        "registro/investigador/",
        RegistroInvestigadorView.as_view(),
        name="registro-investigador",
    ),
    path(
        "registro/normal/",
        RegistroNormalView.as_view(),
        name="registro-normal",
    ),

    # ── Sesión ────────────────────────────────────────────
    path(
        "login/",
        LoginView.as_view(),
        name="login",
    ),
    path(
        "logout/",
        LogoutView.as_view(),
        name="logout",
    ),
    path(
        "token/refresh/",
        TokenRefreshView.as_view(),
        name="token-refresh",
    ),

    # ── Perfil propio ─────────────────────────────────────
    path(
        "me/",
        MiPerfilView.as_view(),
        name="mi-perfil",
    ),
    path(
        "me/cambiar-password/",
        CambioContrasenaView.as_view(),
        name="cambiar-password",
    ),

    # ── Recuperación de contraseña ────────────────────────
    path(
        "recuperar-password/",
        SolicitudRecuperacionView.as_view(),
        name="recuperar-password",
    ),
    path(
        "recuperar-password/confirmar/",
        ConfirmarRecuperacionView.as_view(),
        name="recuperar-password-confirmar",
    ),
]