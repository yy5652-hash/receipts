// Receipts web app. Vanilla ES module, no build step, no framework.
// Everything that touches the page goes through DOM nodes and textContent, never innerHTML on user or
// model text, so a pasted report cannot inject markup. The pure helpers up to "DOM" are also used by tests.

/* ------------------------------------------------------------------ constants */

export const ORDER = ["supported", "partial", "contradicted", "not_in_source", "source_unavailable"];
export const PROBLEMS = new Set(["partial", "contradicted", "not_in_source", "source_unavailable"]);
export const LABELS = {
  supported: { badge: "Supported", short: "supported", icon: "check" },
  partial: { badge: "Partly supported", short: "partly", icon: "half" },
  contradicted: { badge: "Contradicted", short: "contradicted", icon: "cross" },
  not_in_source: { badge: "Not in source", short: "not in source", icon: "question" },
  source_unavailable: { badge: "Source missing", short: "source missing", icon: "broken" },
  checking: { badge: "Checking…", short: "checking", icon: "dots" },
  unchecked: { badge: "Not checked", short: "not checked", icon: "question" }, // the run stopped before this claim was judged
};
const SHOWN = [...ORDER, "checking", "unchecked"];
const TIER_NAME = { nano: "Nemotron 3 Nano", super: "Nemotron 3 Super", ultra: "Nemotron 3 Ultra" };
const STAGES = ["extract", "fetch", "judge", "done"];

const ICON_PATHS = {
  check: '<path d="M3.5 8.5l3 3 6-7"/>',
  half: '<circle cx="8" cy="8" r="5.5"/><path d="M8 2.5a5.5 5.5 0 0 1 0 11z" fill="currentColor" stroke="none"/>',
  cross: '<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>',
  question: '<path d="M6 6.1a2.1 2.1 0 1 1 3.2 1.8c-.7.4-1.2.9-1.2 1.7"/><circle cx="8" cy="12.3" r=".7" fill="currentColor" stroke="none"/>',
  broken: '<path d="M6.8 5.2H5.6a2.8 2.8 0 0 0 0 5.6h1.2M9.2 5.2h1.2a2.8 2.8 0 0 1 0 5.6H9.2M5.4 13l5.2-10"/>',
  dots: '<circle cx="3.5" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1.1" fill="currentColor" stroke="none"/><circle cx="12.5" cy="8" r="1.1" fill="currentColor" stroke="none"/>',
  external: '<path d="M6.5 3.5h-2a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1v-2M9 3.5h3.5V7M12.5 3.5L7.5 8.5"/>',
  receipt: '<path d="M3.5 2h9v12l-1.5-1-1.5 1L8 13l-1.5 1L5 13l-1.5 1z"/><path d="M6 5.5h4M6 8h4M6 10.5h2.5"/>',
};

/* ------------------------------------------------------------------ pure helpers */

/** Best label across a claim's sources, or "checking" while verdicts are still due. */
export function claimLabel(claim, vmap, running, knownSources, halted = false) {
  const vs = vmap ? [...vmap.values()] : [];
  if (vs.some((v) => v.label === "supported")) return "supported";
  const expected = claim.source_ids.filter((id) => !knownSources || knownSources.has(id)).length;
  if ((running || halted) && vs.length < Math.max(expected, 1)) return running ? "checking" : "unchecked";
  if (!vs.length) return "source_unavailable";
  return vs.map((v) => v.label).reduce((a, b) => (ORDER.indexOf(b) < ORDER.indexOf(a) ? b : a));
}

/** The verdict that decides the claim's label (first best one), or null. */
export function primaryVerdict(vmap) {
  let best = null;
  for (const v of vmap ? vmap.values() : []) if (!best || ORDER.indexOf(v.label) < ORDER.indexOf(best.label)) best = v;
  return best;
}

export function countLabels(labels) {
  const counts = { supported: 0, partial: 0, contradicted: 0, not_in_source: 0, source_unavailable: 0, checking: 0, unchecked: 0 };
  for (const l of labels) counts[l] += 1;
  return counts;
}

export function fmtTime(sec) {
  if (!(sec >= 0)) return "—";
  if (sec < 60) return `${sec.toFixed(1)} s`;
  return `${Math.floor(sec / 60)} min ${Math.round(sec % 60)} s`;
}

export const fmtCost = (usd) => `$${(usd || 0).toFixed(4)}`;
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/**
 * The "Models" fact: only the tiers that were used, in the order nano, super, ultra, each with its role.
 * used: Set of tiers with calls; direct: Set of tiers that gave a first-pass verdict; escalatedClaims: claims Ultra re-checked.
 */
export function modelsLine(used, direct, escalatedClaims = 0) {
  const parts = [];
  if (used.has("nano")) parts.push(`${TIER_NAME.nano} (${direct.has("nano") ? "extraction and checking" : "extraction"})`);
  if (used.has("super")) parts.push(`${parts.length ? "Super" : TIER_NAME.super} (${direct.has("super") || escalatedClaims ? "checking" : "extraction repair"})`);
  if (used.has("ultra")) {
    const roles = [];
    if (direct.has("ultra") || !escalatedClaims) roles.push("checking");
    if (escalatedClaims) roles.push(`re-check, ${plural(escalatedClaims, "claim")}`);
    parts.push(`${parts.length ? "Ultra" : TIER_NAME.ultra} (${roles.join("; ")})`);
  }
  return parts.join(" · ") || "—";
}

export function checkedBy(v) {
  if (!v || v.tier === "none") return "No model call: the source could not be read.";
  const conf = `confidence ${Math.round((v.confidence || 0) * 100)}%`;
  if (v.escalated) return `${TIER_NAME.super} → re-checked by ${TIER_NAME[v.tier] || v.tier} · ${conf}`;
  return `${TIER_NAME[v.tier] || v.tier} · ${conf}`;
}

