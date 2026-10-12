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

const byId = (elementId) => document.getElementById(elementId);
// example: the open inventory is the bundled example, shown until the player opens their own;
// scan: what the screenshots showed, until dismissed; rareUnknown: no bag screenshot gave the Rare Candy count
const state = { inv: null, source: "", example: false, options: { ...DEFAULTS }, scan: null, rareUnknown: false,
                mode: "" };
let catalog = null;          // from the planner: {species: {ID: {family, dex}}, items: {ID: name}}
let families = new Set();    // every family id in the species table
let itemIds = new Map();     // an item's id, or its name folded like an id, -> its id
let reply = null;            // the planner's last answer

/** An element with an optional class and text. */
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

/** Display text for an id: DRATINI -> Dratini, RATTATA_ALOLA -> Rattata Alola. */
function title(gameId) {
  return String(gameId).replace(/_/g, " ").toLowerCase().replace(/(^| )\S/g, (letter) => letter.toUpperCase());
}

/** A name in the planner's id style: "Mr. Mime" -> MR_MIME, "King's Rock" -> KINGS_ROCK. */
function norm(name) {
  return String(name).trim().toUpperCase().replace(/[.'’]/g, "").replace(/[\s-]+/g, "_");
}

/** Sort ids by their display text. */
function byTitle(first, second) {
  return title(first).localeCompare(title(second));
}

/** An item as players know it: ITEM_GEN4_EVOLUTION_STONE -> Sinnoh Stone. */
function itemName(itemKey) {
  return (catalog && catalog.items[itemKey]) || title(itemKey.replace(/^ITEM_/, ""));
}

/** The species id for a typed name or id, or null when the species table has no such species. */
function findSpecies(name) {
  const normalized = norm(name);
  return catalog.species[normalized] ? normalized : null;
}

/** The item id for an id or a name players use (Sinnoh Stone, ITEM_SINNOH_STONE), or null when no evolution needs
    such an item. The planner reads inventory files the same way (gamemaster.item_id). */
function findItem(name) {
  const key = norm(name).replace(/^ITEM_/, "");
  return itemIds.get(key) || itemIds.get("ITEM_" + key) || null;
}

/** A Pokémon's picture by dex number; its initial shows until the picture loads, or if it can't. */
function sprite(speciesId) {
  const picture = element("span", "spr");
  const initial = element("span", null, title(speciesId).charAt(0));
  picture.append(initial);
  const dex = catalog && catalog.species[speciesId] && catalog.species[speciesId].dex;
  if (dex) {
    const image = element("img");
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    image.onload = () => initial.remove();
    image.onerror = () => image.remove();
    image.src = SPRITES + dex + ".png";
    picture.append(image);
  }
  return picture;
}

/** A whole number of 0 or more from a number field, or null for anything else. */
function wholeNumber(input) {
  const number = Number(input.value);
  return input.value !== "" && Number.isInteger(number) && number >= 0 ? number : null;
}

/** A number field for a count. */
function numberField(label, value) {
  const input = element("input");
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
  const button = element("button", null, glyph);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", () => { remove(); edited(true); });
  return button;
}

// ---- opening, checking and keeping an inventory

/** A count from a file: a whole number of 0 or more. Throws, naming the field, otherwise. */
function count(value, what) {
  const number = Number(value);
  if (value === null || value === "" || !Number.isInteger(number) || number < 0) {
    throw new Error(`${what} should be a whole number, not ${JSON.stringify(value)}`);
  }
  return number;
}

/** The inventory as the command line writes it: every field present and checked, names in id style. Fields the
    editor doesn't use are kept, so a download gives back everything that was opened. Throws for a bad file. */
function checked(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("it isn't a JSON object");
  if (!Object.keys(EMPTY).some((field) => field in data)) {
    throw new Error("it has none of an inventory's fields (rare_candy, candy, pokemon and so on)");
  }
  const table = (value, what, normalizeKey) => {
    if (value == null) return {};
    if (typeof value !== "object" || Array.isArray(value)) throw new Error(`"${what}" should map names to counts`);
    return Object.fromEntries(Object.entries(value).map(([name, amount]) => [normalizeKey(name),
                                                                              count(amount, `${what}: ${name}`)]));
  };
  const list = (value, what) => {
    if (value == null) return [];
    if (!Array.isArray(value)) throw new Error(`"${what}" should be a list`);
    return [...new Set(value.map(norm))];
  };
  const pokemon = data.pokemon == null ? [] : data.pokemon;
  if (!Array.isArray(pokemon)) throw new Error('"pokemon" should be a list');
  return {
    ...data,
    rare_candy: count(data.rare_candy == null ? 0 : data.rare_candy, "rare_candy"),
    candy: table(data.candy, "candy", norm),
    items: table(data.items, "items", (name) => name),
    pokedex: list(data.pokedex, "pokedex"),
    not_caught: list(data.not_caught, "not_caught"),
    pokemon: pokemon.map((entry, index) => {
      if (!entry || typeof entry.species !== "string") throw new Error(`Pokémon number ${index + 1} has no species`);
      return { ...entry, species: norm(entry.species) };
    }),
  };
}

/** Keep the inventory and settings in this browser. Private browsing can refuse; then the page just forgets. */
function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify(state));
    byId("saved").hidden = false;
  } catch (error) {
    byId("saved").hidden = true;
  }
}

