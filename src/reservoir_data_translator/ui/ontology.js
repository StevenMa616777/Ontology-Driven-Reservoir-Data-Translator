"use strict";

const palette = { reservoir_simulation: "#263b36", rock: "#ad694b", fluid: "#267c71", scal: "#988431", well: "#526ea6", schedule: "#8b5f91", condition: "#b36b79" };
const state = { data: null, selected: null, focus: null, domains: new Set(), relations: new Set(), query: "", transform: { x: 0, y: 0, scale: 1 }, positions: new Map() };
const el = Object.fromEntries(["explorer-shell","ontology-version","concept-search","search-results","domain-filters","relation-filters","concept-tree","graph-title","graph-count","graph-stage","ontology-graph","graph-viewport","graph-edges","graph-nodes","graph-status","concept-detail","reset-view","fit-graph","focus-neighborhood"].map(id => [id.replaceAll("-", "_"), document.getElementById(id)]));

function escapeHtml(value) { return String(value ?? "—").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;"); }
function domainColor(domain) { return palette[domain] || "#687670"; }
function byId(id) { return state.data.nodes.find(node => node.id === id); }
function visibleNodes() { const focused = state.focus ? neighborhood(state.focus) : null; return state.data.nodes.filter(node => (state.domains.has(node.domain) || node.id === "reservoir_simulation") && (!focused || focused.has(node.id))); }
function visibleEdges(nodes) { const ids = new Set(nodes.map(node => node.id)); return state.data.edges.filter(edge => ids.has(edge.source) && ids.has(edge.target) && state.relations.has(edge.type)); }
function nodeMatches(node) { const q = state.query.trim().toLocaleLowerCase(); return !q || [node.id,node.label,node.description,...node.aliases].join(" ").toLocaleLowerCase().includes(q); }

function buildControls() {
  const domains = [...new Set(state.data.nodes.filter(n => n.parent).map(n => n.domain))].sort();
  domains.forEach(domain => state.domains.add(domain));
  state.relations.add("parent");
  Object.keys(state.data.relationship_types).filter(type => type !== "parent").forEach(type => state.relations.add(type));
  el.domain_filters.innerHTML = domains.map(domain => `<label class="filter-item"><input type="checkbox" value="${escapeHtml(domain)}" checked><i class="domain-dot" style="background:${domainColor(domain)}"></i><span>${escapeHtml(domain)}</span><b>${state.data.nodes.filter(n => n.domain === domain).length}</b></label>`).join("");
  el.relation_filters.innerHTML = Object.entries(state.data.relationship_types).sort(([a]) => a === "parent" ? -1 : 1).map(([type, meta]) => `<label class="filter-item" title="${escapeHtml(meta.description)}"><input type="checkbox" value="${escapeHtml(type)}" checked><span>${escapeHtml(type)}</span><b>${state.data.edges.filter(e => e.type === type).length}</b></label>`).join("");
  el.domain_filters.addEventListener("change", event => { event.target.checked ? state.domains.add(event.target.value) : state.domains.delete(event.target.value); renderAll(); });
  el.relation_filters.addEventListener("change", event => { event.target.checked ? state.relations.add(event.target.value) : state.relations.delete(event.target.value); renderGraph(); });
}

function renderTree() {
  const children = new Map(); state.data.nodes.forEach(node => { const key = node.parent || ""; if (!children.has(key)) children.set(key, []); children.get(key).push(node); });
  const rows = [];
  function walk(parent, depth) { (children.get(parent) || []).sort((a,b) => a.id.localeCompare(b.id)).forEach(node => { if ((state.domains.has(node.domain) || !node.parent) && nodeMatches(node)) rows.push(`<button class="tree-node ${node.id === state.selected ? "is-selected" : ""}" data-id="${escapeHtml(node.id)}" style="padding-left:${8 + depth * 12}px" title="${escapeHtml(node.id)}">${escapeHtml(node.label)}</button>`); walk(node.id, depth + 1); }); }
  if (state.query) state.data.nodes.filter(node => (state.domains.has(node.domain) || !node.parent) && nodeMatches(node)).forEach(node => rows.push(`<button class="tree-node ${node.id === state.selected ? "is-selected" : ""}" data-id="${escapeHtml(node.id)}" style="padding-left:8px">${escapeHtml(node.label)}</button>`)); else walk("", 0);
  el.concept_tree.innerHTML = rows.join("") || '<span class="search-note">没有符合条件的 Concept</span>';
}

function layout(nodes) {
  const children = new Map(); nodes.forEach(n => { if (!children.has(n.parent)) children.set(n.parent, []); children.get(n.parent).push(n); });
  const roots = nodes.filter(n => !n.parent || !nodes.some(other => other.id === n.parent)); let leaf = 0;
  function place(node, depth) { const kids = (children.get(node.id) || []).sort((a,b) => a.id.localeCompare(b.id)); let x; if (!kids.length) x = 100 + leaf++ * 145; else { const childXs = kids.map(k => place(k, depth + 1)); x = childXs.reduce((a,b) => a+b,0) / childXs.length; } state.positions.set(node.id, { x, y: 90 + depth * 135 }); return x; }
  roots.sort((a,b) => a.id.localeCompare(b.id)).forEach(root => place(root, 0));
}

function renderGraph() {
  const nodes = visibleNodes(); const edges = visibleEdges(nodes); layout(nodes);
  const focusedIds = null;
  el.graph_edges.innerHTML = edges.map(edge => { const a = state.positions.get(edge.source), b = state.positions.get(edge.target); if (!a || !b) return ""; const muted = focusedIds && (!focusedIds.has(edge.source) || !focusedIds.has(edge.target)); const emphasis = state.selected && (edge.source === state.selected || edge.target === state.selected); return `<path class="graph-edge ${edge.type === "parent" ? "hierarchy" : "semantic"} ${muted ? "is-muted" : ""} ${emphasis ? "is-emphasis" : ""}" d="M ${a.x} ${a.y + 17} C ${a.x} ${(a.y+b.y)/2}, ${b.x} ${(a.y+b.y)/2}, ${b.x} ${b.y - 17}"><title>${escapeHtml(edge.source)} — ${escapeHtml(edge.type)} → ${escapeHtml(edge.target)}</title></path>`; }).join("");
  el.graph_nodes.innerHTML = nodes.map(node => { const p = state.positions.get(node.id); const muted = focusedIds && !focusedIds.has(node.id); const selected = node.id === state.selected; const match = state.query && nodeMatches(node); const root = !node.parent; const shape = node.value_type === "table" ? `<rect class="node-shape" x="-12" y="-12" width="24" height="24" rx="3" fill="${domainColor(node.domain)}"></rect>` : `<circle class="node-shape" r="${root ? 16 : node.value_type === "object" || node.value_type === "entity" ? 12 : 8}" fill="${domainColor(node.domain)}"></circle>`; return `<g class="graph-node ${muted ? "is-muted" : ""} ${selected ? "is-selected" : ""} ${match ? "is-match" : ""}" data-id="${escapeHtml(node.id)}" transform="translate(${p.x} ${p.y})">${shape}<text x="0" y="30" text-anchor="middle">${escapeHtml(node.label.length > 27 ? node.label.slice(0,25) + "…" : node.label)}</text><text class="node-type" x="0" y="41" text-anchor="middle">${escapeHtml(node.value_type)}</text><title>${escapeHtml(node.id)}\n${escapeHtml(node.description)}</title></g>`; }).join("");
  el.graph_count.textContent = `${nodes.length} concepts · ${edges.length} visible relations`;
  el.graph_title.textContent = state.focus ? `${byId(state.focus)?.label || state.focus} · 邻域` : "完整图谱";
  el.graph_status.hidden = nodes.length > 0;
  if (!nodes.length) el.graph_status.textContent = "当前筛选条件下没有节点";
  updateTransform();
}

function neighborhood(id) { const ids = new Set([id]); state.data.edges.forEach(edge => { if (edge.source === id) ids.add(edge.target); if (edge.target === id) ids.add(edge.source); }); return ids; }
function selectConcept(id, updateUrl = true) { const node = byId(id); if (!node) return; state.selected = id; state.focus = id; el.focus_neighborhood.disabled = false; if (updateUrl) { const url = new URL(location.href); url.searchParams.set("concept", id); history.replaceState(null, "", url); } renderDetail(node); renderTree(); renderGraph(); requestAnimationFrame(fitGraph); }
function renderRelationRows(node) { const rows = []; Object.entries(node.relationships).forEach(([type, targets]) => targets.forEach(target => rows.push([type, target]))); node.incoming_relationships.forEach(rel => rows.push([`← ${rel.type}`, rel.source])); return rows.length ? rows.map(([type,target]) => `<div class="relation-row"><b>${escapeHtml(type)}</b><button type="button" data-id="${escapeHtml(target)}">${escapeHtml(target)}</button></div>`).join("") : '<span class="search-note">无声明关系</span>'; }
function renderDetail(node) { const constraints = Object.keys(node.constraints).length ? Object.entries(node.constraints).map(([k,v]) => `<span class="chip">${escapeHtml(k)}: ${escapeHtml(v)}</span>`).join("") : '<span class="search-note">无</span>'; el.concept_detail.innerHTML = `<div class="detail-header"><span class="detail-domain">${escapeHtml(node.domain)} · ${escapeHtml(node.status)}</span><h2>${escapeHtml(node.label)}</h2><div class="detail-id">${escapeHtml(node.id)}</div><p class="detail-description">${escapeHtml(node.description)}</p></div><div class="detail-grid"><div><span>Value type</span><strong>${escapeHtml(node.value_type)}</strong></div><div><span>Parent</span><strong>${escapeHtml(node.parent)}</strong></div><div><span>Dimension</span><strong>${escapeHtml(node.dimension)}</strong></div><div><span>Canonical unit</span><strong>${escapeHtml(node.canonical_unit)}</strong></div></div><div class="detail-section"><span>Aliases</span><div class="chip-list">${node.aliases.map(a => `<span class="chip">${escapeHtml(a)}</span>`).join("") || '<span class="search-note">无</span>'}</div></div><div class="detail-section"><span>Constraints</span><div class="chip-list">${constraints}</div></div><div class="detail-section"><span>Outgoing & Incoming relationships</span><div class="relation-list">${renderRelationRows(node)}</div></div><div class="detail-section"><span>Source</span><div class="detail-source">${escapeHtml(node.source_file)}</div></div>`; }

function renderSearch() { const q = state.query.trim(); if (!q) { el.search_results.innerHTML = ""; return; } const matches = state.data.nodes.filter(node => nodeMatches(node)).slice(0,8); el.search_results.innerHTML = matches.length ? matches.map(node => `<button class="search-result" data-id="${escapeHtml(node.id)}"><strong>${escapeHtml(node.label)}</strong><small>${escapeHtml(node.id)}</small></button>`).join("") : '<span class="search-note">没有匹配结果</span>'; }
function renderAll() { renderSearch(); renderTree(); renderGraph(); }
function updateTransform() { const t = state.transform; el.graph_viewport.setAttribute("transform", `translate(${t.x} ${t.y}) scale(${t.scale})`); }
function fitGraph() { const nodes = visibleNodes(); if (!nodes.length) return; const points = nodes.map(n => state.positions.get(n.id)); const minX = Math.min(...points.map(p=>p.x))-80, maxX = Math.max(...points.map(p=>p.x))+80, minY = Math.min(...points.map(p=>p.y))-60, maxY = Math.max(...points.map(p=>p.y))+70; const box = el.graph_stage.getBoundingClientRect(); const scale = Math.min(box.width/(maxX-minX), box.height/(maxY-minY), 1.25); state.transform = { scale, x: (box.width-(maxX-minX)*scale)/2-minX*scale, y: (box.height-(maxY-minY)*scale)/2-minY*scale }; updateTransform(); }

function bindEvents() {
  el.concept_search.addEventListener("input", event => { state.query = event.target.value; renderAll(); });
  document.addEventListener("click", event => { const target = event.target.closest("[data-id]"); if (target && byId(target.dataset.id)) selectConcept(target.dataset.id); });
  el.reset_view.addEventListener("click", () => { state.focus = null; state.query = ""; el.concept_search.value = ""; renderAll(); fitGraph(); });
  el.fit_graph.addEventListener("click", fitGraph);
  el.focus_neighborhood.addEventListener("click", () => { state.focus = state.focus === state.selected ? null : state.selected; renderGraph(); fitGraph(); });
  window.addEventListener("ui-readability-change", () => requestAnimationFrame(fitGraph));
  el.ontology_graph.addEventListener("wheel", event => { event.preventDefault(); const rect = el.ontology_graph.getBoundingClientRect(); const px = event.clientX-rect.left, py = event.clientY-rect.top; const next = Math.min(2.5, Math.max(.18, state.transform.scale * (event.deltaY < 0 ? 1.12 : .89))); state.transform.x = px-(px-state.transform.x)*next/state.transform.scale; state.transform.y = py-(py-state.transform.y)*next/state.transform.scale; state.transform.scale = next; updateTransform(); }, { passive:false });
  let drag = null; el.ontology_graph.addEventListener("pointerdown", event => { if (event.target.closest?.(".graph-node")) return; drag = { x:event.clientX, y:event.clientY, tx:state.transform.x, ty:state.transform.y }; el.ontology_graph.setPointerCapture(event.pointerId); el.ontology_graph.classList.add("is-panning"); });
  el.ontology_graph.addEventListener("pointermove", event => { if (!drag) return; state.transform.x = drag.tx + event.clientX-drag.x; state.transform.y = drag.ty + event.clientY-drag.y; updateTransform(); });
  el.ontology_graph.addEventListener("pointerup", () => { drag = null; el.ontology_graph.classList.remove("is-panning"); });
}

async function init() { try { const response = await fetch("/api/ontology/graph"); if (!response.ok) throw new Error(`HTTP ${response.status}`); state.data = await response.json(); el.ontology_version.textContent = `ONTOLOGY v${state.data.ontology.version}`; buildControls(); bindEvents(); const requested = new URL(location.href).searchParams.get("concept"); state.focus = requested && byId(requested) ? requested : "reservoir_simulation"; renderAll(); el.explorer_shell.setAttribute("aria-busy", "false"); if (requested && byId(requested)) selectConcept(requested, false); else requestAnimationFrame(fitGraph); } catch (error) { el.graph_status.textContent = `Ontology 加载失败：${error.message}`; el.graph_status.hidden = false; el.explorer_shell.setAttribute("aria-busy", "false"); } }
init();
