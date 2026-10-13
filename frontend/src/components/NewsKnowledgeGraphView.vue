<script setup>
import { onBeforeUnmount, onMounted, watch } from 'vue';
import * as d3 from 'd3';
import {
  newsV6Store,
  selectNewsEntity,
  selectNewsEvent,
  toggleNewsNodeSelection,
  clearNewsSelection,
} from '../data/newsV6Store.js';

let host;
let simulation;
let observer;
let rootSel = null;   // zoomable <g>; d3.zoom is attached once so pan/zoom survives re-renders
let nodeSel = null;   // node <g> selection of the latest render (fx reach live nodes through it)
let fxLayerSel = null; // overlay group hosting all transition effect elements
let simNodesById = new Map();
let zoomReady = false;
let fxGeneration = 0; // bumped on every render/animation start; stale callbacks bail out
let lastPlayedOpAt = 0;
let firstRenderDone = false;
let prevNodeIds = new Set();
let prevEdgeKeys = new Set();
let fxFollowers = []; // gooey clones tracking live force-layout positions while animating
// Node id -> last simulated geometry. Restoring this on render keeps the
// layout stable across rebuilds — the root cause of the old "everything
// jumps/re-flies" harshness on merge and split.
const positionCache = new Map();
const gooeyFilterId = 'news-gooey';
const reducedMotion = (typeof window !== 'undefined' && window.matchMedia)
  ? window.matchMedia('(prefers-reduced-motion: reduce)')
  : { matches: false };

function nodeColor(node) {
  if (node.type === 'event') return '#b86f3d';
  if (node.type === 'person') return '#39769a';
  if (node.type === 'organization') return '#715b91';
  if (node.type === 'place') return '#4f8a75';
  if (node.type === 'geopolitical_entity') return '#9a7840';
  return '#7c8b94';
}

function nodeRadius(item) {
  return item.type === 'event' ? 18 + Math.min(6, item.report_count || 0) : 9;
}

function isSelected(id) {
  return newsV6Store.selectionIds.includes(id);
}

function isFocused(id) {
  return id === newsV6Store.selectedEventId || id === newsV6Store.selectedEntityId;
}

function edgeKeyOf(link) {
  return `${link.source}|${link.target}|${link.relation || ''}`;
}

// Classic gooey SVG recipe: blur, then sharpen the alpha channel, so shapes
// that come close fuse through a liquid bridge. Used only by fx clones.
function appendGooeyFilter(defs) {
  const filter = defs.append('filter')
    .attr('id', gooeyFilterId)
    .attr('x', '-60%')
    .attr('y', '-60%')
    .attr('width', '220%')
    .attr('height', '220%')
    .attr('color-interpolation-filters', 'sRGB');
  filter.append('feGaussianBlur')
    .attr('in', 'SourceGraphic')
    .attr('stdDeviation', 7)
    .attr('result', 'blur');
  filter.append('feColorMatrix')
    .attr('in', 'blur')
    .attr('type', 'matrix')
    .attr('values', '1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 19 -9')
    .attr('result', 'goo');
  return filter;
}

function liveCircle(id) {
  return nodeSel ? nodeSel.filter((d) => d.id === id).select('circle') : d3.select(null);
}

function liveLabel(id) {
  return nodeSel ? nodeSel.filter((d) => d.id === id).select('text') : d3.select(null);
}

// Gooey clones follow the live simulation positions so the liquid bridge
// stretches with the real nodes during the animation.
function updateFxFollowers() {
  for (const follower of fxFollowers) {
    const datum = simNodesById.get(follower.id);
    if (datum && Number.isFinite(datum.x)) {
      follower.sel.attr('cx', datum.x).attr('cy', datum.y);
    }
  }
}

