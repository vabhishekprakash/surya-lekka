// Surya Lekka web app. Plain JavaScript, no build step.
// Every piece of text from a quote is placed with textContent, never as HTML.

const CONFIG = window.SURYA_CONFIG || {};
const API_BASE = String(CONFIG.API_BASE || "").replace(/\/+$/, "");
// pdf.js is pinned to one version and checked against these hashes before it runs.
const PDFJS = {
  url: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.min.mjs",
  integrity: "sha384-+0ti2moQlmLN7WZHE2RHIf5lV8hHxhxEalN0il3YZceG26fUPyOkR0hp9daxk1i7",
};
const PDFJS_WORKER = {
  url: "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38/pdf.worker.min.mjs",
  integrity: "sha384-ToeVvShCxKc6CEvhHeMt0Q8A06pSPDbAlngO9nokrDmh914gk/pYd0N7D0a4Lz2o",
};

// Page limits shared with the API (src/extract/render.py).
const MAX_PAGES = 20;
const MAX_IMAGE_BYTES = 3750000;
const MAX_SIDE = 8000;
const PDF_DPIS = [150, 120, 96, 72];
const PHOTO_SIDE = 3000;
const QUALITIES = [0.85, 0.75, 0.65, 0.55];
const POLL_MS = 2000;
const SLOW_POLL_MS = 4000;
const SLOW_AFTER_MS = 45000;
const MAX_CHARGES = 10;

// The privacy notice, from the Region this copy is deployed in (config.js).
const REGION_NAMES = {
  "ap-south-1": "AWS's Mumbai region (India)",
  "ap-southeast-2": "AWS's Sydney region (Australia)",
};
const PRIVACY = {
  local: "This copy runs on your own computer, so your pages stay on it.",
  inRegion: "Your pages are processed in {where} and deleted after reading. If reading fails, they're removed automatically, usually within two days.",
  crossRegion: "Your pages are stored in {where} and may be read in other AWS regions through cross-Region inference. They're deleted after reading. If reading fails, they're removed automatically, usually within two days.",
};
const READING_UNAVAILABLE = "AI reading isn't available yet. Please type the numbers instead.";

const MODE_LABELS = {
  saved: "Sample (saved reading)",
  nova: "Read by Amazon Nova",
  manual: "Entered by you",
  stub: "Read by the local test stub, not a model",
};

// The names the subsidy rules recognise (src/rules/cfa_rules.json).
const STATES = [
  "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
  "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
  "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
  "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
];
const UNION_TERRITORIES = [
  "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
  "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
];

const QUESTIONS = [
  { name: "consumer_type", text: "Who is the system for?", options: [
    ["individual_household", "An individual household"],
    ["rwa", "A residents' welfare association (RWA)"],
    ["group_housing", "A group housing society"],
    ["other_non_household", "Something else"],
  ] },
  { name: "portal_application_on_or_after_cutoff",
    text: "Did you apply, or will you apply, for the subsidy on the National Portal on or after 13 Feb 2024?",
    yesNo: true },
  { name: "first_system", text: "Is this the first rooftop solar system at this house?", yesNo: true },
  { name: "prior_central_subsidy",
    text: "Has this house received central subsidy for rooftop solar before?", yesNo: true },
  { name: "give_it_up", text: "Are you choosing to give up the central subsidy (the Give It Up option)?",
    yesNo: true },
];

// What the quote says, read by the model and open to correction as answers.
const FLAGS = [
  { name: "multiple_options", text: "Does the quote offer more than one option, such as two panel brands at different prices?",
    options: [["false", "No, one option"], ["true", "Yes, more than one"]] },
  { name: "capacity_basis", text: "What does the system size on the quote measure?",
    options: [["dc_kwp", "The solar panels (DC, kWp)"], ["ac_kw", "The inverter or AC output (kW)"],
      ["unspecified", "The quote doesn't say"]] },
  { name: "gst_treatment", text: "Is GST part of the base price?",
    options: [["included", "Yes, the base price includes GST"], ["excluded", "No, GST is added on top"],
      ["unclear", "The quote doesn't say"]] },
  { name: "extra_charges_complete", text: "Does the quote say there are no other charges?",
    options: [["true", "Yes, it says there are no other charges"], ["false", "No, it doesn't say that"]] },
  { name: "net_cost_subsidy_basis", text: "Which subsidy does the net cost take off?",
    options: [["central", "The central subsidy"], ["central_and_state", "The central and state subsidies"],
      ["state", "The state subsidy"], ["combined", "The single subsidy figure"], ["none", "No subsidy is taken off"],
      ["unspecified", "The quote doesn't say"]] },
];

const AMOUNTS = [
  ["base_price", "Base price"], ["gst_amount", "GST amount"], ["discount", "Discount"], ["gross_total", "Total"],
  ["subsidy_central", "Central subsidy"], ["subsidy_state", "State subsidy"],
  ["subsidy_combined", "Subsidy shown as one figure"], ["subsidy_unspecified", "Subsidy (type not stated)"],
  ["net_cost", "Net cost after subsidy"],
];

