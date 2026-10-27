/**
 * SIVAG — js/sivag_chat.js  (v2 — rediseño UI)
 * =============================================
 * Agente IA SIVAG: burbuja flotante + panel de chat rediseñado.
 *
 * Cambios v2:
 *  - Panel más grande (420px ancho, 580px alto)
 *  - Texto legible (15px base, 14px mensajes)
 *  - Markdown renderizado (negrita, listas, párrafos sin asteriscos)
 *  - Avatar del bot en cada mensaje
 *  - Animación de entrada suave
 *  - Botón enviar con ícono moderno
 */

import { API } from './api.js';

// ─────────────────────────────────────────────────────────────────────────────
// Constantes
// ─────────────────────────────────────────────────────────────────────────────

const STORAGE_KEY_PREFIX = 'sivag_chat_';
const MAX_HISTORY        = 20;
const GREETING = `¡Hola! Soy **SIVAG**, tu asistente de análisis geoespacial 🌍

Analicé el proyecto activo y estoy listo para ayudarte a interpretar el mapa, las capas, las gráficas y los datos.

¿En qué puedo ayudarte?`;

// ─────────────────────────────────────────────────────────────────────────────
// Renderizador de Markdown simple (sin dependencias externas)
// Convierte: **negrita**, *cursiva*, listas con * o -, saltos de línea
// ─────────────────────────────────────────────────────────────────────────────

function renderMarkdown(text) {
  if (!text) return '';

  // Escapar HTML primero para seguridad
  let html = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Bloques de código ```...```
  html = html.replace(/```([\s\S]*?)```/g, '<pre class="sc-code">$1</pre>');

  // Código inline `...`
  html = html.replace(/`([^`]+)`/g, '<code class="sc-inline-code">$1</code>');

  // Negritas **texto** o __texto__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/__(.+?)__/g, '<strong>$1</strong>');

  // Cursivas *texto* o _texto_ (que no sean parte de listas)
  html = html.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');

  // Listas con * o - al inicio de línea
  html = html.replace(/^[\*\-] (.+)$/gm, '<li>$1</li>');
  // Agrupar <li> consecutivos en <ul>
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul class="sc-list">${match}</ul>`);

  // Listas numeradas 1. 2. etc
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

  // Saltos de línea dobles → párrafos
  html = html.replace(/\n\n+/g, '</p><p class="sc-p">');
  html = '<p class="sc-p">' + html + '</p>';

  // Saltos de línea simples → <br>
  html = html.replace(/\n/g, '<br>');

  // Limpiar párrafos vacíos
  html = html.replace(/<p class="sc-p"><\/p>/g, '');
  html = html.replace(/<p class="sc-p"><br><\/p>/g, '');

  return html;
}

// ─────────────────────────────────────────────────────────────────────────────
// CSS — inyectado dinámicamente
// ─────────────────────────────────────────────────────────────────────────────

