/* Runs the Python planner and screen reader in Pyodide, off the page's main thread, so a long plan or scan never
   freezes the page. A module worker: Pyodide 314's classic pyodide.js fails to load through importScripts in Chrome.

   The page sends {id, type, ...arguments} and gets {id, result} or {id, error} back, one call at a time. The
   Python side is pogo_evo_planner.webapp, whose @json_api methods take one JSON object and reply in JSON:
     start {build}                   -> what the inventory editor needs from the species table
     plan {request}                  -> {ms, reply}
     scan-start                      -> loads NumPy and OpenCV the first time, and starts a scan
     scan-add {name, modified, data} -> the images Tesseract reads for that screenshot, [{png, scale}]
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
  const response = await fetch(url);
  if (!response.ok) throw new Error(`couldn't fetch ${url} (HTTP ${response.status})`);
  return response;
}

/** The last line of a Python traceback: the error itself. */
function lastLine(error) {
  const lines = String((error && error.message) || error).trim().split("\n");
  return lines[lines.length - 1];
}

/** Call a @json_api method with its arguments as one JSON object, and parse the reply. A mistake the player can
    fix comes back as {"error": message}; it's thrown, so each call fails the same way. */
function api(method, args = {}) {
  const reply = JSON.parse(method(JSON.stringify(args)));
  if (reply && reply.error) throw new Error(reply.error);
  return reply;
}

/** Load Python, unpack the planner package next to it, and return what the editor needs. */
async function start({ build }) {
  postMessage({ type: "progress", text: "Downloading Python…" });
  const [loadedPyodide, zip, species] = await Promise.all([
    loadPyodide({ indexURL: PYODIDE }),
    fetchOk("planner.zip?v=" + build).then((response) => response.arrayBuffer()),
    fetchOk("species.json?v=" + build).then((response) => response.text()),
  ]);
  postMessage({ type: "progress", text: "Starting the planner…" });
  pyodide = loadedPyodide;
  pyodide.unpackArchive(zip, "zip", { extractDir: "/planner" });
  pyodide.runPython("import sys; sys.path.insert(0, '/planner')");
  planner = pyodide.pyimport("pogo_evo_planner.webapp").WebPlanner(species);
  return api(planner.catalog_json);
}

/** Plan an inventory. The page shows reply.error in place of the plan: for an inventory that isn't valid, and for a
    bug in the planner. */
function planRequest({ request }) {
  const started = performance.now();
  let reply;
  try {
    reply = api(planner.plan_json, request);
  } catch (error) {
    const pythonBug = error instanceof pyodide.ffi.PythonError;
    reply = { error: pythonBug ? "The planner hit an error: " + lastLine(error) : error.message };
  }
  return { ms: performance.now() - started, reply };
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
  const passes = api(scanner.passes_json, { name });   // throws for a file that isn't an image
  const result = passes.map((imagePass) => ({ png: pyodide.FS.readFile(imagePass.path), scale: imagePass.scale }));
  return { result, transfer: result.map((imagePass) => imagePass.png.buffer) };
}

const CALLS = {
  start,
  plan: planRequest,
  "scan-start": scanStart,
  "scan-add": scanAdd,
  "scan-read": ({ name, tsvs }) => api(scanner.read_json, { name, tsvs }),
  "scan-finish": ({ evolvenew }) => api(scanner.finish_json, { evolvenew }),
};

let queue = Promise.resolve();   // one call at a time, in the order they came

onmessage = (event) => {
  const { id, type, ...args } = event.data;
  queue = queue.then(async () => {
    try {
      const outcome = await CALLS[type](args);
      const wrapped = type === "scan-add" ? outcome : { result: outcome };
      postMessage({ id, result: wrapped.result }, wrapped.transfer || []);
    } catch (error) {
      postMessage({ id, error: lastLine(error) });
    }
  });
};