/** Bring back what save() kept, if anything. */
function restore() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORE) || "null");
    if (stored && stored.options) state.options = { ...DEFAULTS, ...stored.options };
    if (stored && typeof stored.mode === "string") state.mode = stored.mode;
    if (stored && stored.inv) {
      state.inv = checked(stored.inv);
      state.source = String(stored.source || "");
      state.example = !!stored.example;
      state.scan = stored.scan && typeof stored.scan === "object" ? stored.scan : null;
      state.rareUnknown = !!stored.rareUnknown;
    }
  } catch (error) {
    state.inv = null;   // nothing kept, storage is off, or what was kept no longer checks out
  }
}

/** Show a message above the inventory, or hide it with no text. */
function message(text) {
  byId("msg").textContent = text || "";
  byId("msg").hidden = !text;
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
    const response = await fetch("inventory_sample.json?v=" + BUILD);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    open(await response.json(), "inventory_sample.json", null, true);
  } catch (error) {
    message(`Couldn't load the example (${error.message}). You can still upload your own.`);
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
  } catch (error) {
    message(`Couldn't open ${file.name}: ${error instanceof SyntaxError ? "it isn't valid JSON" : error.message}.`);
  }
}

/** Offer text as a file to save. */
function download(name, text, type) {
  const link = element("a");
  link.href = URL.createObjectURL(new Blob([text], { type }));
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
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
  const inventory = state.inv;
  byId("start").hidden = (!!inventory && !state.example) || !byId("scan").hidden;
  byId("start-example").hidden = !state.example;
  byId("editor").hidden = !inventory;
  document.querySelectorAll(".own").forEach((node) => { node.hidden = state.example; });   // nothing to save or close
  renderOptions();
  if (!inventory) {
    showNoPlan();
    return;
  }
  byId("name").textContent = state.example ? "Example inventory" : state.source || "Your inventory";
  byId("rare").value = inventory.rare_candy;
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
  const container = element("div", "row" + (note ? " unknown" : ""));
  container.dataset.k = name.toLowerCase();
  const label = element("span", "nm", name);
  if (note) label.append(element("small", null, note));
  const input = numberField(name, value);
  input.addEventListener("change", () => {
    const number = wholeNumber(input);
    if (number === null) {
      input.value = value;   // keep the last good count
      return;
    }
    value = number;
    set(number);
    edited(false);
  });
  container.append(picture, label, input, removeButton("×", "Remove " + name, remove));
  return container;
}

