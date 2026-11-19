"use strict";

// Parco review platform: renders the document with its edits and threads, and sends the
// user's decisions to the local API. Every write goes through the server (and core rules).

const POLL_MS = 1500;
const CARD_GAP = 8;
const RANGE_KINDS = [
  ["del", "mk-del"],
  ["ins", "mk-ins"],
  ["acc", "mk-acc"],
  ["thread", "mk-thread"],
];

const ui = {
  data: null,
  doc: null,
  revision: null,
  activeId: null,
  drafts: new Map(),
  refreshPending: false,
  frozenModal: false,
  order: [],
  openForms: new Set(),
  inflight: null,
  dirty: false,
  seen: null,
  expanded: new Map(),
  pendingOnly: loadSetting("pendingOnly", false),
  tab: loadSetting("tab", "document"),
};

// View preferences only: the page works the same when storage is unavailable.
function loadSetting(key, fallback) {
  try {
    const value = localStorage.getItem(`parco.${key}`);
    return value === null ? fallback : JSON.parse(value);
  } catch {
    return fallback;
  }
}

function saveSetting(key, value) {
  try {
    localStorage.setItem(`parco.${key}`, JSON.stringify(value));
  } catch {
    // storage blocked: the setting lasts for this page only
  }
}

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
  return payload;
}

function toast(message, kind = "error") {
  const node = el("div", { class: `toast ${kind}` }, message);
  document.getElementById("toasts").append(node);
  setTimeout(() => node.remove(), kind === "error" ? 7000 : 3500);
}

async function act(method, path, body) {
  try {
    const result = await api(method, path, body);
    await refresh(true);
    return result;
  } catch (error) {
    toast(error.message);
    return null;
  }
}

function isReviewOpen() {
  return ui.data && ui.data.phase !== "idle" && ui.data.frozen.length === 0;
}

function composing() {
  const active = document.activeElement;
  return Boolean(active && active.tagName === "TEXTAREA" && active.dataset.draft);
}

// ---------------------------------------------------------------- refresh loop

// Refreshes never overlap: a request arriving during one marks the view dirty and runs once
// the current one ends. A refresh is postponed while the user types in a compose box.
function refresh(force = false) {
  if (!force && composing()) {
    ui.refreshPending = true;
    return Promise.resolve();
  }
  if (ui.inflight) {
    ui.dirty = true;
    return ui.inflight;
  }
  ui.refreshPending = false;
  ui.inflight = doRefresh().finally(() => {
    ui.inflight = null;
    if (ui.dirty) {
      ui.dirty = false;
      refresh(true);
    }
  });
  return ui.inflight;
}

async function doRefresh() {
  const [revision, data, doc] = await Promise.all([
    api("GET", "/api/revision"), api("GET", "/api/state"), api("GET", "/api/document"),
  ]);
  const scroll = window.scrollY;
  const focus = captureFocus();
  announceNewItems(data);
  ui.data = data;
  ui.doc = doc;
  renderHeader();
  renderBanner();
  await renderDocument();
  renderCards();
  renderGlobal();
  window.scrollTo(0, scroll);
  restoreFocus(focus);
  keepActiveInView();
  ui.revision = revision.revision;
}

async function poll() {
  try {
    const { revision } = await api("GET", "/api/revision");
    if (revision !== ui.revision || ui.refreshPending) await refresh();
  } catch (error) {
    console.warn("poll failed", error);
  }
}

function captureFocus() {
  const active = document.activeElement;
  if (!active || active.tagName !== "TEXTAREA" || !active.dataset.draft) return null;
  return { key: active.dataset.draft, start: active.selectionStart, end: active.selectionEnd };
}

function restoreFocus(focus) {
  if (!focus) return;
  const textarea = document.querySelector(`textarea[data-draft="${focus.key}"]`);
  if (!textarea) return;
  textarea.focus({ preventScroll: true });
  textarea.setSelectionRange(focus.start, focus.end);
}

