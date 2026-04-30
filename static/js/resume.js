/**
 * resume.js — VirtuWill Resume + Portfolio module
 *
 * Portfolio management:
 *   - Projects rendered dynamically from PROJECTS data store
 *   - Each project has a `visible` flag (default true)
 *   - Admin controls: toggle slider (show/hide) + trash icon (delete with confirm)
 *   - Admin state read from window.VW.Auth.isAdmin()
 *   - Persistence via /api/portfolio (localStorage fallback if API absent)
 */

'use strict';
window.VW = window.VW || {};

window.VW.Resume = (() => {

  // ── Project data ────────────────────────────────────────────────────────────
  // `visible` = shown to public visitors. `deleted` = removed for everyone incl admin.
  const DEFAULT_PROJECTS = [
    {
      id: 'dmp',
      tag: 'Data platform', tagClass: 'data',
      title: 'Greystar DataMarketplace (DMP)',
      desc: 'Databricks lakehouse with gold/silver/bronze layers serving 40+ property management domains across 2,000+ properties.',
      chips: ['Databricks', 'ADF', 'Snowflake'],
      subtitle: "A production Databricks lakehouse powering Greystar's complete property management data estate — from raw source ingestion through curated gold entities consumed by 50+ stakeholders.",
      metrics: [{ val:'$6M', lbl:'Annual savings' }, { val:'2,000+', lbl:'Properties' }, { val:'50+', lbl:'Stakeholders' }],
      overview: 'Led migration and reconciliation of the Greystar Analytics Reporting Schema onto Databricks. Delivered an internal payroll calculation portal estimated to save $6M annually. Redesigned Databricks platform security for granular, scalable permission grants.',
      timeline: [
        { period:'2025', phase:'Analytics migration', desc:'Led full migration of Greystar Analytics Reporting Schema onto Databricks, reconciling legacy data structures.' },
        { period:'2025', phase:'Payroll portal', desc:'Delivered internal payroll calculation portal replacing an external vendor — estimated $6M annual savings.' },
        { period:'2025', phase:'Security redesign', desc:'Redesigned Databricks platform security for granular, scalable permission grants across data engineering pods.' },
        { period:'2025', phase:'Source integrations', desc:'Integrated Yardi Canada PMS, Yardi Investment Management EU, and Anaplan Budgeting into the data warehouse.' },
      ],
      visible: true,
    },
    {
      id: 'snc',
      tag: 'Regulatory', tagClass: 'analytics',
      title: 'SNC Federal Regulatory Automation',
      desc: 'Automated the Shared National Credit federal regulatory reporting process at Texas Capital, eliminating manual effort and reducing compliance risk.',
      chips: ['Snowflake', 'Azure', 'SQL'],
      subtitle: 'Automated the Shared National Credit federal regulatory reporting process at Texas Capital, eliminating manual effort and reducing compliance risk.',
      metrics: [{ val:'100%', lbl:'Process automated' }, { val:'Federal', lbl:'Regulatory compliance' }, { val:'0', lbl:'Manual steps remaining' }],
      overview: 'Migrated the Credit Datamart from Azure Cloud to Snowflake and automated the SNC regulatory reporting pipeline. Also led integration of Private Wealth Credit data into the Supernova platform for unified portfolio management.',
      timeline: [
        { period:'2023', phase:'Datamart migration', desc:'Migrated Credit Datamart from Azure to Snowflake, establishing clean medallion architecture.' },
        { period:'2024', phase:'SNC automation', desc:'Automated SNC Federal Regulatory Report — eliminating manual quarterly reporting process.' },
        { period:'2024', phase:'Supernova integration', desc:'Integrated Private Wealth Credit data into Supernova, giving Wealth Managers a unified portfolio view.' },
      ],
      visible: true,
    },
    {
      id: 'virtuwill',
      tag: 'Personal project', tagClass: 'personal',
      title: 'VirtuWill Application',
      desc: 'This site — modular Flask SPA with a private journal, music portfolio, illustrated garden planner, and local LLaVA chat agent.',
      chips: ['Flask', 'Ollama', 'Vanilla JS'],
      subtitle: 'A personal digital dashboard — modular Flask SPA with a private journal, music portfolio, garden planner with satellite imagery, and locally-run LLaVA chat agent.',
      metrics: [{ val:'6', lbl:'App modules' }, { val:'0', lbl:'npm dependencies' }, { val:'14', lbl:'Garden beds mapped' }],
      overview: 'Built from scratch as a personal platform — zero build tooling, modular IIFE architecture, satellite+illustrated aerial garden planner with plat-calibrated bed polygons, season snapshots, and a 30-plant inventory with space-requirement circles.',
      timeline: [
        { period:'Phase 1', phase:'Flask SPA scaffold', desc:'Refactored from React prototype to Flask with session-based journal auth and modular JS namespace pattern.' },
        { period:'Phase 2', phase:'Garden planner', desc:'Canvas-based property map with satellite imagery, 3" dot grid, highlighter boundary tool, and plant placement.' },
        { period:'Phase 3', phase:'Chat with Will', desc:'Integrated LLaVA via Ollama with FastAPI proxy and persona grounding for an on-brand chat agent.' },
        { period:'Phase 4', phase:'AI coursework', desc:'Built multi-modal conversational agent on Google Vertex AI for UTD Special Topics: Generative AI Fundamentals.' },
      ],
      visible: true,
    },
    {
      id: 'drm',
      tag: 'Nonprofit tech', tagClass: 'platform',
      title: 'AHA Donor Revenue Management',
      desc: 'Integrated donation management systems, migrated the Direct Mail platform, and delivered digital assets for 3 national peer-to-peer campaigns at the American Heart Association.',
      chips: ['Boomi ETL', 'Salesforce', 'Data Warehouse'],
      subtitle: 'Integrated new donation management systems, migrated the Direct Mail platform, and delivered digital assets for 3 national peer-to-peer fundraising campaigns at American Heart Association.',
      metrics: [{ val:'3', lbl:'National campaigns' }, { val:'1', lbl:'DMS migration' }, { val:'1', lbl:'New virtual platform' }],
      overview: 'Led migration of the Direct Mail Donation Management System, delivered Go Red for Women / Women of Impact / Teen of Impact digital assets, and onboarded Bizzabo virtual event software with full DWH integration.',
      timeline: [
        { period:'2020', phase:'DMS migration', desc:'Led migration of Direct Mail Donation Management System with zero data loss.' },
        { period:'2021', phase:'Campaign delivery', desc:'Delivered digital assets for 3 national Peer-to-Peer Fundraising Campaigns.' },
        { period:'2022', phase:'Virtual events', desc:'Onboarded Bizzabo and integrated campaign data into DRM application and Data Warehouse.' },
      ],
      visible: true,
    },
  ];

  // ── Runtime state ───────────────────────────────────────────────────────────
  let _projects    = [];   // live copy loaded from persistence or defaults
  let _activeTab   = 'ethos';
  let _pendingDel  = null; // id of project awaiting delete confirmation

  // ── Persistence ─────────────────────────────────────────────────────────────
  // Uses localStorage as a lightweight store (no extra API endpoint needed).
  // Key: 'vw_portfolio_state' → { [id]: { visible, deleted } }
  const STORE_KEY = 'vw_portfolio_state';

  function _loadProjects() {
    // Start with a deep copy of defaults
    _projects = DEFAULT_PROJECTS.map(p => ({ ...p }));
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) {
        const state = JSON.parse(raw);
        _projects.forEach(p => {
          if (state[p.id] !== undefined) {
            p.visible = state[p.id].visible ?? p.visible;
            p.deleted = state[p.id].deleted ?? false;
          }
        });
      }
    } catch { /* ignore */ }
    _projects = _projects.filter(p => !p.deleted);
  }

  // Fetch server-uploaded HTML projects and merge into _projects
  async function _mergeUploads() {
    try {
      const r = await fetch('/api/portfolio/uploads');
      const uploads = await r.json();
      // Remove any existing uploaded entries, re-add fresh
      _projects = _projects.filter(p => !p._uploaded);
      uploads.forEach(u => {
        if (!_projects.find(p => p.id === u.id)) {
          _projects.push({
            id: u.id, title: u.title, tag: u.tag || 'Project', tagClass: 'platform',
            desc: u.desc || '', chips: [], visible: u.visible ?? true,
            _uploaded: true, _url: u.url, _filename: u.filename,
          });
        }
      });
    } catch { /* server not available */ }
  }

  function _saveState() {
    try {
      const state = {};
      _projects.forEach(p => { state[p.id] = { visible: p.visible, deleted: false }; });
      // Also store deleted ids
      DEFAULT_PROJECTS.forEach(p => {
        if (!_projects.find(lp => lp.id === p.id)) {
          state[p.id] = { visible: false, deleted: true };
        }
      });
      localStorage.setItem(STORE_KEY, JSON.stringify(state));
    } catch { /* ignore */ }
  }

  // ── Portfolio grid rendering ────────────────────────────────────────────────
  async function renderPortfolio() {
    _loadProjects();
    await _mergeUploads();
    const grid = document.getElementById('projects-grid-dynamic');
    if (!grid) return;

    const isAdmin = window.VW?.Auth?.isAdmin?.() || false;

    // Show admin upload button
    const upBtn = document.getElementById('portfolio-admin-upload');
    if (upBtn) upBtn.style.display = isAdmin ? 'block' : 'none';

    const visible = _projects.filter(p => isAdmin || p.visible);

    if (!visible.length) {
      grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;color:var(--text3);padding:48px 0;font-size:14px;">No projects to display.</div>';
      return;
    }

    grid.innerHTML = visible.map(p => _projectCard(p, isAdmin)).join('');
  }

  function _projectCard(p, isAdmin) {
    const dimmed = !p.visible ? 'proj-card-hidden' : '';

    const adminControls = isAdmin ? `
      <div class="proj-admin-bar" onclick="event.stopPropagation()">
        <div class="proj-toggle-wrap" title="${p.visible ? 'Hide from visitors' : 'Show to visitors'}">
          <label class="toggle-switch">
            <input type="checkbox" ${p.visible ? 'checked' : ''}
              onchange="VW.Resume.toggleVisibility('${p.id}', this.checked)"/>
            <span class="toggle-slider"></span>
          </label>
          <span class="toggle-label">${p.visible ? 'Visible' : 'Hidden'}</span>
        </div>
        <button class="proj-delete-btn" onclick="VW.Resume.confirmDelete('${p.id}')" title="Delete project">
          &#128465;
        </button>
      </div>` : '';

    return `
      <div class="proj-card ${dimmed}" id="proj-card-${p.id}" onclick="VW.Resume.${p._uploaded ? 'showUploadedProject' : 'showProject'}('${p.id}')">
        ${adminControls}
        <div class="proj-tag ${p.tagClass}">${p.tag}</div>
        <div class="proj-name">${p.title}</div>
        <div class="proj-desc">${p.desc}</div>
        <div class="proj-meta">
          <div class="proj-tech">${p.chips.map(c => `<span class="tech-pill">${c}</span>`).join('')}</div>
          <span class="proj-arrow">›</span>
        </div>
        ${!p.visible && isAdmin ? '<div class="proj-hidden-badge">Hidden from visitors</div>' : ''}
      </div>`;
  }

  // ── Toggle visibility ───────────────────────────────────────────────────────
  function toggleVisibility(id, visible) {
    const p = _projects.find(p => p.id === id);
    if (!p) return;
    p.visible = visible;
    _saveState();

    // Update the card in place without full re-render
    const card = document.getElementById('proj-card-' + id);
    if (card) {
      card.classList.toggle('proj-card-hidden', !visible);
      // Update toggle label
      const lbl = card.querySelector('.toggle-label');
      if (lbl) lbl.textContent = visible ? 'Visible' : 'Hidden';
      // Update hidden badge
      const badge = card.querySelector('.proj-hidden-badge');
      if (badge) badge.style.display = visible ? 'none' : 'block';
      if (!badge && !visible) {
        card.insertAdjacentHTML('beforeend', '<div class="proj-hidden-badge">Hidden from visitors</div>');
      }
    }

    window.toast?.(visible ? 'Project visible to visitors' : 'Project hidden from visitors', 'success');
  }

  // ── Delete confirmation ─────────────────────────────────────────────────────
  function confirmDelete(id) {
    _pendingDel = id;
    const p = _projects.find(p => p.id === id);
    if (!p) return;

    const modal = document.getElementById('proj-delete-modal');
    const title = document.getElementById('proj-delete-title');
    if (title) title.textContent = `Delete "${p.title}"?`;
    if (modal) modal.classList.add('open');
  }

  function cancelDelete() {
    _pendingDel = null;
    document.getElementById('proj-delete-modal')?.classList.remove('open');
  }

  function executeDelete() {
    const id = _pendingDel;
    if (!id) return;
    _pendingDel = null;

    // Remove from runtime list
    _projects = _projects.filter(p => p.id !== id);
    _saveState();

    // Animate out the card
    const card = document.getElementById('proj-card-' + id);
    if (card) {
      card.style.transition = 'opacity .3s, transform .3s';
      card.style.opacity = '0';
      card.style.transform = 'scale(0.92)';
      setTimeout(() => { card.remove(); }, 320);
    }

    document.getElementById('proj-delete-modal')?.classList.remove('open');
    window.toast?.('Project deleted', 'success');
  }

  // ── Project detail page ─────────────────────────────────────────────────────
  function showProject(id) {
    const p = _projects.find(p => p.id === id);
    if (!p) return;

    // Hide iframe when showing a built-in project detail
    const iframe = document.getElementById('project-iframe');
    if (iframe) { iframe.src = ''; iframe.style.display = 'none'; }

    const wrap = document.getElementById('detail-wrap');
    if (!wrap) return;
    wrap.style.display = 'block';

    wrap.innerHTML = `
      <div class="detail-hero">
        <div class="detail-tag">${p.tag}</div>
        <div class="detail-title">${p.title}</div>
        <div class="detail-subtitle">${p.subtitle}</div>
        <div class="detail-chips">
          ${p.chips.map(c => `<span class="detail-chip">${c}</span>`).join('')}
        </div>
      </div>
      <div class="detail-body">
        <div class="detail-section">
          <div class="detail-sec-title">At a glance</div>
          <div class="detail-metrics">
            ${p.metrics.map(m => `
              <div class="detail-metric">
                <div class="dm-val">${m.val}</div>
                <div class="dm-lbl">${m.lbl}</div>
              </div>`).join('')}
          </div>
        </div>
        <div class="detail-section">
          <div class="detail-sec-title">Overview</div>
          <div class="detail-text">${p.overview}</div>
        </div>
        <div class="detail-section">
          <div class="detail-sec-title">How it came together</div>
          <div class="detail-timeline">
            ${p.timeline.map(s => `
              <div class="tl-step">
                <div class="tl-left"><div class="tl-dot"></div><div class="tl-line"></div></div>
                <div class="tl-content">
                  <div class="tl-period">${s.period}</div>
                  <div class="tl-phase">${s.phase}</div>
                  <div class="tl-desc">${s.desc}</div>
                </div>
              </div>`).join('')}
          </div>
        </div>
      </div>`;

    wrap.scrollTop = 0;
    window.go('project');
    document.getElementById('portfolio-btn')?.classList.add('on');
    _setBreadcrumb(['Resume', 'Portfolio', p.title]);
  }

  // ── Resume sidebar tabs ─────────────────────────────────────────────────────
  function setTab(tab) {
    _activeTab = tab;
    document.querySelectorAll('.rs-content-section').forEach(el => {
      el.classList.toggle('active', el.id === 'rs-' + tab);
    });
    document.querySelectorAll('.rs-item[data-tab]').forEach(el => {
      el.classList.toggle('on', el.dataset.tab === tab);
    });
  }

  function showPortfolio() {
    window.go('portfolio');
    document.getElementById('portfolio-btn')?.classList.add('on');
    _setBreadcrumb(['Resume', 'Portfolio']);
    // Render grid when navigating to portfolio
    requestAnimationFrame(renderPortfolio);
  }

  function _setBreadcrumb(parts) { window.VW.Nav?.setBreadcrumb(parts); }

  function onEnter()          { setTab(_activeTab); }
  function onEnterPortfolio() {
    document.getElementById('portfolio-btn')?.classList.add('on');
    _setBreadcrumb(['Resume', 'Portfolio']);
    requestAnimationFrame(renderPortfolio);
  }
  function onLeave()          { document.getElementById('portfolio-btn')?.classList.remove('on'); }

  // ── Uploaded HTML project display ───────────────────────────────────────────
  function showUploadedProject(id) {
    const p = _projects.find(p => p.id === id);
    if (!p || !p._url) return;
    const iframe  = document.getElementById('project-iframe');
    const detWrap = document.getElementById('detail-wrap');
    if (iframe)  { iframe.src = p._url; iframe.style.display = 'block'; }
    if (detWrap) detWrap.style.display = 'none';
    window.go('project');
    document.getElementById('portfolio-btn')?.classList.add('on');
    _setBreadcrumb(['Resume', 'Portfolio', p.title]);
  }

  // ── Upload modal ──────────────────────────────────────────────────────────────
  let _uploadFile = null;

  function openUpload() {
    _uploadFile = null;
    ['pu-title','pu-tag','pu-desc'].forEach(id => {
      const el = document.getElementById(id); if (el) el.value = '';
    });
    document.getElementById('pu-file-name').textContent = '';
    document.getElementById('pu-error').textContent     = '';
    document.getElementById('portfolio-upload-modal').style.display = 'flex';
  }

  function closeUpload() {
    document.getElementById('portfolio-upload-modal').style.display = 'none';
    _uploadFile = null;
  }

  function fileChosen(files) {
    if (files && files.length) {
      _uploadFile = files[0];
      document.getElementById('pu-file-name').textContent = files[0].name;
      const titleEl = document.getElementById('pu-title');
      if (titleEl && !titleEl.value) {
        titleEl.value = files[0].name
          .replace(/\.html?$/i, '')
          .replace(/[-_]/g, ' ')
          .replace(/\b\w/g, c => c.toUpperCase());
      }
    }
  }

  function handleUploadDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('over');
    fileChosen(e.dataTransfer.files);
  }

  async function submitUpload() {
    const errEl = document.getElementById('pu-error');
    if (errEl) errEl.textContent = '';
    if (!_uploadFile) {
      if (errEl) errEl.textContent = 'Please choose an HTML file.'; return;
    }
    const fd = new FormData();
    fd.append('file',  _uploadFile);
    fd.append('title', document.getElementById('pu-title')?.value || _uploadFile.name);
    fd.append('tag',   document.getElementById('pu-tag')?.value   || 'Project');
    fd.append('desc',  document.getElementById('pu-desc')?.value  || '');
    try {
      const r = await fetch('/api/portfolio/upload', { method:'POST', body: fd });
      const d = await r.json();
      if (d.ok) {
        closeUpload();
        await renderPortfolio();
        window.toast?.('Project uploaded \u2713', 'success');
      } else {
        if (errEl) errEl.textContent = d.error || 'Upload failed.';
      }
    } catch {
      if (errEl) errEl.textContent = 'Upload error \u2014 check connection.';
    }
  }

  // ── Public API ──────────────────────────────────────────────────────────────
  return {
    setTab,
    showPortfolio,
    showProject,
    showUploadedProject,
    toggleVisibility,
    confirmDelete,
    cancelDelete,
    executeDelete,
    onEnter,
    onEnterPortfolio,
    onLeave,
    renderPortfolio,
    openUpload,
    closeUpload,
    fileChosen,
    handleUploadDrop,
    submitUpload,
  };

})();
