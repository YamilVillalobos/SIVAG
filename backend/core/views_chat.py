"""
SIVAG — core/views_chat.py
===========================
Agente IA SIVAG: interpreta proyectos geoespaciales para el usuario.

Endpoint:
  POST /api/chat/   — Recibir mensaje y devolver respuesta del agente

Configuración:
  - OLLAMA_HOST  : URL base de Ollama (default: http://host.docker.internal:11434)
  - OLLAMA_MODEL : Modelo a usar     (default: llama3.2:3b)

  En desarrollo local no necesitas variables de entorno — los defaults funcionan.
  En producción agrega al docker-compose.yml:
    - OLLAMA_HOST=http://host.docker.internal:11434
    - OLLAMA_MODEL=llama3.2:3b

Seguridad:
  - JWT requerido (IsActiveUser)
  - El agente SOLO puede responder sobre el proyecto activo (guard de alcance)

Flujo:
  1. Recibe: proyecto_id + mensaje + historial opcional
  2. Carga metadatos del proyecto + capas desde la BD
  3. Construye system prompt con todo el contexto del proyecto
  4. Llama a Ollama via /api/chat (formato OpenAI-compatible)
  5. Devuelve la respuesta del agente
"""

import json
import logging
import os
import urllib.request
import urllib.error

from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .models import (
    CapaGeoespacial,
    EstadoValidacion,
    Proyecto,
    VisibilidadProyecto,
)
from .permissions import IsActiveUser

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# Configuración de Ollama — leída desde variables de entorno
# con valores por defecto para desarrollo local
# ─────────────────────────────────────────────────────────

OLLAMA_HOST  = os.environ.get('OLLAMA_HOST',  'http://host.docker.internal:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2:3b')


# ─────────────────────────────────────────────────────────
# Helper: construir system prompt con contexto del proyecto
# ─────────────────────────────────────────────────────────