function render() {
  if (!host || !newsV6Store.graph.nodes.length) return;
  if (simulation) simulation.stop();
  fxGeneration += 1; // any in-flight overlay animation is deliberately cut short
  fxFollowers = [];
  const width = Math.max(host.clientWidth, 640);
  const height = Math.max(host.clientHeight, 480);
  const op = newsV6Store.lastGraphOp;

  // Rebuild data copies, but restore last-known geometry so the simulation
  // continues from the previous equilibrium instead of re-randomizing.
  const nodes = newsV6Store.graph.nodes.map((node) => {
    const cached = positionCache.get(node.id);
    if (cached) {
      return {
        ...node,
        x: cached.x, y: cached.y, vx: cached.vx, vy: cached.vy,
        fx: cached.fx, fy: cached.fy,
      };
    }
    // A freshly split node is born on top of its source node (tiny offset to
    // avoid degenerate zero-distance forces) so the separation can animate.
    if (op && op.type === 'split' && node.id === op.newId) {
      const src = positionCache.get(op.sourceId);
      if (src) return { ...node, x: src.x + 2.5, y: src.y + 2.5, vx: 0, vy: 0 };
    }
    return { ...node };
  });
  const links = newsV6Store.graph.edges.map((edge) => ({ ...edge }));
  for (const item of links) item.__enter = !prevEdgeKeys.has(edgeKeyOf(item));

  // Diff against the previous render. Generic enter/exit covers everything;
  // the nodes a recorded merge/split animates itself are excluded.
  const enteringNodeIds = new Set(nodes.filter((d) => !prevNodeIds.has(d.id)).map((d) => d.id));
  const exitingNodes = [];
  for (const id of prevNodeIds) {
    if (!nodes.some((d) => d.id === id)) {
      const cached = positionCache.get(id);
      if (cached) exitingNodes.push({ id, ...cached });
    }
  }
  const currentEdgeKeys = new Set(links.map(edgeKeyOf));
  const exitingEdges = [];
  for (const key of prevEdgeKeys) {
    if (currentEdgeKeys.has(key)) continue;
    const [sourceId, targetId] = key.split('|');
    const a = positionCache.get(sourceId);
    const b = positionCache.get(targetId);
    if (a && b) exitingEdges.push({ x1: a.x, y1: a.y, x2: b.x, y2: b.y });
  }
  const opEnterIds = op && op.type === 'split' ? new Set([op.newId]) : new Set();
  const opExitIds = op && op.type === 'merge' ? new Set(op.fromIds || []) : new Set();

  const svg = d3.select(host).selectAll('svg').data([null]).join('svg')
    .attr('viewBox', `0 0 ${width} ${height}`)
    .attr('aria-label', 'News event and entity knowledge graph');
  svg.selectAll('*').remove();
  appendGooeyFilter(svg.append('defs'));
  rootSel = svg.append('g');
  if (!zoomReady) {
    // Attach once: re-applying d3.zoom resets the stored transform, which used
    // to throw away the user's pan/zoom on every re-render.
    svg.call(d3.zoom().scaleExtent([0.45, 3]).on('zoom', (event) => {
      if (rootSel) rootSel.attr('transform', event.transform);
    }));
    zoomReady = true;
  }
  const root = rootSel;

  const link = root.append('g').attr('class', 'news-links')
    .selectAll('line').data(links).join('line')
    .attr('stroke', '#a9b1b5').attr('stroke-width', 1.2);
  nodeSel = root.append('g').selectAll('g').data(nodes).join('g')
    .attr('class', (item) => `news-node ${item.type}`)
    .on('click', (event, item) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey) {
        toggleNewsNodeSelection(item.id);
        render();
        return;
      }
      if (newsV6Store.selectionIds.length) {
        clearNewsSelection();
        render();
        return;
      }
      if (item.type === 'event') selectNewsEvent(item.id);
      else selectNewsEntity(item.id);
      render();
    })
    .call(d3.drag()
      .on('start', (event, item) => {
        if (!event.active) simulation.alphaTarget(0.25).restart();
        item.fx = item.x;
        item.fy = item.y;
      })
      .on('drag', (event, item) => {
        item.fx = event.x;
        item.fy = event.y;
      })
      .on('end', (event, item) => {
        if (!event.active) simulation.alphaTarget(0);
        item.fx = null;
        item.fy = null;
      }));
  nodeSel.append('circle')
    .attr('r', (item) => nodeRadius(item))
    .attr('fill', nodeColor)
    .attr('stroke', (item) => (
      isSelected(item.id) ? '#c8912b' : isFocused(item.id) ? '#20292e' : '#fff'
    ))
    .attr('stroke-width', (item) => (
      isSelected(item.id) ? 3.5 : isFocused(item.id) ? 3 : 1.5
    ));
  nodeSel.append('text')
    .attr('x', (item) => item.type === 'event' ? 27 : 14)
    .attr('y', 4)
    .text((item) => item.label)
    .attr('font-size', (item) => item.type === 'event' ? 13 : 11)
    .attr('font-weight', (item) => item.type === 'event' ? 600 : 400)
    .attr('fill', '#263238');
  nodeSel.append('title').text((item) => {
    const base = item.type === 'event'
      ? `${item.label} · ${item.report_count} reports`
      : `${item.label} · ${item.type} · ${item.event_count || 1} events`;
    return `${base}\nClick to focus · Ctrl+Click to multi-select (merge/split)`;
  });
  fxLayerSel = root.append('g').attr('class', 'fx-layer');

  // Alpha policy: with restored geometry a rebuild is visually a no-op, so
  // only structural changes get a meaningful reheat of the simulation.
  const cacheHits = nodes.filter((item) => positionCache.has(item.id)).length;
  const hasLayout = cacheHits >= Math.ceil(nodes.length / 2);
  let startAlpha = 1;
  if (hasLayout) {
    if (op && op.type === 'merge') startAlpha = 0.15;
    else if (op && op.type === 'split') startAlpha = 0.4;
    else if (op && op.type === 'undo') startAlpha = 0.5;
    else startAlpha = 0.05;
  }

  simNodesById = new Map(nodes.map((item) => [item.id, item]));
  simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((item) => item.id).distance((item) => (
      item.source.type === 'event' && item.target.type === 'event' ? 180 : 115
    )).strength(0.55))
    .force('charge', d3.forceManyBody().strength((item) => item.type === 'event' ? -520 : -180))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius((item) => item.type === 'event' ? 44 : 26))
    .alpha(startAlpha)
    .on('tick', () => {
      link
        .attr('x1', (item) => item.source.x).attr('y1', (item) => item.source.y)
        .attr('x2', (item) => item.target.x).attr('y2', (item) => item.target.y);
      nodeSel.attr('transform', (item) => `translate(${item.x},${item.y})`);
      // persist geometry so the next render starts from this equilibrium
      for (const item of nodes) {
        positionCache.set(item.id, {
          x: item.x, y: item.y, vx: item.vx, vy: item.vy, fx: item.fx, fy: item.fy,
          r: nodeRadius(item), color: nodeColor(item), type: item.type, label: item.label,
        });
      }
      updateFxFollowers();
    });

  // Generic enter/exit polish; dedicated op animations handle their own nodes.
  if (!reducedMotion.matches && firstRenderDone) {
    nodeSel.filter((d) => enteringNodeIds.has(d.id) && !opEnterIds.has(d.id)).each(function (d) {
      const circle = d3.select(this).select('circle');
      const label = d3.select(this).select('text');
      circle.attr('r', 0);
      label.attr('opacity', 0);
      circle.transition('enter-grow').duration(350).ease(d3.easeBackOut).attr('r', nodeRadius(d));
      label.transition('enter-label').delay(150).duration(220).attr('opacity', 1);
    });
    const dashLength = 480;
    link.filter((item) => item.__enter)
      .attr('stroke-dasharray', dashLength)
      .attr('stroke-dashoffset', dashLength)
      .transition('edge-draw').duration(500).ease(d3.easeCubicOut)
      .attr('stroke-dashoffset', 0)
      .on('end', function () { d3.select(this).attr('stroke-dasharray', null); });
    for (const ghost of exitingNodes) {
      if (opExitIds.has(ghost.id)) continue;
      const group = fxLayerSel.append('g').attr('transform', `translate(${ghost.x},${ghost.y})`);
      group.append('circle')
        .attr('r', ghost.r)
        .attr('fill', ghost.color)
        .attr('stroke', '#fff')
        .attr('stroke-width', 1.5);
      group.transition('ghost-fade').duration(300).attr('opacity', 0).remove();
    }
    for (const ghost of exitingEdges) {
      fxLayerSel.append('line')
        .attr('x1', ghost.x1).attr('y1', ghost.y1)
        .attr('x2', ghost.x2).attr('y2', ghost.y2)
        .attr('stroke', '#a9b1b5').attr('stroke-width', 1.2)
        .transition('edge-fade').duration(280).attr('opacity', 0).remove();
    }
  }

  prevNodeIds = new Set(nodes.map((item) => item.id));
  prevEdgeKeys = currentEdgeKeys;
  firstRenderDone = true;
}

