<script setup>
import { computed, onMounted } from 'vue';
import NewsKnowledgeGraphView from './NewsKnowledgeGraphView.vue';
import NewsComparisonPanel from './NewsComparisonPanel.vue';
import NewsMergeDialog from './NewsMergeDialog.vue';
import NewsSplitDialog from './NewsSplitDialog.vue';
import {
  loadNewsV6,
  newsV6Store,
  selectNewsEntity,
  selectNewsEvent,
  clearNewsSelection,
  openNewsMergeDialog,
  openNewsSplitDialog,
  undoLastAction,
} from '../data/newsV6Store.js';

onMounted(() => {
  if (!newsV6Store.loaded) loadNewsV6();
});

const events = computed(() => newsV6Store.comparisons.events || []);
const sharedEntities = computed(() => (
  (newsV6Store.graph.nodes || [])
    .filter((node) => node.type !== 'event' && Number(node.event_count) > 1)
    .sort((left, right) => Number(right.event_count) - Number(left.event_count))
));

// manual graph correction toolbar (Phase 1)
const selectionNodes = computed(() => {
  const ids = newsV6Store.selectionIds || [];
  return (newsV6Store.graph.nodes || []).filter((node) => ids.includes(node.id));
});
const selectionCount = computed(() => selectionNodes.value.length);
const canMerge = computed(() => (
  selectionNodes.value.length >= 2
  && new Set(selectionNodes.value.map((node) => node.type)).size === 1
));
const canSplit = computed(() => selectionCount.value === 1);
const canUndo = computed(() => newsV6Store.undoStack.length > 0);
</script>

<template>
  <section class="news-workspace" aria-label="News knowledge graph workspace">
    <div v-if="newsV6Store.loading || newsV6Store.error" class="news-mask">
      {{ newsV6Store.loading ? 'Loading Case 3 · News…' : newsV6Store.error }}
    </div>
    <aside class="news-panel news-left">
      <header>Case 3 · News</header>
      <section class="news-summary">
        <h2>Cross-source narrative analysis</h2>
        <div>
          <span><b>{{ newsV6Store.graph.summary?.events || 0 }}</b> events</span>
          <span><b>{{ newsV6Store.comparisons.summary?.reports || 0 }}</b> reports</span>
          <span><b>{{ newsV6Store.graph.summary?.cross_event_links || 0 }}</b> links</span>
        </div>
      </section>
      <section>
        <h3>Events</h3>
        <button
          v-for="event in events"
          :key="event.event_id"
          type="button"
          :class="{ active: event.event_id === newsV6Store.selectedEventId }"
          @click="selectNewsEvent(event.event_id)"
        >
          <strong>{{ event.canonical_name }}</strong>
          <small>{{ event.report_count }} report{{ Number(event.report_count) === 1 ? '' : 's' }}</small>
        </button>
      </section>
      <section>
        <h3>Cross-event entities</h3>
        <button
          v-for="entity in sharedEntities"
          :key="entity.id"
          type="button"
          :class="{ active: entity.id === newsV6Store.selectedEntityId }"
          @click="selectNewsEntity(entity.id)"
        >
          <strong>{{ entity.label }}</strong>
          <small>{{ entity.event_count }} events</small>
        </button>
      </section>
    </aside>

    <section class="news-panel news-center">
      <header class="news-toolbar">
        <div>
          <h2>Event–Entity Knowledge Graph</h2>
          <p>Events connect through normalized actors and political entities.</p>
        </div>
        <div class="news-legend">
          <span><i class="event"></i>event</span>
          <span><i class="person"></i>person</span>
          <span><i class="geo"></i>country/place</span>
        </div>
        <div class="news-ops">
          <span v-if="selectionCount" class="ops-count">{{ selectionCount }} selected</span>
          <button
            type="button"
            :disabled="!canMerge"
            title="Merge the selected same-type nodes into one (Ctrl+click to select)"
            @click="openNewsMergeDialog"
          >Merge</button>
          <button
            type="button"
            :disabled="!canSplit"
            title="Split the selected node (Ctrl+click exactly one node)"
            @click="openNewsSplitDialog"
          >Split</button>
          <button type="button" :disabled="!canUndo" title="Undo the last merge/split" @click="undoLastAction">Undo</button>
          <button type="button" :disabled="!selectionCount" title="Clear the multi-selection" @click="clearNewsSelection">Reselect</button>
        </div>
      </header>
      <NewsKnowledgeGraphView v-if="newsV6Store.loaded" />
      <span v-if="newsV6Store.lastAction" class="news-toast">{{ newsV6Store.lastAction }}</span>
    </section>

    <aside class="news-panel news-right">
      <header>Report Comparison &amp; Evidence</header>
      <NewsComparisonPanel v-if="newsV6Store.loaded" />
    </aside>

    <NewsMergeDialog v-if="newsV6Store.mergeDialogOpen" />
    <NewsSplitDialog v-if="newsV6Store.splitTargetId" />
  </section>
