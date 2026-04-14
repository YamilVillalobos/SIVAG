"""
SIVAG — core/serializers_proyectos.py
======================================
Serializers para la gestión de proyectos geoespaciales.

Contenido:
  ProyectoCreateSerializer    — Creación de proyecto (RF-04)
  ProyectoUpdateSerializer    — Edición parcial de proyecto
  ProyectoListSerializer      — Listado compacto (explorador + panel)
  ProyectoDetailSerializer    — Detalle completo con capas y métricas
  ProyectoPublicoSerializer   — Vista pública (sin datos sensibles)
  ToggleVisibilidadSerializer — Publicar / despublicar (RF-06)
"""

from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework_gis.fields import GeometryField  # noqa: F401 — disponible para serializers futuros

from .models import (
    CapaGeoespacial,
    CategoriaProyecto,
    CustomUser,
    EstadoValidacion,
    Proyecto,
    VisibilidadProyecto,
)


# ─────────────────────────────────────────────────────────
# Helper interno: resumen de una capa para incrustar en proyecto
# ─────────────────────────────────────────────────────────

def _capa_resumen(capa: CapaGeoespacial) -> dict:
    """
    Devuelve un dict compacto con los datos esenciales de una capa
    para incrustar en la respuesta del proyecto (sin datos pesados).
    """
    return {
        "id":                  str(capa.id),
        "nombre":              capa.nombre,
        "tipo_archivo":        capa.tipo_archivo,
        "estado_validacion":   capa.estado_validacion,
        "tipo_geometria":      capa.tipo_geometria,
        "num_features":        capa.num_features,
        "sistema_coordenadas": capa.sistema_coordenadas,
        "fecha_subida":        capa.fecha_subida,
        "fecha_procesado":     capa.fecha_procesado,
        "tiene_geojson":       bool(capa.geojson_path),
        "tiene_raster":        bool(capa.raster_path),
    }


# ─────────────────────────────────────────────────────────
# 1. Creación de proyecto
# ─────────────────────────────────────────────────────────

