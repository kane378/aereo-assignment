"use strict";
const $ = (id) => document.getElementById(id);
const fileInput = $("fileInput"), go = $("go"), msg = $("msg"), drop = $("drop");
let current = null; // {file, measurements}

// ---- helpers
const keyStore = { get() { try { return sessionStorage.getItem("apiKey") || ""; } catch { return ""; } },
                   set(v) { try { sessionStorage.setItem("apiKey", v); } catch { /* ignore */ } } };
$("apiKey").value = keyStore.get();
$("apiKey").addEventListener("input", (e) => keyStore.set(e.target.value));

function setMsg(text, isErr) { msg.textContent = text; msg.className = "msg" + (isErr ? " err" : ""); }
function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
const fmt = (v, d = 2) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d });

async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers);
  const key = $("apiKey").value.trim();
  if (key) headers["X-API-Key"] = key;
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new Error(res.status + ": " + detail);
  }
  return res.status === 204 ? null : res.json();
}

// ---- file picking
function picked(file) {
  if (!file) return;
  const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files;
  $("dropText").textContent = file.name + " (" + fmt(file.size / 1024, 1) + " KB)";
  go.disabled = false; setMsg("");
}
fileInput.addEventListener("change", () => picked(fileInput.files[0]));
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => picked(e.dataTransfer.files[0]));

// ---- upload
$("uploadForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = fileInput.files[0]; if (!file) return;
  const body = new FormData();
  body.append("file", file);
  const crs = $("assumeCrs").value.trim(); if (crs) body.append("assume_crs", crs);
  go.disabled = true; setMsg("Processing…");
  try {
    const rec = await api("/api/files/", { method: "POST", body });
    if (rec.status === "FAILED") { setMsg("Saved but could not be processed: " + (rec.error_message || "unknown error"), true); }
    else { setMsg("Done: " + rec.feature_count + " features."); await show(rec.id); }
    loadHistory();
  } catch (err) { setMsg(err.message, true); }
  finally { go.disabled = false; }
});