// Tell the user when the AI added edits or messages since the last view.
function announceNewItems(data) {
  const seen = new Set([...data.edits.map((e) => e.id), ...data.threads.flatMap((t) => t.messages.map((m, i) => `${t.id}:${i}`))]);
  if (ui.seen) {
    const fresh = data.edits.filter((e) => !ui.seen.has(e.id)).length
      + data.threads.flatMap((t) => t.messages.map((m, i) => ({ m, key: `${t.id}:${i}` })))
        .filter(({ m, key }) => m.author === "ai" && !ui.seen.has(key)).length;
    if (fresh > 0) toast(`${fresh} new edit(s)/comment(s) from the AI`, "info");
  }
  ui.seen = seen;
}

function keepActiveInView() {
  if (!ui.activeId) return;
  const card = document.querySelector(`.card[data-card="${ui.activeId}"]`);
  if (!card) return;
  const rect = card.getBoundingClientRect();
  if (rect.bottom < 0 || rect.top > window.innerHeight) card.scrollIntoView({ block: "nearest" });
}

// ---------------------------------------------------------------- header and banner

function renderHeader() {
  const data = ui.data;
  document.getElementById("project-name").textContent = data.project;
  const mode = data.mode ? ` · mode ${data.mode}` : "";
  document.getElementById("status-line").textContent = `version ${data.version} · ${data.phase}${mode}`;
  const counts = document.getElementById("counts");
  counts.replaceChildren(
    el("b", {}, data.counts.pending), " pending · ",
    el("b", {}, data.counts.accepted), " accepted · ",
    el("b", {}, data.counts.rejected), " rejected · ",
    el("b", {}, data.open_threads), " open threads",
  );
  document.getElementById("close-button").hidden = data.phase === "idle";
  document.getElementById("global-form").hidden = !isReviewOpen();
}

function renderBanner() {
  const banner = document.getElementById("banner");
  banner.className = "";
  const { data, doc } = ui;
  if (data.frozen.length > 0) {
    showFrozenModal(data.frozen);
    banner.hidden = true;
    return;
  }
  if (ui.frozenModal) hideModal();
  if (doc.error) {
    banner.className = "error";
    banner.textContent = `The document could not be rendered: ${doc.error}`;
    banner.hidden = false;
  } else if (data.phase === "idle") {
    banner.replaceChildren(
      el("b", {}, "No review in progress. "),
      "Ask your AI agent to review the document with parco, or run ",
      el("code", {}, "parco open --mode <reference|comment|proofread|generative>"),
      ".",
    );
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
}

// ---------------------------------------------------------------- document

async function renderDocument() {
  const docEl = document.getElementById("doc");
  docEl.innerHTML = ui.doc.html;
  applyRangeMarkers(docEl);
  applyPointMarkers(docEl);
  for (const img of docEl.querySelectorAll("img")) img.addEventListener("load", relayout, { once: true });
  if (window.MathJax && MathJax.startup) {
    await MathJax.startup.promise;
    MathJax.typesetClear([docEl]);
    // Equation numbers and \label names are global to MathJax: reset them before re-typesetting.
    MathJax.texReset();
    await MathJax.typesetPromise([docEl]).catch((error) => console.warn("MathJax", error));
  }
}

function applyRangeMarkers(docEl) {
  const threadStatus = new Map(ui.data.threads.map((t) => [t.id, t.status]));
  for (const [kind, className] of RANGE_KINDS) {
    const open = new Map();
    const pairs = [];
    for (const marker of docEl.querySelectorAll(`.pm[data-kind="${kind}-start"], .pm[data-kind="${kind}-end"]`)) {
      const id = marker.dataset.id;
      if (marker.dataset.kind.endsWith("-start")) {
        if (open.has(id)) asPoint(open.get(id));
        open.set(id, marker);
      } else if (open.has(id)) {
        pairs.push([open.get(id), marker]);
        open.delete(id);
      } else {
        asPoint(marker);
      }
    }
    for (const lone of open.values()) asPoint(lone);
    for (const [start, end] of pairs) {
      const id = start.dataset.id;
      const extra = kind === "thread" && threadStatus.get(id) === "resolved" ? " resolved" : "";
      wrapBetween(docEl, start, end, className + extra, id);
    }
  }
}

// pandoc may drop one marker of a pair (e.g. in a moved float): show the survivor as a point.
function asPoint(marker) {
  marker.dataset.kind = "point";
}

// Wrap every text node (and image) between two marker elements, so a range crossing element
// boundaries (emphasis, paragraphs, list items) is still highlighted piece by piece.
function wrapBetween(root, start, end, className, id) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  walker.currentNode = start;
  const targets = [];
  let node = walker.nextNode();
  while (node && node !== end) {
    if (node.nodeType === Node.ELEMENT_NODE && (node.tagName === "IMG" || node.classList.contains("math") || node.classList.contains("parco-fill"))) {
      targets.push(node);
      node = nextOutside(walker, node, root);
      continue;
    }
    if (node.nodeType === Node.TEXT_NODE && node.textContent.length > 0) targets.push(node);
    node = walker.nextNode();
  }
  for (const target of targets) {
    if (target.nodeType === Node.ELEMENT_NODE) {
      target.classList.add(...className.split(" "));
      target.dataset.item = id;
    } else if (target.textContent.trim() !== "" || target.parentElement.closest("p, li, td, figcaption")) {
      const span = el("span", { class: className, "data-item": id });
      target.replaceWith(span);
      span.append(target);
    }
  }
}

