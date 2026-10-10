// The results screen and the vendor message, in English or Telugu.
//
// English shows the text the checks wrote. Telugu rebuilds every message, note and working from
// the keyed parts the server sends (src/checks/message_parts.py) with the Telugu vocabulary in
// te.js, and every label on the screen from that file's "ui" table. Text quoted from the quote,
// numbers, amounts and units are shown as printed. Anything without a Telugu key throws
// MissingText, and the caller shows the whole screen in English instead: never half of each.

export const UI_EN = {
  "results.heading": "What the checks found",
  "results.hint": "A finding only appears after you confirm the numbers it uses.",
  "results.edit": "Change answers or values",
  "results.another": "Check another quote",
  "vendor.heading": "Questions for your vendor",
  "lang.label": "Language",
  "mode.saved": "Saved reading of a made-up quote. Not read live.",
  "mode.nova": "Read by Amazon Nova",
  "mode.textract": "Read by Amazon Textract",
  "mode.manual": "Entered by you",
  "mode.stub": "Read by the local test stub, not a model",
  "status.inconsistent.badge": "Doesn't match",
  "status.inconsistent.group": "These don't match",
  "status.needs_confirmation.badge": "Needs checking",
  "status.needs_confirmation.group": "These need checking or an answer from you",
  "status.missing.badge": "Missing",
  "status.missing.group": "These are missing from the quote",
  "status.out_of_scope.badge": "Not checked",
  "status.out_of_scope.group": "These are outside what this tool checks",
  "status.consistent.badge": "Matches",
  "status.consistent.group": "These match",
  "check.C1_capacity": "System size",
  "check.C2_central_subsidy": "Central subsidy",
  "check.C3_gross_total": "Total price",
  "check.C3_net_cost": "Net cost after subsidy",
  "check.C4_missing_details": "Details on the quote",
  "item.panel_wattage": "Panel wattage",
  "item.panel_count": "Number of panels",
  "item.module_make_model": "Panel make and model",
  "item.dcr_declaration": "DCR declaration",
  "item.inverter_make_model": "Inverter make and model",
  "item.inverter_rating": "Inverter rating",
  "item.vendor_registration": "Vendor registration number",
  "item.gst_basis": "GST",
  "item.extras_outside_total": "Charges outside the total",
  "item.net_meter": "Net-meter charges",
  "summary.results": "Results: {parts}.",
  "summary.join": ", ",
  "summary.inconsistent.one": "{n} doesn't match",
  "summary.inconsistent.many": "{n} don't match",
  "summary.needs_confirmation.one": "{n} needs checking",
  "summary.needs_confirmation.many": "{n} need checking",
  "summary.missing.one": "{n} missing",
  "summary.missing.many": "{n} missing",
  "summary.out_of_scope.one": "{n} not checked",
  "summary.out_of_scope.many": "{n} not checked",
  "summary.consistent.one": "{n} matches",
  "summary.consistent.many": "{n} match",
  "summary.to_check.one": " One value still needs checking against the quote.",
  "summary.to_check.many": " {n} values still need checking against the quote.",
  "summary.unusual": " Some numbers you entered look unusual. Please check them.",
  "label.module_groups.count": "Number of panels",
  "label.module_groups.wattage_w": "Panel wattage",
  "label.module_groups.wattage": "Panel wattage",
  "label.module_groups.make_model": "Panel make and model",
  "label.module_groups.make_model_alternatives": "Other panel makes and models",
  "label.inverters.make_model_alternatives": "Other inverter makes and models",
  "label.inverters.make_model": "Inverter make and model",
  "label.inverters.rating_kw": "Inverter rating",
  "label.inverters.rating_kva": "Inverter rating",
  "label.inverters.rating": "Inverter rating",
  "label.stated_capacity_kw": "System size",
  "label.stated_capacity": "System size",
  "label.capacity_basis": "What the size measures",
  "label.dcr_declaration": "DCR declaration",
  "label.vendor_registration": "Vendor registration number",
  "label.gst_treatment": "GST basis",
  "label.extra_charges_complete": "No other charges",
  "label.extra_charges.amount": "Extra charge",
  "label.extra_charges.label": "Extra charge",
  "label.extra_charges.included_in_total": "Extra charge inside the total",
  "label.net_cost_subsidy_basis": "Subsidy the net cost takes off",
  "label.multiple_options": "More than one option",
  "label.consumer_type": "Who the system is for",
  "label.give_it_up": "Give It Up",
  "label.state": "State",
  "label.selected_option": "Option",
  "label.portal_application_on_or_after_cutoff": "Applied on the National Portal on or after 13 Feb 2024",
  "label.first_system": "First rooftop solar system at this house",
  "label.prior_central_subsidy": "Central subsidy received before",
  "label.base_price": "Base price",
  "label.gst_amount": "GST amount",
  "label.discount": "Discount",
  "label.gross_total": "Total",
  "label.subsidy_central": "Central subsidy",
  "label.subsidy_state": "State subsidy",
  "label.subsidy_combined": "Subsidy shown as one figure",
  "label.subsidy_unspecified": "Subsidy (type not stated)",
  "label.net_cost": "Net cost after subsidy",
  "computed.dc_kwp": "Panel capacity worked out",
  "computed.dc_kwp_range": "Panel capacity worked out",
  "computed.central_cfa_rule": "Central subsidy under the rule",
  "computed.cfa_range": "Central subsidy under the rule",
  "computed.gross total": "Total worked out",
  "computed.net cost": "Net cost worked out",
  "rule.CFA-RES-GENERAL": "central subsidy (CFA) rates for an individual household in a general-category state or UT, from the MNRE PM Surya Ghar guidelines",
  "rule.CFA-RES-SPECIAL": "central subsidy (CFA) rates for an individual household in a special-category state or UT, from the MNRE PM Surya Ghar guidelines",
  "rule.line": "Rule: {rule}. It applies to applications on the National Portal from {date}.",
  "rule.other": "Rule: {id}{dated}.",
  "rule.dated": ", dated {date}",
  "ev.label": "{label}: ",
  "ev.plain": "{label}: {value}",
  "ev.computed": "{label}: {value} ",
  "ev.answer": "{label}: {value} (your answer)",
  "ev.entered": "{label}: {value} (entered by you{was})",
  "ev.entered_read": "{label}: {value} (entered by you{was}). Read from the quote: ",
  "ev.was": "; the reading was {value}",
  "ev.page": "page {n}, ",
  "op.plain": "{label}: {value}",
  "op.from": "{label}: {value}, from ",
  "op.typed": "{label}: {value} (you typed this)",
  "op.typed_read": "{label}: {value} (you typed this). Read from the quote: {read}, ",
  "op.yes": "Yes",
  "op.fix": "Fix a number",
  "page.show": "Show page {n}",
  "page.text": "Page {n}",
  "page.where": "Show where page {n} says this",
  "viewer.title": "Page {n}",
  "viewer.close": "Close",
  "value.bool_true": "Yes",
  "value.bool_false": "No",
  "value.not_found": "not found",
  "value.unreadable": "unclear",
  // the household's answers, as the review screen words them (app.js VALUE_WORDS)
  "value.false": "No",
  "value.true": "Yes",
  "value.dc_kwp": "The solar panels (DC, kWp)",
  "value.ac_kw": "The inverter or AC output (kW)",
  "value.unspecified": "The quote doesn't say",
  "value.included": "Yes, the base price includes GST",
  "value.excluded": "No, GST is added on top",
  "value.unclear": "Not clear",
  "value.central": "The central subsidy",
  "value.central_and_state": "The central and state subsidies",
  "value.state": "The state subsidy",
  "value.combined": "The single subsidy figure",
  "value.none": "No subsidy is taken off",
  "value.individual_household": "An individual household",
  "value.rwa": "A residents' welfare association (RWA)",
  "value.group_housing": "A group housing society",
  "value.other_non_household": "Something else",
  "value.yes": "Inside the total",
  "value.no": "Outside the total",
  "vendor.none": "These checks didn't raise any questions for the vendor.",
  "vendor.copy": "Copy",
  "vendor.copied": "Copied.",
  "vendor.select": "Select the message and copy it.",
  "vendor.whatsapp": "Send on WhatsApp",
  "vendor.hint": "The message below asks these questions politely. Send it to your vendor.",
  "vendor.textarea": "Message for your vendor",
  "foot.advice": "This is not financial or legal advice. Eligibility (DCR panels, registration, inspection) is not verified.",
  "privacy.local": "This copy runs on your own computer, so your pages stay on it.",
  "privacy.inRegion": "Your pages are processed in {where} and deleted after reading. If reading fails, they're removed automatically, usually within two days.",
  "privacy.crossRegion": "Your pages are stored in {where} and may be read in other AWS regions through cross-Region inference. They're deleted after reading. If reading fails, they're removed automatically, usually within two days.",
  "privacy.textractOptedOut": "Your pages are read by Amazon Textract in {where} and deleted from our storage after reading. This AWS account has opted out of AWS using them to improve its services. If reading fails, they're removed automatically, usually within two days.",
  "privacy.textract": "Your pages are read by Amazon Textract in {where} and deleted from our storage after reading. AWS may keep and use them to improve its AI services and may store some of that content in another AWS region. If reading fails, they're removed automatically, usually within two days.",
  "region.ap-south-1": "AWS's Mumbai region (India)",
  "region.ap-southeast-2": "AWS's Sydney region (Australia)",
  "region.other": "AWS's {region} region",
};

