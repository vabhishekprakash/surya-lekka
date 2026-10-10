"""Every word a check fills into a message at runtime, as its own translatable key.

A message's parameters are English phrases ("total", "more", "base price and GST", "6 panels of
550 W make 3.3 kWp"). message_parts() turns each into a node a translation can rebuild without
any English left over:

  {"text": "..."}                     printed as is: numbers, amounts, units, and text quoted from
                                      the quote (charge labels, makes and models), kept as printed
  {"key": "...", "params": {...}}     a keyed phrase from WORDS with its own nodes
  {"join": [...], "sep": key, "last": key}   a list, joined by keyed separators

render(key, parts, vocabulary) rebuilds the message from any language's vocabulary; with the
English one it gives back exactly the message the check wrote (tests check every message).
"""

import re

from rules import load_cfa_rules

from .common import FIELD_WORDS
from .messages import TEMPLATES, VENDOR_LINES
from .questions import TEMPLATES as QUESTIONS

GAPS = {
    "gap.stated_capacity": ("the panel capacity comes from the stated DC capacity, not from a panel count and wattage "
                            "for every panel group"),
    "gap.range": "the panel wattage is a range",
    "gap.options": "it isn't confirmed that the quote has a single option",
    "gap.pages": "not every page was processed",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WORDS = {
    "word.total": "total", "word.net_cost": "net cost", "word.more": "more", "word.less": "less",
    "hint.missing_charge": "Is a charge missing from the list?",
    "hint.check_amounts": "Please check the amounts with the vendor.",
    **{f"field.{name}": words for name, words in FIELD_WORDS.items()},
    # field names with no words of their own read as the name itself (common.field_words)
    **{f"field.{name}": name.replace("_", " ") for name in ("wattage", "rating", "item", "label", "included_in_total")},
    "field.line": "{words} (line {n})", "field.quoted": '"{label}"', "field.extra_charge": "an extra charge",
    "join.comma": ", ", "join.and": " and ", "join.or": " or ", "join.semicolon": "; ", "join.none": "",
    "panels.one": "{count} panel of {watts} W", "panels.many": "{count} panels of {watts} W",
    "panels.make": "{lines} make {kwp} kWp",
    "sum.term": "{words} {amount}", "sum.plus": " + {words} {amount}", "sum.minus": " minus {words} {amount}",
    "sum.equals": "{terms} = {total}",
    "range.to": "{low} to {high}",
    "charge.item": "{label}: {amount}, {state}{where}", "charge.where": " ({total_label})",
    "charge.outside": "outside the total", "charge.unclear": "not clearly inside the total",
    "charge.no_amount": "amount not given",
    "date": "{day} {month} {year}", **{f"month.{m.lower()}": m for m in MONTHS},
    **GAPS,
}
# The notes under a finding and the working shown beside a computed number.
NOTES = {
    "note.dcr": ("The central subsidy also requires DCR (domestic content) panels and cells. "
                 "This was not verified from the quote."),
    "note.pending": "Some rule values used here are pending verification against the MNRE sources.",
    "note.rules_checked": "The rule values were checked against the MNRE guidelines on {date}.",
    "note.charges_not_added": "Charges listed outside the total were not added: {charges}.",
}
FORMULAS = {
    "formula.stated_dc": "stated DC capacity",
    "formula.slab_first": "{rate} per kWp up to {high} kWp",
    "formula.slab_next": "{rate} per kWp from {low} to {high} kWp",
    "formula.then": ", then ",
    "formula.cap": "{slabs}, at most {cap}",
    "formula.rule_for": "{rule} ({category} category, {state})",
    "category.general": "general", "category.special": "special",
    "formula.first": "{words}", "formula.plus": " + {words}", "formula.minus": " minus {words}",
}
WORDS.update(NOTES)
WORDS.update(FORMULAS)
_RULES = load_cfa_rules()
STATE_NAMES = sorted({name for category in ("general_category", "special_category")
                      for kind in ("states", "union_territories") for name in _RULES[category][kind]})
WORDS.update({f"state.{name}": name for name in STATE_NAMES})
ENGLISH = {**{k: t for k, (_, t) in TEMPLATES.items()}, **{f"question.{k}": v for k, v in QUESTIONS.items()},
           **VENDOR_LINES, **WORDS}
_BY_WORDS = {}
for _key, _words in WORDS.items():
    if _key.startswith("field.") and "{" not in _words:
        _BY_WORDS.setdefault(_words, _key)
_KEYS_OF = {"what": {"total": "word.total", "net cost": "word.net_cost"},
            "direction": {"more": "word.more", "less": "word.less"},
            "hint": {WORDS["hint.missing_charge"]: "hint.missing_charge", WORDS["hint.check_amounts"]: "hint.check_amounts"}}
_LINE = re.compile(r"^(.*) \(line (\d+)\)$")
_PANEL = re.compile(r"^(\d+) panels? of ([0-9.]+) W$")
_TERM = re.compile(r"^(.*) (₹[0-9,]+(?:\.[0-9]+)?)$")
_CHARGE = re.compile(r"^(.*): (.+?), (outside the total|not clearly inside the total)(?: \((.*)\))?$", re.S)
_DATE = re.compile(r"^(\d{1,2}) (" + "|".join(MONTHS) + r") (\d{4})$")


def text(value):
    return {"text": value}


def keyed(key, **params):
    return {"key": key, "params": params}


def _split(value, separators):
    """Split at any of separators, never inside double quotes. Returns (items, separators used)."""
    items, used, current, quoted, i = [], [], "", False, 0
    while i < len(value):
        if value[i] == '"':
            quoted = not quoted
        if not quoted:
            hit = next((s for s in separators if value.startswith(s, i)), None)
            if hit:
                items.append(current)
                used.append(hit)
                current, i = "", i + len(hit)
                continue
        current += value[i]
        i += 1
    return items + [current], used


def _join(nodes, used, names):
    """A join node; separators must be the list form "a, b and c" (or only one kind)."""
    if not used:
        return nodes[0]
    sep, last = names[used[0]], names[used[-1]]
    return {"join": nodes, "sep": sep, "last": last}


def field(words):
    if words.startswith('"') and words.endswith('"') and len(words) >= 2:
        return keyed("field.quoted", label=text(words[1:-1]))
    if words == WORDS["field.extra_charge"]:
        return keyed("field.extra_charge")
    if m := _LINE.match(words):
        return keyed("field.line", words=field(m.group(1)), n=text(m.group(2)))
    key = _BY_WORDS.get(words) or _BY_WORDS.get(words[:1].lower() + words[1:])
    return keyed(key) if key else text(words)  # a charge label in a sum, as printed


def fields(value):
    items, used = _split(value, (", ", " and "))
    return _join([field(i) for i in items], used, {", ": "join.comma", " and ": "join.and"})


def panels(value):
    lines, kwp = value.rsplit(" make ", 1)
    items, used = _split(lines, (", ", " and "))
    nodes = []
    for item in items:
        m = _PANEL.match(item)
        nodes.append(keyed("panels.one" if m.group(1) == "1" else "panels.many", count=text(m.group(1)),
                           watts=text(m.group(2))))
    return keyed("panels.make", lines=_join(nodes, used, {", ": "join.comma", " and ": "join.and"}),
                 kwp=text(kwp.removesuffix(" kWp")))


def sums(value):
    terms_text, total = value.rsplit(" = ", 1)
    items, used = _split(terms_text, (" + ", " minus "))
    nodes = []
    for i, item in enumerate(items):
        m = _TERM.match(item)
        key = "sum.term" if i == 0 else ("sum.plus" if used[i - 1] == " + " else "sum.minus")
        node = keyed(key, words=field(m.group(1)), amount=text(m.group(2)))
        if i == 0 and m.group(1)[:1].isupper() and not m.group(1)[:1] == m.group(1)[:1].lower():
            node["capitalize"] = True
        nodes.append(node)
    return keyed("sum.equals", terms={"join": nodes, "sep": "join.none", "last": "join.none"}, total=text(total))


def gaps(value):
    rest, nodes = value, []
    while rest:
        key = next(k for k, phrase in GAPS.items() if rest.startswith(phrase))
        nodes.append(keyed(key))
        rest = rest[len(GAPS[key]):].removeprefix(" and ")
    return nodes[0] if len(nodes) == 1 else {"join": nodes, "sep": "join.and", "last": "join.and"}


def charges(value):
    items, _ = _split(value, ("; ",))
    nodes = []
    for item in items:
        m = _CHARGE.match(item)
        label = keyed("field.extra_charge") if m.group(1) == WORDS["field.extra_charge"] else text(m.group(1))
        amount = keyed("charge.no_amount") if m.group(2) == WORDS["charge.no_amount"] else text(m.group(2))
        state = keyed("charge.outside" if m.group(3) == WORDS["charge.outside"] else "charge.unclear")
        where = keyed("charge.where", total_label=text(m.group(4))) if m.group(4) is not None else text("")
        nodes.append(keyed("charge.item", label=label, amount=amount, state=state, where=where))
    return nodes[0] if len(nodes) == 1 else {"join": nodes, "sep": "join.semicolon", "last": "join.semicolon"}


def options(value):
    choices, _ = _split(value, ("; ",))
    nodes = []
    for choice in choices:
        items, used = _split(choice, (", ", " or "))
        nodes.append(_join([text(i) for i in items], used, {", ": "join.comma", " or ": "join.or"}))
    return nodes[0] if len(nodes) == 1 else {"join": nodes, "sep": "join.semicolon", "last": "join.semicolon"}


def dc_kwp(value):
    if " to " in value:
        low, high = value.split(" to ", 1)
        return keyed("range.to", low=text(low), high=text(high))
    return text(value)


def cutoff(value):
    m = _DATE.match(value)
    if not m:
        return text(value)
    return keyed("date", day=text(m.group(1)), month=keyed(f"month.{m.group(2).lower()}"), year=text(m.group(3)))


PARSERS = {"fields": fields, "panels": panels, "sums": sums, "gaps": gaps, "charges": charges, "options": options,
           "dc_kwp": dc_kwp, "cutoff": cutoff}
# A placeholder that means something else in one message: "Is {charges} inside the total?" names fields.
BY_MESSAGE = {"C3.charge_inside": {"charges": fields}}


def node(name, value, key=None):
    if name in _KEYS_OF and value in _KEYS_OF[name]:
        return keyed(_KEYS_OF[name][value])
    parser = BY_MESSAGE.get(key, {}).get(name) or PARSERS.get(name)
    if parser and isinstance(value, str):
        return parser(value)
    return text("" if value is None else str(value))


def message_parts(params, key=None):
    """{placeholder: node} for one message's parameters (key: the message's key)."""
    return {name: node(name, value, key) for name, value in (params or {}).items()}


_RULES_CHECKED = re.compile(r"^The rule values were checked against the MNRE guidelines on (.+)\.$")
_NOT_ADDED = re.compile(r"^Charges listed outside the total were not added: (.+)\.$", re.S)
_SLAB_FIRST = re.compile(r"^(₹[0-9,]+) per kWp up to ([0-9.]+) kWp$")
_SLAB_NEXT = re.compile(r"^(₹[0-9,]+) per kWp from ([0-9.]+) to ([0-9.]+) kWp$")
_RULE_FOR = re.compile(r"^(.*) \((general|special) category, (.+)\)$")


def note_node(note):
    """A finding's note as a node, or None for a note with no key (shown only in English)."""
    for key in ("note.dcr", "note.pending"):
        if note == WORDS[key]:
            return keyed(key)
    if m := _RULES_CHECKED.match(note):
        return keyed("note.rules_checked", date=cutoff(m.group(1)))
    if m := _NOT_ADDED.match(note):
        return keyed("note.charges_not_added", charges=fields(m.group(1)))
    return None


def _rule(formula):
    slabs, cap = formula.rsplit(", at most ", 1)
    nodes = []
    for part in slabs.split(", then "):
        if m := _SLAB_FIRST.match(part):
            nodes.append(keyed("formula.slab_first", rate=text(m.group(1)), high=text(m.group(2))))
        else:
            m = _SLAB_NEXT.match(part)
            nodes.append(keyed("formula.slab_next", rate=text(m.group(1)), low=text(m.group(2)),
                               high=text(m.group(3))))
    joined = nodes[0] if len(nodes) == 1 else {"join": nodes, "sep": "formula.then", "last": "formula.then"}
    return keyed("formula.cap", slabs=joined, cap=text(cap))


def formula_node(name, formula):
    """The working beside a computed number as a node, or None when it has no key."""
    if name in ("dc_kwp", "dc_kwp_range"):
        if formula == WORDS["formula.stated_dc"]:
            return keyed("formula.stated_dc")
        return text(formula) if re.fullmatch(r"[0-9.xW+() /-]+", formula) else None
    if name in ("central_cfa_rule", "cfa_range"):
        m = _RULE_FOR.match(formula)
        if m:
            return keyed("formula.rule_for", rule=_rule(m.group(1)), category=keyed(f"category.{m.group(2)}"),
                         state=keyed(f"state.{m.group(3)}"))
        return _rule(formula)
    if name in ("gross total", "net cost"):
        items, used = _split(formula, (" + ", " minus "))
        nodes = []
        for i, item in enumerate(items):
            key = "formula.first" if i == 0 else ("formula.plus" if used[i - 1] == " + " else "formula.minus")
            node = keyed(key, words=field(item))
            if i == 0 and item[:1] != item[:1].lower():
                node["capitalize"] = True
            nodes.append(node)
        return {"join": nodes, "sep": "join.none", "last": "join.none"}
    return None


def render_node(n, vocabulary):
    if "text" in n:
        return n["text"]
    if "join" in n:
        parts = [render_node(x, vocabulary) for x in n["join"]]
        if len(parts) == 1:
            return parts[0]
        return vocabulary[n["sep"]].join(parts[:-1]) + vocabulary[n["last"]] + parts[-1]
    out = vocabulary[n["key"]].format(**{k: render_node(v, vocabulary) for k, v in n["params"].items()})
    return out[:1].upper() + out[1:] if n.get("capitalize") else out


def render(key, parts, vocabulary=ENGLISH):
    return vocabulary[key].format(**{k: render_node(v, vocabulary) for k, v in parts.items()})


def text_nodes(n):
    """Every printed-as-is value under a node."""
    if "text" in n:
        yield n["text"]
    for child in n.get("join") or []:
        yield from text_nodes(child)
    for child in (n.get("params") or {}).values():
        yield from text_nodes(child)
