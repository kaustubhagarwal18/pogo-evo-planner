/* Runs the Python planner in Pyodide, off the page's main thread, so a long plan never freezes the page.
   A module worker: Pyodide 314's classic pyodide.js fails to load through importScripts in Chrome.

   Page -> worker: {type: "start", build}, then {type: "plan", request: {inventory, options, source}}
   Worker -> page: {type: "progress", text}, {type: "ready", catalog} or {type: "failed", message},
                   then {type: "plan", ms, reply} for each request, in order. */

import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";   // the same version as the import above

let planner = null;   // pogo_evo_planner.webapp.WebPlanner, holding the species table

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

/** Load Python, unpack the planner package next to it, and hand the page what its editor needs. */
async function start(build) {
  postMessage({ type: "progress", text: "Downloading Python…" });
  const [pyodide, zip, species] = await Promise.all([
    loadPyodide({ indexURL: PYODIDE }),
    fetchOk("planner.zip?v=" + build).then((r) => r.arrayBuffer()),
    fetchOk("species.json?v=" + build).then((r) => r.text()),
  ]);
  postMessage({ type: "progress", text: "Starting the planner…" });
  pyodide.unpackArchive(zip, "zip", { extractDir: "/planner" });
  pyodide.runPython("import sys; sys.path.insert(0, '/planner')");
  planner = pyodide.pyimport("pogo_evo_planner.webapp").WebPlanner(species);
  postMessage({ type: "ready", catalog: JSON.parse(planner.catalog_json()) });
}

onmessage = (e) => {
  const m = e.data;
  if (m.type === "start") {
    start(m.build).catch((err) => postMessage({ type: "failed", message: lastLine(err) }));
  } else if (m.type === "plan") {
    const t0 = performance.now();
    let reply;
    try {
      reply = JSON.parse(planner.plan_json(JSON.stringify(m.request)));
    } catch (err) {   // a bug in the planner, not a bad inventory: plan_json reports those itself
      reply = { error: "The planner hit an error: " + lastLine(err) };
    }
    postMessage({ type: "plan", ms: performance.now() - t0, reply });
  }
};
