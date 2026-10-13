<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import * as d3 from 'd3';
import { store, CLASS_COLORS, worstClass, displayCls, isOpenConflict } from '../data/store.js';

import { newsV6Store } from '../data/newsV6Store.js';
import {
  appendGooeyFilter,
  burst,
  ensureFxLayer,
  gooGroup,
  OP_COLOR,
  reducedMotion,
  ripple,
} from '../data/graphFx.js';

const ARTIFACTS = store.artifacts;
const SOURCES = store.sources;

const props = defineProps({
  variant: { type: String, default: 'global' },
  selectedArtifactId: { type: String, default: null },
  selectedSlotId: { type: String, default: null },
  visibleArtifactIds: { type: Array, default: () => [] },
  // Case 3 · News: artifacts multi-selected for merge/split (amber rings).
  selectedArtifactIds: { type: Array, default: () => [] },
});

const emit = defineEmits(['select-artifact', 'select-slot', 'pin-artifact', 'toggle-select']);

const rootEl = ref(null);
let resizeObserver;
const posCache = new Map();


// ---------------------------------------------------------------------------
// Case 3 · News liquid transitions (state A → animation → state B).
// A fresh merge/split/undo op snapshots the pre-op geometry (state A); the
// next render computes state B headlessly as it always has, then plays the
// deterministic A→B transition instead of drawing the result directly. The
// op watcher below is registered ahead of the render triggers so the
// snapshot is taken before this render overwrites posCache with state B in
// the same flush.
// ---------------------------------------------------------------------------
let pendingTransition = null; // { op, snapshot: Map(posCache) } awaiting render
let playingRev = -1;          // store.adjudicationRev whose transition is on stage
let playingStartedAt = 0;     // bounds the redundant-render skip window
let lastPlayedOpAt = 0;       // freshness guard: play each op exactly once
let fxEpoch = 0;              // stale-callback bail-out across re-renders
let lastRenderedIds = new Set(); // previous render's node ids (undo pop-ins)

function ringArc(radius, startFrac, endFrac, pad = 0.03) {
  return d3.arc()
    .innerRadius(radius - 3)
    .outerRadius(radius + 3)
    .startAngle((startFrac + pad) * 2 * Math.PI)
    .endAngle((endFrac - pad) * 2 * Math.PI)();
}

function drawContentionGlyph(g, artifact, r) {
  const cls = worstClass(artifact);
  // The outer ring is a fixed provenance axis. The center encodes the
  // record's current evidence state; no unexplained source/count number is
  // placed inside the node.
  const sourceIds = Object.keys(SOURCES).length
    ? Object.keys(SOURCES)
    : (artifact.sources.length ? artifact.sources : ['unknown']);
  const n = sourceIds.length;
  const alignmentColor = {
    confirmed: '#5d8c82',
    candidate: '#b29a62',
    'source-local': '#aab4ba',
    unresolved: '#b86f3d',
  }[artifact.alignmentStatus] || '#aab4ba';
  sourceIds.forEach((sid, i) => {
    const present = artifact.sources.includes(sid);
    const involved = artifact.slots.some((s) =>
      s.assertions.some((a) => a.source === sid && a.raw));
    g.append('path')
      .attr('d', ringArc(r + 5, i / n, (i + 1) / n))
      .attr('fill', sid === 'unknown' || !present ? CLASS_COLORS.missing : involved ? (SOURCES[sid]?.color || alignmentColor) : '#d3dadd')
      .append('title')
      .text(`${present ? 'source present' : 'source absent'} · ${SOURCES[sid]?.title || sid}`);
  });
  const stateColor = CLASS_COLORS[cls] || CLASS_COLORS.missing;
  g.append('circle')
    .attr('r', r)
    .attr('fill', '#ffffff')
    .attr('stroke', stateColor)
    .attr('stroke-width', 1.8)
    .attr('stroke-dasharray', cls === 'A' ? '4 2.5' : null)
    .append('title')
    .text(`${artifact.name} · ${artifact.sources.length} source(s) · ${cls}${store.caseId === 'news' ? '\nCtrl+Click to multi-select for merge/split' : ''}`);
  g.append('circle')
    .attr('r', Math.max(6, r * 0.32))
    .attr('fill', stateColor)
    .attr('stroke', '#ffffff')
    .attr('stroke-width', 1);
  g.append('text')
    .attr('class', 'source-count')
    .attr('text-anchor', 'middle')
    .attr('dy', 3.5)
    .attr('fill', '#ffffff')
    .attr('font-size', Math.max(8, r * 0.54))
    .attr('font-weight', 800)
    .text(artifact.sources.length);
  g.append('text')
    .attr('class', 'source-count-label')
    .attr('x', r + 7)
    .attr('y', -r - 5)
    .attr('fill', '#5d6871')
    .attr('font-size', 8.5)
    .attr('font-weight', 700)
    .text(`${artifact.sources.length} src`);
}