function nextOutside(walker, node, root) {
  let next = walker.nextSibling();
  let current = node;
  while (!next && current.parentNode && current.parentNode !== root) {
    walker.currentNode = current.parentNode;
    current = current.parentNode;
    next = walker.nextSibling();
  }
  return next;
}

function applyPointMarkers(docEl) {
  const editStatus = new Map(ui.data.edits.map((e) => [e.id, e.status]));
  for (const point of docEl.querySelectorAll('.pm[data-kind="point"]')) {
    point.dataset.item = point.dataset.id;
    if (editStatus.get(point.dataset.id) === "rejected") point.classList.add("rejected");
    point.title = point.dataset.id;
  }
}

function anchorOf(id) {
  return document.querySelector(`#doc [data-item="${id}"]`);
}

// ---------------------------------------------------------------- cards

function renderCards() {
  const column = document.getElementById("cards");
  column.replaceChildren();
  ui.order = [];
  if (!ui.data || ui.data.phase === "idle") return;
  const items = [
    ...ui.data.edits.filter((edit) => isShown(editNeedsAttention(edit))).map((edit) => ({ id: edit.id, card: editCard(edit) })),
    ...ui.data.threads.filter((t) => !t.global && !t.edit && isShown(t.status === "open"))
      .map((thread) => ({ id: thread.id, card: threadCard(thread) })),
  ];
  for (const item of items) column.append(item.card);
  layoutCards(column, items);
  highlightActive();
}

// Cards follow the document order given by the server (hidden items included, at their file
// position); each one sits as close to its anchor as the cards above it allow.
function layoutCards(column, items) {
  const columnTop = column.getBoundingClientRect().top;
  const placed = items.map((item, index) => {
    const anchor = anchorOf(item.id);
    const top = anchor ? anchor.getBoundingClientRect().top - columnTop : Number.NaN;
    const position = ui.doc.items[item.id]?.position ?? Number.MAX_SAFE_INTEGER;
    return { ...item, top, index, position };
  });
  placed.sort((a, b) => a.position - b.position || a.index - b.index);
  let cursor = 0;
  for (const item of placed) {
    const top = Number.isFinite(item.top) ? Math.max(item.top, cursor) : cursor;
    item.card.style.top = `${top}px`;
    cursor = top + item.card.offsetHeight + CARD_GAP;
  }
  column.style.minHeight = `${cursor}px`;
  ui.order = placed.map((item) => item.id);
}

function editNeedsAttention(edit) {
  return edit.status === "pending" || ui.data.threads.some((t) => t.edit === edit.id && t.status === "open");
}

function isShown(needsAttention) {
  return needsAttention || !ui.pendingOnly;
}