// Shown in both languages when a result has text with no Telugu key.
export const NOT_IN_TELUGU = "ఈ ఫలితాన్ని తెలుగులో పూర్తిగా చూపించలేకపోయాం, అందుకే ఇంగ్లీష్‌లో చూపిస్తున్నాం. "
  + "This result can't be shown fully in Telugu, so it is shown in English.";

export class MissingText extends Error {}

export const ENGLISH = { code: "en", ui: UI_EN, vocab: null };

export function language(pack) {
  return { code: pack.language, ui: pack.ui, vocab: pack.strings };
}

function fill(template, params, what) {
  return template.replace(/\{(\w+)\}/g, (_, name) => {
    if (!(name in params)) throw new MissingText(`${what}: {${name}}`);
    return String(params[name]);
  });
}

export function t(lang, key, params = {}) {
  const template = lang.ui[key];
  if (template === undefined) throw new MissingText(key);
  return fill(template, params, key);
}

function word(lang, key) {
  const text = lang.vocab[key];
  if (text === undefined) throw new MissingText(key);
  return text;
}

// The same rebuild as message_parts.render_node.
export function renderNode(node, lang) {
  if (node === null || node === undefined) throw new MissingText("no parts");
  if ("text" in node) return node.text;
  if ("join" in node) {
    const parts = node.join.map((n) => renderNode(n, lang));
    if (parts.length === 1) return parts[0];
    return parts.slice(0, -1).join(word(lang, node.sep)) + word(lang, node.last) + parts[parts.length - 1];
  }
  const params = Object.fromEntries(Object.entries(node.params || {}).map(([k, v]) => [k, renderNode(v, lang)]));
  const out = fill(word(lang, node.key), params, node.key);
  return node.capitalize ? out.charAt(0).toUpperCase() + out.slice(1) : out;
}

