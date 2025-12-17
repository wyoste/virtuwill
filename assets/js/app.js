import { initJournalPage, teardownJournalPage } from "./journal.js";

const pageEl = document.getElementById("page");
const buttons = Array.from(document.querySelectorAll(".nav button"));

let currentRoute = "home";

function setActive(route) {
  buttons.forEach(b => b.classList.toggle("active", b.dataset.route === route));
}

async function loadPage(route) {
  // cleanup (journal has listeners/state)
  if (currentRoute === "journal") teardownJournalPage();

  currentRoute = route;
  setActive(route);

  const res = await fetch(`pages/${route}.html`);
  if (!res.ok) {
    pageEl.innerHTML = `<h2>Not found</h2><p class="muted">Missing pages/${route}.html</p>`;
    return;
  }
  pageEl.innerHTML = await res.text();

  // initialize page-specific logic
  if (route === "journal") initJournalPage();
}

function routeFromHash() {
  const r = (location.hash || "#home").slice(1);
  return r || "home";
}

// nav clicks
buttons.forEach(btn => {
  btn.addEventListener("click", () => {
    const route = btn.dataset.route;
    location.hash = `#${route}`;
  });
});

// hash routing
window.addEventListener("hashchange", () => loadPage(routeFromHash()));

// boot
loadPage(routeFromHash());