const STATUS_ORDER = ["inconsistent", "needs_confirmation", "missing", "out_of_scope", "consistent"];
const STATUS_WORDS = {
  inconsistent: ["Doesn't match", "These don't match"],
  needs_confirmation: ["Needs checking", "These need checking or an answer from you"],
  missing: ["Missing", "These are missing from the quote"],
  out_of_scope: ["Not checked", "These are outside what this tool checks"],
  consistent: ["Matches", "These match"],
};
const CHECK_TITLES = {
  C1_capacity: "System size",
  C2_central_subsidy: "Central subsidy",
  C3_gross_total: "Total price",
  C3_net_cost: "Net cost after subsidy",
  C4_missing_details: "Details on the quote",
};
const ITEM_TITLES = {
  panel_wattage: "Panel wattage", panel_count: "Number of panels", module_make_model: "Panel make and model",
  dcr_declaration: "DCR declaration", inverter_make_model: "Inverter make and model",
  inverter_rating: "Inverter rating", vendor_registration: "Vendor registration number", gst_basis: "GST",
  extras_outside_total: "Charges outside the total", net_meter: "Net-meter charges",
};
const FIELD_LABELS = {
  "module_groups.count": "Number of panels", "module_groups.wattage_w": "Panel wattage",
  "module_groups.wattage": "Panel wattage", "module_groups.make_model": "Panel make and model",
  "inverters.make_model": "Inverter make and model", "inverters.rating_kw": "Inverter rating",
  "inverters.rating_kva": "Inverter rating", "inverters.rating": "Inverter rating",
  stated_capacity_kw: "System size", stated_capacity: "System size", capacity_basis: "What the size measures",
  dcr_declaration: "DCR declaration", vendor_registration: "Vendor registration number",
  gst_treatment: "GST basis", extra_charges_complete: "No other charges",
  "extra_charges.amount": "Extra charge", "extra_charges.label": "Extra charge",
  "extra_charges.included_in_total": "Extra charge inside the total", net_cost_subsidy_basis: "Subsidy the net cost takes off",
  multiple_options: "More than one option", consumer_type: "Who the system is for", give_it_up: "Give It Up",
  state: "State", selected_option: "Option",
  ...Object.fromEntries(AMOUNTS),
};
const VALUE_WORDS = Object.fromEntries([
  ...FLAGS.flatMap((f) => f.options),
  ...QUESTIONS[0].options,
  ["yes", "Inside the total"], ["no", "Outside the total"], ["unclear", "Not clear"],
]);
const COMPUTED = {
  dc_kwp: ["Panel capacity worked out", "kwp"],
  dc_kwp_range: ["Panel capacity worked out", "kwp"],
  central_cfa_rule: ["Central subsidy under the rule", "inr"],
  cfa_range: ["Central subsidy under the rule", "inr"],
  "gross total": ["Total worked out", "inr"],
  "net cost": ["Net cost worked out", "inr"],
};
const RULES = {
  "CFA-RES-GENERAL": "central subsidy (CFA) rates for an individual household in a general-category state or UT, from the MNRE PM Surya Ghar guidelines",
  "CFA-RES-SPECIAL": "central subsidy (CFA) rates for an individual household in a special-category state or UT, from the MNRE PM Surya Ghar guidelines",
};
const FAILURES = {
  timed_out: "Reading took too long and was stopped.",
  model_busy: "The reading service was busy.",
  model_unavailable: "The reading service wasn't available.",
  extraction_failed: "The pages couldn't be read.",
  model_rejected_request: "The pages couldn't be read.",
  model_access_denied: "The reading service isn't available right now.",
  not_a_jpeg: "The pages didn't arrive as images.",
  bad_manifest: "The upload didn't finish properly.",
  missing_upload: "Some pages didn't arrive.",
  upload_too_large: "A page was too large.",
  result_too_large: "This quote has more detail than can be stored.",
  storage_error: "Something went wrong on our side.",
  internal_error: "Something went wrong on our side.",
  reading_unavailable: "Reading was switched off before this quote was read.",
};

const state = {
  mode: null,
  job: null,
  extraction: null,
  pages: [],
  pageText: null,
  option: "",
  result: null,
  pollTimer: null,
  view: "home",
  editView: null,
};

// ---------------------------------------------------------------- helpers

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(method, path, body) {
  const init = { method };
  if (body !== undefined) {
    init.headers = { "content-type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(0, "network", "The checker couldn't be reached. Check your connection and try again.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    throw new ApiError(res.status, (data && data.error) || `http_${res.status}`,
      (data && data.message) || "Something went wrong. Please try again.");
  }
  return data;
}

function jobPath(suffix = "") {
  return `/jobs/${encodeURIComponent(state.job.job_id)}${suffix}?t=${encodeURIComponent(state.job.token)}`;
}

function formatInr(text) {
  const n = Number(text);
  if (text === null || text === "" || !Number.isFinite(n)) return String(text);
  return "Rs " + n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

function formatNumber(text) {
  const n = Number(text);
  return Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 3 }) : String(text);
}

function formatDate(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

function fieldLabel(name) {
  const key = String(name).replace(/\[[^\]]*\]/g, "");
  return FIELD_LABELS[key] || key.replace(/_/g, " ");
}

function valueText(value, raw) {
  if (raw !== undefined && raw !== null && raw !== "") return String(raw);
  if (value === true) return "Yes";
  if (value === false) return "No";
  if (value === null || value === undefined) return "not found";
  if (typeof value === "object") return value.raw ? String(value.raw) : "unclear";
  return VALUE_WORDS[String(value)] || String(value);
}

// ---------------------------------------------------------------- views

function show(view) {
  if (state.view === "wait" && view !== "wait") stopPolling();
  state.view = view;
  for (const section of $$("[data-view]")) section.hidden = section.dataset.view !== view;
  for (const label of $$("[data-mode-label]")) label.textContent = MODE_LABELS[state.mode] || "";
  window.scrollTo(0, 0);
  const heading = $(`[data-view="${view}"] h1, [data-view="${view}"] h2`);
  if (heading && view !== "home") heading.focus({ preventScroll: true });
}