function _injectStyles() {
  if (document.getElementById('sivag-chat-styles')) return;
  const style = document.createElement('style');
  style.id = 'sivag-chat-styles';
  style.textContent = `
    /* ── Variables locales del chat ── */
    #sivag-chat-panel, #sivag-chat-bubble {
      --sc-bg:          #060d1a;
      --sc-bg-card:     #0b1729;
      --sc-bg-msg-bot:  #0f2040;
      --sc-bg-msg-user: rgba(0, 119, 204, 0.3);
      --sc-accent:      #00d4ff;
      --sc-accent2:     #00b8a0;
      --sc-green:       #00e5aa;
      --sc-border:      rgba(0, 212, 255, 0.15);
      --sc-border-strong: rgba(0, 212, 255, 0.35);
      --sc-text:        #e8f4ff;
      --sc-text-muted:  #7a9db8;
      --sc-font:        'Exo 2', 'Rajdhani', sans-serif;
      --sc-font-mono:   'Space Mono', monospace;
      --sc-radius:      14px;
      --sc-shadow:      0 16px 48px rgba(0,0,0,0.7), 0 0 0 1px rgba(0,212,255,0.1);
    }

    /* ── Burbuja flotante ── */
    #sivag-chat-bubble {
      position: fixed;
      bottom: 28px;
      right: 28px;
      width: 58px;
      height: 58px;
      border-radius: 50%;
      background: linear-gradient(135deg, #00d4ff 0%, #0077cc 60%, #005fa3 100%);
      border: none;
      cursor: pointer;
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 24px rgba(0,212,255,0.5), 0 0 0 0 rgba(0,212,255,0.4);
      transition: transform 0.2s ease, box-shadow 0.2s ease;
      font-size: 24px;
      animation: sc-pulse 3s infinite;
    }
    @keyframes sc-pulse {
      0%   { box-shadow: 0 4px 24px rgba(0,212,255,0.5), 0 0 0 0 rgba(0,212,255,0.4); }
      70%  { box-shadow: 0 4px 24px rgba(0,212,255,0.5), 0 0 0 12px rgba(0,212,255,0); }
      100% { box-shadow: 0 4px 24px rgba(0,212,255,0.5), 0 0 0 0 rgba(0,212,255,0); }
    }
    #sivag-chat-bubble:hover {
      transform: scale(1.08);
      box-shadow: 0 6px 32px rgba(0,212,255,0.65);
      animation: none;
    }
    #sivag-chat-bubble.open {
      background: linear-gradient(135deg, #005fa3, #003d70);
      animation: none;
    }
    /* Indicador online */
    #sivag-chat-bubble::after {
      content: '';
      position: absolute;
      bottom: 5px;
      right: 5px;
      width: 11px;
      height: 11px;
      border-radius: 50%;
      background: #00e5aa;
      border: 2px solid #060d1a;
      box-shadow: 0 0 6px rgba(0,229,170,0.8);
    }

    /* ── Panel principal ── */
    #sivag-chat-panel {
      position: fixed;
      bottom: 100px;
      right: 28px;
      width: 420px;
      height: 580px;
      background: var(--sc-bg);
      border: 1px solid var(--sc-border-strong);
      border-radius: var(--sc-radius);
      display: flex;
      flex-direction: column;
      z-index: 9998;
      box-shadow: var(--sc-shadow);
      overflow: hidden;
      transform: scale(0.9) translateY(20px);
      opacity: 0;
      pointer-events: none;
      transition: transform 0.25s cubic-bezier(0.34,1.56,0.64,1),
                  opacity 0.2s ease;
    }
    #sivag-chat-panel.visible {
      transform: scale(1) translateY(0);
      opacity: 1;
      pointer-events: all;
    }

    /* ── Header ── */
    #sivag-chat-header {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      background: linear-gradient(135deg, #0b1729 0%, #0f2040 100%);
      border-bottom: 1px solid var(--sc-border);
      flex-shrink: 0;
    }
    .sc-avatar {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: linear-gradient(135deg, #00d4ff, #0077cc);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      flex-shrink: 0;
      box-shadow: 0 0 12px rgba(0,212,255,0.4);
    }
    .sc-header-info { flex: 1; min-width: 0; }
    .sc-header-name {
      font-family: var(--sc-font);
      font-size: 15px;
      font-weight: 700;
      color: var(--sc-text);
      letter-spacing: 0.3px;
    }
    .sc-header-status {
      font-family: var(--sc-font-mono);
      font-size: 10px;
      color: var(--sc-green);
      letter-spacing: 1.5px;
      text-transform: uppercase;
      margin-top: 2px;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .sc-header-status::before {
      content: '';
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--sc-green);
      box-shadow: 0 0 6px var(--sc-green);
      flex-shrink: 0;
    }
    .sc-header-actions {
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .sc-header-btn {
      background: none;
      border: none;
      color: var(--sc-text-muted);
      width: 30px;
      height: 30px;
      border-radius: 8px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 13px;
      transition: all 0.15s;
    }
    .sc-header-btn:hover {
      color: var(--sc-text);
      background: rgba(255,255,255,0.08);
    }
    .sc-header-btn.danger:hover {
      color: #ff6b8a;
      background: rgba(255,68,102,0.1);
    }

    /* ── Badge del proyecto ── */
    #sivag-chat-project-badge {
      padding: 7px 16px;
      background: rgba(0,212,255,0.05);
      border-bottom: 1px solid var(--sc-border);
      font-family: var(--sc-font-mono);
      font-size: 10px;
      color: var(--sc-accent);
      letter-spacing: 0.5px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      flex-shrink: 0;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    #sivag-chat-project-badge::before {
      content: '◈';
      color: var(--sc-accent2);
      flex-shrink: 0;
    }

    /* ── Área de mensajes ── */
    #sivag-chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px 14px 8px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      scrollbar-width: thin;
      scrollbar-color: rgba(0,212,255,0.2) transparent;
    }
    #sivag-chat-messages::-webkit-scrollbar { width: 4px; }
    #sivag-chat-messages::-webkit-scrollbar-thumb {
      background: rgba(0,212,255,0.25);
      border-radius: 2px;
    }

    /* ── Grupos de mensajes ── */
    .sc-msg-group {
      display: flex;
      gap: 10px;
      align-items: flex-start;
    }
    .sc-msg-group.user {
      flex-direction: row-reverse;
    }
    .sc-msg-group-avatar {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
      flex-shrink: 0;
      margin-top: 2px;
    }
    .sc-msg-group.bot .sc-msg-group-avatar {
      background: linear-gradient(135deg, #00d4ff, #0077cc);
      box-shadow: 0 0 8px rgba(0,212,255,0.3);
    }
    .sc-msg-group.user .sc-msg-group-avatar {
      background: linear-gradient(135deg, #1a6fff, #0050cc);
    }
    .sc-msg-group-content {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .sc-msg-group.user .sc-msg-group-content {
      align-items: flex-end;
    }
    .sc-msg-label {
      font-family: var(--sc-font-mono);
      font-size: 9px;
      color: var(--sc-text-muted);
      letter-spacing: 1px;
      text-transform: uppercase;
      padding: 0 4px;
    }

    /* ── Burbuja de mensaje ── */
    .sc-bubble {
      max-width: 100%;
      padding: 10px 14px;
      border-radius: 12px;
      font-family: var(--sc-font);
      font-size: 14px;
      line-height: 1.6;
      word-break: break-word;
    }
    .sc-msg-group.bot .sc-bubble {
      background: var(--sc-bg-msg-bot);
      border: 1px solid var(--sc-border);
      color: var(--sc-text);
      border-top-left-radius: 4px;
    }
    .sc-msg-group.user .sc-bubble {
      background: var(--sc-bg-msg-user);
      border: 1px solid rgba(0,119,204,0.3);
      color: var(--sc-text);
      border-top-right-radius: 4px;
    }
    .sc-msg-group.bot .sc-bubble.error {
      background: rgba(255,68,102,0.08);
      border-color: rgba(255,68,102,0.25);
      color: #ff9aac;
    }

    /* ── Estilos del markdown renderizado ── */
    .sc-bubble .sc-p {
      margin: 0 0 8px 0;
      font-size: 14px;
      line-height: 1.65;
    }
    .sc-bubble .sc-p:last-child { margin-bottom: 0; }
    .sc-bubble strong {
      color: var(--sc-accent);
      font-weight: 700;
    }
    .sc-bubble em {
      color: #b0d8f0;
      font-style: italic;
    }
    .sc-bubble .sc-list {
      margin: 6px 0;
      padding-left: 18px;
      list-style: none;
    }
    .sc-bubble .sc-list li {
      position: relative;
      padding-left: 14px;
      margin-bottom: 5px;
      font-size: 14px;
      line-height: 1.55;
      color: var(--sc-text);
    }
    .sc-bubble .sc-list li::before {
      content: '▸';
      position: absolute;
      left: -2px;
      color: var(--sc-accent2);
      font-size: 10px;
      top: 3px;
    }
    .sc-bubble .sc-code {
      background: rgba(0,0,0,0.4);
      border: 1px solid var(--sc-border);
      border-radius: 6px;
      padding: 8px 12px;
      font-family: var(--sc-font-mono);
      font-size: 12px;
      white-space: pre-wrap;
      margin: 6px 0;
      color: #7ee8ff;
      overflow-x: auto;
    }
    .sc-bubble .sc-inline-code {
      background: rgba(0,212,255,0.1);
      border: 1px solid rgba(0,212,255,0.2);
      border-radius: 4px;
      padding: 1px 6px;
      font-family: var(--sc-font-mono);
      font-size: 12px;
      color: var(--sc-accent);
    }

    /* ── Indicador "escribiendo" ── */
    .sc-typing {
      display: flex;
      gap: 5px;
      align-items: center;
      padding: 12px 14px;
      background: var(--sc-bg-msg-bot);
      border: 1px solid var(--sc-border);
      border-radius: 12px;
      border-top-left-radius: 4px;
      width: fit-content;
    }
    .sc-typing span {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--sc-accent);
      animation: sc-bounce 1.3s ease-in-out infinite;
      opacity: 0.5;
    }
    .sc-typing span:nth-child(2) { animation-delay: 0.18s; }
    .sc-typing span:nth-child(3) { animation-delay: 0.36s; }
    @keyframes sc-bounce {
      0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
      40%           { transform: translateY(-6px); opacity: 1; }
    }

    /* ── Separador de fecha ── */
    .sc-date-sep {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 0;
    }
    .sc-date-sep::before,
    .sc-date-sep::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--sc-border);
    }
    .sc-date-sep span {
      font-family: var(--sc-font-mono);
      font-size: 9px;
      color: var(--sc-text-muted);
      letter-spacing: 1px;
      text-transform: uppercase;
      white-space: nowrap;
    }

    /* ── Footer / Input ── */
    #sivag-chat-footer {
      padding: 10px 12px 14px;
      border-top: 1px solid var(--sc-border);
      background: rgba(0,0,0,0.2);
      flex-shrink: 0;
    }
    .sc-input-row {
      display: flex;
      gap: 8px;
      align-items: flex-end;
      background: #0a1830;
      border: 1px solid var(--sc-border);
      border-radius: 12px;
      padding: 8px 8px 8px 14px;
      transition: border-color 0.15s;
    }
    .sc-input-row:focus-within {
      border-color: var(--sc-accent);
      box-shadow: 0 0 0 2px rgba(0,212,255,0.08);
    }
    #sivag-chat-input {
      flex: 1;
      background: none;
      border: none;
      color: var(--sc-text);
      font-family: var(--sc-font);
      font-size: 14px;
      resize: none;
      outline: none;
      max-height: 100px;
      min-height: 22px;
      line-height: 1.5;
    }
    #sivag-chat-input::placeholder { color: #3a6070; }
    #sivag-chat-send {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      border: none;
      background: linear-gradient(135deg, #00d4ff, #0077cc);
      color: #fff;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: opacity 0.15s, transform 0.15s;
      box-shadow: 0 2px 10px rgba(0,212,255,0.35);
    }
    #sivag-chat-send svg {
      width: 16px;
      height: 16px;
      fill: none;
      stroke: white;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    #sivag-chat-send:disabled { opacity: 0.35; cursor: not-allowed; transform: none; }
    #sivag-chat-send:not(:disabled):hover { transform: scale(1.08); }
    .sc-input-hint {
      font-family: var(--sc-font-mono);
      font-size: 9px;
      color: var(--sc-text-muted);
      text-align: right;
      margin-top: 6px;
      letter-spacing: 0.3px;
    }

    /* ── Responsive ── */
    @media (max-width: 500px) {
      #sivag-chat-panel {
        right: 0; left: 0;
        bottom: 0;
        width: 100%;
        height: 70vh;
        border-radius: 20px 20px 0 0;
      }
      #sivag-chat-bubble { right: 16px; bottom: 16px; }
    }
  `;
  document.head.appendChild(style);
}

