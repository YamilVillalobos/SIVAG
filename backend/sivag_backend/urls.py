"""
SIVAG — sivag_backend/urls.py
==============================
Configuración raíz de URLs del proyecto.

Estructura de prefijos:
  /admin/            — Panel de administración de Django
  /api/auth/         — Autenticación, registro y perfiles (Paso 3)
  /api/capas/        — Ingesta geoespacial y ETL (Paso 4)
  /api/proyectos/    — Gestión de proyectos geoespaciales (Paso 5)

  Próximos pasos:
  /api/admin/        — Panel de administración SIVAG (Paso 8)
  /api/dashboard/    — Endpoints de dashboard e inteligencia visual (Paso 6)
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Panel de administración de Django
    path("admin/", admin.site.urls),

    # ── API v1 ─────────────────────────────────────────────────────────────

    # Paso 3 — Autenticación, registro y perfiles
    path("api/auth/",       include("core.urls",           namespace="auth")),

    # Paso 4 — Ingesta geoespacial (Drag & Drop + ETL)
    path("api/capas/",      include("core.urls_capas",     namespace="capas")),

    # Paso 5 — Gestión de proyectos (CRUD, publicación, versiones)
    path("api/proyectos/",  include("core.urls_proyectos", namespace="proyectos")),

    # ── Próximos módulos (descomentar conforme avance el desarrollo) ────────
    # path("api/dashboard/",  include("core.urls_dashboard",  namespace="dashboard")),
    # path("api/admin/",      include("core.urls_admin",       namespace="sivag-admin")),
]

# ── Archivos media en desarrollo ───────────────────────────────────────────
# En producción Nginx sirve /media/ directamente con alias y X-Accel-Redirect.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)