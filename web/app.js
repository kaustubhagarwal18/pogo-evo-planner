"use strict";
/* Rare Candy Planner in the browser. This file is the editor: it reads screenshots or opens an inventory (the JSON
   the command line reads and writes), lets you change it, and shows the plan. worker.js runs the Python planner and
   screen reader; tesseract.js, loaded only when screenshots are read, reads their text. */

const BUILD = document.querySelector('meta[name="build"]').content;
const STORE = "rare-candy-planner";   // localStorage: the open inventory, its file name and the settings
const SPRITES = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/";
const DEFAULTS = { new_entries_only: false, allow_trade_evolutions: false, dex_bonus: 20, repeat_value: 0 };
const LISTS = ["pokedex", "not_caught"];
const EMPTY = { rare_candy: 0, candy: {}, items: {}, pokedex: [], not_caught: [], pokemon: [] };
// added to the dashboard page: links open in a new tab, and the page reports its height so the frame fits it
const RESIZE = "<script>new ResizeObserver(function () { parent.postMessage({ dashboardHeight: " +
  "Math.ceil(document.body.getBoundingClientRect().height) }, '*'); }).observe(document.body);<\/script>";

const $ = (id) => document.getElementById(id);
// example: the open inventory is the bundled example, shown until the player opens their own;
// scan: what the screenshots showed, until dismissed; rareUnknown: no bag screenshot gave the Rare Candy count
const state = { inv: null, source: "", example: false, options: { ...DEFAULTS }, scan: null, rareUnknown: false,
                mode: "" };
let catalog = null;          // from the planner: {species: {ID: {family, dex}}, items: {ID: name}}
let families = new Set();    // every family id in the species table
let itemIds = new Map();     // an item's id, or its name folded like an id, -> its id
let reply = null;            // the planner's last answer

/** An element with an optional class and text. */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

/** Display text for an id: DRATINI -> Dratini, RATTATA_ALOLA -> Rattata Alola. */
function title(id) {
  return String(id).replace(/_/g, " ").toLowerCase().replace(/(^| )\S/g, (m) => m.toUpperCase());
}

/** A name in the planner's id style: "Mr. Mime" -> MR_MIME, "King's Rock" -> KINGS_ROCK. */
function norm(name) {
  return String(name).trim().toUpperCase().replace(/[.'’]/g, "").replace(/[\s-]+/g, "_");
}

/** Sort ids by their display text. */
function byTitle(a, b) {
  return title(a).localeCompare(title(b));
}

/** An item as players know it: ITEM_GEN4_EVOLUTION_STONE -> Sinnoh Stone. */
function itemName(id) {
  return (catalog && catalog.items[id]) || title(id.replace(/^ITEM_/, ""));
}

/** The species id for a typed name or id, or null when the species table has no such species. */
function speciesId(name) {
  const id = norm(name);
  return catalog.species[id] ? id : null;
}

/** The item id for an id or a name players use (Sinnoh Stone, ITEM_SINNOH_STONE), or null when no evolution needs
    such an item. The planner reads inventory files the same way (gamemaster.item_id). */
function itemId(name) {
  const k = norm(name).replace(/^ITEM_/, "");
  return itemIds.get(k) || itemIds.get("ITEM_" + k) || null;
}

/** A Pokémon's picture by dex number; its initial shows until the picture loads, or if it can't. */
function sprite(id) {
  const s = el("span", "spr");
  const initial = el("span", null, title(id).charAt(0));
  s.append(initial);
  const dex = catalog && catalog.species[id] && catalog.species[id].dex;
  if (dex) {
    const img = el("img");
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.onload = () => initial.remove();
    img.onerror = () => img.remove();
    img.src = SPRITES + dex + ".png";
    s.append(img);
  }
  return s;
}

/** A whole number of 0 or more from a number field, or null for anything else. */
function wholeNumber(input) {
  const n = Number(input.value);
  return input.value !== "" && Number.isInteger(n) && n >= 0 ? n : null;
}

/** A number field for a count. */
function numberField(label, value) {
  const input = el("input");
  input.type = "number";
  input.min = "0";
  input.step = "1";
  input.inputMode = "numeric";
  input.setAttribute("aria-label", label);
  if (value != null) input.value = value;
  return input;
}

/** A small remove button. */
function removeButton(glyph, label, remove) {
  const b = el("button", null, glyph);
  b.type = "button";
  b.title = label;
  b.setAttribute("aria-label", label);
  b.addEventListener("click", () => { remove(); edited(true); });
  return b;
}

// ---- opening, checking and keeping an inventory

/** A count from a file: a whole number of 0 or more. Throws, naming the field, otherwise. */
function count(v, what) {
  const n = Number(v);
  if (v === null || v === "" || !Number.isInteger(n) || n < 0) {
    throw new Error(`${what} should be a whole number, not ${JSON.stringify(v)}`);
  }
  return n;
}

/** The inventory as the command line writes it: every field present and checked, names in id style. Fields the
    editor doesn't use are kept, so a download gives back everything that was opened. Throws for a bad file. */
function checked(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("it isn't a JSON object");
  if (!Object.keys(EMPTY).some((k) => k in data)) {
    throw new Error("it has none of an inventory's fields (rare_candy, candy, pokemon and so on)");
  }
  const table = (v, what, key) => {
    if (v == null) return {};
    if (typeof v !== "object" || Array.isArray(v)) throw new Error(`"${what}" should map names to counts`);
    return Object.fromEntries(Object.entries(v).map(([k, n]) => [key(k), count(n, `${what}: ${k}`)]));
  };
  const list = (v, what) => {
    if (v == null) return [];
    if (!Array.isArray(v)) throw new Error(`"${what}" should be a list`);
    return [...new Set(v.map(norm))];
  };
  const pokemon = data.pokemon == null ? [] : data.pokemon;
  if (!Array.isArray(pokemon)) throw new Error('"pokemon" should be a list');
  return {
    ...data,
    rare_candy: count(data.rare_candy == null ? 0 : data.rare_candy, "rare_candy"),
    candy: table(data.candy, "candy", norm),
    items: table(data.items, "items", (k) => k),
    pokedex: list(data.pokedex, "pokedex"),
    not_caught: list(data.not_caught, "not_caught"),
    pokemon: pokemon.map((p, i) => {
      if (!p || typeof p.species !== "string") throw new Error(`Pokémon number ${i + 1} has no species`);
      return { ...p, species: norm(p.species) };
    }),
  };
}

/** Keep the inventory and settings in this browser. Private browsing can refuse; then the page just forgets. */
function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify(state));
    $("saved").hidden = false;
  } catch (e) {
    $("saved").hidden = true;
  }
}