function captureOpSnapshot(op) {
  if (!op) return null;
  if (op.type === 'merge') {
    const survivor = positionCache.get(op.intoId);
    const absorbed = (op.fromIds || [])
      .map((id) => positionCache.get(id))
      .filter(Boolean);
    if (!survivor || !absorbed.length) return null;
    return { survivor: { ...survivor }, absorbed };
  }
  if (op.type === 'split') {
    const source = positionCache.get(op.sourceId);
    const newNode = (newsV6Store.graph.nodes || []).find((item) => item.id === op.newId);
    if (!source || !newNode) return null;
    return {
      source: { ...source },
      newId: op.newId,
      newR: nodeRadius(newNode),
      newColor: nodeColor(newNode),
    };
  }
  return null; // 'undo' and unknown types use the generic enter/exit path
}

function playOpAnimation(op, snapshot) {
  if (!fxLayerSel) return;
  const generation = ++fxGeneration;
  fxLayerSel.selectAll('*').interrupt().remove();
  if (op.type === 'merge') animateMerge(op, snapshot, generation);
  else if (op.type === 'split') animateSplit(op, snapshot, generation);
}

function ripple(center) {
  fxLayerSel.append('circle')
    .attr('cx', center.x)
    .attr('cy', center.y)
    .attr('r', center.r)
    .attr('fill', 'none')
    .attr('stroke', center.color)
    .attr('stroke-width', 2)
    .attr('opacity', 0.55)
    .transition('ripple').duration(560).ease(d3.easeCubicOut)
    .attr('r', center.r * 2.4)
    .attr('opacity', 0)
    .on('end', function () { d3.select(this).remove(); });
}