export function sourceStatus(f, running) {
  if (!f) return running ? { key: "pending", text: "Looking up…", icon: "dots" } : { key: "unknown", text: "Not checked", icon: "question" };
  if (f.status === "dead_link") return { key: "dead", text: "Dead link", icon: "broken" };
  if (f.status === "not_found") {
    return f.exists ? { key: "unreadable", text: "Exists · text unreadable", icon: "question" } : { key: "notfound", text: "Not found", icon: "question" };
  }
  return f.coverage === "abstract" ? { key: "abstract", text: "Abstract only", icon: "half" } : { key: "full", text: "Found · full text", icon: "check" };
}

export function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

const safeUrl = (u) => (typeof u === "string" && /^https?:\/\//i.test(u) ? u : null);

/** "en.wikipedia.org · Climate change" for https://en.wikipedia.org/wiki/Climate_change; "" when there is no URL. */
export function readableUrl(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return "";
  }
  const host = u.hostname.replace(/^www\./, "");
  let last = u.pathname.split("/").filter(Boolean).pop() || "";
  try {
    last = decodeURIComponent(last);
  } catch { /* keep it as written */ }
  last = last.replace(/\.(?:html?|php|aspx?)$/i, "").replace(/[_\-+]+/g, " ").replace(/\s+/g, " ").trim();
  return last ? `${host} · ${last}` : host;
}

/** Title to show for a source: its own title, the fetched page's title, then a readable form of the URL, never the raw URL. */
export function sourceTitle(src, fetched) {
  const real = (t) => {
    const x = typeof t === "string" ? t.replace(/\s+/g, " ").trim() : "";
    return x && !/^https?:\/\/\S+$/i.test(x) ? x : "";
  };
  const own = real(src && src.title) || real(fetched && fetched.title);
  if (own) return own;
  const url = safeUrl((src && src.url) || (fetched && fetched.url));
  const fromUrl = url ? readableUrl(url) : "";
  if (fromUrl) return fromUrl;
  const raw = ((src && src.raw) || "").replace(/\s+/g, " ").trim();
  return (/^https?:\/\/\S+$/i.test(raw) ? readableUrl(raw) : raw.slice(0, 120)) || "Untitled source";
}

/** Block structure of the raw text, with offsets into it: headings, paragraphs, list items, reference entries... */
export function splitBlocks(doc) {
  const blocks = [];
  let cur = null;
  let fence = null;
  const flush = () => {
    if (cur) blocks.push(cur);
    cur = null;
  };
  let i = 0;
  while (i <= doc.length) {
    let j = doc.indexOf("\n", i);
    if (j === -1) j = doc.length;
    const start = i;
    const end = j > start && doc[j - 1] === "\r" ? j - 1 : j;
    const line = doc.slice(start, end);
    i = j + 1;
    let m;
    if (fence) {
      if (/^\s*(```|~~~)/.test(line)) {
        if (fence.start !== -1) blocks.push(fence);
        fence = null;
      } else {
        fence.end = end;
        if (fence.start === -1) fence.start = start;
      }
    } else if (/^\s*(```|~~~)/.test(line)) {
      flush();
      fence = { type: "code", start: -1, end: start };
    } else if (!line.trim()) {
      flush();
    } else if ((m = /^(#{1,6})[ \t]+/.exec(line))) {
      flush();
      blocks.push({ type: "h", level: m[1].length, start: start + m[0].length, end });
    } else if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      blocks.push({ type: "hr", start, end });
    } else if ((m = /^\s*([-*+]|\d+[.)])[ \t]+/.exec(line))) {
      flush();
      cur = { type: "li", ordered: /\d/.test(m[1]), start: start + m[0].length, end };
    } else if (/^\s*\[(\d+|\^[^\]\s]+)\]:?[ \t]+/.test(line)) {
      flush();
      cur = { type: "ref", start: start + (line.length - line.trimStart().length), end };
    } else if ((m = /^\s*>[ \t]?/.exec(line))) {
      if (cur && cur.type === "quote") cur.end = end;
      else {
        flush();
        cur = { type: "quote", start: start + m[0].length, end };
      }
    } else if (cur && ["p", "li", "ref"].includes(cur.type)) {
      cur.end = end;
    } else {
      flush();
      cur = { type: "p", start: start + (line.length - line.trimStart().length), end };
    }
    if (j >= doc.length) break;
  }
  if (fence && fence.start !== -1) blocks.push(fence);
  flush();
  return blocks.filter((b) => b.end >= b.start && (b.type === "hr" || b.end > b.start));
}

/** Claims sorted, clipped to the text, trimmed of whitespace, and made non-overlapping. */
export function layoutClaims(claims, doc) {
  const out = [];
  let cursor = 0;
  for (const claim of [...claims].sort((a, b) => a.start - b.start || a.end - b.end)) {
    let start = Math.max(claim.start, cursor);
    let end = Math.min(claim.end, doc.length);
    while (start < end && /\s/.test(doc[start])) start++;
    while (end > start && /\s/.test(doc[end - 1])) end--;
    if (start >= end) continue;
    out.push({ claim, start, end });
    cursor = end;
  }
  return out;
}

/** The pieces of one block: plain text runs and claim runs, covering [block.start, block.end). */
export function blockPieces(block, layout) {
  const pieces = [];
  let pos = block.start;
  for (const L of layout) {
    if (L.end <= block.start) continue;
    if (L.start >= block.end) break;
    const a = Math.max(L.start, block.start);
    const b = Math.min(L.end, block.end);
    if (a > pos) pieces.push({ a: pos, b: a, claimId: null });
    pieces.push({ a, b, claimId: L.claim.id, first: a === L.start, last: b === L.end });
    pos = b;
  }
  if (pos < block.end) pieces.push({ a: pos, b: block.end, claimId: null });
  return pieces;
}

