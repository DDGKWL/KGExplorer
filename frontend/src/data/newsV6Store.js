import { reactive } from 'vue';

// ---------------------------------------------------------------------------
// newsV6Store
// Client-side reactive store for the news knowledge-graph case study.
//
// Phase 1 additions (manual graph correction, session-scoped only):
//   - selectionIds: multi-node selection built with Ctrl/Cmd/Shift + click
//   - mergeNodes()/splitNode(): in-memory graph + comparisons transforms
//   - undoStack: full snapshots taken before each transform
// Nothing here touches the backend; a page reload restores pipeline output.
// ---------------------------------------------------------------------------

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function isEventId(id) {
  return String(id || '').startsWith('event:');
}

export function edgeKey(edge) {
  return `${edge.source}|${edge.target}|${edge.relation || ''}`;
}

export const newsV6Store = reactive({
  loading: false,
  loaded: false,
  error: '',
  graph: { nodes: [], edges: [], event_links: [], summary: {} },
  comparisons: { events: [], summary: {} },
  // DORIS/FrameNet framing sidecar (third judgment method, additive to the
  // rule status). Empty until /api/news/framing-verdicts responds.
  framingVerdicts: {},
  selectedEventId: null,
  selectedEntityId: null,
  // manual graph correction state (Phase 1)
  selectionIds: [],
  mergeDialogOpen: false,
  splitTargetId: null,
  lastAction: '',
  undoStack: [],
  // Context of the last merge/split/undo, written just before finishOp so the
  // graph view's watcher sees { new graph, new op } in the same flush and can
  // play its liquid transition effect. merge: intoId/fromIds; split: sourceId/newId.
  lastGraphOp: null,
});

let toastTimer = null;

function toast(message) {
  newsV6Store.lastAction = message;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    newsV6Store.lastAction = '';
  }, 4000);
}

export async function loadNewsV6() {
  newsV6Store.loading = true;
  newsV6Store.error = '';
  try {
    const [graphResponse, comparisonResponse, framingResponse] = await Promise.all([
      fetch('/api/news/v6/graph'),
      fetch('/api/news/v6/comparisons'),
      // The framing sidecar is optional: failures just leave slots rule-colored.
      fetch('/api/news/framing-verdicts').catch(() => null),
    ]);
    if (!graphResponse.ok || !comparisonResponse.ok) {
      throw new Error(`API graph=${graphResponse.status} comparisons=${comparisonResponse.status}`);
    }
    newsV6Store.graph = await graphResponse.json();
    newsV6Store.comparisons = await comparisonResponse.json();
    newsV6Store.framingVerdicts = framingResponse?.ok
      ? (await framingResponse.json())?.verdicts || {}
      : {};
    const first = newsV6Store.comparisons.events.find((event) => Number(event.report_count) > 1)
      || newsV6Store.comparisons.events[0];
    if (!newsV6Store.selectedEventId) newsV6Store.selectedEventId = first?.event_id || null;
    newsV6Store.loaded = true;
  } catch (error) {
    newsV6Store.error = String(error);
  } finally {
    newsV6Store.loading = false;
  }
}

export function selectNewsEvent(eventId) {
  newsV6Store.selectedEventId = eventId;
  newsV6Store.selectedEntityId = null;
}

export function selectNewsEntity(entityId) {
  newsV6Store.selectedEntityId = entityId;
}

// ---------------------------------------------------------------------------
// DORIS framing verdicts (third judgment method, additive to rule + LLM).
// newsV6Store derives its own key + lookup instead of importing store.js:
// store.js already imports this module, so a reverse import would create a
// circular dependency. The derivation must stay identical on both sides.
// ---------------------------------------------------------------------------

export function framingVerdictKey(eventId, comparison) {
  // Must match llm_judge.verdict_key() in backend/llm_judge.py exactly:
  // event_id::attribute::sorted "report_id:canonical" joined by ';'.
  const parts = [];
  Object.entries(comparison?.reports || {}).forEach(([reportId, items]) => {
    (items || []).forEach((item) => {
      const canonical = String(item?.canonical || '').trim().toLowerCase();
      if (canonical) parts.push(`${reportId}:${canonical}`);
    });
  });
  parts.sort();
  return `${eventId}::${comparison?.attribute}::${parts.join(';')}`;
}

