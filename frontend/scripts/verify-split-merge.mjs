// Zero-dependency logic verification for newsV6Store.js (split / merge / undo).
// Run: node frontend/scripts/verify-split-merge.mjs
//
// The store is pure in-memory logic (its only import is `reactive` from vue),
// so this exercises the exact code paths the UI dialogs trigger, without a
// browser or backend. The network loader (loadNewsV6) is never called.
import assert from 'node:assert/strict';
import { copyFileSync, unlinkSync } from 'node:fs';

// Node (16.x) classifies plain .js files as CommonJS because frontend/package.json
// has no "type": "module", so the ESM store source cannot be imported directly.
// Verify a byte-identical temporary .mjs copy instead; it is removed on exit.
const storeSource = new URL('../src/data/newsV6Store.js', import.meta.url);
const storeCopy = new URL('./newsV6Store.test-copy.mjs', import.meta.url);
copyFileSync(storeSource, storeCopy);
process.on('exit', () => {
  try {
    unlinkSync(storeCopy);
  } catch {
    // best-effort cleanup
  }
});

const {
  newsV6Store,
  mergeNodes,
  splitNode,
  undoLastAction,
  toggleNewsNodeSelection,
  clearNewsSelection,
  openNewsMergeDialog,
  openNewsSplitDialog,
} = await import(storeCopy.href);

// --- Synthetic fixture: 2 events, 3 entities, 6 edges, 1 cross-event link ---
const baseGraph = {
  summary: {},
  nodes: [
    { id: 'event:a', type: 'event', label: 'Event A', event_type: 'meeting', report_count: 3 },
    { id: 'event:b', type: 'event', label: 'Event B', event_type: 'meeting', report_count: 2 },
    { id: 'person:p1', type: 'person', label: 'Alice', event_count: 2 },
    { id: 'person:p2', type: 'person', label: 'Bob', event_count: 2 },
    { id: 'org:o1', type: 'organization', label: 'UN HQ', event_count: 1 },
  ],
  edges: [
    { source: 'event:a', target: 'person:p1', relation: 'involves', report_ids: ['r1', 'r2'], evidence: [{ text: 'a1' }, { text: 'a2' }] },
    { source: 'event:a', target: 'person:p2', relation: 'involves', report_ids: ['r1'], evidence: [{ text: 'a3' }] },
    { source: 'event:a', target: 'org:o1', relation: 'held_at', report_ids: ['r2', 'r3'], evidence: [{ text: 'a4' }] },
    { source: 'event:b', target: 'person:p1', relation: 'involves', report_ids: ['r4'], evidence: [{ text: 'b1' }] },
    { source: 'event:b', target: 'person:p2', relation: 'involves', report_ids: ['r4', 'r5'], evidence: [{ text: 'b2' }] },
    { source: 'person:p1', target: 'org:o1', relation: 'member_of', report_ids: ['r1'], evidence: [] },
  ],
  event_links: [
    { source: 'event:a', target: 'event:b', entity_id: 'person:p1' },
  ],
};

const baseComparisons = {
  summary: { events: 2, reports: 5 },
  events: [
    {
      event_id: 'event:a',
      canonical_name: 'Event A',
      event_type: 'meeting',
      report_count: 3,
      reports: [
        { report_id: 'r1', domain: 'apnews.com', title: 'A1' },
        { report_id: 'r2', domain: 'reuters.com', title: 'A2' },
        { report_id: 'r3', domain: 'apnews.com', title: 'A3' },
      ],
      comparisons: [
        { attribute: 'time', status: 'consensus', reports: { r1: ['Monday'], r2: ['Monday'], r3: ['Monday'] } },
        { attribute: 'place', status: 'different', reports: { r1: ['New York'], r2: ['Geneva'], r3: ['New York'] } },
      ],
    },
    {
      event_id: 'event:b',
      canonical_name: 'Event B',
      event_type: 'meeting',
      report_count: 2,
      reports: [
        { report_id: 'r4', domain: 'xinhua.net', title: 'B1' },
        { report_id: 'r5', domain: 'bbc.com', title: 'B2' },
      ],
      comparisons: [
        { attribute: 'time', status: 'consensus', reports: { r4: ['Tuesday'], r5: ['Tuesday'] } },
        { attribute: 'place', status: 'different', reports: { r4: ['Berlin'], r5: ['Berlin'] } },
      ],
    },
  ],
};