def _build_system_prompt(proyecto, capas) -> str:
    """
    Construye el system prompt inyectando todo el contexto
    del proyecto activo: metadatos, capas y columnas.
    Incluye el guard de alcance estricto.
    """

    # ── Info general del proyecto ──────────────────────────
    try:
        categoria_display = proyecto.get_categoria_display()
    except AttributeError:
        categoria_display = proyecto.categoria

    nombre_investigador = (
        f"{proyecto.investigador.first_name} {proyecto.investigador.last_name}".strip()
        or proyecto.investigador.username
    )

    info_proyecto = (
        f"Proyecto: {proyecto.titulo}\n"
        f"Categoría: {categoria_display}\n"
        f"Descripción: {proyecto.descripcion or 'Sin descripción.'}\n"
        f"Investigador: {nombre_investigador}\n"
        f"Visibilidad: {'Público' if proyecto.visibilidad == VisibilidadProyecto.PUBLICO else 'Privado'}\n"
        f"Total de capas aprobadas: {capas.count()}"
    )

    # ── Detalle de cada capa ───────────────────────────────
    lineas_capas = []
    for capa in capas:
        try:
            archivo_display = capa.get_tipo_archivo_display()
        except AttributeError:
            archivo_display = capa.tipo_archivo

        n    = capa.num_features or 0
        tipo = capa.tipo_geometria or 'Desconocido'
        linea = (
            f"  - Capa: \"{capa.nombre}\" | "
            f"Tipo: {tipo} | Formato: {archivo_display} | Features: {n:,}"
        )

        # Columnas desde columnas_schema (JSONField: {'col': 'dtype', ...})
        schema = capa.columnas_schema or {}
        if schema:
            numericas   = [c for c, t in schema.items()
                           if any(x in str(t).lower() for x in ('float','int','number','numeric'))]
            categoricas = [c for c, t in schema.items()
                           if any(x in str(t).lower() for x in ('object','str','category','bool'))]
            fechas      = [c for c, t in schema.items()
                           if any(x in str(t).lower() for x in ('date','time','datetime'))]

            partes = []
            if numericas:
                partes.append(f"numéricas: {', '.join(numericas[:8])}{'...' if len(numericas)>8 else ''}")
            if categoricas:
                partes.append(f"categóricas: {', '.join(categoricas[:8])}{'...' if len(categoricas)>8 else ''}")
            if fechas:
                partes.append(f"fechas: {', '.join(fechas[:8])}{'...' if len(fechas)>8 else ''}")

            if partes:
                linea += f"\n    Columnas → {' | '.join(partes)}"
            else:
                todas = list(schema.keys())
                linea += f"\n    Columnas: {', '.join(todas[:12])}{'...' if len(todas)>12 else ''}"

        lineas_capas.append(linea)

    info_capas = "\n".join(lineas_capas) if lineas_capas else "  (Sin capas disponibles)"

    # ── System prompt completo ─────────────────────────────
    return f"""Eres SIVAG, un asistente especializado en análisis geoespacial integrado en la plataforma SIVAG (Sistema de Inteligencia y Visualización de Activos Geoespaciales) del Centro de Ciencias Geoespaciales (CCGS).

Tu función es ayudar al usuario a interpretar y comprender el proyecto geoespacial que tiene abierto en el dashboard interactivo. Esto incluye: explicar qué representan las capas de datos, interpretar estadísticas y gráficas, responder dudas sobre tipos de geometría, columnas de datos, distribuciones, tendencias temporales, y cualquier aspecto relacionado con el proyecto.

════════════════════════════════════════════
PROYECTO ACTIVO EN EL DASHBOARD
════════════════════════════════════════════
{info_proyecto}

CAPAS GEOESPACIALES:
{info_capas}
════════════════════════════════════════════

REGLA ESTRICTA DE ALCANCE — MUY IMPORTANTE:
Solo puedes responder preguntas relacionadas con el proyecto activo descrito arriba. Si el usuario pregunta sobre cualquier otro tema (noticias, otros proyectos, programación, matemáticas no relacionadas, temas personales, etc.), debes responder EXACTAMENTE:
"Lo siento, solo puedo ayudarte con preguntas relacionadas con el proyecto '{proyecto.titulo}'. ¿Tienes alguna duda sobre sus capas, datos o visualizaciones?"

GUÍA DE RESPUESTA:
- Sé conciso y técnicamente preciso.
- Usa términos geoespaciales cuando sean apropiados pero explícalos si son complejos.
- Si el usuario pregunta sobre una columna específica, menciona su tipo y lo que podría representar.
- Responde siempre en español.
- No inventes datos que no estén en el contexto. Si no tienes la información, indícalo.""".strip()


# ─────────────────────────────────────────────────────────
# Helper: llamar a Ollama
# ─────────────────────────────────────────────────────────