export function framingVerdictFor(event, comparison) {
  if (!event || !comparison) return null;
  return newsV6Store.framingVerdicts[framingVerdictKey(event.event_id, comparison)] || null;
}

// ---------------------------------------------------------------------------
// Multi-node selection (Ctrl/Cmd/Shift + click on graph nodes)
// ---------------------------------------------------------------------------

export function toggleNewsNodeSelection(nodeId) {
  const ids = newsV6Store.selectionIds;
  const index = ids.indexOf(nodeId);
  if (index >= 0) ids.splice(index, 1);
  else ids.push(nodeId);
}

export function clearNewsSelection() {
  newsV6Store.selectionIds = [];
}

export function openNewsMergeDialog() {
  const store = newsV6Store;
  if (store.selectionIds.length < 2) return;
  store.mergeDialogOpen = true;
}

export function closeNewsMergeDialog() {
  newsV6Store.mergeDialogOpen = false;
}

export function openNewsSplitDialog() {
  const store = newsV6Store;
  store.splitTargetId = store.selectionIds[0]
    || store.selectedEventId
    || store.selectedEntityId
    || null;
}

export function closeNewsSplitDialog() {
  newsV6Store.splitTargetId = null;
}

// ---------------------------------------------------------------------------
// Undo support: deep snapshot of graph + comparisons before each transform.
// The graph is small (hundreds of nodes), so full copies are cheap.
// ---------------------------------------------------------------------------

function pushUndo(label) {
  const store = newsV6Store;
  store.undoStack.push({
    label,
    graph: clone(store.graph),
    comparisons: clone(store.comparisons),
    selectedEventId: store.selectedEventId,
    selectedEntityId: store.selectedEntityId,
    selectionIds: [...store.selectionIds],
  });
  if (store.undoStack.length > 30) store.undoStack.shift();
}

export function undoLastAction() {
  const store = newsV6Store;
  const previous = store.undoStack.pop();
  if (!previous) return;
  store.graph = previous.graph;
  store.comparisons = previous.comparisons;
  store.selectedEventId = previous.selectedEventId;
  store.selectedEntityId = previous.selectedEntityId;
  store.selectionIds = previous.selectionIds || [];
  store.mergeDialogOpen = false;
  store.splitTargetId = null;
  store.lastGraphOp = { type: 'undo', at: Date.now() };
  toast(`Undone: ${previous.label}`);
}

// Shared bookkeeping after every transform: close dialogs, clear selection,
// and reassign graph/comparisons so reference-based watchers re-render.
function finishOp(message) {
  const store = newsV6Store;
  store.selectionIds = [];
  store.mergeDialogOpen = false;
  store.splitTargetId = null;
  store.graph = { ...store.graph };
  store.comparisons = { ...store.comparisons };
  toast(message);
}

// ---------------------------------------------------------------------------
// Transform helpers
// ---------------------------------------------------------------------------

// Union of parallel edges that share source|target|relation.
function mergeParallelEdges(edges) {
  const seen = new Map();
  const result = [];
  for (const edge of edges) {
    const key = edgeKey(edge);
    const existing = seen.get(key);
    if (!existing) {
      seen.set(key, edge);
      result.push(edge);
      continue;
    }
    existing.actions = [...new Set([...(existing.actions || []), ...(edge.actions || [])])];
    existing.report_ids = [...new Set([...(existing.report_ids || []), ...(edge.report_ids || [])])];
    if (!Array.isArray(existing.evidence)) existing.evidence = [];
    const evidenceTexts = new Set(existing.evidence.map((item) => JSON.stringify(item)));
    for (const item of edge.evidence || []) {
      const serialized = JSON.stringify(item);
      if (!evidenceTexts.has(serialized)) {
        evidenceTexts.add(serialized);
        existing.evidence.push(item);
      }
    }
  }
  return result;
}