/** Bring back what save() kept, if anything. */
function restore() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE) || "null");
    if (s && s.options) state.options = { ...DEFAULTS, ...s.options };
    if (s && typeof s.mode === "string") state.mode = s.mode;
    if (s && s.inv) {
      state.inv = checked(s.inv);
      state.source = String(s.source || "");
      state.example = !!s.example;
      state.scan = s.scan && typeof s.scan === "object" ? s.scan : null;
      state.rareUnknown = !!s.rareUnknown;
    }
  } catch (e) {
    state.inv = null;   // nothing kept, storage is off, or what was kept no longer checks out
  }
}

/** Show a message above the inventory, or hide it with no text. */
function message(text) {
  $("msg").textContent = text || "";
  $("msg").hidden = !text;
}

/** Open an inventory: check it, keep it in this browser, show it and plan it. scan is what screenshots showed, when
    they were read into it; example says it's the bundled example rather than the player's own. */
function open(data, source, scan = null, example = false) {
  state.inv = checked(data);
  state.source = source;
  state.example = example;
  state.scan = scan;
  state.rareUnknown = !!scan && !scan.bag;
  reply = null;
  message("");
  save();
  render();
  plan();
}

/** Open the bundled example, which shows a new visitor how the page works until they open their own. */
async function openExample() {
  try {
    const r = await fetch("inventory_sample.json?v=" + BUILD);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    open(await r.json(), "inventory_sample.json", null, true);
  } catch (e) {
    message(`Couldn't load the example (${e.message}). You can still upload your own.`);
    render();
  }
}

/** Whether the open inventory may be replaced by what: yes when there's none or it's the example, else ask. */
function replaceOk(what) {
  return !state.inv || state.example
    || window.confirm(`Replace the open inventory with ${what}? Download it first to keep it.`);
}

/** Open an inventory file, or say in one line why it can't be opened. */
async function openFile(file) {
  if (!replaceOk(file.name)) return;
  try {
    open(JSON.parse(await file.text()), file.name);
  } catch (e) {
    message(`Couldn't open ${file.name}: ${e instanceof SyntaxError ? "it isn't valid JSON" : e.message}.`);
  }
}