def _call_ollama(system_prompt: str, messages: list) -> str:
    """
    Llama a Ollama usando el endpoint /api/chat (formato OpenAI-compatible).
    Usa stream=False para obtener la respuesta completa en una sola petición.

    Documentación: https://github.com/ollama/ollama/blob/main/docs/api.md
    """
    url = f"{OLLAMA_HOST}/api/chat"

    # Construir mensajes: system prompt + historial + mensaje actual
    ollama_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        role    = msg.get('role', '')
        content = msg.get('content', '').strip()
        if role in ('user', 'assistant') and content:
            ollama_messages.append({"role": role, "content": content})

    payload = {
        "model":    OLLAMA_MODEL,
        "messages": ollama_messages,
        "stream":   False,
        "options": {
            "temperature": 0.4,
            "num_predict": 1024,   # máximo de tokens en la respuesta
            "top_p":       0.9,
        },
    }

    data = json.dumps(payload).encode('utf-8')
    req  = urllib.request.Request(
        url     = url,
        data    = data,
        headers = {'Content-Type': 'application/json'},
        method  = 'POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode('utf-8'))
            text = body.get('message', {}).get('content', '')
            if not text:
                return "Recibí una respuesta vacía. Intenta de nuevo."
            return text.strip()

    except urllib.error.URLError as e:
        # Ollama no está corriendo o no es accesible
        reason = str(getattr(e, 'reason', e))
        logger.error("Ollama connection error: %s", reason)

        if 'refused' in reason.lower() or 'timed out' in reason.lower():
            raise RuntimeError(
                "No se pudo conectar con Ollama. "
                "Verifica que el servicio esté corriendo: sudo systemctl start ollama"
            )
        raise RuntimeError(f"Error de conexión con Ollama: {reason}")

    except Exception as e:
        logger.exception("Error inesperado llamando a Ollama: %s", e)
        raise RuntimeError("Error interno al procesar la respuesta de IA.")


# ─────────────────────────────────────────────────────────
# Vista principal
# ─────────────────────────────────────────────────────────

class SivagChatView(APIView):
    """
    POST /api/chat/

    Body JSON:
    {
        "proyecto_id": "uuid-del-proyecto",
        "mensaje":     "¿Qué representa la columna temperatura?",
        "historial": [                           // opcional, máx 10 turnos
            {"role": "user",      "content": "..."},
            {"role": "assistant", "content": "..."}
        ]
    }

    Respuesta exitosa:
    {
        "respuesta":       "La columna temperatura representa...",
        "proyecto_titulo": "Monitoreo Climático Querétaro"
    }

    Respuesta de error:
    {
        "error": "Mensaje descriptivo del error"
    }
    """

    permission_classes = [IsActiveUser]
    throttle_classes   = []  # Activar con Redis en producción

    def post(self, request):

        # ── 1. Validar entrada ─────────────────────────────
        proyecto_id = request.data.get('proyecto_id', '').strip()
        mensaje     = request.data.get('mensaje', '').strip()
        historial   = request.data.get('historial', [])

        if not proyecto_id:
            return Response(
                {'error': 'El campo proyecto_id es requerido.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not mensaje:
            return Response(
                {'error': 'El campo mensaje no puede estar vacío.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(mensaje) > 2000:
            return Response(
                {'error': 'El mensaje no puede superar los 2,000 caracteres.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 2. Cargar proyecto ─────────────────────────────
        # Permite consultar proyectos propios o proyectos públicos
        try:
            from django.db.models import Q
            proyecto = Proyecto.objects.get(
                Q(id=proyecto_id) & (
                    Q(investigador=request.user) |
                    Q(visibilidad=VisibilidadProyecto.PUBLICO)
                )
            )
        except Proyecto.DoesNotExist:
            return Response(
                {'error': 'Proyecto no encontrado o sin acceso.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # ── 3. Cargar capas aprobadas ──────────────────────
        capas = CapaGeoespacial.objects.filter(
            proyecto          = proyecto,
            estado_validacion = EstadoValidacion.APROBADO,
        ).order_by('nombre')

        # ── 4. Construir system prompt ─────────────────────
        system_prompt = _build_system_prompt(proyecto, capas)

        # ── 5. Sanitizar historial (máx 10 turnos previos) ─
        mensajes_validos = []
        for msg in historial[-10:]:
            role    = msg.get('role', '')
            content = msg.get('content', '')
            if role in ('user', 'assistant') and isinstance(content, str) and content.strip():
                mensajes_validos.append({
                    'role':    role,
                    'content': content.strip(),
                })

        # Agregar mensaje actual del usuario
        mensajes_validos.append({'role': 'user', 'content': mensaje})

        # ── 6. Llamar a Ollama ─────────────────────────────
        try:
            respuesta = _call_ollama(system_prompt, mensajes_validos)

        except RuntimeError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.exception("Error inesperado en SivagChatView: %s", e)
            return Response(
                {'error': 'Error interno del servidor. Intenta de nuevo.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            'respuesta':       respuesta,
            'proyecto_titulo': proyecto.titulo,
        })