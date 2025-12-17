const API_BASE = "http://127.0.0.1:8001";
const $ = (sel, el=document) => el.querySelector(sel);

let mounted = false;

function addBubble(messagesEl, text, who) {
  const row = document.createElement("div");
  row.className = `row ${who === "me" ? "me" : "ai"}`;

  const bubble = document.createElement("div");
  bubble.className = `bubble ${who}`;
  bubble.textContent = text;

  row.appendChild(bubble);
  messagesEl.appendChild(row);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function formatFileList(files) {
  if (!files || files.length === 0) return "";
  const names = Array.from(files).map(f => f.name);
  return `\n\nAttachments:\n- ${names.join("\n- ")}`;
}

async function health(statusEl) {
  try {
    const r = await fetch(`${API_BASE}/health`);
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error("health failed");
    statusEl.textContent = `Connected • ${j.model ?? "llava"}`;
  } catch {
    statusEl.textContent = "Disconnected (start uvicorn server:app --port 8001)";
  }
}

async function chatWithFiles(prompt, files) {
  const fd = new FormData();
  fd.append("prompt", prompt);

  for (const f of (files || [])) {
    fd.append("files", f, f.name); // backend expects parameter name "files"
  }

  const r = await fetch(`${API_BASE}/chat`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()).text;
}

export function initChatPage() {
  if (mounted) return;
  mounted = true;

  const messagesEl = $("#messages");
  const form = $("#composer");
  const input = $("#input");
  const fileInput = $("#file");
  const statusEl = $("#status");
  const modelNameEl = $("#modelName");

  if (!messagesEl || !form || !input || !fileInput || !statusEl) return;

  modelNameEl && (modelNameEl.textContent = "llava");

   // Enter = send, Shift+Enter = newline (ChatGPT behavior)
input.addEventListener("keydown", (e) => {
  // Allow IME composition (important for non-English keyboards)
  if (e.isComposing) return;

  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();       // prevent newline
    form.requestSubmit();     // submit form
  }
  // Shift+Enter falls through → browser inserts newline naturally
});

  addBubble(messagesEl, "Welcome. Attach an image (jpg/png) for LLaVA, or attach txt/csv/docx for context.", "ai");

  health(statusEl);
  const ping = setInterval(() => health(statusEl), 4000);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = input.value.trim();
    const files = fileInput.files;

    if (!text && (!files || files.length === 0)) return;

    // Show user bubble with attachment list
    const userText = (text || "(no text)") + formatFileList(files);
    addBubble(messagesEl, userText, "me");

    // Clear inputs early
    input.value = "";
    fileInput.value = "";

    // Pending AI bubble
    const pending = addBubble(messagesEl, "…", "ai");

    try {
      const reply = await chatWithFiles(text || "", files);
      pending.textContent = reply;
    } catch (err) {
      pending.textContent = `Error: ${String(err)}`;
    }
  });

  initChatPage._teardown = () => {
    clearInterval(ping);
    mounted = false;
  };
}

export function teardownChatPage() {
  if (initChatPage._teardown) initChatPage._teardown();
}
