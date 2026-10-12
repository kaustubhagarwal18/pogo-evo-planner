/* Runs the Python planner and screen reader in Pyodide, off the page's main thread, so a long plan or scan never
   freezes the page. A module worker: Pyodide 314's classic pyodide.js fails to load through importScripts in Chrome.

   The page sends {id, type, ...arguments} and gets {id, result} or {id, error} back, one call at a time:
     start {build}                   -> what the inventory editor needs from the species table
     plan {request}                  -> {ms, reply}
     scan-start                      -> loads NumPy and OpenCV the first time, and starts a scan
     scan-add {name, modified, data} -> the images Tesseract reads for that screenshot, [{png, scale}], or {error}
     scan-read {name, tsvs}          -> {kind}: what Tesseract read in each of them, and the kind of screen it is
     scan-finish {evolvenew}         -> the inventory and what the scan found
   It also sends {type: "progress", text} while loading. */

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";   // the same version as the import above
const SCAN = "/scan";   // where the current scan's screenshots are written, in Pyodide's file system

let pyodide = null;
let planner = null;   // pogo_evo_planner.webapp.WebPlanner, holding the species table
let scanner = null;   // the current pogo_evo_planner.webapp.WebScan
let imaging = false;  // NumPy and OpenCV are loaded

/** Fetch a file next to this script, failing on an HTTP error rather than handing back an error page. */
async function fetchOk(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`couldn't fetch ${url} (HTTP ${r.status})`);
  return r;
}

/** The last line of a Python traceback: the error itself. */
function lastLine(err) {
  const lines = String((err && err.message) || err).trim().split("\n");
  return lines[lines.length - 1];
}

/** Load Python, unpack the planner package next to it, and return what the editor needs. */
async function start({ build }) {
  postMessage({ type: "progress", text: "Downloading Python…" });
  const [py, zip, species] = await Promise.all([
    loadPyodide({ indexURL: PYODIDE }),
    fetchOk("planner.zip?v=" + build).then((r) => r.arrayBuffer()),
    fetchOk("species.json?v=" + build).then((r) => r.text()),
  ]);
  postMessage({ type: "progress", text: "Starting the planner…" });
  pyodide = py;
  pyodide.unpackArchive(zip, "zip", { extractDir: "/planner" });
  pyodide.runPython("import sys; sys.path.insert(0, '/planner')");
  planner = pyodide.pyimport("pogo_evo_planner.webapp").WebPlanner(species);
  return JSON.parse(planner.catalog_json());
}

/** Plan an inventory. A bad inventory comes back as reply.error, and so does a bug in the planner. */
function planRequest({ request }) {
  const t0 = performance.now();
  let reply;
  try {
    reply = JSON.parse(planner.plan_json(JSON.stringify(request)));
  } catch (err) {
    reply = { error: "The planner hit an error: " + lastLine(err) };
  }
  return { ms: performance.now() - t0, reply };
}

/** Start a scan, loading the image packages the first time. */
async function scanStart() {
  if (!imaging) {
    await pyodide.loadPackage(["numpy", "opencv-python"]);
    imaging = true;
  }
  scanner = planner.scanner(SCAN);
  return null;
}

/** Write one screenshot with its own modification time (the scan falls back on it for when it was taken), and
    hand back the images Tesseract should read, moved rather than copied to the page. */
function scanAdd({ name, modified, data }) {
  const path = `${SCAN}/${name}`;
  pyodide.FS.writeFile(path, new Uint8Array(data));
  pyodide.FS.utime(path, modified, modified);
  const passes = JSON.parse(scanner.passes_json(name));
  if (passes.error) return { result: passes };
  const result = passes.map((p) => ({ png: pyodide.FS.readFile(p.path), scale: p.scale }));
  return { result, transfer: result.map((p) => p.png.buffer) };
}

const CALLS = {
  start,
  plan: planRequest,
  "scan-start": scanStart,
  "scan-add": scanAdd,
  "scan-read": ({ name, tsvs }) => JSON.parse(scanner.read_json(name, JSON.stringify(tsvs))),
  "scan-finish": ({ evolvenew }) => JSON.parse(scanner.finish_json(evolvenew)),
};

let queue = Promise.resolve();   // one call at a time, in the order they came

onmessage = (e) => {
  const { id, type, ...args } = e.data;
  queue = queue.then(async () => {
    try {
      const out = await CALLS[type](args);
      const wrapped = type === "scan-add" ? out : { result: out };
      postMessage({ id, result: wrapped.result }, wrapped.transfer || []);
    } catch (err) {
      postMessage({ id, error: lastLine(err) });
    }
  });
};
