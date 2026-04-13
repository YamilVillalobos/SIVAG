"""
SIVAG — core/models.py
======================
Modelos de base de datos para el Sistema de Inteligencia y
Visualización de Activos Geoespaciales.

Estructura:
  Usuario         — CustomUser con roles (INVESTIGADOR | NORMAL | ADMIN)
  Proyecto        — Contenedor temático por investigador
  CapaGeoespacial — Activo geoespacial individual dentro de un proyecto
  AtributoTabular — Filas editables de archivos Excel (sin re-carga)
  VersionCapa     — Historial de versiones por capa
  LogAuditoria    — Registro forense de cada acción relevante
  ExportacionReporte — Registro de PDFs/JPGs generados por usuarios
"""

import os
import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ─────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────

def avatar_upload_path(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower()
    return f"avatars/{instance.id}.{ext}"


def capa_upload_path(instance, filename):
    return f"capas/{instance.proyecto.id}/{uuid.uuid4().hex}_{filename}"


def reporte_upload_path(instance, filename):
    return f"reportes/{instance.usuario.id}/{uuid.uuid4().hex}_{filename}"


# ─────────────────────────────────────────────────────────
# Constantes / choices
# ─────────────────────────────────────────────────────────

class Rol(models.TextChoices):
    INVESTIGADOR = "INVESTIGADOR", _("Investigador")
    NORMAL       = "NORMAL",       _("Usuario Normal")
    ADMIN        = "ADMIN",        _("Administrador")


class CategoriaProyecto(models.TextChoices):
    CLIMA       = "CLIMA",       _("Clima")
    FAUNA       = "FAUNA",       _("Fauna")
    AGUA        = "AGUA",        _("Agua / Hidrología")
    SUELO       = "SUELO",       _("Suelo")
    VEGETACION  = "VEGETACION",  _("Vegetación")
    ATMOSFERA   = "ATMOSFERA",   _("Atmósfera")
    OTRO        = "OTRO",        _("Otro")


class TipoArchivo(models.TextChoices):
    EXCEL    = "EXCEL",    _("Excel (.xlsx)")
    SHAPE    = "SHAPE",    _("Shapefile (.zip)")
    GEOTIFF  = "GEOTIFF",  _("GeoTIFF (.tiff)")


class TipoGeometria(models.TextChoices):
    PUNTO     = "PUNTO",     _("Puntos")
    LINEA     = "LINEA",     _("Líneas")
    POLIGONO  = "POLIGONO",  _("Polígonos")
    RASTER    = "RASTER",    _("Ráster")
    MIXTO     = "MIXTO",     _("Mixto")


class EstadoValidacion(models.TextChoices):
    PENDIENTE  = "PENDIENTE",  _("Pendiente")
    APROBADO   = "APROBADO",   _("Aprobado")
    ERROR      = "ERROR",      _("Error de validación")
    PROCESANDO = "PROCESANDO", _("Procesando ETL")


class VisibilidadProyecto(models.TextChoices):
    PRIVADO = "PRIVADO", _("Privado")
    PUBLICO = "PUBLICO", _("Público")


class AccionLog(models.TextChoices):
    LOGIN           = "LOGIN",          _("Inicio de sesión")
    LOGOUT          = "LOGOUT",         _("Cierre de sesión")
    REGISTRO        = "REGISTRO",       _("Registro de usuario")
    SUBIDA          = "SUBIDA",         _("Subida de archivo")
    VALIDACION_OK   = "VALIDACION_OK",  _("Validación exitosa")
    VALIDACION_ERR  = "VALIDACION_ERR", _("Error de validación")
    EDICION_ATTR    = "EDICION_ATTR",   _("Edición de atributos")
    PUBLICACION     = "PUBLICACION",    _("Publicación de proyecto")
    DESPUBLICACION  = "DESPUBLICACION", _("Despublicación")
    ELIMINACION     = "ELIMINACION",    _("Eliminación de recurso")
    EXPORTACION     = "EXPORTACION",    _("Exportación de reporte")
    MODERACION      = "MODERACION",     _("Moderación por administrador")
    CAMBIO_CONTRASENA = "CAMBIO_CONTRASENA", _("Cambio de contraseña")
    RECUPERACION    = "RECUPERACION",   _("Recuperación de contraseña")


class FormatoExportacion(models.TextChoices):
    PDF = "PDF", _("PDF")
    JPG = "JPG", _("JPG")


# ─────────────────────────────────────────────────────────
# Manager personalizado para CustomUser
# ─────────────────────────────────────────────────────────

class CustomUserManager(BaseUserManager):
    """Manager que usa email como identificador único en lugar de username."""

    def create_user(self, email, username, password=None, **extra_fields):
        if not email:
            raise ValueError(_("El correo electrónico es obligatorio."))
        if not username:
            raise ValueError(_("El nombre de usuario es obligatorio."))

        email = self.normalize_email(email)
        extra_fields.setdefault("rol", Rol.NORMAL)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, username, password=None, **extra_fields):
        extra_fields.setdefault("rol", Rol.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(email, username, password, **extra_fields)


# ─────────────────────────────────────────────────────────
# 1. CustomUser
# ─────────────────────────────────────────────────────────

class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Usuario central de SIVAG con tres roles:
      - INVESTIGADOR: puede subir activos y publicar proyectos.
      - NORMAL:       puede consultar y exportar proyectos públicos.
      - ADMIN:        control total de la plataforma.

    Extiende AbstractBaseUser para control fino sobre campos y
    autenticación JWT (djangorestframework-simplejwt).
    """

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email         = models.EmailField(_("correo electrónico"), unique=True, db_index=True)
    username      = models.CharField(_("nombre de usuario"), max_length=50, unique=True, db_index=True)
    first_name    = models.CharField(_("nombre(s)"), max_length=100)
    last_name     = models.CharField(_("apellidos"), max_length=150)
    fecha_nacimiento = models.DateField(_("fecha de nacimiento"), null=True, blank=True)

    # Campo exclusivo para investigadores
    especialidad  = models.CharField(
        _("especialidad"),
        max_length=200,
        blank=True,
        help_text=_("Área de investigación (solo perfil Investigador).")
    )

    rol           = models.CharField(
        _("rol"),
        max_length=20,
        choices=Rol.choices,
        default=Rol.NORMAL,
        db_index=True,
    )
    avatar        = models.ImageField(
        _("foto de perfil"),
        upload_to=avatar_upload_path,
        null=True,
        blank=True,
    )

    # Flags estándar de Django
    is_active     = models.BooleanField(_("activo"), default=True)
    is_staff      = models.BooleanField(_("staff"), default=False)

    fecha_registro = models.DateTimeField(_("fecha de registro"), default=timezone.now)
    ultimo_acceso  = models.DateTimeField(_("último acceso"), null=True, blank=True)

    # Token para recuperación de contraseña
    token_recuperacion     = models.CharField(max_length=96, blank=True, db_index=True)
    token_recuperacion_exp = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD  = "email"
    REQUIRED_FIELDS = ["username", "first_name", "last_name"]

    class Meta:
        verbose_name        = _("usuario")
        verbose_name_plural = _("usuarios")
        ordering            = ["-fecha_registro"]
        indexes = [
            models.Index(fields=["rol", "is_active"]),
        ]

    def __str__(self):
        return f"{self.username} <{self.email}> [{self.rol}]"

    @property
    def nombre_completo(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def es_investigador(self):
        return self.rol == Rol.INVESTIGADOR

    @property
    def es_admin(self):
        return self.rol == Rol.ADMIN


# ─────────────────────────────────────────────────────────
# 2. Proyecto
# ─────────────────────────────────────────────────────────

class Proyecto(models.Model):
    """
    Contenedor temático que agrupa una o más capas geoespaciales.
    Pertenece a un Investigador y puede ser publicado para consulta pública.

    Ejemplos: "Monitoreo de aves 2024 — Tabasco",
              "Índice de sequía histórico Q1-2025"
    """

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    investigador = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="proyectos",
        limit_choices_to={"rol": Rol.INVESTIGADOR},
    )
    titulo      = models.CharField(_("título"), max_length=250)
    descripcion = models.TextField(_("descripción"), blank=True)
    categoria   = models.CharField(
        _("categoría"),
        max_length=20,
        choices=CategoriaProyecto.choices,
        db_index=True,
    )
    visibilidad = models.CharField(
        _("visibilidad"),
        max_length=10,
        choices=VisibilidadProyecto.choices,
        default=VisibilidadProyecto.PRIVADO,
        db_index=True,
    )

    # Metadatos para búsqueda y filtros
    etiquetas      = models.JSONField(_("etiquetas"), default=list, blank=True)
    fecha_inicio   = models.DateField(_("fecha inicio del estudio"), null=True, blank=True)
    fecha_fin      = models.DateField(_("fecha fin del estudio"),    null=True, blank=True)

    # Área geográfica de cobertura (bounding box, SRID 4326)
    bbox = gis_models.PolygonField(
        _("bounding box"),
        srid=4326,
        null=True,
        blank=True,
        help_text=_("Calculado automáticamente al procesar las capas.")
    )

    # Thumbnail generado del dashboard
    thumbnail = models.ImageField(
        _("miniatura del dashboard"),
        upload_to="thumbnails/",
        null=True,
        blank=True,
    )

    fecha_creacion     = models.DateTimeField(auto_now_add=True)
    fecha_actualizacion = models.DateTimeField(auto_now=True)
    publicado_en       = models.DateTimeField(_("publicado en"), null=True, blank=True)

    class Meta:
        verbose_name        = _("proyecto")
        verbose_name_plural = _("proyectos")
        ordering            = ["-fecha_actualizacion"]
        indexes = [
            models.Index(fields=["visibilidad", "categoria"]),
            models.Index(fields=["investigador", "visibilidad"]),
        ]

    def __str__(self):
        return f"{self.titulo} ({self.categoria}) — {self.investigador.username}"

    def publicar(self):
        self.visibilidad = VisibilidadProyecto.PUBLICO
        self.publicado_en = timezone.now()
        self.save(update_fields=["visibilidad", "publicado_en"])

    def despublicar(self):
        self.visibilidad = VisibilidadProyecto.PRIVADO
        self.save(update_fields=["visibilidad"])


# ─────────────────────────────────────────────────────────
# 3. CapaGeoespacial
# ─────────────────────────────────────────────────────────

class CapaGeoespacial(models.Model):
    """
    Activo geoespacial individual dentro de un proyecto.
    Soporta tres tipos de origen: Excel (.xlsx), Shapefile (.zip), GeoTIFF (.tiff).

    El motor ETL de Python (Fiona/Rasterio/Pandas) procesa el archivo
    original y rellena los campos 'geojson_path' / 'wkb_geometria' para
    el renderizado rápido con Leaflet.js.

    Campos de geometría:
      - wkb_geometria  → para vectores (Shape/Excel con coords).
        Almacena la geometría como GeometryCollectionField para soportar
        puntos, líneas y polígonos en la misma capa.
      - raster_path    → ruta relativa al archivo GeoTIFF optimizado (COG).
    """

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proyecto    = models.ForeignKey(
        Proyecto,
        on_delete=models.CASCADE,
        related_name="capas",
    )
    nombre      = models.CharField(_("nombre de la capa"), max_length=200)
    descripcion = models.TextField(_("descripción"), blank=True)

    # ── Archivo original ──
    tipo_archivo = models.CharField(
        _("tipo de archivo"),
        max_length=10,
        choices=TipoArchivo.choices,
        db_index=True,
    )
    archivo_original = models.FileField(
        _("archivo original"),
        upload_to=capa_upload_path,
        help_text=_("Archivo tal como lo subió el investigador.")
    )
    tamano_bytes = models.BigIntegerField(_("tamaño en bytes"), default=0)
    checksum_md5 = models.CharField(max_length=32, blank=True)

    # ── Validación ETL ──
    estado_validacion = models.CharField(
        _("estado de validación"),
        max_length=15,
        choices=EstadoValidacion.choices,
        default=EstadoValidacion.PENDIENTE,
        db_index=True,
    )
    mensaje_error = models.TextField(_("mensaje de error"), blank=True)
    sistema_coordenadas = models.CharField(
        _("sistema de coordenadas (EPSG)"),
        max_length=50,
        blank=True,
        help_text=_("Ej: EPSG:4326, EPSG:32614")
    )

    # ── Datos procesados (vectores) ──
    tipo_geometria = models.CharField(
        _("tipo de geometría"),
        max_length=10,
        choices=TipoGeometria.choices,
        blank=True,
        db_index=True,
    )
    wkb_geometria = gis_models.GeometryCollectionField(
        _("geometría (PostGIS)"),
        srid=4326,
        null=True,
        blank=True,
        help_text=_("Geometría completa almacenada en PostGIS para consultas espaciales.")
    )
    geojson_path = models.CharField(
        _("ruta GeoJSON optimizado"),
        max_length=500,
        blank=True,
        help_text=_("Ruta relativa al archivo GeoJSON generado por el ETL para Leaflet.")
    )
    num_features  = models.PositiveIntegerField(_("número de features"), default=0)

    # ── Datos procesados (ráster) ──
    raster_path = models.CharField(
        _("ruta GeoTIFF optimizado (COG)"),
        max_length=500,
        blank=True,
    )
    raster_bbox = gis_models.PolygonField(
        _("bounding box del ráster"),
        srid=4326,
        null=True,
        blank=True,
    )
    raster_banda_min = models.FloatField(null=True, blank=True)
    raster_banda_max = models.FloatField(null=True, blank=True)

    # ── Metadatos de columnas (para Excel/Shape) ──
    columnas_schema = models.JSONField(
        _("esquema de columnas"),
        default=dict,
        blank=True,
        help_text=_("{'col_name': 'dtype', ...} detectado durante validación.")
    )
    columna_lat = models.CharField(max_length=100, blank=True)
    columna_lon = models.CharField(max_length=100, blank=True)

    fecha_subida      = models.DateTimeField(auto_now_add=True)
    fecha_procesado   = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = _("capa geoespacial")
        verbose_name_plural = _("capas geoespaciales")
        ordering            = ["-fecha_subida"]
        indexes = [
            models.Index(fields=["proyecto", "estado_validacion"]),
            models.Index(fields=["tipo_archivo", "tipo_geometria"]),
        ]

    def __str__(self):
        return f"{self.nombre} [{self.tipo_archivo}] — {self.proyecto.titulo}"

    def archivo_es_valido(self):
        return self.estado_validacion == EstadoValidacion.APROBADO


# ─────────────────────────────────────────────────────────
# 4. AtributoTabular
# ─────────────────────────────────────────────────────────

class AtributoTabular(models.Model):
    """
    Fila editable de un archivo Excel / Shape con sus atributos.
    Permite al investigador corregir valores (ej: nombre de especie)
    directamente en la tabla web sin re-cargar el archivo original.

    Cada fila tiene un campo JSON flexible `datos` que guarda
    todos los atributos detectados durante el ETL.
    """

    id      = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    capa    = models.ForeignKey(
        CapaGeoespacial,
        on_delete=models.CASCADE,
        related_name="atributos",
    )
    indice_original = models.PositiveIntegerField(
        _("índice fila original"),
        help_text=_("Número de fila en el archivo fuente para trazabilidad.")
    )

    # Coordenadas individuales del punto (para Excel)
    latitud  = models.FloatField(null=True, blank=True)
    longitud = models.FloatField(null=True, blank=True)
    punto    = gis_models.PointField(srid=4326, null=True, blank=True)

    # Todos los atributos del feature en JSON
    datos = models.JSONField(
        _("datos del feature"),
        default=dict,
        help_text=_("Atributos del feature: {'especie': 'X', 'fecha': '...', ...}")
    )

    # Flag de edición para auditoría
    modificado_por = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="atributos_editados",
    )
    fecha_modificacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = _("atributo tabular")
        verbose_name_plural = _("atributos tabulares")
        ordering            = ["capa", "indice_original"]
        indexes = [
            models.Index(fields=["capa", "indice_original"]),
        ]
        # Índice espacial para consultas de radio
        # PostGIS crea automáticamente índice GIST sobre PointField

    def __str__(self):
        return f"Fila {self.indice_original} — {self.capa.nombre}"


# ─────────────────────────────────────────────────────────
# 5. VersionCapa
# ─────────────────────────────────────────────────────────

class VersionCapa(models.Model):
    """
    Registro histórico de versiones de una CapaGeoespacial.
    Cada vez que el investigador actualiza un archivo se crea
    una nueva versión; la capa activa apunta siempre a la última.
    """

    id      = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    capa    = models.ForeignKey(
        CapaGeoespacial,
        on_delete=models.CASCADE,
        related_name="versiones",
    )
    numero_version = models.PositiveSmallIntegerField(_("número de versión"), default=1)
    archivo_version = models.FileField(
        _("archivo de esta versión"),
        upload_to="versiones/",
    )
    nota_version = models.CharField(_("nota de versión"), max_length=500, blank=True)
    creado_en    = models.DateTimeField(auto_now_add=True)
    creado_por   = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name="versiones_creadas",
    )

    class Meta:
        verbose_name        = _("versión de capa")
        verbose_name_plural = _("versiones de capas")
        unique_together     = [("capa", "numero_version")]
        ordering            = ["capa", "-numero_version"]

    def __str__(self):
        return f"{self.capa.nombre} v{self.numero_version}"


# ─────────────────────────────────────────────────────────
# 6. HistorialConsulta
# ─────────────────────────────────────────────────────────

class HistorialConsulta(models.Model):
    """
    Registro de proyectos públicos visitados por un Usuario Normal.
    Alimenta el panel "Historial de consulta" del perfil Normal.
    """

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario  = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="historial_consultas",
    )
    proyecto = models.ForeignKey(
        Proyecto,
        on_delete=models.CASCADE,
        related_name="visitas",
    )
    visitado_en = models.DateTimeField(auto_now_add=True)
    veces       = models.PositiveIntegerField(default=1)

    class Meta:
        verbose_name        = _("historial de consulta")
        verbose_name_plural = _("historial de consultas")
        unique_together     = [("usuario", "proyecto")]
        ordering            = ["-visitado_en"]


# ─────────────────────────────────────────────────────────
# 7. ExportacionReporte
# ─────────────────────────────────────────────────────────

class ExportacionReporte(models.Model):
    """
    Registro de cada PDF o JPG generado por un usuario.
    - Alimenta el panel "Mis exportaciones" del perfil Normal.
    - Permite al Administrador auditar exportaciones masivas.
    """

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    usuario  = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="exportaciones",
    )
    proyecto = models.ForeignKey(
        Proyecto,
        on_delete=models.CASCADE,
        related_name="exportaciones",
    )
    formato  = models.CharField(
        _("formato"),
        max_length=5,
        choices=FormatoExportacion.choices,
    )
    archivo  = models.FileField(
        _("archivo generado"),
        upload_to=reporte_upload_path,
        null=True,
        blank=True,
    )
    # Snapshot del viewport del mapa en el momento de exportar
    bbox_exportado = models.JSONField(
        _("bbox del mapa al exportar"),
        default=dict,
        blank=True,
        help_text=_("{'north': ..., 'south': ..., 'east': ..., 'west': ...}")
    )
    generado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = _("exportación de reporte")
        verbose_name_plural = _("exportaciones de reportes")
        ordering            = ["-generado_en"]


# ─────────────────────────────────────────────────────────
# 8. LogAuditoria
# ─────────────────────────────────────────────────────────

class LogAuditoria(models.Model):
    """
    Registro forense inmutable de cada acción relevante.
    Solo el Administrador puede leerlos; nadie puede editarlos
    (el ORM nunca llamará a save() sobre registros existentes).

    Campos clave:
      - usuario_id  → puede ser NULL si la acción es anónima (intento de login fallido).
      - objeto_tipo → nombre del modelo afectado ('CapaGeoespacial', 'Proyecto', etc.)
      - objeto_id   → UUID del objeto afectado (como string, para flexibilidad).
      - ip_origen   → IPv4 o IPv6.
      - user_agent  → para detección de bots o accesos inusuales.
      - datos_extra → JSON libre para contexto adicional (nombre de archivo, error msg, etc.)
    """

    id            = models.BigAutoField(primary_key=True)
    usuario       = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="logs",
        db_index=True,
    )
    # Guardamos email y username en el momento por si el usuario es eliminado
    usuario_email    = models.EmailField(blank=True)
    usuario_username = models.CharField(max_length=50, blank=True)

    accion       = models.CharField(
        _("acción"),
        max_length=25,
        choices=AccionLog.choices,
        db_index=True,
    )
    objeto_tipo  = models.CharField(_("tipo de objeto"), max_length=100, blank=True)
    objeto_id    = models.CharField(_("ID del objeto"),  max_length=36, blank=True)

    ip_origen    = models.GenericIPAddressField(
        _("IP de origen"),
        null=True,
        blank=True,
        db_index=True,
    )
    user_agent   = models.TextField(_("user agent"), blank=True)

    datos_extra  = models.JSONField(
        _("datos adicionales"),
        default=dict,
        blank=True,
        help_text=_("Contexto libre: nombre de archivo, mensaje de error, etc.")
    )

    timestamp = models.DateTimeField(
        _("fecha y hora"),
        default=timezone.now,
        db_index=True,
    )

    class Meta:
        verbose_name        = _("log de auditoría")
        verbose_name_plural = _("logs de auditoría")
        ordering            = ["-timestamp"]
        indexes = [
            models.Index(fields=["accion", "timestamp"]),
            models.Index(fields=["usuario", "timestamp"]),
            models.Index(fields=["ip_origen", "timestamp"]),
            models.Index(fields=["objeto_tipo", "objeto_id"]),
        ]

    def __str__(self):
        usr = self.usuario_username or "anónimo"
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {usr} — {self.accion}"

    def save(self, *args, **kwargs):
        """
        Pobla los campos de snapshot (email/username) en la primera inserción.
        """
        if not self.pk and self.usuario:
            self.usuario_email    = self.usuario.email
            self.usuario_username = self.usuario.username
        super().save(*args, **kwargs)
