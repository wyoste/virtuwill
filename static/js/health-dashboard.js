/**
 * health-dashboard.js — goals and metrics above the Health tracker in Admin
 *
 * Reads /api/health/dashboard, which queries the health.* views over the shared
 * journal/health tables in Lakebase, and logs workouts into the same tables.
 */
'use strict';
window.VW = window.VW || {};

window.VW.HealthDashboard = (() => {
  const root = () => document.getElementById('adm-health-summary');
  const num = v => (v === null || v === undefined ? null : Number(v));
  const fmt = (v, digits = 0) => (v === null || v === undefined ? '—' : Number(v).toFixed(digits));
  const localToday = () => {
    const d = new Date();
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function stat(label, value, sub) {
    const card = el('div', 'adm-stat');
    card.append(el('div', 'adm-stat-num', value), el('div', 'adm-stat-lbl', label), el('div', 'adm-stat-sub', sub));
    return card;
  }

  async function load() {
    const host = root();
    if (!host || !window.VW?.Auth?.isAdmin?.()) return;
    host.replaceChildren(el('div', 'hd-note', 'Loading goals…'));
    let data;
    try {
      const r = await fetch('/api/health/dashboard', { cache: 'no-store' });
      data = await r.json();
      if (!r.ok) throw new Error(data.error || 'Could not load the health dashboard.');
    } catch (error) {
      host.replaceChildren(el('div', 'hd-note', error.message));
      return;
    }
    render(host, data);
  }

  function render(host, data) {
    const goals = Object.fromEntries(data.goals.map(g => [g.metric, g]));
    const today = localToday();
    const week = data.weeks[0] || {};
    const latest = data.weights[0];
    const todayRow = data.days.find(d => d.day === today) || {};
    const minMinutes = num(goals.qualifying_workout_minutes?.target) ?? 45;

    const grid = el('div', 'adm-stat-grid');
    grid.append(
      stat('Workout days this week',
        `${num(goals.workout_days_per_week?.current_value) ?? 0} / ${fmt(goals.workout_days_per_week?.target)}`,
        `${fmt(week.workout_minutes)} min · ${minMinutes}+ min days count · dog walks ${fmt(week.dog_walk_minutes)} min`),
      stat('Weight', latest ? `${fmt(latest.weight, 1)} ${latest.weight_unit || ''}` : '—',
        latest ? `7-day avg ${fmt(latest.avg_7d, 1)}${goals.weight ? ' · goal ' + fmt(goals.weight.target, 1) : ''}` : 'No weights yet'),
      stat('Today', `${fmt(todayRow.workout_minutes)} min`,
        `${todayRow.meals_logged || 0} meals${todayRow.calories ? ' · ' + fmt(todayRow.calories) + ' kcal' : ''}`),
    );

    // Last 14 days: qualifying day, some activity, or none.
    const strip = el('div', 'hd-strip');
    strip.setAttribute('aria-label', 'Workout minutes, last 14 days');
    const byDay = Object.fromEntries(data.days.map(d => [d.day, d]));
    for (let i = 13; i >= 0; i--) {
      const d = new Date(Date.now() - i * 86400000);
      const key = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      const row = byDay[key] || {};
      const mins = num(row.workout_minutes) || 0;
      const cell = el('span', 'hd-day ' + (row.qualifying_workout_day ? 'hd-day-met' : mins > 0 ? 'hd-day-some' : ''));
      cell.title = `${key}: ${mins} min${row.dog_walk_minutes ? ', dog walk ' + row.dog_walk_minutes + ' min' : ''}${row.weight ? ', ' + row.weight : ''}`;
      strip.append(cell);
    }

    host.replaceChildren(grid, el('div', 'hd-label', 'Last 14 days'), strip, logForm(today), recent(data.workouts), syncLine(data.sync));
  }

  function logForm(today) {
    const form = el('form', 'hd-form');
    form.setAttribute('aria-label', 'Log a workout');
    const date = el('input', 'adm-input'); date.type = 'date'; date.value = today; date.required = true; date.setAttribute('aria-label', 'Date');
    const activity = el('input', 'adm-input'); activity.placeholder = 'Activity (run, lift, bike…)'; activity.maxLength = 100; activity.setAttribute('aria-label', 'Activity');
    const minutes = el('input', 'adm-input hd-minutes'); minutes.type = 'number'; minutes.min = '0'; minutes.max = '1440'; minutes.placeholder = 'Min'; minutes.required = true; minutes.setAttribute('aria-label', 'Minutes');
    const dogLabel = el('label', 'hd-check');
    const dog = el('input'); dog.type = 'checkbox';
    dogLabel.append(dog, document.createTextNode(' Dog walk'));
    const submit = el('button', 'adm-btn-secondary', 'Log workout'); submit.type = 'submit';
    form.append(date, activity, minutes, dogLabel, submit);
    form.onsubmit = async event => {
      event.preventDefault();
      submit.disabled = true;
      try {
        const r = await fetch('/api/health/workouts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date: date.value, activity: activity.value, minutes: Number(minutes.value), dogWalk: dog.checked }),
        });
        const result = await r.json();
        if (!r.ok) throw new Error(result.error || 'Could not log the workout.');
        window.toast?.('Workout logged', 'success');
        load();
      } catch (error) {
        window.toast?.(error.message, 'warn');
        submit.disabled = false;
      }
    };
    return form;
  }

  function recent(workouts) {
    const list = el('ul', 'hd-list');
    list.setAttribute('aria-label', 'Recent workouts');
    for (const w of workouts.slice(0, 8)) {
      const item = el('li');
      const source = w.source === 'manual' ? 'logged here' : w.source === 'health_tracker' ? 'Health tracker' : w.source;
      item.append(el('span', '', `${w.workout_date} · ${w.activity || 'Workout'} · ${fmt(w.minutes)} min${w.is_dog_walk ? ' · dog walk' : ''}`),
                  el('span', 'hd-source', source));
      if (w.source === 'manual') {
        const del = el('button', 'hd-del', '×');
        del.setAttribute('aria-label', 'Delete workout on ' + w.workout_date);
        del.onclick = async () => {
          const r = await fetch('/api/health/workouts/' + w.workout_id, { method: 'DELETE' });
          if (r.ok) load(); else window.toast?.('Could not delete the workout', 'warn');
        };
        item.append(del);
      }
      list.append(item);
    }
    if (!workouts.length) list.append(el('li', 'hd-note', 'No workouts yet.'));
    return list;
  }

  function syncLine(sync) {
    if (!sync) return el('div', 'hd-note', 'Health tracker not synced yet — it syncs each time the tracker saves.');
    if (sync.error) return el('div', 'hd-note tracker-error', 'Health tracker sync failed: ' + sync.error + '. The tracker itself still saved.');
    const skipped = Object.entries(sync.skipped || {}).map(([k, n]) => `${n} ${k}`).join(', ');
    return el('div', 'hd-note',
      `Health tracker sync: ${sync.workouts} workouts, ${sync.meals} meals, ${sync.weights} weights` +
      (skipped ? ` · skipped (no date): ${skipped}` : '') +
      ` · ${new Date(sync.syncedAt).toLocaleString()}`);
  }

  function clear() { root()?.replaceChildren(); }

  return { load, clear };
})();