function renderGlobal(container, width, height) {
  const svg = d3.select(container).append('svg')
    .attr('viewBox', [0, 0, width, height])
    .attr('role', 'img')
    .attr('aria-label', 'Global contested knowledge graph');

  // Keep the full corpus in the matrix and hierarchy. The graph starts with
  // objects that participate in cross-source matching or conflict analysis.
  const visibleIds = new Set(props.visibleArtifactIds);
  const hasActiveFilter = visibleIds.size > 0 && visibleIds.size < ARTIFACTS.length;
  const eligible = ARTIFACTS.filter((artifact) => {
    if (visibleIds.size && !visibleIds.has(artifact.id)) return false;
    return hasActiveFilter
      || artifact.featured
      || artifact.sources.length > 1
      || artifact.alignmentCandidates?.some((candidate) => candidate.score >= 0.45)
      || artifact.slots.some((slot) => isOpenConflict(slot));
  });
  const GRAPH_CAP = 200;
  const rank = (artifact) => {
    let score = 0;
    if (artifact.id === props.selectedArtifactId) score += 200;
    if (artifact.featured) score += 100;
    if (Object.keys(artifact.imagesByBook || {}).length) score += 40;
    if (artifact.slots.some((slot) => displayCls(slot) === 'A')) score += 10;
    return score;
  };
  const graphArtifacts = eligible.length <= GRAPH_CAP
    ? eligible
    : [...eligible]
      .sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name, 'zh-CN'))
      .slice(0, GRAPH_CAP);
  svg.append('text')
    .attr('x', 16)
    .attr('y', 20)
    .attr('fill', '#56636d')
    .attr('font-size', 10)
    .attr('font-weight', 700)
    .text(
      eligible.length > graphArtifacts.length
        ? `featured view · ${graphArtifacts.length} of ${ARTIFACTS.length} records (${eligible.length} contested)`
        : `cross-source view · ${graphArtifacts.length} of ${ARTIFACTS.length} records`,
    )
    .append('title')
    .text('The matrix and source folders retain the complete corpus; the global view foregrounds featured, imaged, or contested records.');
  const nodes = [];
  const nodeById = new Map();
  const links = [];
  const short = (value, max = 24) => {
    const text = String(value || '');
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  };
  const addNode = (nodeData) => {
    if (!nodeById.has(nodeData.id)) {
      nodeById.set(nodeData.id, nodeData);
      nodes.push(nodeData);
    }
    return nodeById.get(nodeData.id);
  };
  const sourceIds = Object.keys(SOURCES);

  graphArtifacts.forEach((artifact) => addNode({
    id: `object:${artifact.id}`,
    kind: 'object',
    artifact,
    label: artifact.name,
    r: 14 + Math.min(6, artifact.slots.filter((slot) => {
      const cls = displayCls(slot);
      return cls === 'A' || cls === 'B';
    }).length),
  }));

  // A value node is a normalized attribute value shared by at least two
  // artifact records. Singletons remain in the evidence matrix but do not
  // create noise in the global relational backbone.
  const sharedValues = new Map();
  graphArtifacts.forEach((artifact) => {
    artifact.slots.forEach((slot) => {
      slot.assertions.forEach((assertion) => {
        const values = assertion.normalizedValues?.length ? assertion.normalizedValues : assertion.values;
        values.forEach((value, valueIndex) => {
          const canonical = String(value || '').replace(/\s+/g, ' ').trim();
          if (!canonical || canonical.length > 120) return;
          const key = `${slot.id}:${canonical.toLowerCase()}`;
          const group = sharedValues.get(key) || {
            key,
            slot,
            label: canonical,
            artifacts: new Set(),
            recordsByArtifact: new Map(),
            rawLabels: new Set(),
          };
          group.artifacts.add(artifact.id);
          const sources = group.recordsByArtifact.get(artifact.id) || new Set();
          sources.add(assertion.source);
          group.recordsByArtifact.set(artifact.id, sources);
          const detail = assertion.valueDetails?.find((item) => item.canonical === value);
          if (detail?.raw) group.rawLabels.add(detail.raw);
          else if (assertion.values?.[valueIndex]) group.rawLabels.add(assertion.values[valueIndex]);
          sharedValues.set(key, group);
        });
      });
    });
  });
  sharedValues.forEach((group) => {
    if (group.artifacts.size < 2) return;
    const value = addNode({
      id: `value:${group.key}`,
      kind: 'value',
      label: group.label,
      slotLabel: group.slot.label,
      rawLabels: [...group.rawLabels],
      support: group.artifacts.size,
      r: 5 + Math.min(3, Math.sqrt(group.artifacts.size)),
    });
    group.recordsByArtifact.forEach((sourceSet, artifactId) => {
      const artifact = graphArtifacts.find((item) => item.id === artifactId);
      const slot = artifact?.slots.find((item) => item.id === group.slot.id);
      links.push({
        source: `object:${artifactId}`,
        target: value.id,
        kind: 'asserts value',
        slotLabel: group.slot.label,
        sources: [...sourceSet],
        status: displayCls(slot) || 'single',
      });
    });
  });

  nodes.forEach((n, i) => {
    const cached = posCache.get(`clean-kg-v1:${n.id}`);
    if (cached) {
      n.x = cached.x;
      n.y = cached.y;
    } else {
      const seed = (i * 9301 + 49297) % 233280;
      n.x = width * (0.24 + 0.52 * (seed / 233280));
      n.y = height * (0.24 + 0.52 * (((seed * 17) % 233280) / 233280));
    }
  });

  const graph = svg.append('g');
  const zoom = d3.zoom()
    .scaleExtent([0.12, 8])
    .on('zoom', (event) => graph.attr('transform', event.transform));
  svg.call(zoom).on('dblclick.zoom', null);
  const link = graph.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', (d) => d.status === 'A' ? CLASS_COLORS.A : d.status === 'B' ? CLASS_COLORS.B : '#aab8bf')
    .attr('stroke-opacity', (d) => d.status === 'A' || d.status === 'B' ? 0.48 : 0.28)
    .attr('stroke-width', (d) => d.status === 'A' ? 1.25 : 0.9);
  link.append('title').text((d) => `${d.kind} · ${d.slotLabel} · ${d.sources.map((source) => SOURCES[source]?.title || source).join(', ')}`);

  const node = graph.append('g').selectAll('g').data(nodes).join('g')
    .attr('cursor', (d) => (d.kind === 'object' ? 'pointer' : 'grab'))
    .on('click', (event, d) => {
      if (event.defaultPrevented) return;
      if (d.kind !== 'object') return;
      // Ctrl/Cmd/Shift+click builds a multi-selection used by the Case 3
      // merge/split toolbar; a plain click keeps the classic single-select.
      if (event.ctrlKey || event.metaKey || event.shiftKey) {
        emit('toggle-select', d.artifact.id);
        return;
      }
      emit('select-artifact', d.artifact.id);
    })
    .on('dblclick', (event, d) => {
      if (d.kind === 'object') emit('pin-artifact', d.artifact.id);
    })
    .on('mouseenter', function (event, d) {
      d3.select(this).select('.semantic-label').attr('opacity', 1);
    })
    .on('mouseleave', function (event, d) {
      if ((d.kind === 'object' && d.artifact?.id === props.selectedArtifactId)) return;
      d3.select(this).select('.semantic-label').attr('opacity', 0);
    });

  node.filter((d) => d.kind === 'object').each(function drawEach(d) {
    drawContentionGlyph(d3.select(this), d.artifact, d.r);
  });
  node.filter((d) => d.kind === 'object')
    .append('text').text((d) => short(d.label, 28))
    .attr('class', 'semantic-label artifact-label')
    .attr('x', (d) => d.r + 8).attr('y', 4)
    .attr('fill', '#4a4438').attr('font-size', 11).attr('font-weight', 600)
    .attr('paint-order', 'stroke').attr('stroke', '#ffffff').attr('stroke-width', 3)
    .attr('opacity', (d) => d.artifact.id === props.selectedArtifactId ? 1 : 0);
  node.filter((d) => d.kind === 'value')
    .append('circle').attr('r', (d) => d.r)
    .attr('fill', '#ffffff').attr('stroke', '#7f9aa7').attr('stroke-width', 1.2)
    .append('title').text((d) => `attribute value · ${d.slotLabel} · ${d.label} · ${d.support} artifact records`);
  node.filter((d) => d.kind === 'value')
    .append('text').text((d) => short(d.label, 20))
    .attr('class', 'semantic-label')
    .attr('x', (d) => d.r + 7)
    .attr('y', 4).attr('fill', '#8f6b2f').attr('font-size', 8.5).attr('font-weight', 600)
    .attr('paint-order', 'stroke').attr('stroke', '#ffffff').attr('stroke-width', 3)
    .attr('opacity', 0);

  node.filter((d) => d.kind === 'object')
    .append('circle')
    .attr('class', 'sel-ring')
    .attr('r', (d) => d.r + 9)
    .attr('fill', 'none')
    .attr('stroke', '#365c78')
    .attr('stroke-width', (d) => (d.artifact.id === props.selectedArtifactId ? 1.6 : 0))
    .attr('stroke-dasharray', '2 3');

  // Case 3 · News multi-selection ring (merge/split). Amber, solid —
  // visually distinct from the dashed blue single-focus ring above.
  node.filter((d) => d.kind === 'object')
    .append('circle')
    .attr('class', 'multi-ring')
    .attr('r', (d) => d.r + 4.5)
    .attr('fill', 'none')
    .attr('stroke', '#c8912b')
    .attr('stroke-width', (d) => (props.selectedArtifactIds.includes(d.artifact.id) ? 2.4 : 0));

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id)
      .distance(145)
      .strength(0.42))
    .force('charge', d3.forceManyBody()
      .strength((d) => d.kind === 'object' ? -460 : -150)
      .distanceMax(Math.max(width, height) * 3))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius((d) => d.kind === 'object' ? d.r + 14 : d.r + 7));

  function positionAll() {
    // Intentionally leave nodes outside the viewport; zoom and pan reveal them.
    link.attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
    node.attr('transform', (d) => `translate(${d.x},${d.y})`);
  }


  simulation.stop();
  simulation.tick(420);
  // Case 3 · News: a fresh merge/split/undo turns this render's headless
  // layout into state B and plays the liquid A→B transition instead of
  // drawing the result directly (see takeOpTransition / playOpTransition).
  const opTransition = takeOpTransition(nodes);
  if (opTransition) {
    playOpTransition({ svg, graph, link, node, transition: opTransition });
  } else {
    positionAll();
  }
  nodes.forEach((n) => posCache.set(`clean-kg-v1:${n.id}`, { x: n.x, y: n.y, r: n.r, label: n.label }));
  lastRenderedIds = new Set(nodes.map((n) => n.id));

  node.call(
    d3.drag()
      .filter((event) => event.button === 0)
      .on('start', (event) => {
        event.sourceEvent?.stopPropagation();
      })
      .on('drag', (event, d) => {
        event.sourceEvent?.stopPropagation();
        d.fx = event.x;
        d.fy = event.y;
        positionAll();
      })
      .on('end', (event, d) => {
        event.sourceEvent?.stopPropagation();
        d.fx = null;
        d.fy = null;
        posCache.set(`clean-kg-v1:${d.id}`, { x: d.x, y: d.y, r: d.r, label: d.label });
      }),
  );
}