// Settled cards (decided edits, resolved threads) are folded unless the user unfolded them.
function isCollapsed(id, settled) {
  return ui.expanded.has(id) ? !ui.expanded.get(id) : settled;
}

function setExpanded(id, expanded) {
  ui.expanded.set(id, expanded);
  renderCards();
}

// A card is a head (always visible), a one-line preview (shown when folded) and a body.
function cardShell(id, classes, headParts, preview, settled) {
  const collapsed = isCollapsed(id, settled);
  const toggle = el("button", {
    class: "card-toggle",
    type: "button",
    title: collapsed ? "Expand" : "Collapse",
    onclick: () => setExpanded(id, collapsed),
  }, collapsed ? "\u25B8" : "\u25BE");
  const body = el("div", { class: "card-body" });
  const card = el("section", {
    class: `card ${classes}${collapsed ? " collapsed" : ""}`,
    "data-card": id,
    onclick: (event) => {
      if (event.target.closest("button, textarea, form")) return;
      setActive(id, true);
      if (collapsed) setExpanded(id, true);
    },
  }, el("div", { class: "card-head" }, headParts, toggle), el("div", { class: "preview" }, preview), body);
  return { card, body };
}

function diffView(hunks) {
  return el("div", { class: "diff" }, hunks.flatMap(([op, oldPart, newPart]) => (op === "equal"
    ? [oldPart]
    : [oldPart ? el("del", {}, oldPart) : null, newPart ? el("ins", {}, newPart) : null])));
}

function editCard(edit) {
  const info = ui.doc.items[edit.id];
  const { card, body } = cardShell(edit.id, `edit ${edit.status}`, [
    el("span", { class: "badge" }, edit.id),
    el("span", {}, edit.kind),
    el("span", {}, "·"),
    el("span", {}, edit.status),
    edit.thread ? el("span", {}, `· answers ${edit.thread}`) : null,
  ], edit.reason || edit.replace, edit.status !== "pending");
  if (edit.reason) body.append(el("div", { class: "reason" }, edit.reason));
  body.append(diffView(edit.diff));
  body.append(...placementNote(info, edit.status !== "rejected"));
  if (edit.bibtex) body.append(el("div", { class: "diff" }, edit.bibtex));
  if (isReviewOpen()) body.append(editActions(edit));
  for (const thread of ui.data.threads.filter((t) => t.edit === edit.id)) body.append(threadBody(thread));
  return card;
}

// Deciding an edit drops any manual unfolding, so the card folds as soon as it is settled.
function decideEdit(id, action) {
  ui.expanded.delete(id);
  return act("POST", `/api/edits/${id}/${action}`);
}

function editActions(edit) {
  const actions = el("div", { class: "actions" });
  const button = (label, action, cls) => el("button", {
    class: `button ${cls}`,
    type: "button",
    onclick: () => decideEdit(edit.id, action),
  }, label);
  if (edit.status !== "accepted") actions.append(button("Accept", "accept", "accept"));
  if (edit.status !== "rejected") actions.append(button("Reject", "reject", "reject"));
  if (edit.status !== "pending") actions.append(button("Reset", "reset", ""));
  const hasDiscussion = ui.data.threads.some((t) => t.edit === edit.id);
  if (!hasDiscussion) {
    actions.append(collapsibleForm(`discuss-${edit.id}`, "Discuss", "Discuss this edit", (body) =>
      act("POST", `/api/edits/${edit.id}/discuss`, { body })));
  }
  return actions;
}

function threadCard(thread) {
  const last = thread.messages[thread.messages.length - 1];
  const { card, body } = cardShell(thread.id, `thread ${thread.status}`, [
    el("span", { class: "badge" }, thread.id),
    el("span", {}, "comment"),
    el("span", {}, "·"),
    el("span", {}, thread.status),
  ], last ? last.body : thread.anchor, thread.status === "resolved");
  if (thread.anchor) body.append(el("div", { class: "quote" }, thread.anchor));
  body.append(...placementNote(ui.doc.items[thread.id], true));
  body.append(threadBody(thread));
  return card;
}

