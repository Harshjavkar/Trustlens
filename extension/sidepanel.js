/* TrustLens – side panel controller */
const $ = (id) => document.getElementById(id);
const DEFAULT_URL = "http://localhost:8000";

/* ── DOM helper ── */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs))
    k === "class" ? (el.className = v) : k === "style" ? Object.assign(el.style, v) : (el[k] = v);
  kids.flat().forEach((c) => c != null && el.append(typeof c === "string" ? document.createTextNode(c) : c));
  return el;
}

/* ── API helpers ── */
async function getBackend() {
  try {
    const { backend } = await chrome.storage.local.get("backend");
    return backend || DEFAULT_URL;
  } catch {
    return DEFAULT_URL;
  }
}

async function apiPost(path, body) {
  const backend = await getBackend();
  const r = await fetch(backend + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  return r.json();
}

async function apiGet(path) {
  const backend = await getBackend();
  const r = await fetch(backend + path);
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  return r.json();
}

/* ── Status helpers ── */
function setStatus(text, type = "") {
  const el = $("status");
  el.className = type;
  if (text && type !== "error" && type !== "success") {
    el.innerHTML = '<span class="spinner"></span>' + escapeHtml(text);
  } else {
    el.textContent = text;
  }
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* ── Tab navigation ── */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.panel).classList.add("active");
  });
});

/* ── Trust slider ── */
$("trust").oninput = (e) => ($("trustv").textContent = e.target.value);

/* ── Backend URL setting ── */
$("backend").onchange = (e) => {
  const val = e.target.value.trim() || DEFAULT_URL;
  try { chrome.storage.local.set({ backend: val }); } catch {}
};
getBackend().then((url) => {
  if (url !== DEFAULT_URL) $("backend").value = url;
});

/* ── Add creator ── */
$("add").onclick = async () => {
  const url = $("url").value.trim();
  if (!url) { setStatus("Enter a channel URL", "error"); return; }
  $("add").disabled = true;
  try {
    setStatus("Resolving channel...");
    const c = await apiPost("/creators", { channel_url: url, trust: +$("trust").value });
    setStatus("Ingesting latest videos...");
    const s = await apiPost("/ingest", {});
    setStatus(`✓ Added ${c.name}. ${s.videos} videos, ${s.chunks} chunks ingested.`, "success");
    $("url").value = "";
    loadCreators();
  } catch (e) {
    setStatus("Error: " + e.message, "error");
  } finally {
    $("add").disabled = false;
  }
};

/* ── Ask ── */
$("ask").onclick = async () => {
  const q = $("q").value.trim();
  if (!q) { setStatus("Type a question first", "error"); return; }
  $("ask").disabled = true;
  $("results").replaceChildren();
  try {
    setStatus("Searching and cross-checking…");
    const { results } = await apiPost("/ask", { q });
    $("results").replaceChildren(...results.map((r, i) => buildCard(r, i)));
    if (results.length) {
      setStatus(`${results.length} results found`, "success");
    } else {
      $("results").replaceChildren(emptyState("🔍", "No results found. Add creators first, then ask again."));
      setStatus("");
    }
  } catch (e) {
    setStatus("Error: " + e.message, "error");
  } finally {
    $("ask").disabled = false;
  }
};

/* allow Enter key to trigger Ask */
$("q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("ask").click();
});

