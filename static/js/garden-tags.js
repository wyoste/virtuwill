'use strict';
window.VW = window.VW || {};
VW.GardenTags = (() => {
  const normalize = text => String(text).replace(/#/g, '').trim().toLowerCase()
    .replace(/[^\p{L}\p{N}_\s-]/gu, '').replace(/[\s-]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
  function pill(text, onclick) {
    const el = document.createElement(onclick ? 'button' : 'span');
    el.className = 'garden-tag'; el.textContent = '#' + normalize(text);
    if (onclick) { el.type = 'button'; el.onclick = onclick; }
    return el;
  }
  function editor(initial = []) {
    let tags = [...new Set(initial.map(normalize).filter(Boolean))];
    const el = document.createElement('div'); el.className = 'ws-field wide';
    const label = document.createElement('label'); label.textContent = 'Additional tags';
    const row = document.createElement('div'); row.className = 'garden-tags';
    const input = document.createElement('input'); input.type = 'text'; input.className = 'ws-input';
    input.placeholder = 'e.g. spring-blooms — Enter to add'; input.setAttribute('aria-label', 'New tag'); input.maxLength = 64;
    const state = document.createElement('input'); state.type = 'hidden';
    const draw = () => {
      row.replaceChildren(...tags.map(tag => {
        const button = pill(tag, () => { tags = tags.filter(t => t !== tag); draw(); });
        button.textContent += ' ×'; button.setAttribute('aria-label', 'Remove tag ' + tag); return button;
      }));
      state.value = JSON.stringify(tags); state.dispatchEvent(new Event('input', { bubbles: true }));
    };
    const commit = () => {
      const tag = normalize(input.value);
      if (tag && !tags.includes(tag) && tags.length < 30) tags.push(tag);
      input.value = ''; draw();
    };
    input.onbeforeinput = e => { if (e.data === '#') e.preventDefault(); };
    input.oninput = () => { input.value = input.value.replace(/#/g, ''); };
    input.onkeydown = e => {
      if (e.key === '#') e.preventDefault();
      if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit(); }
    };
    input.onblur = commit;
    const add = document.createElement('button'); add.type = 'button'; add.className = 'btn'; add.textContent = 'Add tag'; add.onclick = commit;
    const hint = document.createElement('small'); hint.textContent = 'The # is added automatically. Up to 30 tags.';
    label.append(input); el.append(label, add, row, hint, state); draw();
    return { el, values: () => { commit(); return [...tags]; } };
  }
  return { normalize, pill, editor };
})();