/** Offer text as a file to save. */
function download(name, text, type) {
  const a = el("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

/** After an edit: keep it, redraw the editor if rows came or went, and plan again. */
function edited(redraw) {
  save();
  if (redraw) render();
  plan();
}

// ---- the editor

/** Draw everything from the state: the start card (over the example, or alone), the editor, and the settings. */
function render() {
  const inv = state.inv;
  $("start").hidden = (!!inv && !state.example) || !$("scan").hidden;
  $("start-example").hidden = !state.example;
  $("editor").hidden = !inv;
  document.querySelectorAll(".own").forEach((e) => { e.hidden = state.example; });   // nothing to save or close
  renderOptions();
  if (!inv) {
    showNoPlan();
    return;
  }
  $("name").textContent = state.example ? "Example inventory" : state.source || "Your inventory";
  $("rare").value = inv.rare_candy;
  renderScanned();
  renderCandy();
  renderPokemon();
  renderItems();
  LISTS.forEach(renderList);
  renderNeeds();
  find();
}

/** One count you can edit: picture, name (with a note when something's off), a number field and a remove button. */
function row(picture, name, note, value, set, remove) {
  const r = el("div", "row" + (note ? " unknown" : ""));
  r.dataset.k = name.toLowerCase();
  const nm = el("span", "nm", name);
  if (note) nm.append(el("small", null, note));
  const input = numberField(name, value);
  input.addEventListener("change", () => {
    const n = wholeNumber(input);
    if (n === null) {
      input.value = value;   // keep the last good count
      return;
    }
    value = n;
    set(n);
    edited(false);
  });
  r.append(picture, nm, input, removeButton("×", "Remove " + name, remove));
  return r;
}

/** What's wrong with a candy key, or "" when it's a family in the species table. */
function familyNote(id) {
  if (!catalog || families.has(id)) return "";
  const sp = catalog.species[id];
  return sp ? `Not a family: candy is counted under ${title(sp.family)}` : "Not in the species table";
}

/** The candy counts, one row per family. */
function renderCandy() {
  const box = $("candy");
  const fams = Object.keys(state.inv.candy).sort(byTitle);
  $("n-candy").textContent = fams.length === 1 ? "1 family" : `${fams.length} families`;
  box.replaceChildren(...fams.map((fam) => row(sprite(fam), title(fam), familyNote(fam), state.inv.candy[fam],
                                               (n) => { state.inv.candy[fam] = n; },
                                               () => { delete state.inv.candy[fam]; })));
  if (!fams.length) box.append(el("p", "empty", "No candy counts yet."));
}

/** A species chip: picture, name, how many when more than one, and a remove button. */
function chip(id, n, glyph, removeLabel, remove) {
  const known = !catalog || id in catalog.species;
  const c = el("span", "chip" + (known ? "" : " unknown"));
  c.dataset.k = title(id).toLowerCase();
  if (!known) c.title = "Not in the species table, so the plan skips it";
  c.append(sprite(id), el("span", null, title(id)));
  if (n > 1) c.append(el("span", "x", "×" + n));
  c.append(removeButton(glyph, removeLabel, remove));
  return c;
}

/** The Pokémon held, one chip per species with how many. */
function renderPokemon() {
  const counts = new Map();
  state.inv.pokemon.forEach((p) => counts.set(p.species, (counts.get(p.species) || 0) + 1));
  const ids = [...counts.keys()].sort(byTitle);
  const n = state.inv.pokemon.length;
  $("n-pokemon").textContent = `${n} (${ids.length} species)`;
  $("pokemon").replaceChildren(...ids.map((id) => chip(id, counts.get(id), "−", "Remove one " + title(id), () => {
    state.inv.pokemon.splice(state.inv.pokemon.map((p) => p.species).lastIndexOf(id), 1);
  })));
  if (!n) $("pokemon").append(el("p", "empty", "No Pokémon yet."));
}

/** The evolution items held, one row per item. */
function renderItems() {
  const box = $("items");
  const ids = Object.keys(state.inv.items).sort((a, b) => itemName(a).localeCompare(itemName(b)));
  $("n-items").textContent = String(ids.length);
  box.replaceChildren(...ids.map((key) => {   // a key as the file wrote it: an id or a name
    const id = catalog ? itemId(key) : key;
    const name = itemName(id || key);
    const note = id ? "" : "No evolution uses this item, so the plan ignores it";
    const r = row(el("span", "spr", name.charAt(0)), name, note, state.inv.items[key],
                  (n) => { state.inv.items[key] = n; }, () => { delete state.inv.items[key]; });
    r.dataset.k = (name + " " + key).toLowerCase();
    return r;
  }));
  if (!ids.length) box.append(el("p", "empty", "No evolution items yet."));
}

/** The Pokédex or never-caught list, one chip per species. */
function renderList(key) {
  const ids = [...state.inv[key]].sort(byTitle);
  $("n-" + key).textContent = String(ids.length);
  $(key).replaceChildren(...ids.map((id) => chip(id, 1, "×", "Remove " + title(id), () => {
    state.inv[key] = state.inv[key].filter((s) => s !== id);
  })));
  if (!ids.length) $(key).append(el("p", "empty", "None."));
}

/** A question for a count the plan needs: a blank number field that fills the inventory in once answered. */
function question(picture, name, why, set) {
  const r = el("div", "row");
  const nm = el("span", "nm", name);
  nm.append(el("small", null, why));
  const input = numberField(name, null);
  input.placeholder = "?";
  input.addEventListener("change", () => {
    const n = wholeNumber(input);
    if (n === null) {
      input.value = "";
      return;
    }
    set(n);
    edited(true);
  });
  r.append(picture, nm, input, el("span"));
  return r;
}

/** Ask for the candy and item counts the last plan was missing and still aren't filled in. */
function renderNeeds() {
  const ok = state.inv && reply && !reply.error;
  const fams = ok ? reply.missing_candy.filter((f) => !(f in state.inv.candy)) : [];
  const held = new Set(Object.keys(state.inv ? state.inv.items : {}).map(itemId));
  const items = ok ? Object.entries(reply.missing_items).filter(([id]) => !held.has(id)) : [];
  const rare = state.inv && state.rareUnknown
    ? [question(el("span", "spr", "R"), "Rare Candy", "No item bag screenshot was read: the plan uses 0 until then",
                (n) => { state.inv.rare_candy = n; state.rareUnknown = false; })]
    : [];
  $("needs-rows").replaceChildren(
    ...rare,
    ...fams.map((fam) => question(sprite(fam), title(fam) + " candy", "From any of its Pokémon's pages",
                                  (n) => { state.inv.candy[fam] = n; })),
    ...items.map(([id, steps]) => question(el("span", "spr", itemName(id).charAt(0)), itemName(id),
                                           "For " + steps.map(([a, b]) => `${title(a)} → ${title(b)}`).join(", "),
                                           (n) => { state.inv.items[id] = n; })));
  $("needs").hidden = !(rare.length || fams.length || items.length);
}

/** Show only the rows and chips matching the search, opening the sections that have a match. */
function find() {
  const q = $("find").value.trim().toLowerCase();
  $("editor").querySelectorAll("[data-k]").forEach((e) => { e.hidden = !!q && !e.dataset.k.includes(q); });
  if (q) {
    $("editor").querySelectorAll("details").forEach((d) => {
      if (d.querySelector("[data-k]:not([hidden])")) d.open = true;
    });
  }
}

/** The settings fields, from the state. */
function renderOptions() {
  document.querySelectorAll("[data-opt]").forEach((i) => {
    const v = state.options[i.dataset.opt];
    if (i.type === "checkbox") i.checked = !!v;
    else i.value = v;
  });
}

/** Wire an add form: apply(name, n) changes the inventory and returns "", or returns what's wrong. */
function addForm(id, apply) {
  const f = $(id);
  const err = f.querySelector(".err");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    const n = f.elements.n ? wholeNumber(f.elements.n) : 0;
    const problem = !catalog ? "One moment: the planner is still loading."
      : n === null ? "Enter a whole number, 0 or more."
        : apply(f.elements.name.value, n);
    err.textContent = problem;
    err.hidden = !problem;
    if (problem) return;
    f.reset();
    f.elements.name.focus();
    edited(true);
  });
}