function placementNote(info, showPoint) {
  if (!info || info.display === "hidden") {
    return [el("div", { class: "note" }, "Not shown in the document (its text could not be located in the rendering): the source is above.")];
  }
  if (info.display === "point" && showPoint) {
    return [el("div", { class: "note" }, "Shown as a marker in the document (math, command argument or document structure): the source is above.")];
  }
  return [];
}

function threadBody(thread) {
  const body = el("div", { class: "thread-body" });
  const messages = el("div", { class: "messages" });
  for (const message of thread.messages) {
    messages.append(el("div", { class: `message ${message.author}` }, el("span", { class: "author" }, message.author === "ai" ? "AI" : "You"), message.body));
  }
  body.append(messages);
  if (!isReviewOpen()) return body;
  if (thread.status === "open") {
    body.append(collapsibleForm(`reply-${thread.id}`, "Reply", "Reply", (text) => act("POST", `/api/threads/${thread.id}/reply`, { body: text })));
  }
  const toggle = thread.status === "open" ? "resolve" : "reopen";
  body.append(el("div", { class: "actions" }, el("button", {
    class: "button",
    type: "button",
    onclick: () => {
      ui.expanded.delete(thread.id);
      act("POST", `/api/threads/${thread.id}/${toggle}`);
    },
  }, toggle === "resolve" ? "Resolve" : "Reopen")));
  return body;
}

// A compose form folded behind a link; it stays open across refreshes (and opens by itself
// when a draft is pending) so that the cards do not grow with empty boxes.
function collapsibleForm(draftKey, label, placeholder, submit) {
  const wrapper = el("div", { class: "collapsible" });
  const collapse = () => {
    ui.openForms.delete(draftKey);
    wrapper.replaceChildren(link);
    relayout();
  };
  const expand = (focus) => {
    ui.openForms.add(draftKey);
    wrapper.replaceChildren(composeForm(draftKey, placeholder, label, submit, collapse));
    if (focus) {
      relayout();
      wrapper.querySelector("textarea").focus();
    }
  };
  const link = el("button", { class: "link", type: "button", onclick: () => expand(true) }, label);
  if (ui.openForms.has(draftKey) || ui.drafts.get(draftKey)) expand(false);
  else wrapper.append(link);
  return wrapper;
}

function composeForm(draftKey, placeholder, label, submit, onClose) {
  const textarea = el("textarea", { rows: 2, placeholder, "data-draft": draftKey });
  textarea.value = ui.drafts.get(draftKey) || "";
  textarea.addEventListener("input", () => ui.drafts.set(draftKey, textarea.value));
  textarea.addEventListener("blur", () => { if (ui.refreshPending) setTimeout(poll, 0); });
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && onClose) onClose();
  });
  const cancel = onClose ? el("button", { class: "button", type: "button", onclick: onClose }, "Cancel") : null;
  const form = el("form", { class: "compose" }, textarea,
    el("div", { class: "actions" }, cancel, el("button", { class: "button primary", type: "submit" }, label)));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!textarea.value.trim()) return;
    const result = await submit(textarea.value);
    if (result) {
      ui.drafts.delete(draftKey);
      ui.openForms.delete(draftKey);
    }
  });
  return form;
}

function relayout() {
  const column = document.getElementById("cards");
  const items = [...column.querySelectorAll(".card")].map((card) => ({ id: card.dataset.card, card }));
  layoutCards(column, items);
}

// ---------------------------------------------------------------- global column

function renderGlobal() {
  const container = document.getElementById("global-threads");
  container.replaceChildren();
  const all = ui.data.phase === "idle" ? [] : ui.data.threads.filter((t) => t.global);
  const globals = all.filter((t) => isShown(t.status === "open"));
  const openCount = all.filter((t) => t.status === "open").length;
  document.getElementById("global-count").textContent = openCount > 0 ? String(openCount) : "";
  if (globals.length === 0) container.append(el("p", { class: "note" }, all.length === 0 ? "No global comment." : "No open global comment."));
  for (const thread of globals) {
    const card = el("section", { class: `card thread ${thread.status}`, "data-card": thread.id },
      el("div", { class: "card-head" }, el("span", { class: "badge" }, thread.id), el("span", {}, thread.status)));
    card.append(threadBody(thread));
    container.append(card);
  }
  const textarea = document.querySelector("#global-form textarea");
  textarea.value = ui.drafts.get("global-new") || "";
}