function resetState() {
  newsV6Store.graph = JSON.parse(JSON.stringify(baseGraph));
  newsV6Store.comparisons = JSON.parse(JSON.stringify(baseComparisons));
  newsV6Store.undoStack = [];
  newsV6Store.selectionIds = [];
  newsV6Store.selectedEventId = null;
  newsV6Store.selectedEntityId = null;
  newsV6Store.mergeDialogOpen = false;
  newsV6Store.splitTargetId = null;
  newsV6Store.lastAction = '';
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

// Unwrapped (plain) view of the edges, keyed by source|target|relation. Values
// are copied out of the reactive store so deepStrictEqual compares plain data
// (Node's deepStrictEqual cannot match Vue reactive proxies against literals).
function edgeIndex() {
  const map = new Map();
  for (const edge of newsV6Store.graph.edges) {
    map.set(`${edge.source}|${edge.target}|${edge.relation}`, plain(edge));
  }
  return map;
}

function nodeById(id) {
  return newsV6Store.graph.nodes.find((node) => node.id === id);
}

// --- 1. Ctrl+click selection helpers ---------------------------------------
function testSelection() {
  resetState();
  toggleNewsNodeSelection('event:a');
  toggleNewsNodeSelection('event:b');
  assert.deepStrictEqual(plain(newsV6Store.selectionIds), ['event:a', 'event:b']);
  toggleNewsNodeSelection('event:a'); // deselect
  assert.deepStrictEqual(plain(newsV6Store.selectionIds), ['event:b']);
  clearNewsSelection();
  assert.deepStrictEqual(plain(newsV6Store.selectionIds), []);
  assert.equal(newsV6Store.undoStack.length, 0);
}

// --- 2. Merge: redirect + parallel union + comparisons fold + undo ----------
function testMerge() {
  resetState();
  toggleNewsNodeSelection('event:a');
  toggleNewsNodeSelection('event:b');
  openNewsMergeDialog(); // requires >= 2 selected
  assert.equal(newsV6Store.mergeDialogOpen, true);

  mergeNodes({ intoId: 'event:a', fromIds: ['event:b'], label: '', note: 'verification merge' });

  // absorbed node removed, label kept as alias, survivor label unchanged ('')
  assert.ok(!nodeById('event:b'), 'absorbed node removed');
  const survivor = nodeById('event:a');
  assert.equal(survivor.label, 'Event A');
  assert.ok(survivor.aliases.includes('Event B'), 'absorbed label becomes alias');
  assert.equal(survivor.user_adjusted, true);

  // edges: redirected into survivor, parallel edges unioned by report_ids,
  // evidence concatenated and deduped
  const edges = edgeIndex();
  assert.equal(newsV6Store.graph.edges.length, 4);
  const aP1 = edges.get('event:a|person:p1|involves');
  assert.deepStrictEqual(aP1.report_ids, ['r1', 'r2', 'r4']);
  assert.equal(aP1.evidence.length, 3);
  assert.deepStrictEqual(edges.get('event:a|person:p2|involves').report_ids, ['r1', 'r4', 'r5']);
  assert.deepStrictEqual(edges.get('event:a|org:o1|held_at').report_ids, ['r2', 'r3']);
  assert.deepStrictEqual(edges.get('person:p1|org:o1|member_of').report_ids, ['r1']);

  // cross-event link a—b became a self-loop and was dropped
  assert.equal(newsV6Store.graph.event_links.length, 0);

  // comparisons entries folded: reports unioned, attribute maps merged
  const entries = plain(newsV6Store.comparisons.events);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].event_id, 'event:a');
  assert.equal(entries[0].report_count, 5);
  assert.deepStrictEqual(entries[0].reports.map((r) => r.report_id), ['r1', 'r2', 'r3', 'r4', 'r5']);
  const time = entries[0].comparisons.find((c) => c.attribute === 'time');
  assert.deepStrictEqual(Object.keys(time.reports).sort(), ['r1', 'r2', 'r3', 'r4', 'r5']);
  assert.deepStrictEqual(time.reports.r4, ['Tuesday']);
  const place = entries[0].comparisons.find((c) => c.attribute === 'place');
  assert.equal(place.status, 'different');
  assert.deepStrictEqual(place.reports.r5, ['Berlin']);

  // entity event_counts recomputed against the rewired edges
  assert.equal(nodeById('person:p1').event_count, 1);
  assert.equal(nodeById('person:p2').event_count, 1);
  assert.equal(nodeById('org:o1').event_count, 1);

  // summary refreshed
  assert.equal(newsV6Store.graph.summary.events, 1);
  assert.equal(newsV6Store.graph.summary.entities, 3);
  assert.equal(newsV6Store.graph.summary.event_entity_edges, 4);
  assert.equal(newsV6Store.graph.summary.cross_event_links, 0);

  // op bookkeeping: undo snapshot, selection cleared, dialog closed, toast
  assert.equal(newsV6Store.undoStack.length, 1);
  assert.deepStrictEqual(plain(newsV6Store.selectionIds), []);
  assert.equal(newsV6Store.mergeDialogOpen, false);
  assert.match(newsV6Store.lastAction, /Merged 2 event nodes/, 'toast message published');

  // undo restores graph + comparisons exactly
  undoLastAction();
  assert.equal(JSON.stringify(newsV6Store.graph), JSON.stringify(baseGraph));
  assert.equal(JSON.stringify(newsV6Store.comparisons), JSON.stringify(baseComparisons));
  assert.equal(newsV6Store.undoStack.length, 0);
}

