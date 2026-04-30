/**
 * journal.js — VirtuWill Journal module
 *
 * All journal state and behaviour:
 *   Auth (gate, unlock, lock)
 *   Entry CRUD — load from API, save, delete, upsert
 *   Timeline — collapsible year/month groups
 *   Search / date filter
 *   Sidebar stats and year navigation
 *   Upload modal with real base64 OCR via /api/journal/ocr
 *
 * Schema (matches app.py — all camelCase):
 *   { id, date, quote, quoteAuthor, meals:{B,L,D}, freeWrite, source, createdAt }
 *
 * Exposes: window.VW.Journal
 * Calls:   window.toast(), window.go()  — provided by app.js
 */

'use strict';

window.VW = window.VW || {};

window.VW.Journal = (() => {

  // ── Constants ──────────────────────────────────────────────────────────────
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  // ── Private state ──────────────────────────────────────────────────────────
  let _unlocked        = false;
  // ── Daily habits ─────────────────────────────────────────────────────────────
  const HABITS = [
    { id:'run',    label:'Run',          emoji:'🏃' },
    { id:'lift',   label:'Lift Weights', emoji:'🏋️' },
    { id:'guitar', label:'Play Guitar',  emoji:'🎸' },
    { id:'sexual', label:'Sexual',       emoji:'❤️'  },
    { id:'drink',  label:'Drinking',     emoji:'🥃' },
    { id:'smoke',  label:'Smoking',      emoji:'🚬' },
    { id:'read',   label:'Reading',      emoji:'📖' },
  ];

  let _entries         = [];
  let _openId          = null;   // expanded card id
  let _pendingDel      = null;   // id awaiting delete confirm
  let _collapsedYears  = new Set();
  let _collapsedMonths = new Set();
  let _searchActive    = false;

  // Queued files awaiting OCR: [{ file, base64, mediaType, previewUrl }]
  let _uploadQueue = [];

  // ── Helpers ────────────────────────────────────────────────────────────────
  const _esc = window.VW?.esc || (s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'));  // shared
  function _esc_old(s) {
    return (s || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
  }

  function _getSearchState() {
    return {
      kw: (document.getElementById('searchText')?.value || '').trim().toLowerCase(),
      dt: document.getElementById('searchDate')?.value || '',
    };
  }

  // ── Auth ───────────────────────────────────────────────────────────────────
  function isUnlocked()       { return _unlocked; }
  function setUnlocked(val)   { _unlocked = val; }

  async function tryUnlock() {
    const pw = document.getElementById('gateInput')?.value || '';
    try {
      const res  = await fetch('/api/journal/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: pw }),
      });
      const data = await res.json();
      if (data.ok) {
        _unlocked = true;
        document.getElementById('gateErr').style.display = 'none';
        document.getElementById('gateInput').value = '';
        await loadEntries();
        window.go('journal');
        window.toast('Journal unlocked', 'success');
      } else {
        _shakeGate();
      }
    } catch {
      _shakeGate();
    }
  }

  function _shakeGate() {
    document.getElementById('gateErr').style.display = 'block';
    const box = document.getElementById('gateBox');
    if (!box) return;
    box.style.animation = 'none';
    void box.offsetWidth;
    box.style.animation = 'shake .4s ease';
  }

  async function lock() {
    await fetch('/api/journal/logout', { method: 'POST' });
    _unlocked = false;
    _entries  = [];
    _openId   = null;
    window.toast('Journal locked', 'success');
    window.go('journal');
  }

  function onEnter() {
    // Called by router each time the journal page is shown (after unlock)
    if (_entries.length === 0) loadEntries();
  }

  // ── API — data operations ──────────────────────────────────────────────────
  async function loadEntries() {
    try {
      const res = await fetch('/api/journal/entries');
      if (res.status === 401) { _unlocked = false; window.go('journal'); return; }
      const data = await res.json();
      if (Array.isArray(data)) {
        _entries = data;
        buildTimeline();
      }
    } catch (e) {
      console.warn('Could not load journal entries:', e);
    }
  }

  async function saveCard(id) {
    const idx = _entries.findIndex(e => e.id === id);
    if (idx < 0) return;

    // Collect daily habit toggle state from DOM
    const habitMap = {};
    HABITS.forEach(h => {
      const inp = document.querySelector(`#ht-${h.id}-${id} input`);
      if (inp) habitMap[h.id] = inp.checked;
    });

    const editorEl = document.getElementById('fw-' + id);
    const freeWrite = editorEl?.getAttribute('contenteditable')
      ? _editorToHtml(editorEl)
      : (editorEl?.value || _entries[idx].freeWrite || '');
    const tagEls = document.querySelectorAll(`#tagwrap-${id} .j-tag`);
    const tags = [...tagEls].map(el => el.childNodes[0]?.textContent?.trim()).filter(Boolean);

    const updated = {
      ..._entries[idx],
      quote:       document.getElementById('q-'  + id)?.value || '',
      quoteAuthor: document.getElementById('qa-' + id)?.value || '',
      freeWrite,
      tags,
      meals: {
        B: document.getElementById('mB-' + id)?.value || '',
        L: document.getElementById('mL-' + id)?.value || '',
        D: document.getElementById('mD-' + id)?.value || '',
      },
      habits:   { ...(_entries[idx].habits || {}), ...habitMap },
      accounts: _entries[idx].accounts || [],
    };

    _entries[idx] = updated;

    try {
      await fetch('/api/journal/entry', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(updated),
      });
    } catch { /* optimistic — UI already updated */ }

    window.toast('Entry saved', 'success');
    buildTimeline();
  }

  async function addFromSb() {
    const date = document.getElementById('sbDate')?.value;
    if (!date) { window.toast('Pick a date first', 'warn'); return; }
    if (_entries.find(e => e.date === date)) {
      window.toast('Entry already exists for ' + date, 'warn');
      return;
    }

    const entry = {
      id:          'e' + Date.now(),
      date,
      quote:       '',
      quoteAuthor: '',
      meals:       { B: '', L: '', D: '' },
      freeWrite:   '',
      tags:        [],
      source:      'manual',
      createdAt:   new Date().toISOString(),
    };

    try {
      const res  = await fetch('/api/journal/entry', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(entry),
      });
      const data = await res.json();
      if (data.entry) entry.id = data.entry.id; // use server-assigned id
    } catch { /* optimistic */ }

    _entries.unshift(entry);
    _entries.sort((a, b) => b.date.localeCompare(a.date));
    _openId = entry.id;
    _collapsedYears.delete(date.slice(0, 4));
    buildTimeline();
    window.toast('Entry created for ' + date, 'success');
  }

  function askDel(id, date) {
    _pendingDel = id;
    document.getElementById('confTitle').textContent = 'Delete entry?';
    document.getElementById('confMsg').textContent   =
      `Permanently remove the entry for ${date}. This cannot be undone.`;
    document.getElementById('confOk').onclick        = _executeDel;
    document.getElementById('confirmModal').classList.add('open');
  }

  function closeConf() {
    document.getElementById('confirmModal').classList.remove('open');
    _pendingDel = null;
  }

  async function _executeDel() {
    const id = _pendingDel;
    _entries  = _entries.filter(e => e.id !== id);
    if (_openId === id) _openId = null;
    closeConf();
    buildTimeline();
    window.toast('Entry deleted', 'success');
    try { await fetch('/api/journal/entry/' + id, { method: 'DELETE' }); } catch { /* optimistic */ }
  }

  // ── Search ─────────────────────────────────────────────────────────────────
  function doSearch() {
    const kw = (document.getElementById('searchText')?.value || '').trim().toLowerCase();
    const dt = document.getElementById('searchDate')?.value || '';
    const clearBtn = document.getElementById('searchClear');
    if (clearBtn) clearBtn.style.display = (kw || dt) ? 'block' : 'none';
    _searchActive = !!(kw || dt);
    buildTimeline(kw, dt);
  }

  function clearSearch() {
    if (document.getElementById('searchText'))  document.getElementById('searchText').value  = '';
    if (document.getElementById('searchDate'))  document.getElementById('searchDate').value  = '';
    if (document.getElementById('searchClear')) document.getElementById('searchClear').style.display = 'none';
    _searchActive = false;
    buildTimeline();
  }

  function _entryMatches(entry, kw, dt) {
    if (dt && entry.date !== dt) return false;
    if (kw) {
      const hay = [
        entry.date, entry.quote, entry.quoteAuthor, entry.freeWrite,
        (entry.tags||[]).join(' '),
        entry.meals?.B, entry.meals?.L, entry.meals?.D,
      ].join(' ').toLowerCase();
      if (!hay.includes(kw)) return false;
    }
    return true;
  }

  // ── Timeline rendering ─────────────────────────────────────────────────────
  function buildTimeline(kw = '', dt = '') {
    const container = document.getElementById('timeline');
    if (!container) return;
    container.innerHTML = '';

    const filtered = _entries.filter(e => _entryMatches(e, kw, dt));

    // Search result info banner
    const info = document.getElementById('searchInfo');
    if (info) {
      info.style.display = _searchActive ? 'block' : 'none';
      if (_searchActive) {
        info.textContent = `${filtered.length} result${filtered.length !== 1 ? 's' : ''} found`
          + (kw ? ` for "${kw}"` : '') + (dt ? ` on ${dt}` : '');
      }
    }

    if (filtered.length === 0) {
      container.innerHTML = `
        <div class="no-results">
          <div class="no-results-icon">🔍</div>
          <div style="font-weight:600;margin-bottom:6px;">No entries found</div>
          <div style="font-size:13px;color:var(--text3);">Try different keywords or clear the search.</div>
        </div>`;
      _updateStats(0);
      return;
    }

    // Group by year → month
    const grouped = {};
    filtered.forEach(e => {
      const yr = e.date.slice(0, 4);
      const mo = e.date.slice(5, 7);
      if (!grouped[yr])     grouped[yr]     = {};
      if (!grouped[yr][mo]) grouped[yr][mo] = [];
      grouped[yr][mo].push(e);
    });

    Object.keys(grouped).sort((a, b) => b - a).forEach(yr => {
      const yrCollapsed  = _collapsedYears.has(yr);
      const yrEntryCount = Object.values(grouped[yr]).reduce((s, arr) => s + arr.length, 0);

      const yrGroup     = document.createElement('div');
      yrGroup.className = 'yr-group';
      yrGroup.id        = 'yrg-' + yr;
      yrGroup.innerHTML = `
        <div class="yr-header" onclick="VW.Journal.toggleYear('${yr}')">
          <div class="yr-dot"></div>
          <span class="yr-lbl">${yr}</span>
          <span class="yr-count">${yrEntryCount} entr${yrEntryCount !== 1 ? 'ies' : 'y'}</span>
          <span class="yr-chev${yrCollapsed ? '' : ' open'}">›</span>
        </div>
        <div class="yr-body${yrCollapsed ? ' hidden' : ''}" id="yrb-${yr}"></div>`;
      container.appendChild(yrGroup);

      if (!yrCollapsed) {
        const yrBody = document.getElementById('yrb-' + yr);
        Object.keys(grouped[yr]).sort((a, b) => b - a).forEach(mo => {
          const moKey       = yr + '-' + mo;
          const moCollapsed = _collapsedMonths.has(moKey);
          const moEntries   = grouped[yr][mo];
          const moName      = MONTHS[parseInt(mo, 10) - 1];

          const moGroup     = document.createElement('div');
          moGroup.className = 'mo-group';
          moGroup.innerHTML = `
            <div class="mo-header" onclick="VW.Journal.toggleMonth('${moKey}')">
              <div class="mo-bar"></div>
              <span class="mo-lbl">${moName}</span>
              <span class="mo-count">${moEntries.length}</span>
              <span class="mo-chev${moCollapsed ? '' : ' open'}">›</span>
            </div>
            <div class="mo-body${moCollapsed ? ' hidden' : ''}" id="mob-${moKey}"></div>`;
          yrBody.appendChild(moGroup);

          if (!moCollapsed) {
            const moBody = document.getElementById('mob-' + moKey);
            moEntries.forEach(e => moBody.appendChild(_buildEntryCard(e, kw)));
          }
        });
      }
    });

    const end     = document.createElement('div');
    end.className = 'tl-end';
    end.textContent = '— beginning of the record —';
    container.appendChild(end);

    _updateStats(filtered.length);
    _rebuildSidebarYears();
  }

  function _buildEntryCard(e, kw = '') {
    const isOpen  = _openId === e.id;
    const preview = e.quote
      ? `"${e.quote.slice(0, 46)}${e.quote.length > 46 ? '…' : ''}"`
      : (e.freeWrite ? e.freeWrite.replace(/<[^>]+>/g,'').slice(0, 46) + '…' : '');
    const tagPills = (e.tags||[]).map(t=>`<span class="j-tag-sm">${_esc(t)}</span>`).join('');

    const div     = document.createElement('div');
    div.className = 'entry' + (isOpen ? ' open' : '');
    div.id        = 'card-' + e.id;
    div.innerHTML = `
      <div class="entry-hd" onclick="VW.Journal.toggle('${e.id}')">
        <div class="entry-left">
          <div class="entry-dot"></div>
          <span class="entry-date">${e.date}</span>
          <span class="entry-prev">${_esc(preview)}</span>
          ${tagPills ? `<div class="j-tags-preview">${tagPills}</div>` : ''}
        </div>
        <div class="entry-right">
          ${e.source === 'photo' ? '<span class="badge badge-photo">📷</span>' : ''}
          ${kw && preview.toLowerCase().includes(kw) ? '<span class="badge badge-match">match</span>' : ''}
          <span class="chev${isOpen ? ' open' : ''}">›</span>
        </div>
      </div>
      <div class="entry-body${isOpen ? ' open' : ''}" id="body-${e.id}">
        <div class="f-lbl">Quote</div>
        <input class="input" type="text" id="q-${e.id}"
          value="${_esc(e.quote)}" placeholder="Today's quote…" style="margin-bottom:6px;"/>
        <input class="input" type="text" id="qa-${e.id}"
          value="${_esc(e.quoteAuthor)}" placeholder="— Author"/>
        <div class="f-lbl" style="margin-top:10px">Tags</div>
        <div class="j-tag-wrap" id="tagwrap-${e.id}">
          ${(e.tags||[]).map(t=>`<span class="j-tag">${_esc(t)}<button class="j-tag-x" onclick="VW.Journal.removeTag('${e.id}','${_esc(t)}')">×</button></span>`).join('')}
          <input class="j-tag-input" id="taginput-${e.id}" placeholder="Add tag…"
            onkeydown="VW.Journal.onTagKey(event,'${e.id}')"
            onfocus="this.closest('.j-tag-wrap').classList.add('focused')"
            onblur="VW.Journal.commitTag('${e.id}');this.closest('.j-tag-wrap').classList.remove('focused')"/>
        </div>
        <div class="f-lbl">Meals</div>
        <div class="meals-grid">
          ${['B', 'L', 'D'].map(m => `
            <div class="meal-cell">
              <div class="meal-lbl">${m}·${{ B: 'Breakfast', L: 'Lunch', D: 'Dinner' }[m]}</div>
              <textarea class="input j-meal-ta" rows="2"
                id="m${m}-${e.id}"
                placeholder="• ${{ B: 'Breakfast', L: 'Lunch', D: 'Dinner' }[m]}…"
                onkeydown="VW.Journal.mealKey(event)" onfocus="VW.Journal.mealFocus(this)"
                >${_esc(e.meals?.[m] || '')}</textarea>
            </div>`).join('')}
        </div>
        <div class="f-lbl">Daily Habits</div>
        <div class="habits-grid" id="habits-${e.id}">
          ${HABITS.map(h => {
            const on = e.habits?.[h.id] || false;
            return `<label class="habit-toggle ${on ? 'on' : ''}" id="ht-${h.id}-${e.id}">
              <input type="checkbox" ${on ? 'checked' : ''}
                onchange="this.closest('.habit-toggle').classList.toggle('on',this.checked)"
                data-hid="${h.id}"
                style="display:none"/>
              <span class="habit-emoji">${h.emoji}</span>
              <span class="habit-label">${h.label}</span>
              <span class="habit-switch"></span>
            </label>`;
          }).join('')}
        </div>
        <div class="f-lbl">Journal Entry</div>
        <div class="j-editor-wrap">
          <div class="j-ribbon">
            <button class="j-rb-btn" title="Bold"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('bold')"><b>B</b></button>
            <button class="j-rb-btn j-rb-i" title="Italic"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('italic')"><i>I</i></button>
            <button class="j-rb-btn j-rb-u" title="Underline"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('underline')"><u>U</u></button>
            <button class="j-rb-btn j-rb-s" title="Strikethrough"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('strike')"><s>S</s></button>
            <div class="j-rb-sep"></div>
            <select class="j-rb-size" title="Font size"
              onmousedown="VW.Journal.editorSelectionChange();event.stopPropagation()"
              onchange="VW.Journal.fmt('size',this.value);this.blur()">
              <option value="">Size</option>
              <option value="1">Small</option>
              <option value="3">Normal</option>
              <option value="5">Large</option>
              <option value="7">XL</option>
            </select>
            <div class="j-rb-sep"></div>
            <button class="j-rb-btn" title="Heading 2"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('heading')">H2</button>
            <button class="j-rb-btn" title="Blockquote"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('quote')">❝</button>
            <button class="j-rb-btn" title="Bullet list"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('bullet')">• List</button>
            <button class="j-rb-btn" title="Numbered list"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('ordered')">1. List</button>
            <div class="j-rb-sep"></div>
            <button class="j-rb-btn" title="Insert link"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('link')">🔗</button>
            <div class="j-rb-emoji-wrap" id="j-rb-emoji-wrap-${e.id}">
              <button class="j-rb-btn" title="Insert emoji"
                onmousedown="event.stopPropagation()"
                onclick="VW.Journal.toggleEmojiPicker(this)">😊</button>
              <div class="j-emoji-picker" id="j-emoji-picker-${e.id}" style="display:none">
                <div class="j-emoji-search-wrap">
                  <input class="j-emoji-search" placeholder="Search emojis…"
                    oninput="VW.Journal.filterEmoji(this.value,'${e.id}')"
                    onmousedown="event.stopPropagation()"/>
                </div>
                <div class="j-emoji-grid" id="j-emoji-grid-${e.id}"></div>
              </div>
            </div>
            <div class="j-rb-sep"></div>
            <button class="j-rb-btn j-rb-clear" title="Clear formatting"
              onmousedown="event.preventDefault()"
              onclick="VW.Journal.fmt('clear')">Tx</button>
          </div>
          <div class="j-editor input" id="fw-${e.id}"
            contenteditable="true"
            data-placeholder="Write anything…"
            oninput="VW.Journal.editorInput(this)"
            onfocus="VW.Journal.editorInput(this)"
            onmouseup="VW.Journal.editorSelectionChange()"
            onkeyup="VW.Journal.editorSelectionChange()"
            onkeydown="VW.Journal.editorKeydown(event,this)"
            >${e.freeWrite ? _htmlToEditor(e.freeWrite) : ''}</div>
        </div>
        <div class="card-acts">
          <button class="btn btn-ghost"  style="font-size:12px;"
            onclick="VW.Journal.saveCard('${e.id}')">💾 Save</button>
          <button class="btn btn-danger" style="font-size:12px;"
            onclick="VW.Journal.askDel('${e.id}','${e.date}')">🗑 Delete</button>
        </div>
        <!-- ── Financial Accounts ──────────────────────────────────── -->
        <div class="j-fin-section">
          <div class="j-fin-header">
            <span class="f-lbl" style="margin:0">Financial Accounts</span>
            <div class="j-fin-header-actions">
              <button class="j-fin-mask-btn" id="j-mask-btn-${e.id}"
                onclick="VW.Journal.toggleMask('${e.id}')"
                title="Show/hide balances">
                <span id="j-mask-icon-${e.id}">👁 Show</span>
              </button>
              <button class="j-fin-add-btn"
                onclick="VW.Journal.addAccount('${e.id}')">+ Add account</button>
            </div>
          </div>
          <div class="j-fin-table-wrap">
            <table class="j-fin-table" id="j-fin-table-${e.id}">
              <thead>
                <tr>
                  <th class="j-fin-th-drag"></th>
                  <th>Institution</th>
                  <th>Account</th>
                  <th class="j-fin-th-bal">Balance</th>
                  <th class="j-fin-th-act"></th>
                </tr>
              </thead>
              <tbody id="j-fin-body-${e.id}">
                ${_renderAccountRows(e.id, e.accounts || [])}
              </tbody>
            </table>
            ${!(e.accounts?.length) ? '<div class="j-fin-empty">No accounts yet — click + Add account</div>' : ''}
          </div>
          ${(e.accounts?.length) ? `<div class="j-fin-net" id="j-fin-net-${e.id}">
            ${_renderNetWorth(e.id, e.accounts)}
          </div>` : ''}
        </div>
      </div>`;
    return div;
  }

  // ── Timeline interaction ───────────────────────────────────────────────────
  function toggleYear(yr) {
    _collapsedYears.has(yr) ? _collapsedYears.delete(yr) : _collapsedYears.add(yr);
    const { kw, dt } = _getSearchState();
    buildTimeline(kw, dt);
  }

  function toggleMonth(key) {
    _collapsedMonths.has(key) ? _collapsedMonths.delete(key) : _collapsedMonths.add(key);
    const { kw, dt } = _getSearchState();
    buildTimeline(kw, dt);
  }

  function toggle(id) {
    _openId = (_openId === id) ? null : id;
    const { kw, dt } = _getSearchState();
    buildTimeline(kw, dt);
    if (_openId) {
      setTimeout(() => {
        document.getElementById('card-' + id)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }, 80);
    }
  }

  function jumpYear(yr, el) {
    _collapsedYears.delete(yr);
    buildTimeline();
    setTimeout(() => {
      document.getElementById('yrg-' + yr)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
    document.querySelectorAll('.sb-item[data-year]').forEach(x => x.classList.remove('on'));
    if (el) el.classList.add('on');
  }

  // ── Sidebar ────────────────────────────────────────────────────────────────
  function _updateStats(shown) {
    const n = _entries.length;
    const sbCount  = document.getElementById('sbCount');
    const stTotal  = document.getElementById('stTotal');
    const stLatest = document.getElementById('stLatest');

    if (sbCount)  sbCount.textContent  = n;
    if (stTotal)  stTotal.textContent  = shown !== undefined ? shown : n;
    if (stLatest && n > 0) {
      const d = _entries[0].date;
      stLatest.textContent = MONTHS[parseInt(d.slice(5, 7), 10) - 1] + ' ' + d.slice(8);
    }
  }

  function _rebuildSidebarYears() {
    // Remove old year items (identified by data-year attribute)
    document.querySelectorAll('.sb-item[data-year]').forEach(el => el.remove());

    const years      = [...new Set(_entries.map(e => e.date.slice(0, 4)))].sort().reverse();
    const sidebar    = document.querySelector('.journal-sidebar');
    const firstHr    = sidebar?.querySelector('.sb-hr');
    if (!firstHr) return;

    // Insert year items after the first horizontal rule
    let insertAfter = firstHr;
    years.forEach(yr => {
      const el         = document.createElement('div');
      el.className     = 'sb-item';
      el.dataset.year  = yr;
      el.innerHTML     = `<span>📅</span> ${yr}`;
      el.onclick       = () => jumpYear(yr, el);
      insertAfter.insertAdjacentElement('afterend', el);
      insertAfter      = el;
    });
  }

  // ── Upload / OCR ───────────────────────────────────────────────────────────
  function openUpload() {
    _uploadQueue = [];
    document.getElementById('uploadQ').innerHTML   = '';
    document.getElementById('procBtn').style.display = 'none';
    document.getElementById('uploadModal').classList.add('open');
  }

  function closeUpload() {
    document.getElementById('uploadModal').classList.remove('open');
    _uploadQueue = [];
  }

  function handleDrop(e) {
    e.preventDefault();
    document.getElementById('dropZone')?.classList.remove('over');
    handleFiles(e.dataTransfer.files);
  }

  function handleFiles(files) {
    Array.from(files).forEach(file => {
      if (!file.type.startsWith('image/')) {
        window.toast(file.name + ' is not an image', 'warn');
        return;
      }
      const reader = new FileReader();
      reader.onload = ev => {
        const dataUrl   = ev.target.result;
        const base64    = dataUrl.split(',')[1];
        const mediaType = dataUrl.split(';')[0].split(':')[1];
        const item      = { file, base64, mediaType, previewUrl: dataUrl };
        _uploadQueue.push(item);
        _renderUploadQueue();
      };
      reader.readAsDataURL(file);
    });
  }

  function _renderUploadQueue() {
    const q   = document.getElementById('uploadQ');
    const btn = document.getElementById('procBtn');
    if (!q) return;

    q.innerHTML = '';
    _uploadQueue.forEach((item, idx) => {
      const el     = document.createElement('div');
      el.className = 'q-item';
      el.innerHTML = `
        <img src="${item.previewUrl}" style="width:40px;height:40px;object-fit:cover;border-radius:6px;"/>
        <div style="flex:1;">
          <div style="font-weight:500;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${item.file.name}</div>
          <div style="font-size:11px;color:var(--text3);">${(item.file.size / 1024 / 1024).toFixed(1)} MB</div>
        </div>
        <button onclick="VW.Journal._removeFromQueue(${idx})"
          style="background:none;border:none;color:var(--text3);cursor:pointer;font-size:20px;line-height:1;">×</button>`;
      q.appendChild(el);
    });

    if (btn) btn.style.display = _uploadQueue.length > 0 ? 'flex' : 'none';
  }

  function _removeFromQueue(idx) {
    _uploadQueue.splice(idx, 1);
    _renderUploadQueue();
  }

  async function processOcr() {
    if (_uploadQueue.length === 0) return;

    const btn = document.getElementById('procBtn');
    if (btn) { btn.textContent = 'Processing…'; btn.disabled = true; }

    let added = 0, skipped = 0, errors = 0;

    for (const item of _uploadQueue) {
      try {
        // POST base64 to Flask OCR proxy — API key never leaves the server
        const res = await fetch('/api/journal/ocr', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ base64: item.base64, mediaType: item.mediaType }),
        });
        const data = await res.json();
        if (!data.ok) throw new Error(data.error || 'OCR failed');

        // Check for date collision
        const ocrDate = data.data.date || new Date().toISOString().slice(0, 10);
        if (_entries.find(e => e.date === ocrDate)) {
          window.toast(`Skipped ${ocrDate} — entry already exists`, 'warn');
          skipped++;
          continue;
        }

        const entry = {
          id:          'e' + Date.now() + Math.random().toString(36).slice(2),
          date:        ocrDate,
          quote:       data.data.quote       || '',
          quoteAuthor: data.data.quoteAuthor || '',
          meals:       data.data.meals       || { B: '', L: '', D: '' },
          freeWrite:   data.data.freeWrite   || '',
          source:      'photo',
          createdAt:   new Date().toISOString(),
        };

        // Save to server
        const saveRes = await fetch('/api/journal/entry', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(entry),
        });
        const saveData = await saveRes.json();
        if (saveData.entry) entry.id = saveData.entry.id;

        _entries.unshift(entry);
        _entries.sort((a, b) => b.date.localeCompare(a.date));
        _openId = entry.id;
        _collapsedYears.delete(ocrDate.slice(0, 4));
        added++;
      } catch (err) {
        console.error('OCR error:', err);
        window.toast('OCR error: ' + err.message, 'error');
        errors++;
      }
    }

    if (btn) { btn.textContent = 'Process with Claude'; btn.disabled = false; }
    _uploadQueue = [];
    closeUpload();

    if (added > 0) {
      buildTimeline();
      const msg = [added && `${added} added`, skipped && `${skipped} skipped`, errors && `${errors} errors`]
        .filter(Boolean).join(' · ');
      window.toast(msg || 'Done', errors > 0 ? 'warn' : 'success');
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  // ── Tag management ─────────────────────────────────────────────────────────

  function onTagKey(e, entryId) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commitTag(entryId);
    } else if (e.key === 'Backspace' && e.target.value === '') {
      const wrap = document.getElementById('tagwrap-' + entryId);
      const tags = wrap?.querySelectorAll('.j-tag');
      if (tags?.length) tags[tags.length - 1].remove();
    }
  }

  function commitTag(entryId) {
    const input = document.getElementById('taginput-' + entryId);
    if (!input) return;
    const val = input.value.replace(/,/g, '').trim();
    if (!val) return;
    const wrap = document.getElementById('tagwrap-' + entryId);
    const existing = [...(wrap?.querySelectorAll('.j-tag') || [])]
      .map(el => el.childNodes[0]?.textContent?.trim());
    if (existing.includes(val)) { input.value = ''; return; }
    const pill = document.createElement('span');
    pill.className = 'j-tag';
    pill.innerHTML = `${_esc(val)}<button class="j-tag-x" onclick="VW.Journal.removeTag('${entryId}','${_esc(val)}')">×</button>`;
    wrap.insertBefore(pill, input);
    input.value = '';
  }

  function removeTag(entryId, tagVal) {
    const wrap = document.getElementById('tagwrap-' + entryId);
    wrap?.querySelectorAll('.j-tag').forEach(el => {
      if (el.childNodes[0]?.textContent?.trim() === tagVal) el.remove();
    });
  }

  // ── Meal textarea auto-bullet ──────────────────────────────────────────────

  function mealKey(e) {
    const ta = e.target;
    if (e.key === 'Enter') {
      e.preventDefault();
      const pos = ta.selectionStart;
      const ins = '\n• ';
      ta.value = ta.value.slice(0, pos) + ins + ta.value.slice(pos);
      ta.selectionStart = ta.selectionEnd = pos + ins.length;
      _mealResize(ta);
      return;
    }
    // On any printable key: if the textarea is empty, prefix with bullet
    if (ta.value === '' && e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      ta.value = '• ' + e.key;
      ta.selectionStart = ta.selectionEnd = ta.value.length;
      _mealResize(ta);
      return;
    }
    // On backspace: if cursor is right after a bullet at start of line, remove the bullet too
    if (e.key === 'Backspace') {
      const pos = ta.selectionStart;
      const before = ta.value.slice(0, pos);
      if (before.endsWith('• ') || before === '• ') {
        e.preventDefault();
        const remove = before.endsWith('\n• ') ? 3 : 2;
        ta.value = ta.value.slice(0, pos - remove) + ta.value.slice(pos);
        ta.selectionStart = ta.selectionEnd = pos - remove;
        _mealResize(ta);
      }
    }
  }

  function _mealResize(ta) {
    ta.style.height = 'auto';
    ta.style.height = ta.scrollHeight + 'px';
  }

  function mealFocus(ta) {
    // Auto-add bullet on first focus if empty
    if (ta.value.trim() === '') {
      ta.value = '• ';
      ta.selectionStart = ta.selectionEnd = 2;
    }
  }

  // ── Editor helpers ─────────────────────────────────────────────────────────

  function _htmlToEditor(text) {
    if (!text) return '';
    if (/<[a-z][\s\S]*>/i.test(text)) return text;
    return _esc(text).replace(/\n/g, '<br>');
  }

  function _editorToHtml(el) {
    return el.innerHTML
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"');
  }

  function editorInput(el) {
    _lastEditor = el;
    el.dataset.empty = (el.textContent.trim() === '' && !el.innerHTML.includes('<')) ? '1' : '';
    // Save the current selection so ribbon buttons can restore it
    const sel = window.getSelection();
    if (sel.rangeCount) _savedRange = sel.getRangeAt(0).cloneRange();
  }

  function editorSelectionChange() {
    // Track selection changes (fires on mouseup, keyup inside contenteditable)
    const active = document.activeElement;
    if (active?.classList.contains('j-editor')) {
      _lastEditor = active;
      const sel = window.getSelection();
      if (sel.rangeCount && active.contains(sel.anchorNode)) {
        _savedRange = sel.getRangeAt(0).cloneRange();
      }
    }
  }

  // Passive document-level selectionchange — most reliable across browsers
  document.addEventListener('selectionchange', () => {
    const sel = window.getSelection();
    if (!sel.rangeCount) return;
    const node = sel.anchorNode;
    // Walk up to find if we're inside a .j-editor
    let el = node?.nodeType === 3 ? node.parentElement : node;
    while (el) {
      if (el.classList?.contains('j-editor')) {
        _lastEditor = el;
        _savedRange = sel.getRangeAt(0).cloneRange();
        return;
      }
      el = el.parentElement;
    }
  });

  function editorKeydown(e, el) {
    _lastEditor = el;
    if (e.key === 'Tab') {
      e.preventDefault();
      document.execCommand('insertText', false, '    ');
    }
    // Save selection on next tick (after cursor has moved)
    requestAnimationFrame(() => {
      const sel = window.getSelection();
      if (sel.rangeCount && el.contains(sel.anchorNode)) {
        _savedRange = sel.getRangeAt(0).cloneRange();
      }
    });
  }

  // ── Formatting ribbon ──────────────────────────────────────────────────────

  function fmt(cmd, val) {
    // Find the target editor — prefer last focused, then any focused editor
    const editor = _lastEditor ||
      document.querySelector('.j-editor-wrap:focus-within .j-editor') ||
      document.querySelector('.j-editor:focus');

    if (!editor) return;

    // Restore saved selection if we have one, otherwise focus the editor
    // (onmousedown="event.preventDefault()" on each button preserves the
    //  editor's focus and selection, so we just need to ensure it's active)
    if (_savedRange && _savedRange.startContainer &&
        editor.contains(_savedRange.startContainer)) {
      editor.focus();
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(_savedRange);
    } else {
      editor.focus();
    }

    switch (cmd) {
      case 'bold':
        document.execCommand('bold', false);
        break;

      case 'italic':
        document.execCommand('italic', false);
        break;

      case 'underline':
        document.execCommand('underline', false);
        break;

      case 'strike':
        document.execCommand('strikeThrough', false);
        break;

      case 'clear':
        document.execCommand('removeFormat', false);
        // Also remove block formatting
        document.execCommand('formatBlock', false, 'div');
        break;

      case 'bullet':
        document.execCommand('insertUnorderedList', false);
        break;

      case 'ordered':
        document.execCommand('insertOrderedList', false);
        break;

      case 'size':
        if (val) document.execCommand('fontSize', false, val);
        break;

      case 'heading': {
        // formatBlock is more reliable than surroundContents across browsers
        const sel2 = window.getSelection();
        const inH2 = sel2.anchorNode &&
          (sel2.anchorNode.parentElement?.tagName === 'H2' ||
           sel2.anchorNode.parentElement?.closest?.('h2'));
        document.execCommand('formatBlock', false, inH2 ? 'div' : 'h2');
        break;
      }

      case 'quote': {
        const sel3 = window.getSelection();
        const inBQ = sel3.anchorNode &&
          (sel3.anchorNode.parentElement?.tagName === 'BLOCKQUOTE' ||
           sel3.anchorNode.parentElement?.closest?.('blockquote'));
        document.execCommand('formatBlock', false, inBQ ? 'div' : 'blockquote');
        break;
      }

      case 'link': {
        // Save selection before prompt (prompt blurs the editor)
        const savedSel = window.getSelection();
        let savedRng = null;
        if (savedSel.rangeCount) savedRng = savedSel.getRangeAt(0).cloneRange();
        const url = prompt('Enter URL (include https://):');
        if (url && savedRng) {
          editor.focus();
          const sel4 = window.getSelection();
          sel4.removeAllRanges();
          sel4.addRange(savedRng);
          document.execCommand('createLink', false, url);
        }
        break;
      }
    }

    // Keep _lastEditor current after command
    _lastEditor = editor;
  }

  // ── Emoji picker ────────────────────────────────────────────────────────────

  const EMOJI_LIST = [
    '😀','😃','😄','😁','😆','😅','🤣','😂','🙂','🙃','😉','😊','😇','🥰','😍','🤩',
    '😘','😗','😚','😙','🥲','😋','😛','😜','🤪','😝','🤑','🤗','🤭','🤫','🤔','🤐',
    '😐','😑','😶','😏','😒','🙄','😬','🤥','😌','😔','😪','🤤','😴','😷','🤒','🤕',
    '🤢','🤮','🤧','🥵','🥶','😵','💫','🤯','🤠','🥳','😎','🤓','🧐','😕','😟','🙁',
    '😮','😯','😲','😳','🥺','😦','😧','😨','😰','😥','😢','😭','😱','😖','😣','😞',
    '😓','😩','😫','🥱','😤','😡','😠','🤬','😈','👿','💀','☠️','💩','🤡','👹','👻',
    '❤️','🧡','💛','💚','💙','💜','🖤','🤍','🤎','💔','❣️','💕','💞','💓','💗','💖',
    '💝','💟','☮️','✌️','🤞','👍','👎','✊','👊','🤛','🤜','👏','🙌','👐','🤲','🙏',
    '🌸','🌺','🌻','🌹','🌷','🌼','💐','🍀','🌿','🌱','🌲','🌳','🌴','🌵','🌾','🍁',
    '🍂','🍃','🌍','🌙','⭐','🌟','💫','✨','☀️','🌈','⛅','🌧️','❄️','🔥','💧','🌊',
    '🍎','🍊','🍋','🍇','🍓','🫐','🍈','🍑','🍒','🥭','🍍','🥥','🥝','🍅','🍆','🥑',
    '🥦','🌽','🥕','🧅','🧄','🥔','🍠','🍞','🥐','🍳','🥚','🧀','🥓','🌮','🍕','🍔',
    '🍜','🍣','🍦','🎂','🍰','🧁','🍩','🍪','🍫','🍭','🧃','🥤','☕','🍵','🍺','🥂',
    '⚽','🏀','🏈','⚾','🥎','🏐','🏉','🎾','🏸','🏒','🥊','🤼','🏋️','🤸','⛹️','🚴',
    '🏄','🏊','🧘','🎯','🎲','🎮','🎸','🎵','🎶','🎤','🎧','🎨','🖊️','📚','✏️','📝',
    '🏠','🏡','🏢','⛪','🏔️','🌋','🏕️','🏖️','🏗️','🚗','✈️','🚀','🛸','⛵','🚂',
    '💻','📱','📷','🎁','🎀','💌','📮','🔑','💡','🔦','🕯️','🪴','🛋️','🛁','🧹','🌂',
    '⏰','📅','💰','💳','🔮','🧲','🔬','🔭','⚗️','🧪','🎭','🎪','🎬','📺','📻',
    '✅','❌','⚠️','🔴','🟠','🟡','🟢','🔵','🟣','⚫','⚪','🔶','🔷','💯','🔝','🆕',
    '🆒','🆓','⁉️','❓','❗','💬','💭','🗯️','🔔','🔕','🎵','🎼','🎹',
  ];

  function toggleEmojiPicker(btn) {
    _lastEditor = document.querySelector('.j-editor-wrap:focus-within .j-editor') || _lastEditor;
    if (_lastEditor) {
      const sel = window.getSelection();
      if (sel.rangeCount) _savedRange = sel.getRangeAt(0).cloneRange();
    }
    // Find the picker inside the same emoji-wrap as this button
    const wrap = btn.closest('.j-rb-emoji-wrap');
    const picker = wrap?.querySelector('.j-emoji-picker');
    if (!picker) return;
    const isOpen = picker.style.display !== 'none';
    // Close all other pickers first
    document.querySelectorAll('.j-emoji-picker').forEach(p => { if (p !== picker) p.style.display = 'none'; });
    if (isOpen) { picker.style.display = 'none'; return; }
    const grid = wrap.querySelector('.j-emoji-grid');
    if (grid && !grid.children.length) _renderEmojiGrid(EMOJI_LIST, grid);
    picker.style.display = 'block';
    _activeEmojiWrap = wrap;
    setTimeout(() => document.addEventListener('mousedown', _closeEmojiOnOutside, { once: true }), 0);
  }

  let _activeEmojiWrap = null;
  function _closeEmojiOnOutside(e) {
    if (_activeEmojiWrap && !_activeEmojiWrap.contains(e.target)) {
      _activeEmojiWrap.querySelector('.j-emoji-picker').style.display = 'none';
      _activeEmojiWrap = null;
    }
  }

  function _renderEmojiGrid(list, grid) {
    if (!grid) return;
    grid.innerHTML = list.map(e =>
      `<button class="j-emoji-btn" onmousedown="event.preventDefault()"
        onclick="VW.Journal.insertEmoji('${e}')">${e}</button>`
    ).join('');
  }

  function filterEmoji(query, entryId) {
    const q = query.trim().toLowerCase();
    const wrap = document.getElementById('j-rb-emoji-wrap-' + entryId);
    const grid = wrap?.querySelector('.j-emoji-grid');
    _renderEmojiGrid(q ? EMOJI_LIST.filter(e => e.toLowerCase().includes(q)) : EMOJI_LIST, grid);
  }

  function insertEmoji(emoji) {
    const picker = document.getElementById('j-emoji-picker');
    if (picker) picker.style.display = 'none';
    const editor = _lastEditor;
    if (!editor) return;
    editor.focus();
    if (_savedRange) {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(_savedRange);
    }
    document.execCommand('insertText', false, emoji);
    _savedRange = null;
  }

  // ── Financial Accounts ─────────────────────────────────────────────────────

  const _masked = {};   // entryId → boolean (true = masked/hidden)

  function _isMasked(entryId) {
    return _masked[entryId] !== false;  // default: masked for privacy
  }

  function toggleMask(entryId) {
    _masked[entryId] = !_isMasked(entryId);
    // Re-render the table body and net worth in place (no full timeline rebuild)
    const entry = _entries.find(e => e.id === entryId);
    if (!entry) return;
    const tbody = document.getElementById('j-fin-body-' + entryId);
    if (tbody) tbody.innerHTML = _renderAccountRows(entryId, entry.accounts || []);
    const net = document.getElementById('j-fin-net-' + entryId);
    if (net) net.innerHTML = _renderNetWorth(entryId, entry.accounts || []);
    const icon = document.getElementById('j-mask-icon-' + entryId);
    if (icon) icon.textContent = _isMasked(entryId) ? '👁 Show' : '🙈 Hide';
  }

  function _fmtBalance(balance, masked) {
    const num = parseFloat(balance) || 0;
    if (masked) {
      // Show structure but mask digits — keep sign and $ prefix
      const isNeg = num < 0;
      return `<span class="j-fin-bal${isNeg ? ' neg' : ''}">
        ${isNeg ? '(' : ''}$••••${isNeg ? ')' : ''}
      </span>`;
    }
    const abs  = Math.abs(num).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (num < 0) {
      return `<span class="j-fin-bal neg">($${abs})</span>`;
    }
    return `<span class="j-fin-bal">$${abs}</span>`;
  }

  function _renderAccountRows(entryId, accounts) {
    if (!accounts?.length) return '';
    const masked = _isMasked(entryId);
    return accounts.map((acct, i) => `
      <tr class="j-fin-row" id="j-fin-row-${entryId}-${i}"
        draggable="true"
        ondragstart="VW.Journal._acctDragStart(event,'${entryId}',${i})"
        ondragover="VW.Journal._acctDragOver(event,'${entryId}',${i})"
        ondrop="VW.Journal._acctDrop(event,'${entryId}',${i})"
        ondragend="VW.Journal._acctDragEnd(event,'${entryId}')">
        <td class="j-fin-td j-fin-td-drag">
          <span class="j-fin-drag-handle" title="Drag to reorder">⠿</span>
        </td>
        <td class="j-fin-td">
          <input class="j-fin-input" value="${_esc(acct.institution || '')}"
            placeholder="e.g. Chase"
            onchange="VW.Journal.updateAccount('${entryId}',${i},'institution',this.value)"/>
        </td>
        <td class="j-fin-td">
          <input class="j-fin-input" value="${_esc(acct.name || '')}"
            placeholder="e.g. Checking"
            onchange="VW.Journal.updateAccount('${entryId}',${i},'name',this.value)"/>
        </td>
        <td class="j-fin-td j-fin-td-bal">
          <div class="j-fin-bal-cell">
            ${_fmtBalance(acct.balance, masked)}
            <input class="j-fin-bal-input${masked ? ' masked' : ''}"
              type="number" step="0.01"
              value="${acct.balance ?? ''}"
              placeholder="0.00"
              ${masked ? 'tabindex="-1"' : ''}
              onchange="VW.Journal.updateAccount('${entryId}',${i},'balance',this.value)"/>
          </div>
        </td>
        <td class="j-fin-td j-fin-td-del">
          <button class="j-fin-del-btn" title="Remove account"
            onclick="VW.Journal.removeAccount('${entryId}',${i})">✕</button>
        </td>
      </tr>`).join('');
  }

  function _renderNetWorth(entryId, accounts) {
    if (!accounts?.length) return '';
    const total  = accounts.reduce((s, a) => s + (parseFloat(a.balance) || 0), 0);
    const masked = _isMasked(entryId);
    const fmt    = masked
      ? '<span class="j-fin-bal">$••••</span>'
      : (() => {
          const abs = Math.abs(total).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
          return total < 0
            ? `<span class="j-fin-bal neg">($${abs})</span>`
            : `<span class="j-fin-bal">$${abs}</span>`;
        // ── Register dirty state ─────────────────────────────────────────────────────
window.VW?.Dirty?.register?.('journal', {
  label: 'Journal',
  isDirty: () => !!document.querySelector('.entry-body.open'),
  save: async () => {
    // Save all open cards
    const openBodies = document.querySelectorAll('.entry-body.open');
    for (const body of openBodies) {
      const id = body.id.replace('body-', '');
      if (id) await window.VW.Journal.saveCard(id);
    }
  },
});

})();
    return `<div class="j-fin-net-row">
      <span class="j-fin-net-lbl">Net worth</span>
      ${fmt}
    </div>`;
  }

  function addAccount(entryId) {
    const idx = _entries.findIndex(e => e.id === entryId);
    if (idx < 0) return;
    if (!_entries[idx].accounts) _entries[idx].accounts = [];
    _entries[idx].accounts.push({ institution: '', name: '', balance: '' });
    _refreshAccountTable(entryId);
  }

  function removeAccount(entryId, acctIdx) {
    const idx = _entries.findIndex(e => e.id === entryId);
    if (idx < 0) return;
    _entries[idx].accounts.splice(acctIdx, 1);
    _refreshAccountTable(entryId);
  }

  function updateAccount(entryId, acctIdx, field, value) {
    const idx = _entries.findIndex(e => e.id === entryId);
    if (idx < 0 || !_entries[idx].accounts?.[acctIdx]) return;
    _entries[idx].accounts[acctIdx][field] = field === 'balance' ? value : value;
    // Update net worth display live
    const net = document.getElementById('j-fin-net-' + entryId);
    if (net) net.innerHTML = _renderNetWorth(entryId, _entries[idx].accounts);
  }

  function _refreshAccountTable(entryId) {
    const entry = _entries.find(e => e.id === entryId);
    if (!entry) return;
    const tbody = document.getElementById('j-fin-body-' + entryId);
    if (tbody) tbody.innerHTML = _renderAccountRows(entryId, entry.accounts || []);
    const net = document.getElementById('j-fin-net-' + entryId);
    if (net) net.innerHTML = _renderNetWorth(entryId, entry.accounts || []);
    // Show/hide empty state
    const wrap = tbody?.closest('.j-fin-table-wrap');
    const empty = wrap?.querySelector('.j-fin-empty');
    if (empty) empty.style.display = entry.accounts?.length ? 'none' : 'block';
  }

  // ── Account row drag-to-reorder ───────────────────────────────────────────────
  let _acctDragIdx = null;

  function _acctDragStart(e, entryId, idx) {
    _acctDragIdx = idx;
    e.dataTransfer.effectAllowed = 'move';
    setTimeout(() => {
      document.getElementById(`j-fin-row-${entryId}-${idx}`)?.classList.add('j-fin-dragging');
    }, 0);
  }

  function _acctDragOver(e, entryId, idx) {
    e.preventDefault();
    if (_acctDragIdx === null || _acctDragIdx === idx) return;
    const el = document.getElementById(`j-fin-row-${entryId}-${idx}`);
    const rect = el?.getBoundingClientRect();
    const before = rect && e.clientY < rect.top + rect.height / 2;
    document.querySelectorAll('.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-drop-above','j-fin-drop-below'));
    el?.classList.add(before ? 'j-fin-drop-above' : 'j-fin-drop-below');
  }

  function _acctDrop(e, entryId, toIdx) {
    e.preventDefault();
    document.querySelectorAll('.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-drop-above','j-fin-drop-below'));
    if (_acctDragIdx === null || _acctDragIdx === toIdx) return;
    const eIdx = _entries.findIndex(en => en.id === entryId);
    if (eIdx < 0) return;
    const accts = _entries[eIdx].accounts;
    const el = document.getElementById(`j-fin-row-${entryId}-${toIdx}`);
    const rect = el?.getBoundingClientRect();
    const before = rect && e.clientY < rect.top + rect.height / 2;
    const insert = before ? toIdx : toIdx + 1;
    const [moved] = accts.splice(_acctDragIdx, 1);
    const adj = insert > _acctDragIdx ? insert - 1 : insert;
    accts.splice(adj, 0, moved);
    _acctDragIdx = null;
    _refreshAccountTable(entryId);
  }

  function _acctDragEnd(e, entryId) {
    _acctDragIdx = null;
    document.querySelectorAll('.j-fin-dragging,.j-fin-drop-above,.j-fin-drop-below')
      .forEach(n => n.classList.remove('j-fin-dragging','j-fin-drop-above','j-fin-drop-below'));
  }

  // ── Template pre-population ────────────────────────────────────────────────
  let _acctTemplate = null;   // cached template from API

  async function _loadAcctTemplate() {
    if (_acctTemplate !== null) return _acctTemplate;
    try {
      const r = await fetch('/api/accounts-template');
      _acctTemplate = await r.json();
    } catch { _acctTemplate = []; }
    return _acctTemplate;
  }

  async function _applyTemplateToEntry(entry) {
    // Only pre-populate if entry has no accounts yet
    if (entry.accounts?.length) return;
    const tmpl = await _loadAcctTemplate();
    if (!tmpl?.length) return;
    entry.accounts = tmpl.map(t => ({
      institution: t.institution || '',
      name:        t.name        || '',
      balance:     '',            // balance always starts blank — user fills daily
    }));
  }

  return {
    // Auth
    isUnlocked, setUnlocked, tryUnlock, lock, onEnter,
    // Data
    loadEntries, saveCard, addFromSb, askDel, closeConf,
    // Tags
    onTagKey, commitTag, removeTag,
    // Meals
    mealKey, mealFocus,
    // Rich text & emoji
    fmt, editorInput, editorKeydown, editorSelectionChange,
    toggleEmojiPicker, filterEmoji, insertEmoji,
    // Financial accounts
    toggleMask, addAccount, removeAccount, updateAccount,
    _acctDragStart, _acctDragOver, _acctDrop, _acctDragEnd,
    get _acctTemplate() { return _acctTemplate; },
    set _acctTemplate(v) { _acctTemplate = v; },
    // Timeline interaction
    buildTimeline, toggleYear, toggleMonth, toggle, jumpYear,
    // Search
    doSearch, clearSearch,
    // Upload / OCR
    openUpload, closeUpload, handleDrop, handleFiles, processOcr,
    // Internal (called from rendered HTML)
    _removeFromQueue,
  };

})();
