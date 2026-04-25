import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-insecure-sivag-dev-key-cambiar-en-produccion'

DEBUG = True

ALLOWED_HOSTS = ['*']

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.gis',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',   # ← NUEVO: habilita CORS para el frontend
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',   # ← NUEVO: debe ir aquí, antes de CommonMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'sivag_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'sivag_backend.wsgi.application'

# ── Base de datos — PostgreSQL/PostGIS ──────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': 'SIVAG_db',
        'USER': 'postgres',
        'PASSWORD': 'GreenHat_MY77',
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': '5432',
        'OPTIONS': {
            'connect_timeout': 10,
        },
        'CONN_MAX_AGE': 60,
    }
}

# ── Modelo de usuario personalizado ─────────────────────────────────────────
AUTH_USER_MODEL = 'core.CustomUser'

# ── Validación de contraseñas ────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Internacionalización ─────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-mx'
TIME_ZONE     = 'America/Mexico_City'
USE_I18N      = True
USE_TZ        = True

# ── Archivos estáticos y media ───────────────────────────────────────────────
STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── PK por defecto ───────────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Django REST Framework + JWT ──────────────────────────────────────────────
from datetime import timedelta

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME':  timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS':  True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ── GDAL ─────────────────────────────────────────────────────────────────────
GDAL_LIBRARY_PATH = os.environ.get(
    'GDAL_LIBRARY_PATH',
    '/usr/lib/x86_64-linux-gnu/libgdal.so'
)

# ── Límites de subida de archivos ────────────────────────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 104_857_600   # 100 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 104_857_600   # 100 MB

# ── Email ─────────────────────────────────────────────────────────────────────
# DESARROLLO: imprime los emails en la consola de Docker (docker logs sivag_backend)
EMAIL_BACKEND   = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'no-reply@sivag.ccgs.mx'

# PRODUCCIÓN: cuando estés listo para desplegar en el servidor del CCGS,
# comenta las dos líneas de arriba y descomenta el bloque de abajo.
# Las credenciales se leen desde variables de entorno (nunca hardcodeadas aquí).
#
# EMAIL_BACKEND       = 'django.core.mail.backends.smtp.EmailBackend'
# EMAIL_HOST          = os.environ.get('EMAIL_HOST',          'smtp.gmail.com')
# EMAIL_PORT          = int(os.environ.get('EMAIL_PORT',      587))
# EMAIL_USE_TLS       = True
# EMAIL_HOST_USER     = os.environ.get('EMAIL_HOST_USER',     '')
# EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
#
# Guía rápida Gmail:
#   1. myaccount.google.com → Seguridad → Verificación en 2 pasos (activar)
#   2. myaccount.google.com → Seguridad → Contraseñas de aplicaciones
#   3. Crear contraseña para "Correo / Otro dispositivo" → copiar los 16 chars
#   4. Agregar al docker-compose.yml o entorno del servidor:
#      - EMAIL_HOST_USER=tu-correo@gmail.com
#      - EMAIL_HOST_PASSWORD=abcdefghijklmnop
#
# Alternativa SMTP institucional CCGS (preguntar a TI del centro):
#   EMAIL_HOST = 'mail.ccgs.mx'
#   EMAIL_PORT = 587
#   EMAIL_HOST_USER = 'no-reply@sivag.ccgs.mx'

# ── URL del frontend ──────────────────────────────────────────────────────────
# Se usa para construir el enlace de recuperación de contraseña en los emails.
# DESARROLLO: Live Server de VS Code en puerto 5500
# PRODUCCIÓN: cambiar por el dominio real → https://sivag.ccgs.mx
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://127.0.0.1:5500')

# ── CORS ──────────────────────────────────────────────────────────────────────
# Orígenes permitidos en DESARROLLO (Live Server de VS Code).
# PRODUCCIÓN: reemplazar con el dominio real → "https://sivag.ccgs.mx"
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5501",
    "http://127.0.0.1:5501",
    # Agregar aquí si usas otro puerto o herramienta en desarrollo:
    # "http://localhost:3000",
    # "http://localhost:8080",
]

# Permite enviar el header Authorization con el JWT desde el frontend.
CORS_ALLOW_CREDENTIALS = True

# Cabeceras que el frontend puede incluir en sus peticiones.
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

# Métodos HTTP permitidos desde el frontend.
CORS_ALLOW_METHODS = [
    'DELETE',
    'GET',
    'OPTIONS',
    'PATCH',
    'POST',
    'PUT',
]
