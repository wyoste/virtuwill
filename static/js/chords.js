/**
 * chords.js — reading, transposing and drawing guitar chords for the song pages.
 *
 *   VW.Chords.parse('F#m7/C#')      → { root: 'F#', suffix: 'm7', bass: 'C#' } or null
 *   VW.Chords.transpose('Bb', 2)    → 'C'
 *   VW.Chords.tokens('G-C x2 | D')  → [{chord:'G'}, {text:'-'}, {chord:'C'}, {text:' x2 '}, …]
 *   VW.Chords.inline('[G]Hello [C]there') → [{chord:'G', text:'Hello '}, {chord:'C', text:'there'}]
 *   VW.Chords.diagram('Am')         → an <svg> fingering, or null
 *
 * Diagrams come from common open shapes, else the lower of the E- and A-shape
 * barre chords. Chords without a shape of their own (add9, 6, 9…) are drawn as
 * the triad under them.
 * Exposes: window.VW.Chords
 */
'use strict';
window.VW = window.VW || {};

window.VW.Chords = (() => {
  const SHARPS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const FLATS = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
  const FLAT_KEYS = new Set(['F', 'Bb', 'Eb', 'Ab', 'Db', 'Gb', 'Dm', 'Gm', 'Cm', 'Fm', 'Bbm', 'Ebm']);
  const CHORD = /^([A-G])([#b]?)((?:maj|min|m|M|dim|aug|sus|add|[+°ø]|\d|[#b](?=\d)|\(|\))*)(?:\/([A-G][#b]?))?$/;

  function pitch(note) {
    const i = SHARPS.indexOf(note);
    return i >= 0 ? i : FLATS.indexOf(note);
  }

  function parse(name) {
    const m = CHORD.exec(String(name || '').trim());
    return m ? { root: m[1] + m[2], suffix: m[3], bass: m[4] || '' } : null;
  }

  // The shape a diagram is drawn from.
  function quality(suffix) {
    const s = suffix.replace(/[()]/g, '');
    if (/^(maj7|M7|maj9|M9)$/.test(s)) return 'maj7';
    if (/^(m7|min7|m9|m11)$/.test(s)) return 'm7';
    if (/^(dim|dim7|°|°7)$/.test(s)) return 'dim';
    if (/^(sus2)$/.test(s)) return 'sus2';
    if (/^(sus|sus4|7sus4|7sus)$/.test(s)) return 'sus4';
    if (/^(7|9|11|13)$/.test(s)) return '7';
    if (/^(m|min|m6|madd9|mmaj7)$/.test(s)) return 'm';
    return 'maj';          // '', maj, 6, add9, 5, aug…
  }

  function spell(index, flats) { return (flats ? FLATS : SHARPS)[((index % 12) + 12) % 12]; }

  /** A chord name moved by `steps` semitones; `flats` picks the spelling (default: as written). */
  function transpose(name, steps, flats) {
    const c = parse(name);
    if (!c || !steps) return name;
    const useFlats = flats ?? c.root.includes('b');
    return spell(pitch(c.root) + steps, useFlats) + c.suffix + (c.bass ? '/' + spell(pitch(c.bass) + steps, useFlats) : '');
  }

  /** The key moved by `steps`, and whether chords in it read better with flats. */
  function key(name, steps) {
    const m = /^([A-G][#b]?)\s*(m|min|minor)?/i.exec(String(name || '').trim());
    if (!m) return { name: name || '', flats: null };
    const root = m[1][0].toUpperCase() + m[1].slice(1);
    const minor = m[2] ? 'm' : '';
    let moved = steps ? spell(pitch(root) + steps, false) : root;
    if (steps && FLAT_KEYS.has(spell(pitch(root) + steps, true) + minor)) moved = spell(pitch(root) + steps, true);
    return { name: moved + minor, flats: FLAT_KEYS.has(moved + minor) || (!steps && root.includes('b')) };
  }

  /** A progression line split into chords and the text between them ("x3", "|", "Verse:"). */
  function tokens(line) {
    const out = [];
    for (const part of String(line).split(/(\s+|-|\||,)/)) {
      if (!part) continue;
      if (parse(part)) out.push({ chord: part });
      else if (out.length && out[out.length - 1].text !== undefined) out[out.length - 1].text += part;
      else out.push({ text: part });
    }
    return out;
  }

  const INLINE = /\[([^\]\s]{1,12})\]/g;
  function hasInline(text) { return /\[[A-G][^\]\s]{0,11}\]/.test(text || ''); }

  /** A ChordPro line ("[G]Hello [C]there") as chord-over-text pieces. */
  function inline(line) {
    const pieces = [];
    let last = 0, chord = '';
    for (const m of String(line).matchAll(INLINE)) {
      if (!parse(m[1])) continue;
      if (m.index > last || chord) pieces.push({ chord, text: line.slice(last, m.index) });
      chord = m[1];
      last = m.index + m[0].length;
    }
    pieces.push({ chord, text: line.slice(last) });
    return pieces.filter(p => p.chord || p.text);
  }

  /** The line's words without inline chords, for matching notes to lines. */
  function plain(line) { return String(line || '').replace(INLINE, ''); }

  // ── Shapes: strings low E → high e; -1 muted, 0 open ─────────────────────────
  const OPEN = {
    maj: { C: [-1, 3, 2, 0, 1, 0], D: [-1, -1, 0, 2, 3, 2], E: [0, 2, 2, 1, 0, 0], G: [3, 2, 0, 0, 0, 3], A: [-1, 0, 2, 2, 2, 0] },
    m: { Am: [-1, 0, 2, 2, 1, 0], Dm: [-1, -1, 0, 2, 3, 1], Em: [0, 2, 2, 0, 0, 0] },
    '7': { A7: [-1, 0, 2, 0, 2, 0], B7: [-1, 2, 1, 2, 0, 2], C7: [-1, 3, 2, 3, 1, 0], D7: [-1, -1, 0, 2, 1, 2], E7: [0, 2, 0, 1, 0, 0], G7: [3, 2, 0, 0, 0, 1] },
    m7: { Am7: [-1, 0, 2, 0, 1, 0], Dm7: [-1, -1, 0, 2, 1, 1], Em7: [0, 2, 0, 0, 0, 0] },
    maj7: { Amaj7: [-1, 0, 2, 1, 2, 0], Cmaj7: [-1, 3, 2, 0, 0, 0], Dmaj7: [-1, -1, 0, 2, 2, 2], Emaj7: [0, 2, 1, 1, 0, 0], Fmaj7: [-1, -1, 3, 2, 1, 0], Gmaj7: [3, 2, 0, 0, 0, 2] },
    sus2: { Asus2: [-1, 0, 2, 2, 0, 0], Dsus2: [-1, -1, 0, 2, 3, 0] },
    sus4: { Asus4: [-1, 0, 2, 2, 3, 0], Dsus4: [-1, -1, 0, 2, 3, 3], Esus4: [0, 2, 2, 2, 0, 0] },
    dim: {},
  };
  const SUFFIX = { maj: '', m: 'm', '7': '7', m7: 'm7', maj7: 'maj7', sus2: 'sus2', sus4: 'sus4', dim: 'dim' };
  // Offsets from the barre fret; null = muted.
  const E_SHAPE = { maj: [0, 2, 2, 1, 0, 0], m: [0, 2, 2, 0, 0, 0], '7': [0, 2, 0, 1, 0, 0], m7: [0, 2, 0, 0, 0, 0],
                    maj7: [0, null, 1, 1, 0, null], sus4: [0, 2, 2, 2, 0, 0], dim: [0, 1, 2, 0, null, null] };
  const A_SHAPE = { maj: [null, 0, 2, 2, 2, 0], m: [null, 0, 2, 2, 1, 0], '7': [null, 0, 2, 0, 2, 0], m7: [null, 0, 2, 0, 1, 0],
                    maj7: [null, 0, 2, 1, 2, 0], sus2: [null, 0, 2, 2, 0, 0], sus4: [null, 0, 2, 2, 3, 0], dim: [null, 0, 1, 2, 1, null] };

  /** Frets for a chord name: {frets, barre, shown} or null. */
  function shape(name) {
    const c = parse(name);
    if (!c) return null;
    const q = quality(c.suffix);
    const root = pitch(c.root);
    const shown = spell(root, c.root.includes('b')) + SUFFIX[q];
    for (const [n, frets] of Object.entries(OPEN[q])) {
      if (pitch(n.replace(SUFFIX[q], '')) === root) return { frets, barre: 0, shown };
    }
    const options = [];
    if (E_SHAPE[q]) options.push([(root - 4 + 12) % 12 || 12, E_SHAPE[q]]);
    if (A_SHAPE[q]) options.push([(root - 9 + 12) % 12 || 12, A_SHAPE[q]]);
    if (!options.length) return null;
    const [fret, offsets] = options.sort((a, b) => a[0] - b[0])[0];
    return { frets: offsets.map(o => o === null ? -1 : fret + o), barre: fret, shown };
  }

  const NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  }

  /** An SVG diagram for the chord, titled with its name. */
  function diagram(name) {
    const s = shape(name);
    if (!s) return null;
    const pressed = s.frets.filter(f => f > 0);
    const top = Math.max(...pressed, 1) <= 4 ? 1 : Math.min(...pressed);
    const rows = Math.max(4, Math.max(...pressed, top) - top + 1);
    const W = 88, x0 = 16, gap = 12, y0 = 22, fh = 16;
    const svg = el('svg', { viewBox: `0 0 ${W} ${y0 + rows * fh + 8}`, class: 'ch-svg', role: 'img', 'aria-label': `${name} chord: ` +
      s.frets.map(f => f < 0 ? 'x' : f).join(' ') });
    svg.append(el('title', {}, name + (s.shown !== name ? ` (shown as ${s.shown})` : '')));
    for (let i = 0; i < 6; i++) svg.append(el('line', { x1: x0 + i * gap, y1: y0, x2: x0 + i * gap, y2: y0 + rows * fh, class: 'ch-string' }));
    for (let r = 0; r <= rows; r++) svg.append(el('line', { x1: x0, y1: y0 + r * fh, x2: x0 + 5 * gap, y2: y0 + r * fh,
      class: r === 0 && top === 1 ? 'ch-nut' : 'ch-fret' }));
    if (top > 1) svg.append(el('text', { x: x0 + 5 * gap + 5, y: y0 + fh * 0.7, class: 'ch-pos' }, top + 'fr'));
    if (s.barre >= top && s.barre > 0) {
      const on = s.frets.map((f, i) => f === s.barre ? i : -1).filter(i => i >= 0);
      if (on.length > 1) svg.append(el('rect', { x: x0 + on[0] * gap - 5, y: y0 + (s.barre - top) * fh + 3, width: (on[on.length - 1] - on[0]) * gap + 10,
        height: fh - 6, rx: 5, class: 'ch-dot' }));
    }
    s.frets.forEach((f, i) => {
      const x = x0 + i * gap;
      if (f < 0) svg.append(el('text', { x, y: y0 - 6, class: 'ch-mark' }, '×'));
      else if (f === 0) svg.append(el('circle', { cx: x, cy: y0 - 9, r: 3.2, class: 'ch-open' }));
      else svg.append(el('circle', { cx: x, cy: y0 + (f - top) * fh + fh / 2, r: 4.6, class: 'ch-dot' }));
    });
    return svg;
  }

  return { parse, transpose, key, tokens, inline, hasInline, plain, shape, diagram };
})();
