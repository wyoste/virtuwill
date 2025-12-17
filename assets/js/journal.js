const $ = (sel, el=document) => el.querySelector(sel);
const $$ = (sel, el=document) => Array.from(el.querySelectorAll(sel));

let mounted = false;

function isoToday(){
  const d = new Date();
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}

function prettyDate(iso){
  const [y,m,d] = iso.split("-").map(Number);
  const dt = new Date(y, m-1, d);
  return dt.toLocaleDateString(undefined, { weekday:"short", year:"numeric", month:"short", day:"numeric" });
}

function collapseAllExcept(card){
  $$(".card.open").forEach(c => { if (c !== card) c.classList.remove("open"); });
}

function getCardISO(card){ return card.getAttribute("data-date"); }

function sortCardsDesc(){
  const tl = $("#timeline");
  const cards = $$(".card", tl);
  cards.sort((a,b) => getCardISO(b).localeCompare(getCardISO(a)));
  cards.forEach(c => tl.appendChild(c));
}

function createCard(isoDate){
  const card = document.createElement("div");
  card.className = "card open cols-1";
  card.setAttribute("data-date", isoDate);

  card.innerHTML = `
    <div class="card-header" role="button" aria-label="Toggle card">
      <div class="date">${prettyDate(isoDate)}</div>
      <div class="meta">
        <span class="pill">Quote</span>
        <span class="pill">Meals</span>
        <span class="pill">Entry</span>
      </div>
    </div>

    <div class="card-body">
      <div class="quote">
        <div class="section-title">Quote</div>
        <div class="quote-edit placeholder" contenteditable="true" data-placeholder="Write a quote or a line that matters today…"></div>
      </div>

      <div class="meals">
        <div class="section-title">Meals</div>
        <table>
          <thead>
            <tr><th>B</th><th>L</th><th>D</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><div class="cell-edit placeholder" contenteditable="true" data-placeholder="Breakfast…"></div></td>
              <td><div class="cell-edit placeholder" contenteditable="true" data-placeholder="Lunch…"></div></td>
              <td><div class="cell-edit placeholder" contenteditable="true" data-placeholder="Dinner…"></div></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="entry">
        <div class="ribbon">
          <div class="btn-group">
            <button class="tool" data-cmd="bold" type="button"><b>B</b></button>
            <button class="tool" data-cmd="italic" type="button"><i>I</i></button>
            <button class="tool" data-cmd="underline" type="button"><u>U</u></button>
            <button class="tool" data-cmd="strikeThrough" type="button"><s>S</s></button>
          </div>
          <div class="btn-group" aria-label="Columns">
            <span class="hint" style="margin-right:6px; opacity:.75; font-size:12px;">Columns:</span>
            <button class="tool" data-cols="1" type="button">1</button>
            <button class="tool" data-cols="2" type="button">2</button>
            <button class="tool" data-cols="3" type="button">3</button>
          </div>
        </div>
        <div class="editor-area">
          <div class="editor placeholder" contenteditable="true" data-placeholder="Free-write here…"></div>
        </div>
      </div>
    </div>
  `;

  // accordion
  $(".card-header", card).addEventListener("click", () => {
    const willOpen = !card.classList.contains("open");
    if (willOpen) collapseAllExcept(card);
    card.classList.toggle("open");
  });

  // editor tools
  const editor = $(".editor", card);
  $$(".tool[data-cmd]", card).forEach(btn => {
    btn.addEventListener("click", () => {
      editor.focus();
      document.execCommand(btn.dataset.cmd, false, null);
    });
  });

  // columns
  const colBtns = $$(".tool[data-cols]", card);
  colBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      const cols = btn.dataset.cols;
      card.classList.remove("cols-1","cols-2","cols-3");
      card.classList.add(`cols-${cols}`);
      colBtns.forEach(b => b.classList.toggle("active", b === btn));
    });
  });
  const one = $(`.tool[data-cols="1"]`, card);
  if (one) one.classList.add("active");

  // clean paste
  editor.addEventListener("paste", (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData("text/plain");
    document.execCommand("insertText", false, text);
  });

  collapseAllExcept(card);
  return card;
}

function ensureCardForDate(isoDate){
  const existing = $(`.card[data-date="${isoDate}"]`);
  if (existing) {
    existing.classList.add("open");
    collapseAllExcept(existing);
    existing.scrollIntoView({behavior:"smooth", block:"start"});
    return existing;
  }
  const card = createCard(isoDate);
  $("#timeline").appendChild(card);
  sortCardsDesc();
  card.scrollIntoView({behavior:"smooth", block:"start"});
  return card;
}

export function initJournalPage(){
  if (mounted) return;
  mounted = true;

  const dateEl = $("#newDate");
  const addBtn = $("#addCardBtn");

  if (!dateEl || !addBtn) return; // not on journal page

  dateEl.value = isoToday();
  addBtn.addEventListener("click", () => ensureCardForDate(dateEl.value || isoToday()));

  // start with today
  ensureCardForDate(isoToday());
}

export function teardownJournalPage(){
  // For now, nothing heavy to remove.
  // If you later add global listeners/timers, clean them here.
  mounted = false;
}