// ---------------------------------------------------------------------------
// Case 3 · News: liquid transition playback (merge / split / undo).
// Both endpoint layouts are precomputed: state A comes from the pre-op
// posCache snapshot, state B is the layout this render just computed with
// simulation.stop() + tick(420). Nothing here runs a live simulation, so the
// animation always lands exactly on the final static interface.
// ---------------------------------------------------------------------------

// Consume the pending one-shot transition and resolve state-A geometry for
// the freshly computed node set. Returns null when there is nothing to
// animate (non-news case, reduced motion, or the involved nodes are not on
// this canvas).
function takeOpTransition(nodes) {
  const pending = pendingTransition;
  pendingTransition = null;
  if (!pending || store.caseId !== 'news' || reducedMotion.matches) return null;
  const { op, snapshot } = pending;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const artifactKey = (id) => `clean-kg-v1:object:${id}`;
  // State A of every node that already existed before the op; nodes without
  // an A position (brand-new value nodes) fade in as the topology settles.
  const aPositions = new Map();
  for (const n of nodes) {
    const a = snapshot.get(`clean-kg-v1:${n.id}`);
    if (a) aPositions.set(n.id, { x: a.x, y: a.y });
  }
  if (op.type === 'merge') {
    const survivor = byId.get(`object:${op.intoId}`);
    const absorbed = (op.fromIds || [])
      .map((id) => snapshot.get(artifactKey(id)))
      .filter(Boolean);
    if (!survivor || !absorbed.length) return null;
    return { op, byId, aPositions, survivor, absorbed };
  }
  if (op.type === 'split') {
    const born = byId.get(`object:${op.newId}`);
    if (!born) return null;
    const sourceA = snapshot.get(artifactKey(op.sourceId));
    // The new node is born on top of its source (or in place when the source
    // is filtered off this canvas).
    aPositions.set(born.id, sourceA
      ? { x: sourceA.x + 2.5, y: sourceA.y + 2.5 }
      : { x: born.x, y: born.y });
    return { op, byId, aPositions, born, sourceA };
  }
  return { op, byId, aPositions }; // undo: generic soft restore
}