function burst(center) {
  const group = fxLayerSel.append('g');
  const count = 6;
  for (let i = 0; i < count; i += 1) {
    const angle = (Math.PI * 2 * i) / count + Math.random() * 0.6;
    const distance = 20 + Math.random() * 24;
    group.append('circle')
      .attr('cx', center.x)
      .attr('cy', center.y)
      .attr('r', 2 + Math.random() * 1.8)
      .attr('fill', center.color)
      .attr('opacity', 0.85)
      .transition('spark').duration(380 + Math.random() * 200).ease(d3.easeCubicOut)
      .attr('cx', center.x + Math.cos(angle) * distance)
      .attr('cy', center.y + Math.sin(angle) * distance)
      .attr('opacity', 0)
      .on('end', function () { d3.select(this).remove(); });
  }
  group.transition('spark-cleanup').delay(650).duration(0).remove();
}

onMounted(() => {
  observer = new ResizeObserver(render);
  observer.observe(host);
  render();
});
onBeforeUnmount(() => {
  observer?.disconnect();
  simulation?.stop();
});

// MERGE: absorbed nodes melt into the survivor through a gooey filter. The
// clones fly in (easeCubicIn), the fused blob overshoots to 1.3x the new
// radius and settles back with an elastic bounce, ending in a particle burst.
function animateMerge(op, snapshot, generation) {
  const survivorNode = (newsV6Store.graph.nodes || []).find((item) => item.id === op.intoId);
  const newRadius = survivorNode ? nodeRadius(survivorNode) : snapshot.survivor.r;
  const goo = fxLayerSel.append('g')
    .attr('pointer-events', 'none')
    .attr('filter', `url(#${gooeyFilterId})`);
  // Hide the real survivor; the goo clone (old radius) stands in for it and
  // grows into the merged radius with the overshoot at the moment of fusion.
  liveCircle(op.intoId).attr('opacity', 0);

  const survivorClone = goo.append('circle')
    .attr('cx', snapshot.survivor.x)
    .attr('cy', snapshot.survivor.y)
    .attr('r', snapshot.survivor.r)
    .attr('fill', snapshot.survivor.color);
  fxFollowers.push({ sel: survivorClone, id: op.intoId });

  // Labels of the absorbed nodes linger briefly, then fade with the blob.
  const ghostLabels = fxLayerSel.append('g').attr('pointer-events', 'none');
  const ghosts = snapshot.absorbed.map((ghost) => {
    ghostLabels.append('text')
      .attr('x', ghost.x + (ghost.type === 'event' ? 27 : 14))
      .attr('y', ghost.y + 4)
      .text(ghost.label || '')
      .attr('font-size', ghost.type === 'event' ? 13 : 11)
      .attr('font-weight', ghost.type === 'event' ? 600 : 400)
      .attr('fill', '#263238')
      .transition('ghost-label').duration(340).attr('opacity', 0).remove();
    return {
      sel: goo.append('circle')
        .attr('cx', ghost.x)
        .attr('cy', ghost.y)
        .attr('r', ghost.r)
        .attr('fill', ghost.color),
      ghost,
    };
  });

  const impact = 550;
  for (const { sel, ghost } of ghosts) {
    // Each absorbed clone flies into the survivor; the gooey filter fuses
    // them into one blob on contact.
    sel.transition('absorb').duration(impact).ease(d3.easeCubicIn)
      .attr('cx', snapshot.survivor.x)
      .attr('cy', snapshot.survivor.y)
      .attr('r', Math.max(2, ghost.r * 0.35));
  }

  d3.timeout(() => {
    if (generation !== fxGeneration) return;
    for (const { sel } of ghosts) sel.remove();
    survivorClone
      .transition('overshoot').duration(160).ease(d3.easeCubicOut)
      .attr('r', newRadius * 1.3)
      .transition('settle').duration(520).ease(d3.easeElasticOut)
      .attr('r', newRadius);
    burst(snapshot.survivor);
  }, impact);

  d3.timeout(() => {
    if (generation !== fxGeneration) return;
    fxFollowers = fxFollowers.filter((item) => item.id !== op.intoId);
    goo.remove();
    ghostLabels.remove();
    liveCircle(op.intoId).attr('opacity', 1);
  }, impact + 700);
}

