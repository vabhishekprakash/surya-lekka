"use strict";

const $ = (id) => document.getElementById(id);
let docs = [];
let doc = null;       // review state of the open document
let pageIdx = 0;
let selected = -1;
let drawMode = false;
let drag = null;
let zoom = 1;
let showExported = false;
let previewVersion = 0;

const WARNINGS = {
  rotated: "Page is rotated. Check that masks line up with the text.",
  no_text_layer: "No usable text layer on this page; proposals rely on OCR.",
  scanned: "Scanned or photographed page.",
  ocr_skipped: "OCR is off, so nothing on this image page was read. Check every line by eye.",
  ocr_failed: "OCR failed on this page. Nothing was read automatically. Check every line by eye.",
  low_confidence: "Some OCR lines are low confidence (yellow). Read them yourself.",
  no_text_found: "No text was found at all. It may be handwriting, a photo or unreadable. Check by eye.",
  code_detect_failed: "QR and barcode detection failed. Look for codes yourself.",
  render_failed: "This page could not be rendered and cannot be approved or exported.",
};

function msg(text, isErr) {
  $("msg").textContent = text || "";
  $("msg").className = isErr ? "err" : "";
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ---- document list ---------------------------------------------------
async function loadDocs() {
  docs = (await api("/api/docs")).docs;
  renderDocs();
}

function renderDocs() {
  const ol = $("docs");
  ol.textContent = "";
  for (const d of docs) {
    const li = document.createElement("li");
    if (!d.original_found) li.classList.add("missing");
    if (doc && doc.doc_id === d.doc_id) li.classList.add("current");
    const name = document.createElement("div");
    name.className = "name";
    const label = document.createElement("span");
    label.textContent = d.doc_id + (d.held_out ? " (held-out)" : "");
    const tag = document.createElement("span");
    tag.className = "tag";
    if (!d.original_found) tag.textContent = "missing";
    else if (d.exported) { tag.textContent = "exported"; tag.classList.add("ok"); }
    else if (!d.denylist_found) { tag.textContent = "no denylist"; tag.classList.add("warn"); }
    else if (d.pages) tag.textContent = `${d.approved}/${d.pages}`;
    name.append(label, tag);
    const bar = document.createElement("div");
    bar.className = "bar";
    const fill = document.createElement("div");
    fill.style.width = d.pages ? `${(100 * d.approved) / d.pages}%` : "0";
    bar.append(fill);
    li.append(name, bar);
    if (d.original_found) li.addEventListener("click", () => openDoc(d.doc_id));
    ol.append(li);
  }
}

function updateDocSummary() {
  const d = docs.find((x) => x.doc_id === doc.doc_id);
  d.pages = doc.pages.length;
  d.approved = doc.pages.filter((p) => p.approved).length;
  renderDocs();
}

// ---- document and page -----------------------------------------------
async function openDoc(id) {
  msg("Loading. The first open of a document runs detection and OCR, which can take a while.");
  try {
    doc = await api(`/api/doc/${id}`);
  } catch (e) { msg(e.message, true); return; }
  const firstTodo = doc.pages.findIndex((p) => !p.approved);
  showExported = false;
  showPage(firstTodo >= 0 ? firstTodo : 0);
  updateDocSummary();
  msg(doc.denylist_found ? "" : "No denylist file for this document. Check every page by eye.", !doc.denylist_found);
}

function showPage(i) {
  if (!doc) return;
  pageIdx = Math.max(0, Math.min(doc.pages.length - 1, i));
  selected = -1;
  $("img-left").src = `/api/doc/${doc.doc_id}/original/${pageIdx}`;
  refreshRight();
  render();
}

function refreshRight() {
  const which = showExported ? "exported" : "masked";
  previewVersion += 1;
  $("img-right").src = `/api/doc/${doc.doc_id}/${which}/${pageIdx}?v=${previewVersion}`;
  $("right-title").textContent = showExported
    ? "Exported file (final check: reopened from the saved PDF)"
    : "Masked preview (what will be exported)";
  $("btn-final").classList.toggle("on", showExported);
}

function page() { return doc.pages[pageIdx]; }

function unresolved(p) {
  return p.boxes.filter((b) => b.conflict && b.conflict.length && b.source === "auto" && !b.keep);
}

function render() {
  const p = page();
  const done = doc.pages.filter((x) => x.approved).length;
  $("doc-title").textContent = doc.doc_id;
  $("page-label").textContent = `Page ${pageIdx + 1} of ${doc.pages.length}`;
  $("page-status").textContent = p.approved ? "Approved" : "Not approved";
  $("page-status").className = p.approved ? "ok" : "todo";
  $("doc-count").textContent = `${done} of ${doc.pages.length} pages approved`;
  $("doc-bar").style.width = `${(100 * done) / doc.pages.length}%`;
  $("btn-export").disabled = done !== doc.pages.length;
  const sel = selected >= 0 ? p.boxes[selected] : null;
  $("btn-keep").disabled = !(sel && sel.conflict && sel.conflict.length);

  const ul = $("warnings");
  ul.textContent = "";
  const lines = p.warnings.map((w) => WARNINGS[w] || w);
  const open = unresolved(p).length;
  if (open) lines.push(`${open} proposed mask(s) cover protected fields (orange). Delete, redraw, or select and press K to keep.`);
  if (!p.boxes.length) lines.push("No masks proposed. That does not mean the page is clean.");
  for (const t of lines) {
    const li = document.createElement("li");
    li.textContent = t;
    ul.append(li);
  }

  for (const id of ["sheet", "sheet-right"]) $(id).style.width = `${zoom * 100}%`;
  const ov = $("overlay");
  ov.textContent = "";
  const scale = $("img-left").clientWidth / p.width || 0;
  for (const h of p.hints) ov.append(rect(h, scale, "hint"));
  for (const r of p.protected) ov.append(rect(r, scale, "prot"));
  p.boxes.forEach((b, i) => {
    let cls = "box";
    if (b.conflict && b.conflict.length) cls += b.keep || b.source !== "auto" ? " kept" : " conflict";
    if (i === selected) cls += " sel";
    ov.append(rect(b, scale, cls));
  });
  if (drag) ov.append(rect(norm(drag), scale, "box drawing"));
}

function rect(b, scale, cls) {
  const el = document.createElement("div");
  el.className = "r " + cls;
  el.style.left = `${b.x0 * scale}px`;
  el.style.top = `${b.y0 * scale}px`;
  el.style.width = `${(b.x1 - b.x0) * scale}px`;
  el.style.height = `${(b.y1 - b.y0) * scale}px`;
  return el;
}

// Saves go out one at a time so an approval never races an earlier edit.
let saveQueue = Promise.resolve();

function save(approved) {
  const d = doc;
  const idx = pageIdx;
  const boxes = d.pages[idx].boxes.map((b) => ({ ...b }));
  const run = () => sendPage(d, idx, boxes, approved);
  const p = saveQueue.then(run, run);
  saveQueue = p;
  return p;
}

async function sendPage(d, idx, boxes, approved) {
  try {
    const res = await api(`/api/doc/${d.doc_id}/page/${idx}`, {
      source_sha256: d.source_sha256, boxes, approved,
    });
    if (d !== doc) return true;
    doc.pages[idx] = res.page;
    updateDocSummary();
    if (idx === pageIdx) { render(); if (!showExported) refreshRight(); }
    return true;
  } catch (e) { msg(e.message, true); return false; }
}

function edited() {
  page().approved = false;
  render();
  save(false);
}

async function approve() {
  if (!doc) return;
  if (!(await save(true))) return;
  msg("");
  const next = doc.pages.findIndex((p, i) => i > pageIdx && !p.approved);
  const any = doc.pages.findIndex((p) => !p.approved);
  if (next >= 0) showPage(next);
  else if (any >= 0) showPage(any);
  else msg("All pages approved. Press E to export, then F to check the exported file.");
}

async function doExport() {
  if (!doc) return;
  msg("Exporting...");
  try {
    const r = await api(`/api/doc/${doc.doc_id}/export`, {});
    msg(`Exported ${r.doc_id}.pdf (${r.pages} pages, ${r.encoding}). Automated checks passed; now check every exported page by eye.`);
    await loadDocs();
    showExported = true;
    showPage(0);
  } catch (e) { msg(e.message, true); }
}

function deleteSelected() {
  if (!doc || selected < 0) return;
  page().boxes.splice(selected, 1);
  selected = -1;
  edited();
}

function keepSelected() {
  if (!doc || selected < 0) return;
  const b = page().boxes[selected];
  if (!(b.conflict && b.conflict.length)) return;
  b.keep = !b.keep;
  edited();
}

function setDraw(on) {
  drawMode = on;
  $("btn-draw").classList.toggle("on", on);
  $("sheet").classList.toggle("draw", on);
}

function setZoom(z) {
  zoom = Math.max(0.5, Math.min(4, z));
  if (doc) render();
}

function stepDoc(dir) {
  const open = docs.filter((d) => d.original_found);
  const i = doc ? open.findIndex((d) => d.doc_id === doc.doc_id) : -1;
  const next = open[i + dir];
  if (next) openDoc(next.doc_id);
}

// ---- mouse -----------------------------------------------------------
function toPage(ev) {
  const r = $("img-left").getBoundingClientRect();
  const s = page().width / r.width;
  return {
    x: Math.round(Math.max(0, Math.min(r.width, ev.clientX - r.left)) * s),
    y: Math.round(Math.max(0, Math.min(r.height, ev.clientY - r.top)) * s),
  };
}

function norm(d) {
  return {
    x0: Math.min(d.x0, d.x1), y0: Math.min(d.y0, d.y1),
    x1: Math.max(d.x0, d.x1), y1: Math.max(d.y0, d.y1), kind: "manual", source: "manual", keep: false,
  };
}

$("sheet").addEventListener("mousedown", (ev) => {
  if (!doc || ev.button !== 0) return;
  ev.preventDefault();
  const pt = toPage(ev);
  if (drawMode) {
    drag = { x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y };
    return;
  }
  const boxes = page().boxes;
  selected = -1;
  for (let i = boxes.length - 1; i >= 0; i--) {
    const b = boxes[i];
    if (pt.x >= b.x0 && pt.x <= b.x1 && pt.y >= b.y0 && pt.y <= b.y1) { selected = i; break; }
  }
  render();
});

window.addEventListener("mousemove", (ev) => {
  if (!drag) return;
  const pt = toPage(ev);
  drag.x1 = pt.x; drag.y1 = pt.y;
  render();
});

window.addEventListener("mouseup", () => {
  if (!drag) return;
  const b = norm(drag);
  drag = null;
  if (b.x1 - b.x0 >= 4 && b.y1 - b.y0 >= 4) {
    page().boxes.push(b);
    selected = page().boxes.length - 1;
    edited();
  } else render();
});

// Keep both panes scrolled to the same spot.
let syncing = false;
for (const [a, b] of [["scroll-left", "scroll-right"], ["scroll-right", "scroll-left"]]) {
  $(a).addEventListener("scroll", () => {
    if (syncing) { syncing = false; return; }
    if ($(b).scrollTop === $(a).scrollTop && $(b).scrollLeft === $(a).scrollLeft) return;
    syncing = true;
    $(b).scrollTop = $(a).scrollTop;
    $(b).scrollLeft = $(a).scrollLeft;
  });
}

// ---- keyboard and buttons ----------------------------------------------
document.addEventListener("keydown", (ev) => {
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
  const k = ev.key;
  if (k === "a" || k === "A") approve();
  else if (k === "ArrowRight") showPage(pageIdx + 1);
  else if (k === "ArrowLeft") showPage(pageIdx - 1);
  else if (k === "d" || k === "D") setDraw(!drawMode);
  else if (k === "Delete" || k === "Backspace") deleteSelected();
  else if (k === "k" || k === "K") keepSelected();
  else if (k === "+" || k === "=") setZoom(zoom * 1.25);
  else if (k === "-" || k === "_") setZoom(zoom / 1.25);
  else if (k === "0") setZoom(1);
  else if (k === "f" || k === "F") { if (doc) { showExported = !showExported; refreshRight(); } }
  else if (k === "e" || k === "E") { if (!$("btn-export").disabled) doExport(); }
  else if (k === "]") stepDoc(1);
  else if (k === "[") stepDoc(-1);
  else if (k === "Escape") { drag = null; setDraw(false); selected = -1; if (doc) render(); }
  else return;
  ev.preventDefault();
});

$("btn-prev").onclick = () => showPage(pageIdx - 1);
$("btn-next").onclick = () => showPage(pageIdx + 1);
$("btn-approve").onclick = approve;
$("btn-draw").onclick = () => setDraw(!drawMode);
$("btn-delete").onclick = deleteSelected;
$("btn-keep").onclick = keepSelected;
$("btn-zout").onclick = () => setZoom(zoom / 1.25);
$("btn-zfit").onclick = () => setZoom(1);
$("btn-zin").onclick = () => setZoom(zoom * 1.25);
$("btn-final").onclick = () => { if (doc) { showExported = !showExported; refreshRight(); } };
$("btn-export").onclick = doExport;
$("img-left").addEventListener("load", () => doc && render());
$("img-right").addEventListener("error", () => {
  if (showExported) msg("No exported file yet for this document.", true);
});
window.addEventListener("resize", () => doc && render());

loadDocs().then(() => {
  const first = docs.find((d) => d.original_found && !d.exported);
  if (first) openDoc(first.doc_id);
}).catch((e) => msg(e.message, true));
