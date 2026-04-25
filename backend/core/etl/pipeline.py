"""
SIVAG — core/etl/pipeline.py
==============================
Orquestador principal del pipeline ETL.

Formatos soportados:
  Excel   (.xlsx) → GeoJSON + AtributoTabular
  CSV     (.csv)  → GeoJSON + AtributoTabular
  Shape   (.zip)  → GeoJSON reproyectado a EPSG:4326
  GeoTIFF (.tiff) → GeoTIFF nativo con overviews (sin conversión)

Es el único punto de entrada que deben llamar las vistas Django.

Uso:
    from core.etl.pipeline import run_etl_pipeline
    resultado = run_etl_pipeline(capa_id=str(capa.id), request=request)
"""

from __future__ import annotations

import logging
import os
import traceback

from django.conf import settings
from django.utils import timezone

from core.models import (
    AccionLog,
    AtributoTabular,
    CapaGeoespacial,
    EstadoValidacion,
    LogAuditoria,
    TipoArchivo,
    TipoGeometria,
    VersionCapa,
)
from core.etl.validators import (
    validate_excel,
    validate_csv,
    validate_shapefile,
    validate_geotiff,
)
from core.etl.converters import (
    convert_excel_to_geojson,
    convert_csv_to_geojson,
    convert_shapefile_to_geojson,
    process_geotiff_native,
    _geojson_output_path,
    _raster_output_path,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Helpers de request
# ─────────────────────────────────────────────────────────

def _get_ip(request) -> str:
    if request is None:
        return ""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _get_ua(request) -> str:
    if request is None:
        return ""
    return request.META.get("HTTP_USER_AGENT", "")[:500]


# ─────────────────────────────────────────────────────────
# Pipeline principal
# ─────────────────────────────────────────────────────────

def run_etl_pipeline(capa_id: str, request=None) -> dict:
    """
    Ejecuta el pipeline ETL completo para una CapaGeoespacial.

    Pasos:
      1. Cargar la instancia desde BD.
      2. Marcar estado PROCESANDO.
      3. Ejecutar el validador según tipo_archivo.
      4. Si falla → marcar ERROR + log.
      5. Si pasa  → ejecutar el conversor/procesador correspondiente.
      6. Persistir resultados en CapaGeoespacial y modelos relacionados.
      7. Marcar APROBADO + log de éxito.

    Retorna:
      { "ok", "capa_id", "estado", "mensaje", "meta" }
    """
    resultado = {
        "ok":      False,
        "capa_id": capa_id,
        "estado":  EstadoValidacion.ERROR,
        "mensaje": "",
        "meta":    {},
    }

    # ── 1. Cargar capa ──────────────────────────────────
    try:
        capa = CapaGeoespacial.objects.select_related("proyecto").get(pk=capa_id)
    except CapaGeoespacial.DoesNotExist:
        resultado["mensaje"] = f"No existe ninguna capa con id={capa_id}."
        return resultado

    usuario    = capa.proyecto.investigador
    archivo_path = capa.archivo_original.path
    tipo       = capa.tipo_archivo
    media_root = settings.MEDIA_ROOT

    # ── 2. Marcar PROCESANDO ────────────────────────────
    capa.estado_validacion = EstadoValidacion.PROCESANDO
    capa.save(update_fields=["estado_validacion"])

    # ── 3. Validar ──────────────────────────────────────
    try:
        if tipo == TipoArchivo.EXCEL:
            vr = validate_excel(archivo_path)
        elif tipo == TipoArchivo.CSV:
            vr = validate_csv(archivo_path)
        elif tipo == TipoArchivo.SHAPE:
            vr = validate_shapefile(archivo_path)
        elif tipo == TipoArchivo.GEOTIFF:
            vr = validate_geotiff(archivo_path)
        else:
            vr = _error_result(f"Tipo de archivo '{tipo}' no soportado.")
    except Exception:
        logger.error("Error en validador, capa %s:\n%s", capa_id, traceback.format_exc())
        vr = _error_result("Error interno del validador. Contacta al administrador.")

    # ── 4. Fallo de validación ───────────────────────────
    if not vr.ok:
        capa.estado_validacion = EstadoValidacion.ERROR
        capa.mensaje_error     = vr.error_msg
        capa.save(update_fields=["estado_validacion", "mensaje_error"])
        _log_auditoria(request, AccionLog.VALIDACION_ERR, usuario,
                       "CapaGeoespacial", capa_id,
                       {"error": vr.error_msg, "tipo": tipo})
        resultado["mensaje"] = vr.error_msg
        return resultado

    # ── 5. Convertir / procesar ──────────────────────────
    try:
        conversion_result = _run_conversion(capa, vr.meta, archivo_path, media_root)
    except Exception:
        logger.error("Error en conversor, capa %s:\n%s", capa_id, traceback.format_exc())
        error_msg = "Error durante el procesamiento del archivo. Contacta al administrador."
        capa.estado_validacion = EstadoValidacion.ERROR
        capa.mensaje_error     = error_msg
        capa.save(update_fields=["estado_validacion", "mensaje_error"])
        resultado["mensaje"] = error_msg
        return resultado

    # ── 6. Persistir ─────────────────────────────────────
    _apply_conversion_to_capa(capa, vr.meta, conversion_result, tipo)

    # ── 7. AtributoTabular (Excel y CSV) ─────────────────
    if tipo in (TipoArchivo.EXCEL, TipoArchivo.CSV) and "filas" in conversion_result:
        _create_atributos(capa, conversion_result["filas"])

    # ── 8. Versión ───────────────────────────────────────
    _create_version(capa, usuario)

    # ── 9. Marcar APROBADO ───────────────────────────────
    capa.estado_validacion = EstadoValidacion.APROBADO
    capa.fecha_procesado   = timezone.now()
    capa.mensaje_error     = ""
    capa.save(update_fields=[
        "estado_validacion", "fecha_procesado", "mensaje_error",
        "geojson_path", "raster_path", "num_features",
        "tipo_geometria", "sistema_coordenadas",
        "columnas_schema", "columna_lat", "columna_lon",
        "raster_banda_min", "raster_banda_max",
    ])

    _log_auditoria(request, AccionLog.VALIDACION_OK, usuario,
                   "CapaGeoespacial", capa_id,
                   {"tipo": tipo, "num_features": capa.num_features,
                    "crs": capa.sistema_coordenadas})

    resultado.update({
        "ok":      True,
        "estado":  EstadoValidacion.APROBADO,
        "mensaje": f"Archivo procesado correctamente. {capa.num_features} feature(s) importados.",
        "meta":    vr.meta,
    })
    return resultado


# ─────────────────────────────────────────────────────────
# Sub-rutinas internas
# ─────────────────────────────────────────────────────────

def _run_conversion(capa, meta: dict, archivo_path: str, media_root: str) -> dict:
    """Despacha al conversor correcto según el tipo de archivo."""
    tipo        = capa.tipo_archivo
    proyecto_id = str(capa.proyecto.id)
    capa_id     = str(capa.id)

    if tipo == TipoArchivo.EXCEL:
        output_path = _geojson_output_path(media_root, proyecto_id, capa_id)
        return convert_excel_to_geojson(
            filepath=archivo_path,
            col_lat=meta["columna_lat"],
            col_lon=meta["columna_lon"],
            output_path=output_path,
        )

    elif tipo == TipoArchivo.CSV:
        output_path = _geojson_output_path(media_root, proyecto_id, capa_id)
        return convert_csv_to_geojson(
            filepath=archivo_path,
            col_lat=meta["columna_lat"],
            col_lon=meta["columna_lon"],
            separador=meta.get("separador_detectado", ","),
            encoding=meta.get("encoding_detectado", "utf-8"),
            output_path=output_path,
        )

    elif tipo == TipoArchivo.SHAPE:
        output_path = _geojson_output_path(media_root, proyecto_id, capa_id)
        return convert_shapefile_to_geojson(
            zip_filepath=archivo_path,
            shp_filename=meta["shp_filename"],
            output_path=output_path,
        )

    elif tipo == TipoArchivo.GEOTIFF:
        # El GeoTIFF se procesa nativo: copia + overviews, sin conversión
        output_path = _raster_output_path(media_root, proyecto_id, capa_id)
        return process_geotiff_native(
            input_filepath=archivo_path,
            output_path=output_path,
        )

    return {}


def _apply_conversion_to_capa(
    capa: CapaGeoespacial,
    meta: dict,
    conversion: dict,
    tipo: str,
) -> None:
    """Escribe los resultados del procesamiento en los campos de la capa."""
    capa.sistema_coordenadas = meta.get("sistema_coordenadas", "")
    capa.columnas_schema     = meta.get("columnas_schema", {})

    if tipo in (TipoArchivo.EXCEL, TipoArchivo.CSV):
        capa.geojson_path   = _relative_path(conversion.get("geojson_path", ""))
        capa.num_features   = conversion.get("num_features", 0)
        capa.tipo_geometria = TipoGeometria.PUNTO
        capa.columna_lat    = meta.get("columna_lat", "")
        capa.columna_lon    = meta.get("columna_lon", "")

    elif tipo == TipoArchivo.SHAPE:
        capa.geojson_path   = _relative_path(conversion.get("geojson_path", ""))
        capa.num_features   = conversion.get("num_features", 0)
        capa.tipo_geometria = conversion.get("tipo_geometria", TipoGeometria.PUNTO)

    elif tipo == TipoArchivo.GEOTIFF:
        # El archivo se guarda tal cual, sin conversión a GeoJSON
        capa.raster_path      = _relative_path(conversion.get("raster_path", ""))
        capa.raster_banda_min = conversion.get("banda_min")
        capa.raster_banda_max = conversion.get("banda_max")
        capa.tipo_geometria   = TipoGeometria.RASTER
        # num_features = número de bandas (más informativo que 1 para un ráster)
        capa.num_features     = meta.get("num_bandas", 1)


def _relative_path(absolute_path: str) -> str:
    """Convierte ruta absoluta a relativa respecto a MEDIA_ROOT."""
    if not absolute_path:
        return ""
    media_root = str(settings.MEDIA_ROOT)
    if absolute_path.startswith(media_root):
        rel = absolute_path[len(media_root):]
        return rel.lstrip(os.sep)
    return absolute_path


def _create_atributos(capa: CapaGeoespacial, filas: list[dict]) -> None:
    """Crea registros AtributoTabular en bulk (lotes de 500)."""
    from django.contrib.gis.geos import Point as GEOSPoint

    objs = []
    for fila in filas:
        lat   = fila.get("lat")
        lon   = fila.get("lon")
        punto = GEOSPoint(lon, lat, srid=4326) if lat is not None and lon is not None else None

        objs.append(AtributoTabular(
            capa=capa,
            indice_original=fila["indice"],
            latitud=lat,
            longitud=lon,
            punto=punto,
            datos=fila["datos"],
            modificado_por=None,
        ))

    BATCH_SIZE = 500
    for i in range(0, len(objs), BATCH_SIZE):
        AtributoTabular.objects.bulk_create(objs[i:i + BATCH_SIZE])


def _create_version(capa: CapaGeoespacial, usuario) -> None:
    """Registra una nueva VersionCapa."""
    ultimo = VersionCapa.objects.filter(capa=capa).order_by("-numero_version").first()
    numero = (ultimo.numero_version + 1) if ultimo else 1

    VersionCapa.objects.create(
        capa=capa,
        numero_version=numero,
        archivo_version=capa.archivo_original,
        nota_version=f"Procesado automáticamente vía ETL – {timezone.now():%Y-%m-%d %H:%M}",
        creado_por=usuario,
    )


def _log_auditoria(request, accion, usuario, objeto_tipo, objeto_id, datos_extra) -> None:
    try:
        LogAuditoria.objects.create(
            usuario=usuario,
            accion=accion,
            objeto_tipo=objeto_tipo,
            objeto_id=str(objeto_id),
            ip_origen=_get_ip(request),
            user_agent=_get_ua(request),
            datos_extra=datos_extra or {},
        )
    except Exception as e:
        logger.error("Error al registrar LogAuditoria: %s", e)


def _error_result(msg: str):
    from core.etl.validators import ValidationResult
    r = ValidationResult()
    r.error_msg = msg
    return r