"""
SIVAG — core/views.py
======================
Vistas de autenticación, registro y gestión de perfiles.

Endpoints implementados:
  POST   /api/auth/registro/investigador/     — Registro investigador (RF-01)
  POST   /api/auth/registro/normal/           — Registro usuario normal (RF-01)
  POST   /api/auth/login/                     — Login JWT (RF-01)
  POST   /api/auth/logout/                    — Blacklist del refresh token
  POST   /api/auth/token/refresh/             — Renovar access token
  GET    /api/auth/me/                        — Ver perfil propio (RF-10)
  PATCH  /api/auth/me/                        — Actualizar perfil propio (RF-10)
  POST   /api/auth/me/cambiar-password/       — Cambio de contraseña (RF-10)
  POST   /api/auth/recuperar-password/        — Solicitar recuperación (RF-01)
  POST   /api/auth/recuperar-password/confirmar/ — Confirmar recuperación (RF-01)

Cada vista que modifica datos genera un LogAuditoria (RF-09).
"""

import secrets
import logging

from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.mail import send_mail
from django.conf import settings

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny

from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import TokenError

from .models import CustomUser, LogAuditoria, AccionLog
from .permissions import IsActiveUser, IsOwnerOrAdmin
from .serializers import (
    RegistroInvestigadorSerializer,
    RegistroNormalSerializer,
    LoginSerializer,
    PerfilUsuarioSerializer,
    CambioContrasenaSerializer,
    SolicitudRecuperacionSerializer,
    ConfirmarRecuperacionSerializer,
    TokenRefreshSerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Helper: obtener IP real del request
# ─────────────────────────────────────────────────────────

def _get_client_ip(request) -> str:
    """
    Obtiene la IP real del cliente.
    Considera el header X-Forwarded-For que Nginx añade
    cuando actúa como proxy inverso.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


# ─────────────────────────────────────────────────────────
# Helper: crear log de auditoría
# ─────────────────────────────────────────────────────────

def _log(request, accion: str, usuario=None, objeto_tipo="", objeto_id="", datos_extra=None):
    """
    Registra una acción en LogAuditoria de forma segura.
    Si falla el log, NO interrumpe el flujo principal.
    """
    try:
        LogAuditoria.objects.create(
            usuario=usuario,
            accion=accion,
            objeto_tipo=objeto_tipo,
            objeto_id=str(objeto_id) if objeto_id else "",
            ip_origen=_get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            datos_extra=datos_extra or {},
        )
    except Exception as e:
        logger.error("Error al registrar log de auditoría: %s", e)


# ─────────────────────────────────────────────────────────
# 1. Registro — Investigador
# ─────────────────────────────────────────────────────────

class RegistroInvestigadorView(APIView):
    """
    POST /api/auth/registro/investigador/

    Crea una cuenta con rol INVESTIGADOR.
    No requiere autenticación previa.

    Body JSON:
      email, username, first_name, last_name,
      fecha_nacimiento, especialidad, password, password2

    Response 201:
      { "mensaje": "...", "user": { id, email, username, rol } }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroInvestigadorSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()

        _log(
            request,
            accion=AccionLog.REGISTRO,
            usuario=user,
            objeto_tipo="CustomUser",
            objeto_id=user.id,
            datos_extra={"rol": user.rol, "email": user.email},
        )

        return Response(
            {
                "mensaje": "Cuenta de Investigador creada exitosamente.",
                "user": {
                    "id":       str(user.id),
                    "email":    user.email,
                    "username": user.username,
                    "rol":      user.rol,
                },
            },
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────
# 2. Registro — Usuario Normal
# ─────────────────────────────────────────────────────────

class RegistroNormalView(APIView):
    """
    POST /api/auth/registro/normal/

    Crea una cuenta con rol NORMAL.
    No requiere autenticación previa.

    Body JSON:
      email, username, first_name, last_name,
      fecha_nacimiento, password, password2

    Response 201:
      { "mensaje": "...", "user": { id, email, username, rol } }
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegistroNormalSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()

        _log(
            request,
            accion=AccionLog.REGISTRO,
            usuario=user,
            objeto_tipo="CustomUser",
            objeto_id=user.id,
            datos_extra={"rol": user.rol, "email": user.email},
        )

        return Response(
            {
                "mensaje": "Cuenta creada exitosamente.",
                "user": {
                    "id":       str(user.id),
                    "email":    user.email,
                    "username": user.username,
                    "rol":      user.rol,
                },
            },
            status=status.HTTP_201_CREATED,
        )


# ─────────────────────────────────────────────────────────
# 3. Login
# ─────────────────────────────────────────────────────────

class LoginView(APIView):
    """
    POST /api/auth/login/

    Autentica con email + contraseña.
    No requiere autenticación previa.

    Body JSON:
      { "email": "...", "password": "..." }

    Response 200:
      {
        "access":  "<jwt>",
        "refresh": "<jwt>",
        "user": { id, email, username, rol, nombre_completo, avatar }
      }

    Response 401: credenciales inválidas o cuenta suspendida.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            # Login fallido — log sin usuario (puede ser intento con email inválido)
            _log(
                request,
                accion=AccionLog.LOGIN,
                usuario=None,
                datos_extra={
                    "exito": False,
                    "email_intentado": request.data.get("email", ""),
                    "error": serializer.errors,
                },
            )
            return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)

        data = serializer.validated_data

        # Log de login exitoso
        try:
            user = CustomUser.objects.get(email=data["user"]["email"])
            _log(
                request,
                accion=AccionLog.LOGIN,
                usuario=user,
                objeto_tipo="CustomUser",
                objeto_id=user.id,
                datos_extra={"exito": True},
            )
        except CustomUser.DoesNotExist:
            pass

        return Response(data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────
# 4. Logout (blacklist del refresh token)
# ─────────────────────────────────────────────────────────

class LogoutView(APIView):
    """
    POST /api/auth/logout/

    Invalida el refresh token enviado en el body.
    El access token expirará solo (vida útil: 8h configurada en settings).

    Requiere autenticación (access token en header Authorization: Bearer).

    Body JSON:
      { "refresh": "<refresh_token>" }

    Response 205: logout exitoso.
    Response 400: token inválido o ya usado.
    """

    permission_classes = [IsActiveUser]

    def post(self, request):
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"detail": _("El campo 'refresh' es requerido.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": _("El token no es válido o ya fue invalidado.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _log(
            request,
            accion=AccionLog.LOGOUT,
            usuario=request.user,
            objeto_tipo="CustomUser",
            objeto_id=request.user.id,
        )

        return Response(
            {"mensaje": "Sesión cerrada correctamente."},
            status=status.HTTP_205_RESET_CONTENT,
        )


# ─────────────────────────────────────────────────────────
# 5. Refresh Token
# ─────────────────────────────────────────────────────────

class TokenRefreshView(APIView):
    """
    POST /api/auth/token/refresh/

    Renueva el access token usando el refresh token.
    No requiere autenticación (el refresh ES la credencial).

    Body JSON:
      { "refresh": "<refresh_token>" }

    Response 200:
      { "access": "<nuevo_jwt>", "refresh": "<nuevo_jwt>" }
      (ROTATE_REFRESH_TOKENS = True en settings, se devuelve nuevo refresh)
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────
# 6. Perfil propio — Ver y Actualizar (/me)
# ─────────────────────────────────────────────────────────

class MiPerfilView(APIView):
    """
    GET   /api/auth/me/  — Devuelve el perfil del usuario autenticado.
    PATCH /api/auth/me/  — Actualiza campos permitidos del perfil.

    Requiere autenticación.
    Acepta multipart/form-data para subir avatar.

    Campos actualizables vía PATCH:
      first_name, last_name, email, especialidad (investigadores), avatar

    Campos de solo lectura (no se pueden cambiar aquí):
      id, username, rol, fecha_nacimiento, fecha_registro, ultimo_acceso
    """

    permission_classes = [IsActiveUser, IsOwnerOrAdmin]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        serializer = PerfilUsuarioSerializer(
            request.user,
            context={"request": request},
        )
        return Response(serializer.data)

    def patch(self, request):
        serializer = PerfilUsuarioSerializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        _log(
            request,
            accion=AccionLog.EDICION_ATTR,
            usuario=request.user,
            objeto_tipo="CustomUser",
            objeto_id=request.user.id,
            datos_extra={"campos_actualizados": list(request.data.keys())},
        )

        return Response(serializer.data)


# ─────────────────────────────────────────────────────────
# 7. Cambio de contraseña
# ─────────────────────────────────────────────────────────

class CambioContrasenaView(APIView):
    """
    POST /api/auth/me/cambiar-password/

    Cambia la contraseña del usuario autenticado.
    Requiere la contraseña actual para confirmar identidad.

    Body JSON:
      {
        "password_actual":  "...",
        "password_nuevo":   "...",
        "password_nuevo2":  "..."
      }

    Response 200: cambio exitoso.
    Response 400: contraseña actual incorrecta o nuevas no coinciden.
    """

    permission_classes = [IsActiveUser]

    def post(self, request):
        serializer = CambioContrasenaSerializer(
            data=request.data,
            context={"request": request},
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()

        _log(
            request,
            accion=AccionLog.CAMBIO_CONTRASENA,
            usuario=request.user,
            objeto_tipo="CustomUser",
            objeto_id=request.user.id,
        )

        return Response(
            {"mensaje": "Contraseña actualizada correctamente."},
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────
# 8. Recuperación de contraseña — Paso 1 (solicitud)
# ─────────────────────────────────────────────────────────

class SolicitudRecuperacionView(APIView):
    """
    POST /api/auth/recuperar-password/

    Genera un token de recuperación y envía el email.
    SIEMPRE responde 200 aunque el email no exista
    (protección contra enumeración de usuarios).

    Body JSON:
      { "email": "usuario@ejemplo.com" }

    Flujo:
      1. Busca el usuario por email (silenciosamente si no existe).
      2. Genera token seguro de 48 bytes (96 chars hex).
      3. Establece expiración a 1 hora desde ahora.
      4. Envía email con el token (o URL con token).
      5. Registra en LogAuditoria.

    En desarrollo el email se imprime en consola (EMAIL_BACKEND = console).
    En producción configurar SMTP real en settings.py.
    """

    permission_classes = [AllowAny]

    # Tiempo de vida del token en horas
    TOKEN_EXPIRY_HOURS = 1

    def post(self, request):
        serializer = SolicitudRecuperacionSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]

        # Respuesta genérica — siempre la misma sin importar si existe
        respuesta_generica = Response(
            {
                "mensaje": (
                    "Si ese correo está registrado, recibirás un enlace "
                    "de recuperación en los próximos minutos."
                )
            },
            status=status.HTTP_200_OK,
        )

        try:
            user = CustomUser.objects.get(email=email, is_active=True)
        except CustomUser.DoesNotExist:
            # No revelamos que el email no existe
            return respuesta_generica

        # Generar token criptográficamente seguro
        token = secrets.token_hex(48)   # 96 caracteres
        expiracion = timezone.now() + timezone.timedelta(hours=self.TOKEN_EXPIRY_HOURS)

        user.token_recuperacion     = token
        user.token_recuperacion_exp = expiracion
        user.save(update_fields=["token_recuperacion", "token_recuperacion_exp"])

        # Construir URL de recuperación
        # En producción esta URL apunta al frontend (React/HTML)
        frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
        url_recuperacion = f"{frontend_url}/recuperar-password?token={token}"

        # Enviar email
        try:
            send_mail(
                subject="SIVAG — Recuperación de contraseña",
                message=(
                    f"Hola {user.first_name},\n\n"
                    f"Recibimos una solicitud para restablecer tu contraseña en SIVAG.\n\n"
                    f"Haz clic en el siguiente enlace para crear una nueva contraseña:\n"
                    f"{url_recuperacion}\n\n"
                    f"Este enlace expira en {self.TOKEN_EXPIRY_HOURS} hora(s).\n\n"
                    f"Si no solicitaste este cambio, ignora este mensaje.\n\n"
                    f"— Equipo SIVAG / CCGS"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )
        except Exception as e:
            logger.error("Error al enviar email de recuperación a %s: %s", email, e)
            # No revelamos el error al cliente
            return respuesta_generica

        _log(
            request,
            accion=AccionLog.RECUPERACION,
            usuario=user,
            objeto_tipo="CustomUser",
            objeto_id=user.id,
            datos_extra={"email": email, "token_exp": str(expiracion)},
        )

        return respuesta_generica


# ─────────────────────────────────────────────────────────
# 9. Recuperación de contraseña — Paso 2 (confirmar)
# ─────────────────────────────────────────────────────────

class ConfirmarRecuperacionView(APIView):
    """
    POST /api/auth/recuperar-password/confirmar/

    Valida el token y establece la nueva contraseña.

    Body JSON:
      {
        "token":           "<token_del_email>",
        "password_nuevo":  "...",
        "password_nuevo2": "..."
      }

    Response 200: contraseña restablecida.
    Response 400: token inválido/expirado o contraseñas no coinciden.

    Tras el cambio exitoso el token queda invalidado
    (no puede reutilizarse).
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ConfirmarRecuperacionSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.save()

        _log(
            request,
            accion=AccionLog.CAMBIO_CONTRASENA,
            usuario=user,
            objeto_tipo="CustomUser",
            objeto_id=user.id,
            datos_extra={"via": "recuperacion"},
        )

        return Response(
            {"mensaje": "Contraseña restablecida correctamente. Ya puedes iniciar sesión."},
            status=status.HTTP_200_OK,
        )