function playOpTransition({ svg, graph, link, node, transition }) {
  const { aPositions, op } = transition;
  const epoch = ++fxEpoch;
  playingRev = store.adjudicationRev;
  playingStartedAt = Date.now();
  const DURATION = 640;

  appendGooeyFilter(svg.append('defs'));
  // The fx layer lives inside the zoomable group so it pans/zooms with the
  // content and stays aligned with data coordinates.
  const fx = ensureFxLayer(graph);

  // 1 · draw state A (nodes without a pre-op position fade in near the end)
  node.attr('transform', (d) => {
    const a = aPositions.get(d.id);
    return a ? `translate(${a.x},${a.y})` : `translate(${d.x},${d.y})`;
  });
  link
    .attr('x1', (d) => aPositions.get(d.source.id)?.x ?? d.source.x)
    .attr('y1', (d) => aPositions.get(d.source.id)?.y ?? d.source.y)
    .attr('x2', (d) => aPositions.get(d.target.id)?.x ?? d.target.x)
    .attr('y2', (d) => aPositions.get(d.target.id)?.y ?? d.target.y);
  node.filter((d) => !aPositions.has(d.id)).attr('opacity', 0);
  link.filter((d) => !aPositions.has(d.source.id) || !aPositions.has(d.target.id))
    .attr('opacity', 0);

  // 2 · everything tweens to state B (d.x/d.y are the post-op layout)
  node.transition('kg-op-move').duration(DURATION).ease(d3.easeCubicInOut)
    .attr('transform', (d) => `translate(${d.x},${d.y})`);
  link.transition('kg-op-move').duration(DURATION).ease(d3.easeCubicInOut)
    .attr('x1', (d) => d.source.x).attr('y1', (d) => d.source.y)
    .attr('x2', (d) => d.target.x).attr('y2', (d) => d.target.y);
  node.filter((d) => !aPositions.has(d.id))
    .transition('kg-op-enter').delay(DURATION * 0.7).duration(280).ease(d3.easeCubicOut)
    .attr('opacity', 1);
  link.filter((d) => !aPositions.has(d.source.id) || !aPositions.has(d.target.id))
    .transition('kg-op-enter').delay(DURATION * 0.7).duration(280).ease(d3.easeCubicOut)
    .attr('opacity', 1);

  // 3 · op-specific liquid choreography on the overlay
  if (op.type === 'merge') playMergeFx({ fx, node, transition, epoch, DURATION });
  else if (op.type === 'split') playSplitFx({ fx, node, transition, epoch, DURATION });
  else playUndoFx({ node, transition, DURATION });

  d3.timeout(() => {
    if (epoch === fxEpoch) playingRev = -1;
  }, DURATION + 720);
}