/** Why a typed species can't be added. */
function noSpecies(name) {
  return `No species called “${name.trim()}” in the species table. Pick one from the list.`;
}

// ---- the worker: the Python planner and screen reader

const worker = new Worker("worker.js?v=" + BUILD, { type: "module" });
const calls = new Map();   // id -> {resolve, reject} of each call still with the worker
let lastCall = 0;

/** Ask the worker for something; it answers calls one at a time, in order. Rejects with the worker's error. */
function call(type, args = {}, transfer = []) {
  return new Promise((resolve, reject) => {
    lastCall += 1;
    calls.set(lastCall, { resolve, reject });
    worker.postMessage({ id: lastCall, type, ...args }, transfer);
  });
}

worker.onmessage = (e) => {
  const m = e.data;
  if (m.type === "progress") {
    status("loading", m.text);
    return;
  }
  const waiting = calls.get(m.id);
  calls.delete(m.id);
  if (m.error) waiting.reject(new Error(m.error));
  else waiting.resolve(m.result);
};
worker.onerror = (e) => {
  e.preventDefault();
  status("error", "The planner didn't load. Check your connection, then reload the page.");
};

// ---- planning

let busy = false;      // a plan request is with the worker
let again = false;     // the inventory changed while it was planning
let timer = 0;