export function renderKeyed(key, parts, lang) {
  const params = Object.fromEntries(Object.entries(parts || {}).map(([k, v]) => [k, renderNode(v, lang)]));
  return fill(word(lang, key), params, key);
}

export function messageText(f, lang) {
  if (lang.code === "en") return f.message;
  if (!f.message_key || f.message_key === "unkeyed") throw new MissingText("unkeyed message");
  return renderKeyed(f.message_key, f.message_parts, lang);
}

export function notesText(f, lang) {
  if (lang.code === "en") return f.notes || [];
  const notes = f.notes || [];
  const parts = f.notes_parts || [];
  if (parts.length !== notes.length) throw new MissingText("notes");
  return parts.map((p) => renderNode(p, lang));
}

export function formulaText(e, lang) {
  if (lang.code === "en") return e.formula;
  return renderNode(e.formula_parts, lang).replace(/(\d) x (\d)/g, "$1 × $2");
}

export function questionText(q, lang) {
  return lang.code === "en" ? q.text : renderKeyed(q.key, q.parts, lang);
}

export function vendorMessage(result, lang) {
  if (lang.code === "en") return result.vendor_message;
  // Every line the English message has must come keyed, or the whole result stays English:
  // never a Telugu screen that drops the vendor questions.
  const lines = result.vendor_message_lines || [];
  if (!Array.isArray(result.vendor_message_lines)
      || (result.vendor_message && lines.length !== result.vendor_message.split("\n").length)) {
    throw new MissingText("vendor lines");
  }
  return lines.map((line) => renderKeyed(line.key,
    line.parts || Object.fromEntries(Object.entries(line.params || {}).map(([k, v]) => [k, { text: String(v) }])),
    lang)).join("\n");
}

