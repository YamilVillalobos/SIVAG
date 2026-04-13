"""
SIVAG — core/serializers.py
============================
Serializers para autenticación, registro y gestión de perfiles.

Contenido:
  RegistroInvestigadorSerializer  — Registro con campo especialidad (RF-01)
  RegistroNormalSerializer        — Registro básico (RF-01)
  LoginSerializer                 — Login con JWT; devuelve tokens + datos usuario
  TokenRefreshSerializer          — Re-exportado de simplejwt (por comodidad)
  PerfilUsuarioSerializer         — Lectura y actualización de /me (RF-10)
  CambioContrasenaSerializer      — Cambio seguro de contraseña (RF-10)
"""

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
# Re-exportamos para que urls.py solo importe desde aquí
from rest_framework_simplejwt.serializers import TokenRefreshSerializer  # noqa: F401

from .models import CustomUser, Rol


# ─────────────────────────────────────────────────────────
# Helpers internos
# ─────────────────────────────────────────────────────────

def _get_tokens(user: CustomUser) -> dict:
    """Genera par access/refresh para un usuario dado."""
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access":  str(refresh.access_token),
    }


# ─────────────────────────────────────────────────────────
# 1. Registro — Investigador
# ─────────────────────────────────────────────────────────

class RegistroInvestigadorSerializer(serializers.ModelSerializer):
    """
    Crea una cuenta con rol INVESTIGADOR.

    Campos requeridos según RF-01:
      nombre, apellidos, especialidad, fecha_nacimiento,
      nombre_de_usuario, correo y contraseña.

    La contraseña se valida con los validadores de Django
    (mínimo 8 chars, no muy común, no solo números).
    """

    password  = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password2 = serializers.CharField(
        write_only=True,
        required=True,
        label=_("Confirmar contraseña"),
        style={"input_type": "password"},
    )

    class Meta:
        model  = CustomUser
        fields = [
            "email",
            "username",
            "first_name",
            "last_name",
            "fecha_nacimiento",
            "especialidad",
            "password",
            "password2",
        ]
        extra_kwargs = {
            "first_name":      {"required": True},
            "last_name":       {"required": True},
            "fecha_nacimiento":{"required": True},
            "especialidad":    {"required": True},
        }

    # ── Validaciones de campo ──────────────────────────────

    def validate_email(self, value):
        if CustomUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                _("Ya existe una cuenta registrada con este correo.")
            )
        return value.lower()

    def validate_username(self, value):
        if CustomUser.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                _("Este nombre de usuario ya está en uso.")
            )
        return value

    def validate_especialidad(self, value):
        if not value.strip():
            raise serializers.ValidationError(
                _("La especialidad no puede estar vacía.")
            )
        return value.strip()

    # ── Validación cruzada ─────────────────────────────────

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password2": _("Las contraseñas no coinciden.")}
            )
        return attrs

    # ── Creación ───────────────────────────────────────────

    def create(self, validated_data):
        validated_data.pop("password2")
        password = validated_data.pop("password")

        user = CustomUser(**validated_data)
        user.rol = Rol.INVESTIGADOR
        user.set_password(password)
        user.save()
        return user


# ─────────────────────────────────────────────────────────
# 2. Registro — Usuario Normal
# ─────────────────────────────────────────────────────────

class RegistroNormalSerializer(serializers.ModelSerializer):
    """
    Crea una cuenta con rol NORMAL (público general).

    Campos requeridos según RF-01:
      nombre, apellidos, fecha_nacimiento, nombre_de_usuario,
      correo y contraseña.

    No incluye el campo especialidad.
    """

    password  = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password2 = serializers.CharField(
        write_only=True,
        required=True,
        label=_("Confirmar contraseña"),
        style={"input_type": "password"},
    )

    class Meta:
        model  = CustomUser
        fields = [
            "email",
            "username",
            "first_name",
            "last_name",
            "fecha_nacimiento",
            "password",
            "password2",
        ]
        extra_kwargs = {
            "first_name":      {"required": True},
            "last_name":       {"required": True},
            "fecha_nacimiento":{"required": True},
        }

    def validate_email(self, value):
        if CustomUser.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                _("Ya existe una cuenta registrada con este correo.")
            )
        return value.lower()

    def validate_username(self, value):
        if CustomUser.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError(
                _("Este nombre de usuario ya está en uso.")
            )
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError(
                {"password2": _("Las contraseñas no coinciden.")}
            )
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        password = validated_data.pop("password")

        user = CustomUser(**validated_data)
        user.rol = Rol.NORMAL
        user.set_password(password)
        user.save()
        return user