function canShow(view) {
  if (view === "review") return Boolean(state.extraction);
  if (view === "results") return Boolean(state.result);
  return ["home", "upload", "manual"].includes(view);
}

function go(view, push = true) {
  if (!canShow(view)) view = "home";
  if (view === "upload") resetUpload();
  show(view);
  if (push) history.pushState({ view }, "", `#${view}`);
}

window.addEventListener("popstate", (event) => {
  const view = (event.state && event.state.view) || location.hash.slice(1) || "home";
  go(view, false);
});

function showProblem(title, message, { retry = false, sample = false } = {}) {
  $("#problem-title").textContent = title;
  $("#problem-message").textContent = message;
  $("#problem-retry").hidden = !retry;
  $("#problem-sample").hidden = !sample;
  show("problem");
}

function showApiError(error) {
  if (error.code === "reading_unavailable") {
    showProblem("Quote reading is off for now", READING_UNAVAILABLE, { sample: true });
  } else if (error.status === 429) {
    showProblem("Daily limit reached", error.message, { sample: true });
  } else if (error.status === 503) {
    showProblem("New checks are paused", error.message, { sample: true });
  } else if (error.code === "network") {
    showProblem("The checker couldn't be reached", error.message);
  } else if (error.status === 404 && error.code === "not_found") {
    showProblem("This check is no longer available", "Please start again.");
  } else {
    showProblem("Something went wrong", error.message);
  }
}

// ---------------------------------------------------------------- samples

