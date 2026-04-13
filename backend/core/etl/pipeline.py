"""
SIVAG — core/etl/pipeline.py
==============================
Orquestador principal del pipeline ETL.

Este módulo conecta los validadores y conversores, actualiza el modelo
CapaGeoespacial, crea los AtributoTabular (Excel), registra la VersionCapa
y genera el LogAuditoria correspondiente.

Es el único punto de entrada que deben llamar las vistas Django.

Uso:
    from core.etl.pipeline import run_etl_pipeline

    resultado = run_etl_pipeline(
        capa_id=str(capa.id),
        request=request,   # para el log de IP
    )
"""

from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime

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
from core.etl.validators import validate_excel, validate_shapefile, validate_geotiff
from core.etl.converters import (
    convert_excel_to_geojson,
    convert_shapefile_to_geojson,
    convert_geotiff_to_cog,
    _geojson_output_path,
    _raster_output_path,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Helper: IP del request (puede ser None en llamadas programáticas)
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
      1. Cargar la instancia de CapaGeoespacial desde la BD.
      2. Marcar estado PROCESANDO.
      3. Ejecutar el validador según tipo_archivo.
      4. Si la validación falla → marcar ERROR + log.
      5. Si la validación pasa → ejecutar el conversor correspondiente.
      6. Persistir resultados en CapaGeoespacial y modelos relacionados.
      7. Marcar estado APROBADO + log de éxito.

    Retorna:
      {
        "ok":        bool,
        "capa_id":   str,
        "estado":    "APROBADO" | "ERROR",
        "mensaje":   str,
        "meta":      dict,   # metadatos del proceso
      }
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

    usuario = capa.proyecto.investigador

    # ── 2. Marcar PROCESANDO ────────────────────────────
    capa.estado_validacion = EstadoValidacion.PROCESANDO
    capa.save(update_fields=["estado_validacion"])

    archivo_path = capa.archivo_original.path
    tipo         = capa.tipo_archivo
    media_root   = settings.MEDIA_ROOT

    # ── 3. Validar ──────────────────────────────────────
    try:
        if tipo == TipoArchivo.EXCEL:
            vr = validate_excel(archivo_path)
        elif tipo == TipoArchivo.SHAPE:
            vr = validate_shapefile(archivo_path)
        elif tipo == TipoArchivo.GEOTIFF:
            vr = validate_geotiff(archivo_path)
        else:
            vr = _unknown_type_result(tipo)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Error inesperado en validador para capa %s:\n%s", capa_id, tb)
        vr = _fake_error_result(f"Error interno del validador. Contacta al administrador.")

    # ── 4. Manejar fallo de validación ──────────────────
    if not vr.ok:
        capa.estado_validacion = EstadoValidacion.ERROR
        capa.mensaje_error     = vr.error_msg
        capa.save(update_fields=["estado_validacion", "mensaje_error"])

        _log_auditoria(
            request,
            AccionLog.VALIDACION_ERR,
            usuario,
            "CapaGeoespacial",
            capa_id,
            {"error": vr.error_msg, "tipo": tipo},
        )

        resultado["mensaje"] = vr.error_msg
        return resultado

    # ── 5. Convertir ─────────────────────────────────────
    try:
        conversion_result = _run_conversion(
            capa, vr.meta, archivo_path, media_root
        )
    except Exception:
        tb = traceback.format_exc()
        logger.error("Error en conversor para capa %s:\n%s", capa_id, tb)
        error_msg = "Error durante la conversión del archivo. Contacta al administrador."
        capa.estado_validacion = EstadoValidacion.ERROR
        capa.mensaje_error     = error_msg
        capa.save(update_fields=["estado_validacion", "mensaje_error"])
        resultado["mensaje"] = error_msg
        return resultado

    # ── 6. Persistir resultados ──────────────────────────
    _apply_conversion_to_capa(capa, vr.meta, conversion_result, tipo)

    # ── 7. Crear AtributoTabular (solo Excel) ────────────
    if tipo == TipoArchivo.EXCEL and "filas" in conversion_result:
        _create_atributos(capa, conversion_result["filas"], usuario)

    # ── 8. Registrar versión ─────────────────────────────
    _create_version(capa, usuario)

    # ── 9. Marcar APROBADO + log ─────────────────────────
    capa.estado_validacion = EstadoValidacion.APROBADO
    capa.fecha_procesado   = timezone.now()
    capa.mensaje_error     = ""
    capa.save(update_fields=[
        "estado_validacion", "fecha_procesado", "mensaje_error",
        "geojson_path", "raster_path", "num_features",
        "tipo_geometria", "sistema_coordenadas",
        "columnas_schema", "columna_lat", "columna_lon",
        "raster_bbox", "raster_banda_min", "raster_banda_max",
    ])

    _log_auditoria(
        request,
        AccionLog.VALIDACION_OK,
        usuario,
        "CapaGeoespacial",
        capa_id,
        {
            "tipo":         tipo,
            "num_features": capa.num_features,
            "crs":          capa.sistema_coordenadas,
        },
    )

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
    """Llama al conversor apropiado según el tipo de archivo."""
    tipo      = capa.tipo_archivo
    proyecto_id = str(capa.proyecto.id)
    capa_id   = str(capa.id)

    if tipo == TipoArchivo.EXCEL:
        output_path = _geojson_output_path(media_root, proyecto_id, capa_id)
        return convert_excel_to_geojson(
            filepath=archivo_path,
            col_lat=meta["columna_lat"],
            col_lon=meta["columna_lon"],
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
        output_path = _raster_output_path(media_root, proyecto_id, capa_id)
        return convert_geotiff_to_cog(
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
    """Escribe los resultados del conversor en los campos de la capa."""
    capa.sistema_coordenadas = meta.get("sistema_coordenadas", "")
    capa.columnas_schema     = meta.get("columnas_schema", {})

    if tipo in (TipoArchivo.EXCEL, TipoArchivo.SHAPE):
        capa.geojson_path    = _relative_path(conversion.get("geojson_path", ""))
        capa.num_features    = conversion.get("num_features", 0)
        capa.tipo_geometria  = conversion.get("tipo_geometria", TipoGeometria.PUNTO)

        if tipo == TipoArchivo.EXCEL:
            capa.columna_lat    = meta.get("columna_lat", "")
            capa.columna_lon    = meta.get("columna_lon", "")
            capa.tipo_geometria = TipoGeometria.PUNTO

    elif tipo == TipoArchivo.GEOTIFF:
        capa.raster_path      = _relative_path(conversion.get("raster_path", ""))
        capa.raster_banda_min = conversion.get("banda_min")
        capa.raster_banda_max = conversion.get("banda_max")
        capa.tipo_geometria   = TipoGeometria.RASTER
        capa.num_features     = 1


def _relative_path(absolute_path: str) -> str:
    """Convierte ruta absoluta a ruta relativa respecto a MEDIA_ROOT."""
    if not absolute_path:
        return ""
    media_root = str(settings.MEDIA_ROOT)
    if absolute_path.startswith(media_root):
        rel = absolute_path[len(media_root):]
        return rel.lstrip(os.sep)
    return absolute_path


def _create_atributos(capa: CapaGeoespacial, filas: list[dict], usuario) -> None:
    """Crea los registros AtributoTabular en bulk para mayor eficiencia."""
    from django.contrib.gis.geos import Point as GEOSPoint

    objs = []
    for fila in filas:
        lat = fila.get("lat")
        lon = fila.get("lon")
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

    # Inserción masiva en lotes de 500 para evitar timeouts
    BATCH_SIZE = 500
    for i in range(0, len(objs), BATCH_SIZE):
        AtributoTabular.objects.bulk_create(objs[i:i + BATCH_SIZE], ignore_conflicts=False)


def _create_version(capa: CapaGeoespacial, usuario) -> None:
    """Crea o actualiza el registro de versión para la capa."""
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
    """Registra un LogAuditoria de forma segura (no interrumpe el flujo si falla)."""
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


def _unknown_type_result(tipo):
    from core.etl.validators import ValidationResult
    r = ValidationResult()
    r.error_msg = f"Tipo de archivo '{tipo}' no soportado por el motor ETL."
    return r


def _fake_error_result(msg):
    from core.etl.validators import ValidationResult
    r = ValidationResult()
    r.error_msg = msg
    return r