/** Plan the open inventory shortly, once a burst of edits settles. */
function plan() {
  clearTimeout(timer);
  timer = setTimeout(send, 250);
}

/** Send the open inventory to the planner; while one plan runs, the next waits for it. */
async function send() {
  if (!catalog) return;
  if (!state.inv) {
    showNoPlan();
    return;
  }
  if (busy) {
    again = true;
    return;
  }
  busy = true;
  $("plan-state").textContent = "Planning…";
  $("dash").classList.add("stale");
  const answer = await call("plan", { request: { inventory: state.inv, options: state.options,
                                                 source: state.example ? "the example inventory"
                                                   : state.source || "your inventory" } });
  busy = false;
  if (again) {   // this answer is for an older inventory: plan the current one instead
    again = false;
    send();
  } else if (state.inv) {
    showPlan(answer.reply, answer.ms);
  }
}

/** The header's status: loading, ready or error. */
function status(kind, text) {
  $("status").dataset.state = kind;
  $("status").textContent = text;
}

/** The planner is up: fill the name lists, redraw with pictures and checks, and plan. */
function ready(c) {
  catalog = c;
  families = new Set(Object.values(c.species).map((s) => s.family));
  itemIds = new Map();
  Object.entries(c.items).forEach(([id, name]) => {
    itemIds.set(id, id);
    itemIds.set(norm(name), id);
  });
  const option = (value) => { const o = el("option"); o.value = value; return o; };
  $("dl-species").replaceChildren(...Object.keys(c.species).sort(byTitle).map((id) => option(title(id))));
  $("dl-items").replaceChildren(...Object.keys(c.items).map(itemName).sort().map(option));
  status("ready", `Ready · ${Object.keys(c.species).length.toLocaleString()} species`);
  render();
  plan();
}

/** Show the planner's answer, or the error that came back instead. */
function showPlan(r, ms) {
  reply = r;
  $("dash").classList.remove("stale");
  $("plan-error").textContent = r.error || "";
  $("plan-error").hidden = !r.error;
  if (r.error) {
    $("plan-state").textContent = "";
  } else {
    $("plan-empty").hidden = true;
    $("dash").hidden = false;
    $("dash").srcdoc = r.dashboard.replace('<meta charset="utf-8">', '<meta charset="utf-8"><base target="_blank">')
      + RESIZE;
    $("report").textContent = r.report;
    $("text-plan").hidden = false;
    $("copy").disabled = $("dl-dash").disabled = false;
    const took = ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
    $("plan-state").textContent = (state.example ? "Example · " : "") +
      `${r.rare_candy_used} of ${r.rare_candy_available} Rare Candy used · ` +
      `${r.families} ${r.families === 1 ? "family" : "families"} to evolve · planned in ${took}`;
  }
  renderNeeds();
}

/** Clear the plan pane back to its placeholder. */
function showNoPlan() {
  reply = null;
  $("plan-empty").hidden = false;
  $("dash").hidden = $("text-plan").hidden = $("plan-error").hidden = true;
  $("copy").disabled = $("dl-dash").disabled = true;
  $("plan-state").textContent = "";
}

call("start", { build: BUILD }).then(ready, (err) => {
  status("error", `The planner didn't load: ${err.message}. Check your connection, then reload the page.`);
});

// ---- reading screenshots: tesseract.js reads their text here, the worker does everything else in Python

const TESSERACT = "https://cdn.jsdelivr.net/npm/tesseract.js@7.0.0/dist/";
const TESSERACT_CORE = "https://cdn.jsdelivr.net/npm/tesseract.js-core@7.0.0";
// the "fast" English model the command line's Tesseract uses (Homebrew's and Debian's), so both read alike
const TESSDATA = "https://cdn.jsdelivr.net/gh/tesseract-ocr/tessdata_fast@4.1.0";
const KINDS = {   // what each kind of screen is called: one, several
  bag: ["item bag", "item bags"], detail: ["Pokémon page", "Pokémon pages"], pokedex: ["Pokédex page", "Pokédex pages"],
  storage: ["storage screen", "storage screens"], unknown: ["not recognised", "not recognised"],
};
let reader = null;     // a tesseract.js scheduler, made the first time screenshots are read
let shots = [];        // the screenshots chosen: {file, name, state: waiting|reading|done|failed, kind, note}
let reading = false;