// ─────────────────────────────────────────────────────────────────────────────
// HTML del panel
// ─────────────────────────────────────────────────────────────────────────────

function _buildHTML() {
  return `
    <button id="sivag-chat-bubble" aria-label="Abrir asistente SIVAG">🌍</button>

    <div id="sivag-chat-panel" role="dialog" aria-label="Asistente IA SIVAG" aria-modal="true">

      <!-- Header -->
      <div id="sivag-chat-header">
        <div class="sc-avatar">🤖</div>
        <div class="sc-header-info">
          <div class="sc-header-name">SIVAG · Asistente IA</div>
          <div class="sc-header-status">En línea</div>
        </div>
        <div class="sc-header-actions">
          <button class="sc-header-btn danger" id="sivag-chat-clear" title="Limpiar conversación">
            🗑
          </button>
          <button class="sc-header-btn" id="sivag-chat-close" aria-label="Cerrar">✕</button>
        </div>
      </div>

      <!-- Badge proyecto -->
      <div id="sivag-chat-project-badge">Cargando proyecto...</div>

      <!-- Mensajes -->
      <div id="sivag-chat-messages" aria-live="polite"></div>

      <!-- Input -->
      <div id="sivag-chat-footer">
        <div class="sc-input-row">
          <textarea
            id="sivag-chat-input"
            placeholder="Pregunta sobre este proyecto..."
            rows="1"
            maxlength="2000"
            aria-label="Escribe tu pregunta"
          ></textarea>
          <button id="sivag-chat-send" aria-label="Enviar" title="Enviar (Enter)">
            <svg viewBox="0 0 24 24">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
        <div class="sc-input-hint">Enter para enviar · Shift+Enter nueva línea</div>
      </div>

    </div>
  `;
}