// ---------------------------------------------------------------- activation and keyboard

function setActive(id, scrollDocument) {
  ui.activeId = id;
  highlightActive();
  const targets = document.querySelectorAll(`#doc [data-item="${id}"]`);
  if (scrollDocument && targets.length > 0) {
    targets[0].scrollIntoView({ behavior: "smooth", block: "center" });
  }
  for (const target of targets) {
    target.classList.remove("mk-flash");
    void target.offsetWidth;
    target.classList.add("mk-flash");
  }
}

function highlightActive() {
  for (const node of document.querySelectorAll(".card.active, #doc .mk-active")) node.classList.remove("active", "mk-active");
  if (!ui.activeId) return;
  document.querySelector(`.card[data-card="${ui.activeId}"]`)?.classList.add("active");
  markAnchor(ui.activeId, "mk-active", true);
}

function markAnchor(id, className, on) {
  for (const target of document.querySelectorAll(`#doc [data-item="${id}"]`)) target.classList.toggle(className, on);
}

function onCardHover(event) {
  const card = event.target.closest?.(".card[data-card]");
  if (!card || card.contains(event.relatedTarget)) return;
  markAnchor(card.dataset.card, "mk-hover", event.type === "mouseover");
}

function onKey(event) {
  if (event.key === "Escape") {
    closePopovers();
    return;
  }
  if (event.target.closest("textarea, input") || event.metaKey || event.ctrlKey || event.altKey) return;
  const index = ui.order.indexOf(ui.activeId);
  if (event.key === "j" || event.key === "k") {
    if (ui.order.length === 0) return;
    const step = event.key === "j" ? 1 : -1;
    const next = ui.order[(index + step + ui.order.length) % ui.order.length] ?? ui.order[0];
    setActive(next, true);
    document.querySelector(`.card[data-card="${next}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } else if (event.key === "c" && !document.getElementById("comment-fab").hidden) {
    event.preventDefault();
    openSelectionForm();
  } else if ((event.key === "a" || event.key === "r") && ui.activeId && ui.activeId.startsWith("e") && isReviewOpen()) {
    decideEdit(ui.activeId, event.key === "a" ? "accept" : "reject");
  } else if (event.key === "p") {
    setPendingOnly(!ui.pendingOnly);
  }
}

function onDocumentClick(event) {
  const marked = event.target.closest("[data-item]");
  if (!marked) return;
  setActive(marked.dataset.item, false);
  document.querySelector(`.card[data-card="${marked.dataset.item}"]`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closePopovers() {
  document.getElementById("comment-fab").hidden = true;
  document.getElementById("selection-form").hidden = true;
  if (!document.getElementById("modal").hidden && !ui.frozenModal) cancelModal();
}

// ---------------------------------------------------------------- selection to comment

function compactText(text) {
  return text.replace(/[\u2018\u2019]/g, "'").replace(/[\u201c\u201d]/g, '"').replace(/[\u2013\u2014]/g, "-").replace(/\s+/g, "");
}

// Index of the selection among identical texts since the preceding block marker, so that the
// server can tell repeated words apart.
function occurrenceIndex(range, beforeId, text) {
  const docEl = document.getElementById("doc");
  const marker = beforeId ? docEl.querySelector(`.pm[data-kind="block"][data-id="${beforeId}"]`) : null;
  const prefix = document.createRange();
  if (marker) prefix.setStartAfter(marker);
  else prefix.setStart(docEl, 0);
  prefix.setEnd(range.startContainer, range.startOffset);
  const needle = compactText(text);
  return needle ? compactText(prefix.toString()).split(needle).length - 1 : 0;
}

function currentSelection() {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  const docEl = document.getElementById("doc");
  if (!docEl.contains(range.commonAncestorContainer)) return null;
  const text = selection.toString();
  return text.trim() ? { range, text } : null;
}

function surroundingBlocks(range) {
  const blocks = [...document.querySelectorAll('#doc .pm[data-kind="block"]')];
  let before = null;
  let after = null;
  for (const block of blocks) {
    if (block.compareDocumentPosition(range.startContainer) & Node.DOCUMENT_POSITION_FOLLOWING) before = block.dataset.id;
    if (!after && block.compareDocumentPosition(range.endContainer) & Node.DOCUMENT_POSITION_PRECEDING) after = block.dataset.id;
  }
  return { before, after };
}

let pendingSelection = null;

function onSelectionEnd() {
  if (!document.getElementById("selection-form").hidden) return;
  const fab = document.getElementById("comment-fab");
  const found = isReviewOpen() ? currentSelection() : null;
  if (!found) {
    fab.hidden = true;
    return;
  }
  const rect = found.range.getBoundingClientRect();
  const blocks = surroundingBlocks(found.range);
  pendingSelection = {
    text: found.text,
    ...blocks,
    occurrence: occurrenceIndex(found.range, blocks.before, found.text),
    renderKey: ui.doc.render_key,
  };
  fab.style.top = `${rect.bottom + window.scrollY + 6}px`;
  fab.style.left = `${rect.left + window.scrollX}px`;
  fab.hidden = false;
}

function openSelectionForm() {
  const fab = document.getElementById("comment-fab");
  const form = document.getElementById("selection-form");
  form.style.top = fab.style.top;
  form.style.left = fab.style.left;
  document.getElementById("selection-quote").textContent = pendingSelection.text;
  fab.hidden = true;
  form.hidden = false;
  const textarea = form.querySelector("textarea");
  textarea.value = ui.drafts.get("selection-new") || "";
  textarea.focus();
}

async function submitSelectionForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const textarea = form.querySelector("textarea");
  if (!textarea.value.trim() || !pendingSelection) return;
  const result = await act("POST", "/api/threads", {
    selection: pendingSelection.text,
    before: pendingSelection.before,
    after: pendingSelection.after,
    occurrence: pendingSelection.occurrence,
    render_key: pendingSelection.renderKey,
    body: textarea.value,
  });
  if (result) {
    textarea.value = "";
    ui.drafts.delete("selection-new");
    form.hidden = true;
    setActive(result.id, false);
  }
}

// ---------------------------------------------------------------- modal and close

function showModal(content, actions, frozen = false) {
  ui.frozenModal = frozen;
  document.getElementById("modal-content").replaceChildren(...content);
  document.getElementById("modal-actions").replaceChildren(...actions);
  document.getElementById("modal").hidden = false;
}

function hideModal() {
  document.getElementById("modal").hidden = true;
  ui.frozenModal = false;
}

function cancelModal() {
  hideModal();
  if (ui.data && ui.data.frozen.length > 0) showFrozenModal(ui.data.frozen);
}

function showFrozenModal(files) {
  showModal([
    el("h2", {}, "The sources changed during the review"),
    el("p", {}, "These tracked files were modified since the version was opened:"),
    el("ul", {}, files.map((file) => el("li", {}, file))),
    el("p", {}, "The review cannot continue on modified files. Close this version (accepted edits are applied, pending ones rejected) and start a new one."),
  ], [el("button", { class: "button danger", type: "button", onclick: confirmClose }, "Close version")], true);
}

function confirmClose() {
  const { counts, version } = ui.data;
  showModal([
    el("h2", {}, `Close version ${version}?`),
    el("ul", {},
      el("li", {}, `${counts.accepted} accepted edit(s) will be written to the sources`),
      el("li", {}, `${counts.pending} pending edit(s) will be rejected`),
      el("li", {}, "all threads will be closed and a new snapshot taken")),
  ], [
    el("button", { class: "button", type: "button", onclick: cancelModal }, "Cancel"),
    el("button", { class: "button danger", type: "button", onclick: runClose }, "Close version"),
  ]);
}

async function runClose() {
  try {
    const report = await api("POST", "/api/close");
    showModal([
      el("h2", {}, `Version ${report.version} closed`),
      el("ul", {},
        el("li", {}, `Applied: ${report.applied.join(", ") || "none"}`),
        el("li", {}, `Rejected: ${report.rejected.join(", ") || "none"}`),
        report.skipped.length ? el("li", {}, `Skipped: ${report.skipped.map((s) => `${s.id} (${s.reason})`).join(", ")}`) : null,
        report.bib_added.length ? el("li", {}, `Added to ${report.bib_file}: ${report.bib_added.join(", ")}`) : null,
        report.bib_not_written.length ? el("li", {}, `Bibliography entries not written (no bib file): ${report.bib_not_written.join(", ")}`) : null,
        el("li", {}, `Snapshot: ${report.snapshot_files.length} file(s)`)),
    ], [el("button", { class: "button primary", type: "button", onclick: () => { hideModal(); refresh(true); } }, "OK")]);
  } catch (error) {
    cancelModal();
    toast(error.message);
  }
}

// ---------------------------------------------------------------- view controls

function setTab(tab) {
  ui.tab = tab;
  saveSetting("tab", tab);
  document.body.dataset.tab = tab;
  for (const button of document.querySelectorAll("#tabs .tab")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === tab));
  }
  if (tab === "document") relayout();
}

function setPendingOnly(on) {
  ui.pendingOnly = on;
  saveSetting("pendingOnly", on);
  document.getElementById("pending-only").checked = on;
  if (!ui.data) return;
  renderCards();
  renderGlobal();
}

function setAllExpanded(expanded) {
  for (const card of document.querySelectorAll("#cards .card[data-card]")) ui.expanded.set(card.dataset.card, expanded);
  renderCards();
}

// ---------------------------------------------------------------- startup

function start() {
  for (const button of document.querySelectorAll("#tabs .tab")) button.addEventListener("click", () => setTab(button.dataset.tab));
  setTab(ui.tab === "global" ? "global" : "document");
  const pendingOnly = document.getElementById("pending-only");
  pendingOnly.checked = ui.pendingOnly;
  pendingOnly.addEventListener("change", () => setPendingOnly(pendingOnly.checked));
  document.getElementById("collapse-all").addEventListener("click", () => setAllExpanded(false));
  document.getElementById("expand-all").addEventListener("click", () => setAllExpanded(true));
  document.getElementById("close-button").addEventListener("click", confirmClose);
  document.getElementById("comment-fab").addEventListener("mousedown", (event) => event.preventDefault());
  document.getElementById("comment-fab").addEventListener("click", openSelectionForm);
  const selectionForm = document.getElementById("selection-form");
  selectionForm.addEventListener("submit", submitSelectionForm);
  const selectionText = selectionForm.querySelector("textarea");
  selectionText.addEventListener("input", () => ui.drafts.set("selection-new", selectionText.value));
  selectionForm.querySelector('[data-action="cancel"]').addEventListener("click", () => { selectionForm.hidden = true; });
  const globalForm = document.getElementById("global-form");
  const globalText = globalForm.querySelector("textarea");
  globalText.addEventListener("input", () => ui.drafts.set("global-new", globalText.value));
  globalForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!globalText.value.trim()) return;
    const result = await act("POST", "/api/threads", { global: true, body: globalText.value });
    if (result) { ui.drafts.delete("global-new"); globalText.value = ""; }
  });
  const docEl = document.getElementById("doc");
  let selectionTimer = null;
  document.addEventListener("selectionchange", () => {
    clearTimeout(selectionTimer);
    selectionTimer = setTimeout(onSelectionEnd, 250);
  });
  docEl.addEventListener("click", onDocumentClick);
  document.addEventListener("keydown", onKey);
  const cards = document.getElementById("cards");
  cards.addEventListener("mouseover", onCardHover);
  cards.addEventListener("mouseout", onCardHover);
  window.addEventListener("resize", relayout);
  refresh(true).catch((error) => toast(error.message));
  setInterval(poll, POLL_MS);
}

start();