/* ── Result card builder ── */
function buildCard(r, idx) {
  const rep = r.report;
  const isBad = rep.verdict === "contradicted";

  /* Evidence list */
  const evItems = rep.evidence.map((e) =>
    h("li", {},
      h("span", { textContent: `${stanceIcon(e.stance)} ${e.stance} (${e.source_type}): ` }),
      /^https?:/.test(e.url_or_chunk_id)
        ? h("a", { href: e.url_or_chunk_id, target: "_blank", textContent: truncUrl(e.url_or_chunk_id) })
        : h("span", { textContent: e.meta || e.url_or_chunk_id })
    )
  );

  /* Vote buttons */
  const voteRow = h("div", { class: "vote-row" });
  const mkVote = (v) => {
    const btn = h("button", { class: "vote-btn", textContent: v === "up" ? "👍 Helpful" : "👎 Unhelpful" });
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const res = await apiPost("/feedback", { chunk_id: r.chunk_id, vote: v });
        voteRow.append(h("span", { class: "vote-feedback", textContent: `${res.creator} trust → ${res.trust}` }));
      } catch {}
    };
    return btn;
  };
  voteRow.append(mkVote("up"), mkVote("down"));

  /* Score bar */
  const scoreBar = h("div", { class: "score-bar-wrap" },
    h("div", { class: "score-bar-label" },
      h("span", { textContent: "Score" }),
      h("span", { textContent: `${Math.round(r.score * 100)}%` })
    ),
    h("div", { class: "score-bar" },
      h("div", { class: "score-fill", style: { width: `${Math.round(r.score * 100)}%` } })
    )
  );

  const card = h("div", { class: "card" + (isBad ? " contradicted" : "") },
    isBad ? h("div", { class: "card-warn" }, "⚠ ", r.warning) : null,
    h("div", { class: "card-title", textContent: r.title }),
    h("div", { class: "card-meta", textContent: `${r.creator} · ${fmtTime(r.start)}` }),
    h("blockquote", { class: "card-quote", textContent: `"${r.quote}…"` }),
    h("a", { class: "card-link", href: r.url, target: "_blank", textContent: `▶ Jump to ${fmtTime(r.start)}` }),
    scoreBar,
    h("div", { class: "badge-row" },
      h("span", { class: "badge " + rep.verdict, textContent: rep.label }),
      h("span", { class: "confidence", textContent: `${Math.round(rep.confidence * 100)}% confidence` }),
      ...rep.red_flags.map((f) => h("span", { class: "chip", textContent: f }))
    ),
    h("div", { class: "card-reason", textContent: rep.reason }),
    h("details", {},
      h("summary", { textContent: `Evidence (${rep.evidence.length}) · cross-checked, not guaranteed` }),
      h("div", { class: "ev-claim", textContent: "Claim: " + rep.claim }),
      h("ul", { class: "ev-list" }, ...evItems)
    ),
    voteRow
  );

  card.style.animationDelay = `${idx * 0.06}s`;
  return card;
}

/* ── Digest ── */
$("load-digest").onclick = async () => {
  const statusEl = $("digest-status");
  const resultsEl = $("digest-results");
  statusEl.innerHTML = '<span class="spinner"></span>Loading digest…';
  statusEl.style.color = "var(--text-dim)";
  resultsEl.replaceChildren();
  try {
    const data = await apiGet("/digest");
    statusEl.textContent = "";
    if (!data.topics || data.topics.length === 0) {
      resultsEl.replaceChildren(emptyState("📰", "No new content this week. Add creators and ingest some videos first."));
      return;
    }
    const frag = document.createDocumentFragment();
    for (const t of data.topics) {
      frag.append(h("div", { class: "digest-topic", textContent: t.topic }));
      for (const item of t.items) {
        frag.append(
          h("div", { class: "digest-item" },
            h("div", { class: "card-title", textContent: item.title }),
            h("div", { class: "card-meta", textContent: item.creator }),
            h("blockquote", { class: "card-quote", textContent: `"${item.quote}…"` }),
            h("a", { class: "card-link", href: item.url, target: "_blank", textContent: "Open ▸" })
          )
        );
      }
    }
    resultsEl.append(frag);
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
    statusEl.style.color = "var(--red)";
  }
};

/* ── Creator list ── */
async function loadCreators() {
  const el = $("creator-list");
  try {
    const creators = await apiGet("/creators");
    el.replaceChildren(...creators.map((c) =>
      h("div", { class: "creator-item" },
        h("span", { class: "creator-name", textContent: c.name }),
        h("span", { class: "creator-trust", textContent: `Trust: ${Number(c.trust).toFixed(1)}` })
      )
    ));
    if (!creators.length) {
      el.replaceChildren(h("div", { style: { color: "var(--text-muted)", fontSize: "12px", padding: "8px 0" }, textContent: "No creators added yet." }));
    }
  } catch {
    el.replaceChildren(h("div", { style: { color: "var(--text-muted)", fontSize: "12px", padding: "8px 0" }, textContent: "Backend offline." }));
  }
}

$("refresh-creators").onclick = loadCreators;

/* ── Re-ingest ── */
$("reingest").onclick = async () => {
  const el = $("ingest-status");
  el.innerHTML = '<span class="spinner"></span>Ingesting…';
  $("reingest").disabled = true;
  try {
    const s = await apiPost("/ingest", {});
    el.textContent = `✓ ${s.videos} videos, ${s.chunks} chunks, ${s.skipped_no_captions} skipped.`;
    el.style.color = "var(--green)";
  } catch (e) {
    el.textContent = "Error: " + e.message;
    el.style.color = "var(--red)";
  } finally {
    $("reingest").disabled = false;
  }
};

/* ── Utility ── */
function fmtTime(sec) {
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

function stanceIcon(s) {
  return s === "supports" ? "✅" : s === "contradicts" ? "❌" : "➖";
}

function truncUrl(u) {
  try { return new URL(u).hostname + new URL(u).pathname.slice(0, 40); } catch { return u.slice(0, 60); }
}

function emptyState(icon, text) {
  return h("div", { class: "empty" },
    h("div", { class: "empty-icon", textContent: icon }),
    h("div", { class: "empty-text", textContent: text })
  );
}

/* ── Init ── */
loadCreators();
