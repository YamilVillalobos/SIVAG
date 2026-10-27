"""
SIVAG — core/urls_capas.py
===========================
Rutas del módulo de ingesta geoespacial.

Prefijo base: /api/capas/  (definido en sivag_backend/urls.py)

  Método  URL                                Descripción
  ------  ---------------------------------  ----------------------------------------
  POST    subir/                             Subir archivo + lanzar ETL
  GET     <uuid:capa_id>/estado/             Estado de procesamiento (vector y raster)
  GET     <uuid:capa_id>/geojson/            Descargar GeoJSON vectorial (Leaflet)
  GET     <uuid:capa_id>/raster/             Descargar GeoTIFF nativo (georaster-leaflet)
  GET     <uuid:capa_id>/atributos/          Listar atributos editables (Excel/CSV)
  PATCH   <uuid:capa_id>/atributos/<uuid>/   Editar un atributo
  DELETE  <uuid:capa_id>/                    Eliminar capa y archivos en disco
"""

from django.urls import path
from core.views_capas import (
    SubirCapaView,
    EstadoCapaView,
    GeoJSONCapaView,
    RasterCapaView,
    AtributosCapaView,
    EditarAtributoView,
    EliminarCapaView,
)

app_name = "capas"

urlpatterns = [
    # ── Subida ────────────────────────────────────────────
    path(
        "subir/",
        SubirCapaView.as_view(),
        name="subir-capa",
    ),

    # ── Consulta de estado (vector y raster) ──────────────
    path(
        "<uuid:capa_id>/estado/",
        EstadoCapaView.as_view(),
        name="estado-capa",
    ),

    # ── Datos vectoriales → GeoJSON ───────────────────────
    path(
        "<uuid:capa_id>/geojson/",
        GeoJSONCapaView.as_view(),
        name="geojson-capa",
    ),

    # ── Datos raster → GeoTIFF nativo ─────────────────────
    path(
        "<uuid:capa_id>/raster/",
        RasterCapaView.as_view(),
        name="raster-capa",
    ),

    # ── Atributos tabulares editables ─────────────────────
    path(
        "<uuid:capa_id>/atributos/",
        AtributosCapaView.as_view(),
        name="atributos-capa",
    ),
    path(
        "<uuid:capa_id>/atributos/<uuid:attr_id>/",
        EditarAtributoView.as_view(),
        name="editar-atributo",
    ),

    # ── Eliminación ───────────────────────────────────────
    # IMPORTANTE: este path va al final porque "<uuid>/" matchea
    # cualquier UUID y entraría en conflicto con las rutas anteriores
    # si se pusiera antes.
    path(
        "<uuid:capa_id>/",
        EliminarCapaView.as_view(),
        name="eliminar-capa",
    ),
]