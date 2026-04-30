/**
 * blog.js — VirtuWill Blog / Newsletter
 *
 * Two responsibilities:
 *   1. Home page — fetch published posts, render cards, open reader modal
 *   2. Admin portal (via VW.Admin) — list/compose/edit/delete posts
 *
 * Markdown support: bold, italic, headers (h1-h3), unordered lists,
 *   ordered lists, blockquotes, inline code, horizontal rule, links, line breaks.
 */
'use strict';
window.VW = window.VW || {};

window.VW.Blog = (() => {

  let _posts  = [];   // all posts in memory
  let _editId = null; // id of post being edited (null = new)
  let _thumbFile = null; // pending thumbnail file

  // ── Init (called on home page load and admin portal) ──────────────────────
  async function initHome() {
    await _load();
    _renderHomeCards();
  }

  async function initAdmin() {
    await _load(true); // admin sees all (including drafts)
    _renderAdminList();
  }

  async function _load(admin = false) {
    try {
      const r = await fetch('/api/blog');
      _posts = await r.json();
    } catch { _posts = []; }
  }

  // ════════════════════════════════════════════════════════════
  //  HOME PAGE — cards + reader
  // ════════════════════════════════════════════════════════════

  function _renderHomeCards() {
    const published = _posts.filter(p => p.published);
    const section   = document.getElementById('home-blog-section');
    const grid      = document.getElementById('home-blog-grid');
    if (!section || !grid) return;

    if (!published.length) { section.style.display = 'none'; return; }
    section.style.display = 'block';

    grid.innerHTML = published.map(p => `
      <div class="home-blog-card" onclick="VW.Blog.openReader('${p.id}')">
        <div class="home-blog-card-thumb">
          ${p.thumbnail
            ? `<img src="${_esc(p.thumbnail)}" alt="${_esc(p.title)}" loading="lazy"/>`
            : `<div class="home-blog-card-thumb-placeholder">&#128240;</div>`}
        </div>
        <div class="home-blog-card-body">
          <div class="home-blog-card-date">${_fmtDate(p.date)}</div>
          <div class="home-blog-card-title">${_esc(p.title)}</div>
          <div class="home-blog-card-excerpt">${_esc(p.excerpt || _strip(p.body).slice(0,120))}${(p.excerpt||p.body).length>120?'…':''}</div>
          <div class="home-blog-card-cta">Read post &#8594;</div>
        </div>
      </div>`).join('');
  }

  function openReader(id) {
    const post = _posts.find(p => p.id === id);
    if (!post) return;

    const bg    = document.getElementById('blog-reader-bg');
    const thumb = document.getElementById('blog-reader-thumb');
    const twrap = document.getElementById('blog-reader-thumb-wrap');
    const meta  = document.getElementById('blog-reader-meta');
    const title = document.getElementById('blog-reader-title');
    const body  = document.getElementById('blog-reader-body');

    if (!bg) return;

    if (post.thumbnail) {
      thumb.src = post.thumbnail;
      thumb.style.display = 'block';
      twrap.style.display = 'block';
    } else {
      twrap.style.display = 'none';
    }

    if (meta)  meta.textContent  = `${_fmtDate(post.date)} · ${post.author || 'Will Yoste'}`;
    if (title) title.textContent = post.title;
    if (body)  body.innerHTML    = _markdown(post.body || '');

    bg.classList.add('open');
    document.body.style.overflow = 'hidden';

    // Close on Escape
    document.addEventListener('keydown', _readerKeyClose, { once: true });
  }

  function closeReader() {
    const bg = document.getElementById('blog-reader-bg');
    if (bg) bg.classList.remove('open');
    document.body.style.overflow = '';
  }

  function _readerKeyClose(e) {
    if (e.key === 'Escape') closeReader();
  }

  // ════════════════════════════════════════════════════════════
  //  ADMIN — post list
  // ════════════════════════════════════════════════════════════

  function _renderAdminList() {
    const el = document.getElementById('adm-blog-body');
    if (!el) return;

    el.innerHTML = `
      <div class="adm-section-actions">
        <button class="adm-btn-primary" onclick="VW.Blog.openCompose()">&#43; New post</button>
        <button class="adm-btn-secondary" onclick="VW.Blog.initAdmin()">&#8635; Refresh</button>
      </div>
      ${_posts.length ? `<table class="adm-table">
        <thead><tr>
          <th>Post</th><th>Date</th><th>Status</th><th></th>
        </tr></thead>
        <tbody>
          ${_posts.map(p => `<tr>
            <td>
              <div style="display:flex;align-items:center;gap:10px">
                ${p.thumbnail
                  ? `<img src="${_esc(p.thumbnail)}" style="width:52px;height:34px;object-fit:cover;border-radius:4px;flex-shrink:0"/>`
                  : `<div style="width:52px;height:34px;background:var(--surface2);border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0">&#128240;</div>`}
                <div>
                  <strong>${_esc(p.title)}</strong>
                  ${p.excerpt?`<div class="adm-table-sub">${_esc(p.excerpt.slice(0,60))}…</div>`:''}
                </div>
              </div>
            </td>
            <td>${_fmtDate(p.date)}</td>
            <td>
              <label class="adm-toggle-label" title="${p.published?'Published — click to unpublish':'Draft — click to publish'}">
                <input type="checkbox" ${p.published?'checked':''} class="adm-toggle-cb"
                  onchange="VW.Blog.togglePublished('${p.id}',this.checked)"/>
                <span class="adm-toggle-track"></span>
                <span class="adm-toggle-text">${p.published?'Live':'Draft'}</span>
              </label>
            </td>
            <td class="adm-table-actions">
              <button class="adm-icon-btn" onclick="VW.Blog.openCompose('${p.id}')" title="Edit">&#9998;</button>
              <button class="adm-icon-btn danger" onclick="VW.Blog.deletePost('${p.id}')" title="Delete">&#128465;</button>
            </td>
          </tr>`).join('')}
        </tbody>
      </table>` : `<div class="adm-empty">No posts yet. Click "New post" to write your first.</div>`}`;
  }

  // ════════════════════════════════════════════════════════════
  //  ADMIN — compose / edit
  // ════════════════════════════════════════════════════════════

  function openCompose(id = null) {
    _editId = id;
    _thumbFile = null;

    const compose  = document.getElementById('adm-blog-compose');
    const blogSec  = document.getElementById('adm-sec-blog');
    const titleEl  = document.getElementById('adm-blog-compose-title');
    const inp      = document.getElementById('adm-blog-title-input');
    const bodyInp  = document.getElementById('adm-blog-body-input');
    const excerptInp = document.getElementById('adm-blog-excerpt-input');
    const thumbImg = document.getElementById('adm-blog-thumb-img');
    const thumbName= document.getElementById('adm-blog-thumb-name');
    const editingId= document.getElementById('adm-blog-editing-id');
    const thumbUrl = document.getElementById('adm-blog-thumb-url');

    if (!compose) return;

    if (id) {
      const post = _posts.find(p => p.id === id);
      if (!post) return;
      if (titleEl)   titleEl.textContent  = 'Edit post';
      if (inp)       inp.value            = post.title;
      if (bodyInp)   bodyInp.value        = post.body;
      if (excerptInp)excerptInp.value     = post.excerpt || '';
      if (editingId) editingId.value      = id;
      if (thumbUrl)  thumbUrl.value       = post.thumbnail || '';
      if (post.thumbnail && thumbImg) {
        thumbImg.src = post.thumbnail; thumbImg.style.display = 'block';
      } else if (thumbImg) {
        thumbImg.style.display = 'none';
      }
      if (thumbName) thumbName.textContent = post.thumbnail ? 'Current thumbnail' : '';
    } else {
      if (titleEl)   titleEl.textContent  = 'New post';
      if (inp)       inp.value            = '';
      if (bodyInp)   bodyInp.value        = '';
      if (excerptInp)excerptInp.value     = '';
      if (editingId) editingId.value      = '';
      if (thumbUrl)  thumbUrl.value       = '';
      if (thumbImg)  { thumbImg.src = ''; thumbImg.style.display = 'none'; }
      if (thumbName) thumbName.textContent = '';
    }

    // Show compose, hide blog list section
    if (blogSec)  blogSec.classList.add('hidden');
    compose.style.display = 'flex';
    // Lock parent scroll so only compose scrolls
    document.querySelector('.adm-main')?.classList.add('overflow-locked');
    inp?.focus();
  }

  function closeCompose() {
    document.getElementById('adm-blog-compose').style.display = 'none';
    document.getElementById('adm-sec-blog').classList.remove('hidden');
    document.querySelector('.adm-main')?.classList.remove('overflow-locked');
    _editId = null; _thumbFile = null;
  }

  function thumbChosen(files) {
    if (!files?.length) return;
    _thumbFile = files[0];
    const url  = URL.createObjectURL(files[0]);
    const img  = document.getElementById('adm-blog-thumb-img');
    const name = document.getElementById('adm-blog-thumb-name');
    if (img)  { img.src = url; img.style.display = 'block'; }
    if (name) name.textContent = files[0].name;
  }

  async function _uploadThumbIfNeeded() {
    if (!_thumbFile) return document.getElementById('adm-blog-thumb-url')?.value || '';
    const fd = new FormData();
    fd.append('file', _thumbFile);
    try {
      const r = await fetch('/api/blog/thumbnail', { method:'POST', body: fd });
      const d = await r.json();
      return d.url || '';
    } catch { return ''; }
  }

  async function _save(published) {
    const title   = document.getElementById('adm-blog-title-input')?.value.trim();
    const body    = document.getElementById('adm-blog-body-input')?.value.trim();
    const excerpt = document.getElementById('adm-blog-excerpt-input')?.value.trim();
    const editId  = document.getElementById('adm-blog-editing-id')?.value;

    if (!title) { window.toast?.('Post needs a title', 'error'); return; }

    const thumbnail = await _uploadThumbIfNeeded();

    const payload = { title, body, excerpt, thumbnail, published };

    try {
      let r;
      if (editId) {
        r = await fetch(`/api/blog/${editId}`, {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload),
        });
      } else {
        r = await fetch('/api/blog', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload),
        });
      }
      const d = await r.json();
      if (d.ok) {
        window.toast?.(published ? '🎉 Post published!' : '💾 Draft saved', 'success');
        const statusEl = document.getElementById('adm-blog-compose-status');
        if (statusEl) {
          statusEl.textContent = (published ? 'Published' : 'Draft saved') +
            ' at ' + new Date().toLocaleTimeString();
          setTimeout(() => { statusEl.textContent = ''; }, 5000);
        }
        closeCompose();
        await initAdmin();
        // Refresh home page cards too
        await _load();
        _renderHomeCards();
      } else {
        window.toast?.('Save failed', 'error');
      }
    } catch {
      window.toast?.('Network error', 'error');
    }
  }

  function saveDraft()  { _save(false); }
  function publish()    { _save(true);  }

  async function togglePublished(id, val) {
    const post = _posts.find(p => p.id === id);
    if (!post) return;
    try {
      await fetch(`/api/blog/${id}`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ ...post, published: val }),
      });
      post.published = val;
      _renderAdminList();
      _renderHomeCards();
      window.toast?.(val ? 'Post published' : 'Post set to draft', 'success', 1800);
    } catch { window.toast?.('Update failed', 'error'); }
  }

  async function deletePost(id) {
    if (!confirm('Delete this post? This cannot be undone.')) return;
    await fetch(`/api/blog/${id}`, { method:'DELETE' });
    _posts = _posts.filter(p => p.id !== id);
    _renderAdminList();
    _renderHomeCards();
    window.toast?.('Post deleted', 'success');
  }

  // ════════════════════════════════════════════════════════════
  //  MARKDOWN renderer (lightweight, no dependencies)
  // ════════════════════════════════════════════════════════════

  function _markdown(src) {
    if (!src) return '';
    let html = _esc(src);

    // Fenced code blocks (```...```)
    html = html.replace(/```([^`]*?)```/gs,
      (_, c) => `<pre class="blog-pre"><code>${c.trim()}</code></pre>`);

    // Headings
    html = html.replace(/^### (.+)$/gm, '<h3 class="blog-h3">$1</h3>');
    html = html.replace(/^## (.+)$/gm,  '<h2 class="blog-h2">$1</h2>');
    html = html.replace(/^# (.+)$/gm,   '<h1 class="blog-h1">$1</h1>');

    // Blockquote
    html = html.replace(/^&gt; (.+)$/gm, '<blockquote class="blog-bq">$1</blockquote>');

    // Horizontal rule
    html = html.replace(/^(-{3,}|\*{3,})$/gm, '<hr class="blog-hr"/>');

    // Unordered list groups
    html = html.replace(/((?:^- .+\n?)+)/gm, match => {
      const items = match.trim().split('\n').map(l =>
        `<li>${l.replace(/^- /, '')}</li>`).join('');
      return `<ul class="blog-ul">${items}</ul>`;
    });

    // Ordered list groups
    html = html.replace(/((?:^\d+\. .+\n?)+)/gm, match => {
      const items = match.trim().split('\n').map(l =>
        `<li>${l.replace(/^\d+\. /, '')}</li>`).join('');
      return `<ol class="blog-ol">${items}</ol>`;
    });

    // Inline formatting
    html = html.replace(/\*\*(.+?)\*\*/g,   '<strong>$1</strong>');
    html = html.replace(/__(.+?)__/g,        '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g,        '<em>$1</em>');
    html = html.replace(/_(.+?)_/g,          '<em>$1</em>');
    html = html.replace(/`(.+?)`/g,          '<code class="blog-code">$1</code>');

    // Links [text](url)
    html = html.replace(/\[(.+?)\]\((.+?)\)/g,
      '<a href="$2" target="_blank" rel="noopener" class="blog-link">$1</a>');

    // Paragraphs: double newlines
    html = html.replace(/\n{2,}/g, '</p><p class="blog-p">');
    html = '<p class="blog-p">' + html + '</p>';

    // Single newlines inside paragraphs → <br>
    html = html.replace(/\n/g, '<br/>');

    // Clean up empty paragraphs
    html = html.replace(/<p class="blog-p"><\/p>/g, '');
    html = html.replace(/<p class="blog-p">(<h[123]|<ul|<ol|<blockquote|<hr|<pre)/g, '$1');
    html = html.replace(/(<\/h[123]>|<\/ul>|<\/ol>|<\/blockquote>|<\/pre>)<\/p>/g, '$1');

    return html;
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────
  function _esc(s) {
    return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function _strip(s) {
    return String(s||'').replace(/[#*_`>-]/g,'').replace(/\s+/g,' ').trim();
  }

  function _fmtDate(iso) {
    if (!iso) return '';
    try {
      return new Date(iso + 'T00:00:00').toLocaleDateString('en-US',
        { year:'numeric', month:'long', day:'numeric' });
    } catch { return iso; }
  }

  return {
    initHome, initAdmin,
    openReader, closeReader,
    openCompose, closeCompose,
    thumbChosen, saveDraft, publish,
    togglePublished, deletePost,
  };

// ── Register dirty state ─────────────────────────────────────────────────────
window.VW?.Dirty?.register?.('admin', {
  label: 'Blog post',
  isDirty: () => {
    const compose = document.getElementById('adm-blog-compose');
    const title   = document.getElementById('adm-blog-title-input')?.value?.trim();
    return compose?.style.display === 'flex' && !!title;
  },
  save: async () => {
    const compose = document.getElementById('adm-blog-compose');
    if (compose?.style.display === 'flex') window.VW.Blog.saveDraft();
  },
});

})();