/** Load a script from a CDN once. */
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = el("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error("couldn't download the text reader"));
    document.head.append(s);
  });
}

/** tesseract.js with a few workers, reading sparse text (page segmentation mode 11) as the command line does. */
async function textReader() {
  if (reader) return reader;
  if (!window.Tesseract) await loadScript(TESSERACT + "tesseract.min.js");
  const n = Math.max(1, Math.min(4, Math.floor((navigator.hardwareConcurrency || 2) / 2)));
  const scheduler = window.Tesseract.createScheduler();
  const workers = await Promise.all(Array.from({ length: n }, async () => {
    const w = await window.Tesseract.createWorker("eng", 1, {
      workerPath: TESSERACT + "worker.min.js", corePath: TESSERACT_CORE, langPath: TESSDATA, gzip: false,
    });
    await w.setParameters({ tessedit_pageseg_mode: "11" });
    return w;
  }));
  workers.forEach((w) => scheduler.addWorker(w));
  reader = scheduler;
  return reader;
}

/** A file name safe to use as a path in Pyodide's file system, and not used yet in this scan. */
function shotName(file) {
  const base = file.name.replace(/[^\w.-]+/g, "_") || "screenshot";
  let name = base;
  for (let i = 2; shots.some((s) => s.name === name); i += 1) name = base.replace(/(\.\w+)?$/, `-${i}$1`);
  return name;
}

/** Show the screenshot reader, with any files already chosen. */
function showScan(files = []) {
  if (reading) return;
  $("scan").hidden = false;
  $("start").hidden = true;
  addShots(files);
  $("scan").scrollIntoView({ block: "start" });
}

/** Hide the screenshot reader and forget the files chosen. */
function hideScan() {
  shots = [];
  $("scan").hidden = true;
  $("scan-msg").hidden = true;
  render();
}

/** Add chosen files to the list: images only, since recordings aren't read in the browser yet. */
function addShots(files) {
  const videos = [];
  [...files].forEach((file) => {
    if (file.type.startsWith("video/")) videos.push(file.name);
    else if (file.type.startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(file.name)) {
      shots.push({ file, name: shotName(file), state: "waiting" });
    }
  });
  scanMessage(videos.length ? `${videos.join(", ")}: recordings aren't read in the browser yet. Take screenshots ` +
    "instead, or read the recording with pogo-evo-planner scan." : "");
  renderShots();
}

/** A line under the screenshot reader, or none. */
function scanMessage(text) {
  $("scan-msg").textContent = text;
  $("scan-msg").hidden = !text;
}

/** The chosen screenshots, each with what it turned out to be. */
function renderShots() {
  $("shot-list").replaceChildren(...shots.map((s) => {
    const li = el("li", s.state);
    li.append(el("span", "nm", s.file.name));
    const what = s.state === "done" ? (KINDS[s.kind] || [s.kind])[0] : s.state === "failed" ? s.note
      : s.state === "reading" ? "reading…" : "";
    li.append(el("span", "what", what));
    if (!reading && s.state === "waiting") {
      li.append(removeButtonQuiet("×", "Remove " + s.file.name, () => {
        shots = shots.filter((t) => t !== s);
        renderShots();
      }));
    }
    return li;
  }));
  const mode = document.querySelector('input[name="mode"]:checked');
  $("read").disabled = reading || !shots.length || !mode;
  $("read").textContent = reading ? "Reading…"
    : shots.length ? `Read ${shots.length} screenshot${shots.length === 1 ? "" : "s"}` : "Read screenshots";
  $("scan-cancel").disabled = reading;
  document.querySelectorAll('input[name="mode"], #shots').forEach((i) => { i.disabled = reading; });
}

/** A small remove button that doesn't touch the inventory. */
function removeButtonQuiet(glyph, label, remove) {
  const b = el("button", null, glyph);
  b.type = "button";
  b.title = label;
  b.setAttribute("aria-label", label);
  b.addEventListener("click", remove);
  return b;
}