export function formatInr(text) {
  const n = Number(text);
  if (text === null || text === "" || !Number.isFinite(n)) return String(text);
  return "₹" + n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

export function formatNumber(text) {
  const n = Number(text);
  return Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 3 }) : String(text);
}

const MONTH_KEYS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

export function formatDate(iso, lang = ENGLISH) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return iso;
  if (lang.code === "en") {
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  }
  return fill(word(lang, "date"), { day: d.getUTCDate(), month: word(lang, `month.${MONTH_KEYS[d.getUTCMonth()]}`),
    year: d.getUTCFullYear() }, "date");
}

export function fieldLabel(name, lang) {
  const key = String(name).replace(/\[[^\]]*\]/g, "");
  const label = lang.ui[`label.${key}`];
  if (label !== undefined) return label;
  if (lang.code === "en") return key.replace(/_/g, " ");
  throw new MissingText(`label.${key}`);
}

const AMOUNT_FIELDS = ["base_price", "gst_amount", "discount", "gross_total", "subsidy_central", "subsidy_state",
  "subsidy_combined", "subsidy_unspecified", "net_cost"];

// A price, subsidy or charge amount, whose value shows as ₹ with Indian grouping.
export function isAmountField(field) {
  const name = String(field || "");
  return name.endsWith(".amount") || AMOUNT_FIELDS.includes(name);
}

// A value as the household sees it: raw text from the quote as printed, or our words for an answer.
export function valueText(value, raw, ctx) {
  const { lang } = ctx;
  if (raw !== undefined && raw !== null && raw !== "") return String(raw);
  if (value === true) return t(lang, "value.bool_true");
  if (value === false) return t(lang, "value.bool_false");
  if (value === null || value === undefined) return t(lang, "value.not_found");
  if (typeof value === "object") return value.raw ? String(value.raw) : t(lang, "value.unreadable");
  const text = String(value);
  if (lang.ui[`value.${text}`] !== undefined) return lang.ui[`value.${text}`];
  if (lang.code === "en") return text;
  if (lang.vocab[`state.${text}`] !== undefined) return lang.vocab[`state.${text}`];
  // Text the quote printed or the household typed (a make and model, a label) is shown as is;
  // an answer code with no words would be English, so it stops the Telugu screen.
  if (/^[a-z_]+$/.test(text) || ctx.answer) throw new MissingText(`value.${text}`);
  return text;
}

function shownValue(field, value, raw, ctx) {
  return isAmountField(field) && value !== null && value !== "" && Number.isFinite(Number(value))
    ? formatInr(value) : valueText(value, raw, ctx);
}

function evidenceItem(e, ctx) {
  const { el, lang } = ctx;
  if (e.kind === "computed") {
    const unit = { dc_kwp: "kwp", dc_kwp_range: "kwp" }[e.name] || (e.name in COMPUTED_INR ? "inr" : "");
    const label = lang.ui[`computed.${e.name}`] !== undefined ? t(lang, `computed.${e.name}`)
      : lang.code === "en" ? e.name : t(lang, `computed.${e.name}`);
    const value = unit === "inr" ? (String(e.value).includes("-") ? `₹${e.value}` : formatInr(e.value))
      : unit === "kwp" ? `${String(e.value).includes("-") ? e.value : formatNumber(e.value)} kWp` : e.value;
    return el("li", {}, t(lang, "ev.computed", { label, value }),
      el("span", { class: "note", text: `(${formulaText(e, lang)})` }));
  }
  const label = fieldLabel(e.field, lang);
  if (e.kind === "user_corrected") {
    const was = e.original_value !== undefined && e.original_value !== null && ctx.mode !== "manual"
      ? t(lang, "ev.was", { value: valueText(e.original_value, undefined, ctx) }) : "";
    const value = shownValue(e.field, e.value, e.raw, ctx);
    if (!e.evidence_text || ctx.mode === "manual") return el("li", { text: t(lang, "ev.entered", { label, value, was }) });
    return el("li", {}, t(lang, "ev.entered_read", { label, value, was }),
      e.page ? t(lang, "ev.page", { n: e.page }) : "", ctx.quoteButton(e.page, e.evidence_text), " ",
      ctx.pageButton(e.page, e.evidence_text));
  }
  if (e.kind === "user_confirmed") {
    return el("li", { text: t(lang, "ev.answer", { label, value: valueText(e.value, undefined, { ...ctx, answer: true }) }) });
  }
  if (!e.evidence_text) return el("li", { text: t(lang, "ev.plain", { label, value: valueText(e.value, e.raw, ctx) }) });
  return el("li", {}, t(lang, "ev.label", { label }), e.page ? t(lang, "ev.page", { n: e.page }) : "",
    ctx.quoteButton(e.page, e.evidence_text), " ", ctx.pageButton(e.page, e.evidence_text));
}

