"use strict";

const $ = (id) => document.getElementById(id);
const state = { creators: [], creator: null, posts: [], history: [], pending: false, generation: 0, searchGeneration: 0, dialogGeneration: 0, job: null, poll: null, conversation: { configured: false, checked: false, checking: false, error: false } };
const labels = { caption: "Post caption", speech: "Speech transcript", ocr: "On-screen text" };
const make = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
function announce(text) { $("global-status").textContent = text; }
function setChatStatus(text, error = false) { $("chat-state").textContent = text; $("chat-state").classList.toggle("error", error); }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { "Accept": "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers } });
  let result;
  try { result = await response.json(); } catch { throw new Error("The local workspace returned an unreadable response. Try again."); }
  if (!response.ok) throw new Error(typeof result.error === "string" ? result.error : "The workspace couldn’t complete this request.");
  return result;
}
function number(value) { return typeof value === "number" && Number.isFinite(value) ? value : 0; }
function date(value) { const parsed = value ? new Date(value) : null; return parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "Date unavailable"; }
function timestamp(value) { const total = Math.floor(value); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; }
function realTime(source) { return ["speech", "ocr"].includes(source.modality) && typeof source.start_seconds === "number" && Number.isFinite(source.start_seconds) && source.start_seconds >= 0; }
function interval(source) { if (!realTime(source)) return ""; const end = source.end_seconds; return timestamp(source.start_seconds) + (typeof end === "number" && Number.isFinite(end) && end >= source.start_seconds ? `–${timestamp(end)}` : ""); }
function modalityBadge(modality) { return make("span", `modality ${modality}`, labels[modality] || "Source"); }
function originalLink(url, text = "Open on Instagram ↗") {
  try { const parsed = new URL(url); if (parsed.protocol !== "https:" || !["instagram.com", "www.instagram.com"].includes(parsed.hostname)) return null; const link = make("a", "", text); link.href = parsed.href; link.target = "_blank"; link.rel = "noopener noreferrer"; return link; } catch { return null; }
}
function syncComposer() {
  const ready = Boolean(state.creator) && state.conversation.configured && !state.pending;
  $("question").disabled = !ready;
  $("question").placeholder = !state.creator ? "Choose a creator to start a conversation…" : state.conversation.configured ? "Ask about their published knowledge…" : "Conversation setup is pending. Explore the sources meanwhile.";
  $("ask-button").disabled = !ready || !$("question").value.trim();
  $("creator-select").disabled = state.pending || !state.creators.length;
  $("new-chat").disabled = state.pending;
  $("browse-sources").disabled = !state.creator;
  $("source-query").disabled = !state.creator;
  $("source-search-form").querySelector("button").disabled = !state.creator;
  $("suggestions").querySelectorAll("button").forEach((button) => { button.disabled = button.dataset.action === "browse" ? !state.creator : !ready; });
}
function showConversationReadiness() {
  const status = state.conversation;
  $("conversation-readiness").classList.toggle("ready", status.configured);
  $("conversation-readiness-text").textContent = status.checking ? "Checking conversation availability…" : status.error ? "Conversation availability couldn’t be checked. You can still explore the sources." : status.configured ? "Conversation ready" : "Conversation setup is pending. The source library is available.";
  $("refresh-conversation").hidden = status.configured || status.checking;
  $("refresh-conversation").disabled = status.checking;
  syncComposer();
}
async function refreshConversation() {
  if (state.conversation.checking) return;
  state.conversation.checking = true; showConversationReadiness();
  try { const result = await api("/api/health"); state.conversation = { configured: result.conversation?.configured === true, checked: true, checking: false, error: false }; }
  catch { state.conversation = { configured: false, checked: true, checking: false, error: true }; }
  showConversationReadiness();
}
function showCoverage(creator) {
  const coverage = $("coverage"); coverage.replaceChildren();
  for (const [label, key] of [["Saved posts", "posts"], ["Post captions", "captions"], ["Speech transcripts", "speech"], ["On-screen text", "ocr"]]) {
    const row = make("div"); row.append(make("dt", "", label), make("dd", "", creator ? String(number(creator[key])) : "—")); coverage.append(row);
  }
  $("coverage-note").textContent = creator ? `${number(creator.posts)} saved posts; a partial profile library. ${creator.coverage_note || "Full profile coverage hasn’t been verified."}` : "No creator library is ready yet. Add a creator to begin preparing one.";
  const summary = $("creator-summary"); summary.replaceChildren();
  if (creator) { summary.append(make("p", "creator-name", `@${creator.handle}`), make("p", "", `${(number(creator.duration_seconds) / 60).toFixed(1)} minutes of source video · partial coverage`)); }
  else summary.append(make("p", "", "Choose a prepared creator or add one below."));
  $("active-creator").textContent = creator ? `@${creator.handle}` : "Conversation";
  $("composer-scope").replaceChildren(make("span", "", "◎"), document.createTextNode(creator ? ` Only @${creator.handle}’s saved material` : " Choose a source to begin"));
  $("welcome-description").textContent = creator ? `Explore @${creator.handle}’s published knowledge through ${number(creator.posts)} saved posts. Every source stays within reach.` : "Add a creator to prepare a library, then explore what they’ve published with the original posts beside every answer.";
}
function clearChat() { state.history = []; $("messages").replaceChildren(); $("welcome").hidden = false; $("question").value = ""; resizeComposer(); setChatStatus(""); syncComposer(); }
async function loadCreators(preferred) {
  const result = await api("/api/creators");
  state.creators = Array.isArray(result.creators) ? result.creators : [];
  const select = $("creator-select"); select.replaceChildren();
  if (!state.creators.length) select.append(new Option("No prepared creators yet", ""));
  state.creators.forEach((creator) => select.append(new Option(`@${creator.handle}`, creator.handle)));
  const desired = preferred || state.creator?.handle;
  const chosen = state.creators.find((creator) => creator.handle === desired) || state.creators[0] || null;
  if (chosen) select.value = chosen.handle;
  await chooseCreator(chosen);
}
async function chooseCreator(creator) {
  const generation = ++state.generation;
  ++state.searchGeneration;
  state.creator = creator; state.posts = []; clearChat(); showCoverage(creator);
  $("source-query").value = ""; $("clear-search").hidden = true; $("source-filter-label").textContent = "ALL SAVED POSTS";
  if (!creator) { renderSources([]); syncComposer(); return; }
  $("source-list").replaceChildren(make("p", "muted small loading-dots", "Reading saved posts"));
  $("source-count").textContent = String(number(creator.posts));
  try { const result = await api(`/api/posts?creator=${encodeURIComponent(creator.handle)}`); if (generation !== state.generation) return; state.posts = Array.isArray(result.posts) ? result.posts : []; renderSources(state.posts); announce(`${state.posts.length} saved posts from ${creator.handle} loaded.`); }
  catch (error) { if (generation === state.generation) { showSourceError(error.message, () => chooseCreator(creator)); } }
}
function showSourceError(message, retry) { const box = make("div", "state-box", message); const button = make("button", "text-button", "Try again"); button.type = "button"; button.addEventListener("click", retry); box.append(make("br"), button); $("source-list").replaceChildren(box); }
function renderSources(items, search = false) {
  const list = $("source-list"); list.replaceChildren();
  $("source-count").textContent = String(items.length);
  $("source-description").textContent = search ? "Matching passages from this creator’s saved material." : "The published material behind your conversation.";
  if (!items.length) { list.append(make("p", "state-box", search ? "No matching source passages were found in this partial library. Try different words." : state.creator ? "There are no saved posts to show for this creator." : "Add a creator to prepare their published sources.")); return; }
  items.forEach((item) => {
    const card = make("button", "source-card"); card.type = "button";
    const top = make("div", "source-card-top"); const type = make("span"); type.append(make("span", "reel-icon", "▣"), document.createTextNode("INSTAGRAM REEL")); top.append(type, make("span", "", date(item.published_at)));
    card.append(top, make("h3", "", item.title || `Reel ${item.post_id || item.id}`));
    const excerpt = item.text || item.caption || item.segments?.find((segment) => segment.text)?.text;
    if (excerpt) card.append(make("p", "", excerpt));
    const meta = make("div", "source-meta");
    const modalities = item.modality ? [item.modality] : [...new Set((item.segments || []).map((segment) => segment.modality))];
    modalities.forEach((modality) => meta.append(modalityBadge(modality)));
    if (realTime(item)) meta.append(make("span", "modality", interval(item)));
    card.append(meta); card.addEventListener("click", () => openSource(item)); list.append(card);
  });
}
async function searchSources(event) {
  event?.preventDefault(); if (!state.creator) return;
  const query = $("source-query").value.trim(); const generation = ++state.searchGeneration; const creator = state.creator.handle;
  $("clear-search").hidden = !query; $("source-filter-label").textContent = query ? "SEARCH RESULTS" : "ALL SAVED POSTS";
  if (!query) { renderSources(state.posts); return; }
  $("source-list").replaceChildren(make("p", "muted small loading-dots", "Finding passages"));
  try { const result = await api(`/api/search?q=${encodeURIComponent(query)}&creator=${encodeURIComponent(creator)}`); if (generation !== state.searchGeneration || creator !== state.creator?.handle) return; renderSources(Array.isArray(result.sources) ? result.sources : [], true); }
  catch (error) { if (generation === state.searchGeneration) showSourceError(error.message, () => searchSources()); }
}
function message(role) {
  const article = make("article", `message ${role}`); const author = make("div", "message-author"); author.append(make("span", "avatar", role === "user" ? "Y" : "i"), document.createTextNode(role === "user" ? "You" : "InstaNT")); const body = make("div", "message-content"); article.append(author, body); $("messages").append(article); return { article, body };
}
function citedText(text, sources) {
  const fragment = document.createDocumentFragment(); const pattern = /\[(\d+)\]/g; let position = 0; let match;
  while ((match = pattern.exec(text))) { fragment.append(document.createTextNode(text.slice(position, match.index))); const index = Number(match[1]) - 1; if (sources[index]) { const citation = make("button", "citation", match[1]); citation.type = "button"; citation.setAttribute("aria-label", `Read source ${match[1]}`); citation.addEventListener("click", () => openSource(sources[index])); fragment.append(citation); } else fragment.append(document.createTextNode(match[0])); position = pattern.lastIndex; }
  fragment.append(document.createTextNode(text.slice(position))); return fragment;
}
function renderAnswer(result) {
  const sources = Array.isArray(result.sources) ? result.sources : []; const { article, body } = message("assistant");
  const answer = make("div", "answer-text"); answer.append(citedText(result.answer, sources)); body.append(answer);
  if (result.mode === "model") { const note = make("span", "answer-mode", sources.length ? "AI-generated · source support unverified" : "AI-generated · no sources cited"); note.title = sources.length ? "This reply has not been manually reviewed against its linked sources." : "This reply did not include source citations."; body.append(note); }
  if (sources.length) { const links = make("div", "answer-sources"); sources.forEach((source, index) => { const button = make("button", "answer-source"); button.type = "button"; button.append(make("span", "", `[${index + 1}]`), document.createTextNode(`${labels[source.modality] || "Source"}${realTime(source) ? ` · ${interval(source)}` : ""}`)); button.addEventListener("click", () => openSource(source)); links.append(button); }); body.append(links); }
  if (Array.isArray(result.warnings) && result.warnings.length) { const warnings = make("details", "answer-warnings"); warnings.append(make("summary", "", "About this reply")); result.warnings.forEach((warning) => warnings.append(make("p", "", warning))); body.append(warnings); }
  return article;
}
async function ask(event) {
  event?.preventDefault(); const question = $("question").value.trim(); if (!question || !state.creator || !state.conversation.configured || state.pending) return;
  const generation = state.generation; const creator = state.creator.handle;
  state.pending = true; syncComposer(); $("welcome").hidden = true;
  const user = message("user"); user.body.textContent = question; $("question").value = ""; resizeComposer();
  setChatStatus("Thinking…"); $("chat-state").classList.add("loading-dots");
  const history = state.history.slice(-18); const payload = { question, creator, history }; let success = false;
  while (history.length && new TextEncoder().encode(JSON.stringify(payload)).length > 60000) history.splice(0, 2);
  try { const result = await api("/api/chat", { method: "POST", body: JSON.stringify(payload) }); if (generation !== state.generation) return; if (typeof result.answer !== "string" || !result.answer.trim()) throw new Error("The assistant returned an empty reply. Please try again."); renderAnswer(result); state.history.push({ role: "user", content: question }, { role: "assistant", content: result.answer }); if (result.coverage) { Object.assign(state.creator, result.coverage); showCoverage(state.creator); } setChatStatus(""); announce("Reply ready."); success = true; }
  catch (error) { if (generation !== state.generation) return; user.article.remove(); $("welcome").hidden = $("messages").children.length > 0; setChatStatus(error.message, true); $("question").value = question; resizeComposer(); refreshConversation(); }
  finally { state.pending = false; $("chat-state").classList.remove("loading-dots"); syncComposer(); if (!success && generation === state.generation && state.conversation.configured) $("question").focus(); }
}
function resizeComposer() { const input = $("question"); input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 150)}px`; }
function evidenceGroup(post, modality, selected, video) {
  const group = make("section", "evidence-group"); const heading = make("h3"); heading.append(modalityBadge(modality));
  const segments = (post.segments || []).filter((segment) => segment.modality === modality);
  const status = post.processing?.[modality] || (segments.length ? "available" : "missing"); heading.append(make("span", "status-label", status === "complete" ? "Available" : status.replaceAll("_", " "))); group.append(heading);
  if (!segments.length) { group.append(make("p", "no-evidence", `${labels[modality]} isn’t available for this post.`)); return group; }
  if (modality === "speech") group.append(make("p", "no-evidence", "Automatically transcribed; wording and speaker attribution may need review."));
  if (modality === "ocr") group.append(make("p", "no-evidence", "Sampled on-screen text; observations may miss words or contain recognition errors."));
  segments.forEach((segment) => { const excerpt = make("div", `evidence-segment${segment.id === selected?.id ? " selected" : ""}`); if (segment.id === selected?.id) excerpt.id = "selected-evidence"; if (realTime(segment)) { const time = make(video ? "button" : "span", "timestamp", `${interval(segment)}${modality === "ocr" ? " · sampled observation" : ""}`); if (video) { time.type = "button"; time.setAttribute("aria-label", `Seek to ${timestamp(segment.start_seconds)}`); time.addEventListener("click", () => { video.currentTime = segment.start_seconds; video.focus(); }); } excerpt.append(time); } if (segment.speaker) excerpt.append(make("span", "status-label", `Speaker: ${segment.speaker}\n`)); excerpt.append(document.createTextNode(segment.text || "")); group.append(excerpt); }); return group;
}
async function openSource(source) {
  const generation = ++state.dialogGeneration; const dialog = $("source-dialog"); const content = $("dialog-content"); content.replaceChildren(make("p", "dialog-status loading-dots", "Opening the source")); if (!dialog.open) dialog.showModal();
  try {
    const id = source.post_id || source.id; const result = await api(`/api/posts/${encodeURIComponent(id)}`); if (generation !== state.dialogGeneration || !dialog.open) return; const post = result.post; if (!post) throw new Error("This source is no longer available.");
    content.replaceChildren(); const title = make("h2", "dialog-title", post.title || `Reel ${post.id}`); title.id = "dialog-title"; const meta = make("div", "dialog-meta"); meta.append(make("span", "", `@${post.creator}`), make("span", "", date(post.published_at))); const link = originalLink(post.url); if (link) meta.append(link); content.append(title, meta);
    const layout = make("div", "dialog-layout"); const videoContainer = make("div", "video-container"); let video = null;
    const media = Array.isArray(post.media) ? post.media : post.media ? [post.media] : [];
    if (media.length && post.processing?.media !== "missing") { video = make("video", "source-video"); video.controls = true; video.preload = "metadata"; video.playsInline = true; video.src = `/media/${encodeURIComponent(post.id)}`; video.setAttribute("aria-label", `Original saved reel from ${post.creator}`); if (realTime(source)) video.addEventListener("loadedmetadata", () => { if (source.start_seconds < video.duration) video.currentTime = source.start_seconds; }, { once: true }); video.addEventListener("error", () => { videoContainer.append(make("p", "video-caption", "The saved video could not be opened. You can still read the evidence or open the original post.")); }, { once: true }); videoContainer.append(video, make("p", "video-caption", "Original saved reel. Caption text has no video timestamp.")); }
    else videoContainer.append(make("p", "state-box", "A local video preview isn’t available for this post."));
    const evidence = make("div", "source-evidence"); evidence.append(make("h3", "", "Read the evidence in context")); ["caption", "speech", "ocr"].forEach((modality) => evidence.append(evidenceGroup(post, modality, source, video))); layout.append(videoContainer, evidence); content.append(layout);
  } catch (error) { if (generation !== state.dialogGeneration || !dialog.open) return; const heading = make("h2", "dialog-title", "Source unavailable"); heading.id = "dialog-title"; content.replaceChildren(heading, make("p", "dialog-status", error.message)); }
}
function closeSource() { ++state.dialogGeneration; const video = $("source-dialog").querySelector("video"); if (video) video.pause(); $("source-dialog").close(); }
function stopPolling() { if (state.poll) clearTimeout(state.poll); state.poll = null; }
function jobState(job) { return String(job.status || "unknown"); }
function renderJob(job, reused = false) {
  state.job = job; const box = $("creator-job"); box.hidden = false; box.replaceChildren(); const status = jobState(job);
  const headings = { queued: "Waiting to prepare the library", processing: "Preparing published sources", discovering: "Finding public source posts", downloading: "Saving source posts", ready: "Creator library is ready", needs_source_links: "Public discovery needs source links", failed: "The library couldn’t be prepared", cancelled: "Preparation was stopped" };
  box.append(make("span", "job-state", status.replaceAll("_", " ")), make("strong", "", `@${job.handle || "creator"}`), make("p", "", headings[status] || "Creator preparation status"));
  if (job.detail) box.append(make("p", "", job.detail));
  if (reused) box.append(make("p", "", status === "ready" ? "Using the library already prepared in this workspace." : "Using the existing preparation request."));
  if (["queued", "processing", "discovering", "downloading"].includes(status)) box.append(make("p", "", "This may take a while. Access and source availability determine coverage."));
  if (["failed", "needs_source_links", "cancelled"].includes(status)) { const retry = make("button", "retry-button", "Retry preparation"); retry.type = "button"; retry.addEventListener("click", () => requestCreator(job.handle, true)); box.append(retry); }
  if (status === "ready") { const open = make("button", "text-button", "Open this creator’s library ↗"); open.type = "button"; open.addEventListener("click", () => loadCreators(job.handle).catch((error) => setChatStatus(error.message, true))); box.append(open); }
}
async function pollJob(handle) {
  stopPolling();
  try { const result = await api(`/api/creators/status?handle=${encodeURIComponent(handle)}`); if (state.job?.handle !== handle) return; const job = result.creator; if (!job) throw new Error("No preparation status is available."); const previous = state.job.status; renderJob(job); if (job.status === "ready") { if (previous !== "ready") { await loadCreators(handle); announce(`The source library for ${handle} is ready.`); } return; } if (["queued", "processing", "discovering", "downloading"].includes(jobState(job))) state.poll = setTimeout(() => pollJob(handle), 3000); }
  catch (error) { const box = $("creator-job"); box.append(make("p", "", `Couldn’t refresh preparation status: ${error.message}`)); const button = make("button", "retry-button", "Refresh status"); button.type = "button"; button.addEventListener("click", () => pollJob(handle)); box.append(button); }
}
async function requestCreator(value, retry = false) {
  const handle = value.trim(); if (!handle) return; stopPolling(); const button = $("request-creator"); button.disabled = true; const box = $("creator-job"); box.hidden = false; box.replaceChildren(make("p", "loading-dots", retry ? "Requesting another attempt" : "Checking the creator library"));
  try { const result = await api(retry ? "/api/creators/retry" : "/api/creators/request", { method: "POST", body: JSON.stringify({ handle }) }); if (!result.creator) throw new Error("No creator status was returned."); renderJob(result.creator, result.reused); if (result.creator.status === "ready") await loadCreators(result.creator.handle); else if (["queued", "processing", "discovering", "downloading"].includes(jobState(result.creator))) state.poll = setTimeout(() => pollJob(result.creator.handle), 3000); }
  catch (error) { box.replaceChildren(make("strong", "", "Couldn’t request this creator"), make("p", "", error.message)); }
  finally { button.disabled = false; }
}
$("creator-select").addEventListener("change", () => chooseCreator(state.creators.find((creator) => creator.handle === $("creator-select").value) || null));
$("new-chat").addEventListener("click", () => { clearChat(); $("question").focus(); announce("New conversation started."); });
$("ask-form").addEventListener("submit", ask);
$("question").addEventListener("input", () => { resizeComposer(); syncComposer(); });
$("question").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); ask(); } });
$("suggestions").addEventListener("click", (event) => { const button = event.target.closest("button"); if (!button || button.disabled) return; if (button.dataset.action === "browse") { $("evidence-panel").scrollIntoView({ behavior: "smooth", block: "start" }); $("source-query").focus({ preventScroll: true }); } else { $("question").value = button.dataset.prompt; resizeComposer(); syncComposer(); $("question").focus(); } });
$("browse-sources").addEventListener("click", () => { $("evidence-panel").scrollIntoView({ behavior: "smooth", block: "start" }); $("source-query").focus({ preventScroll: true }); });
$("source-search-form").addEventListener("submit", searchSources);
$("clear-search").addEventListener("click", () => { $("source-query").value = ""; searchSources(); });
$("close-dialog").addEventListener("click", closeSource);
$("source-dialog").addEventListener("cancel", (event) => { event.preventDefault(); closeSource(); });
$("source-dialog").addEventListener("click", (event) => { if (event.target === $("source-dialog")) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeSource(); } });
$("creator-request-form").addEventListener("submit", (event) => { event.preventDefault(); requestCreator($("creator-handle").value); });
$("refresh-conversation").addEventListener("click", refreshConversation);
syncComposer();
refreshConversation();
loadCreators().catch((error) => { $("creator-select").replaceChildren(new Option("Library unavailable", "")); showCoverage(null); syncComposer(); setChatStatus(error.message, true); showSourceError("The source library couldn’t be loaded.", () => loadCreators().catch((retryError) => setChatStatus(retryError.message, true))); });