// MERGE: amber clones of the absorbed nodes fly into the survivor through the
// gooey filter, their labels linger and fade; on impact the survivor pulses
// (1.22x overshoot) over a particle burst and a ripple.
function playMergeFx({ fx, node, transition, epoch, DURATION }) {
  const { survivor, absorbed } = transition;
  const goo = gooGroup(fx);
  const ghosts = fx.append('g');
  for (const a of absorbed) {
    goo.append('circle')
      .attr('cx', a.x).attr('cy', a.y)
      .attr('r', Math.max(7, (a.r || 14) * 0.85))
      .attr('fill', OP_COLOR)
      .transition('kg-absorb').duration(DURATION - 40).ease(d3.easeCubicIn)
      .attr('cx', survivor.x).attr('cy', survivor.y)
      .attr('r', 2.5);
    if (a.label) {
      ghosts.append('text')
        .attr('x', a.x + 14).attr('y', a.y + 4)
        .attr('font-size', 10).attr('font-weight', 600)
        .attr('fill', '#5d6871')
        .attr('paint-order', 'stroke').attr('stroke', '#ffffff').attr('stroke-width', 3)
        .attr('opacity', 0.9)
        .text(a.label)
        .transition('kg-ghost').duration(DURATION - 60).ease(d3.easeCubicIn)
        .attr('opacity', 0);
    }
  }
  const survivorSel = node.filter((d) => d.id === survivor.id);
  d3.timeout(() => {
    if (epoch !== fxEpoch) return;
    goo.remove();
    ghosts.remove();
    ripple(fx, { x: survivor.x, y: survivor.y, r: survivor.r });
    burst(fx, { x: survivor.x, y: survivor.y });
    survivorSel
      .transition('kg-op-pulse').duration(170).ease(d3.easeCubicOut)
      .attr('transform', `translate(${survivor.x},${survivor.y}) scale(1.22)`)
      .transition('kg-op-settle').duration(470).ease(d3.easeCubicOut)
      .attr('transform', `translate(${survivor.x},${survivor.y}) scale(1)`);
  }, DURATION);
}