/** Match each reference-list entry of the report to the block that holds it (entries sit after the last claim). */
export function attachReferences(doc, blocks, sources, layout) {
  const floor = layout.length ? layout[layout.length - 1].end : 0;
  const unmatched = [];
  const hits = [];
  for (const s of sources) {
    const raw = (s.raw || "").replace(/\s+/g, " ").trim();
    let pos = -1;
    for (const n of [70, 40, 20]) {
      if (raw.length < Math.min(n, 12)) break;
      pos = doc.indexOf(raw.slice(0, n), floor);
      if (pos >= 0) break;
    }
    if (pos >= 0) hits.push({ pos, s });
    else unmatched.push(s);
  }
  // Unnumbered lists (APA, Chicago) arrive as one paragraph per run of lines: split at each entry.
  hits.sort((a, b) => a.pos - b.pos);
  for (const { pos, s } of hits) {
    const i = blocks.findIndex((b) => b.start <= pos && pos < b.end && b.type !== "hr" && b.type !== "code");
    if (i < 0) {
      unmatched.push(s);
      continue;
    }
    const b = blocks[i];
    if (pos > b.start && doc.slice(b.start, pos).trim()) {
      let headEnd = pos;
      while (headEnd > b.start && /\s/.test(doc[headEnd - 1])) headEnd--;
      blocks.splice(i, 1, { ...b, end: headEnd }, { type: "ref", start: pos, end: b.end, sourceId: s.id });
    } else if (!b.sourceId) {
      b.sourceId = s.id;
      if (b.type === "p") b.type = "ref";
    } else {
      unmatched.push(s);
    }
  }
  return unmatched;
}

const INLINE_RULES = [
  { t: "code", re: /`([^`\n]+)`/y },
  { t: "link", re: /\[([^\]\n]+)\]\((https?:\/\/(?:[^()\s]|\([^()\s]*\))+)\)/y },
  { t: "strong", re: /\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__/y },
  { t: "em", re: /\*(?=[^\s*])(.+?)(?<=[^\s*])\*|(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])/y },
  { t: "cite", re: /\[(?:\d+(?:\s*[,–-]\s*\d+)*|\^[^\]\s]+)\]/y },
  { t: "url", re: /https?:\/\/[^\s<>]+/y },
];

/** Minimal inline Markdown -> tree: code, links, bold, italic, citation markers, bare URLs. Everything else is text. */
export function parseInline(text, depth = 0) {
  const out = [];
  let buf = "";
  const flush = () => {
    if (buf) out.push({ t: "text", v: buf });
    buf = "";
  };
  let i = 0;
  while (i < text.length) {
    let hit = null;
    const ch = text[i];
    if ("`[*_h".includes(ch)) {
      for (const rule of INLINE_RULES) {
        rule.re.lastIndex = i;
        const m = rule.re.exec(text);
        if (m) {
          hit = { rule, m };
          break;
        }
      }
    }
    if (!hit) {
      buf += ch;
      i += 1;
      continue;
    }
    flush();
    const { rule, m } = hit;
    const inner = m[1] ?? m[2];
    if (rule.t === "code") out.push({ t: "code", v: inner });
    else if (rule.t === "cite") out.push({ t: "cite", v: m[0] });
    else if (rule.t === "url") {
      const url = m[0].replace(/[.,;:!?)\]'"]+$/, "");
      out.push({ t: "link", href: url, children: [{ t: "text", v: url }] });
      i += url.length;
      continue;
    } else if (rule.t === "link") out.push({ t: "link", href: m[2], children: depth < 3 ? parseInline(inner, depth + 1) : [{ t: "text", v: inner }] });
    else out.push({ t: rule.t, children: depth < 3 ? parseInline(inner, depth + 1) : [{ t: "text", v: inner }] });
    i += m[0].length;
  }
  flush();
  return out;
}

/** Filename-safe slug of the report title. */
export function slugOf(doc) {
  const m = /^#\s+(.+)$/m.exec(doc || "");
  const s = (m ? m[1] : "report").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return s.slice(0, 50) || "report";
}

/* ------------------------------------------------------------------ DOM */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid);
  return el;
}

function icon(name, size = 14) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  svg.innerHTML = ICON_PATHS[name] || ""; // static strings from ICON_PATHS only
  return svg;
}

function inlineNodes(tokens) {
  return tokens.map((t) => {
    switch (t.t) {
      case "text": return document.createTextNode(t.v);
      case "code": return h("code", { text: t.v });
      case "strong": return h("strong", null, inlineNodes(t.children));
      case "em": return h("em", null, inlineNodes(t.children));
      case "cite": return h("span", { class: "cite", text: t.v });
      case "link": {
        const href = safeUrl(t.href);
        return href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }, inlineNodes(t.children)) : h("span", null, inlineNodes(t.children));
      }
      default: return document.createTextNode("");
    }
  });
}

const flat = (s) => s.replace(/\s*\r?\n\s*(?:>[ \t]?)?/g, " "); // soft line breaks (and quote markers) become spaces

/* ------------------------------------------------------------------ state */

const S = {
  config: { live: false, reason: null, models: {}, limits: { max_chars: 40000 }, examples: [] },
  doc: "",
  mode: "ultra",
  claims: [],
  sources: [],
  fetched: new Map(), // source_id -> FetchedSource (no text)
  verdicts: new Map(), // claim_id -> Map(source_id -> Verdict)
  stage: "idle",
  running: false,
  report: null,
  replayId: null,
  selected: null,
  selSource: null,
  filter: "all",
  notices: [],
  error: null,
  startedAt: 0,
  timer: null,
  abort: null,
  spans: new Map(), // claim_id -> [span]
  flags: new Map(), // claim_id -> flag element
  chips: new Map(), // source_id -> [chip element]
  raf: 0,
  dirtyEvidence: false,
  known: new Set(), // ids of the sources of this report
  renderedClaims: -1,
  inputError: "",
  emptyHint: false,
  titles: new Map(), // source_id -> element showing the title in the reference list
};

const setSources = (sources) => {
  S.sources = sources;
  S.known = new Set(sources.map((s) => s.id));
};
const labelOf = (claim) => claimLabel(claim, S.verdicts.get(claim.id), S.running, S.known, !!S.error);
const claimById = (id) => S.claims.find((c) => c.id === id);
const sourceById = (id) => S.sources.find((s) => s.id === id);
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const isNarrow = () => window.matchMedia("(max-width: 899px)").matches;

