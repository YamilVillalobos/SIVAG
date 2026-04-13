"""
SIVAG — sivag_backend/urls.py
==============================
Configuración raíz de URLs del proyecto.

Estructura de prefijos:
  /admin/          — Panel de administración de Django
  /api/auth/       — Autenticación y perfiles (core)
  /api/            — Resto de la API (pasos futuros: proyectos, capas, etc.)

A medida que avance el desarrollo se irán añadiendo más include():
  /api/proyectos/  — Paso 4 (gestión de proyectos)
  /api/capas/      — Paso 5 (ingesta geoespacial)
  /api/admin/      — Paso 9 (panel de administración)
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Panel de administración de Django
    path("admin/", admin.site.urls),

    # ── API v1 ────────────────────────────────────────────
    # Autenticación, registro y perfiles
    path("api/auth/", include("core.urls", namespace="auth")),

    # Ingesta geoespacial (Paso 4)
    path("api/capas/",     include("core.urls_capas",     namespace="capas")),

    # Aquí irán los próximos módulos:
    # path("api/proyectos/", include("core.urls_proyectos", namespace="proyectos")),
    # path("api/admin/",     include("core.urls_admin",     namespace="sivag-admin")),
]

# Servir archivos media en desarrollo (avatares, capas subidas, reportes)
# En producción Nginx se encarga de esto directamente
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)