// ─────────────────────────────────────────────────────────────────────────────
// Módulo principal SivagChat
// ─────────────────────────────────────────────────────────────────────────────

export const SivagChat = {

  _state:      null,
  _proyectoId: null,
  _isOpen:     false,
  _isSending:  false,
  _historial:  [],

  // ── Init ─────────────────────────────────────────────────────────────────
  init(dashboardState) {
    this._state = dashboardState;
    _injectStyles();

    const container = document.createElement('div');
    container.innerHTML = _buildHTML();
    document.body.appendChild(container);

    this._bindEvents();
    this._waitForProject();
  },

  // ── Esperar proyecto ──────────────────────────────────────────────────────
  _waitForProject() {
    let intentos = 0;
    const check = setInterval(() => {
      intentos++;
      const proyecto = this._state?.proyecto;
      if (proyecto?.id || intentos > 40) {
        clearInterval(check);
        if (proyecto?.id) {
          this._proyectoId = proyecto.id;
          this._updateProjectBadge(proyecto);
          this._loadHistorial();
        }
      }
    }, 250);
  },

  // ── Badge ─────────────────────────────────────────────────────────────────
  _updateProjectBadge(proyecto) {
    const badge = document.getElementById('sivag-chat-project-badge');
    if (!badge) return;
    const capas = this._state?.capas?.length || 0;
    badge.textContent = `${proyecto.titulo || 'Proyecto'} · ${capas} capa(s)`;
  },

  // ── Persistencia ─────────────────────────────────────────────────────────
  _storageKey() {
    return `${STORAGE_KEY_PREFIX}${this._proyectoId || 'unknown'}`;
  },

  _loadHistorial() {
    try {
      const raw = localStorage.getItem(this._storageKey());
      if (raw) {
        this._historial = JSON.parse(raw).slice(-MAX_HISTORY);
        // Separador de sesión anterior
        this._appendDateSep('Conversación anterior');
        this._historial.forEach(msg => {
          this._appendMessage(msg.role === 'user' ? 'user' : 'bot', msg.content);
        });
        this._appendDateSep('Ahora');
      } else {
        this._appendMessage('bot', GREETING);
      }
    } catch {
      this._historial = [];
      this._appendMessage('bot', GREETING);
    }
  },

  _saveHistorial() {
    try {
      localStorage.setItem(
        this._storageKey(),
        JSON.stringify(this._historial.slice(-MAX_HISTORY))
      );
    } catch {}
  },

  _clearHistorial() {
    this._historial = [];
    try { localStorage.removeItem(this._storageKey()); } catch {}
    const container = document.getElementById('sivag-chat-messages');
    if (container) container.innerHTML = '';
    this._appendMessage('bot', GREETING);
  },

  // ── Toggle panel ──────────────────────────────────────────────────────────
  _toggle() {
    this._isOpen = !this._isOpen;
    document.getElementById('sivag-chat-panel')?.classList.toggle('visible', this._isOpen);
    document.getElementById('sivag-chat-bubble')?.classList.toggle('open', this._isOpen);

    if (this._isOpen) {
      this._scrollToBottom();
      setTimeout(() => document.getElementById('sivag-chat-input')?.focus(), 250);
    }
  },

  // ── Separador de fecha ────────────────────────────────────────────────────
  _appendDateSep(label) {
    const container = document.getElementById('sivag-chat-messages');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'sc-date-sep';
    div.innerHTML = `<span>${label}</span>`;
    container.appendChild(div);
  },

  // ── Renderizar mensaje ────────────────────────────────────────────────────
  _appendMessage(role, text, isError = false) {
    const container = document.getElementById('sivag-chat-messages');
    if (!container) return;

    const isBot  = role === 'bot' || role === 'assistant';
    const group  = document.createElement('div');
    group.className = `sc-msg-group ${isBot ? 'bot' : 'user'}`;

    const avatarEmoji = isBot ? '🤖' : '👤';
    const labelText   = isBot ? 'SIVAG IA' : 'Tú';

    const bubble = document.createElement('div');
    bubble.className = `sc-bubble${isError ? ' error' : ''}`;

    if (isBot) {
      // Renderizar markdown solo en mensajes del bot
      bubble.innerHTML = renderMarkdown(text);
    } else {
      // Mensajes del usuario: solo escapar HTML
      bubble.textContent = text;
    }

    group.innerHTML = `
      <div class="sc-msg-group-avatar">${avatarEmoji}</div>
      <div class="sc-msg-group-content">
        <div class="sc-msg-label">${labelText}</div>
      </div>
    `;
    group.querySelector('.sc-msg-group-content').appendChild(bubble);

    container.appendChild(group);
    this._scrollToBottom();
    return group;
  },

  // ── Indicador de escritura ────────────────────────────────────────────────
  _showTyping() {
    const container = document.getElementById('sivag-chat-messages');
    if (!container) return null;

    const group = document.createElement('div');
    group.className = 'sc-msg-group bot';
    group.id = 'sivag-typing-group';
    group.innerHTML = `
      <div class="sc-msg-group-avatar">🤖</div>
      <div class="sc-msg-group-content">
        <div class="sc-msg-label">SIVAG IA</div>
        <div class="sc-typing">
          <span></span><span></span><span></span>
        </div>
      </div>
    `;
    container.appendChild(group);
    this._scrollToBottom();
    return group;
  },

  _removeTyping() {
    document.getElementById('sivag-typing-group')?.remove();
  },

  _scrollToBottom() {
    const container = document.getElementById('sivag-chat-messages');
    if (container) container.scrollTop = container.scrollHeight;
  },

  // ── Contexto visual del mapa ──────────────────────────────────────────────
  _buildContextNote() {
    try {
      const capas   = this._state?.capas || [];
      const mapa    = this._state?.map;
      const zoom    = mapa?.getZoom?.() ?? null;
      const bounds  = mapa?.getBounds?.();
      const bbox    = bounds ? {
        norte: bounds.getNorth().toFixed(4),
        sur:   bounds.getSouth().toFixed(4),
        este:  bounds.getEast().toFixed(4),
        oeste: bounds.getWest().toFixed(4),
      } : null;

      const selectedId = this._state?.selectedLayer;
      const capaSel    = capas.find(c => c.id === selectedId);

      const partes = [];
      if (zoom !== null)  partes.push(`Zoom actual: ${zoom}`);
      if (bbox)           partes.push(`Área visible: N${bbox.norte} S${bbox.sur} E${bbox.este} O${bbox.oeste}`);
      if (capaSel)        partes.push(`Capa activa: "${capaSel.nombre}" (${capaSel.num_features?.toLocaleString() || '?'} features)`);

      const capasVis = capas.filter(c => c.visible && c.leafletLayer);
      if (capasVis.length) {
        partes.push(`Capas visibles: ${capasVis.map(c => `"${c.nombre}"`).join(', ')}`);
      }

      return partes.length ? `\n\n[Contexto del mapa: ${partes.join(' | ')}]` : '';
    } catch {
      return '';
    }
  },

  // ── Enviar mensaje ────────────────────────────────────────────────────────
  async _send() {
    if (this._isSending) return;

    const input   = document.getElementById('sivag-chat-input');
    const sendBtn = document.getElementById('sivag-chat-send');
    const texto   = input?.value?.trim();

    if (!texto) return;
    if (!this._proyectoId) {
      this._appendMessage('bot', 'El proyecto aún no está cargado. Espera un momento.', true);
      return;
    }

    this._appendMessage('user', texto);
    input.value = '';
    input.style.height = 'auto';

    this._historial.push({ role: 'user', content: texto });

    this._isSending = true;
    if (sendBtn) sendBtn.disabled = true;
    this._showTyping();

    try {
      const contextoMapa    = this._buildContextNote();
      const mensajeCompleto = texto + contextoMapa;
      const historialPrevio = this._historial.slice(0, -1);

      const data = await API.post('/api/chat/', {
        proyecto_id: this._proyectoId,
        mensaje:     mensajeCompleto,
        historial:   historialPrevio,
      });

      this._removeTyping();
      const respuesta = data.respuesta || 'Sin respuesta del servidor.';
      this._appendMessage('bot', respuesta);
      this._historial.push({ role: 'assistant', content: respuesta });
      this._saveHistorial();

    } catch (err) {
      this._removeTyping();
      const msg = err?.message || 'Error al conectar con el asistente.';
      this._appendMessage('bot', `⚠ ${msg}`, true);
      this._historial.pop();
    } finally {
      this._isSending = false;
      if (sendBtn) sendBtn.disabled = false;
      input?.focus();
    }
  },

  // ── Eventos ───────────────────────────────────────────────────────────────
  _bindEvents() {
    document.getElementById('sivag-chat-bubble')
      ?.addEventListener('click', () => this._toggle());

    document.getElementById('sivag-chat-close')
      ?.addEventListener('click', () => this._toggle());

    document.getElementById('sivag-chat-send')
      ?.addEventListener('click', () => this._send());

    document.getElementById('sivag-chat-input')
      ?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault();
          this._send();
        }
      });

    document.getElementById('sivag-chat-input')
      ?.addEventListener('input', (e) => {
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 100) + 'px';
      });

    document.getElementById('sivag-chat-clear')
      ?.addEventListener('click', () => {
        if (confirm('¿Borrar el historial de esta conversación?')) {
          this._clearHistorial();
        }
      });
  },
};