function resetResult() {
  if (S.abort) S.abort.abort();
  clearInterval(S.timer);
  Object.assign(S, {
    doc: "", claims: [], sources: [], fetched: new Map(), verdicts: new Map(), stage: "idle", running: false,
    report: null, replayId: null, selected: null, selSource: null, filter: "all", notices: [], error: null,
    abort: null, spans: new Map(), flags: new Map(), chips: new Map(), titles: new Map(), dirtyEvidence: false, known: new Set(),
    renderedClaims: -1,
  });
  $("#doc").replaceChildren();
}

function showScreen(name) {
  document.body.dataset.screen = name;
  $("#screen-input").hidden = name !== "input";
  $("#screen-result").hidden = name !== "result";
  document.body.removeAttribute("data-sheet");
  window.scrollTo(0, 0);
}

/* ------------------------------------------------------------------ input screen */

function liveNoteText() {
  const c = S.config;
  if (c.live) return "";
  if (c.reason === "budget") return "Today's budget for live checks is used up, so live checking is paused. The recorded examples below still work.";
  return "Live checks are not set up on this server (no model or search keys). The recorded examples below still work.";
}

function updateInputUi() {
  const ta = $("#doc-input");
  const max = S.config.limits.max_chars;
  const n = ta.value.length;
  const over = n > max;
  const counter = $("#char-count");
  counter.textContent = `${n.toLocaleString("en-US")} / ${max.toLocaleString("en-US")}`;
  counter.classList.toggle("over", over);
  const ready = !!(S.config.live && ta.value.trim() && !over);
  $("#check-btn").setAttribute("aria-disabled", String(!ready));
  const hint = $("#empty-hint");
  hint.hidden = !S.emptyHint;
  if (S.emptyHint) ta.setAttribute("aria-describedby", "empty-hint");
  else ta.removeAttribute("aria-describedby");

  const note = $("#live-note");
  const overText = `Reports longer than ${max.toLocaleString("en-US")} characters are not accepted. Shorten it to check it.`;
  const text = !S.config.live ? liveNoteText() : over ? overText : S.inputError;
  note.textContent = text;
  note.hidden = !text;
  note.classList.toggle("error", over || !!S.inputError);

  const m = S.config.models || {};
  $("#mode-hint").textContent = {
    ultra: "Most accurate on ReceiptsBench: 99.6% macro accuracy, 0 false alarms in 150 supported sentences, about $0.003 per claim.",
    nano: "98.6% macro accuracy on ReceiptsBench at about a twelfth of Ultra's cost ($0.0003 per claim).",
    super: "98.6% macro accuracy on ReceiptsBench, about $0.0007 per claim.",
    routed: "Nano checks every claim; Ultra re-checks a “supported” verdict only when its quote does not show the claim's figures or absolute words. 98.3% on ReceiptsBench.",
    cascade: "Super checks every claim; Ultra re-checks the uncertain, partial, contradicted or not-found ones. 98.2% on ReceiptsBench.",
  }[$("#mode").value];
}

function renderExamples() {
  const list = $("#example-list");
  list.replaceChildren();
  for (const ex of S.config.examples) {
    list.append(
      h("button", { type: "button", class: "example-card", dataset: { id: ex.id }, "aria-label": `Open recorded example: ${ex.title}`, onclick: () => openExample(ex.id) },
        h("span", { class: "example-tag" }, icon("receipt", 13), "Recorded run"),
        h("span", { class: "example-title", text: ex.title }),
        ex.blurb ? h("span", { class: "example-blurb", text: ex.blurb }) : null,
      ),
    );
  }
  $(".examples").hidden = !S.config.examples.length;
}

function showInputError(message) {
  S.inputError = message;
  updateInputUi();
}

/* ------------------------------------------------------------------ document rendering */

function renderDocument() {
  const root = $("#doc");
  root.replaceChildren();
  S.spans = new Map();
  S.flags = new Map();
  S.chips = new Map();
  S.titles = new Map();
  S.renderedClaims = S.claims.length;
  const doc = S.doc;
  const blocks = splitBlocks(doc);
  const layout = layoutClaims(S.claims, doc);
  const unmatched = attachReferences(doc, blocks, S.sources, layout);

  const fillBlock = (el, block) => {
    for (const p of blockPieces(block, layout)) {
      const nodes = inlineNodes(parseInline(flat(doc.slice(p.a, p.b))));
      if (!p.claimId) {
        el.append(...nodes);
        continue;
      }
      const span = h("span", {
        class: "claim", role: "button", tabindex: "0", "aria-pressed": "false", dataset: { claim: p.claimId, label: "checking" },
      }, nodes);
      if (!S.spans.has(p.claimId)) S.spans.set(p.claimId, []);
      S.spans.get(p.claimId).push(span);
      el.append(span);
      if (p.last) {
        const flag = h("span", { class: "flag", role: "img", "aria-label": LABELS.checking.badge }, icon("dots", 12));
        S.flags.set(p.claimId, flag);
        el.append(flag);
      }
    }
    if (block.sourceId) {
      const chip = h("span", { class: "src-chip", dataset: { source: block.sourceId } });
      (S.chips.get(block.sourceId) || S.chips.set(block.sourceId, []).get(block.sourceId)).push(chip);
      el.append(" ", chip);
    }
  };

  let list = null;
  let refs = null;
  for (const b of blocks) {
    if (b.type !== "li") list = null;
    if (b.type !== "ref") refs = null;
    let el;
    switch (b.type) {
      case "h": el = h(`h${Math.min(b.level + 2, 6)}`, { class: `md-h md-h${b.level}` }); fillBlock(el, b); break;
      case "hr": el = h("hr"); break;
      case "li": {
        if (!list || list.dataset.ordered !== String(b.ordered)) {
          list = h(b.ordered ? "ol" : "ul", { dataset: { ordered: String(b.ordered) } });
          root.append(list);
        }
        el = h("li");
        fillBlock(el, b);
        list.append(el);
        continue;
      }
      case "ref": {
        if (!refs) {
          refs = h("div", { class: "refs" });
          root.append(refs);
        }
        el = h("p", { class: "ref" });
        fillBlock(el, b);
        refs.append(el);
        continue;
      }
      case "quote": el = h("blockquote"); el.append(h("p")); fillBlock(el.firstChild, b); break;
      case "code": el = h("pre", null, h("code", { text: doc.slice(Math.max(0, b.start), b.end) })); break;
      default: el = h("p"); fillBlock(el, b);
    }
    root.append(el);
  }

  if (unmatched.length) {
    const ul = h("ul", { class: "extra-sources" });
    for (const s of unmatched) {
      const chip = h("span", { class: "src-chip", dataset: { source: s.id } });
      (S.chips.get(s.id) || S.chips.set(s.id, []).get(s.id)).push(chip);
      const href = safeUrl(s.url);
      const label = href ? h("a", { href, target: "_blank", rel: "noopener noreferrer" }) : h("span");
      label.textContent = sourceTitle(s, S.fetched.get(s.id));
      S.titles.set(s.id, label);
      ul.append(h("li", null, label, " ", chip));
    }
    root.append(h("section", { class: "doc-sources" }, h("h4", { text: unmatched.length === S.sources.length ? "Linked sources" : "Other sources" }), ul));
  }
  updateClaimStates();
  updateSourceChips();
}