// SPLIT: the new node is born on top of its source inside the gooey layer.
// Both goo clones track the live force-layout positions, so the liquid thread
// stretches as the pair separates; then it snaps and the real nodes take over.
function animateSplit(op, snapshot, generation) {
  const goo = fxLayerSel.append('g')
    .attr('pointer-events', 'none')
    .attr('filter', `url(#${gooeyFilterId})`);
  const sourceClone = goo.append('circle')
    .attr('cx', snapshot.source.x)
    .attr('cy', snapshot.source.y)
    .attr('r', snapshot.source.r)
    .attr('fill', snapshot.source.color);
  const newClone = goo.append('circle')
    .attr('cx', snapshot.source.x)
    .attr('cy', snapshot.source.y)
    .attr('r', 0)
    .attr('fill', snapshot.newColor);
  fxFollowers.push({ sel: sourceClone, id: op.sourceId }, { sel: newClone, id: snapshot.newId });

  // Hide the real pair while the gooey clones stand in for them.
  liveCircle(op.sourceId).attr('opacity', 0);
  liveCircle(snapshot.newId).attr('opacity', 0);
  liveLabel(snapshot.newId).attr('opacity', 0);

  newClone.transition('sprout').delay(120).duration(450).ease(d3.easeBackOut)
    .attr('r', snapshot.newR);
  ripple(snapshot.source);

  d3.timeout(() => {
    if (generation !== fxGeneration) return;
    fxFollowers = fxFollowers.filter(
      (item) => item.id !== op.sourceId && item.id !== snapshot.newId,
    );
    goo.remove();
    liveCircle(op.sourceId).attr('opacity', 1);
    liveCircle(snapshot.newId).attr('opacity', 1);
    liveLabel(snapshot.newId).transition('label-in').duration(250).attr('opacity', 1);
  }, 780);
}

// Both graph and op context are watched: render() is the DOM sync; a fresh op
// additionally plays its dedicated overlay animation on top of the new render.
watch(
  () => [newsV6Store.graph, newsV6Store.lastGraphOp],
  () => {
    const op = newsV6Store.lastGraphOp;
    const fresh = Boolean(op && op.at && op.at > lastPlayedOpAt);
    const snapshot = fresh ? captureOpSnapshot(op) : null;
    render();
    if (!fresh) return;
    lastPlayedOpAt = op.at;
    if (snapshot && !reducedMotion.matches) playOpAnimation(op, snapshot);
  },
);
</script>

<template>
  <div ref="host" class="news-graph"></div>
</template>

<style scoped>
.news-graph {
  width: 100%;
  height: 100%;
  min-height: 520px;
  overflow: hidden;
  background:
    linear-gradient(#edf0f1 1px, transparent 1px),
    linear-gradient(90deg, #edf0f1 1px, transparent 1px),
    #fbfcfc;
  background-size: 24px 24px;
}
.news-graph :deep(svg) { display: block; width: 100%; height: 100%; }
.news-graph :deep(.fx-layer) { pointer-events: none; }
.news-graph :deep(.news-node) { cursor: pointer; }
</style>