"""
SIVAG — core/views_proyectos.py
=================================
Vistas para la gestión completa de proyectos geoespaciales.

Endpoints implementados:
  POST   /api/proyectos/                     — Crear proyecto (RF-04)
  GET    /api/proyectos/                     — Explorador público con filtros (RF-06)
  GET    /api/proyectos/mis/                 — Panel del investigador (RF-11)
  GET    /api/proyectos/<id>/                — Detalle de un proyecto
  PATCH  /api/proyectos/<id>/                — Editar metadatos (RF-04)
  DELETE /api/proyectos/<id>/                — Eliminación lógica (RF-06)
  POST   /api/proyectos/<id>/toggle/         — Publicar / despublicar (RF-06)
  GET    /api/proyectos/<id>/versiones/      — Historial de versiones de capas

Reglas de acceso RBAC (RNF-SEG-02):
  - Crear:      solo INVESTIGADOR autenticado
  - Editar/Borrar: dueño INVESTIGADOR o ADMIN
  - Explorador:  cualquiera (sin auth) — solo PÚBLICOS
  - Panel:       INVESTIGADOR autenticado — solo los suyos
  - Detalle:     dueño / ADMIN → completo; público → versión pública
  - Toggle:      dueño INVESTIGADOR o ADMIN

Cada acción que modifica datos genera un LogAuditoria (RF-09).
"""

import logging

