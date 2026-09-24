// Site › Music: songs (the compositions) and their versions (recordings),
// plus audio files not yet attached to a song.
import { h, api, fmt, card, pageHead, empty, toast, run, dialog, field, values, confirmDelete } from '../lib.js';
import { setDirty } from '../main.js';

const SECTION_TYPES = ['verse', 'chorus', 'pre-chorus', 'bridge', 'intro', 'solo', 'outro', 'coda', 'full'];

export async function render(view, { path, navigate }) {
  const songId = path.split('/')[4];
  return songId ? songEditor(view, songId, navigate) : overview(view, navigate);
}

async function overview(view, navigate) {
  const d = await api('/api/v1/music?view=owner');
  const redraw = () => { view.replaceChildren(); overview(view, navigate); };
  const upload = h('input', { type: 'file', accept: 'audio/*', hidden: true });
  const albumName = h('input', { class: 'ws-input', placeholder: 'Album folder (optional)', 'aria-label': 'Album for the upload', style: { width: '200px' } });
  upload.onchange = () => run(null, async () => {
    const form = new FormData();
    form.append('file', upload.files[0]);
    form.append('album', albumName.value.trim());
    await api('/api/music/upload', { method: 'POST', form });
    toast('Uploaded — attach it to a song below');
    redraw();
  });
  const newSong = h('button', { class: 'btn primary', onclick: () => createSong(null, navigate) }, '+ New song');
  const suggest = d.unassigned.length ? h('button', { class: 'btn', onclick: () => suggestSongs(d, redraw) }, 'Suggest songs') : null;

  view.append(pageHead('Music', 'Songs are what visitors browse; each version is an audio file.', albumName,
    h('button', { class: 'btn', onclick: () => upload.click() }, 'Upload audio'), upload, suggest, newSong));

  // Unassigned audio: the inbox.
  const songOptions = [['', 'Attach to…'], ...d.songs.map(s => [s.song_id, s.title]), ['__new', '+ New song from this file']];
  view.append(card(h('span', {}, `Audio not attached to a song (${d.unassigned.length})`,
      h('span', { class: 'ws-note' }, 'Still playable in its album on the public page')),
    d.unassigned.length ? h('ul', { class: 'ws-list' }, d.unassigned.map(r => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, r.title),
        h('div', { class: 'ws-row-meta' }, [r.album || 'single', r.path.split('/').pop()].join(' · '))),
      h('div', { class: 'ws-row-end' },
        h('select', { class: 'ws-input', 'aria-label': 'Attach ' + r.title + ' to a song', onchange: async e => {
          const value = e.target.value;
          if (!value) return;
          if (value === '__new') return createSong(r, navigate);
          await run(e.target, async () => {
            await api('/api/v1/music/recordings/' + r.recording_id, { method: 'PUT', body: { song_id: value } });
            toast('Attached'); redraw();
          });
        } }, songOptions.map(([v, t]) => h('option', { value: v }, t)))))))
      : empty('Every audio file belongs to a song.')));

  // Songs
  view.append(card(`Songs (${d.songs.length})`, d.songs.length ? h('ul', { class: 'ws-list' }, d.songs.map(s => h('li', { class: 'ws-row ws-song-row' },
    h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, h('a', { href: '/app/site/music/' + s.song_id }, s.title)),
      h('div', { class: 'ws-row-meta' }, [s.year_written, `${s.versions.length} version${s.versions.length === 1 ? '' : 's'}`,
        s.has_lyrics ? 'lyrics ✓' : 'no lyrics', s.story ? 'story ✓' : null].filter(Boolean).join(' · '))),
    h('div', { class: 'ws-row-end' }, h('span', { class: 'ws-chip ' + (s.published ? 'good' : '') }, s.published ? 'Live' : 'Draft'),
      h('a', { class: 'btn small', href: '/app/site/music/' + s.song_id }, 'Edit'),
      s.published ? h('a', { class: 'btn small link', href: '/music/' + s.slug, target: '_blank' }, 'View ↗') : null)))) : empty('No songs yet.')));

  // Albums
  view.append(card('Albums', d.albums.length ? h('ul', { class: 'ws-list' }, d.albums.map(a => h('li', { class: 'ws-row' },
    h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, a.title), h('div', { class: 'ws-row-meta' }, `${a.tracks.length} tracks${a.year ? ' · ' + a.year : ''}`)),
    h('div', { class: 'ws-row-end' }, h('span', { class: 'ws-chip ' + (a.published ? 'good' : '') }, a.published ? 'Live' : 'Hidden'),
      h('button', { class: 'btn small', onclick: () => editAlbum(a, redraw) }, 'Edit'))))) : empty('No albums.')));
}

