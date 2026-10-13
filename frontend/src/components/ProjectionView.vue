<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import * as d3 from 'd3';
import { store, CLASS_COLORS, worstClass } from '../data/store.js';

const props = defineProps({
  mode: { type: String, default: 'conflict' },
  selectedArtifactId: { type: String, default: null },
  visibleArtifactIds: { type: Array, default: () => [] },
  brushedArtifactIds: { type: Array, default: () => [] },
});

const emit = defineEmits(['select-artifact', 'brush-artifacts']);

const rootEl = ref(null);
let resizeObserver;

function pointOf(artifact) {
  return artifact.projection?.[props.mode] || null;
}

function render() {
  const container = rootEl.value;
  if (!container) return;
  const { width, height } = container.getBoundingClientRect();
  d3.select(container).selectAll('*').remove();
  if (!width || !height) return;

  const svg = d3.select(container).append('svg')
    .attr('viewBox', [0, 0, width, height])
    .attr('role', 'img')
    .attr('aria-label', 'UMAP lens projection');

  const caption = props.mode === 'conflict'
    ? 'conflict lens · objects cluster by disagreement pattern'
    : 'attribute lens · objects cluster by normalized catalogue values';
  const method = store.projection?.methods?.[props.mode];
  svg.append('text')
    .attr('x', 14)
    .attr('y', 18)
    .attr('fill', '#56636d')
    .attr('font-size', 10)
    .attr('font-weight', 700)
    .text(
      store.projection?.ready
        ? `${caption}${method ? ` · ${method}` : ''}`
        : 'projection not built · run backend/scripts/build_umap_projection.py',
    );

  const visible = new Set(props.visibleArtifactIds);
  const brushed = new Set(props.brushedArtifactIds);
  const points = store.artifacts
    .map((artifact) => ({ artifact, xy: pointOf(artifact) }))
    .filter((item) => item.xy && Number.isFinite(item.xy.x) && Number.isFinite(item.xy.y));

  if (!points.length) {
    svg.append('text')
      .attr('x', width / 2)
      .attr('y', height / 2)
      .attr('text-anchor', 'middle')
      .attr('fill', '#9a9284')
      .attr('font-size', 12)
      .text(store.projection?.ready ? 'No coordinates for this mode' : 'No lens coordinates yet');
    return;
  }

  const pad = { top: 36, right: 18, bottom: 18, left: 18 };
  const x = d3.scaleLinear().domain([0, 1]).range([pad.left, width - pad.right]);
  const y = d3.scaleLinear().domain([0, 1]).range([height - pad.bottom, pad.top]);

  const plot = svg.append('g');
  const brushLayer = plot.append('g').attr('class', 'lens-brush');
  const node = plot.append('g').selectAll('g')
    .data(points)
    .join('g')
    .attr('transform', (d) => `translate(${x(d.xy.x)},${y(d.xy.y)})`)
    .attr('cursor', 'pointer')
    .attr('opacity', (d) => {
      if (visible.size && !visible.has(d.artifact.id)) return 0.12;
      if (brushed.size && !brushed.has(d.artifact.id)) return 0.28;
      return 1;
    })
    .on('click', (event, d) => {
      event.stopPropagation();
      emit('select-artifact', d.artifact.id);
    });

  node.append('circle')
    .attr('r', (d) => 4.2 + Math.min(3, (d.artifact.sources?.length || 1) * 0.6))
    .attr('fill', (d) => CLASS_COLORS[worstClass(d.artifact)] || CLASS_COLORS.consensus)
    .attr('stroke', (d) => (d.artifact.id === props.selectedArtifactId ? '#294e6b' : '#ffffff'))
    .attr('stroke-width', (d) => (d.artifact.id === props.selectedArtifactId ? 2 : 1));

  node.append('title').text((d) => {
    const cls = worstClass(d.artifact);
    return `${d.artifact.name} · ${cls} · ${d.artifact.sources?.length || 0} source(s)`;
  });

  node.filter((d) => d.artifact.id === props.selectedArtifactId)
    .append('text')
    .text((d) => d.artifact.name)
    .attr('x', 8)
    .attr('y', 3)
    .attr('fill', '#294e6b')
    .attr('font-size', 10)
    .attr('font-weight', 700)
    .attr('paint-order', 'stroke')
    .attr('stroke', '#ffffff')
    .attr('stroke-width', 3);

  const brush = d3.brush()
    .extent([[pad.left, pad.top], [width - pad.right, height - pad.bottom]])
    .on('end', (event) => {
      if (!event.selection) {
        emit('brush-artifacts', []);
        return;
      }
      const [[x0, y0], [x1, y1]] = event.selection;
      const ids = points
        .filter((d) => {
          const px = x(d.xy.x);
          const py = y(d.xy.y);
          return px >= x0 && px <= x1 && py >= y0 && py <= y1;
        })
        .map((d) => d.artifact.id);
      emit('brush-artifacts', ids);
    });
  brushLayer.call(brush);
}

onMounted(async () => {
  await nextTick();
  if (!rootEl.value) return;
  render();
  resizeObserver = new ResizeObserver(render);
  resizeObserver.observe(rootEl.value);
});

watch(
  () => [
    props.mode,
    props.selectedArtifactId,
    props.visibleArtifactIds.join('|'),
    props.brushedArtifactIds.join('|'),
    store.artifacts.length,
    store.caseId,
    store.projection?.ready,
    store.adjudicationRev,
    store.judgeMode,
  ],
  render,
);

onUnmounted(() => {
  resizeObserver?.disconnect();
});
</script>

<template>
  <div ref="rootEl" class="knowledge-graph-canvas lens-canvas"></div>
</template>