from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    AccionLog,
    CategoriaProyecto,
    EstadoValidacion,
    LogAuditoria,
    Proyecto,
    Rol,
    VersionCapa,
    VisibilidadProyecto,
)
from .permissions import (
    IsActiveUser,
    IsAdmin,
    IsInvestigador,
    IsInvestigadorOrAdmin,
    IsOwnerInvestigadorOrAdmin,
    IsOwnerOrAdmin,
    ReadOnly,
)
from .serializers_proyectos import (
    PanelInvestigadorSerializer,
    ProyectoCreateSerializer,
    ProyectoDetailSerializer,
    ProyectoListSerializer,
    ProyectoPublicoSerializer,
    ProyectoUpdateSerializer,
    ToggleVisibilidadSerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def _get_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _log(
    request,
    accion: str,
    usuario=None,
    objeto_tipo: str = "",
    objeto_id: str = "",
    datos_extra: dict = None,
) -> None:
    """Registra una acción en LogAuditoria sin interrumpir el flujo si falla."""
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
    except Exception as exc:
        logger.error("Error al registrar LogAuditoria: %s", exc)


def _user_puede_ver_proyecto(user, proyecto: Proyecto) -> bool:
    """
    Determina si un usuario tiene permiso de lectura sobre un proyecto.

    - ADMIN: siempre puede ver todo.
    - Investigador dueño: puede ver sus propios proyectos (privados y públicos).
    - Resto: solo proyectos PÚBLICOS.
    """
    if not user or not user.is_authenticated:
        return proyecto.visibilidad == VisibilidadProyecto.PUBLICO

    if user.rol == Rol.ADMIN:
        return True

    if proyecto.investigador == user:
        return True

    return proyecto.visibilidad == VisibilidadProyecto.PUBLICO


# ─────────────────────────────────────────────────────────
# Vista 1: Crear proyecto + Explorador público
# POST /api/proyectos/
# GET  /api/proyectos/
# ─────────────────────────────────────────────────────────

class ProyectoListCreateView(APIView):
    """
    GET  — Explorador público de proyectos (RF-06).
           Devuelve proyectos PÚBLICOS con filtros opcionales.
           No requiere autenticación.

    POST — Crea un nuevo proyecto (RF-04).
           Solo investigadores autenticados.

    Filtros GET (query params):
      categoria   : CLIMA | FAUNA | AGUA | SUELO | VEGETACION | ATMOSFERA | OTRO
      q           : búsqueda por texto en título y descripción
      etiqueta    : filtrar por una etiqueta específica
      investigador: username del investigador
      fecha_desde : proyectos actualizados desde esta fecha (YYYY-MM-DD)
      fecha_hasta : proyectos actualizados hasta esta fecha (YYYY-MM-DD)
      bbox        : bounding box geográfico "min_lon,min_lat,max_lon,max_lat"
                    (filtra proyectos cuyo bbox intersecta con el dado)
      ordering    : campo de ordenamiento (fecha_actualizacion, titulo,
                    publicado_en). Prefijo "-" para descendente.
      page        : número de página (default: 1)
      page_size   : elementos por página (default: 12, máx: 50)
    """

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsActiveUser(), IsInvestigador()]
        return [AllowAny()]

    # ── GET: explorador público ──────────────────────────

    def get(self, request):
        qs = Proyecto.objects.filter(
            visibilidad=VisibilidadProyecto.PUBLICO
        ).select_related("investigador").prefetch_related("capas")

        # ── Filtros ──────────────────────────────────────

        categoria = request.query_params.get("categoria", "").strip().upper()
        if categoria and categoria in CategoriaProyecto.values:
            qs = qs.filter(categoria=categoria)

        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(titulo__icontains=q) |
                Q(descripcion__icontains=q)
            )

        etiqueta = request.query_params.get("etiqueta", "").strip().lower()
        if etiqueta:
            # JSONField contains lookup para listas
            qs = qs.filter(etiquetas__contains=[etiqueta])

        investigador_username = request.query_params.get("investigador", "").strip()
        if investigador_username:
            qs = qs.filter(investigador__username__iexact=investigador_username)

        fecha_desde = request.query_params.get("fecha_desde", "").strip()
        if fecha_desde:
            try:
                qs = qs.filter(fecha_actualizacion__date__gte=fecha_desde)
            except (ValueError, TypeError):
                pass

        fecha_hasta = request.query_params.get("fecha_hasta", "").strip()
        if fecha_hasta:
            try:
                qs = qs.filter(fecha_actualizacion__date__lte=fecha_hasta)
            except (ValueError, TypeError):
                pass

        # Filtro geográfico por BBOX (requiere PostGIS)
        bbox_param = request.query_params.get("bbox", "").strip()
        if bbox_param:
            try:
                parts = [float(x) for x in bbox_param.split(",")]
                if len(parts) == 4:
                    min_lon, min_lat, max_lon, max_lat = parts
                    from django.contrib.gis.geos import Polygon
                    from django.contrib.gis.db.models.functions import Envelope
                    bbox_poly = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
                    bbox_poly.srid = 4326
                    qs = qs.filter(bbox__intersects=bbox_poly)
            except (ValueError, TypeError, Exception) as exc:
                logger.warning("Filtro bbox inválido: %s — %s", bbox_param, exc)

        # ── Ordenamiento ──────────────────────────────────
        ORDERING_FIELDS = {
            "fecha_actualizacion":  "fecha_actualizacion",
            "-fecha_actualizacion": "-fecha_actualizacion",
            "titulo":               "titulo",
            "-titulo":              "-titulo",
            "publicado_en":         "publicado_en",
            "-publicado_en":        "-publicado_en",
        }
        ordering_param = request.query_params.get("ordering", "-publicado_en").strip()
        ordering = ORDERING_FIELDS.get(ordering_param, "-publicado_en")
        qs = qs.order_by(ordering)

        # ── Paginación ────────────────────────────────────
        try:
            page      = max(1, int(request.query_params.get("page", 1)))
            page_size = min(50, max(1, int(request.query_params.get("page_size", 12))))
        except (ValueError, TypeError):
            page, page_size = 1, 12

        total  = qs.count()
        start  = (page - 1) * page_size
        end    = start + page_size
        pagina = qs[start:end]

        # ── Serializar ────────────────────────────────────
        serializer = ProyectoListSerializer(pagina, many=True)

        return Response({
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     (total + page_size - 1) // page_size,
            "results":   serializer.data,
        })

    # ── POST: crear proyecto ─────────────────────────────

    def post(self, request):
        serializer = ProyectoCreateSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        proyecto = serializer.save()

        _log(
            request,
            accion=AccionLog.SUBIDA,
            usuario=request.user,
            objeto_tipo="Proyecto",
            objeto_id=proyecto.id,
            datos_extra={
                "titulo":    proyecto.titulo,
                "categoria": proyecto.categoria,
            },
        )

        # Devolver el detalle completo del proyecto recién creado
        detail_serializer = ProyectoDetailSerializer(
            proyecto, context={"request": request}
        )
        return Response(detail_serializer.data, status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────
# Vista 2: Panel del Investigador
# GET /api/proyectos/mis/
# ─────────────────────────────────────────────────────────

class MisProyectosView(APIView):
    """
    Panel de Gestión Personal del Investigador (RF-11).

    Devuelve todos los proyectos del investigador autenticado
    (privados + públicos) con métricas y estado de capas.

    Filtros:
      visibilidad : PRIVADO | PUBLICO
      categoria   : filtrar por categoría
      q           : búsqueda por texto
      ordering    : campo de ordenamiento
      page / page_size

    Solo accesible para investigadores autenticados.
    """

    permission_classes = [IsActiveUser, IsInvestigador]

    def get(self, request):
        qs = Proyecto.objects.filter(
            investigador=request.user
        ).exclude(
            # Excluir proyectos con eliminación lógica (RF-09)
            etiquetas__contains=["eliminado"]
        ).select_related("investigador").prefetch_related("capas")

        # ── Filtros ──────────────────────────────────────
        visibilidad = request.query_params.get("visibilidad", "").strip().upper()
        if visibilidad in VisibilidadProyecto.values:
            qs = qs.filter(visibilidad=visibilidad)

        categoria = request.query_params.get("categoria", "").strip().upper()
        if categoria and categoria in CategoriaProyecto.values:
            qs = qs.filter(categoria=categoria)

        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(titulo__icontains=q) | Q(descripcion__icontains=q)
            )

        # ── Ordenamiento ──────────────────────────────────
        ORDERING_FIELDS = {
            "fecha_actualizacion":  "fecha_actualizacion",
            "-fecha_actualizacion": "-fecha_actualizacion",
            "titulo":               "titulo",
            "-titulo":              "-titulo",
            "fecha_creacion":       "fecha_creacion",
            "-fecha_creacion":      "-fecha_creacion",
        }
        ordering_param = request.query_params.get("ordering", "-fecha_actualizacion").strip()
        ordering = ORDERING_FIELDS.get(ordering_param, "-fecha_actualizacion")
        qs = qs.order_by(ordering)

        # ── Paginación ────────────────────────────────────
        try:
            page      = max(1, int(request.query_params.get("page", 1)))
            page_size = min(50, max(1, int(request.query_params.get("page_size", 10))))
        except (ValueError, TypeError):
            page, page_size = 1, 10

        total  = qs.count()
        start  = (page - 1) * page_size
        end    = start + page_size
        pagina = qs[start:end]

        serializer = PanelInvestigadorSerializer(pagina, many=True)

        # Métricas globales del investigador (excluye eliminados)
        totales = Proyecto.objects.filter(
            investigador=request.user
        ).exclude(etiquetas__contains=["eliminado"]).aggregate(
            total_proyectos  = Count("id"),
            publicados       = Count("id", filter=Q(visibilidad=VisibilidadProyecto.PUBLICO)),
            privados         = Count("id", filter=Q(visibilidad=VisibilidadProyecto.PRIVADO)),
            total_features   = Sum("capas__num_features",
                                   filter=Q(capas__estado_validacion=EstadoValidacion.APROBADO)),
        )

        return Response({
            "metricas": {
                "total_proyectos": totales["total_proyectos"] or 0,
                "publicados":      totales["publicados"]      or 0,
                "privados":        totales["privados"]        or 0,
                "total_features":  totales["total_features"]  or 0,
            },
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "pages":     (total + page_size - 1) // page_size,
            "results":   serializer.data,
        })


