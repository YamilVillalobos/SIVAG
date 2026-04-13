"""
SIVAG — core/urls_capas.py
===========================
Rutas del módulo de ingesta geoespacial.

Prefijo base: /api/capas/  (definido en sivag_backend/urls.py)

  Método  URL                                Descripción
  ------  ---------------------------------  --------------------------------
  POST    subir/                             Subir archivo + lanzar ETL
  GET     <uuid:capa_id>/estado/             Estado de procesamiento
  GET     <uuid:capa_id>/geojson/            Descargar GeoJSON (para Leaflet)
  GET     <uuid:capa_id>/atributos/          Listar atributos editables
  PATCH   <uuid:capa_id>/atributos/<uuid>/   Editar un atributo
  DELETE  <uuid:capa_id>/                    Eliminar capa
"""

from django.urls import path
from core.views_capas import (
    SubirCapaView,
    EstadoCapaView,
    GeoJSONCapaView,
    AtributosCapaView,
    EditarAtributoView,
    EliminarCapaView,
)

app_name = "capas"

urlpatterns = [
    path(
        "subir/",
        SubirCapaView.as_view(),
        name="subir-capa",
    ),
    path(
        "<uuid:capa_id>/estado/",
        EstadoCapaView.as_view(),
        name="estado-capa",
    ),
    path(
        "<uuid:capa_id>/geojson/",
        GeoJSONCapaView.as_view(),
        name="geojson-capa",
    ),
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
    path(
        "<uuid:capa_id>/",
        EliminarCapaView.as_view(),
        name="eliminar-capa",
    ),
]
