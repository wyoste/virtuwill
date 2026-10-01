// <finance-healthmeter>: a savings-goal thermometer that fills as the amount saved nears the target.
//
//   <finance-healthmeter label="Emergency fund" current="6500" goal="10000" due="by Dec 1, 2026">
//
// Dependency-free; colours and type come from the page (--meter-fill, --meter-track,
// --meter-text, --meter-muted, set in workspace.css), so it follows the workspace theme
// and dark mode. Every value is set with textContent, never parsed as markup.
export class FinanceHealthmeter extends HTMLElement {
  static observedAttributes = ['current', 'goal', 'label', 'currency', 'due'];

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; color: var(--meter-text, #193a2d); font: inherit; }
        * { box-sizing: border-box; }
        .label { font-size: 15px; font-weight: 650; margin: 0 0 4px; overflow-wrap: anywhere; }
        .amount { font-size: 28px; line-height: 1.2; font-weight: 700; letter-spacing: -.5px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
        .target, .remaining, .due { color: var(--meter-muted, #60736a); font-size: 13.5px; }
        .body { display: flex; align-items: center; gap: 16px; margin: 12px 0; }
        svg { width: 120px; height: 240px; flex: none; overflow: visible; }
        .percent { font-size: 34px; letter-spacing: -1px; font-weight: 700; font-variant-numeric: tabular-nums; }
        .status { font-size: 13px; color: var(--meter-muted, #60736a); }
        .liquid { transform-origin: 0 270px; transition: transform 650ms cubic-bezier(.2, .7, .2, 1); }
        .bulb { transition: opacity 200ms; }
        @media (prefers-reduced-motion: reduce) { .liquid, .bulb { transition: none; } }
        @media (max-width: 360px) { .body { gap: 8px; } .percent { font-size: 28px; } svg { width: 100px; height: 200px; } }
      </style>
      <div class="label"></div><div class="amount"></div><div class="target"></div>
      <div class="body">
        <svg viewBox="0 0 150 300" role="meter" aria-valuemin="0" aria-valuemax="100">
          <defs><clipPath id="vessel"><path d="M40 38a18 18 0 0 1 36 0v194a29 29 0 1 1-36 0Z"/></clipPath></defs>
          <path d="M40 38a18 18 0 0 1 36 0v194a29 29 0 1 1-36 0Z" fill="var(--meter-track, #e7eee9)"/>
          <g clip-path="url(#vessel)">
            <rect class="liquid" x="20" y="20" width="76" height="250" fill="var(--meter-fill, #24694e)"/>
            <circle class="bulb" cx="58" cy="255" r="29" fill="var(--meter-fill, #24694e)"/>
          </g>
          <g fill="var(--meter-muted, #60736a)" font-family="inherit" font-size="12">
            <text x="108" y="24">100%</text><text x="108" y="79">75%</text><text x="108" y="134">50%</text><text x="108" y="189">25%</text><text x="108" y="244">0%</text>
          </g>
          <g stroke="var(--meter-muted, #60736a)" stroke-opacity=".4"><path d="M87 20h12M87 75h12M87 130h12M87 185h12M87 240h12"/></g>
        </svg>
        <div><div class="percent"></div><div class="status"></div></div>
      </div>
      <div class="remaining" aria-live="polite"></div><div class="due"></div>`;
  }

  connectedCallback() { this.render(); }
  attributeChangedCallback() { if (this.isConnected) this.render(); }

  render() {
    const root = this.shadowRoot;
    const raw = Number(this.getAttribute('current'));
    const goal = Number(this.getAttribute('goal'));
    const current = Number.isFinite(raw) ? raw : 0;
    const valid = Number.isFinite(goal) && goal > 0;
    const fraction = valid ? Math.min(1, Math.max(0, current / goal)) : 0;
    const label = this.getAttribute('label') || 'Savings goal';
    let money;
    try { money = new Intl.NumberFormat('en-US', { style: 'currency', currency: this.getAttribute('currency') || 'USD', maximumFractionDigits: 0 }); }
    catch { money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }); }
    const set = (selector, text) => { root.querySelector(selector).textContent = text; };
    const percent = valid ? Math.floor(Math.max(0, current) / goal * 100) : 0;
    set('.label', label);
    set('.amount', money.format(current));
    set('.target', valid ? `of ${money.format(goal)} goal` : 'Set a goal amount');
    set('.percent', valid ? `${percent}%` : '—');
    set('.status', !valid ? 'No target set' : fraction >= 1 ? 'Goal reached' : fraction === 0 ? 'Ready to begin' : 'of goal saved');
    set('.remaining', !valid ? 'Add a positive goal to track progress.'
      : current > goal ? `${money.format(current - goal)} above your goal`
      : current === goal ? 'You reached your savings goal.' : `${money.format(goal - current)} left to save`);
    set('.due', this.getAttribute('due') || '');
    // The scale runs from the 0% mark on the stem (y = 240) to 100% (y = 20).
    root.querySelector('.liquid').style.transform = `translateY(-30px) scaleY(${fraction * 220 / 250})`;
    root.querySelector('.bulb').style.opacity = fraction > 0 ? '1' : '0';
    const meter = root.querySelector('svg');
    meter.setAttribute('aria-label', label);
    meter.setAttribute('aria-valuenow', String(Math.round(fraction * 100)));
    meter.setAttribute('aria-valuetext', valid ? `${money.format(current)} saved toward ${money.format(goal)}; ${percent} percent` : 'No goal set');
  }
}

if (!customElements.get('finance-healthmeter')) customElements.define('finance-healthmeter', FinanceHealthmeter);