class ProyectoCreateSerializer(serializers.ModelSerializer):
    """
    Crea un nuevo proyecto vinculado al investigador autenticado.

    El investigador asigna título, categoría, descripción opcional,
    etiquetas, rango de fechas del estudio y visibilidad inicial.

    Campos aceptados:
      titulo, descripcion, categoria, etiquetas (list),
      fecha_inicio, fecha_fin, visibilidad
    """

    class Meta:
        model  = Proyecto
        fields = [
            "titulo",
            "descripcion",
            "categoria",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
            "visibilidad",
        ]
        extra_kwargs = {
            "titulo":      {"required": True},
            "categoria":   {"required": True},
            "descripcion": {"required": False, "allow_blank": True},
            "etiquetas":   {"required": False},
            "fecha_inicio":{"required": False},
            "fecha_fin":   {"required": False},
            "visibilidad": {"required": False},
        }

    def validate_titulo(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError(
                _("El título debe tener al menos 5 caracteres.")
            )
        return value

    def validate_etiquetas(self, value):
        """Asegura que etiquetas sea una lista de strings no vacíos."""
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Las etiquetas deben ser una lista."))
        cleaned = [str(tag).strip().lower() for tag in value if str(tag).strip()]
        if len(cleaned) > 20:
            raise serializers.ValidationError(
                _("No se pueden asignar más de 20 etiquetas a un proyecto.")
            )
        return cleaned

    def validate(self, attrs):
        fecha_inicio = attrs.get("fecha_inicio")
        fecha_fin    = attrs.get("fecha_fin")
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            raise serializers.ValidationError(
                {"fecha_fin": _("La fecha de fin no puede ser anterior a la fecha de inicio.")}
            )
        return attrs

    def create(self, validated_data):
        """El investigador se asigna desde el contexto del request."""
        investigador = self.context["request"].user
        return Proyecto.objects.create(
            investigador=investigador,
            **validated_data,
        )


# ─────────────────────────────────────────────────────────
# 2. Edición parcial de proyecto
# ─────────────────────────────────────────────────────────

class ProyectoUpdateSerializer(serializers.ModelSerializer):
    """
    Permite editar los metadatos de un proyecto existente.
    Solo se actualizan los campos enviados (PATCH semántico).

    Campos NO editables aquí (requieren acciones dedicadas):
      - visibilidad  → usar el endpoint toggle
      - investigador → inmutable
    """

    class Meta:
        model  = Proyecto
        fields = [
            "titulo",
            "descripcion",
            "categoria",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
        ]
        extra_kwargs = {
            "titulo":      {"required": False},
            "categoria":   {"required": False},
            "descripcion": {"required": False, "allow_blank": True},
            "etiquetas":   {"required": False},
            "fecha_inicio":{"required": False, "allow_null": True},
            "fecha_fin":   {"required": False, "allow_null": True},
        }

    def validate_titulo(self, value):
        value = value.strip()
        if len(value) < 5:
            raise serializers.ValidationError(
                _("El título debe tener al menos 5 caracteres.")
            )
        return value

    def validate_etiquetas(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError(_("Las etiquetas deben ser una lista."))
        cleaned = [str(tag).strip().lower() for tag in value if str(tag).strip()]
        if len(cleaned) > 20:
            raise serializers.ValidationError(
                _("No se pueden asignar más de 20 etiquetas.")
            )
        return cleaned

    def validate(self, attrs):
        # Combinar con valores actuales de la instancia para validación cruzada
        instance     = self.instance
        fecha_inicio = attrs.get("fecha_inicio", instance.fecha_inicio if instance else None)
        fecha_fin    = attrs.get("fecha_fin",    instance.fecha_fin    if instance else None)
        if fecha_inicio and fecha_fin and fecha_fin < fecha_inicio:
            raise serializers.ValidationError(
                {"fecha_fin": _("La fecha de fin no puede ser anterior a la fecha de inicio.")}
            )
        return attrs


# ─────────────────────────────────────────────────────────
# 3. Listado compacto — para explorador y panel
# ─────────────────────────────────────────────────────────

class ProyectoListSerializer(serializers.ModelSerializer):
    """
    Representación liviana de un proyecto para listas y galerías.

    Incluye métricas calculadas:
      - num_capas:         total de capas asociadas
      - capas_aprobadas:   capas con estado APROBADO
      - total_features:    suma de features en todas las capas
      - investigador_nombre: nombre visible del autor

    Usado en:
      - GET /api/proyectos/           (explorador público)
      - GET /api/proyectos/mis/       (panel del investigador)
    """

    investigador_nombre = serializers.SerializerMethodField()
    investigador_username = serializers.SerializerMethodField()
    num_capas           = serializers.SerializerMethodField()
    capas_aprobadas     = serializers.SerializerMethodField()
    total_features      = serializers.SerializerMethodField()

    class Meta:
        model  = Proyecto
        fields = [
            "id",
            "titulo",
            "categoria",
            "descripcion",
            "visibilidad",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
            "thumbnail",
            "fecha_creacion",
            "fecha_actualizacion",
            "publicado_en",
            "investigador_nombre",
            "investigador_username",
            "num_capas",
            "capas_aprobadas",
            "total_features",
        ]

    def get_investigador_nombre(self, obj) -> str:
        return obj.investigador.nombre_completo

    def get_investigador_username(self, obj) -> str:
        return obj.investigador.username

    def get_num_capas(self, obj) -> int:
        # Aprovecha el prefetch si se hizo en la vista
        if hasattr(obj, "_prefetched_capas"):
            return len(obj._prefetched_capas)
        return obj.capas.count()

    def get_capas_aprobadas(self, obj) -> int:
        return obj.capas.filter(estado_validacion=EstadoValidacion.APROBADO).count()

    def get_total_features(self, obj) -> int:
        from django.db.models import Sum
        result = obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).aggregate(total=Sum("num_features"))
        return result["total"] or 0


# ─────────────────────────────────────────────────────────
# 4. Detalle completo del proyecto (dueño / admin)
# ─────────────────────────────────────────────────────────

class ProyectoDetailSerializer(serializers.ModelSerializer):
    """
    Detalle completo de un proyecto con sus capas y métricas.
    Solo accesible para el dueño o el admin.

    Incluye la lista completa de capas con resumen de cada una.
    """

    investigador_info   = serializers.SerializerMethodField()
    capas               = serializers.SerializerMethodField()
    num_capas           = serializers.SerializerMethodField()
    total_features      = serializers.SerializerMethodField()
    capas_por_estado    = serializers.SerializerMethodField()
    capas_por_tipo      = serializers.SerializerMethodField()

    class Meta:
        model  = Proyecto
        fields = [
            "id",
            "titulo",
            "descripcion",
            "categoria",
            "visibilidad",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
            "bbox",
            "thumbnail",
            "fecha_creacion",
            "fecha_actualizacion",
            "publicado_en",
            "investigador_info",
            "capas",
            "num_capas",
            "total_features",
            "capas_por_estado",
            "capas_por_tipo",
        ]

    def get_investigador_info(self, obj) -> dict:
        u = obj.investigador
        return {
            "id":               str(u.id),
            "username":         u.username,
            "nombre_completo":  u.nombre_completo,
            "especialidad":     u.especialidad,
            "avatar":           u.avatar.url if u.avatar else None,
        }

    def get_capas(self, obj) -> list:
        capas_qs = obj.capas.order_by("-fecha_subida")
        return [_capa_resumen(c) for c in capas_qs]

    def get_num_capas(self, obj) -> int:
        return obj.capas.count()

    def get_total_features(self, obj) -> int:
        from django.db.models import Sum
        result = obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).aggregate(total=Sum("num_features"))
        return result["total"] or 0

    def get_capas_por_estado(self, obj) -> dict:
        """Distribución de capas según estado de validación."""
        from django.db.models import Count
        qs = obj.capas.values("estado_validacion").annotate(total=Count("id"))
        return {item["estado_validacion"]: item["total"] for item in qs}

    def get_capas_por_tipo(self, obj) -> dict:
        """Distribución de capas según tipo de archivo."""
        from django.db.models import Count
        qs = obj.capas.values("tipo_archivo").annotate(total=Count("id"))
        return {item["tipo_archivo"]: item["total"] for item in qs}