async function startSample(sampleId) {
  const buttons = $$("[data-sample]");
  buttons.forEach((b) => { b.disabled = true; });
  resetUpload();  // a sample has no page images of its own
  try {
    const job = await api("POST", `/samples/${encodeURIComponent(sampleId)}`);
    newJob(job, job.mode);
    const view = await api("GET", jobPath());
    if (view.status === "done") openReview(view);
    else startWaiting();
  } catch (error) {
    showApiError(error);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

function newJob(job, mode) {
  state.job = { job_id: job.job_id, token: job.token };
  state.mode = mode;
  state.extraction = null;
  state.result = null;
  state.pageText = null;
  state.option = "";
}

// ---------------------------------------------------------------- upload

let pdfjsLoading = null;

// The module goes through a modulepreload link with integrity and crossorigin
// attributes, so the browser checks its hash before import() runs it (the import
// map in index.html holds the same hash). The worker is fetched with the same
// check and handed to pdf.js as a local blob.
function preloadModule({ url, integrity }) {
  return new Promise((resolve, reject) => {
    const link = el("link", { rel: "modulepreload", href: url, integrity, crossorigin: "anonymous" });
    if (!link.relList.supports("modulepreload")) {
      reject(new Error("modulepreload is not supported"));
      return;
    }
    link.addEventListener("load", resolve);
    link.addEventListener("error", () => reject(new Error("pdf.js failed its integrity check or didn't load")));
    document.head.append(link);
  });
}

async function verifiedScriptUrl({ url, integrity }) {
  const res = await fetch(url, { integrity, mode: "cors", credentials: "omit" });
  if (!res.ok) throw new Error("the pdf.js worker didn't load");
  return URL.createObjectURL(new Blob([await res.blob()], { type: "text/javascript" }));
}

function loadPdfjs() {
  if (!pdfjsLoading) {
    pdfjsLoading = (async () => {
      const [, workerSrc] = await Promise.all([preloadModule(PDFJS), verifiedScriptUrl(PDFJS_WORKER)]);
      const pdfjs = await import(PDFJS.url);
      pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;
      return pdfjs;
    })();
    pdfjsLoading.catch(() => { pdfjsLoading = null; });
  }
  return pdfjsLoading;
}

function resetUpload() {
  for (const page of state.pages) URL.revokeObjectURL(page.url);
  state.pages = [];
  $("#file-input").value = "";
  $("#upload-status").textContent = "";
  $("#upload-status").classList.remove("error");
  $("#upload-notes").replaceChildren();
  $("#page-previews").replaceChildren();
  $("#send-pages").hidden = true;
}

function uploadStatus(text, isError = false) {
  const status = $("#upload-status");
  status.textContent = text;
  status.classList.toggle("error", isError);
}

function canvasToBlob(canvas, quality) {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

async function smallEnoughJpeg(canvas) {
  for (const quality of QUALITIES) {
    const blob = await canvasToBlob(canvas, quality);
    if (blob && blob.size <= MAX_IMAGE_BYTES) return blob;
  }
  return null;
}

function whiteCanvas(width, height) {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.min(MAX_SIDE, Math.floor(width)));
  canvas.height = Math.max(1, Math.min(MAX_SIDE, Math.floor(height)));
  const ctx = canvas.getContext("2d", { alpha: false });
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  return [canvas, ctx];
}

async function pdfPageToJpeg(page) {
  const base = page.getViewport({ scale: 1 });
  for (const dpi of PDF_DPIS) {
    let scale = dpi / 72;
    const longest = Math.max(base.width, base.height) * scale;
    if (longest > MAX_SIDE) scale *= MAX_SIDE / longest;
    const viewport = page.getViewport({ scale });
    const [canvas, ctx] = whiteCanvas(viewport.width, viewport.height);
    await page.render({ canvasContext: ctx, viewport }).promise;
    const blob = await smallEnoughJpeg(canvas);
    canvas.width = 0;
    canvas.height = 0;
    if (blob) return blob;
  }
  return null;
}

async function renderPdf(file, room, onPage) {
  const pdfjs = await loadPdfjs();
  const data = new Uint8Array(await file.arrayBuffer());
  const doc = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
  const blobs = [];
  const total = doc.numPages;
  try {
    for (let n = 1; n <= Math.min(total, room); n += 1) {
      onPage(n, Math.min(total, room));
      const page = await doc.getPage(n);
      blobs.push(await pdfPageToJpeg(page));
      page.cleanup();
    }
  } finally {
    await doc.destroy();
  }
  return { blobs, total };
}

async function photoToJpeg(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    let scale = Math.min(1, PHOTO_SIDE / Math.max(img.naturalWidth, img.naturalHeight));
    for (let attempt = 0; attempt < 5; attempt += 1, scale *= 0.8) {
      const [canvas, ctx] = whiteCanvas(img.naturalWidth * scale, img.naturalHeight * scale);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const blob = await smallEnoughJpeg(canvas);
      canvas.width = 0;
      canvas.height = 0;
      if (blob) return blob;
    }
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function prepareFiles(files) {
  resetUpload();
  const notes = [];
  const blobs = [];
  for (const file of files) {
    const room = MAX_PAGES - blobs.length;
    if (room <= 0) {
      notes.push(`Only the first ${MAX_PAGES} pages are read. ${file.name} was left out.`);
      continue;
    }
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    try {
      if (isPdf) {
        const { blobs: pdfPages, total } = await renderPdf(file, room, (n, of) => {
          uploadStatus(`Preparing page ${n} of ${of} from ${file.name}`);
        });
        if (total > room) notes.push(`Only the first ${MAX_PAGES} pages are read. Pages after that were left out.`);
        pdfPages.forEach((blob, i) => {
          if (blob) blobs.push(blob);
          else notes.push(`Page ${i + 1} of ${file.name} was too large to send and was left out.`);
        });
      } else {
        uploadStatus(`Preparing ${file.name}`);
        const blob = await photoToJpeg(file);
        if (blob) blobs.push(blob);
        else notes.push(`${file.name} was too large to send and was left out.`);
      }
    } catch {
      notes.push(isPdf
        ? `${file.name} couldn't be opened here. Try photos of each page, or type the numbers instead.`
        : `${file.name} couldn't be opened here. Use a PDF, JPEG or PNG, or type the numbers instead.`);
    }
  }
  state.pages = blobs.map((blob, i) => ({ page: i + 1, blob, url: URL.createObjectURL(blob) }));
  $("#upload-notes").replaceChildren(...notes.map((note) => el("li", { text: note })));
  $("#page-previews").replaceChildren(...state.pages.map((p) => el("figure", {},
    el("img", { src: p.url, alt: `Page ${p.page}` }), el("figcaption", { text: `Page ${p.page}` }))));
  if (state.pages.length) {
    uploadStatus(`${state.pages.length} ${state.pages.length === 1 ? "page is" : "pages are"} ready.`);
    $("#send-pages").hidden = false;
  } else {
    uploadStatus("No pages could be prepared from that file.", true);
  }
}

async function postForm(target, blob) {
  const form = new FormData();
  for (const [key, value] of Object.entries(target.fields)) form.append(key, value);
  form.append("file", blob);
  let res;
  try {
    res = await fetch(target.url, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, "network", "A page didn't upload. Check your connection and try again.");
  }
  if (!res.ok) throw new ApiError(res.status, "upload_failed", "A page didn't upload. Please try again.");
}

async function sendPages() {
  const button = $("#send-pages");
  button.disabled = true;
  const pages = state.pages;
  try {
    uploadStatus("Starting the check");
    const job = await api("POST", "/jobs", { page_count: pages.length });
    newJob(job, job.mode || "nova");
    for (const target of job.uploads) {
      uploadStatus(`Uploading page ${target.page} of ${pages.length}`);
      await postForm(target, pages[target.page - 1].blob);
    }
    const manifest = new Blob([JSON.stringify(job.manifest_body)], { type: "application/json" });
    await postForm(job.manifest, manifest);
    startWaiting();
  } catch (error) {
    if (error.code === "upload_failed" || error.code === "network") uploadStatus(error.message, true);
    else showApiError(error);
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- waiting

function stopPolling() {
  clearTimeout(state.pollTimer);
  state.pollTimer = null;
}

function startWaiting() {
  show("wait");
  $("#wait-slow").hidden = true;
  const started = Date.now();
  let misses = 0;
  const tick = async () => {
    if (state.view !== "wait") return;
    try {
      const view = await api("GET", jobPath());
      misses = 0;
      if (state.view !== "wait") return;
      if (view.status === "done") return openReview(view);
      if (view.status === "failed") return showFailed(view);
    } catch (error) {
      if (error.status === 404 || (misses += 1) >= 5) return showApiError(error);
    }
    const waited = Date.now() - started;
    $("#wait-slow").hidden = waited < SLOW_AFTER_MS;
    state.pollTimer = setTimeout(tick, waited > 2 * SLOW_AFTER_MS ? SLOW_POLL_MS : POLL_MS);
  };
  tick();
}

function showFailed(view) {
  const why = FAILURES[view.reason] || "The quote couldn't be read.";
  const next = view.retryable
    ? " You can try again, or type the numbers from the quote yourself."
    : " You can type the numbers from the quote yourself instead.";
  showProblem("The quote couldn't be read", why + next, { retry: Boolean(view.retryable) });
}

async function retryJob() {
  const button = $("#problem-retry");
  button.disabled = true;
  try {
    await api("POST", jobPath("/retry"));
    startWaiting();
  } catch (error) {
    showApiError(error);
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- page viewer

function pageButton(pageNumber, evidenceText) {
  if (!pageNumber) return null;
  const image = state.pages[pageNumber - 1];
  if (image) {
    return el("button", { type: "button", class: "thumb", "aria-label": `Show page ${pageNumber}`,
      onclick: () => openPage(pageNumber, evidenceText) },
    el("img", { src: image.url, alt: "" }));
  }
  if (state.pageText && state.pageText[String(pageNumber)]) {
    return el("button", { type: "button", class: "button secondary text-thumb",
      onclick: () => openPage(pageNumber, evidenceText) }, `Page ${pageNumber}`);
  }
  return null;
}

function openPage(pageNumber, evidenceText) {
  const dialog = $("#page-viewer");
  $("#page-viewer-title").textContent = `Page ${pageNumber}`;
  const body = $("#page-viewer-body");
  const image = state.pages[pageNumber - 1];
  if (image) {
    body.replaceChildren(el("img", { src: image.url, alt: `Page ${pageNumber} of your quote` }));
  } else {
    const lines = state.pageText[String(pageNumber)] || [];
    const wanted = String(evidenceText || "").trim();
    body.replaceChildren(el("div", { class: "page-text" }, lines.map((line) => {
      const hit = wanted && (line.includes(wanted) || wanted.includes(line));
      return [hit ? el("mark", { text: line }) : line, "\n"];
    })));
  }
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

function closePage() {
  const dialog = $("#page-viewer");
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

// ---------------------------------------------------------------- review

function optionIds(q) {
  const ids = new Set(Object.keys(q.option_fields || {}));
  for (const o of q.options || []) if (o.option_id) ids.add(o.option_id);
  for (const key of ["module_groups", "inverters", "extra_charges"]) {
    for (const item of q[key] || []) if (item.option_id) ids.add(item.option_id);
  }
  return [...ids];
}

function optionLabel(q, id) {
  const option = (q.options || []).find((o) => o.option_id === id);
  return (option && option.label && option.label.value) || id;
}

function pick(q, name) {
  const own = (q.option_fields || {})[state.option] || {};
  return name in own ? own[name] : q[name];
}

function forOption(items) {
  return (items || []).filter((item) => !state.option || !item.option_id || item.option_id === state.option);
}

function inputValue(field) {
  if (!field || field.conflict) return "";
  const v = field.value;
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return v.raw || "";
  return String(v);
}

function evidenceLine(field) {
  if (!field || field.value === null || field.value === undefined) {
    return el("div", { class: "evidence" }, el("span", { text: "Not found on the quote." }));
  }
  if (field.conflict) {
    return el("div", { class: "evidence" },
      el("span", { class: "warn", text: "Different pages give different values. Enter the right one." }));
  }
  const parts = [];
  if (field.evidence_text) {
    parts.push(el("span", {}, field.page ? `Page ${field.page}: ` : "", el("q", { text: field.evidence_text })));
  }
  const status = field.value && field.value.parse_status;
  if (status && status !== "ok" && status !== "empty") {
    parts.push(el("span", { class: "warn", text: " This couldn't be read as a number. Please check it." }));
  }
  return el("div", { class: "evidence" }, pageButton(field.page, field.evidence_text), el("div", {}, parts));
}

function textRow(path, label, field, extra = {}) {
  const id = `f-${path.replace(/[^a-z0-9]+/gi, "-")}`;
  const original = inputValue(field);
  return el("div", { class: "row" },
    el("label", { for: id, text: label }),
    el("input", { id, value: original, "data-path": path, "data-original": original,
      "data-kind": extra.kind || "text", inputmode: extra.inputmode, autocomplete: "off",
      placeholder: "Not on the quote" }),
    evidenceLine(field));
}

function selectRow(path, label, field, options) {
  const id = `f-${path.replace(/[^a-z0-9]+/gi, "-")}`;
  const v = field && !field.conflict ? field.value : null;
  const original = v === null || v === undefined ? "" : String(v);
  const select = el("select", { id, "data-path": path, "data-original": original, "data-kind": "choice" },
    options.map(([value, text]) => el("option", { value, text })));
  select.value = original;
  return el("div", { class: "row" }, el("label", { for: id, text: label }), select, evidenceLine(field));
}

const DCR_CHOICES = [["", "The quote doesn't say"], ["true", "Yes, DCR panels and cells"], ["false", "It says they are not DCR"]];
const INCLUDED_CHOICES = [["", "Not stated"], ["yes", "Inside the total"], ["no", "Outside the total"], ["unclear", "Not clear"]];

function renderReviewFields() {
  const q = state.extraction;
  const groups = [];
  const panels = forOption(q.module_groups);
  const inverters = forOption(q.inverters);
  const charges = forOption(q.extra_charges);
  const several = (list) => list.length > 1;

  groups.push(["System size", [textRow("stated_capacity", "System size on the quote", pick(q, "stated_capacity"))]]);
  groups.push(["Panels", panels.flatMap((g, i) => {
    const n = several(panels) ? ` (group ${i + 1})` : "";
    return [
      textRow(`module_groups[${g.group_id}].count`, `Number of panels${n}`, g.count, { kind: "count", inputmode: "numeric" }),
      textRow(`module_groups[${g.group_id}].wattage`, `Wattage of each panel${n}`, g.wattage),
      textRow(`module_groups[${g.group_id}].make_model`, `Panel make and model${n}`, g.make_model),
    ];
  })]);
  groups.push(["Inverter", inverters.flatMap((inv, i) => {
    const n = several(inverters) ? ` (${i + 1})` : "";
    return [
      textRow(`inverters[${inv.inverter_id}].make_model`, `Inverter make and model${n}`, inv.make_model),
      textRow(`inverters[${inv.inverter_id}].rating`, `Inverter rating${n}`, inv.rating),
    ];
  })]);
  const priceRows = [
    textRow("base_price", "Base price", pick(q, "base_price"), { inputmode: "decimal" }),
    textRow("gst_amount", "GST amount", pick(q, "gst_amount"), { inputmode: "decimal" }),
    ...charges.flatMap((c) => [
      textRow(`extra_charges[${c.charge_id}].label`, "Extra charge", c.label),
      textRow(`extra_charges[${c.charge_id}].amount`, "Amount", c.amount, { inputmode: "decimal" }),
      selectRow(`extra_charges[${c.charge_id}].included_in_total`, "Is it inside the total?", c.included_in_total,
        INCLUDED_CHOICES),
    ]),
    textRow("discount", "Discount", pick(q, "discount"), { inputmode: "decimal" }),
    textRow("gross_total", "Total", pick(q, "gross_total"), { inputmode: "decimal" }),
  ];
  groups.push(["Price", priceRows]);
  const subsidyRows = AMOUNTS.filter(([name]) => name.startsWith("subsidy_") || name === "net_cost")
    .filter(([name]) => ["subsidy_central", "subsidy_state", "net_cost"].includes(name)
      || inputValue(pick(q, name)) !== "")
    .map(([name, label]) => textRow(name, label, pick(q, name), { inputmode: "decimal" }));
  groups.push(["Subsidy and net cost", subsidyRows]);
  groups.push(["Other details", [
    selectRow("dcr_declaration", "DCR declaration (panels and cells made in India)", q.dcr_declaration, DCR_CHOICES),
    textRow("vendor_registration", "Vendor registration number", q.vendor_registration),
  ]]);

  $("#review-fields").replaceChildren(...groups.filter(([, rows]) => rows.length).map(([title, rows]) =>
    el("fieldset", { class: "group" }, el("legend", { text: title }), rows)));
}

function renderReviewFlags() {
  const proposed = ((state.extraction.flags || {}).model_proposed) || {};
  $("#review-flags").replaceChildren(...FLAGS.map((flag) => {
    const field = proposed[flag.name];
    const v = field && !field.conflict ? field.value : null;
    const original = v === null || v === undefined ? "" : String(v);
    const id = `flag-${flag.name}`;
    const select = el("select", { id, "data-flag": flag.name, "data-original": original },
      el("option", { value: "", text: "Not sure" }),
      flag.options.map(([value, text]) => el("option", { value, text })));
    select.value = original;
    return el("div", { class: "row" }, el("label", { for: id, text: flag.text }), select,
      field && field.evidence_text ? evidenceLine(field) : null);
  }));
}

function renderOptionChoice() {
  const q = state.extraction;
  const ids = optionIds(q);
  if (!ids.length) {
    $("#review-option").replaceChildren();
    return;
  }
  const select = el("select", { id: "review-option-select" },
    el("option", { value: "", text: "Choose the option you are considering" }),
    ids.map((id) => el("option", { value: id, text: optionLabel(q, id) })));
  select.value = state.option;
  select.addEventListener("change", () => {
    state.option = select.value;
    renderReviewFields();
  });
  $("#review-option").replaceChildren(el("fieldset", { class: "group" }, el("legend", { text: "Options on the quote" }),
    el("label", { class: "field", for: "review-option-select" }, "This quote offers more than one option. Which one do you want to check?", select)));
}

function openReview(view) {
  state.mode = view.mode || state.mode;
  state.extraction = view.extraction;
  state.pageText = view.page_text || null;
  state.option = "";
  const notes = [];
  const skipped = view.extraction.pages_skipped || [];
  if (!view.processing_complete) {
    notes.push(skipped.length
      ? `Some pages couldn't be read (page ${skipped.join(", ")}). Checks that need the whole quote will ask you to confirm.`
      : "Some of the quote couldn't be read. Checks that need the whole quote will ask you to confirm.");
  }
  if (state.mode === "saved") notes.push("This is a made-up sample quote. Its values were saved in advance.");
  $("#review-notes").replaceChildren(...notes.map((text) => el("p", { class: "hint", text })));
  renderOptionChoice();
  renderReviewFields();
  renderReviewFlags();
  $("#review-status").textContent = "";
  state.editView = "review";
  go("review");
}

function correctionValue(input) {
  const value = input.value.trim();
  if (input.dataset.kind === "choice") {
    if (value === "") return null;
    if (value === "true" || value === "false") return value === "true";
    return value;
  }
  if (value === "") return null;
  if (input.dataset.kind === "count" && /^\d+$/.test(value)) return Number(value);
  return value;
}

function answerValue(value) {
  if (value === "true") return true;
  if (value === "false") return false;
  return value;
}

function collectAnswers(root) {
  const answers = {};
  const stateSelect = $("select[data-question='state']", root);
  if (stateSelect && stateSelect.value) answers.state = stateSelect.value;
  for (const question of QUESTIONS) {
    const checked = $(`input[data-question='${question.name}']:checked`, root);
    if (checked && checked.value !== "") answers[question.name] = answerValue(checked.value);
  }
  return answers;
}

function collectReview() {
  const corrections = {};
  for (const input of $$("#review-fields [data-path]")) {
    if (input.value.trim() !== input.dataset.original.trim()) {
      corrections[input.dataset.path] = correctionValue(input);
    }
  }
  const answers = collectAnswers($("#view-review"));
  for (const select of $$("#review-flags [data-flag]")) {
    if (select.value !== "" && select.value !== select.dataset.original) {
      answers[select.dataset.flag] = answerValue(select.value);
    }
  }
  if (state.option) answers.selected_option = state.option;
  return { corrections, answers };
}

async function submitReview(event) {
  event.preventDefault();
  const status = $("#review-status");
  const button = $("#review-form button[type=submit]");
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Running the checks";
  try {
    const result = await api("POST", jobPath("/checks"), collectReview());
    status.textContent = "";
    openResults(result);
  } catch (error) {
    if (error.status === 400) {
      status.textContent = `${error.message} Please check the values you changed.`;
      status.classList.add("error");
    } else {
      status.textContent = "";
      showApiError(error);
    }
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- questions

function renderQuestions(root, prefix) {
  const stateId = `${prefix}-state`;
  const stateSelect = el("select", { id: stateId, "data-question": "state" },
    el("option", { value: "", text: "Choose a state or union territory" }),
    el("optgroup", { label: "States" }, STATES.map((s) => el("option", { value: s, text: s }))),
    el("optgroup", { label: "Union territories" }, UNION_TERRITORIES.map((s) => el("option", { value: s, text: s }))));
  const sets = QUESTIONS.map((question) => {
    const choices = question.yesNo ? [["true", "Yes"], ["false", "No"], ["", "Not sure"]]
      : [...question.options, ["", "Not sure"]];
    return el("fieldset", { class: "radio-set" }, el("legend", { text: question.text }),
      el("div", { class: "opts" }, choices.map(([value, text]) => el("label", {},
        el("input", { type: "radio", name: `${prefix}-${question.name}`, value, "data-question": question.name,
          checked: value === "" }),
        text))));
  });
  root.replaceChildren(
    el("label", { class: "field", for: stateId }, "Which state or union territory is the house in?", stateSelect),
    ...sets);
}

// ---------------------------------------------------------------- manual entry

function addCharge(values = {}) {
  const box = $("#manual-charges");
  if (box.children.length >= MAX_CHARGES) return;
  const n = box.children.length + 1;
  const row = el("div", { class: "charge" },
    el("div", { class: "charge-row" },
      el("label", { class: "field" }, `Extra charge ${n} (for example net meter)`,
        el("input", { "data-charge": "label", value: values.label || "", autocomplete: "off" })),
      el("label", { class: "field" }, "Amount",
        el("input", { "data-charge": "amount", inputmode: "decimal", placeholder: "Rs", value: values.amount || "",
          autocomplete: "off" })),
      el("label", { class: "field" }, "Inside the total?",
        el("select", { "data-charge": "included_in_total" },
          INCLUDED_CHOICES.map(([value, text]) => el("option", { value, text }))))));
  box.append(row);
  $("#add-charge").hidden = box.children.length >= MAX_CHARGES;
}

function collectManual() {
  const form = $("#manual-form");
  const fields = {};
  for (const input of $$("input[name]:not([data-question])", form)) {
    if (input.value.trim()) fields[input.name] = input.value.trim();
  }
  const dcr = form.elements.dcr_declaration.value;
  if (dcr) fields.dcr_declaration = dcr === "true";
  const charges = $$(".charge", form).map((row) => {
    const charge = {};
    const label = $("[data-charge=label]", row).value.trim();
    const amount = $("[data-charge=amount]", row).value.trim();
    const included = $("[data-charge=included_in_total]", row).value;
    if (label) charge.label = label;
    if (amount) charge.amount = amount;
    if (included) charge.included_in_total = included;
    return charge;
  }).filter((charge) => Object.keys(charge).length);
  if (charges.length) fields.extra_charges = charges;
  const answers = collectAnswers($("#view-manual"));
  for (const select of $$("select[data-answer]", form)) {
    if (select.value) answers[select.name] = answerValue(select.value);
  }
  return { fields, answers };
}

async function submitManual(event) {
  event.preventDefault();
  const status = $("#manual-status");
  const button = $("#manual-form button[type=submit]");
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Running the checks";
  try {
    const result = await api("POST", "/checks", collectManual());
    status.textContent = "";
    state.mode = "manual";
    state.job = null;
    state.extraction = null;
    state.pages = [];
    state.pageText = null;
    state.editView = "manual";
    openResults(result);
  } catch (error) {
    if (error.status === 400) {
      status.textContent = error.message;
      status.classList.add("error");
    } else {
      status.textContent = "";
      showApiError(error);
    }
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- results

function evidenceItem(e) {
  if (e.kind === "computed") {
    const [label, unit] = COMPUTED[e.name] || [e.name, ""];
    const value = unit === "inr" ? (String(e.value).includes("-") ? `Rs ${e.value}` : formatInr(e.value))
      : unit === "kwp" ? `${String(e.value).includes("-") ? e.value : formatNumber(e.value)} kWp` : e.value;
    return el("li", {}, `${label}: ${value} `, el("span", { class: "note", text: `(${e.formula})` }));
  }
  const label = fieldLabel(e.field);
  if (e.kind === "user_corrected") {
    const was = e.original_value !== undefined && e.original_value !== null && state.mode !== "manual"
      ? `; the reading was ${valueText(e.original_value)}` : "";
    return el("li", { text: `${label}: ${valueText(e.value, e.raw)} (you entered this${was})` });
  }
  if (e.kind === "user_confirmed") {
    return el("li", { text: `${label}: ${valueText(e.value)} (your answer)` });
  }
  if (!e.evidence_text) return el("li", { text: `${label}: ${valueText(e.value, e.raw)}` });
  return el("li", {}, `${label}: `, e.page ? `page ${e.page}, ` : "", el("q", { text: e.evidence_text }), " ",
    pageButton(e.page, e.evidence_text));
}

function findingCard(f) {
  const title = f.item ? (ITEM_TITLES[f.item] || f.item) : (CHECK_TITLES[f.check_id] || f.check_id);
  const evidence = (f.evidence || []).map(evidenceItem);
  const rule = f.rule_id && RULES[f.rule_id]
    ? el("p", { class: "rule",
      text: `Rule: ${RULES[f.rule_id]}. It applies to applications on the National Portal from ${formatDate(f.rule_date)}.` })
    : f.rule_id ? el("p", { class: "rule", text: `Rule: ${f.rule_id}${f.rule_date ? `, dated ${formatDate(f.rule_date)}` : ""}.` })
      : null;
  return el("article", { class: `finding status-${f.status}` },
    el("h4", {}, title, " ", el("span", { class: `badge status-${f.status}`, text: STATUS_WORDS[f.status][0] })),
    el("p", { text: f.message }),
    evidence.length ? el("ul", {}, evidence) : null,
    rule,
    (f.notes || []).map((note) => el("p", { class: "note", text: note })));
}

function renderVendor(result) {
  const box = $("#vendor-questions");
  if (!result.vendor_message) {
    box.replaceChildren(el("p", { text: "These checks didn't raise any questions for the vendor." }));
    return;
  }
  const copyStatus = el("span", { class: "status", role: "status", "aria-live": "polite" });
  const copy = el("button", { type: "button", class: "button secondary", onclick: async () => {
    try {
      await navigator.clipboard.writeText(result.vendor_message);
      copyStatus.textContent = "Copied.";
    } catch {
      const area = $("textarea", box);
      area.hidden = false;
      area.select();
      copyStatus.textContent = "Select the message and copy it.";
    }
  } }, "Copy");
  const whatsapp = el("a", { class: "button", target: "_blank", rel: "noopener noreferrer",
    href: `https://wa.me/?text=${encodeURIComponent(result.vendor_message)}` }, "Send on WhatsApp");
  box.replaceChildren(
    el("ol", {}, (result.questions || []).map((q) => el("li", { text: q.text }))),
    el("p", { class: "hint", text: "The message below asks these questions politely. Send it to your vendor." }),
    el("div", { class: "message", text: result.vendor_message }),
    el("textarea", { class: "message", rows: 6, readonly: true, hidden: true, "aria-label": "Message for your vendor" },
      result.vendor_message),
    el("div", { class: "actions" }, copy, whatsapp, copyStatus));
}

function openResults(result) {
  state.result = result;
  const findings = result.findings || [];
  const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, findings.filter((f) => f.status === s).length]));
  const phrases = {
    inconsistent: ["doesn't match", "don't match"], needs_confirmation: ["needs checking", "need checking"],
    missing: ["missing", "missing"], out_of_scope: ["not checked", "not checked"], consistent: ["matches", "match"],
  };
  const parts = STATUS_ORDER.filter((s) => counts[s])
    .map((s) => `${counts[s]} ${phrases[s][counts[s] === 1 ? 0 : 1]}`);
  $("#results-summary").textContent = parts.length ? `Results: ${parts.join(", ")}.` : "";
  $("#results-groups").replaceChildren(...STATUS_ORDER.filter((s) => counts[s]).map((status) =>
    el("section", { class: "finding-group" },
      el("h3", { text: STATUS_WORDS[status][1] }),
      findings.filter((f) => f.status === status).map(findingCard))));
  renderVendor(result);
  go("results");
}

// ---------------------------------------------------------------- start

function privacyLine(region, crossRegion) {
  if (!region) return PRIVACY.local;
  const where = REGION_NAMES[region] || `AWS's ${region} region`;
  return (crossRegion ? PRIVACY.crossRegion : PRIVACY.inRegion).replace("{where}", where);
}

function init() {
  $("#privacy-line").textContent = privacyLine(String(CONFIG.REGION || ""), CONFIG.CROSS_REGION === true);
  document.addEventListener("click", (event) => {
    const target = event.target.closest("[data-go]");
    if (!target) return;
    event.preventDefault();
    go(target.dataset.go);
  });
  for (const button of $$("[data-sample]")) button.addEventListener("click", () => startSample(button.dataset.sample));
  $("#file-input").addEventListener("change", (event) => {
    const files = [...event.target.files];
    if (files.length) prepareFiles(files);
  });
  $("#send-pages").addEventListener("click", sendPages);
  $("#problem-retry").addEventListener("click", retryJob);
  $("#review-form").addEventListener("submit", submitReview);
  $("#manual-form").addEventListener("submit", submitManual);
  $("#add-charge").addEventListener("click", () => addCharge());
  $("#results-edit").addEventListener("click", () => go(state.editView || "home"));
  $("#page-viewer-close").addEventListener("click", closePage);
  renderQuestions($("[data-questions=review]"), "review");
  renderQuestions($("[data-questions=manual]"), "manual");
  addCharge();
  const start = location.hash.slice(1);
  go(["upload", "manual"].includes(start) ? start : "home", false);
  history.replaceState({ view: state.view }, "", `#${state.view}`);
}

init();