// --- 3. Split event by reports (mode 1) --------------------------------------
function testSplitEventByReports() {
  resetState();
  toggleNewsNodeSelection('event:a');
  openNewsSplitDialog();
  assert.equal(newsV6Store.splitTargetId, 'event:a');

  splitNode({ label: 'Event A2', reportIds: ['r3'], note: 'verification split' });

  // new event node created
  const newNodes = newsV6Store.graph.nodes.filter((n) => n.id.startsWith('event:split-'));
  assert.equal(newNodes.length, 1);
  const split = newNodes[0];
  assert.equal(split.label, 'Event A2');
  assert.equal(split.report_count, 1);
  assert.equal(split.event_type, 'meeting');

  // edge report_ids migrated; moved edge copies start with empty evidence
  const edges = edgeIndex();
  assert.equal(newsV6Store.graph.edges.length, 7); // a-o1 split in two
  assert.deepStrictEqual(edges.get('event:a|person:p1|involves').report_ids, ['r1', 'r2']);
  assert.deepStrictEqual(edges.get('event:a|person:p2|involves').report_ids, ['r1']);
  assert.deepStrictEqual(edges.get('event:a|org:o1|held_at').report_ids, ['r2']);
  assert.deepStrictEqual(edges.get('event:a|org:o1|held_at').evidence, [{ text: 'a4' }]);
  const moved = edges.get(`${split.id}|org:o1|held_at`);
  assert.ok(moved, 'edge branch carrying r3 moved to the new event');
  assert.deepStrictEqual(moved.report_ids, ['r3']);
  assert.deepStrictEqual(moved.evidence, []);

  // comparisons: original keeps r1/r2 (maps filtered), new entry carries r3
  const orig = plain(newsV6Store.comparisons.events.find((e) => e.event_id === 'event:a'));
  assert.equal(orig.report_count, 2);
  assert.deepStrictEqual(orig.reports.map((r) => r.report_id), ['r1', 'r2']);
  const origPlace = orig.comparisons.find((c) => c.attribute === 'place');
  assert.deepStrictEqual(Object.keys(origPlace.reports).sort(), ['r1', 'r2']);
  const carried = plain(newsV6Store.comparisons.events.find((e) => e.event_id === split.id));
  assert.ok(carried, 'new comparisons entry for split event');
  assert.equal(carried.canonical_name, 'Event A2');
  assert.equal(carried.report_count, 1);
  assert.deepStrictEqual(carried.reports.map((r) => r.report_id), ['r3']);
  assert.deepStrictEqual(carried.comparisons.find((c) => c.attribute === 'time').reports.r3, ['Monday']);
  assert.deepStrictEqual(carried.comparisons.find((c) => c.attribute === 'place').reports.r3, ['New York']);

  assert.equal(newsV6Store.undoStack.length, 1);
  assert.deepStrictEqual(plain(newsV6Store.selectionIds), []);
  undoLastAction();
  assert.equal(JSON.stringify(newsV6Store.graph), JSON.stringify(baseGraph));
  assert.equal(JSON.stringify(newsV6Store.comparisons), JSON.stringify(baseComparisons));
}