// SPLIT: the new node is born on top of its source inside the gooey layer.
// Both goo clones fly to their precomputed state-B spots so the liquid
// thread stretches deterministically; the real new node is revealed as the
// clone vanishes, exactly on its final position.
function playSplitFx({ fx, node, transition, epoch, DURATION }) {
  const { op, byId, aPositions, born, sourceA } = transition;
  const sourceNode = byId.get(`object:${op.sourceId}`);
  const bornA = aPositions.get(born.id) ?? { x: born.x, y: born.y };
  const srcA = sourceA ?? bornA;
  const baseR = Math.max(10, sourceNode?.r || born.r);
  // The real new node is hidden while its gooey clone stands in.
  const bornSel = node.filter((d) => d.id === born.id).attr('opacity', 0);
  const goo = gooGroup(fx);
  const sourceClone = goo.append('circle')
    .attr('cx', srcA.x).attr('cy', srcA.y)
    .attr('r', baseR)
    .attr('fill', OP_COLOR);
  const bornClone = goo.append('circle')
    .attr('cx', bornA.x).attr('cy', bornA.y).attr('r', 0)
    .attr('fill', OP_COLOR);
  ripple(fx, { x: srcA.x, y: srcA.y, r: baseR });
  bornClone
    .transition('kg-sprout').delay(80).duration(360).ease(d3.easeBackOut)
    .attr('r', born.r * 0.9);
  if (sourceNode) {
    sourceClone
      .transition('kg-stretch').duration(DURATION).ease(d3.easeCubicInOut)
      .attr('cx', sourceNode.x).attr('cy', sourceNode.y);
  }
  bornClone
    .transition('kg-separate').duration(DURATION).ease(d3.easeCubicInOut)
    .attr('cx', born.x).attr('cy', born.y);
  d3.timeout(() => {
    if (epoch !== fxEpoch) return;
    goo.remove();
    bornSel.transition('kg-reveal').duration(200).ease(d3.easeCubicOut)
      .attr('opacity', 1);
    burst(fx, { x: born.x, y: born.y, count: 5 });
  }, DURATION + 20);
}

// UNDO: generic soft restore — everything tweens back, artifacts restored by
// the undo pop in where they land.
function playUndoFx({ node, transition, DURATION }) {
  const { aPositions } = transition;
  node.filter((d) => d.kind === 'object' && !lastRenderedIds.has(d.id) && aPositions.has(d.id))
    .attr('opacity', 0)
    .transition('kg-op-restore').delay(200).duration(380).ease(d3.easeCubicOut)
    .attr('opacity', 1);
}