// event_links dedupe (source|target|entity_id) after rewiring.
function dedupeEventLinks(links) {
  const seen = new Set();
  const result = [];
  for (const link of links) {
    const key = `${link.source}|${link.target}|${link.entity_id || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(link);
  }
  return result;
}

function recountEntityEvents(graph, nodeId) {
  const node = graph.nodes.find((item) => item.id === nodeId);
  if (!node || node.type === 'event') return;
  const events = new Set();
  for (const edge of graph.edges) {
    if (edge.source === nodeId && isEventId(edge.target)) events.add(edge.target);
    if (edge.target === nodeId && isEventId(edge.source)) events.add(edge.source);
  }
  node.event_count = events.size;
}

// Fallback when an event has no comparisons entry: derive report_count from
// the union of report_ids carried by the event's edges.
function recountEventReports(graph, nodeId) {
  const node = graph.nodes.find((item) => item.id === nodeId);
  if (!node || node.type !== 'event') return;
  const reports = new Set();
  for (const edge of graph.edges) {
    if (edge.source !== nodeId && edge.target !== nodeId) continue;
    for (const reportId of edge.report_ids || []) reports.add(reportId);
  }
  if (reports.size) node.report_count = reports.size;
}

function refreshSummary(graph) {
  if (!graph.summary) return;
  graph.summary.events = graph.nodes.filter((node) => node.type === 'event').length;
  graph.summary.entities = graph.nodes.filter((node) => node.type !== 'event').length;
  graph.summary.event_entity_edges = graph.edges.length;
  graph.summary.cross_event_links = (graph.event_links || []).length;
}

// Fold an absorbed event's comparisons entry (reports + attribute maps) into
// the surviving event's entry.
function mergeEventEntry(survivor, absorbed) {
  const known = new Set((survivor.reports || []).map((report) => report.report_id));
  survivor.reports = survivor.reports || [];
  for (const report of absorbed.reports || []) {
    if (!known.has(report.report_id)) {
      survivor.reports.push(report);
      known.add(report.report_id);
    }
  }
  survivor.comparisons = survivor.comparisons || [];
  for (const comparison of absorbed.comparisons || []) {
    let target = survivor.comparisons.find((item) => item.attribute === comparison.attribute);
    if (!target) {
      target = { attribute: comparison.attribute, status: comparison.status, reports: {} };
      survivor.comparisons.push(target);
    }
    for (const [reportId, values] of Object.entries(comparison.reports || {})) {
      const merged = Array.isArray(target.reports[reportId]) ? target.reports[reportId] : [];
      for (const value of values || []) {
        if (!merged.some((existing) => sameValue(existing, value))) merged.push(value);
      }
      target.reports[reportId] = merged;
    }
  }
}

function mergeEventComparisons(survivorId, absorbedIds, survivorLabel) {
  const events = newsV6Store.comparisons.events || [];
  let survivor = events.find((item) => item.event_id === survivorId) || null;
  for (const absorbedId of absorbedIds) {
    const index = events.findIndex((item) => item.event_id === absorbedId);
    if (index < 0) continue;
    const absorbed = events[index];
    if (!survivor) {
      // Survivor had no comparisons entry: adopt the absorbed one under the
      // survivor's id, then keep merging any remaining absorbed entries in.
      survivor = absorbed;
      survivor.event_id = survivorId;
      if (survivorLabel) survivor.canonical_name = survivorLabel;
      continue;
    }
    mergeEventEntry(survivor, absorbed);
    events.splice(index, 1);
  }
  if (survivor) {
    survivor.report_count = (survivor.reports || []).length;
    survivor.user_adjusted = true;
  }
  if (newsV6Store.comparisons.summary) {
    newsV6Store.comparisons.summary.reports = events.reduce(
      (sum, item) => sum + (item.reports || []).length,
      0,
    );
    newsV6Store.comparisons.summary.events = events.length;
  }
}

// Edge evidence arrays are plain sentences (not keyed by report), so a split
// edge cannot carry an attributable subset: sentences stay on the original
// edge and the moved copy starts with an empty evidence list.
function splitEdgeEvidence(evidence) {
  return [Array.isArray(evidence) ? evidence : [], []];
}
// ---------------------------------------------------------------------------
// Merge: redirect absorbed nodes' edges into the survivor, drop self-loops,
// union parallel edges, rewire cross-event links, keep absorbed labels as
// aliases, recompute counts, and fold absorbed event comparisons in.
// ---------------------------------------------------------------------------

export function mergeNodes({ intoId, fromIds, label, note }) {
  const store = newsV6Store;
  const graph = store.graph;
  const survivor = (graph.nodes || []).find((node) => node.id === intoId);
  const absorbedIds = (fromIds || []).filter((id) => id && id !== intoId);
  if (!survivor || !absorbedIds.length) return;
  pushUndo('merge nodes');

  const absorbedSet = new Set(absorbedIds);
  const absorbedNodes = graph.nodes.filter((node) => absorbedSet.has(node.id));

  // 1. redirect edges and drop self-loops
  for (const edge of graph.edges) {
    if (absorbedSet.has(edge.source)) edge.source = intoId;
    if (absorbedSet.has(edge.target)) edge.target = intoId;
  }
  graph.edges = graph.edges.filter((edge) => edge.source !== edge.target);
  // 2. union parallel edges created by the redirect
  graph.edges = mergeParallelEdges(graph.edges);

  // 3. rewire cross-event links and dedupe
  if (Array.isArray(graph.event_links)) {
    for (const link of graph.event_links) {
      if (absorbedSet.has(link.source)) link.source = intoId;
      if (absorbedSet.has(link.target)) link.target = intoId;
    }
    graph.event_links = dedupeEventLinks(
      graph.event_links.filter((link) => link.source !== link.target),
    );
  }

  // 4. remove absorbed nodes, keep their labels as aliases
  graph.nodes = graph.nodes.filter((node) => !absorbedSet.has(node.id));
  survivor.aliases = [...new Set([
    ...(survivor.aliases || []),
    ...absorbedNodes.map((node) => node.label).filter(Boolean),
  ])].filter((alias) => alias !== survivor.label);
  if (label) survivor.label = label;
  survivor.user_adjusted = true;

  // 5. recompute counts
  if (survivor.type === 'event') {
    const hasEntry = (store.comparisons.events || []).some((item) => item.event_id === intoId);
    if (!hasEntry) {
      survivor.report_count = (survivor.report_count || 0)
        + absorbedNodes.reduce((sum, node) => sum + (node.report_count || 0), 0);
    }
    mergeEventComparisons(intoId, absorbedIds, survivor.label);
  }
  for (const node of graph.nodes) {
    if (node.type !== 'event') recountEntityEvents(graph, node.id);
  }
  refreshSummary(graph);

  // 6. keep any single-selection pointing at an absorbed node consistent
  if (absorbedSet.has(store.selectedEventId)) store.selectedEventId = intoId;
  if (absorbedSet.has(store.selectedEntityId)) store.selectedEntityId = intoId;
  store.lastGraphOp = {
    type: 'merge',
    intoId,
    fromIds: absorbedIds,
    kind: survivor.type,
    at: Date.now(),
  };
  finishOp(`Merged ${absorbedIds.length + 1} ${survivor.type === 'event' ? 'event' : 'entity'} nodes${note ? `: ${note}` : ''}`);
}

// ---------------------------------------------------------------------------
// Split: create a new node and move user-picked edges (or, for events with a
// comparisons entry, user-picked reports plus the report-bearing edges).
// New ids are prefixed split: / event:split- so they can never collide with
// stable pipeline ids (matters for backend persistence in Phase 2).
// ---------------------------------------------------------------------------

export function splitNode({ label, edges = [], reportIds = [], note }) {
  const store = newsV6Store;
  const graph = store.graph;
  const nodeId = store.splitTargetId;
  const node = (graph.nodes || []).find((item) => item.id === nodeId);
  if (!node || !label) return;
  const isEvent = node.type === 'event';
  const eventEntry = isEvent
    ? (store.comparisons.events || []).find((item) => item.event_id === nodeId)
    : null;

  // Event split by reports: the original event must keep at least one report.
  if (isEvent && eventEntry) {
    const movedIds = new Set(reportIds || []);
    const allReports = eventEntry.reports || [];
    const movedReports = allReports.filter((report) => movedIds.has(report.report_id));
    const keptReports = allReports.filter((report) => !movedIds.has(report.report_id));
    if (!movedReports.length || !keptReports.length) return;
    pushUndo('split event');

    const newId = `event:split-${Date.now().toString(36)}`;
    graph.nodes.push({
      id: newId,
      type: 'event',
      label,
      event_type: node.event_type || eventEntry.event_type,
      report_count: movedReports.length,
      user_adjusted: true,
    });

    for (const edge of graph.edges) {
      if (edge.source !== nodeId && edge.target !== nodeId) continue;
      const movedForEdge = (edge.report_ids || []).filter((id) => movedIds.has(id));
      if (!movedForEdge.length) continue;
      const keptForEdge = (edge.report_ids || []).filter((id) => !movedIds.has(id));
      if (!keptForEdge.length) {
        // whole edge moves to the new event
        if (edge.source === nodeId) edge.source = newId;
        else edge.target = newId;
        continue;
      }
      const [keptEvidence, movedEvidence] = splitEdgeEvidence(edge.evidence);
      edge.report_ids = keptForEdge;
      edge.evidence = keptEvidence;
      const movedEdge = { ...edge, report_ids: movedForEdge, evidence: movedEvidence };
      if (edge.source === nodeId) movedEdge.source = newId;
      else movedEdge.target = newId;
      graph.edges.push(movedEdge);
    }

    eventEntry.reports = keptReports;
    eventEntry.report_count = keptReports.length;
    eventEntry.user_adjusted = true;
    // snapshot the moved reports' attribute values BEFORE stripping them from
    // the original entry's comparison maps
    const carriedComparisons = (eventEntry.comparisons || []).map((comparison) => ({
      attribute: comparison.attribute,
      status: comparison.status,
      reports: Object.fromEntries(
        Object.entries(comparison.reports || {}).filter(([reportId]) => movedIds.has(reportId)),
      ),
    }));
    for (const comparison of eventEntry.comparisons || []) {
      const filtered = {};
      for (const [reportId, values] of Object.entries(comparison.reports || {})) {
        if (!movedIds.has(reportId)) filtered[reportId] = values;
      }
      comparison.reports = filtered;
    }
    store.comparisons.events.push({
      event_id: newId,
      canonical_name: label,
      event_type: node.event_type || eventEntry.event_type,
      report_count: movedReports.length,
      times: eventEntry.times,
      reports: movedReports,
      user_adjusted: true,
      comparisons: carriedComparisons,
    });
    node.user_adjusted = true;
    refreshSummary(graph);
    store.lastGraphOp = { type: 'split', sourceId: nodeId, newId, kind: 'event', at: Date.now() };
    finishOp(`Split ${movedReports.length} reports into "${label}"${note ? `: ${note}` : ''}`);
    return;
  }

  // Edge-based split (entity nodes and events without a comparisons entry):
  // move the picked edges to a new node, keep the rest on the original.
  if (!edges.length) return;
  pushUndo('split node');
  const newId = isEvent
    ? `event:split-${Date.now().toString(36)}`
    : `split:${node.type}:${Date.now().toString(36)}`;
  const newNode = { id: newId, type: node.type, label, user_adjusted: true };
  if (isEvent) newNode.event_type = node.event_type;
  else {
    newNode.aliases = [];
    newNode.event_count = 0;
  }
  graph.nodes.push(newNode);
  const picked = new Set(edges);
  for (const edge of graph.edges) {
    if (!picked.has(edge)) continue;
    if (edge.source === nodeId) edge.source = newId;
    else if (edge.target === nodeId) edge.target = newId;
  }
  if (isEvent) {
    recountEventReports(graph, nodeId);
    recountEventReports(graph, newId);
  } else {
    recountEntityEvents(graph, nodeId);
    recountEntityEvents(graph, newId);
  }
  refreshSummary(graph);
  store.lastGraphOp = { type: 'split', sourceId: nodeId, newId, kind: node.type, at: Date.now() };
  finishOp(`Split ${edges.length} edge(s) into "${label}"${note ? `: ${note}` : ''}`);
}