function updateClaimStates() {
  for (const claim of S.claims) {
    const label = labelOf(claim);
    const meta = LABELS[label];
    for (const span of S.spans.get(claim.id) || []) {
      span.dataset.label = label;
      if (PROBLEMS.has(label)) span.dataset.problem = "1";
      else delete span.dataset.problem;
      span.setAttribute("aria-pressed", String(S.selected === claim.id));
      span.classList.toggle("is-selected", S.selected === claim.id);
    }
    const flag = S.flags.get(claim.id);
    if (flag && flag.dataset.label !== label) {
      flag.dataset.label = label;
      flag.setAttribute("aria-label", meta.badge);
      flag.replaceChildren(icon(meta.icon, 12));
    }
  }
  $("#doc").dataset.filter = S.filter;
}

function updateSourceChips() {
  for (const [sid, el] of S.titles) {
    const t = sourceTitle(sourceById(sid), S.fetched.get(sid));
    if (el.textContent !== t) el.textContent = t;
  }
  for (const [sid, chips] of S.chips) {
    const st = sourceStatus(S.fetched.get(sid), S.running);
    for (const chip of chips) {
      if (chip.dataset.key === st.key) continue;
      chip.dataset.key = st.key;
      chip.replaceChildren(icon(st.icon, 12), st.text);
    }
  }
}

/* ------------------------------------------------------------------ summary, progress, facts */

function renderSummary() {
  const labels = S.claims.map(labelOf);
  const counts = countLabels(labels);
  const n = S.claims.length;

  let headline;
  if (!n) headline = S.running ? "Reading claims…" : "No cited claims found";
  else headline = plural(n, "cited claim");
  $("#summary-headline").textContent = headline;

  const countsEl = $("#summary-counts");
  countsEl.replaceChildren();
  const parts = [];
  for (const l of SHOWN) {
    if (!counts[l]) continue;
    parts.push(h("span", { class: "count", dataset: { label: l } }, icon(LABELS[l].icon, 13), h("b", { text: String(counts[l]) }), ` ${LABELS[l].short}`));
  }
  countsEl.append(...parts);

  const bar = $("#summary-bar");
  bar.replaceChildren();
  bar.hidden = !n;
  for (const l of SHOWN) {
    if (!counts[l]) continue;
    const seg = h("span", { class: "seg", dataset: { label: l } });
    seg.style.flexGrow = String(counts[l]);
    bar.append(seg);
  }
  bar.setAttribute("aria-label", n ? `${plural(n, "claim")}: ` + SHOWN.filter((l) => counts[l]).map((l) => `${counts[l]} ${LABELS[l].short}`).join(", ") : "No claims yet");

  $("#n-all").textContent = String(n);
  $("#n-problems").textContent = String(labels.filter((l) => PROBLEMS.has(l)).length);

  // facts
  const escalated = new Set();
  const direct = new Set();
  const used = new Set();
  if (S.report) {
    for (const u of S.report.usage || []) if (u.calls > 0) used.add(u.tier);
  } else if (S.running) {
    used.add("nano"); // extraction is the first call of every run
  }
  for (const vm of S.verdicts.values()) {
    for (const v of vm.values()) {
      if (v.tier === "none") continue;
      used.add(v.tier);
      if (v.escalated) {
        escalated.add(v.claim_id);
        used.add("super"); // the first pass of a re-checked claim
      } else direct.add(v.tier);
    }
  }
  $("#fact-models").textContent = modelsLine(used, direct, escalated.size);
  if (S.report) {
    $("#fact-time").textContent = fmtTime(S.report.seconds);
    $("#fact-cost").textContent = fmtCost(S.report.usage.reduce((a, u) => a + (u.cost_usd || 0), 0));
  } else {
    $("#fact-cost").textContent = "—";
  }

  // progress
  const prog = $("#progress");
  prog.hidden = !S.running;
  const idx = STAGES.indexOf(S.stage);
  const checked = labels.filter((l) => l !== "checking").length;
  const counters = [
    n ? plural(n, "claim") : "",
    S.sources.length ? `${S.fetched.size}/${S.sources.length} sources` : "",
    n && idx >= 2 ? `${checked}/${n} claims` : "",
  ];
  $$("li", prog).forEach((li, i) => {
    li.dataset.state = i < idx ? "done" : i === idx ? "active" : "pending";
    $(".step-count", li).textContent = i <= idx || i === 0 ? counters[i] : "";
  });

  $("#dl-md").disabled = $("#dl-json").disabled = !S.report;
}

function renderBanners() {
  const box = $("#banners");
  box.replaceChildren();
  for (const msg of S.notices) box.append(h("p", { class: "banner info", role: "status" }, icon("question", 15), msg));
  if (S.error) {
    box.append(h("p", { class: "banner error", role: "alert" }, icon("cross", 15), S.error));
  } else if (S.report && !S.claims.length) {
    box.append(h("p", { class: "banner info", role: "status" }, icon("question", 15),
      "Receipts found no sentence that cites a source. It looks for numeric markers like [1], inline links, or (Author, 2020), together with a reference list."));
  }
}