/** What's wrong with a candy key, or "" when it's a family in the species table. */
function familyNote(family) {
  if (!catalog || families.has(family)) return "";
  const entry = catalog.species[family];
  return entry ? `Not a family: candy is counted under ${title(entry.family)}` : "Not in the species table";
}

/** The candy counts, one row per family. */
function renderCandy() {
  const box = byId("candy");
  const familyIds = Object.keys(state.inv.candy).sort(byTitle);
  byId("n-candy").textContent = familyIds.length === 1 ? "1 family" : `${familyIds.length} families`;
  box.replaceChildren(...familyIds.map((family) => row(sprite(family), title(family), familyNote(family),
                                                       state.inv.candy[family],
                                                       (number) => { state.inv.candy[family] = number; },
                                                       () => { delete state.inv.candy[family]; })));
  if (!familyIds.length) box.append(element("p", "empty", "No candy counts yet."));
}

/** A species chip: picture, name, how many when more than one, and a remove button. */
function chip(speciesId, copies, glyph, removeLabel, remove) {
  const known = !catalog || speciesId in catalog.species;
  const container = element("span", "chip" + (known ? "" : " unknown"));
  container.dataset.k = title(speciesId).toLowerCase();
  if (!known) container.title = "Not in the species table, so the plan skips it";
  container.append(sprite(speciesId), element("span", null, title(speciesId)));
  if (copies > 1) container.append(element("span", "x", "×" + copies));
  container.append(removeButton(glyph, removeLabel, remove));
  return container;
}

/** The Pokémon held, one chip per species with how many. */
function renderPokemon() {
  const copies = new Map();
  state.inv.pokemon.forEach((specimen) => copies.set(specimen.species, (copies.get(specimen.species) || 0) + 1));
  const speciesIds = [...copies.keys()].sort(byTitle);
  const total = state.inv.pokemon.length;
  byId("n-pokemon").textContent = `${total} (${speciesIds.length} species)`;
  byId("pokemon").replaceChildren(...speciesIds.map((speciesId) => chip(
    speciesId, copies.get(speciesId), "−", "Remove one " + title(speciesId), () => {
      state.inv.pokemon.splice(state.inv.pokemon.map((specimen) => specimen.species).lastIndexOf(speciesId), 1);
    })));
  if (!total) byId("pokemon").append(element("p", "empty", "No Pokémon yet."));
}

/** The evolution items held, one row per item. */
function renderItems() {
  const box = byId("items");
  const keys = Object.keys(state.inv.items).sort((first, second) => itemName(first).localeCompare(itemName(second)));
  byId("n-items").textContent = String(keys.length);
  box.replaceChildren(...keys.map((key) => {   // a key as the file wrote it: an id or a name
    const itemId = catalog ? findItem(key) : key;
    const name = itemName(itemId || key);
    const note = itemId ? "" : "No evolution uses this item, so the plan ignores it";
    const itemRow = row(element("span", "spr", name.charAt(0)), name, note, state.inv.items[key],
                        (number) => { state.inv.items[key] = number; }, () => { delete state.inv.items[key]; });
    itemRow.dataset.k = (name + " " + key).toLowerCase();
    return itemRow;
  }));
  if (!keys.length) box.append(element("p", "empty", "No evolution items yet."));
}

/** The Pokédex or never-caught list, one chip per species. */
function renderList(key) {
  const speciesIds = [...state.inv[key]].sort(byTitle);
  byId("n-" + key).textContent = String(speciesIds.length);
  byId(key).replaceChildren(...speciesIds.map((speciesId) => chip(speciesId, 1, "×", "Remove " + title(speciesId),
                                                                  () => {
    state.inv[key] = state.inv[key].filter((listed) => listed !== speciesId);
  })));
  if (!speciesIds.length) byId(key).append(element("p", "empty", "None."));
}

