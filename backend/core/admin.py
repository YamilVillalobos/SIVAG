"""
SIVAG — core/admin.py
=====================
Registro de modelos en el panel de administración de Django.
Permite al Administrador gestionar usuarios, proyectos y logs
directamente desde /admin/ durante el desarrollo y soporte.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import (
    AtributoTabular,
    CapaGeoespacial,
    CustomUser,
    ExportacionReporte,
    HistorialConsulta,
    LogAuditoria,
    Proyecto,
    VersionCapa,
)


# ─────────────────────────────────────────────────────────
# CustomUser
# ─────────────────────────────────────────────────────────

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display  = ("username", "email", "nombre_completo", "rol", "is_active", "fecha_registro")
    list_filter   = ("rol", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering      = ("-fecha_registro",)

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (_("Información personal"), {"fields": ("first_name", "last_name", "fecha_nacimiento", "especialidad", "avatar")}),
        (_("Permisos"), {"fields": ("rol", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Fechas"), {"fields": ("fecha_registro", "ultimo_acceso")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "username", "first_name", "last_name", "rol", "password1", "password2"),
        }),
    )
    readonly_fields = ("fecha_registro", "ultimo_acceso")


# ─────────────────────────────────────────────────────────
# Proyecto
# ─────────────────────────────────────────────────────────

class CapaInline(admin.TabularInline):
    model  = CapaGeoespacial
    fields = ("nombre", "tipo_archivo", "estado_validacion", "fecha_subida")
    readonly_fields = ("fecha_subida",)
    extra  = 0


@admin.register(Proyecto)
class ProyectoAdmin(admin.ModelAdmin):
    list_display  = ("titulo", "investigador", "categoria", "visibilidad", "fecha_creacion")
    list_filter   = ("categoria", "visibilidad")
    search_fields = ("titulo", "investigador__username")
    ordering      = ("-fecha_creacion",)
    inlines       = [CapaInline]
    readonly_fields = ("fecha_creacion", "fecha_actualizacion", "publicado_en")


# ─────────────────────────────────────────────────────────
# CapaGeoespacial
# ─────────────────────────────────────────────────────────

@admin.register(CapaGeoespacial)
class CapaGeoespacialAdmin(admin.ModelAdmin):
    list_display  = ("nombre", "proyecto", "tipo_archivo", "estado_validacion", "num_features", "fecha_subida")
    list_filter   = ("tipo_archivo", "estado_validacion", "tipo_geometria")
    search_fields = ("nombre", "proyecto__titulo")
    readonly_fields = ("fecha_subida", "fecha_procesado", "checksum_md5")


# ─────────────────────────────────────────────────────────
# LogAuditoria (solo lectura)
# ─────────────────────────────────────────────────────────

@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display  = ("timestamp", "usuario_username", "accion", "objeto_tipo", "ip_origen")
    list_filter   = ("accion",)
    search_fields = ("usuario_username", "usuario_email", "ip_origen", "objeto_id")
    ordering      = ("-timestamp",)
    readonly_fields = [f.name for f in LogAuditoria._meta.get_fields() if hasattr(f, 'name')]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# ─────────────────────────────────────────────────────────
# Resto de modelos
# ─────────────────────────────────────────────────────────

@admin.register(VersionCapa)
class VersionCapaAdmin(admin.ModelAdmin):
    list_display = ("capa", "numero_version", "creado_en", "creado_por")
    readonly_fields = ("creado_en",)


@admin.register(AtributoTabular)
class AtributoTabularAdmin(admin.ModelAdmin):
    list_display = ("capa", "indice_original", "latitud", "longitud", "fecha_modificacion")
    search_fields = ("capa__nombre",)


@admin.register(HistorialConsulta)
class HistorialConsultaAdmin(admin.ModelAdmin):
    list_display = ("usuario", "proyecto", "visitado_en", "veces")
    ordering = ("-visitado_en",)


@admin.register(ExportacionReporte)
class ExportacionReporteAdmin(admin.ModelAdmin):
    list_display = ("usuario", "proyecto", "formato", "generado_en")
    list_filter  = ("formato",)
    ordering     = ("-generado_en",)