async function createSong(recording, navigate) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Title', 'title', { value: recording ? tidy(recording.title) : '', required: true, wide: true }),
    field('Year written', 'year_written', { kind: 'number', value: recording?.release_year ?? '' }));
  const choice = await dialog(recording ? 'New song from this file' : 'New song', form, [['Cancel', null], ['Create', true]]);
  if (!choice) return;
  await run(null, async () => {
    const body = { ...values(form), recording_id: recording?.recording_id };
    const song = await api('/api/v1/music/songs', { method: 'POST', body });
    navigate('/app/site/music/' + song.song_id);
  });
}

// "03 - Aint it Funny" → "Aint it Funny"; "voodoo_2025 - 12:26:25" → "voodoo 2025"
function tidy(name) {
  return name.replace(/^\d+\s*[-.]\s*/, '').replace(/\s*-\s*\d+[\s:_]\d+.*$/, '').replace(/[_]+/g, ' ').trim();
}

// Versions of one song usually share a name once years, "version", "mix" and
// takes are dropped: "Chica (2025)" and "chica demo" → "chica".
function songKey(title) {
  return tidy(title).toLowerCase().replace(/\(.*?\)/g, ' ').replace(/\b(19|20)\d\d\b/g, ' ')
    .replace(/\b(version|v\d+|mix\d*|epmix|\d*st\d*|demo|master|new|instrumental|take \d+|bb)\b/g, ' ')
    .replace(/(19|20)\d\depmix|epmix|mix\d*$/g, '').replace(/[^a-z]+/g, '');
}

async function suggestSongs(d, redraw) {
  const existing = new Map(d.songs.map(s => [songKey(s.title), s]));
  const groups = new Map();
  for (const r of d.unassigned) {
    const key = songKey(r.title) || r.title.toLowerCase();
    if (!groups.has(key)) groups.set(key, { title: titleCase(tidy(r.title).replace(/\((19|20)\d\d\)|\b(19|20)\d\d\b.*$/g, '').replace(/\s+(version|epmix|mix)\b.*$/i, '').replace(/(19|20)\d\d(ep)?mix.*$/i, '').replace(/\s*\(\d+\)$/, '').trim() || r.title),
                                           song: existing.get(key), files: [] });
    groups.get(key).files.push(r);
  }
  const rows = [...groups.values()];
  const picks = rows.map(() => true);
  const list = h('ul', { class: 'ws-list', style: { maxHeight: '55vh', overflow: 'auto' } }, rows.map((g, i) => h('li', { class: 'ws-row' },
    h('label', { class: 'ws-check' }, h('input', { type: 'checkbox', checked: true, onchange: e => { picks[i] = e.target.checked; } }),
      h('span', {}, h('strong', {}, g.song ? g.song.title : g.title), g.song ? ' (existing song)' : ' (new song)')),
    h('span', { class: 'ws-row-meta' }, g.files.map(f => f.title).join(' · ')))));
  const ok = await dialog('Group audio into songs',
    h('div', {}, h('p', { class: 'ws-note' }, 'Files with the same name become versions of one song. New songs start as drafts; untick anything that looks wrong.'), list),
    [['Cancel', null], ['Create', true]]);
  if (!ok) return;
  await run(null, async () => {
    for (const [i, g] of rows.entries()) {
      if (!picks[i]) continue;
      let songId = g.song?.song_id;
      if (!songId) songId = (await api('/api/v1/music/songs', { method: 'POST', body: { title: g.title }, quiet: true })).song_id;
      for (const f of g.files) {
        await api('/api/v1/music/recordings/' + f.recording_id, { method: 'PUT', body: { song_id: songId }, quiet: true });
      }
    }
    toast('Songs created — review each one and publish it');
    redraw();
  });
}