const COMPUTED_INR = { central_cfa_rule: 1, cfa_range: 1, "gross total": 1, "net cost": 1 };

function operandItem(o, ctx) {
  const { el, lang } = ctx;
  const label = fieldLabel(o.field, lang);
  const value = shownValue(o.field, o.value, o.raw, ctx);
  if (o.source === "you typed this") {
    const read = ctx.mode === "manual" ? null : ctx.readingField(o.field);
    if (!read || !read.evidence_text) return el("li", { text: t(lang, "op.typed", { label, value }) });
    return el("li", {}, t(lang, "op.typed_read", { label, value, read: valueText(read.value, undefined, ctx) }),
      read.page ? t(lang, "ev.page", { n: read.page }) : "", ctx.quoteButton(read.page, read.evidence_text, read.boxes),
      " ", ctx.pageButton(read.page, read.evidence_text, read.boxes));
  }
  if (!o.source) return el("li", { text: t(lang, "op.plain", { label, value }) });
  return el("li", {}, t(lang, "op.from", { label, value }), o.source.page ? t(lang, "ev.page", { n: o.source.page }) : "",
    ctx.quoteButton(o.source.page, o.source.text), " ", ctx.pageButton(o.source.page, o.source.text));
}

// A check holds its result until the household says the numbers it used are the ones on the
// quote. "Yes" confirms exactly these numbers for this option; any later change asks again.
function operandQuestion(f, ctx) {
  const { el, lang } = ctx;
  if (!f.confirm_token || f.operands_confirmed !== false) return null;
  const yes = el("button", { type: "button", class: "button", text: t(lang, "op.yes") });
  yes.addEventListener("click", () => ctx.onConfirm(f.confirm_token, yes));
  const fix = el("button", { type: "button", class: "button secondary", text: t(lang, "op.fix"), onclick: ctx.onFix });
  return el("div", { class: "operands" },
    el("ul", {}, (f.operands || []).map((o) => operandItem(o, ctx))),
    el("div", { class: "actions" }, yes, fix));
}

function ruleLine(f, ctx) {
  const { el, lang } = ctx;
  if (!f.rule_id) return null;
  if (lang.ui[`rule.${f.rule_id}`] !== undefined) {
    return el("p", { class: "rule", text: t(lang, "rule.line", { rule: t(lang, `rule.${f.rule_id}`),
      date: formatDate(f.rule_date, lang) }) });
  }
  if (lang.code !== "en") throw new MissingText(`rule.${f.rule_id}`);
  const dated = f.rule_date ? t(lang, "rule.dated", { date: formatDate(f.rule_date, lang) }) : "";
  return el("p", { class: "rule", text: t(lang, "rule.other", { id: f.rule_id, dated }) });
}

function title(f, lang) {
  const key = f.item ? `item.${f.item}` : `check.${f.check_id}`;
  if (lang.ui[key] !== undefined) return lang.ui[key];
  if (lang.code === "en") return f.item || f.check_id;
  throw new MissingText(key);
}

export function findingCard(f, ctx) {
  const { el, lang } = ctx;
  const evidence = (f.evidence || []).map((e) => evidenceItem(e, ctx));
  return el("article", { class: `finding status-${f.status}`, "data-check": `${f.check_id}|${f.item || ""}` },
    el("h4", {}, title(f, lang), " ", el("span", { class: `badge status-${f.status}`,
      text: t(lang, `status.${f.status}.badge`) })),
    el("p", { text: messageText(f, lang) }),
    operandQuestion(f, ctx) || (evidence.length ? el("ul", {}, evidence) : null),
    ruleLine(f, ctx),
    notesText(f, lang).map((note) => el("p", { class: "note", text: note })));
}