function renderComparison(container, width, height) {
  const svg = d3.select(container).append('svg')
    .attr('role', 'img')
    .attr('aria-label', 'Contested slots of selected artifact');

  const artifact = ARTIFACTS.find((a) => a.id === props.selectedArtifactId);
  if (!artifact) {
    svg.append('text')
      .text('Select an object')
      .attr('x', width / 2).attr('y', height / 2)
      .attr('text-anchor', 'middle').attr('fill', '#9a9284').attr('font-size', 12.5);
    return;
  }

  const sources = artifact.sources.length ? artifact.sources : Object.keys(SOURCES);
  const rows = artifact.slots.length ? artifact.slots : [{ id: 'none', label: 'No indexed attributes', cls: 'missing', assertions: [] }];
  const left = 12;
  const top = 40;
  const labelWidth = Math.min(108, Math.max(86, width * 0.27));
  const cellWidth = Math.max(78, (width - left * 2 - labelWidth) / Math.max(1, sources.length));
  const rowHeight = Math.min(54, Math.max(34, (height - top - 18) / (rows.length + 1)));
  // Min row/column sizes let a merged event with many reports (columns) and
  // attribute slots (rows) outgrow the pane. Extend the drawing surface rather
  // than clipping the outer rows/columns; .scrollable-canvas provides the
  // scrollbars so every report stays reachable.
  const drawWidth = Math.max(width, Math.ceil(left * 2 + labelWidth + sources.length * cellWidth));
  const drawHeight = Math.max(height, Math.ceil(top + rows.length * rowHeight + 18));
  svg
    .attr('viewBox', [0, 0, drawWidth, drawHeight])
    .style('width', `${drawWidth}px`)
    .style('height', `${drawHeight}px`);
  const short = (value, max = 18) => {
    const text = String(value || '');
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  };
  const g = svg.append('g');
  g.append('text').text(short(artifact.name, 32)).attr('x', left).attr('y', 20)
    .attr('fill', '#294e6b').attr('font-size', 13).attr('font-weight', 750);
  g.append('text').text(`${artifact.alignmentStatus} · ${artifact.sources.length} source(s)`)
    .attr('x', left).attr('y', 34).attr('fill', '#7d8790').attr('font-size', 9.5);
  g.append('text').text('ATTRIBUTE × SOURCE EVIDENCE MATRIX').attr('x', left + labelWidth).attr('y', 16)
    .attr('fill', '#8f6b2f').attr('font-size', 9.2).attr('font-weight', 750);
  g.append('text').text('Rows = attributes · columns = sources · cells = extracted values · dot = image evidence')
    .attr('x', left + labelWidth).attr('y', 28).attr('fill', '#8a949b').attr('font-size', 7.8);
  sources.forEach((source, index) => {
    const x = left + labelWidth + index * cellWidth;
    const header = `${source} · ${short(SOURCES[source]?.title || source, 7)}`;
    g.append('text').text(header).attr('x', x + cellWidth / 2).attr('y', 40)
      .attr('text-anchor', 'middle').attr('fill', SOURCES[source]?.color || '#526774').attr('font-size', 8.5).attr('font-weight', 700)
      .append('title').text(SOURCES[source]?.title || source);
  });
  rows.forEach((row, rowIndex) => {
    const y = top + rowIndex * rowHeight;
    const rowCls = displayCls(row);
    g.append('rect').attr('x', left).attr('y', y).attr('width', drawWidth - left * 2).attr('height', rowHeight)
      .attr('fill', row.id === props.selectedSlotId ? '#fffaf0' : rowIndex % 2 ? '#fbfcfd' : '#ffffff')
      .attr('stroke', '#e1e6eb').attr('stroke-width', row.id === props.selectedSlotId ? 1.4 : 0.7);
    g.append('rect').attr('x', left).attr('y', y).attr('width', 4).attr('height', rowHeight)
      .attr('fill', CLASS_COLORS[rowCls] || CLASS_COLORS.missing);
    const rowTitle = row.decision?.action && row.decision.action !== 'keep_open'
      ? `${short(row.label, 12)} · decided`
      : short(row.label, 16);
    g.append('text').text(rowTitle).attr('x', left + 11).attr('y', y + rowHeight / 2 + 3)
      .attr('fill', '#45515c').attr('font-size', 10).attr('font-weight', 700);
    sources.forEach((source, sourceIndex) => {
      const assertion = row.assertions.find((item) => item.source === source);
      const x = left + labelWidth + sourceIndex * cellWidth;
      const raw = assertion?.raw;
      const cell = g.append('rect').attr('x', x + 3).attr('y', y + 4).attr('width', cellWidth - 6).attr('height', rowHeight - 8)
        .attr('rx', 4).attr('fill', raw ? (rowCls === 'A' ? '#f5e2dc' : rowCls === 'B' ? '#f4eddf' : '#edf4f2') : '#f0f2f3')
        .attr('stroke', raw ? (rowCls === 'A' ? CLASS_COLORS.A : '#d6dee2') : '#d6dadd').attr('stroke-dasharray', raw ? null : '3 2');
      cell.append('title').text(`${row.label} · ${SOURCES[source]?.title || source} · ${raw || 'not recorded'}${row.decision?.value ? ` · decision: ${row.decision.action} ${row.decision.value}` : ''}`);
      g.append('text').text(raw ? short(raw, Math.max(10, Math.floor(cellWidth / 7))) : 'not recorded')
        .attr('x', x + cellWidth / 2).attr('y', y + rowHeight / 2 + 3).attr('text-anchor', 'middle')
        .attr('fill', raw ? '#4a5058' : '#9ca5aa').attr('font-size', 8.5);
      if (assertion?.imagePath || artifact.imagesByBook[source]?.length) {
        g.append('circle').attr('cx', x + cellWidth - 10).attr('cy', y + 10).attr('r', 3)
          .attr('fill', '#294e6b').append('title').text('image evidence available');
      }
    });
  });
}