function titleCase(s) { return s.replace(/\b([a-z])/g, c => c.toUpperCase()); }

async function editAlbum(album, redraw) {
  const form = h('form', { class: 'ws-form', onsubmit: e => e.preventDefault() },
    field('Title', 'title', { value: album.title, wide: true }), field('Year', 'release_year', { kind: 'number', value: album.year ?? '' }),
    field('Show on the public Music page', 'published', { kind: 'checkbox', value: album.published }));
  if (!(await dialog('Edit album', form, [['Cancel', null], ['Save', true]]))) return;
  await run(null, async () => { await api('/api/v1/music/albums/' + encodeURIComponent(album.album_id), { method: 'PUT', body: values(form) }); redraw(); });
}

// ── One song ─────────────────────────────────────────────────────────────────
async function songEditor(view, songId, navigate) {
  const [song, page] = await Promise.all([api(`/api/v1/music/songs/${songId}?view=owner`), api('/api/v1/music?view=owner')]);
  let dirty = false;
  setDirty(() => dirty);
  const form = h('form', { class: 'ws-form', oninput: () => { dirty = true; } },
    field('Title', 'title', { value: song.title, required: true, wide: true }),
    field('Year written', 'year_written', { kind: 'number', value: song.year_written ?? '' }),
    field('Where', 'written_at', { value: song.written_at, placeholder: 'e.g. Oxford, MS' }),
    field('Genre', 'genre', { value: song.genre }), field('Key', 'musical_key', { value: song.musical_key }),
    field('BPM', 'bpm', { kind: 'number', value: song.bpm ?? '' }),
    field('The story behind it', 'story', { kind: 'textarea', value: song.story, wide: true }),
    field('Published (visible on the public site)', 'published', { kind: 'checkbox', value: song.published }));

  const sections = (song.sections || []).map(s => ({ ...s }));
  const sectionHost = h('div', { class: 'ws-form stack' });
  const drawSections = () => sectionHost.replaceChildren(...sections.map((s, i) => h('div', { class: 'ws-card', style: { padding: '12px' } },
    h('div', { class: 'ws-form' },
      h('label', { class: 'ws-field' }, h('span', {}, 'Section'), h('select', { onchange: e => { s.section_type = e.target.value; dirty = true; } },
        SECTION_TYPES.map(t => h('option', { value: t, selected: t === s.section_type }, t)))),
      h('label', { class: 'ws-field' }, h('span', {}, 'Label'), h('input', { value: s.label, oninput: e => { s.label = e.target.value; dirty = true; } })),
      h('div', { class: 'ws-row-end' },
        h('button', { class: 'btn small', type: 'button', disabled: i === 0, 'aria-label': 'Move up', onclick: () => { sections.splice(i - 1, 0, sections.splice(i, 1)[0]); dirty = true; drawSections(); } }, '↑'),
        h('button', { class: 'btn small danger', type: 'button', 'aria-label': 'Remove section', onclick: () => { sections.splice(i, 1); dirty = true; drawSections(); } }, '✕'))),
    h('div', { class: 'ws-form' },
      h('label', { class: 'ws-field' }, h('span', {}, 'Chords'), h('textarea', { rows: 3, oninput: e => { s.chords = e.target.value; dirty = true; } }, s.chords)),
      h('label', { class: 'ws-field' }, h('span', {}, 'Lyrics'), h('textarea', { rows: 5, oninput: e => { s.lyrics = e.target.value; dirty = true; } }, s.lyrics)),
      h('label', { class: 'ws-field wide' }, h('span', {}, 'Tabs'), h('textarea', { rows: 3, style: { fontFamily: 'monospace' }, oninput: e => { s.tabs = e.target.value; dirty = true; } }, s.tabs))))),
    h('div', {}, h('button', { class: 'btn small', type: 'button', onclick: () => { sections.push({ section_type: 'verse', label: '', chords: '', lyrics: '', tabs: '' }); dirty = true; drawSections(); } }, '+ Section')));
  drawSections();

  const save = h('button', { class: 'btn primary' }, 'Save song');
  save.onclick = () => run(save, async () => {
    const body = { ...values(form), sections };
    await api('/api/v1/music/songs/' + songId, { method: 'PUT', body });
    dirty = false;
    toast('Song saved');
  });
  const del = h('button', { class: 'btn danger', onclick: async () => {
    if (!(await confirmDelete('this song (its audio files stay, unattached)'))) return;
    await run(null, async () => { await api('/api/v1/music/songs/' + songId, { method: 'DELETE' }); dirty = false; navigate('/app/site/music'); });
  } }, 'Delete');

  const attach = h('select', { class: 'ws-input', 'aria-label': 'Attach an audio file as a version', onchange: e => run(e.target, async () => {
    if (!e.target.value) return;
    await api('/api/v1/music/recordings/' + e.target.value, { method: 'PUT', body: { song_id: songId } });
    view.replaceChildren(); songEditor(view, songId, navigate);
  }) }, [h('option', { value: '' }, 'Add a version from unattached audio…'),
         ...page.unassigned.map(r => h('option', { value: r.recording_id }, `${r.title} (${r.album || 'single'})`))]);

  view.append(
    pageHead(song.title, h('span', {}, 'Song · ', h('a', { href: '/app/site/music' }, 'all songs')), del,
      song.published ? h('a', { class: 'btn', href: '/music/' + song.slug, target: '_blank' }, 'View ↗') : null, save),
    h('div', { class: 'ws-grid two' },
      card('Details', form),
      card(`Versions (${song.versions.length})`,
        song.versions.length ? h('ul', { class: 'ws-list' }, song.versions.map(v => versionRow(v, () => { view.replaceChildren(); songEditor(view, songId, navigate); })))
                             : empty('No audio yet. Attach a file below.'),
        h('div', { style: { marginTop: '10px' } }, attach))),
    card('Lyrics, chords and tabs by section', sectionHost));
}

function versionRow(v, redraw) {
  const label = h('input', { class: 'ws-input', value: v.version_label, placeholder: 'e.g. 2025 version, demo', 'aria-label': 'Version label', style: { width: '180px' } });
  const pub = h('input', { type: 'checkbox', checked: v.published, 'aria-label': 'Published' });
  const saveV = () => run(null, async () => {
    await api('/api/v1/music/recordings/' + v.recording_id, { method: 'PUT', body: { version_label: label.value, published: pub.checked } });
  });
  label.onchange = saveV; pub.onchange = saveV;
  return h('li', { class: 'ws-row', style: { flexWrap: 'wrap' } },
    h('div', { class: 'ws-row-main' }, h('div', { class: 'ws-row-title' }, v.title), h('div', { class: 'ws-row-meta' }, v.album || 'single'),
      h('audio', { controls: true, preload: 'none', src: v.url })),
    h('div', { class: 'ws-row-end' }, label, h('label', { class: 'ws-check' }, pub, h('span', {}, 'Public')),
      h('button', { class: 'btn small', onclick: () => run(null, async () => {
        await api('/api/v1/music/recordings/' + v.recording_id, { method: 'PUT', body: { song_id: null } }); redraw();
      }) }, 'Detach')));
}
