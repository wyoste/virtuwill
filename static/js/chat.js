/**
 * chat.js — VirtuWill "Chat with Will" module
 *
 * Floating FAB + expandable chat panel.
 * In production, connects to the local LLaVA FastAPI server
 * (will_chat/server/chat_server.py running on :8765).
 * Falls back to persona-grounded demo responses if server unavailable.
 *
 * Exposes: window.VW.Chat
 */

'use strict';

window.VW = window.VW || {};

window.VW.Chat = (() => {

  // ── Config ─────────────────────────────────────────────────────────────────
  const SERVER_URL    = 'http://localhost:8765';
  const USE_IFRAME    = false;   // set true to embed the full chat UI in an iframe
  const IFRAME_EMBED  = `${SERVER_URL}/?embed=1`;

  // ── Persona fallback responses (used when server is offline) ───────────────
  const PERSONA = [
    "That's something I've been thinking about a lot — especially in the context of the Greystar data platform. The answer really depends on your grain and your consumers.",
    "From a data architecture perspective, the medallion pattern has been the right call for us. Bronze for raw fidelity, silver for conformance, gold for consumption-ready entities.",
    "Music has always been how I process things that are hard to put into words. Quiet Hours came out of a particularly foggy January in 2024.",
    "The garden is coming along. I'm going tomatoes north-facing this year based on the sun study I did last season. Lavender along the south fence.",
    "I built VirtuWill mostly in evenings — the Flask backend, the JS modules, the garden canvas, all of it. It's satisfying to own your own corner of the internet.",
    "On the data mesh question: compelling at scale, but it adds coordination overhead that's not always worth it for smaller orgs. Governance over architecture, every time.",
    "The most underrated skill in data is knowing when NOT to build something. I've seen more value destroyed by over-engineering than under-engineering.",
    "Databricks and Snowflake aren't really competing for the same workloads if you architect correctly. Lakehouse for compute-heavy ML, Snowflake for governed SQL consumption.",
  ];

  // ── State ──────────────────────────────────────────────────────────────────
  let _open        = false;
  let _responding  = false;
  let _threadId    = _generateId();
  let _msgCount    = 0;
  let _serverAlive = false;

  function _generateId() {
    return 'thread-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
  }

  // ── DOM references (populated on init) ────────────────────────────────────
  let _fab, _panel, _messages, _input, _sendBtn, _badge;
  let _iconChat, _iconClose;

  function init() {
    _fab      = document.getElementById('chatFab');
    _panel    = document.getElementById('chatPanel');
    _messages = document.getElementById('chatMessages');
    _input    = document.getElementById('chatInput');
    _sendBtn  = document.getElementById('chatSend');
    _badge    = document.getElementById('chatBadge');
    _iconChat = document.getElementById('chatIconChat');
    _iconClose= document.getElementById('chatIconClose');

    if (!_fab) return;

    // Probe the server non-intrusively
    _probeServer();
  }

  async function _probeServer() {
    try {
      const res = await fetch(`${SERVER_URL}/health`, { signal: AbortSignal.timeout(1500) });
      _serverAlive = res.ok;
    } catch {
      _serverAlive = false;
    }
  }

  // ── Toggle open / close ────────────────────────────────────────────────────
  function toggle() {
    _open = !_open;

    _panel.classList.toggle('hidden',  !_open);
    _panel.classList.toggle('visible',  _open);
    _fab.classList.toggle('open', _open);

    if (_iconChat)  _iconChat.style.display  = _open ? 'none'  : 'block';
    if (_iconClose) _iconClose.style.display = _open ? 'block' : 'none';

    // Dismiss badge
    if (_badge) _badge.style.opacity = '0';

    if (_open) {
      setTimeout(() => _input?.focus(), 180);
    }
  }

  function close() {
    if (_open) toggle();
  }

  // ── Send message ───────────────────────────────────────────────────────────
  async function sendMessage() {
    if (_responding) return;
    const text = (_input?.value || '').trim();
    if (!text) return;

    _input.value = '';
    _appendMessage('user', text);
    _responding = true;
    if (_sendBtn) _sendBtn.disabled = true;

    // Typing indicator
    const typingEl = _showTyping();

    try {
      let reply;
      if (_serverAlive) {
        reply = await _fetchFromServer(text);
      } else {
        // Simulated delay matching real model response feel
        await _sleep(800 + Math.random() * 700);
        reply = PERSONA[_msgCount % PERSONA.length];
      }

      typingEl.remove();
      _appendMessage('will', reply);
      _msgCount++;
    } catch (e) {
      typingEl.remove();
      _appendMessage('will', "Sorry, I'm having trouble connecting right now. Try again in a moment.");
    } finally {
      _responding = false;
      if (_sendBtn) _sendBtn.disabled = false;
      _input?.focus();
    }
  }

  async function _fetchFromServer(text) {
    const res = await fetch(`${SERVER_URL}/chat`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, thread_id: _threadId }),
      signal:  AbortSignal.timeout(30000),
    });
    if (!res.ok) throw new Error('Server error');
    const data = await res.json();
    return data.response || data.message || 'I received your message.';
  }

  // ── DOM helpers ────────────────────────────────────────────────────────────
  function _appendMessage(role, text) {
    if (!_messages) return;

    const now   = new Date();
    const time  = now.getHours() + ':' + String(now.getMinutes()).padStart(2, '0');
    const isUser = role === 'user';

    const div   = document.createElement('div');
    div.className = 'chat-msg ' + role;
    div.innerHTML = `
      <div class="chat-msg-av ${isUser ? 'user-av' : ''}">${isUser ? 'You' : 'W'}</div>
      <div>
        <div class="chat-bubble">${_escHtml(text)}</div>
        <div class="chat-msg-time">${time}</div>
      </div>`;
    _messages.appendChild(div);
    _scrollToBottom();
  }

  function _showTyping() {
    const wrap = document.createElement('div');
    wrap.className = 'chat-msg will';
    wrap.id        = 'chat-typing';
    wrap.innerHTML = `
      <div class="chat-msg-av">W</div>
      <div class="chat-typing"><span></span><span></span><span></span></div>`;
    _messages.appendChild(wrap);
    _scrollToBottom();
    return wrap;
  }

  function _scrollToBottom() {
    if (_messages) _messages.scrollTop = _messages.scrollHeight;
  }

  function _escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  return { init, toggle, close, sendMessage };

})();
