<script setup>
import { computed, ref } from 'vue';
import { closeNewsMergeDialog, mergeNodes, newsV6Store } from '../data/newsV6Store.js';

const store = newsV6Store;
const note = ref('');
const label = ref('');
const keepLabel = ref(true);

const selectedNodes = computed(() => (
  (store.graph.nodes || []).filter((node) => (store.selectionIds || []).includes(node.id))
));

function degree(nodeId) {
  return (store.graph.edges || []).filter(
    (edge) => edge.source === nodeId || edge.target === nodeId,
  ).length;
}

// default survivor: the most connected node of the selection
const intoId = ref([...selectedNodes.value]
  .sort((left, right) => degree(right.id) - degree(left.id))[0]?.id || '');
const survivor = computed(() => selectedNodes.value.find((node) => node.id === intoId.value));
const absorbedCount = computed(() => Math.max(0, selectedNodes.value.length - 1));
const typeLabel = computed(() => (
  survivor.value?.type === 'event' ? 'event' : survivor.value?.type || 'entity'
));
const affectedEdges = computed(() => {
  const absorbedIds = new Set(
    selectedNodes.value.filter((node) => node.id !== intoId.value).map((node) => node.id),
  );
  return (store.graph.edges || []).filter(
    (edge) => absorbedIds.has(edge.source) || absorbedIds.has(edge.target),
  ).length;
});

function confirmMerge() {
  if (!intoId.value || absorbedCount.value < 1) return;
  mergeNodes({
    intoId: intoId.value,
    fromIds: selectedNodes.value.map((node) => node.id).filter((id) => id !== intoId.value),
    label: keepLabel.value ? '' : label.value.trim(),
    note: note.value.trim(),
  });
}
</script>

<template>
  <div class="dialog-mask" @click.self="closeNewsMergeDialog">
    <section class="dialog-card" role="dialog" aria-modal="true" aria-label="Merge nodes">
      <header class="dialog-head">
        <strong>Merge {{ selectedNodes.length }} nodes</strong>
        <button type="button" class="close" aria-label="Close" @click="closeNewsMergeDialog">×</button>
      </header>
      <p class="hint">
        The {{ absorbedCount }} unmarked node(s) will be merged into the kept node:
        {{ affectedEdges }} edge(s) get redirected, parallel edges and their evidence are
        united, and absorbed labels become aliases of the kept node. Fully undoable.
      </p>
      <div class="node-list">
        <label v-for="node in selectedNodes" :key="node.id" class="node-row">
          <input v-model="intoId" type="radio" name="merge-survivor" :value="node.id" />
          <span class="node-label">{{ node.label }}</span>
          <small>
            {{ degree(node.id) }} edges<template v-if="node.type === 'event'"> · {{ node.report_count }} reports</template>
          </small>
        </label>
      </div>
      <label class="field check">
        <input v-model="keepLabel" type="checkbox" />
        Keep the displayed name "{{ survivor?.label }}"
      </label>
      <label v-if="!keepLabel" class="field">
        New display name
        <input v-model="label" type="text" placeholder="e.g. Trump-Zelenskyy meeting" />
      </label>
      <label class="field">
        Note (optional)
        <input v-model="note" type="text" placeholder="Why are these nodes merged?" />
      </label>
      <footer class="dialog-foot">
        <button type="button" @click="closeNewsMergeDialog">Cancel</button>
        <button
          type="button"
          class="primary"
          :disabled="!intoId || absorbedCount < 1"
          @click="confirmMerge"
        >Merge {{ absorbedCount + 1 }} nodes</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.dialog-mask { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: #26323866; }
.dialog-card { width: 440px; max-width: calc(100vw - 40px); max-height: 82vh; overflow-y: auto; background: #fff; border-radius: 6px; padding: 14px 16px; box-shadow: 0 10px 30px #0006; }
.dialog-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: 14px; }
.close { border: none; background: transparent; font-size: 16px; line-height: 1; cursor: pointer; }
.hint { margin: 0 0 10px; font-size: 11px; color: #68777e; line-height: 1.5; }
.node-list { border: 1px solid #dce2e4; border-radius: 4px; margin-bottom: 10px; max-height: 220px; overflow-y: auto; }
.node-row { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-bottom: 1px solid #eef1f2; cursor: pointer; }
.node-row:last-child { border-bottom: none; }
.node-row:hover { background: #f4f7f8; }
.node-label { font-size: 12px; font-weight: 600; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node-row small { font-size: 10px; color: #748188; white-space: nowrap; }
.field { display: block; margin: 8px 0; font-size: 11px; color: #4d5a61; }
.field.check { display: flex; align-items: center; gap: 6px; }
.field input[type='text'] { display: block; width: 100%; margin-top: 4px; padding: 6px 8px; border: 1px solid #b9c3c8; border-radius: 3px; font-size: 12px; }
.dialog-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.dialog-foot button { padding: 5px 14px; font-size: 12px; border: 1px solid #a9b8bf; background: #fff; border-radius: 3px; cursor: pointer; }
.dialog-foot .primary { background: #29353a; border-color: #29353a; color: #fff; }
.dialog-foot .primary:disabled { opacity: 0.45; cursor: default; }
</style>