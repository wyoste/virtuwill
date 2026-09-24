// Site › Writing: posts for the public Writing page.
import { h, api, fmt, card, pageHead, empty, toast, run, field, values, confirmDelete } from '../lib.js';
import { setDirty } from '../main.js';

export async function render(view, { path, navigate }) {
  const id = path.split('/')[4];
  const posts = await api('/api/v1/posts?view=owner');
  if (id) return editor(view, id === 'new' ? null : posts.find(p => p.id === id), navigate);
  view.append(
    pageHead('Writing', 'Posts on the public Writing page and the home page.', h('a', { class: 'btn primary', href: '/app/site/writing/new' }, '+ New post')),
    card(null, posts.length ? h('ul', { class: 'ws-list' }, posts.map(p => h('li', { class: 'ws-row' },
      h('div', { class: 'ws-row-main' }, h('a', { class: 'ws-row-title', href: '/app/site/writing/' + p.id }, p.title),
        h('div', { class: 'ws-row-meta' }, fmt.day(p.date) + (p.excerpt ? ' · ' + p.excerpt.slice(0, 90) : ''))),
      h('span', { class: 'ws-chip ' + (p.published ? 'good' : '') }, p.published ? 'Published' : 'Draft')))) : empty('No posts yet.')));
}

async function editor(view, post, navigate) {
  let dirty = false;
  setDirty(() => dirty);
  const p = post || { title: '', body: '', excerpt: '', thumbnail: '', published: false };
  const thumb = { url: p.thumbnail };
  const preview = h('img', { src: p.thumbnail || '', alt: '', style: { maxWidth: '220px', borderRadius: '8px', display: p.thumbnail ? 'block' : 'none' } });
  const file = h('input', { type: 'file', accept: 'image/*', hidden: true });
  file.onchange = () => run(null, async () => {
    const form = new FormData();
    form.append('file', file.files[0]);
    const r = await api('/api/blog/thumbnail', { method: 'POST', form });
    thumb.url = r.url; preview.src = r.url; preview.style.display = 'block'; dirty = true;
  });
  const form = h('form', { class: 'ws-form', oninput: () => { dirty = true; }, onsubmit: e => e.preventDefault() },
    field('Title', 'title', { value: p.title, wide: true, required: true }),
    field('Summary (shown in lists)', 'excerpt', { value: p.excerpt, wide: true }),
    field('Post', 'body', { kind: 'textarea', value: p.body, wide: true }),
    field('Published', 'published', { kind: 'checkbox', value: p.published }));
  form.querySelector('textarea').rows = 18;
  const save = h('button', { class: 'btn primary' }, 'Save');
  save.onclick = () => run(save, async () => {
    const body = { ...values(form), thumbnail: thumb.url || '' };
    if (!body.title) throw new Error('A post needs a title.');
    if (post) await api('/api/blog/' + post.id, { method: 'PUT', body });
    else {
      const r = await api('/api/blog', { method: 'POST', body });
      dirty = false;
      return navigate('/app/site/writing/' + r.post.id, { replace: true });
    }
    dirty = false;
    toast('Post saved');
  });
  const del = post ? h('button', { class: 'btn danger', onclick: async () => {
    if (!(await confirmDelete('this post'))) return;
    await run(null, async () => { await api('/api/blog/' + post.id, { method: 'DELETE' }); dirty = false; navigate('/app/site/writing'); });
  } }, 'Delete') : null;
  view.append(
    pageHead(post ? 'Edit post' : 'New post', h('a', { href: '/app/site/writing' }, 'All posts'), del,
      post?.published ? h('a', { class: 'btn', href: '/writing/' + post.id, target: '_blank' }, 'View ↗') : null, save),
    card(null, form, h('div', { style: { marginTop: '12px' } }, h('div', { class: 'ws-note', style: { fontWeight: 600 } }, 'Picture'), preview,
      h('button', { class: 'btn small', style: { marginTop: '6px' }, onclick: () => file.click() }, p.thumbnail ? 'Replace picture' : 'Add a picture'), file)));
}