function srSay(text) {
  $("#sr-status").textContent = text;
}

function scheduleRender() {
  if (S.raf) return;
  S.raf = requestAnimationFrame(() => {
    S.raf = 0;
    updateClaimStates();
    updateSourceChips();
    renderSummary();
    if (S.dirtyEvidence) {
      S.dirtyEvidence = false;
      renderEvidence();
    }
  });
}

/* ------------------------------------------------------------------ evidence panel */

const orderedIds = () => layoutClaims(S.claims, S.doc).map((L) => L.claim.id);
const isVisible = (id) => S.filter === "all" || PROBLEMS.has(labelOf(claimById(id)));
const visibleClaimIds = () => orderedIds().filter(isVisible);

function selectClaim(id, { scroll = false, focus = false } = {}) {
  S.selected = id;
  S.selSource = null;
  document.body.toggleAttribute("data-sheet", !!id);
  updateClaimStates();
  renderEvidence();
  $("#evidence").scrollTop = 0;
  const span = id && (S.spans.get(id) || [])[0];
  if (span && scroll) span.scrollIntoView({ block: "center", behavior: reduceMotion() ? "auto" : "smooth" });
  if (span && focus) span.focus({ preventScroll: true });
}

function moveSelection(dir) {
  const order = orderedIds();
  const ids = order.filter(isVisible);
  if (!ids.length) return;
  const at = ids.indexOf(S.selected);
  let next;
  if (at >= 0) next = ids[Math.min(ids.length - 1, Math.max(0, at + dir))];
  else if (!S.selected) next = dir > 0 ? ids[0] : ids[ids.length - 1];
  else {
    // the selected claim is hidden by the filter: go to the nearest visible one in that direction
    const pos = order.indexOf(S.selected);
    next = dir > 0 ? ids.find((i) => order.indexOf(i) > pos) : [...ids].reverse().find((i) => order.indexOf(i) < pos);
    next = next ?? ids[dir > 0 ? ids.length - 1 : 0];
  }
  selectClaim(next, { scroll: true, focus: true });
}

function jumpToFirstProblem() {
  const first = orderedIds().find((id) => PROBLEMS.has(labelOf(claimById(id))));
  if (first) selectClaim(first, { scroll: true, focus: true });
  else toast(S.running ? "No problems found so far." : "No problems found: every claim is supported.");
}

function badge(label, big = false) {
  const meta = LABELS[label];
  return h("span", { class: "badge" + (big ? " big" : ""), dataset: { label } }, icon(meta.icon, big ? 16 : 13), meta.badge);
}

function section(title, ...kids) {
  return h("section", { class: "ev-section" }, h("h3", { class: "ev-label", text: title }), ...kids);
}

function renderEvidence() {
  const body = $("#evidence-body");
  body.replaceChildren();
  const claim = S.selected && claimById(S.selected);
  const ids = visibleClaimIds();
  const at = claim ? ids.indexOf(claim.id) : -1;
  $("#ev-position").textContent = at >= 0 ? `${at + 1} of ${ids.length}` : "";
  $("#ev-prev").disabled = $("#ev-next").disabled = !S.claims.length;

  if (!claim && S.report && !S.claims.length) {
    body.append(
      h("div", { class: "ev-empty" },
        h("div", { class: "ev-empty-art" }, icon("receipt", 28)),
        h("h3", { text: "Nothing to check" }),
        h("p", { text: "Receipts did not find a sentence that cites a source. It recognises:" }),
        h("ul", { class: "formats" },
          h("li", null, "numeric markers like ", h("code", { text: "[1]" }), " with a numbered reference list"),
          h("li", null, "footnotes like ", h("code", { text: "[^1]" })),
          h("li", null, "inline links like ", h("code", { text: "[text](https://…)" })),
          h("li", null, "author and year, like ", h("code", { text: "(Author, 2020)" }), ", with a reference list"),
        ),
        h("button", { type: "button", class: "btn secondary", onclick: newCheck, text: "New check" }),
      ),
    );
    return;
  }

  if (!claim) {
    body.append(
      h("div", { class: "ev-empty" },
        h("div", { class: "ev-empty-art" }, icon("receipt", 28)),
        h("h3", { text: S.claims.length ? "Select a highlighted sentence" : "Claims appear as they are found" }),
        h("p", { text: S.claims.length
          ? "Each highlighted sentence cites a source. Choose one to see the quote the source gives for it, and why Receipts reached its verdict."
          : "Receipts first reads the report and picks out the sentences that cite a source." }),
        S.claims.length ? h("button", { type: "button", class: "btn secondary", onclick: jumpToFirstProblem, text: "Jump to first problem" }) : null,
      ),
    );
    return;
  }

  const vmap = S.verdicts.get(claim.id);
  const label = labelOf(claim);
  const srcIds = claim.source_ids.filter((id) => sourceById(id));
  if (!S.selSource || !srcIds.includes(S.selSource)) {
    const best = primaryVerdict(vmap);
    S.selSource = best ? best.source_id : srcIds[0] || null;
  }
  const verdict = vmap && S.selSource ? vmap.get(S.selSource) : null;
  const src = S.selSource ? sourceById(S.selSource) : null;
  const fetched = S.selSource ? S.fetched.get(S.selSource) : null;

  body.append(h("div", { class: "ev-badge-row" }, badge(verdict && srcIds.length > 1 ? verdict.label : label, true),
    srcIds.length > 1 && verdict && verdict.label !== label ? h("span", { class: "ev-note", text: `Claim overall: ${LABELS[label].badge}` }) : null));
  body.append(section("Claim", h("p", { class: "ev-claim", text: claim.claim })));

  if (srcIds.length > 1) {
    const tabs = h("div", { class: "src-tabs", role: "tablist", "aria-label": "Cited sources" });
    for (const sid of srcIds) {
      const s = sourceById(sid);
      const v = vmap && vmap.get(sid);
      const tab = h("button", {
        type: "button", class: "src-tab", role: "tab", "aria-selected": String(sid === S.selSource), dataset: { label: v ? v.label : "checking" },
        onclick: () => {
          S.selSource = sid;
          renderEvidence();
        },
      }, icon(LABELS[v ? v.label : "checking"].icon, 12), `Source ${s.marker || sid}`);
      tabs.append(tab);
    }
    body.append(tabs);
  }

  if (src) {
    const url = safeUrl((fetched && fetched.url) || src.url);
    const st = sourceStatus(fetched, S.running);
    const title = sourceTitle(src, fetched);
    const sourceBlock = h("div", { class: "ev-source" },
      h("p", { class: "src-title", text: title }),
      h("p", { class: "src-meta" },
        url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, domainOf(url) || "Open source", icon("external", 12)) : h("span", { class: "muted", text: "No link given" }),
        h("span", { class: "src-chip", dataset: { key: st.key } }, icon(st.icon, 12), st.text)),
      fetched && fetched.note ? h("p", { class: "src-note", text: fetched.note[0].toUpperCase() + fetched.note.slice(1) + "." }) : null,
    );
    body.append(section("Source", sourceBlock));
  }

  if (!verdict) {
    body.append(section("Evidence", S.error
      ? h("p", { class: "muted", text: "This claim was not checked because the run stopped." })
      : h("div", { class: "pending" }, h("span", { class: "shimmer-line" }), h("span", { class: "shimmer-line short" }), h("p", { class: "muted", text: "Waiting for the model's verdict…" }))));
    return;
  }

  // Evidence
  const ev = [];
  if (verdict.quote) {
    ev.push(h("blockquote", { class: "ev-quote", dataset: { label: verdict.label } }, h("p", { text: verdict.quote })));
    ev.push(verdict.quote_found
      ? h("p", { class: "quote-check ok" }, icon("check", 13), "Quote found in source")
      : h("p", { class: "quote-check warn" }, icon("question", 13), "Quote not found verbatim"));
  } else if (verdict.tier === "none") {
    ev.push(h("p", { class: "muted", text: "There is no quote to show: the source could not be read." }));
  } else {
    ev.push(h("p", { class: "muted", text: "No passage of the source addresses this claim." }));
  }
  body.append(section("Evidence", ...ev));

  if (verdict.rationale) body.append(section("Why", h("p", { class: "ev-why", text: verdict.rationale })));
  body.append(h("p", { class: "checked-by" }, h("span", { class: "ev-label inline", text: "Checked by" }), " ", checkedBy(verdict)));

  if (verdict.passages && verdict.passages.length) {
    const det = h("details", { class: "passages" }, h("summary", { text: `Passages considered (${verdict.passages.length})` }));
    verdict.passages.forEach((p, i) => {
      const has = verdict.quote && p.text.includes(verdict.quote);
      det.append(h("div", { class: "passage" + (has ? " has-quote" : ""), dataset: { label: verdict.label } },
        h("span", { class: "passage-n", text: `Passage ${i + 1}${has ? " · contains the quote" : ""}` }), h("p", { text: p.text })));
    });
    body.append(det);
  }
}

