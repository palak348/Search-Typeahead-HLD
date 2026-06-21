// Frontend for the search typeahead.
// - Debounces keystrokes so we don't hit the backend on every character.
// - Renders a suggestion dropdown with keyboard navigation (Up/Down/Enter/Esc).
// - Submits searches and shows the dummy backend response.
// - Periodically refreshes the trending section.

const input = document.getElementById("search");
const searchBtn = document.getElementById("search-btn");
const list = document.getElementById("suggestions");
const statusEl = document.getElementById("status");
const resultBox = document.getElementById("result");
const resultBody = document.getElementById("result-body");
const trendingList = document.getElementById("trending-list");

const DEBOUNCE_MS = 150;
let debounceTimer = null;
let suggestions = [];
let activeIndex = -1;
let lastPrefix = "";

// ---- helpers ----------------------------------------------------------------
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function highlight(query, prefix) {
  const safe = escapeHtml(query);
  if (prefix && safe.toLowerCase().startsWith(prefix.toLowerCase())) {
    return "<b>" + safe.slice(0, prefix.length) + "</b>" + safe.slice(prefix.length);
  }
  return safe;
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.classList.toggle("error", isError);
}

// ---- suggestions ------------------------------------------------------------
function renderSuggestions() {
  if (!suggestions.length) {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    return;
  }
  list.innerHTML = suggestions.map((s, i) => `
    <li role="option" data-index="${i}" class="${i === activeIndex ? "active" : ""}">
      <span class="q">${highlight(s.query, lastPrefix)}</span>
      <span class="c">${Number(s.count).toLocaleString()}</span>
    </li>`).join("");
  list.hidden = false;
  input.setAttribute("aria-expanded", "true");

  list.querySelectorAll("li").forEach((li) => {
    li.addEventListener("mousedown", (e) => {
      e.preventDefault(); // keep focus
      submitSearch(suggestions[Number(li.dataset.index)].query);
    });
  });
}

async function fetchSuggestions(prefix) {
  lastPrefix = prefix;
  if (!prefix.trim()) {
    suggestions = [];
    activeIndex = -1;
    renderSuggestions();
    setStatus("");
    return;
  }
  try {
    setStatus("Loading…");
    const t0 = performance.now();
    const res = await fetch(`/suggest?q=${encodeURIComponent(prefix)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    suggestions = data.suggestions || [];
    activeIndex = -1;
    renderSuggestions();
    const ms = (performance.now() - t0).toFixed(0);
    setStatus(suggestions.length
      ? `${suggestions.length} suggestions · ${data.source} · ${ms} ms`
      : `No matches · ${ms} ms`);
  } catch (err) {
    setStatus(`Error fetching suggestions: ${err.message}`, true);
    suggestions = [];
    renderSuggestions();
  }
}

// ---- search submission ------------------------------------------------------
async function submitSearch(query) {
  const q = (query ?? input.value).trim();
  if (!q) return;
  input.value = q;
  list.hidden = true;
  try {
    setStatus("Searching…");
    const res = await fetch("/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: q }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    resultBody.textContent = JSON.stringify(data, null, 2);
    resultBox.hidden = false;
    setStatus(`Submitted "${q}"`);
    refreshTrending();
  } catch (err) {
    setStatus(`Error submitting search: ${err.message}`, true);
  }
}

// ---- trending ---------------------------------------------------------------
async function refreshTrending() {
  try {
    const res = await fetch("/trending");
    const data = await res.json();
    const items = data.trending || [];
    if (!items.length) {
      trendingList.innerHTML =
        '<li class="muted">No trending data yet — submit a few searches.</li>';
      return;
    }
    trendingList.innerHTML = items.map((t) => `
      <li>
        <span>${escapeHtml(t.query)}</span>
        <span class="score">recent ${t.recent_score} · all-time ${Number(t.all_time_count).toLocaleString()}</span>
      </li>`).join("");
  } catch {
    /* leave previous trending list in place on transient errors */
  }
}

// ---- keyboard + events ------------------------------------------------------
input.addEventListener("input", () => {
  clearTimeout(debounceTimer);
  const value = input.value;
  debounceTimer = setTimeout(() => fetchSuggestions(value), DEBOUNCE_MS);
});

input.addEventListener("keydown", (e) => {
  if (list.hidden && e.key !== "Enter") return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeIndex = Math.min(activeIndex + 1, suggestions.length - 1);
    renderSuggestions();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeIndex = Math.max(activeIndex - 1, 0);
    renderSuggestions();
  } else if (e.key === "Enter") {
    if (activeIndex >= 0 && suggestions[activeIndex]) {
      submitSearch(suggestions[activeIndex].query);
    } else {
      submitSearch();
    }
  } else if (e.key === "Escape") {
    list.hidden = true;
  }
});

searchBtn.addEventListener("click", () => submitSearch());

document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) list.hidden = true;
});

// initial trending load + light auto-refresh
refreshTrending();
setInterval(refreshTrending, 5000);
