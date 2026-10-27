"""
SIVAG — core/urls_chat.py
==========================
URLs del agente IA SIVAG.

Prefijo: /api/chat/
  POST /api/chat/   — Enviar mensaje al agente y recibir respuesta
"""

from django.urls import path
from .views_chat import SivagChatView

app_name = 'chat'

urlpatterns = [
    path('', SivagChatView.as_view(), name='chat'),
]