/** A question for a count the plan needs: a blank number field that fills the inventory in once answered. */
function question(picture, name, why, set) {
  const container = element("div", "row");
  const label = element("span", "nm", name);
  label.append(element("small", null, why));
  const input = numberField(name, null);
  input.placeholder = "?";
  input.addEventListener("change", () => {
    const number = wholeNumber(input);
    if (number === null) {
      input.value = "";
      return;
    }
    set(number);
    edited(true);
  });
  container.append(picture, label, input, element("span"));
  return container;
}

/** Ask for the candy and item counts the last plan was missing and still aren't filled in. */
function renderNeeds() {
  const planned = state.inv && reply && !reply.error;
  const familyIds = planned ? reply.missing_candy.filter((family) => !(family in state.inv.candy)) : [];
  const held = new Set(Object.keys(state.inv ? state.inv.items : {}).map(findItem));
  const items = planned ? Object.entries(reply.missing_items).filter(([itemId]) => !held.has(itemId)) : [];
  const rare = state.inv && state.rareUnknown
    ? [question(element("span", "spr", "R"), "Rare Candy", "No item bag screenshot was read: the plan uses 0 until then",
                (number) => { state.inv.rare_candy = number; state.rareUnknown = false; })]
    : [];
  byId("needs-rows").replaceChildren(
    ...rare,
    ...familyIds.map((family) => question(sprite(family), title(family) + " candy", "From any of its Pokémon's pages",
                                          (number) => { state.inv.candy[family] = number; })),
    ...items.map(([itemId, steps]) => question(
      element("span", "spr", itemName(itemId).charAt(0)), itemName(itemId),
      "For " + steps.map(([source, target]) => `${title(source)} → ${title(target)}`).join(", "),
      (number) => { state.inv.items[itemId] = number; })));
  byId("needs").hidden = !(rare.length || familyIds.length || items.length);
}

/** Show only the rows and chips matching the search, opening the sections that have a match. */
function find() {
  const query = byId("find").value.trim().toLowerCase();
  byId("editor").querySelectorAll("[data-k]").forEach((node) => {
    node.hidden = !!query && !node.dataset.k.includes(query);
  });
  if (query) {
    byId("editor").querySelectorAll("details").forEach((section) => {
      if (section.querySelector("[data-k]:not([hidden])")) section.open = true;
    });
  }
}

/** The settings fields, from the state. */
function renderOptions() {
  document.querySelectorAll("[data-opt]").forEach((field) => {
    const value = state.options[field.dataset.opt];
    if (field.type === "checkbox") field.checked = !!value;
    else field.value = value;
  });
}