// ---- results
async function show(id) {
  const [file, meas, feats] = await Promise.all([
    api("/api/files/" + id + "/"),
    api("/api/files/" + id + "/measurements/?limit=1000"),
    api("/api/files/" + id + "/features/?limit=1000"),
  ]);
  current = { file, meas };
  $("result").hidden = false;
  $("rName").textContent = file.filename;
  $("rMeta").textContent = file.file_type.toUpperCase() + " · CRS " + (file.crs || "unknown") + " · " +
    Object.entries(file.geometry_summary || {}).map(([k, v]) => v + " " + k).join(", ");

  const s = meas.summary, stats = $("stats"); stats.replaceChildren();
  [[s.total_features, "features"], [s.measured, "measured"], [fmt(s.total_area_sq_m, 1), "total area m²"],
   [fmt(s.total_area_sq_m / 10000, 4), "total hectares"], [fmt(s.total_length_m, 1), "total length m"]]
    .forEach(([v, l]) => { const d = el("div", undefined, "stat"); d.append(el("b", String(v)), el("span", l)); stats.append(d); });

  const rows = $("rows"); rows.replaceChildren();
  meas.measurements.forEach((m) => {
    const tr = el("tr");
    const cells = [[m.feature_index], [m.layer || "–"], [m.geometry_type || "–"],
      [fmt(m.area_sq_m), "n"], [fmt(m.area_hectares, 4), "n"], [fmt(m.length_m), "n"],
      [fmt(m.geodesic_area_sq_m), "n"], [fmt(m.geodesic_length_m), "n"], [m.projected_crs || "–"]];
    cells.forEach(([t, c]) => tr.append(el("td", String(t), c)));
    const st = el("td"); const pill = el("span", m.status, "pill"); if (m.note) pill.title = m.note; st.append(pill); tr.append(st);
    rows.append(tr);
  });
  drawMap(feats.features);
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- map preview (plain SVG; coordinates are scaled to fit, not reprojected)
const NS = "http://www.w3.org/2000/svg";
function drawMap(features) {
  const svg = $("map"); svg.replaceChildren();
  const shapes = []; let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const track = (c) => { minX = Math.min(minX, c[0]); maxX = Math.max(maxX, c[0]); minY = Math.min(minY, c[1]); maxY = Math.max(maxY, c[1]); };
  const walk = (g) => {
    if (!g) return;
    if (g.type === "GeometryCollection") return g.geometries.forEach(walk);
    const t = g.type, c = g.coordinates;
    if (t === "Point") { track(c); shapes.push({ k: "pt", p: [c] }); }
    else if (t === "MultiPoint") c.forEach((p) => { track(p); shapes.push({ k: "pt", p: [p] }); });
    else if (t === "LineString") { c.forEach(track); shapes.push({ k: "line", p: c }); }
    else if (t === "MultiLineString") c.forEach((l) => { l.forEach(track); shapes.push({ k: "line", p: l }); });
    else if (t === "Polygon") c.forEach((r) => { r.forEach(track); shapes.push({ k: "poly", p: r }); });
    else if (t === "MultiPolygon") c.forEach((poly) => poly.forEach((r) => { r.forEach(track); shapes.push({ k: "poly", p: r }); }));
  };
  features.forEach((f) => walk(f.geometry));
  svg.style.display = shapes.length ? "" : "none";
  if (!shapes.length) return;
  const W = 600, H = 340, pad = 20;
  const sc = Math.min((W - 2 * pad) / ((maxX - minX) || 1), (H - 2 * pad) / ((maxY - minY) || 1));
  const ox = (W - (maxX - minX) * sc) / 2, oy = (H - (maxY - minY) * sc) / 2;
  const X = (x) => ox + (x - minX) * sc, Y = (y) => H - (oy + (y - minY) * sc);
  shapes.forEach((s) => {
    if (s.k === "pt") { const n = document.createElementNS(NS, "circle"); n.setAttribute("cx", X(s.p[0][0])); n.setAttribute("cy", Y(s.p[0][1])); n.setAttribute("r", 4); svg.append(n); return; }
    const n = document.createElementNS(NS, s.k === "poly" ? "polygon" : "polyline");
    n.setAttribute("points", s.p.map((c) => X(c[0]).toFixed(2) + "," + Y(c[1]).toFixed(2)).join(" "));
    n.setAttribute("class", s.k); svg.append(n);
  });
}

// ---- CSV export
$("csv").addEventListener("click", () => {
  if (!current) return;
  const head = ["feature_index", "layer", "geometry_type", "status", "area_sq_m", "area_hectares", "perimeter_m", "length_m", "geodesic_area_sq_m", "geodesic_length_m", "projected_crs", "note"];
  const esc = (v) => { let s = v == null ? "" : String(v); if (/^[=+\-@]/.test(s)) s = "'" + s; return '"' + s.replace(/"/g, '""') + '"'; };
  const csv = [head.join(",")].concat(current.meas.measurements.map((m) => head.map((h) => esc(m[h])).join(","))).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = current.file.filename.replace(/\.[^.]+$/, "") + "_measurements.csv";
  a.click(); URL.revokeObjectURL(a.href);
});

// ---- history
async function loadHistory() {
  const ul = $("history"); ul.replaceChildren();
  try {
    const files = await api("/api/files/?limit=50");
    if (!files.length) { ul.append(el("li", "Nothing uploaded yet.")); return; }
    files.forEach((f) => {
      const li = el("li"), info = el("div", undefined, "info");
      info.append(el("b", f.filename), el("small", new Date(f.created_at).toLocaleString() + " · " + f.feature_count + " features · " + f.status + (f.error_message ? " – " + f.error_message : "")));
      if (f.status !== "COMPLETED") info.classList.add("fail");
      const acts = el("div", undefined, "acts");
      if (f.status === "COMPLETED") { const o = el("button", "Open", "ghost"); o.type = "button"; o.onclick = () => show(f.id).catch((e) => setMsg(e.message, true)); acts.append(o); }
      const d = el("button", "Delete", "danger"); d.type = "button";
      d.onclick = async () => { if (!confirm("Delete " + f.filename + "?")) return; try { await api("/api/files/" + f.id + "/", { method: "DELETE" }); if (current && current.file.id === f.id) { $("result").hidden = true; current = null; } loadHistory(); } catch (e) { setMsg(e.message, true); } };
      acts.append(d); li.append(info, acts); ul.append(li);
    });
  } catch (err) { ul.append(el("li", err.message, "fail")); }
}
$("refresh").addEventListener("click", loadHistory);
$("apiKey").addEventListener("change", loadHistory);
loadHistory();