# ─────────────────────────────────────────────────────────
# 3. Login (devuelve tokens JWT + datos del usuario)
# ─────────────────────────────────────────────────────────

class LoginSerializer(serializers.Serializer):
    """
    Autentica con email + contraseña.

    Respuesta exitosa:
      {
        "access":  "<jwt_access>",
        "refresh": "<jwt_refresh>",
        "user": {
          "id", "email", "username", "rol",
          "nombre_completo", "avatar"
        }
      }

    Notas de seguridad:
      - Mensaje de error genérico (no revela si el email existe).
      - Bloquea el acceso si is_active = False.
      - Actualiza `ultimo_acceso` en cada login exitoso.
    """

    email    = serializers.EmailField()
    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate(self, attrs):
        email    = attrs.get("email", "").lower()
        password = attrs.get("password", "")

        # Django authenticate usa el USERNAME_FIELD (= email en nuestro modelo)
        user = authenticate(
            request=self.context.get("request"),
            username=email,
            password=password,
        )

        if not user:
            raise serializers.ValidationError(
                {"detail": _("Credenciales inválidas. Verifica tu correo y contraseña.")}
            )

        if not user.is_active:
            raise serializers.ValidationError(
                {"detail": _("Esta cuenta ha sido suspendida. Contacta al administrador.")}
            )

        # Actualizar última conexión
        user.ultimo_acceso = timezone.now()
        user.save(update_fields=["ultimo_acceso"])

        tokens = _get_tokens(user)

        return {
            **tokens,
            "user": {
                "id":             str(user.id),
                "email":          user.email,
                "username":       user.username,
                "rol":            user.rol,
                "nombre_completo":user.nombre_completo,
                "avatar":         user.avatar.url if user.avatar else None,
            },
        }


# ─────────────────────────────────────────────────────────
# 4. Perfil de usuario — Lectura y actualización (/me)
# ─────────────────────────────────────────────────────────

class PerfilUsuarioSerializer(serializers.ModelSerializer):
    """
    Serializer de solo lectura/actualización parcial del perfil propio.

    - GET  /api/auth/me/  → devuelve todos los campos visibles.
    - PATCH /api/auth/me/ → permite actualizar nombre, apellidos,
                            correo, especialidad (investigadores) y avatar.

    El rol, username, fecha_nacimiento e is_active NO son editables aquí
    por seguridad (se requiere acción del admin para cambiarlos).
    """

    nombre_completo = serializers.CharField(read_only=True)

    class Meta:
        model  = CustomUser
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "nombre_completo",
            "fecha_nacimiento",
            "especialidad",
            "rol",
            "avatar",
            "fecha_registro",
            "ultimo_acceso",
        ]
        read_only_fields = [
            "id",
            "username",
            "rol",
            "fecha_registro",
            "ultimo_acceso",
            "nombre_completo",
            "fecha_nacimiento",  # solo cambiable por admin
        ]

    def validate_email(self, value):
        user = self.context["request"].user
        # Permitir el mismo email (sin cambio), rechazar si ya lo usa otro
        if (
            CustomUser.objects
            .filter(email__iexact=value)
            .exclude(pk=user.pk)
            .exists()
        ):
            raise serializers.ValidationError(
                _("Este correo ya está registrado en otra cuenta.")
            )
        return value.lower()

    def validate_especialidad(self, value):
        """Solo investigadores deben llenar especialidad."""
        user = self.context["request"].user
        if user.rol != Rol.INVESTIGADOR and value.strip():
            raise serializers.ValidationError(
                _("Solo los investigadores pueden asignar una especialidad.")
            )
        return value.strip()

    def update(self, instance, validated_data):
        # Avatar: si se envía None explícitamente se borra
        avatar = validated_data.pop("avatar", ...)  # ... = sentinel (no enviado)
        if avatar is not ...:
            if instance.avatar and avatar is None:
                instance.avatar.delete(save=False)
            instance.avatar = avatar

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance


# ─────────────────────────────────────────────────────────
# 5. Cambio de contraseña (RF-10)
# ─────────────────────────────────────────────────────────

class CambioContrasenaSerializer(serializers.Serializer):
    """
    Permite al usuario autenticado cambiar su contraseña.
    Requiere la contraseña actual para confirmar identidad.

    Endpoint: POST /api/auth/me/cambiar-password/
    """

    password_actual  = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )
    password_nuevo   = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password_nuevo2  = serializers.CharField(
        write_only=True,
        label=_("Confirmar nueva contraseña"),
        style={"input_type": "password"},
    )

    def validate_password_actual(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(
                _("La contraseña actual es incorrecta.")
            )
        return value

    def validate(self, attrs):
        if attrs["password_nuevo"] != attrs["password_nuevo2"]:
            raise serializers.ValidationError(
                {"password_nuevo2": _("Las contraseñas nuevas no coinciden.")}
            )
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["password_nuevo"])
        user.save(update_fields=["password"])
        return user


# ─────────────────────────────────────────────────────────
# 6. Solicitud de recuperación de contraseña (RF-01)
# ─────────────────────────────────────────────────────────

class SolicitudRecuperacionSerializer(serializers.Serializer):
    """
    Paso 1 del flujo de recuperación:
      POST /api/auth/recuperar-password/ { "email": "..." }

    Siempre responde 200 para no revelar si el email existe
    (protección contra enumeración de usuarios).
    La lógica de generar y enviar el token está en la View.
    """

    email = serializers.EmailField()

    def validate_email(self, value):
        # Normalizamos pero NO lanzamos error si no existe
        return value.lower()


# ─────────────────────────────────────────────────────────
# 7. Confirmación de recuperación de contraseña
# ─────────────────────────────────────────────────────────

class ConfirmarRecuperacionSerializer(serializers.Serializer):
    """
    Paso 2 del flujo de recuperación:
      POST /api/auth/recuperar-password/confirmar/
      { "token": "...", "password_nuevo": "...", "password_nuevo2": "..." }

    El token es el valor almacenado en CustomUser.token_recuperacion.
    """

    token           = serializers.CharField()
    password_nuevo  = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )
    password_nuevo2 = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate(self, attrs):
        if attrs["password_nuevo"] != attrs["password_nuevo2"]:
            raise serializers.ValidationError(
                {"password_nuevo2": _("Las contraseñas no coinciden.")}
            )

        # Verificar que el token exista y no haya expirado
        try:
            user = CustomUser.objects.get(
                token_recuperacion=attrs["token"],
                is_active=True,
            )
        except CustomUser.DoesNotExist:
            raise serializers.ValidationError(
                {"token": _("El enlace de recuperación no es válido o ya fue utilizado.")}
            )

        now = timezone.now()
        if user.token_recuperacion_exp and user.token_recuperacion_exp < now:
            raise serializers.ValidationError(
                {"token": _("El enlace de recuperación ha expirado. Solicita uno nuevo.")}
            )

        attrs["user"] = user
        return attrs

    def save(self, **kwargs):
        user = self.validated_data["user"]
        user.set_password(self.validated_data["password_nuevo"])
        # Invalidar el token para que no se pueda reutilizar
        user.token_recuperacion     = ""
        user.token_recuperacion_exp = None
        user.save(update_fields=["password", "token_recuperacion", "token_recuperacion_exp"])
        return user