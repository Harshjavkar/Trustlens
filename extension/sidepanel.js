const $ = (id) => document.getElementById(id);
const DEFAULT_URL = "http://localhost:8000";

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? (el.className = v) : (el[k] = v);
  kids.flat().forEach((c) => c != null && el.append(c));
  return el;
}

async function api(path, body) {
  const { backend = DEFAULT_URL } = await chrome.storage.local.get("backend");
  const r = await fetch(backend + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}

const status = (t) => ($("status").textContent = t);

$("trust").oninput = (e) => ($("trustv").textContent = e.target.value);
$("backend").onchange = (e) => chrome.storage.local.set({ backend: e.target.value.trim() || DEFAULT_URL });
chrome.storage.local.get("backend").then(({ backend }) => backend && ($("backend").value = backend));

$("add").onclick = async () => {
  try {
    status("Adding creator and ingesting latest videos...");
    const c = await api("/creators", { channel_url: $("url").value.trim(), trust: +$("trust").value });
    const s = await api("/ingest", {});
    status(`Added ${c.name}. Ingested ${s.videos} videos (${s.chunks} chunks, ${s.skipped_no_captions} without captions).`);
  } catch (e) { status("Error: " + e.message); }
};

$("ask").onclick = async () => {
  try {
    status("Searching and cross-checking...");
    const { results } = await api("/ask", { q: $("q").value.trim() });
    $("results").replaceChildren(...results.map(card));
    status(results.length ? "" : "No results. Add creators first.");
  } catch (e) { status("Error: " + e.message); }
};

function card(r) {
  const rep = r.report, bad = rep.verdict === "contradicted";
  const ev = rep.evidence.map((e) => h("li", {},
    `${e.stance} (${e.source_type}): `,
    /^https?:/.test(e.url_or_chunk_id) ? h("a", { href: e.url_or_chunk_id, target: "_blank", textContent: e.url_or_chunk_id }) : (e.meta || e.url_or_chunk_id)));
  const vote = (v) => h("button", { textContent: v === "up" ? "👍" : "👎", onclick: async (ev) => {
    const res = await api("/feedback", { chunk_id: r.chunk_id, vote: v });
    ev.target.parentElement.append(h("span", { class: "meta", textContent: ` ${res.creator} trust: ${res.trust}` }));
  } });
  return h("div", { class: "card" + (bad ? " bad" : "") },
    bad ? h("div", { class: "warn", textContent: "⚠ " + r.warning }) : null,
    h("b", { textContent: r.title }),
    h("div", { class: "meta", textContent: `${r.creator} · score ${r.score}` }),
    h("blockquote", { textContent: `“${r.quote}…”` }),
    h("a", { href: r.url, target: "_blank", textContent: `▶ Jump to ${Math.floor(r.start / 60)}:${String(r.start % 60).padStart(2, "0")}` }),
    h("div", {},
      h("span", { class: "badge " + rep.verdict, textContent: rep.label }),
      ` confidence ${Math.round(rep.confidence * 100)}% `,
      rep.red_flags.map((f) => h("span", { class: "chip", textContent: f }))),
    h("div", { class: "meta", textContent: rep.reason }),
    h("details", {}, h("summary", { textContent: `Evidence (${rep.evidence.length}) · cross-checked, not guaranteed` }),
      h("div", { class: "meta", textContent: "Claim: " + rep.claim }), h("ul", {}, ev)),
    h("div", {}, vote("up"), vote("down")));
}