// --- 4. Split entity by edges (mode 2) ---------------------------------------
function testSplitEntityByEdges() {
  resetState();
  toggleNewsNodeSelection('person:p1');
  openNewsSplitDialog();
  assert.equal(newsV6Store.splitTargetId, 'person:p1');

  // pass the actual edge objects from the reactive graph (identity matters:
  // splitNode matches picked edges with a Set of object references)
  const p1Edges = newsV6Store.graph.edges.filter(
    (edge) => edge.source === 'person:p1' || edge.target === 'person:p1',
  );
  assert.equal(p1Edges.length, 3);
  const picked = [p1Edges[0], p1Edges[2]]; // a—p1 and p1—o1 (keeps b—p1 behind)

  splitNode({ label: 'Alice Prime', edges: picked, note: '' });

  const split = newsV6Store.graph.nodes.find((n) => n.id.startsWith('split:person:'));
  assert.ok(split, 'new entity node created');
  assert.equal(split.label, 'Alice Prime');
  assert.equal(split.type, 'person');

  const edges = edgeIndex();
  assert.equal(newsV6Store.graph.edges.length, 6);
  assert.ok(!edges.has('event:a|person:p1|involves'), 'picked edge migrated');
  assert.ok(edges.has(`event:a|${split.id}|involves`));
  assert.ok(edges.has(`${split.id}|org:o1|member_of`));
  assert.ok(edges.has('event:b|person:p1|involves'), 'unpicked edge untouched');

  // event_count recounted on both sides
  assert.equal(split.event_count, 1); // only event:a
  assert.equal(nodeById('person:p1').event_count, 1); // only event:b

  undoLastAction();
  assert.equal(JSON.stringify(newsV6Store.graph), JSON.stringify(baseGraph));
  assert.equal(nodeById('person:p1').event_count, 2);
}

// --- 5. Guard rails: invalid operations must be no-ops -----------------------
function testGuards() {
  resetState();
  const baseline = JSON.stringify([newsV6Store.graph, newsV6Store.comparisons]);

  // event split must leave at least one report on the original event
  toggleNewsNodeSelection('event:a');
  openNewsSplitDialog();
  splitNode({ label: 'All moved', reportIds: ['r1', 'r2', 'r3'] });
  assert.equal(newsV6Store.undoStack.length, 0, 'no undo pushed for rejected split');
  assert.equal(newsV6Store.graph.nodes.length, 5);

  // edge split with nothing picked
  toggleNewsNodeSelection('person:p1');
  openNewsSplitDialog();
  splitNode({ label: 'No edges', edges: [] });
  assert.equal(newsV6Store.graph.nodes.length, 5);

  // split without a target node
  newsV6Store.splitTargetId = null;
  splitNode({ label: 'Ghost', reportIds: ['r1'] });
  assert.equal(newsV6Store.graph.nodes.length, 5);

  // merge with no valid absorbed ids (self-merge filtered out)
  mergeNodes({ intoId: 'event:a', fromIds: ['event:a'] });
  assert.equal(newsV6Store.graph.nodes.length, 5);
  assert.equal(newsV6Store.undoStack.length, 0, 'no undo pushed for no-op merge');
  assert.equal(
    JSON.stringify([newsV6Store.graph, newsV6Store.comparisons]),
    baseline,
    'state untouched by every rejected operation',
  );
}

// --- runner ------------------------------------------------------------------
const tests = [
  ['Ctrl+click selection helpers (toggle / clear)', testSelection],
  ['Merge events: edge redirect, parallel union, comparisons fold, undo', testMerge],
  ['Split event by reports: edge migration, carried comparisons, undo', testSplitEventByReports],
  ['Split entity by edges: edge migration, event_count recount, undo', testSplitEntityByEdges],
  ['Guard rails: rejected operations leave state untouched', testGuards],
];

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`PASS  ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`FAIL  ${name}`);
    console.error(error && error.stack ? error.stack : error);
  }
}

console.log(
  failed
    ? `\n${failed} of ${tests.length} verification checks FAILED.`
    : `\nAll ${tests.length} split/merge verification checks passed.`,
);
process.exit(failed ? 1 : 0);