/* ------------------------------------------------------------------ running an audit */

function applyEvent(e) {
  switch (e.type) {
    case "stage":
      S.stage = e.stage;
      srSay({ extract: "Reading claims.", fetch: `Found ${plural(S.claims.length, "claim")}. Finding sources.`, judge: "Checking claims.", done: "" }[e.stage] || "");
      scheduleRender();
      break;
    case "notice":
      S.notices.push(e.message);
      renderBanners();
      break;
    case "claims":
      S.claims = e.claims;
      setSources(e.sources);
      renderDocument();
      renderSummary();
      renderEvidence();
      break;
    case "source":
      S.fetched.set(e.fetched.source_id, e.fetched);
      scheduleRender();
      break;
    case "verdict": {
      const v = e.verdict;
      if (!S.verdicts.has(v.claim_id)) S.verdicts.set(v.claim_id, new Map());
      S.verdicts.get(v.claim_id).set(v.source_id, v);
      if (S.selected === v.claim_id) S.dirtyEvidence = true;
      scheduleRender();
      break;
    }
    case "done":
      finish(e.report);
      break;
    case "error":
      fail(e.message);
      break;
  }
}

function loadFromReport(report) {
  S.doc = report.document;
  S.claims = report.claims;
  setSources(report.sources);
  S.fetched = new Map(report.fetched.map((f) => [f.source_id, f]));
  S.verdicts = new Map();
  for (const v of report.verdicts) {
    if (!S.verdicts.has(v.claim_id)) S.verdicts.set(v.claim_id, new Map());
    S.verdicts.get(v.claim_id).set(v.source_id, v);
  }
  S.report = report;
  S.mode = report.mode;
}

function finish(report) {
  clearInterval(S.timer);
  S.running = false;
  S.stage = "done";
  loadFromReport(report);
  if (S.renderedClaims !== S.claims.length) renderDocument();
  renderBanners();
  renderSummary();
  updateClaimStates();
  updateSourceChips();
  renderEvidence();
  const counts = countLabels(S.claims.map(labelOf));
  const problems = ORDER.filter((l) => PROBLEMS.has(l)).reduce((a, l) => a + counts[l], 0);
  srSay(`Done. ${plural(S.claims.length, "claim")} checked, ${counts.supported} supported, ${problems} with problems.`);
}

function fail(message) {
  clearInterval(S.timer);
  S.running = false;
  if (!S.claims.length) {
    // nothing to show yet: go back to the editor, keeping the text
    showScreen("input");
    updateInputUi();
    showInputError(message);
    return;
  }
  S.error = message;
  renderBanners();
  renderSummary();
  updateClaimStates();
  updateSourceChips();
  srSay(`The check stopped: ${message}`);
}

async function readEvents(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let cut;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data:")) continue;
        try {
          onEvent(JSON.parse(line.slice(5).trim()));
        } catch (err) {
          console.warn("bad event", err);
        }
      }
    }
  }
}

