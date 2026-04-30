/**
 * contact.js — VirtuWill Leave a Note
 * Visitor contact form + admin inbox
 */
'use strict';
window.VW = window.VW || {};

window.VW.Contact = (() => {

  function init() {
    _updateAdminInbox();
  }

  function _updateAdminInbox() {
    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;
    const inbox = document.getElementById('contact-inbox');
    if (inbox) inbox.style.display = isAdmin ? 'block' : 'none';
    if (isAdmin) loadInbox();
  }

  async function submit() {
    const name    = document.getElementById('cf-name')?.value.trim();
    const ctype   = document.getElementById('cf-contact-type')?.value || 'email';
    const cval    = document.getElementById('cf-contact-val')?.value.trim();
    const message = document.getElementById('cf-message')?.value.trim();
    const errEl   = document.getElementById('cf-error');
    const btnEl   = document.getElementById('cf-submit');

    if (errEl) errEl.textContent = '';

    if (!name)    { if (errEl) errEl.textContent = 'Please enter your name.'; return; }
    if (!message) { if (errEl) errEl.textContent = 'Please write a message.'; return; }

    if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Sending…'; }

    const contact = cval ? `${ctype}: ${cval}` : '';

    try {
      const r = await fetch('/api/contact', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, contact, message }),
      });
      const d = await r.json();
      if (d.ok) {
        document.getElementById('contact-form-wrap').style.display = 'none';
        document.getElementById('contact-success').style.display   = 'flex';
      } else {
        if (errEl) errEl.textContent = d.error || 'Something went wrong — try again.';
      }
    } catch {
      if (errEl) errEl.textContent = 'Network error — please try again.';
    } finally {
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Send note →'; }
    }
  }

  function reset() {
    document.getElementById('cf-name').value    = '';
    document.getElementById('cf-contact-val').value = '';
    document.getElementById('cf-message').value = '';
    document.getElementById('cf-error').textContent = '';
    document.getElementById('contact-form-wrap').style.display = 'block';
    document.getElementById('contact-success').style.display   = 'none';
  }

  async function loadInbox() {
    const listEl = document.getElementById('contact-messages-list');
    if (!listEl) return;
    listEl.innerHTML = '<div class="contact-empty">Loading…</div>';
    try {
      const r = await fetch('/api/contact/messages');
      if (!r.ok) { listEl.innerHTML = '<div class="contact-empty">No access.</div>'; return; }
      const msgs = await r.json();
      if (!msgs.length) {
        listEl.innerHTML = '<div class="contact-empty">No messages yet.</div>'; return;
      }
      listEl.innerHTML = msgs.map(m => `
        <div class="contact-msg${m.read ? '' : ' unread'}" id="cmsg-${m.id}">
          <div class="contact-msg-hd">
            <span class="contact-msg-name">${_esc(m.name)}</span>
            ${m.contact ? `<span class="contact-msg-contact">${_esc(m.contact)}</span>` : ''}
            <span class="contact-msg-date">${m.date || ''}</span>
            ${!m.read ? `<button class="contact-msg-read" onclick="VW.Contact.markRead('${m.id}')">Mark read</button>` : '<span class="contact-msg-badge">read</span>'}
          </div>
          <div class="contact-msg-body">${_esc(m.message)}</div>
        </div>`).join('');
    } catch {
      listEl.innerHTML = '<div class="contact-empty">Failed to load messages.</div>';
    }
  }

  async function markRead(id) {
    await fetch(`/api/contact/read/${id}`, { method: 'POST' });
    const el = document.getElementById(`cmsg-${id}`);
    if (el) {
      el.classList.remove('unread');
      el.querySelector('.contact-msg-read')?.remove();
      const badge = document.createElement('span');
      badge.className = 'contact-msg-badge'; badge.textContent = 'read';
      el.querySelector('.contact-msg-hd')?.appendChild(badge);
    }
  }

  function onAuthChange() { _updateAdminInbox(); }

  function _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  return { init, submit, reset, loadInbox, markRead, onAuthChange };
})();