/** Read one screenshot: the worker makes the images to read, tesseract.js reads them, the worker parses the text. */
async function readShot(s, text) {
  s.state = "reading";
  renderShots();
  const passes = await call("scan-add", { name: s.name, modified: s.file.lastModified, data: await s.file.arrayBuffer() });
  if (passes.error) {
    Object.assign(s, { state: "failed", note: passes.error });
    return;
  }
  const tsvs = await Promise.all(passes.map(async (p) => {
    const job = await text.addJob("recognize", new Blob([p.png], { type: "image/png" }), {},
                                  { tsv: true, text: false, blocks: false });
    return { tsv: job.data.tsv, scale: p.scale };
  }));
  Object.assign(s, { state: "done", kind: (await call("scan-read", { name: s.name, tsvs })).kind });
  renderShots();
}

/** Read every chosen screenshot into a new inventory and open it, as `scan` or `evolvenew` would. */
async function readShots() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  if (!replaceOk("what the screenshots show")) return;
  reading = true;
  scanMessage("");
  shots.forEach((s) => Object.assign(s, { state: "waiting", kind: null, note: null }));
  renderShots();
  try {
    $("scan-state").textContent = "Downloading the screen reader (about 17 MB, only the first time)…";
    const [text] = await Promise.all([textReader(), call("scan-start")]);
    $("scan-state").textContent = "";
    await Promise.all(shots.map((s) => readShot(s, text)));
    if (!shots.some((s) => s.state === "done")) throw new Error("none of the files could be read");
    const found = await call("scan-finish", { evolvenew: mode === "evolvenew" });
    state.options.new_entries_only = mode === "evolvenew";   // as the evolvenew command plans
    const unrecognised = shots.filter((s) => s.state === "done" && s.kind === "unknown").map((s) => s.file.name);
    const failed = shots.filter((s) => s.state === "failed").map((s) => s.file.name);
    const { inventory, ...summary } = found;
    const n = shots.filter((s) => s.state === "done").length;
    reading = false;
    shots = [];
    $("scan").hidden = true;
    open(inventory, `${n} screenshot${n === 1 ? "" : "s"}`, { ...summary, mode, unrecognised, failed });
  } catch (err) {
    scanMessage(`Couldn't read the screenshots: ${err.message}.`);
  } finally {
    reading = false;
    $("scan-state").textContent = "";
    renderShots();
  }
}

/** What the screenshots showed, above the editor until dismissed. */
function renderScanned() {
  const sc = state.scan;
  $("scanned").hidden = !sc;
  if (!sc) return;
  const kinds = Object.entries(sc.screens || {}).filter(([k]) => k !== "unknown")
    .map(([k, n]) => `${n} ${(KINDS[k] || [k, k])[n === 1 ? 0 : 1]}`);
  const lines = [`Read ${kinds.join(", ") || "no screens"}.`];
  if (sc.unrecognised && sc.unrecognised.length) {
    lines.push(`Not recognised, so left out: ${sc.unrecognised.join(", ")}.`);
  }
  if (sc.failed && sc.failed.length) lines.push(`Couldn't open: ${sc.failed.join(", ")}.`);
  if (sc.listed && sc.grid >= sc.listed) lines.push(`Read all ${sc.listed} Pokémon your search found.`);
  if (sc.mode === "evolvenew") {
    lines.push("Planning new Pokédex entries only, as evolvenew does (see Planner settings).");
    if (sc.already_caught && sc.already_caught.length) {
      lines.push(`Already caught according to the Evolve button: ${sc.already_caught.map(title).join(", ")}.`);
    }
    if (sc.mega_only && sc.mega_only.length) {
      lines.push(`Listed only for their Mega Evolution, which isn't planned: ${sc.mega_only.map(title).join(", ")}.`);
    }
  }
  const box = $("scanned-lines");
  box.replaceChildren(...lines.map((t) => el("p", null, t)));
  const warnings = [...(sc.warnings || []), ...(sc.notes || [])];
  if (warnings.length) {
    const ul = el("ul", "warnings");
    warnings.forEach((w) => ul.append(el("li", null, w)));
    box.append(ul);
  }
}

// ---- wiring