async function startAudit(doc, mode) {
  resetResult();
  S.doc = doc;
  S.mode = mode;
  S.running = true;
  S.stage = "extract";
  S.startedAt = performance.now();
  history.replaceState(null, "", location.pathname);
  showScreen("result");
  $("#replay-note").hidden = true;
  $("#fact-time").textContent = "0.0 s";
  renderBanners();
  renderSummary();
  renderEvidence();
  srSay("Reading claims.");
  S.timer = setInterval(() => {
    $("#fact-time").textContent = fmtTime((performance.now() - S.startedAt) / 1000);
  }, 100);
  const ctl = new AbortController();
  S.abort = ctl;
  try {
    const res = await fetch("/api/audit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document: doc, mode }),
      signal: ctl.signal,
    });
    if (!res.ok) {
      let msg = `The server answered with an error (${res.status}).`;
      try {
        msg = (await res.json()).error || msg;
      } catch { /* keep the generic message */ }
      if (res.status === 503) {
        S.config.live = false;
        S.config.reason = "budget";
      }
      S.running = false;
      fail(msg);
      return;
    }
    await readEvents(res, applyEvent);
    if (S.running) fail("The connection closed before the check finished.");
  } catch (err) {
    if (ctl.signal.aborted) return;
    fail("Could not reach the server. Check your connection and try again.");
  }
}

/* ------------------------------------------------------------------ examples, replay, downloads */

async function openExample(id) {
  let data;
  try {
    const res = await fetch(`/api/examples/${encodeURIComponent(id)}`);
    if (!res.ok) throw new Error(String(res.status));
    data = await res.json();
  } catch {
    toast("Could not load that example.");
    return;
  }
  if (!data.audit) {
    $("#doc-input").value = data.document;
    updateInputUi();
    toast("This example has no recorded run. It is loaded into the editor.");
    return;
  }
  resetResult();
  S.replayId = id;
  loadFromReport(data.audit);
  history.replaceState(null, "", `#example=${encodeURIComponent(id)}`);
  showScreen("result");
  $("#replay-note").hidden = false;
  $("#rerun-btn").disabled = !S.config.live;
  $("#rerun-btn").title = S.config.live ? "" : "Live checks are not available right now";
  renderDocument();
  renderBanners();
  renderSummary();
  renderEvidence();
  srSay(`Recorded run loaded: ${plural(S.claims.length, "claim")}.`);
}

function save(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function downloadMarkdown() {
  if (!S.report) return;
  try {
    const res = await fetch("/api/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(S.report) });
    if (!res.ok) throw new Error(String(res.status));
    save(await res.blob(), `receipts-${slugOf(S.doc)}.md`);
  } catch {
    toast("Could not build the report. Try again.");
  }
}

function downloadJson() {
  if (!S.report) return;
  save(new Blob([JSON.stringify(S.report, null, 2)], { type: "application/json" }), `receipts-${slugOf(S.doc)}.json`);
}

let toastTimer = 0;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3600);
}

function newCheck() {
  resetResult();
  history.replaceState(null, "", location.pathname);
  S.inputError = "";
  S.emptyHint = false;
  showScreen("input");
  updateInputUi();
  $("#main").focus({ preventScroll: true });
}

/* ------------------------------------------------------------------ wiring */

function wire() {
  const ta = $("#doc-input");
  ta.addEventListener("input", () => {
    S.inputError = "";
    S.emptyHint = false;
    updateInputUi();
  });
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#input-form").requestSubmit();
  });
  $("#mode").addEventListener("change", updateInputUi);
  $("#input-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if ($("#check-btn").getAttribute("aria-disabled") === "true") {
      if (S.config.live && !ta.value.trim()) {
        S.emptyHint = true;
        updateInputUi();
        ta.focus();
      }
      return;
    }
    startAudit(ta.value, $("#mode").value);
  });

  $("#brand").addEventListener("click", (e) => {
    e.preventDefault();
    newCheck();
  });
  $("#new-check").addEventListener("click", newCheck);
  $("#dl-md").addEventListener("click", downloadMarkdown);
  $("#dl-json").addEventListener("click", downloadJson);
  $("#rerun-btn").addEventListener("click", () => {
    $("#doc-input").value = S.doc;
    startAudit(S.doc, $("#mode").value || "ultra");
  });

  const doc = $("#doc");
  doc.addEventListener("click", (e) => {
    const claim = e.target.closest(".claim");
    if (!claim) return;
    selectClaim(claim.dataset.claim === S.selected && isNarrow() ? null : claim.dataset.claim);
  });
  doc.addEventListener("keydown", (e) => {
    const claim = e.target.closest && e.target.closest(".claim");
    if (claim && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      selectClaim(claim.dataset.claim);
    }
  });

  $$(".chip[data-filter]").forEach((chip) =>
    chip.addEventListener("click", () => {
      S.filter = chip.dataset.filter;
      $$(".chip[data-filter]").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      updateClaimStates();
      renderEvidence();
    }),
  );
  $("#ev-prev").addEventListener("click", () => moveSelection(-1));
  $("#ev-next").addEventListener("click", () => moveSelection(1));
  $("#ev-close").addEventListener("click", () => selectClaim(null));

  document.addEventListener("keydown", (e) => {
    if (document.body.dataset.screen !== "result" || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (t.closest("input, textarea, select, summary") || t.isContentEditable)) return;
    if (e.key === "Escape" && S.selected) {
      const span = (S.spans.get(S.selected) || [])[0];
      selectClaim(null);
      if (span && document.activeElement === span) span.blur();
      return;
    }
    const onClaim = !!S.selected || (t && t.classList && t.classList.contains("claim"));
    if (e.key === "j" || (e.key === "ArrowDown" && onClaim)) {
      e.preventDefault();
      moveSelection(1);
    } else if (e.key === "k" || (e.key === "ArrowUp" && onClaim)) {
      e.preventDefault();
      moveSelection(-1);
    }
  });
}

async function boot() {
  try {
    const res = await fetch("/api/config");
    if (res.ok) S.config = { ...S.config, ...(await res.json()) };
  } catch { /* the page still works for pasted text; the check will report the problem */ }
  renderExamples();
  wire();
  updateInputUi();
  renderSummary();
  const m = /^#example=([\w-]+)$/.exec(location.hash);
  if (m) await openExample(decodeURIComponent(m[1]));
}

if (typeof document !== "undefined") boot();
