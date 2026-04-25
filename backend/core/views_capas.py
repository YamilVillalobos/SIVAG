"""
SIVAG — core/views_capas.py
============================
Vistas para la ingesta de capas geoespaciales (módulo Drag & Drop).

Endpoints:
  POST   /api/capas/subir/          — Subir archivo + lanzar ETL
  GET    /api/capas/<id>/estado/    — Consultar estado de procesamiento
  GET    /api/capas/<id>/geojson/   — Obtener GeoJSON de una capa aprobada
  GET    /api/capas/<id>/atributos/ — Listar atributos editables (Excel)
  PATCH  /api/capas/<id>/atributos/<attr_id>/ — Editar un atributo (RF-04)
  DELETE /api/capas/<id>/           — Eliminar capa (dueño o admin)

Requisitos: RF-02, RF-03, RF-04, RF-09, RNF-SEG-02
"""

import hashlib
import json
import logging
import os

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    AccionLog,
    AtributoTabular,
    CapaGeoespacial,
    EstadoValidacion,
    LogAuditoria,
    Proyecto,
    Rol,
    TipoArchivo,
)
from core.permissions import IsActiveUser, IsInvestigador, IsOwnerInvestigadorOrAdmin
from core.etl.pipeline import run_etl_pipeline

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _get_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _log(request, accion, usuario, objeto_tipo="", objeto_id="", datos_extra=None):
    try:
        LogAuditoria.objects.create(
            usuario=usuario,
            accion=accion,
            objeto_tipo=objeto_tipo,
            objeto_id=str(objeto_id) if objeto_id else "",
            ip_origen=_get_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            datos_extra=datos_extra or {},
        )
    except Exception as e:
        logger.error("Error al registrar log: %s", e)