# ─────────────────────────────────────────────────────────
# 5. Vista pública del proyecto (usuarios sin auth / normales)
# ─────────────────────────────────────────────────────────

class ProyectoPublicoSerializer(serializers.ModelSerializer):
    """
    Representación pública de un proyecto para el explorador.
    Omite datos sensibles del investigador y solo expone capas APROBADAS.

    Usado en:
      - GET /api/proyectos/<id>/   (detalle público)
      - GET /api/proyectos/        (lista pública)
    """

    investigador_nombre    = serializers.SerializerMethodField()
    investigador_username  = serializers.SerializerMethodField()
    investigador_especialidad = serializers.SerializerMethodField()
    investigador_avatar    = serializers.SerializerMethodField()
    capas_publicas         = serializers.SerializerMethodField()
    total_features         = serializers.SerializerMethodField()

    class Meta:
        model  = Proyecto
        fields = [
            "id",
            "titulo",
            "descripcion",
            "categoria",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
            "thumbnail",
            "fecha_creacion",
            "publicado_en",
            "investigador_nombre",
            "investigador_username",
            "investigador_especialidad",
            "investigador_avatar",
            "capas_publicas",
            "total_features",
        ]

    def get_investigador_nombre(self, obj) -> str:
        return obj.investigador.nombre_completo

    def get_investigador_username(self, obj) -> str:
        return obj.investigador.username

    def get_investigador_especialidad(self, obj) -> str:
        return obj.investigador.especialidad

    def get_investigador_avatar(self, obj):
        u = obj.investigador
        return u.avatar.url if u.avatar else None

    def get_capas_publicas(self, obj) -> list:
        """Solo expone capas APROBADAS al público."""
        capas_qs = obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).order_by("-fecha_subida")
        return [_capa_resumen(c) for c in capas_qs]

    def get_total_features(self, obj) -> int:
        from django.db.models import Sum
        result = obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).aggregate(total=Sum("num_features"))
        return result["total"] or 0


# ─────────────────────────────────────────────────────────
# 6. Toggle de visibilidad (RF-06)
# ─────────────────────────────────────────────────────────

