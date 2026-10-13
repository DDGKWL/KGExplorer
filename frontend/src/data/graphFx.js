// ---------------------------------------------------------------------------
// graphFx · shared liquid-transition primitives for knowledge-graph views.
//
// Extracted from the retired NewsKnowledgeGraphView (Case 3 · News manual
// graph corrections) so the actually mounted KnowledgeGraphView can play the
// merge/split/undo transition between two headlessly computed static layouts:
// state A (pre-op geometry) and state B (post-op layout). Both endpoints are
// deterministic — no live force simulation runs during playback.
// ---------------------------------------------------------------------------
import * as d3 from 'd3';

export const GOOEY_FILTER_ID = 'kg-news-gooey';
// Amber of the multi-selection ring: nodes being merged/split were amber
// highlighted right before the dialog, so the liquid clones keep that color
// for visual continuity.
export const OP_COLOR = '#c8912b';

export const reducedMotion = (typeof window !== 'undefined' && window.matchMedia)
  ? window.matchMedia('(prefers-reduced-motion: reduce)')
  : { matches: false };

// Classic gooey SVG recipe: blur, then sharpen the alpha channel, so shapes
// that come close fuse through a liquid bridge. Applied only to fx clones.
export function appendGooeyFilter(defs) {
  const filter = defs.append('filter')
    .attr('id', GOOEY_FILTER_ID)
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

// Topmost overlay group hosting every transition effect element.
export function ensureFxLayer(svgOrGroup) {
  return svgOrGroup.append('g')
    .attr('class', 'fx-layer')
    .attr('pointer-events', 'none');
}

// Gooey-filtered group: the liquid clone stage.
export function gooGroup(fxLayer) {
  return fxLayer.append('g').attr('filter', `url(#${GOOEY_FILTER_ID})`);
}

export function ripple(fxLayer, { x, y, r, color = OP_COLOR }) {
  fxLayer.append('circle')
    .attr('cx', x)
    .attr('cy', y)
    .attr('r', r)
    .attr('fill', 'none')
    .attr('stroke', color)
    .attr('stroke-width', 2)
    .attr('opacity', 0.55)
    .transition('ripple').duration(560).ease(d3.easeCubicOut)
    .attr('r', r * 2.4)
    .attr('opacity', 0)
    .on('end', function () { d3.select(this).remove(); });
}

export function burst(fxLayer, { x, y, color = OP_COLOR, count = 6 }) {
  const group = fxLayer.append('g');
  for (let i = 0; i < count; i += 1) {
    const angle = (Math.PI * 2 * i) / count + Math.random() * 0.6;
    const distance = 20 + Math.random() * 24;
    group.append('circle')
      .attr('cx', x)
      .attr('cy', y)
      .attr('r', 2 + Math.random() * 1.8)
      .attr('fill', color)
      .attr('opacity', 0.85)
      .transition('spark').duration(380 + Math.random() * 200).ease(d3.easeCubicOut)
      .attr('cx', x + Math.cos(angle) * distance)
      .attr('cy', y + Math.sin(angle) * distance)
      .attr('opacity', 0)
      .on('end', function () { d3.select(this).remove(); });
  }
  group.transition('spark-cleanup').delay(650).duration(0).remove();
}