def _md5(filepath: str) -> str:
    """Calcula el MD5 de un archivo en bloques para no cargar todo en RAM."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────
# Extensiones permitidas por tipo
# ─────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {
    TipoArchivo.EXCEL:   [".xlsx"],
    TipoArchivo.CSV:     [".csv"],
    TipoArchivo.SHAPE:   [".zip"],
    TipoArchivo.GEOTIFF: [".tiff", ".tif"],
}


# ─────────────────────────────────────────────────────────
# 1. SubirCapaView — POST /api/capas/subir/
# ─────────────────────────────────────────────────────────

class SubirCapaView(APIView):
    """
    Recibe el archivo del investigador, crea la CapaGeoespacial
    y lanza el pipeline ETL de forma síncrona.

    Body: multipart/form-data
      archivo      : File  — el archivo a subir
      tipo_archivo : str   — "EXCEL" | "SHAPE" | "GEOTIFF"
      proyecto_id  : str   — UUID del proyecto al que pertenece
      nombre       : str   — nombre descriptivo de la capa
      descripcion  : str   — (opcional)

    Respuesta 202:
      {
        "capa_id": "...",
        "estado":  "PROCESANDO" | "APROBADO" | "ERROR",
        "mensaje": "...",
        "meta":    {...}
      }

    Notas de seguridad:
      - Solo investigadores pueden subir archivos (IsInvestigador).
      - El proyecto debe pertenecer al investigador autenticado.
      - Se valida la extensión del archivo antes de guardarlo.
      - Se calcula el checksum MD5 para detectar duplicados.
    """

    permission_classes = [IsActiveUser, IsInvestigador]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        # ── Validar campos del formulario ────────────────
        archivo      = request.FILES.get("archivo")
        tipo_archivo = request.data.get("tipo_archivo", "").upper()
        proyecto_id  = request.data.get("proyecto_id", "")
        nombre       = request.data.get("nombre", "").strip()
        descripcion  = request.data.get("descripcion", "").strip()

        errors = {}

        if not archivo:
            errors["archivo"] = "Se requiere un archivo."

        if tipo_archivo not in TipoArchivo.values:
            errors["tipo_archivo"] = (
                f"Tipo inválido. Opciones: {', '.join(TipoArchivo.values)}."
            )

        if not proyecto_id:
            errors["proyecto_id"] = "Se requiere el ID del proyecto."

        if not nombre:
            errors["nombre"] = "Se requiere un nombre para la capa."

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        # ── Verificar extensión del archivo ──────────────
        ext = os.path.splitext(archivo.name)[1].lower()
        extensiones_ok = ALLOWED_EXTENSIONS.get(tipo_archivo, [])
        if ext not in extensiones_ok:
            return Response(
                {
                    "archivo": (
                        f"Extensión '{ext}' no válida para tipo {tipo_archivo}. "
                        f"Se esperaba: {', '.join(extensiones_ok)}."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Verificar que el proyecto pertenece al investigador ──
        try:
            proyecto = Proyecto.objects.get(
                pk=proyecto_id,
                investigador=request.user,
            )
        except Proyecto.DoesNotExist:
            return Response(
                {"proyecto_id": "No existe ese proyecto o no tienes permiso para subir capas en él."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── Verificar tamaño ────────────────────────────
        max_bytes = getattr(settings, "FILE_UPLOAD_MAX_MEMORY_SIZE", 104_857_600)
        if archivo.size > max_bytes:
            mb = max_bytes // (1024 * 1024)
            return Response(
                {"archivo": f"El archivo supera el límite de {mb} MB."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Crear CapaGeoespacial ────────────────────────
        capa = CapaGeoespacial.objects.create(
            proyecto=proyecto,
            nombre=nombre,
            descripcion=descripcion,
            tipo_archivo=tipo_archivo,
            archivo_original=archivo,
            tamano_bytes=archivo.size,
            estado_validacion=EstadoValidacion.PENDIENTE,
        )

        # Calcular checksum en background (no bloquea)
        try:
            capa.checksum_md5 = _md5(capa.archivo_original.path)
            capa.save(update_fields=["checksum_md5"])
        except Exception as e:
            logger.warning("No se pudo calcular MD5 para capa %s: %s", capa.id, e)

        # Log de subida
        _log(
            request,
            AccionLog.SUBIDA,
            request.user,
            "CapaGeoespacial",
            capa.id,
            {
                "nombre":      nombre,
                "tipo":        tipo_archivo,
                "tamano_bytes": archivo.size,
                "proyecto_id": str(proyecto.id),
            },
        )

        # ── Ejecutar ETL ─────────────────────────────────
        # En producción con muchos usuarios esto debería ir a una cola
        # (Celery + Redis). Por ahora es síncrono para el MVP.
        etl_result = run_etl_pipeline(str(capa.id), request=request)

        return Response(
            {
                "capa_id": str(capa.id),
                "estado":  etl_result["estado"],
                "mensaje": etl_result["mensaje"],
                "meta":    etl_result.get("meta", {}),
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ─────────────────────────────────────────────────────────
# 2. EstadoCapaView — GET /api/capas/<id>/estado/
# ─────────────────────────────────────────────────────────

class EstadoCapaView(APIView):
    """
    Devuelve el estado actual de procesamiento de una capa.
    Útil para polling desde el frontend mientras el ETL corre.

    Respuesta 200:
      {
        "capa_id":          "...",
        "estado":           "PENDIENTE" | "PROCESANDO" | "APROBADO" | "ERROR",
        "mensaje_error":    "..." (solo en ERROR),
        "num_features":     int,
        "tipo_geometria":   "...",
        "sistema_coordenadas": "...",
      }
    """

    permission_classes = [IsActiveUser]

    def get(self, request, capa_id):
        capa = get_object_or_404(CapaGeoespacial, pk=capa_id)

        # Verificar acceso: dueño, admin, o capa de proyecto público
        user = request.user
        es_dueno = capa.proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN
        es_publico = capa.proyecto.visibilidad == "PUBLICO"

        if not (es_dueno or es_admin or es_publico):
            return Response(
                {"detail": "No tienes acceso a esta capa."},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response({
            "capa_id":             str(capa.id),
            "nombre":              capa.nombre,
            "estado":              capa.estado_validacion,
            "mensaje_error":       capa.mensaje_error,
            "num_features":        capa.num_features,
            "tipo_geometria":      capa.tipo_geometria,
            "sistema_coordenadas": capa.sistema_coordenadas,
            "tipo_archivo":        capa.tipo_archivo,
            "fecha_subida":        capa.fecha_subida,
            "fecha_procesado":     capa.fecha_procesado,
        })


# ─────────────────────────────────────────────────────────
# 3. GeoJSONCapaView — GET /api/capas/<id>/geojson/
# ─────────────────────────────────────────────────────────

class GeoJSONCapaView(APIView):
    """
    Devuelve el GeoJSON de una capa vectorial aprobada.
    Este endpoint es el que consume Leaflet.js en el frontend.

    Optimizaciones:
      - Sirve el archivo directamente con FileResponse (streaming).
      - Nginx en producción debería servirlo con X-Accel-Redirect.

    Solo disponible para capas APROBADAS de tipo EXCEL o SHAPE.
    """

    permission_classes = [IsActiveUser]

    def get(self, request, capa_id):
        capa = get_object_or_404(CapaGeoespacial, pk=capa_id)

        # Control de acceso
        user = request.user
        es_dueno   = capa.proyecto.investigador == user
        es_admin   = user.rol == Rol.ADMIN
        es_publico = capa.proyecto.visibilidad == "PUBLICO"

        if not (es_dueno or es_admin or es_publico):
            return Response(
                {"detail": "No tienes acceso a esta capa."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if capa.estado_validacion != EstadoValidacion.APROBADO:
            return Response(
                {"detail": f"La capa aún no está lista (estado: {capa.estado_validacion})."},
                status=status.HTTP_409_CONFLICT,
            )

        if not capa.geojson_path:
            return Response(
                {"detail": "Esta capa no tiene datos vectoriales (puede ser un ráster)."},
                status=status.HTTP_404_NOT_FOUND,
            )

        geojson_abs = os.path.join(settings.MEDIA_ROOT, capa.geojson_path)

        if not os.path.exists(geojson_abs):
            logger.error("GeoJSON no encontrado en disco: %s", geojson_abs)
            return Response(
                {"detail": "Archivo GeoJSON no encontrado. Contacta al administrador."},
                status=status.HTTP_404_NOT_FOUND,
            )

        response = FileResponse(
            open(geojson_abs, "rb"),
            content_type="application/geo+json",
        )
        response["Content-Disposition"] = f'inline; filename="{capa.id}.geojson"'
        return response


# ─────────────────────────────────────────────────────────
# 4. AtributosCapaView — GET /api/capas/<id>/atributos/
# ─────────────────────────────────────────────────────────

class AtributosCapaView(APIView):
    """
    Lista los AtributoTabular editables de una capa Excel.

    Respuesta paginada (page + page_size como query params).
    Solo el investigador dueño o el admin pueden acceder.

    GET /api/capas/<id>/atributos/?page=1&page_size=50
    """

    permission_classes = [IsActiveUser]

    def get(self, request, capa_id):
        capa = get_object_or_404(CapaGeoespacial, pk=capa_id)

        user     = request.user
        es_dueno = capa.proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN

        if not (es_dueno or es_admin):
            return Response(
                {"detail": "Solo el investigador dueño o el administrador pueden ver los atributos."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if capa.estado_validacion != EstadoValidacion.APROBADO:
            return Response(
                {"detail": "La capa aún no ha sido procesada."},
                status=status.HTTP_409_CONFLICT,
            )

        # Paginación manual simple
        try:
            page      = max(1, int(request.query_params.get("page", 1)))
            page_size = min(500, max(1, int(request.query_params.get("page_size", 50))))
        except ValueError:
            page, page_size = 1, 50

        qs    = AtributoTabular.objects.filter(capa=capa).order_by("indice_original")
        total = qs.count()
        start = (page - 1) * page_size
        end   = start + page_size

        atributos = qs[start:end]

        filas = [
            {
                "id":              str(a.id),
                "indice_original": a.indice_original,
                "latitud":         a.latitud,
                "longitud":        a.longitud,
                "datos":           a.datos,
                "modificado_por":  a.modificado_por.username if a.modificado_por else None,
                "fecha_modificacion": a.fecha_modificacion,
            }
            for a in atributos
        ]

        return Response({
            "capa_id":    str(capa.id),
            "total":      total,
            "page":       page,
            "page_size":  page_size,
            "columnas":   list(capa.columnas_schema.keys()),
            "atributos":  filas,
        })


# ─────────────────────────────────────────────────────────
# 5. EditarAtributoView — PATCH /api/capas/<id>/atributos/<attr_id>/
# ─────────────────────────────────────────────────────────

class EditarAtributoView(APIView):
    """
    Edita los datos de un AtributoTabular específico (RF-04).

    Solo el investigador dueño o el admin pueden editar.
    Registra quién hizo la edición y cuándo (trazabilidad).

    Body JSON:
      { "datos": { "campo": "nuevo_valor", ... } }

    Respuesta 200:
      { "id": "...", "datos": {...}, "modificado_por": "..." }
    """

    permission_classes = [IsActiveUser]

    def patch(self, request, capa_id, attr_id):
        capa = get_object_or_404(CapaGeoespacial, pk=capa_id)
        attr = get_object_or_404(AtributoTabular, pk=attr_id, capa=capa)

        user     = request.user
        es_dueno = capa.proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN

        if not (es_dueno or es_admin):
            return Response(
                {"detail": "Solo el investigador dueño o el administrador pueden editar atributos."},
                status=status.HTTP_403_FORBIDDEN,
            )

        nuevos_datos = request.data.get("datos")
        if not isinstance(nuevos_datos, dict):
            return Response(
                {"datos": "Se espera un objeto JSON con los campos a actualizar."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Merge: solo actualiza los campos enviados
        attr.datos.update(nuevos_datos)
        attr.modificado_por      = user
        attr.fecha_modificacion  = timezone.now()
        attr.save(update_fields=["datos", "modificado_por", "fecha_modificacion"])

        _log(
            request,
            AccionLog.EDICION_ATTR,
            user,
            "AtributoTabular",
            attr.id,
            {"capa_id": str(capa.id), "campos": list(nuevos_datos.keys())},
        )

        return Response({
            "id":               str(attr.id),
            "indice_original":  attr.indice_original,
            "datos":            attr.datos,
            "modificado_por":   user.username,
            "fecha_modificacion": attr.fecha_modificacion,
        })


# ─────────────────────────────────────────────────────────
# 6. EliminarCapaView — DELETE /api/capas/<id>/
# ─────────────────────────────────────────────────────────

class EliminarCapaView(APIView):
    """
    Elimina una capa geoespacial junto con sus archivos en disco.

    Solo el investigador dueño o el admin pueden eliminar.
    Genera un LogAuditoria de tipo ELIMINACION.
    """

    permission_classes = [IsActiveUser]

    def delete(self, request, capa_id):
        capa = get_object_or_404(CapaGeoespacial, pk=capa_id)

        user     = request.user
        es_dueno = capa.proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN

        if not (es_dueno or es_admin):
            return Response(
                {"detail": "No tienes permiso para eliminar esta capa."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Eliminar archivos en disco antes de borrar el registro
        _delete_capa_files(capa)

        _log(
            request,
            AccionLog.ELIMINACION,
            user,
            "CapaGeoespacial",
            capa.id,
            {
                "nombre":      capa.nombre,
                "tipo":        capa.tipo_archivo,
                "proyecto_id": str(capa.proyecto.id),
            },
        )

        capa.delete()

        return Response(
            {"mensaje": "Capa eliminada correctamente."},
            status=status.HTTP_200_OK,
        )


def _delete_capa_files(capa: CapaGeoespacial) -> None:
    """Elimina todos los archivos en disco asociados a una capa."""
    media_root = settings.MEDIA_ROOT

    # Archivo original
    try:
        if capa.archivo_original:
            path = capa.archivo_original.path
            if os.path.exists(path):
                os.remove(path)
    except Exception as e:
        logger.warning("No se pudo eliminar archivo original de capa %s: %s", capa.id, e)

    # GeoJSON
    if capa.geojson_path:
        abs_path = os.path.join(media_root, capa.geojson_path)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception as e:
            logger.warning("No se pudo eliminar GeoJSON de capa %s: %s", capa.id, e)

    # Ráster COG
    if capa.raster_path:
        abs_path = os.path.join(media_root, capa.raster_path)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception as e:
            logger.warning("No se pudo eliminar ráster de capa %s: %s", capa.id, e)