addForm("add-candy", (name, n) => {
  const id = speciesId(name);
  if (!id) return noSpecies(name);
  state.inv.candy[catalog.species[id].family] = n;
  return "";
});
addForm("add-pokemon", (name) => {
  const id = speciesId(name);
  if (!id) return noSpecies(name);
  state.inv.pokemon.push({ species: id });
  return "";
});
addForm("add-item", (name, n) => {
  const id = itemId(name);
  if (!id) return `No evolution needs an item called “${name.trim()}”. Pick one from the list.`;
  Object.keys(state.inv.items).filter((k) => itemId(k) === id).forEach((k) => { delete state.inv.items[k]; });
  state.inv.items[id] = n;   // replacing the same item under another name, so it isn't listed twice
  return "";
});
LISTS.forEach((key) => addForm("add-" + key, (name) => {
  const id = speciesId(name);
  if (!id) return noSpecies(name);
  if (!state.inv[key].includes(id)) state.inv[key].push(id);
  return "";
}));

$("rare").addEventListener("change", () => {
  const n = wholeNumber($("rare"));
  if (n === null) {
    $("rare").value = state.inv.rare_candy;
    return;
  }
  state.inv.rare_candy = n;
  if (state.rareUnknown) {
    state.rareUnknown = false;
    edited(true);
  } else {
    edited(false);
  }
});
document.querySelectorAll("[data-opt]").forEach((i) => i.addEventListener("change", () => {
  const k = i.dataset.opt;
  const n = Number(i.value);
  if (i.type === "checkbox") {
    state.options[k] = i.checked;
  } else if (i.value === "" || !(n >= 0) || (i.max && n > Number(i.max))) {
    i.value = state.options[k];
    return;
  } else {
    state.options[k] = n;
  }
  edited(false);
}));
$("defaults").addEventListener("click", () => {
  state.options = { ...DEFAULTS };
  renderOptions();
  edited(false);
});
$("find").addEventListener("input", find);

document.querySelectorAll(".pick").forEach((input) => input.addEventListener("change", () => {
  if (input.files[0]) openFile(input.files[0]);
  input.value = "";   // so the same file can be opened again
}));
$("blank").addEventListener("click", () => {
  if (replaceOk("an empty one")) open(EMPTY, "inventory.json");
});
document.querySelectorAll(".shots-open").forEach((b) => b.addEventListener("click", () => showScan()));
$("shots").addEventListener("change", () => {
  addShots($("shots").files);
  $("shots").value = "";   // so the same files can be chosen again
});
document.querySelectorAll('input[name="mode"]').forEach((i) => i.addEventListener("change", () => {
  state.mode = i.value;
  save();
  renderShots();
}));
$("read").addEventListener("click", readShots);
$("scan-cancel").addEventListener("click", hideScan);
$("scanned-close").addEventListener("click", () => {
  state.scan = null;
  save();
  renderScanned();
});
$("download").addEventListener("click", () => {
  const name = /\.json$/i.test(state.source) ? state.source : "inventory.json";
  download(name, JSON.stringify(state.inv, null, 2) + "\n", "application/json");
});
$("close").addEventListener("click", () => {
  if (window.confirm("Close this inventory and go back to the example? It's removed from this browser, so download "
                     + "it first to keep it.")) openExample();
});
$("dl-dash").addEventListener("click", () => download("dashboard.html", reply.dashboard, "text/html"));
$("copy").addEventListener("click", () => {
  navigator.clipboard.writeText(reply.report).then(() => {
    $("copy").textContent = "Copied";
    setTimeout(() => { $("copy").textContent = "Copy as text"; }, 1500);
  }, () => { $("text-plan").open = true; });   // no clipboard here: show the text to copy by hand
});

document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
  $("layout").dataset.pane = b.dataset.pane;
  document.querySelectorAll(".tabs button").forEach((t) => t.setAttribute("aria-pressed", String(t === b)));
}));
window.addEventListener("message", (e) => {
  if (e.source === $("dash").contentWindow && e.data && typeof e.data.dashboardHeight === "number") {
    $("dash").style.height = e.data.dashboardHeight + "px";
  }
});
window.addEventListener("dragover", (e) => {
  if (e.dataTransfer && [...e.dataTransfer.types].includes("Files")) e.preventDefault();
});
window.addEventListener("drop", (e) => {
  const files = e.dataTransfer ? [...e.dataTransfer.files] : [];
  if (!files.length) return;
  e.preventDefault();
  const json = files.find((f) => /\.json$/i.test(f.name) || f.type === "application/json");
  if (json) openFile(json);
  else showScan(files);   // screenshots: read them
});

restore();
document.querySelectorAll('input[name="mode"]').forEach((i) => { i.checked = i.value === state.mode; });
render();
if (!state.inv) openExample();   // a first visit: show the example until the player opens their own