export const STATUS_ORDER = ["inconsistent", "needs_confirmation", "missing", "out_of_scope", "consistent"];

export function summaryText(result, ctx) {
  const { lang } = ctx;
  const findings = result.findings || [];
  const counts = Object.fromEntries(STATUS_ORDER.map((s) => [s, findings.filter((f) => f.status === s).length]));
  const parts = STATUS_ORDER.filter((s) => counts[s])
    .map((s) => t(lang, `summary.${s}.${counts[s] === 1 ? "one" : "many"}`, { n: counts[s] }));
  const toCheck = (result.check_this || []).filter((c) => !c.option_id || c.option_id === ctx.option).length;
  return (parts.length ? t(lang, "summary.results", { parts: parts.join(t(lang, "summary.join")) }) : "")
    + (toCheck ? t(lang, toCheck === 1 ? "summary.to_check.one" : "summary.to_check.many", { n: toCheck }) : "")
    + (ctx.entryChecks ? t(lang, "summary.unusual") : "");
}

export function findingGroups(result, ctx) {
  const { el, lang } = ctx;
  const findings = result.findings || [];
  return STATUS_ORDER.filter((s) => findings.some((f) => f.status === s)).map((status) =>
    el("section", { class: "finding-group" },
      el("h3", { text: t(lang, `status.${status}.group`) }),
      findings.filter((f) => f.status === status).map((f) => findingCard(f, ctx))));
}

export function vendorBlock(result, ctx) {
  const { el, lang } = ctx;
  if (!result.vendor_message) {
    // In Telugu, "no questions" needs the keyed lines to say so too (an older result has none).
    if (lang.code !== "en" && ((result.questions || []).length || !Array.isArray(result.vendor_message_lines))) {
      throw new MissingText("vendor message");
    }
    return [el("p", { text: t(lang, "vendor.none") })];
  }
  const message = vendorMessage(result, lang);
  const copyStatus = el("span", { class: "status", role: "status", "aria-live": "polite" });
  const area = el("textarea", { class: "message", rows: 6, readonly: true, hidden: true,
    "aria-label": t(lang, "vendor.textarea") }, message);
  const copied = t(lang, "vendor.copied");
  const select = t(lang, "vendor.select");
  const copy = el("button", { type: "button", class: "button secondary", onclick: async () => {
    try {
      await ctx.copyText(message);
      copyStatus.textContent = copied;
    } catch {
      area.hidden = false;
      area.select();
      copyStatus.textContent = select;
    }
  } }, t(lang, "vendor.copy"));
  const whatsapp = el("a", { class: "button", target: "_blank", rel: "noopener noreferrer",
    href: `https://wa.me/?text=${encodeURIComponent(message)}` }, t(lang, "vendor.whatsapp"));
  return [
    el("ol", {}, (result.questions || []).map((q) => el("li", { text: questionText(q, lang) }))),
    el("p", { class: "hint", text: t(lang, "vendor.hint") }),
    el("div", { class: "message", text: message }),
    area,
    el("div", { class: "actions" }, copy, whatsapp, copyStatus),
  ];
}

// Everything the results screen shows for one result, built in one language. Throws MissingText
// before anything reaches the page if any piece has no words in that language.
export function buildResults(result, ctx) {
  return {
    summary: summaryText(result, ctx),
    groups: findingGroups(result, ctx),
    vendor: vendorBlock(result, ctx),
    mode: ctx.modeKey ? t(ctx.lang, `mode.${ctx.modeKey}`) : "",
  };
}

export function privacyText(lang, region, crossRegion, engine, optOut) {
  if (!region) return t(lang, "privacy.local");
  const where = lang.ui[`region.${region}`] !== undefined ? t(lang, `region.${region}`)
    : t(lang, "region.other", { region });
  if (engine === "textract") return t(lang, optOut ? "privacy.textractOptedOut" : "privacy.textract", { where });
  return t(lang, crossRegion ? "privacy.crossRegion" : "privacy.inRegion", { where });
}
