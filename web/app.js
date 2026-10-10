// Surya Lekka web app. Plain JavaScript, no build step.
// Every piece of text from a quote is placed with textContent, never as HTML.

import { PagePlan, REASONS } from "./pages.js";
import { ENGLISH, MissingText, NOT_IN_TELUGU, buildResults, language, privacyText, t } from "./results.js";

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
  textractOptedOut: "Your pages are read by Amazon Textract in {where} and deleted from our storage after reading. This AWS account has opted out of AWS using them to improve its services. If reading fails, they're removed automatically, usually within two days.",
  textract: "Your pages are read by Amazon Textract in {where} and deleted from our storage after reading. AWS may keep and use them to improve its AI services and may store some of that content in another AWS region. If reading fails, they're removed automatically, usually within two days.",
};
const READING_UNAVAILABLE = "AI reading isn't available yet. Please type the numbers instead.";

const MODE_LABELS = {
  saved: "Saved reading of a made-up quote. Not read live.",
  nova: "Read by Amazon Nova",
  textract: "Read by Amazon Textract",
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
  // Asked beside the charge list on the review screen, not with the other flags.
  { name: "extra_charges_complete", text: "Is every charge on your quote listed here?", beside: "charges",
    options: [["true", "Yes"], ["false", "No"], ["unclear", "Not sure"]] },
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

const FAILURES = {
  timed_out: "Reading took too long and was stopped.",
  read_limit: "This quote was read as many times as allowed, but its reading couldn't be saved.",
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
  plan: null,
  pageText: null,
  option: "",
  result: null,
  pollTimer: null,
  view: "home",
  editView: null,
  checkThis: [],
  verified: new Set(),
  entryChecks: [],
  confirmedOperands: new Set(),
  lastRequest: null,
  pageImages: [],
  boxIndex: new Map(),
  challenge: null,
  seq: 0,          // the latest request whose answer may still change the screen
  lang: "en",      // the results screen's language: "en" or "te" (the household's choice)
  telugu: null,    // the Telugu pack (te.js), loaded when first chosen
  shownLang: ENGLISH,  // the language the results screen was last built in
  upload: null,    // { job, done: Set of upload slots sent, until: ms } while a check's pages go up
};

// Each request that changes the screen takes a new number; an answer that arrives after a newer
// request was made is ignored, so a slow old answer never replaces a newer one.
function nextRequest() {
  state.seq += 1;
  return state.seq;
}

function stillCurrent(seq) {
  return seq === state.seq;
}

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

const API_TIMEOUT_MS = 20000;
const UPLOAD_TIMEOUT_MS = 60000;
const TIMED_OUT = "The checker took too long to answer. Please try again in a moment.";

// fetch that gives up after ms, so a stalled request ends in a clear message instead of a spinner.
async function fetchWithin(url, init, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) throw new ApiError(0, "timeout", TIMED_OUT);
    throw error;
  } finally {
    clearTimeout(timer);
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
    res = await fetchWithin(API_BASE + path, init, API_TIMEOUT_MS);
  } catch (error) {
    if (error instanceof ApiError) throw error;
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

// ---------------------------------------------------------------- views

function show(view) {
  if (state.view === "wait" && view !== "wait") stopPolling();
  state.view = view;
  for (const section of $$("[data-view]")) section.hidden = section.dataset.view !== view;
  for (const label of $$("[data-mode-label]")) label.textContent = MODE_LABELS[state.mode] || "";
  applyLanguage(view === "results" ? state.shownLang : ENGLISH);
  window.scrollTo(0, 0);
  const heading = $(`[data-view="${view}"] h1, [data-view="${view}"] h2`);
  if (heading && state.started) heading.focus({ preventScroll: true });
  state.started = true;  // the first screen on load keeps the browser's own focus
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
    showProblem("New checks are paused", error.message);  // samples and typed checks pause too
  } else if (error.code === "network") {
    showProblem("The checker couldn't be reached", error.message);
  } else if (error.code === "timeout") {
    showProblem("The checker didn't answer in time", error.message);
  } else if (error.code === "numbers_changed" || error.code === "review_changed") {
    showProblem("The numbers changed", error.message);  // another request moved on; confirm again
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
  const seq = nextRequest();
  try {
    const job = await api("POST", `/samples/${encodeURIComponent(sampleId)}`);
    if (!stillCurrent(seq)) return;
    newJob(job, job.mode);
    const view = await api("GET", jobPath());
    if (!stillCurrent(seq)) return;
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
  $("#retry-upload").hidden = true;
  state.upload = null;
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

// Every page of the PDF goes into the plan: kept with its image, or left out with a reason
// (over the page limit, too large to send, or unreadable), keeping the quote's own page numbers.
async function renderPdf(file, plan, onPage) {
  const pdfjs = await loadPdfjs();
  const data = new Uint8Array(await file.arrayBuffer());
  const doc = await pdfjs.getDocument({ data, isEvalSupported: false }).promise;
  try {
    for (let n = 1; n <= doc.numPages; n += 1) {
      if (plan.room() === 0) {
        plan.omit("over_limit");
        continue;
      }
      onPage(n, doc.numPages);
      try {
        const page = await doc.getPage(n);
        const blob = await pdfPageToJpeg(page);
        page.cleanup();
        if (blob) plan.keep(blob);
        else plan.omit("too_large");
      } catch {
        plan.omit("unreadable");
      }
    }
  } finally {
    await doc.destroy();
  }
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
  const plan = new PagePlan(MAX_PAGES);
  for (const file of files) {
    const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
    if (!isPdf && plan.room() === 0) {
      plan.omit("over_limit");
      continue;
    }
    try {
      if (isPdf) {
        await renderPdf(file, plan, (n, of) => uploadStatus(`Preparing page ${n} of ${of} from ${file.name}`));
      } else {
        uploadStatus(`Preparing ${file.name}`);
        const blob = await photoToJpeg(file);
        if (blob) plan.keep(blob);
        else plan.omit("too_large");
      }
    } catch {
      plan.omit("unreadable");  // a file that can't be opened at all counts as one page left out
      notes.push(isPdf
        ? `${file.name} couldn't be opened here. Try photos of each page, or type the numbers instead.`
        : `${file.name} couldn't be opened here. Use a PDF, JPEG or PNG, or type the numbers instead.`);
    }
  }
  for (const [reason, words] of Object.entries(REASONS)) {
    const pages = plan.omitted.filter((o) => o.reason === reason).map((o) => o.page);
    if (pages.length) {
      notes.push(`${pages.length === 1 ? "Page" : "Pages"} ${pages.join(", ")} ${pages.length === 1 ? "was" : "were"} left out `
        + `(${words}). The checks that need the whole quote will ask you to confirm.`);
    }
  }
  state.plan = plan;
  state.pages = plan.pages.map(({ page, blob }) => ({ page, blob, url: URL.createObjectURL(blob) }));
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
    res = await fetchWithin(target.url, { method: "POST", body: form }, UPLOAD_TIMEOUT_MS);
  } catch (error) {
    if (error instanceof ApiError) throw new ApiError(0, "network", "A page took too long to upload. Please try again.");
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
    const job = await api("POST", "/jobs", state.plan.request());
    newJob(job, job.mode || "nova");
    state.upload = { job, done: new Set(), until: Date.now() + (job.expires_in || 0) * 1000 };
    await uploadRest();
  } catch (error) {
    uploadFailed(error);
  } finally {
    button.disabled = false;
  }
}

// Sends the pages (and then the manifest) not yet sent for this check, so a failed upload can
// be tried again for the same check while its upload links last.
async function uploadRest() {
  const { job, done } = state.upload;
  const pages = state.pages;
  for (const target of job.uploads) {  // upload slot n holds the nth page kept
    if (done.has(target.page)) continue;
    uploadStatus(`Uploading page ${pages[target.page - 1].page} (${target.page} of ${pages.length})`);
    await postForm(target, pages[target.page - 1].blob);
    done.add(target.page);
  }
  const manifest = new Blob([JSON.stringify(job.manifest_body)], { type: "application/json" });
  await postForm(job.manifest, manifest);
  state.upload = null;
  $("#retry-upload").hidden = true;
  startWaiting();
}

function uploadFailed(error) {
  if (error.code === "upload_failed" || error.code === "network") {
    const canRetry = state.upload && Date.now() < state.upload.until - 5000;
    uploadStatus(canRetry ? `${error.message} Pages already sent are kept.`
      : `${error.message} The upload links have expired, so please send the pages again.`, true);
    $("#retry-upload").hidden = !canRetry;
  } else {
    showApiError(error);
  }
}

async function retryUpload() {
  const button = $("#retry-upload");
  button.disabled = true;
  try {
    await uploadRest();
  } catch (error) {
    uploadFailed(error);
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

// The page image for a page: the household's own (never uploaded for viewing), or a short-lived
// link the API gives for a made-up sample's page.
function pageImage(pageNumber) {
  const own = state.pages.find((p) => p.page === pageNumber);
  return own ? own.url : (state.pageImages || [])[pageNumber - 1] || null;
}

// Boxes beside each piece of evidence in the reading: (page, quoted text) -> [{page, box}].
function indexBoxes(node, index = new Map()) {
  if (Array.isArray(node)) node.forEach((v) => indexBoxes(v, index));
  else if (node && typeof node === "object") {
    if (node.evidence_text && node.page && Array.isArray(node.boxes)) {
      index.set(`${node.page}|${node.evidence_text}`, node.boxes);
    }
    Object.values(node).forEach((v) => indexBoxes(v, index));
  }
  return index;
}

function boxesFor(pageNumber, evidenceText) {
  return state.boxIndex.get(`${pageNumber}|${evidenceText}`) || [];
}

function pageButton(pageNumber, evidenceText, boxes) {
  if (!pageNumber) return null;
  const found = boxes || boxesFor(pageNumber, evidenceText);
  const image = pageImage(pageNumber);
  if (image) {
    return el("button", { type: "button", class: "thumb", "aria-label": t(viewLang(), "page.show", { n: pageNumber }),
      onclick: () => openPage(pageNumber, evidenceText, found) },
    el("img", { src: image, alt: "" }));
  }
  if (state.pageText && state.pageText[String(pageNumber)]) {
    return el("button", { type: "button", class: "button secondary text-thumb",
      onclick: () => openPage(pageNumber, evidenceText, found) }, t(viewLang(), "page.text", { n: pageNumber }));
  }
  return null;
}

// The quoted line itself is a button: tapping it opens the page at the line's box.
function quoteButton(pageNumber, evidenceText, boxes) {
  const found = boxes || boxesFor(pageNumber, evidenceText);
  if (!pageNumber || (!pageImage(pageNumber) && !(state.pageText && state.pageText[String(pageNumber)]))) {
    return el("q", { text: evidenceText });
  }
  return el("button", { type: "button", class: "quote-link",
    "aria-label": t(viewLang(), "page.where", { n: pageNumber }),
    onclick: () => openPage(pageNumber, evidenceText, found) }, el("q", { text: evidenceText }));
}

function openPage(pageNumber, evidenceText, boxes = []) {
  const dialog = $("#page-viewer");
  $("#page-viewer-title").textContent = t(viewLang(), "viewer.title", { n: pageNumber });
  $("#page-viewer-close").textContent = t(viewLang(), "viewer.close");
  setLang($("#page-viewer"), viewLang());
  const body = $("#page-viewer-body");
  const image = pageImage(pageNumber);
  if (image) {
    const marks = boxes.filter((b) => b.page === pageNumber && Array.isArray(b.box)).map(({ box: [left, top, width, height] }) =>
      el("span", { class: "hl", style: `left:${left * 100}%;top:${top * 100}%;width:${width * 100}%;height:${height * 100}%` }));
    body.replaceChildren(el("div", { class: "page-frame" },
      el("img", { src: image, alt: `Page ${pageNumber} of the quote` }), marks));
    if (marks.length) requestAnimationFrame(() => marks[0].scrollIntoView({ block: "center", behavior: "smooth" }));
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
    const candidates = (field.candidates || []).filter((c) => c.evidence_text).map((c) =>
      el("li", {}, c.page ? `Page ${c.page}: ` : "", quoteButton(c.page, c.evidence_text, c.boxes), " ",
        pageButton(c.page, c.evidence_text, c.boxes)));
    return el("div", { class: "evidence" },
      el("span", { class: "warn", text: "Different pages give different values. Enter the right one." }),
      candidates.length ? el("ul", { class: "candidates" }, candidates) : null);
  }
  const parts = [];
  if (field.evidence_text) {
    parts.push(el("span", {}, field.page ? `Page ${field.page}: ` : "", quoteButton(field.page, field.evidence_text, field.boxes)));
  }
  const status = field.value && field.value.parse_status;
  if (status && status !== "ok" && status !== "empty") {
    parts.push(el("span", { class: "warn", text: " This couldn't be read as a number. Please check it." }));
  }
  return el("div", { class: "evidence" }, pageButton(field.page, field.evidence_text, field.boxes), el("div", {}, parts));
}

// Once the household changes a value, what the quote said stays in view beside it.
function readFromQuote(input, field) {
  if (!field || field.conflict || !field.evidence_text || input.dataset.original === "") return null;
  const note = el("p", { class: "hint read-from", hidden: true },
    `Read from the quote: ${input.dataset.original}, `, field.page ? `page ${field.page}: ` : "",
    quoteButton(field.page, field.evidence_text, field.boxes));
  input.addEventListener("input", () => { note.hidden = input.value.trim() === input.dataset.original.trim(); });
  return note;
}

// "Check this": a value the reading wasn't sure of, or of a kind it has got wrong before.
// Every check that uses it waits until the household ticks it (or types the right
// value). Each value has its own tick: there is no way to accept them all at once.
const CHECK_REASONS = {
  conflict: "different parts of the quote give different values",
  unresolved: "it couldn't be read cleanly",
  low_confidence: "the reading wasn't sure of it",
  source_rule: "readings like this have been wrong before",
};

function checkEntry(path) {
  // A price, subsidy or size row shows the chosen option's own value when it has one.
  const own = (state.extraction.option_fields || {})[state.option] || {};
  const shown = path.includes("[") || path.startsWith("flags.") ? undefined : (path in own ? state.option : null);
  return state.checkThis.find((c) => c.path === path && (shown === undefined || (c.option_id || null) === shown));
}

function checkLine(path) {
  const entry = checkEntry(path);
  if (!entry) return null;
  const why = entry.reasons.map((r) => CHECK_REASONS[r]).filter(Boolean).join(", and ");
  const parts = [el("span", { class: "warn", text: `Check this against the quote: ${why}. The checks that use it wait until you do.` })];
  if (!entry.reasons.includes("conflict")) {
    const id = `verify-${path.replace(/[^a-z0-9]+/gi, "-")}`;
    const box = el("input", { type: "checkbox", id, "data-verify": entry.token, "data-verify-path": path });
    box.checked = state.verified.has(entry.token);
    parts.push(el("label", { class: "verify", for: id }, box, " I checked this value against the quote and it is right"));
  }
  return el("div", { class: "check-this" }, parts);
}

// A number the household typed that looks unusual for a home system. It is never changed:
// the household confirms it with its own tick, or types it again.
function entryLine(path, name) {
  const entry = state.entryChecks.find((e) => (name ? e.field === name : e.path === path));
  if (!entry) return null;
  const id = `confirm-${(name || path).replace(/[^a-z0-9]+/gi, "-")}`;
  const box = el("input", { type: "checkbox", id, "data-confirm-entry": entry.token });
  return el("div", { class: "check-this", "data-entry-note": "" },
    el("span", { class: "warn", text: entry.message }),
    el("label", { class: "verify", for: id }, box, " This number is right as I typed it"));
}

function textRow(path, label, field, extra = {}) {
  const id = `f-${path.replace(/[^a-z0-9]+/gi, "-")}`;
  const original = inputValue(field);
  const input = el("input", { id, value: original, "data-path": path, "data-original": original,
    "data-kind": extra.kind || "text", inputmode: extra.inputmode, autocomplete: "off",
    placeholder: "Not on the quote" });
  return el("div", { class: "row" },
    el("label", { for: id, text: label }), input, readFromQuote(input, field),
    evidenceLine(field), checkLine(path), entryLine(path));
}

function selectRow(path, label, field, options) {
  const id = `f-${path.replace(/[^a-z0-9]+/gi, "-")}`;
  const v = field && !field.conflict ? field.value : null;
  const original = v === null || v === undefined ? "" : String(v);
  const select = el("select", { id, "data-path": path, "data-original": original, "data-kind": "choice" },
    options.map(([value, text]) => el("option", { value, text })));
  select.value = original;
  return el("div", { class: "row" }, el("label", { for: id, text: label }), select, evidenceLine(field),
    checkLine(path));
}

const DCR_CHOICES = [["", "The quote doesn't say"], ["true", "Yes, DCR panels and cells"], ["false", "It says they are not DCR"]];
const INCLUDED_CHOICES = [["", "Not stated"], ["yes", "Inside the total"], ["no", "Outside the total"], ["unclear", "Not clear"]];
const ADDED_INCLUDED_CHOICES = [["unclear", "Not sure"], ["yes", "Yes"], ["no", "No"]];

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
    el("div", { id: "review-added-charges" }),
    el("button", { type: "button", id: "review-add-charge", class: "button link", text: "Add a charge",
      onclick: () => addReviewCharge() }),
    chargesCompleteRow(),
    textRow("discount", "Discount", pick(q, "discount"), { inputmode: "decimal" }),
    textRow("gross_total", "Total", pick(q, "gross_total"), { inputmode: "decimal" }),
  ];
  groups.push(["Price", priceRows]);
  const subsidyRows = AMOUNTS.filter(([name]) => name.startsWith("subsidy_") || name === "net_cost")
    .filter(([name]) => ["subsidy_central", "subsidy_state", "net_cost"].includes(name)
      || inputValue(pick(q, name)) !== "")
    .map(([name, label]) => textRow(name, label, pick(q, name), { inputmode: "decimal" }));
  groups.push(["Subsidy and net cost", subsidyRows]);
  // Information only: the GSTIN's state code says where the vendor is registered for GST. It
  // never fills in the household's own state.
  const gst = q.supplier_gst_state && !q.supplier_gst_state.conflict ? q.supplier_gst_state : null;
  groups.push(["Other details", [
    selectRow("dcr_declaration", "DCR declaration (panels and cells made in India)", q.dcr_declaration, DCR_CHOICES),
    textRow("vendor_registration", "Vendor registration number", q.vendor_registration),
    gst ? el("p", { class: "hint", text: `The vendor is registered for GST in ${gst.value} (from the GSTIN on page ${gst.page}). `
      + "That's the vendor's registration, not where your house is." }) : null,
  ].filter(Boolean)]);

  $("#review-fields").replaceChildren(...groups.filter(([, rows]) => rows.length).map(([title, rows]) =>
    el("fieldset", { class: "group" }, el("legend", { text: title }), rows)));
}

// A charge the reading missed, typed in by the household. The backend adds it as
// extra_charges[U<n>] and marks its values as entered by the household.
function addReviewCharge() {
  const box = $("#review-added-charges");
  if (box.children.length >= MAX_CHARGES) return;
  const n = box.children.length + 1;
  box.append(el("div", { class: "charge", "data-added-charge": "" },
    el("div", { class: "charge-row" },
      el("label", { class: "field" }, `Added charge ${n} (for example net meter)`,
        el("input", { "data-added": "label", autocomplete: "off" })),
      el("label", { class: "field" }, "Amount",
        el("input", { "data-added": "amount", inputmode: "decimal", placeholder: "₹", autocomplete: "off" })),
      el("label", { class: "field" }, "Is it inside the total?",
        el("select", { "data-added": "included_in_total" },
          ADDED_INCLUDED_CHOICES.map(([value, text]) => el("option", { value, text })))))));
  $("#review-add-charge").hidden = box.children.length >= MAX_CHARGES;
}

// The household's own answer, never pre-filled from the quote: anything but Yes keeps
// the total check waiting.
function chargesCompleteRow() {
  const flag = FLAGS.find((f) => f.name === "extra_charges_complete");
  const id = "flag-extra_charges_complete";
  const select = el("select", { id, "data-household": flag.name },
    [["unclear", "Not sure"], ["true", "Yes"], ["false", "No"]].map(([value, text]) => el("option", { value, text })));
  return el("div", { class: "row" }, el("label", { for: id, text: flag.text }), select);
}

function renderReviewFlags() {
  const proposed = ((state.extraction.flags || {}).model_proposed) || {};
  $("#review-flags").replaceChildren(...FLAGS.filter((flag) => !flag.beside).map((flag) => {
    const field = proposed[flag.name];
    const v = field && !field.conflict ? field.value : null;
    const original = v === null || v === undefined ? "" : String(v);
    const id = `flag-${flag.name}`;
    const select = el("select", { id, "data-flag": flag.name, "data-original": original },
      el("option", { value: "", text: "Not sure" }),
      flag.options.map(([value, text]) => el("option", { value, text })));
    select.value = original;
    return el("div", { class: "row" }, el("label", { for: id, text: flag.text }), select,
      field && field.evidence_text ? evidenceLine(field) : null, checkLine(`flags.${flag.name}`));
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
  state.checkThis = view.check_this || [];
  state.verified = new Set();
  state.confirmedOperands = new Set();
  state.pageImages = view.page_images || [];
  state.boxIndex = indexBoxes(view.extraction);
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
  let added = 0;
  for (const row of $$("#review-added-charges [data-added-charge]")) {
    const label = $("[data-added=label]", row).value.trim();
    const amount = $("[data-added=amount]", row).value.trim();
    if (!label && !amount) continue;
    const path = `extra_charges[U${++added}]`;
    if (label) corrections[`${path}.label`] = label;
    if (amount) corrections[`${path}.amount`] = amount;
    corrections[`${path}.included_in_total`] = $("[data-added=included_in_total]", row).value;
  }
  const complete = $("[data-household=extra_charges_complete]");
  if (complete) answers.extra_charges_complete = answerValue(complete.value);
  if (state.option) answers.selected_option = state.option;
  // A ticked value counts only while it is unchanged: a changed value is a correction.
  const verified = $$("#view-review [data-verify]").filter((box) => box.checked)
    .filter(({ dataset: { verifyPath: path } }) => !(path in corrections)
      && !(path.startsWith("flags.") && path.slice(6) in answers))
    .map((box) => box.dataset.verify);
  for (const box of $$("#view-review [data-confirm-entry]")) if (box.checked) verified.push(box.dataset.confirmEntry);
  state.verified = new Set(verified);
  return { corrections, answers, verified };
}

async function submitReview(event) {
  event.preventDefault();
  const status = $("#review-status");
  const button = $("#review-form button[type=submit]");
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Running the checks";
  try {
    // A fresh submission of the review starts over: any change of value, answer or option asks
    // again, and the server refuses tokens from an earlier revision anyway.
    state.confirmedOperands = new Set();
    const body = { ...collectReview(), confirmed_operands: [] };
    state.lastRequest = { path: jobPath("/checks"), body };
    const seq = nextRequest();
    clearInvalid($("#review-form"));
    const result = await api("POST", state.lastRequest.path, body);
    if (!stillCurrent(seq)) return;
    status.textContent = "";
    openResults(result);
  } catch (error) {
    if (error.status === 400) {
      status.textContent = `${error.message} Please check the values you changed.`;
      status.classList.add("error");
      markInvalid($("#review-form"), error.message, status);
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
        el("input", { "data-charge": "amount", inputmode: "decimal", placeholder: "₹", value: values.amount || "",
          autocomplete: "off" })),
      el("label", { class: "field" }, "Inside the total?",
        el("select", { "data-charge": "included_in_total" },
          INCLUDED_CHOICES.map(([value, text]) => el("option", { value, text }))))));
  box.append(row);
  $("#add-charge").hidden = box.children.length >= MAX_CHARGES;
}

function showManualEntryChecks() {
  const form = $("#manual-form");
  for (const note of $$("[data-entry-note]", form)) note.remove();
  for (const entry of state.entryChecks) {
    const charge = /^extra_charges\[E(\d+)\]\.amount$/.exec(entry.field);
    const input = charge ? $$("[data-charge=amount]", form)[Number(charge[1]) - 1] : form.elements[entry.field];
    const note = entryLine(null, entry.field);
    if (!input || !note) continue;
    input.closest("label").after(note);
    input.addEventListener("input", () => note.remove(), { once: true });
  }
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
  const verified = $$("[data-confirm-entry]", form).filter((box) => box.checked).map((box) => box.dataset.confirmEntry);
  return { fields, answers, verified };
}

async function submitManual(event) {
  event.preventDefault();
  const status = $("#manual-status");
  const button = $("#manual-form button[type=submit]");
  button.disabled = true;
  status.classList.remove("error");
  status.textContent = "Running the checks";
  try {
    // A fresh submission of the form starts over: tokens from before an edit never carry over.
    state.confirmedOperands = new Set();
    const body = { ...collectManual(), challenge: state.challenge, confirmed_operands: [] };
    state.lastRequest = { path: "/checks", body };
    const seq = nextRequest();
    clearInvalid($("#manual-form"));
    const result = await api("POST", "/checks", body);
    if (!stillCurrent(seq)) return;
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
      markInvalid($("#manual-form"), error.message, status);
    } else {
      status.textContent = "";
      showApiError(error);
    }
  } finally {
    button.disabled = false;
  }
}

// An error about one field is announced and linked to that field, so a screen reader reads it there.
function markInvalid(form, message, status) {
  const named = $$("input[name], input[data-path], select[name]", form).find((input) => {
    const name = input.name || input.dataset.path;
    return name && (message.startsWith(`${name} `) || message.includes(` ${name} `) || message.includes(`${name}:`));
  });
  if (!named) return;
  named.setAttribute("aria-invalid", "true");
  named.setAttribute("aria-describedby", status.id);
  named.focus();
}

function clearInvalid(form) {
  for (const input of $$("[aria-invalid]", form)) {
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
  }
}

// ---------------------------------------------------------------- results

// The reading's own field behind an operand ("module_groups[0].wattage_w" -> the first panel
// line's wattage for the option being checked), to show what the quote said beside a typed number.
function readingField(field) {
  const q = state.extraction;
  if (!q) return null;
  const item = /^(module_groups|inverters)\[(\d+)\]\.(\w+)$/.exec(field);
  if (item) {
    const key = { wattage_w: "wattage", rating_kw: "rating", rating_kva: "rating" }[item[3]] || item[3];
    const found = forOption(q[item[1]])[Number(item[2])];
    return found ? found[key] : null;
  }
  return pick(q, field === "stated_capacity_kw" ? "stated_capacity" : field);
}

async function confirmOperands(token, button) {
  if (!state.lastRequest) return;
  button.disabled = true;
  state.confirmedOperands.add(token);
  const body = { ...state.lastRequest.body, confirmed_operands: [...state.confirmedOperands] };
  if (state.lastRequest.path === "/checks") body.challenge = state.challenge;
  state.lastRequest = { ...state.lastRequest, body };
  const seq = nextRequest();
  const check = button.closest("article") && button.closest("article").dataset.check;
  try {
    const result = await api("POST", state.lastRequest.path, body);
    if (!stillCurrent(seq)) return;
    openResults(result, { keepPlace: true, focusCheck: check });
  } catch (error) {
    state.confirmedOperands.delete(token);
    button.disabled = false;
    showApiError(error);
  }
}

function openResults(result, { keepPlace = false, focusCheck = null } = {}) {
  state.result = result;
  if (result.challenge) state.challenge = result.challenge;  // typed-in numbers: tokens are signed for it
  state.entryChecks = result.entry_checks || [];
  if (state.mode === "manual") showManualEntryChecks();
  renderResults();
  if (keepPlace && state.view === "results") {
    // After "Yes" the screen stays where it was; focus moves to the same check's updated card.
    const card = focusCheck && $(`article[data-check="${CSS.escape(focusCheck)}"] h4`);
    if (card) {
      card.setAttribute("tabindex", "-1");
      card.focus({ preventScroll: false });
    }
    return;
  }
  go("results");
}

// ---------------------------------------------------------------- language

const LANG_KEY = "surya-lekka-lang";

function viewLang() {
  return state.view === "results" ? state.shownLang : ENGLISH;
}

function resultsContext(lang) {
  return {
    el, lang, mode: state.mode, modeKey: MODE_LABELS[state.mode] ? state.mode : "", option: state.option,
    entryChecks: state.entryChecks.length, readingField,
    quoteButton, pageButton, onConfirm: confirmOperands, onFix: () => go(state.editView || "home"),
    copyText: (message) => navigator.clipboard.writeText(message),
  };
}

// Builds the whole results screen in the chosen language, or in English with a notice when any
// piece of this result has no Telugu words: never a mix.
function renderResults() {
  if (!state.result) return;
  const wanted = state.lang === "te" && state.telugu ? state.telugu : ENGLISH;
  let lang = wanted;
  let built;
  state.shownLang = lang;  // the page buttons built below speak this language
  try {
    built = buildResults(state.result, resultsContext(lang));
  } catch (error) {
    if (!(error instanceof MissingText) || lang === ENGLISH) throw error;
    lang = ENGLISH;
    state.shownLang = lang;
    built = buildResults(state.result, resultsContext(lang));
  }
  $("#results-summary").textContent = built.summary;
  $("#results-groups").replaceChildren(...built.groups);
  $("#vendor-questions").replaceChildren(...built.vendor);
  $("#results-mode").textContent = built.mode;
  const notice = $("#lang-notice");
  notice.textContent = wanted !== lang ? NOT_IN_TELUGU : "";
  notice.hidden = wanted === lang;
  if (state.view === "results") applyLanguage(lang);
}

function setLang(node, lang) {
  if (lang.code === "en") node.removeAttribute("lang");
  else node.setAttribute("lang", lang.code);
}

// The fixed words on the results screen and in the footer, in lang.
function applyLanguage(lang) {
  for (const node of $$("[data-i18n]")) node.textContent = t(lang, node.dataset.i18n);
  setLang($("#view-results"), lang);
  setLang($("footer.foot"), lang);
  $("#privacy-line").textContent = lang.code === "en" ? privacyLine(...privacySettings())
    : privacyText(lang, ...privacySettings());
  for (const button of $$("[data-lang]")) button.setAttribute("aria-pressed", String(button.dataset.lang === state.lang));
}

async function loadTelugu() {
  if (!state.telugu) state.telugu = language((await import("./te.js")).default);
  return state.telugu;
}

async function chooseLanguage(code) {
  state.lang = code;
  try {
    localStorage.setItem(LANG_KEY, code);
  } catch {
    // the choice just isn't remembered
  }
  if (code === "te") {
    try {
      await loadTelugu();
    } catch {
      state.lang = "en";
      const notice = $("#lang-notice");
      notice.textContent = "తెలుగు లోడ్ కాలేదు. Telugu couldn't be loaded. Please try again.";
      notice.hidden = false;
    }
  }
  renderResults();
  if (state.view === "results") applyLanguage(state.shownLang);
}

function rememberedLanguage() {
  try {
    return localStorage.getItem(LANG_KEY) === "te" ? "te" : "en";
  } catch {
    return "en";
  }
}

// ---------------------------------------------------------------- start

// With Textract, the opt-out sentence shows only when deploy.ps1 confirmed the
// account's opt-out policy covers Textract.
function privacySettings() {
  return [String(CONFIG.REGION || ""), CONFIG.CROSS_REGION === true, String(CONFIG.ENGINE || ""), CONFIG.AI_OPT_OUT === true];
}

function privacyLine(region, crossRegion, engine, optOut) {
  if (!region) return PRIVACY.local;
  const where = REGION_NAMES[region] || `AWS's ${region} region`;
  if (engine === "textract") return (optOut ? PRIVACY.textractOptedOut : PRIVACY.textract).replace("{where}", where);
  return (crossRegion ? PRIVACY.crossRegion : PRIVACY.inRegion).replace("{where}", where);
}

function init() {
  const privacy = privacyLine(...privacySettings());
  $("#privacy-line").textContent = privacy;
  $("#privacy-upload").textContent = privacy;  // the same notice, beside the upload button
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
  $("#retry-upload").addEventListener("click", retryUpload);
  $("#problem-retry").addEventListener("click", retryJob);
  $("#review-form").addEventListener("submit", submitReview);
  $("#manual-form").addEventListener("submit", submitManual);
  $("#add-charge").addEventListener("click", () => addCharge());
  $("#results-edit").addEventListener("click", () => go(state.editView || "home"));
  $("#page-viewer-close").addEventListener("click", closePage);
  for (const button of $$("[data-lang]")) button.addEventListener("click", () => chooseLanguage(button.dataset.lang));
  state.lang = rememberedLanguage();
  if (state.lang === "te") loadTelugu().then(renderResults, () => { state.lang = "en"; });
  renderQuestions($("[data-questions=review]"), "review");
  renderQuestions($("[data-questions=manual]"), "manual");
  addCharge();
  const start = location.hash.slice(1);
  // A direct link such as #sample=S4 opens a sample that the home page doesn't list.
  const linked = /^sample=(S\d{1,2})$/.exec(start);
  go(["upload", "manual"].includes(start) ? start : "home", false);
  history.replaceState({ view: state.view }, "", `#${state.view}`);
  if (linked) startSample(linked[1]);
}

init();
