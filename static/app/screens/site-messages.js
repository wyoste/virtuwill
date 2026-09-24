// Site › Messages: notes left on the Contact page.
import { h, api, fmt, card, pageHead, empty, run } from '../lib.js';

export async function render(view) {
  const messages = await api('/api/contact/messages');
  const unread = messages.filter(m => !m.read).length;
  view.append(
    pageHead('Messages', unread ? `${unread} unread of ${messages.length}` : `${messages.length} messages, all read`),
    card(null, messages.length ? h('ul', { class: 'ws-list' }, messages.map(m => {
      const markRead = h('button', { class: 'btn small', onclick: () => run(markRead, async () => {
        await api('/api/contact/read/' + m.id, { method: 'POST', quiet: true });
        markRead.replaceWith(h('span', { class: 'ws-chip' }, 'Read'));
      }) }, 'Mark read');
      return h('li', { class: 'ws-row', style: { alignItems: 'flex-start' } },
        h('div', { class: 'ws-row-main' },
          h('div', { class: 'ws-row-title' }, m.name, m.read ? null : h('span', { class: 'ws-chip info', style: { marginLeft: '8px' } }, 'New')),
          h('div', { class: 'ws-row-meta' }, [fmt.day(m.date), m.contact].filter(Boolean).join(' · ')),
          h('p', { class: 'ws-entry-body', style: { margin: '6px 0 0' } }, m.message)),
        m.read ? h('span', { class: 'ws-chip' }, 'Read') : markRead);
    })) : empty('No messages yet.')));
}
