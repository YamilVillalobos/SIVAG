"""
SIVAG — core/urls_proyectos.py
================================
Rutas del módulo de gestión de proyectos geoespaciales.

Prefijo base: /api/proyectos/  (definido en sivag_backend/urls.py)

  Método   URL                              Vista                      Descripción
  -------  -------------------------------- -------------------------  ----------------------------------------
  GET      /                                ProyectoListCreateView     Explorador público (sin auth requerida)
  POST     /                                ProyectoListCreateView     Crear proyecto (solo INVESTIGADOR)
  GET      /categorias/                     CategoriasDisponiblesView  Filtros de categoría con conteos
  GET      /mis/                            MisProyectosView           Panel del investigador (RF-11)
  GET      /<uuid>/                         ProyectoDetailView         Detalle (público o completo según rol)
  PATCH    /<uuid>/                         ProyectoDetailView         Editar metadatos (dueño o admin)
  DELETE   /<uuid>/                         ProyectoDetailView         Eliminar lógicamente (dueño o admin)
  POST     /<uuid>/toggle/                  ToggleVisibilidadView      Publicar / despublicar (RF-06)
  GET      /<uuid>/versiones/               VersionesProyectoView      Historial de versiones (RF-06)
  POST     /<uuid>/moderar/                 ModerarProyectoView        Ocultar / restaurar (solo ADMIN)
"""

from django.urls import path

from core.views_proyectos import (
    CategoriasDisponiblesView,
    MisProyectosView,
    ModerarProyectoView,
    ProyectoDetailView,
    ProyectoListCreateView,
    ToggleVisibilidadView,
    VersionesProyectoView,
)

app_name = "proyectos"

urlpatterns = [
    # ── Colección ─────────────────────────────────────────
    path(
        "",
        ProyectoListCreateView.as_view(),
        name="lista-crear",
    ),

    # ── Utilidades de UI (sin auth) ───────────────────────
    path(
        "categorias/",
        CategoriasDisponiblesView.as_view(),
        name="categorias",
    ),

    # ── Panel del investigador ────────────────────────────
    path(
        "mis/",
        MisProyectosView.as_view(),
        name="mis-proyectos",
    ),

    # ── Recurso individual ────────────────────────────────
    path(
        "<uuid:proyecto_id>/",
        ProyectoDetailView.as_view(),
        name="detalle",
    ),
    path(
        "<uuid:proyecto_id>/toggle/",
        ToggleVisibilidadView.as_view(),
        name="toggle-visibilidad",
    ),
    path(
        "<uuid:proyecto_id>/versiones/",
        VersionesProyectoView.as_view(),
        name="versiones",
    ),
    path(
        "<uuid:proyecto_id>/moderar/",
        ModerarProyectoView.as_view(),
        name="moderar",
    ),
]