# ─────────────────────────────────────────────────────────
# Vista 3: Detalle, Edición y Eliminación de un proyecto
# GET    /api/proyectos/<id>/
# PATCH  /api/proyectos/<id>/
# DELETE /api/proyectos/<id>/
# ─────────────────────────────────────────────────────────

class ProyectoDetailView(APIView):
    """
    Operaciones sobre un proyecto individual.

    GET:
      - Admin / dueño → ProyectoDetailSerializer (completo + capas privadas)
      - Público/Normal → ProyectoPublicoSerializer (solo capas aprobadas)
      - Usuario sin auth sobre proyecto PRIVADO → 403

    PATCH:
      - Solo dueño INVESTIGADOR o ADMIN.
      - Actualiza metadatos (título, desc, categoría, etiquetas, fechas).
      - NO cambia visibilidad (usar /toggle/).

    DELETE:
      - Solo dueño INVESTIGADOR o ADMIN.
      - Eliminación lógica: oculta el proyecto del explorador público.
      - Mantiene los datos en BD para auditoría (RF-09).
      - Si el admin quiere una eliminación física usará el panel de admin.
    """

    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _get_proyecto(self, proyecto_id):
        return get_object_or_404(
            Proyecto.objects.select_related("investigador").prefetch_related("capas"),
            pk=proyecto_id,
        )

    # ── GET: detalle ──────────────────────────────────────

    def get(self, request, proyecto_id):
        proyecto = self._get_proyecto(proyecto_id)

        if not _user_puede_ver_proyecto(request.user, proyecto):
            return Response(
                {"detail": "Este proyecto es privado o no existe."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Decidir qué serializer usar según perfil del usuario
        user = request.user
        es_dueno = user.is_authenticated and proyecto.investigador == user
        es_admin = user.is_authenticated and user.rol == Rol.ADMIN

        if es_dueno or es_admin:
            serializer = ProyectoDetailSerializer(
                proyecto, context={"request": request}
            )
        else:
            serializer = ProyectoPublicoSerializer(
                proyecto, context={"request": request}
            )

        # Registrar visita en HistorialConsulta para usuarios normales autenticados
        if (
            user.is_authenticated
            and user.rol == Rol.NORMAL
            and proyecto.visibilidad == VisibilidadProyecto.PUBLICO
        ):
            _registrar_visita(user, proyecto)

        return Response(serializer.data)

    # ── PATCH: editar metadatos ───────────────────────────

    def patch(self, request, proyecto_id):
        proyecto = self._get_proyecto(proyecto_id)

        user     = request.user
        es_dueno = user.is_authenticated and proyecto.investigador == user
        es_admin = user.is_authenticated and user.rol == Rol.ADMIN

        if not user.is_authenticated or not (es_dueno or es_admin):
            return Response(
                {"detail": "No tienes permiso para editar este proyecto."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ProyectoUpdateSerializer(
            proyecto,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        proyecto_actualizado = serializer.save()

        _log(
            request,
            accion=AccionLog.EDICION_ATTR,
            usuario=user,
            objeto_tipo="Proyecto",
            objeto_id=proyecto.id,
            datos_extra={
                "campos_actualizados": list(request.data.keys()),
                "titulo":              proyecto_actualizado.titulo,
            },
        )

        # Responder con el detalle completo actualizado
        detail_serializer = ProyectoDetailSerializer(
            proyecto_actualizado, context={"request": request}
        )
        return Response(detail_serializer.data)

    # ── DELETE: eliminación lógica ────────────────────────

    def delete(self, request, proyecto_id):
        proyecto = self._get_proyecto(proyecto_id)

        user     = request.user
        es_dueno = user.is_authenticated and proyecto.investigador == user
        es_admin = user.is_authenticated and user.rol == Rol.ADMIN

        if not user.is_authenticated or not (es_dueno or es_admin):
            return Response(
                {"detail": "No tienes permiso para eliminar este proyecto."},
                status=status.HTTP_403_FORBIDDEN,
            )

        titulo_snapshot = proyecto.titulo

        # Eliminación lógica: despublicar + marcar oculto
        # Se mantienen los registros en BD para auditoría (RF-09)
        if proyecto.visibilidad == VisibilidadProyecto.PUBLICO:
            proyecto.despublicar()

        # Marcamos con etiqueta especial para identificar proyectos eliminados
        # sin borrar el registro (permite al admin restaurarlos si es necesario)
        if "eliminado" not in proyecto.etiquetas:
            proyecto.etiquetas = proyecto.etiquetas + ["eliminado"]
            proyecto.save(update_fields=["etiquetas"])

        _log(
            request,
            accion=AccionLog.ELIMINACION,
            usuario=user,
            objeto_tipo="Proyecto",
            objeto_id=proyecto.id,
            datos_extra={
                "titulo":            titulo_snapshot,
                "accion_admin":      es_admin and not es_dueno,
                "categoria":         proyecto.categoria,
            },
        )

        return Response(
            {
                "mensaje": (
                    f"El proyecto '{titulo_snapshot}' ha sido ocultado correctamente. "
                    "Los datos se conservan para auditoría."
                )
            },
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────
# Vista 4: Toggle Publicar / Despublicar
# POST /api/proyectos/<id>/toggle/
# ─────────────────────────────────────────────────────────

class ToggleVisibilidadView(APIView):
    """
    Cambia la visibilidad de un proyecto entre PRIVADO y PÚBLICO (RF-06).

    Solo el investigador dueño o el admin pueden usar este endpoint.

    Al publicar:
      - Verifica que exista al menos una capa APROBADA.
      - Registra la fecha de publicación.
      - Genera LogAuditoria.

    Al despublicar:
      - Limpia la fecha de publicación.
      - El proyecto desaparece del explorador público.

    Body JSON:
      { "visibilidad": "PUBLICO" | "PRIVADO" }

    Respuesta 200:
      {
        "mensaje":     "...",
        "proyecto_id": "...",
        "visibilidad": "PUBLICO" | "PRIVADO",
        "publicado_en": "..." | null
      }
    """

    permission_classes = [IsActiveUser]

    def post(self, request, proyecto_id):
        proyecto = get_object_or_404(
            Proyecto.objects.select_related("investigador").prefetch_related("capas"),
            pk=proyecto_id,
        )

        user     = request.user
        es_dueno = proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN

        if not (es_dueno or es_admin):
            return Response(
                {"detail": "Solo el investigador dueño o el administrador pueden cambiar la visibilidad."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ToggleVisibilidadSerializer(
            data=request.data,
            context={"proyecto": proyecto, "request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        proyecto_actualizado = serializer.save()
        nueva_visibilidad    = proyecto_actualizado.visibilidad

        # Mensaje amigable según la acción realizada
        if nueva_visibilidad == VisibilidadProyecto.PUBLICO:
            mensaje = (
                f"El proyecto '{proyecto_actualizado.titulo}' ahora es PÚBLICO. "
                "Los usuarios pueden explorarlo en el catálogo."
            )
            accion_log = AccionLog.PUBLICACION
        else:
            mensaje = (
                f"El proyecto '{proyecto_actualizado.titulo}' ahora es PRIVADO. "
                "Solo tú puedes verlo."
            )
            accion_log = AccionLog.DESPUBLICACION

        _log(
            request,
            accion=accion_log,
            usuario=user,
            objeto_tipo="Proyecto",
            objeto_id=proyecto.id,
            datos_extra={
                "visibilidad_anterior": "PUBLICO" if nueva_visibilidad == "PRIVADO" else "PRIVADO",
                "visibilidad_nueva":    nueva_visibilidad,
                "accion_admin":         es_admin and not es_dueno,
            },
        )

        return Response({
            "mensaje":      mensaje,
            "proyecto_id":  str(proyecto_actualizado.id),
            "visibilidad":  nueva_visibilidad,
            "publicado_en": proyecto_actualizado.publicado_en,
        })


# ─────────────────────────────────────────────────────────
# Vista 5: Historial de versiones de capas del proyecto
# GET /api/proyectos/<id>/versiones/
# ─────────────────────────────────────────────────────────

class VersionesProyectoView(APIView):
    """
    Lista el historial de versiones de todas las capas
    de un proyecto (RF-06 — Gestión de Versiones).

    Solo accesible para el dueño o el admin.

    Respuesta 200:
      {
        "proyecto_id": "...",
        "versiones": [
          {
            "id":              "...",
            "capa_id":         "...",
            "capa_nombre":     "...",
            "numero_version":  1,
            "nota_version":    "...",
            "creado_en":       "...",
            "creado_por":      "username"
          },
          ...
        ]
      }
    """

    permission_classes = [IsActiveUser]

    def get(self, request, proyecto_id):
        proyecto = get_object_or_404(
            Proyecto.objects.select_related("investigador"),
            pk=proyecto_id,
        )

        user     = request.user
        es_dueno = proyecto.investigador == user
        es_admin = user.rol == Rol.ADMIN

        if not (es_dueno or es_admin):
            return Response(
                {"detail": "Solo el investigador dueño o el administrador pueden ver el historial de versiones."},
                status=status.HTTP_403_FORBIDDEN,
            )

        versiones = VersionCapa.objects.filter(
            capa__proyecto=proyecto
        ).select_related(
            "capa", "creado_por"
        ).order_by("-creado_en")

        data = [
            {
                "id":              str(v.id),
                "capa_id":         str(v.capa.id),
                "capa_nombre":     v.capa.nombre,
                "capa_tipo":       v.capa.tipo_archivo,
                "numero_version":  v.numero_version,
                "nota_version":    v.nota_version,
                "creado_en":       v.creado_en,
                "creado_por":      v.creado_por.username if v.creado_por else None,
            }
            for v in versiones
        ]

        return Response({
            "proyecto_id": str(proyecto.id),
            "titulo":      proyecto.titulo,
            "total":       len(data),
            "versiones":   data,
        })


# ─────────────────────────────────────────────────────────
# Vista 6: Resumen de categorías disponibles (utilidad UI)
# GET /api/proyectos/categorias/
# ─────────────────────────────────────────────────────────

class CategoriasDisponiblesView(APIView):
    """
    Devuelve las categorías disponibles con conteo de proyectos públicos.
    Útil para renderizar los filtros del explorador en el frontend.

    No requiere autenticación.

    Respuesta 200:
      {
        "categorias": [
          { "valor": "CLIMA", "etiqueta": "Clima", "total": 5 },
          ...
        ]
      }
    """

    permission_classes = [AllowAny]

    def get(self, request):
        conteos = dict(
            Proyecto.objects.filter(
                visibilidad=VisibilidadProyecto.PUBLICO
            ).values("categoria").annotate(
                total=Count("id")
            ).values_list("categoria", "total")
        )

        categorias = [
            {
                "valor":    valor,
                "etiqueta": etiqueta,
                "total":    conteos.get(valor, 0),
            }
            for valor, etiqueta in CategoriaProyecto.choices
        ]

        return Response({"categorias": categorias})


# ─────────────────────────────────────────────────────────
# Vista 7: Ocultación / Restauración por el Admin
# POST /api/proyectos/<id>/moderar/
# ─────────────────────────────────────────────────────────

class ModerarProyectoView(APIView):
    """
    Permite al Administrador ocultar o restaurar un proyecto (RF-08).

    Acciones:
      "ocultar"    — Despublica el proyecto y lo marca como moderado.
      "restaurar"  — Quita la marca de moderación (no republica automáticamente).

    Body JSON:
      {
        "accion": "ocultar" | "restaurar",
        "motivo": "texto explicativo (requerido para ocultar)"
      }

    Solo accesible para ADMIN.
    Genera LogAuditoria con acción MODERACION.
    """

    permission_classes = [IsActiveUser, IsAdmin]

    def post(self, request, proyecto_id):
        proyecto = get_object_or_404(
            Proyecto.objects.select_related("investigador"),
            pk=proyecto_id,
        )

        accion = request.data.get("accion", "").strip().lower()
        motivo = request.data.get("motivo", "").strip()

        if accion not in ("ocultar", "restaurar"):
            return Response(
                {"accion": "El valor debe ser 'ocultar' o 'restaurar'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if accion == "ocultar" and not motivo:
            return Response(
                {"motivo": "Se requiere un motivo para ocultar un proyecto."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        titulo_snapshot = proyecto.titulo

        if accion == "ocultar":
            # Despublicar si estaba público
            if proyecto.visibilidad == VisibilidadProyecto.PUBLICO:
                proyecto.despublicar()

            # Marcar como moderado
            etiquetas = proyecto.etiquetas.copy()
            if "moderado" not in etiquetas:
                etiquetas.append("moderado")
            proyecto.etiquetas = etiquetas
            proyecto.save(update_fields=["etiquetas"])

            mensaje = f"El proyecto '{titulo_snapshot}' ha sido ocultado por moderación."

        else:  # restaurar
            etiquetas = [t for t in proyecto.etiquetas if t not in ("moderado", "eliminado")]
            proyecto.etiquetas = etiquetas
            proyecto.save(update_fields=["etiquetas"])

            mensaje = (
                f"El proyecto '{titulo_snapshot}' ha sido restaurado. "
                "El investigador puede volver a publicarlo."
            )

        _log(
            request,
            accion=AccionLog.MODERACION,
            usuario=request.user,
            objeto_tipo="Proyecto",
            objeto_id=proyecto.id,
            datos_extra={
                "accion_moderacion":  accion,
                "motivo":             motivo,
                "investigador":       proyecto.investigador.username,
                "titulo":             titulo_snapshot,
            },
        )

        return Response({
            "mensaje":      mensaje,
            "proyecto_id":  str(proyecto.id),
            "etiquetas":    proyecto.etiquetas,
        })


# ─────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────

def _registrar_visita(user, proyecto: Proyecto) -> None:
    """
    Registra o incrementa una visita en HistorialConsulta.
    Se usa para el panel 'Mis visitas' del usuario Normal (RF-11).
    Silencia errores para no afectar la respuesta principal.
    """
    from .models import HistorialConsulta
    try:
        historial, creado = HistorialConsulta.objects.get_or_create(
            usuario=user,
            proyecto=proyecto,
            defaults={"veces": 1},
        )
        if not creado:
            historial.veces += 1
            historial.visitado_en = timezone.now()
            historial.save(update_fields=["veces", "visitado_en"])
    except Exception as exc:
        logger.warning("No se pudo registrar visita: %s", exc)