</template>

<style scoped>
.news-workspace {
  position: relative;
  min-height: 0;
  display: grid;
  grid-template-columns: 250px minmax(500px, 1fr) 390px;
  gap: 1px;
  background: #aeb8bd;
}
.news-panel { min-width: 0; min-height: 0; background: #f8f9f9; overflow: hidden; }
.news-panel > header { height: 34px; display: flex; align-items: center; padding: 0 12px; border-bottom: 1px solid #cbd3d6; background: #eef1f2; font-size: 12px; font-weight: 700; }
.news-left { overflow-y: auto; padding-bottom: 16px; }
.news-left > section { padding: 12px; border-bottom: 1px solid #dce2e4; }
.news-left h2 { margin: 0 0 8px; font-size: 14px; }
.news-left h3 { margin: 0 0 8px; font-size: 12px; text-transform: uppercase; color: #68777e; }
.news-summary div { display: flex; gap: 12px; }
.news-summary span { font-size: 10px; color: #68777e; }
.news-summary b { display: block; font-size: 17px; color: #29353a; }
.news-left button { display: block; width: 100%; padding: 8px; margin: 3px 0; border: 1px solid transparent; background: transparent; text-align: left; border-radius: 4px; }
.news-left button:hover, .news-left button.active { border-color: #a9b8bf; background: #e8eef1; }
.news-left button strong, .news-left button small { display: block; }
.news-left button strong { font-size: 11px; }
.news-left button small { margin-top: 3px; color: #748188; }
.news-center { position: relative; display: grid; grid-template-rows: auto 1fr; }
.news-toolbar { height: auto !important; min-height: 48px; justify-content: space-between; flex-wrap: wrap; gap: 4px 12px; }
.news-toolbar h2, .news-toolbar p { margin: 2px 0; }
.news-toolbar h2 { font-size: 13px; }
.news-toolbar p { font-size: 10px; color: #6b787f; }
.news-legend { display: flex; gap: 10px; align-items: center; }
.news-legend span { font-size: 10px; }
.news-legend i { display: inline-block; width: 8px; height: 8px; margin-right: 3px; border-radius: 50%; }
.news-legend .event { background: #b86f3d; }
.news-legend .person { background: #39769a; }
.news-legend .geo { background: #9a7840; }
.news-ops { flex-basis: 100%; display: flex; align-items: center; gap: 6px; padding-bottom: 4px; }
.news-ops button { font-size: 11px; padding: 3px 12px; border: 1px solid #a9b8bf; background: #fff; border-radius: 3px; cursor: pointer; }
.news-ops button:hover:not(:disabled) { background: #e8eef1; }
.news-ops button:disabled { opacity: 0.45; cursor: default; }
.ops-count { font-size: 11px; color: #7c5d10; background: #f6ead0; border: 1px solid #d9a441; padding: 2px 8px; border-radius: 10px; }
.news-toast { position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%); background: #29353a; color: #fff; font-size: 12px; padding: 6px 14px; border-radius: 14px; z-index: 15; pointer-events: none; white-space: nowrap; max-width: 90%; overflow: hidden; text-overflow: ellipsis; }
.news-right { display: grid; grid-template-rows: 34px 1fr; }
.news-mask { position: absolute; z-index: 20; inset: 0; display: grid; place-items: center; background: #f7f8f8dd; }
@media (max-width: 1050px) {
  .news-workspace { grid-template-columns: 210px 1fr; }
  .news-right { grid-column: 1 / -1; min-height: 480px; }
}
</style>