/** Wire an add form: apply(name, number) changes the inventory and returns "", or returns what's wrong. */
function addForm(formId, apply) {
  const form = byId(formId);
  const errorLine = form.querySelector(".err");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const number = form.elements.n ? wholeNumber(form.elements.n) : 0;
    const problem = !catalog ? "One moment: the planner is still loading."
      : number === null ? "Enter a whole number, 0 or more."
        : apply(form.elements.name.value, number);
    errorLine.textContent = problem;
    errorLine.hidden = !problem;
    if (problem) return;
    form.reset();
    form.elements.name.focus();
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

worker.onmessage = (event) => {
  const received = event.data;
  if (received.type === "progress") {
    status("loading", received.text);
    return;
  }
  const waiting = calls.get(received.id);
  calls.delete(received.id);
  if (received.error) waiting.reject(new Error(received.error));
  else waiting.resolve(received.result);
};
worker.onerror = (event) => {
  event.preventDefault();
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
  byId("plan-state").textContent = "Planning…";
  byId("dash").classList.add("stale");
  let answer;
  try {
    answer = await call("plan", { request: { inventory: state.inv, options: state.options,
                                             source: state.example ? "the example inventory"
                                               : state.source || "your inventory" } });
  } catch (error) {   // the worker itself failed; the page still answers, and the next edit plans again
    answer = { reply: { error: "The planner stopped working: " + error.message + ". Reload the page." }, ms: 0 };
  } finally {
    busy = false;
  }
  if (again) {   // this answer is for an older inventory: plan the current one instead
    again = false;
    send();
  } else if (state.inv) {
    showPlan(answer.reply, answer.ms);
  }
}

/** The header's status: loading, ready or error. */
function status(kind, text) {
  byId("status").dataset.state = kind;
  byId("status").textContent = text;
}

/** The planner is up: fill the name lists, redraw with pictures and checks, and plan. */
function ready(loadedCatalog) {
  catalog = loadedCatalog;
  families = new Set(Object.values(loadedCatalog.species).map((entry) => entry.family));
  itemIds = new Map();
  Object.entries(loadedCatalog.items).forEach(([itemId, name]) => {
    itemIds.set(itemId, itemId);
    itemIds.set(norm(name), itemId);
  });
  const option = (value) => { const optionElement = element("option"); optionElement.value = value; return optionElement; };
  byId("dl-species").replaceChildren(...Object.keys(loadedCatalog.species).sort(byTitle)
    .map((speciesId) => option(title(speciesId))));
  byId("dl-items").replaceChildren(...Object.keys(loadedCatalog.items).map(itemName).sort().map(option));
  status("ready", `Ready · ${Object.keys(loadedCatalog.species).length.toLocaleString()} species`);
  render();
  plan();
}

/** Show the planner's answer, or the error that came back instead. */
function showPlan(answer, elapsed) {
  reply = answer;
  byId("dash").classList.remove("stale");
  byId("plan-error").textContent = answer.error || "";
  byId("plan-error").hidden = !answer.error;
  if (answer.error) {
    byId("plan-state").textContent = "";
  } else {
    byId("plan-empty").hidden = true;
    byId("dash").hidden = false;
    byId("dash").srcdoc = answer.dashboard.replace('<meta charset="utf-8">',
                                                   '<meta charset="utf-8"><base target="_blank">') + RESIZE;
    byId("report").textContent = answer.report;
    byId("text-plan").hidden = false;
    byId("copy").disabled = byId("dl-dash").disabled = false;
    const took = elapsed < 1000 ? `${Math.round(elapsed)} ms` : `${(elapsed / 1000).toFixed(1)} s`;
    byId("plan-state").textContent = (state.example ? "Example · " : "") +
      `${answer.rare_candy_used} of ${answer.rare_candy_available} Rare Candy used · ` +
      `${answer.families} ${answer.families === 1 ? "family" : "families"} to evolve · planned in ${took}`;
  }
  renderNeeds();
}

/** Clear the plan pane back to its placeholder. */
function showNoPlan() {
  reply = null;
  byId("plan-empty").hidden = false;
  byId("dash").hidden = byId("text-plan").hidden = byId("plan-error").hidden = true;
  byId("copy").disabled = byId("dl-dash").disabled = true;
  byId("plan-state").textContent = "";
}

call("start", { build: BUILD }).then(ready).catch((error) => {
  status("error", `The planner didn't load: ${error.message}. Check your connection, then reload the page.`);
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
function loadScript(url) {
  return new Promise((resolve, reject) => {
    const script = element("script");
    script.src = url;
    script.onload = resolve;
    script.onerror = () => reject(new Error("couldn't download the text reader"));
    document.head.append(script);
  });
}

/** tesseract.js with a few workers, reading sparse text (page segmentation mode 11) as the command line does. */
async function textReader() {
  if (reader) return reader;
  if (!window.Tesseract) await loadScript(TESSERACT + "tesseract.min.js");
  const workerCount = Math.max(1, Math.min(4, Math.floor((navigator.hardwareConcurrency || 2) / 2)));
  const scheduler = window.Tesseract.createScheduler();
  const textWorkers = await Promise.all(Array.from({ length: workerCount }, async () => {
    const textWorker = await window.Tesseract.createWorker("eng", 1, {
      workerPath: TESSERACT + "worker.min.js", corePath: TESSERACT_CORE, langPath: TESSDATA, gzip: false,
    });
    await textWorker.setParameters({ tessedit_pageseg_mode: "11" });
    return textWorker;
  }));
  textWorkers.forEach((textWorker) => scheduler.addWorker(textWorker));
  reader = scheduler;
  return reader;
}

/** A file name safe to use as a path in Pyodide's file system, and not used yet in this scan. */
function shotName(file) {
  const base = file.name.replace(/[^\w.-]+/g, "_") || "screenshot";
  let name = base;
  for (let copy = 2; shots.some((shot) => shot.name === name); copy += 1) {
    name = base.replace(/(\.\w+)?$/, `-${copy}$1`);
  }
  return name;
}

/** Show the screenshot reader, with any files already chosen. */
function showScan(files = []) {
  if (reading) return;
  byId("scan").hidden = false;
  byId("start").hidden = true;
  addShots(files);
  byId("scan").scrollIntoView({ block: "start" });
}

/** Hide the screenshot reader and forget the files chosen. */
function hideScan() {
  shots = [];
  byId("scan").hidden = true;
  byId("scan-msg").hidden = true;
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
  byId("scan-msg").textContent = text;
  byId("scan-msg").hidden = !text;
}

/** The chosen screenshots, each with what it turned out to be. */
function renderShots() {
  byId("shot-list").replaceChildren(...shots.map((shot) => {
    const item = element("li", shot.state);
    item.append(element("span", "nm", shot.file.name));
    const what = shot.state === "done" ? (KINDS[shot.kind] || [shot.kind])[0] : shot.state === "failed" ? shot.note
      : shot.state === "reading" ? "reading…" : "";
    item.append(element("span", "what", what));
    if (!reading && shot.state === "waiting") {
      item.append(removeButtonQuiet("×", "Remove " + shot.file.name, () => {
        shots = shots.filter((other) => other !== shot);
        renderShots();
      }));
    }
    return item;
  }));
  const mode = document.querySelector('input[name="mode"]:checked');
  byId("read").disabled = reading || !shots.length || !mode;
  byId("read").textContent = reading ? "Reading…"
    : shots.length ? `Read ${shots.length} screenshot${shots.length === 1 ? "" : "s"}` : "Read screenshots";
  byId("scan-cancel").disabled = reading;
  document.querySelectorAll('input[name="mode"], #shots').forEach((field) => { field.disabled = reading; });
}

/** A small remove button that doesn't touch the inventory. */
function removeButtonQuiet(glyph, label, remove) {
  const button = element("button", null, glyph);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.addEventListener("click", remove);
  return button;
}

/** Read one screenshot: the worker makes the images to read, tesseract.js reads them, the worker parses the text.
    A screenshot that can't be read is marked failed with the reason, and the others carry on. */
async function readShot(shot, scheduler) {
  shot.state = "reading";
  renderShots();
  try {
    const passes = await call("scan-add", { name: shot.name, modified: shot.file.lastModified,
                                            data: await shot.file.arrayBuffer() });
    const tsvs = await Promise.all(passes.map(async (imagePass) => {
      const job = await scheduler.addJob("recognize", new Blob([imagePass.png], { type: "image/png" }), {},
                                         { tsv: true, text: false, blocks: false });
      return { tsv: job.data.tsv, scale: imagePass.scale };
    }));
    const { kind } = await call("scan-read", { name: shot.name, tsvs });
    Object.assign(shot, { state: "done", kind });
  } catch (error) {
    Object.assign(shot, { state: "failed", note: error.message });
  }
  renderShots();
}

/** Read every chosen screenshot into a new inventory and open it, as `scan` or `evolvenew` would. */
async function readShots() {
  const mode = document.querySelector('input[name="mode"]:checked').value;
  if (!replaceOk("what the screenshots show")) return;
  reading = true;
  scanMessage("");
  shots.forEach((shot) => Object.assign(shot, { state: "waiting", kind: null, note: null }));
  renderShots();
  try {
    byId("scan-state").textContent = "Downloading the screen reader (about 17 MB, only the first time)…";
    const [scheduler] = await Promise.all([textReader(), call("scan-start")]);
    byId("scan-state").textContent = "";
    await Promise.all(shots.map((shot) => readShot(shot, scheduler)));
    if (!shots.some((shot) => shot.state === "done")) throw new Error("none of the files could be read");
    const found = await call("scan-finish", { evolvenew: mode === "evolvenew" });
    state.options.new_entries_only = mode === "evolvenew";   // as the evolvenew command plans
    const unrecognised = shots.filter((shot) => shot.state === "done" && shot.kind === "unknown")
      .map((shot) => shot.file.name);
    const failed = shots.filter((shot) => shot.state === "failed").map((shot) => shot.file.name);
    const { inventory, ...summary } = found;
    const readCount = shots.filter((shot) => shot.state === "done").length;
    reading = false;
    shots = [];
    byId("scan").hidden = true;
    open(inventory, `${readCount} screenshot${readCount === 1 ? "" : "s"}`,
         { ...summary, mode, unrecognised, failed });
  } catch (error) {
    scanMessage(`Couldn't read the screenshots: ${error.message}.`);
  } finally {
    reading = false;
    byId("scan-state").textContent = "";
    renderShots();
  }
}

/** What the screenshots showed, above the editor until dismissed. */
function renderScanned() {
  const summary = state.scan;
  byId("scanned").hidden = !summary;
  if (!summary) return;
  const kinds = Object.entries(summary.screens || {}).filter(([kind]) => kind !== "unknown")
    .map(([kind, total]) => `${total} ${(KINDS[kind] || [kind, kind])[total === 1 ? 0 : 1]}`);
  const lines = [`Read ${kinds.join(", ") || "no screens"}.`];
  if (summary.unrecognised && summary.unrecognised.length) {
    lines.push(`Not recognised, so left out: ${summary.unrecognised.join(", ")}.`);
  }
  if (summary.failed && summary.failed.length) lines.push(`Couldn't open: ${summary.failed.join(", ")}.`);
  if (summary.listed && summary.grid >= summary.listed) {
    lines.push(`Read all ${summary.listed} Pokémon your search found.`);
  }
  if (summary.mode === "evolvenew") {
    lines.push("Planning new Pokédex entries only, as evolvenew does (see Planner settings).");
    if (summary.already_caught && summary.already_caught.length) {
      lines.push(`Already caught according to the Evolve button: ${summary.already_caught.map(title).join(", ")}.`);
    }
    if (summary.mega_only && summary.mega_only.length) {
      lines.push(`Listed only for their Mega Evolution, which isn't planned: ${summary.mega_only.map(title).join(", ")}.`);
    }
  }
  const box = byId("scanned-lines");
  box.replaceChildren(...lines.map((line) => element("p", null, line)));
  const warnings = [...(summary.warnings || []), ...(summary.notes || [])];
  if (warnings.length) {
    const list = element("ul", "warnings");
    warnings.forEach((warning) => list.append(element("li", null, warning)));
    box.append(list);
  }
}

// ---- wiring

addForm("add-candy", (name, number) => {
  const speciesId = findSpecies(name);
  if (!speciesId) return noSpecies(name);
  state.inv.candy[catalog.species[speciesId].family] = number;
  return "";
});
addForm("add-pokemon", (name) => {
  const speciesId = findSpecies(name);
  if (!speciesId) return noSpecies(name);
  state.inv.pokemon.push({ species: speciesId });
  return "";
});
addForm("add-item", (name, number) => {
  const itemId = findItem(name);
  if (!itemId) return `No evolution needs an item called “${name.trim()}”. Pick one from the list.`;
  Object.keys(state.inv.items).filter((key) => findItem(key) === itemId)
    .forEach((key) => { delete state.inv.items[key]; });
  state.inv.items[itemId] = number;   // replacing the same item under another name, so it isn't listed twice
  return "";
});
LISTS.forEach((key) => addForm("add-" + key, (name) => {
  const speciesId = findSpecies(name);
  if (!speciesId) return noSpecies(name);
  if (!state.inv[key].includes(speciesId)) state.inv[key].push(speciesId);
  return "";
}));

byId("rare").addEventListener("change", () => {
  const number = wholeNumber(byId("rare"));
  if (number === null) {
    byId("rare").value = state.inv.rare_candy;
    return;
  }
  state.inv.rare_candy = number;
  if (state.rareUnknown) {
    state.rareUnknown = false;
    edited(true);
  } else {
    edited(false);
  }
});
document.querySelectorAll("[data-opt]").forEach((field) => field.addEventListener("change", () => {
  const option = field.dataset.opt;
  const number = Number(field.value);
  if (field.type === "checkbox") {
    state.options[option] = field.checked;
  } else if (field.value === "" || !(number >= 0) || (field.max && number > Number(field.max))) {
    field.value = state.options[option];
    return;
  } else {
    state.options[option] = number;
  }
  edited(false);
}));
byId("defaults").addEventListener("click", () => {
  state.options = { ...DEFAULTS };
  renderOptions();
  edited(false);
});
byId("find").addEventListener("input", find);

document.querySelectorAll(".pick").forEach((input) => input.addEventListener("change", () => {
  if (input.files[0]) openFile(input.files[0]);
  input.value = "";   // so the same file can be opened again
}));
byId("blank").addEventListener("click", () => {
  if (replaceOk("an empty one")) open(EMPTY, "inventory.json");
});
document.querySelectorAll(".shots-open").forEach((button) => button.addEventListener("click", () => showScan()));
byId("shots").addEventListener("change", () => {
  addShots(byId("shots").files);
  byId("shots").value = "";   // so the same files can be chosen again
});
document.querySelectorAll('input[name="mode"]').forEach((radio) => radio.addEventListener("change", () => {
  state.mode = radio.value;
  save();
  renderShots();
}));
byId("read").addEventListener("click", readShots);
byId("scan-cancel").addEventListener("click", hideScan);
byId("scanned-close").addEventListener("click", () => {
  state.scan = null;
  save();
  renderScanned();
});
byId("download").addEventListener("click", () => {
  const name = /\.json$/i.test(state.source) ? state.source : "inventory.json";
  download(name, JSON.stringify(state.inv, null, 2) + "\n", "application/json");
});
byId("close").addEventListener("click", () => {
  if (window.confirm("Close this inventory and go back to the example? It's removed from this browser, so download "
                     + "it first to keep it.")) openExample();
});
byId("dl-dash").addEventListener("click", () => download("dashboard.html", reply.dashboard, "text/html"));
byId("copy").addEventListener("click", () => {
  navigator.clipboard.writeText(reply.report).then(() => {
    byId("copy").textContent = "Copied";
    setTimeout(() => { byId("copy").textContent = "Copy as text"; }, 1500);
  }, () => { byId("text-plan").open = true; });   // no clipboard here: show the text to copy by hand
});

document.querySelectorAll(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
  byId("layout").dataset.pane = tab.dataset.pane;
  document.querySelectorAll(".tabs button").forEach((other) => other.setAttribute("aria-pressed", String(other === tab)));
}));
window.addEventListener("message", (event) => {
  if (event.source === byId("dash").contentWindow && event.data && typeof event.data.dashboardHeight === "number") {
    byId("dash").style.height = event.data.dashboardHeight + "px";
  }
});
window.addEventListener("dragover", (event) => {
  if (event.dataTransfer && [...event.dataTransfer.types].includes("Files")) event.preventDefault();
});
window.addEventListener("drop", (event) => {
  const files = event.dataTransfer ? [...event.dataTransfer.files] : [];
  if (!files.length) return;
  event.preventDefault();
  const json = files.find((file) => /\.json$/i.test(file.name) || file.type === "application/json");
  if (json) openFile(json);
  else showScan(files);   // screenshots: read them
});

restore();
document.querySelectorAll('input[name="mode"]').forEach((radio) => { radio.checked = radio.value === state.mode; });
render();
if (!state.inv) openExample();   // a first visit: show the example until the player opens their own