class ToggleVisibilidadSerializer(serializers.Serializer):
    """
    Cambia la visibilidad de un proyecto entre PRIVADO y PÚBLICO.

    Body JSON:
      { "visibilidad": "PUBLICO" | "PRIVADO" }

    Validaciones adicionales al publicar:
      - El proyecto debe tener al menos una capa APROBADA.
      - El título no puede ser genérico (mínimo 5 chars ya validado en create).

    Retorna el estado actualizado del proyecto.
    """

    visibilidad = serializers.ChoiceField(
        choices=VisibilidadProyecto.choices,
        required=True,
    )

    def validate(self, attrs):
        proyecto    = self.context["proyecto"]
        visibilidad = attrs["visibilidad"]

        if visibilidad == VisibilidadProyecto.PUBLICO:
            # Verificar que tiene al menos una capa aprobada antes de publicar
            capas_ok = proyecto.capas.filter(
                estado_validacion=EstadoValidacion.APROBADO
            ).count()
            if capas_ok == 0:
                raise serializers.ValidationError(
                    {
                        "visibilidad": _(
                            "No puedes publicar un proyecto sin al menos una capa "
                            "procesada y aprobada. Sube y valida al menos un archivo primero."
                        )
                    }
                )

        return attrs

    def save(self, **kwargs):
        proyecto    = self.context["proyecto"]
        visibilidad = self.validated_data["visibilidad"]

        if visibilidad == VisibilidadProyecto.PUBLICO:
            proyecto.publicar()
        else:
            proyecto.despublicar()

        return proyecto


# ─────────────────────────────────────────────────────────
# 7. Panel del investigador — resumen enriquecido (RF-11)
# ─────────────────────────────────────────────────────────

class PanelInvestigadorSerializer(serializers.ModelSerializer):
    """
    Vista enriquecida para el Panel de Gestión del investigador (RF-11).

    Extiende ProyectoListSerializer con información de capas
    y acciones rápidas disponibles.

    Devuelve para cada proyecto:
      - Estado de visibilidad (PRIVADO/PÚBLICO)
      - Número de capas y su estado
      - Si puede publicarse (tiene capas aprobadas)
      - Última actividad
    """

    num_capas           = serializers.SerializerMethodField()
    capas_aprobadas     = serializers.SerializerMethodField()
    capas_con_error     = serializers.SerializerMethodField()
    capas_procesando    = serializers.SerializerMethodField()
    total_features      = serializers.SerializerMethodField()
    puede_publicar      = serializers.SerializerMethodField()
    capas_resumen       = serializers.SerializerMethodField()

    class Meta:
        model  = Proyecto
        fields = [
            "id",
            "titulo",
            "descripcion",
            "categoria",
            "visibilidad",
            "etiquetas",
            "fecha_inicio",
            "fecha_fin",
            "thumbnail",
            "fecha_creacion",
            "fecha_actualizacion",
            "publicado_en",
            "num_capas",
            "capas_aprobadas",
            "capas_con_error",
            "capas_procesando",
            "total_features",
            "puede_publicar",
            "capas_resumen",
        ]

    def get_num_capas(self, obj) -> int:
        return obj.capas.count()

    def get_capas_aprobadas(self, obj) -> int:
        return obj.capas.filter(estado_validacion=EstadoValidacion.APROBADO).count()

    def get_capas_con_error(self, obj) -> int:
        return obj.capas.filter(estado_validacion=EstadoValidacion.ERROR).count()

    def get_capas_procesando(self, obj) -> int:
        return obj.capas.filter(
            estado_validacion__in=[EstadoValidacion.PENDIENTE, EstadoValidacion.PROCESANDO]
        ).count()

    def get_total_features(self, obj) -> int:
        from django.db.models import Sum
        result = obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).aggregate(total=Sum("num_features"))
        return result["total"] or 0

    def get_puede_publicar(self, obj) -> bool:
        """True si tiene al menos una capa aprobada y aún no está publicado."""
        if obj.visibilidad == VisibilidadProyecto.PUBLICO:
            return False   # Ya está publicado
        return obj.capas.filter(
            estado_validacion=EstadoValidacion.APROBADO
        ).exists()

    def get_capas_resumen(self, obj) -> list:
        """Lista compacta de capas para mostrar en la tarjeta del panel."""
        capas = obj.capas.order_by("-fecha_subida")[:5]  # máx 5 para el resumen
        return [
            {
                "id":                str(c.id),
                "nombre":            c.nombre,
                "tipo_archivo":      c.tipo_archivo,
                "estado_validacion": c.estado_validacion,
                "num_features":      c.num_features,
            }
            for c in capas
        ]