function render() {

  // One news correction bumps both store.adjudicationRev and artifacts
  // length, queueing two renders in one flush. While a liquid transition for
  // this revision is on stage, the redundant render must not wipe the
  // animation DOM; the bounded time window keeps later renders unaffected.
  if (playingRev === store.adjudicationRev
    && !pendingTransition
    && Date.now() - playingStartedAt < 950) return;
  const container = rootEl.value;
  if (!container) return;
  const rect = container.getBoundingClientRect();
  // Measure the drawable area excluding classic scrollbars (.scrollable-canvas):
  // a vertical scrollbar must not force an otherwise avoidable horizontal one.
  const width = Math.max(0, rect.width - (container.offsetWidth - container.clientWidth));
  const height = Math.max(0, rect.height - (container.offsetHeight - container.clientHeight));
  if (!width || !height) return;
  d3.select(container).selectAll('*').remove();
  // Global graph pans/zooms via d3.zoom (overflow hidden); the derived
  // attribute matrix scrolls instead when its content exceeds the pane.
  container.classList.toggle('scrollable-canvas', props.variant !== 'global');
  if (props.variant === 'global') renderGlobal(container, width, height);
  else renderComparison(container, width, height);
}

onMounted(async () => {
  await nextTick();
  const element = rootEl.value;
  if (!element) return;
  render();
  resizeObserver = new ResizeObserver(render);
  // Observe the border box: with the scrollable matrix, classic scrollbars
  // appearing/disappearing only change the content box, so observing the
  // content box re-triggers render() and ends in "ResizeObserver loop
  // completed with undelivered notifications". The border box is driven by
  // the surrounding grid layout and stays stable while scrollbars toggle.
  resizeObserver.observe(element, { box: 'border-box' });
});

function updateGlobalSelection() {
  if (!rootEl.value) return;
  d3.select(rootEl.value)
    .selectAll('.sel-ring')
    .attr('stroke-width', (d) => (d.artifact?.id === props.selectedArtifactId ? 1.6 : 0));
  d3.select(rootEl.value)
    .selectAll('.artifact-label')
    .attr('opacity', (d) => (d.artifact?.id === props.selectedArtifactId ? 1 : 0));
  d3.select(rootEl.value)
    .selectAll('.issue-mark')
    .attr('stroke', (d) => (d.artifact.id === props.selectedArtifactId && d.slot.id === props.selectedSlotId ? '#4a4438' : '#ffffff'))
    .attr('stroke-width', (d) => (d.artifact.id === props.selectedArtifactId && d.slot.id === props.selectedSlotId ? 2.2 : 1.1));
}

function updateMultiSelection() {
  if (!rootEl.value) return;
  d3.select(rootEl.value)
    .selectAll('.multi-ring')
    .attr('stroke-width', (d) => (props.selectedArtifactIds?.includes(d.artifact?.id) ? 2.4 : 0));
}


// Case 3 · News manual graph corrections (merge/split/undo). Registered
// AHEAD of the render triggers below: within the same flush this watcher
// snapshots state A from posCache before the next render overwrites it.
watch(
  () => [newsV6Store.graph, newsV6Store.lastGraphOp],
  () => {
    if (props.variant !== 'global') return;
    const op = newsV6Store.lastGraphOp;
    if (!op || !op.at || op.at <= lastPlayedOpAt) return;
    lastPlayedOpAt = op.at;
    pendingTransition = { op, snapshot: new Map(posCache) };
  },
);
watch(() => store.adjudicationRev, render);
watch(() => props.variant, render);
watch(() => store.artifacts.length, render);
watch(() => store.caseId, render);
// Case 3 · News: re-render when the rule/LLM judgment toggle changes colors.
watch(() => store.judgeMode, render);
watch(() => props.visibleArtifactIds.join('|'), render);
watch(() => props.selectedArtifactIds.join('|'), () => {
  if (props.variant === 'global') updateMultiSelection();
});
watch(
  () => [props.selectedArtifactId, props.selectedSlotId],
  () => {
    if (props.variant === 'global') {
      updateGlobalSelection();
    } else {
      render();
    }
  },
);

onUnmounted(() => {
  resizeObserver?.disconnect();
});
</script>

<template>
  <div ref="rootEl" class="knowledge-graph-canvas"></div>
</template>
