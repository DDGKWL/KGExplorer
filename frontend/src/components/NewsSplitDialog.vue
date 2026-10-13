<script setup>
import { computed, ref } from 'vue';
import { closeNewsSplitDialog, newsV6Store, splitNode } from '../data/newsV6Store.js';

const store = newsV6Store;

const node = computed(() => (
  (store.graph.nodes || []).find((item) => item.id === store.splitTargetId)
));
const isEvent = computed(() => node.value?.type === 'event');
const eventEntry = computed(() => (
  (store.comparisons.events || []).find((item) => item.event_id === store.splitTargetId)
));
// Events with a comparisons entry split by reports; everything else by edges.
const mode = computed(() => (isEvent.value && eventEntry.value ? 'reports' : 'edges'));

const nodeEdges = computed(() => (
  (store.graph.edges || []).filter(
    (edge) => edge.source === store.splitTargetId || edge.target === store.splitTargetId,
  )
));

const label = ref('');
const note = ref('');
const pickedEdges = ref([]);
const pickedReportIds = ref([]);

function neighborLabel(edge) {
  const otherId = edge.source === store.splitTargetId ? edge.target : edge.source;
  const other = (store.graph.nodes || []).find((item) => item.id === otherId);
  return other?.label || otherId;
}

const totalReports = computed(() => (eventEntry.value?.reports || []).length);
const pickedReports = computed(() => pickedReportIds.value.length);
const canConfirm = computed(() => {
  if (!label.value.trim()) return false;
  if (mode.value === 'reports') {
    // the original event must keep at least one report
    return pickedReports.value > 0 && pickedReports.value < totalReports.value;
  }
  // the original node must keep at least one edge so it stays connected
  return pickedEdges.value.length > 0 && pickedEdges.value.length < nodeEdges.value.length;
});

function confirmSplit() {
  if (!canConfirm.value) return;
  splitNode({
    label: label.value.trim(),
    edges: pickedEdges.value,
    reportIds: [...pickedReportIds.value],
    note: note.value.trim(),
  });
}
</script>

<template>
  <div class="dialog-mask" @click.self="closeNewsSplitDialog">
    <section v-if="node" class="dialog-card" role="dialog" aria-modal="true" aria-label="Split node">
      <header class="dialog-head">
        <strong>Split node: {{ node.label }}</strong>
        <button type="button" class="close" aria-label="Close" @click="closeNewsSplitDialog">×</button>
      </header>

      <template v-if="mode === 'reports'">
        <p class="hint">
          Pick the reports to move into the new event. Their evidence-bearing edges move with
          them (edges shared with kept reports are duplicated), and attribute values of moved
          reports are carried over. The original event keeps the rest. Fully undoable.
        </p>
        <div class="pick-list">
          <label
            v-for="report in eventEntry.reports"
            :key="report.report_id"
            class="pick-row"
          >
            <input v-model="pickedReportIds" type="checkbox" :value="report.report_id" />
            <span class="pick-label">
              <b>{{ report.domain }}</b>
              {{ report.title }}
            </span>
          </label>
        </div>
        <p class="counter">{{ pickedReports }} / {{ totalReports }} reports picked</p>
      </template>

      <template v-else>
        <p class="hint">
          Pick the edges to move to the new node ({{ node.type }}). The original node keeps at
          least one edge so it stays connected. Fully undoable.
        </p>
        <div class="pick-list">
          <label v-for="(edge, index) in nodeEdges" :key="index" class="pick-row">
            <input v-model="pickedEdges" type="checkbox" :value="edge" />
            <span class="pick-label">
              <b>{{ neighborLabel(edge) }}</b> · {{ edge.relation || 'related' }}<template v-if="(edge.report_ids || []).length"> · {{ edge.report_ids.length }} reports</template>
            </span>
          </label>
        </div>
        <p class="counter">{{ pickedEdges.length }} / {{ nodeEdges.length }} edges picked</p>
      </template>

      <label class="field">
        New node name
        <input v-model="label" type="text" placeholder="Name of the split-off node" />
      </label>
      <label class="field">
        Note (optional)
        <input v-model="note" type="text" placeholder="Why is this node split?" />
      </label>
      <footer class="dialog-foot">
        <button type="button" @click="closeNewsSplitDialog">Cancel</button>
        <button type="button" class="primary" :disabled="!canConfirm" @click="confirmSplit">Split</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.dialog-mask { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; background: #26323866; }
.dialog-card { width: 460px; max-width: calc(100vw - 40px); max-height: 82vh; overflow-y: auto; background: #fff; border-radius: 6px; padding: 14px 16px; box-shadow: 0 10px 30px #0006; }
.dialog-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; font-size: 14px; }
.close { border: none; background: transparent; font-size: 16px; line-height: 1; cursor: pointer; }
.hint { margin: 0 0 10px; font-size: 11px; color: #68777e; line-height: 1.5; }
.pick-list { border: 1px solid #dce2e4; border-radius: 4px; margin-bottom: 8px; max-height: 240px; overflow-y: auto; }
.pick-row { display: flex; align-items: flex-start; gap: 8px; padding: 7px 10px; border-bottom: 1px solid #eef1f2; cursor: pointer; }
.pick-row:last-child { border-bottom: none; }
.pick-row:hover { background: #f4f7f8; }
.pick-row input { margin-top: 2px; }
.pick-label { font-size: 11px; line-height: 1.45; color: #33424a; }
.pick-label b { color: #20292e; }
.counter { margin: 0 0 8px; font-size: 10px; color: #748188; text-align: right; }
.field { display: block; margin: 8px 0; font-size: 11px; color: #4d5a61; }
.field input[type='text'] { display: block; width: 100%; margin-top: 4px; padding: 6px 8px; border: 1px solid #b9c3c8; border-radius: 3px; font-size: 12px; }
.dialog-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.dialog-foot button { padding: 5px 14px; font-size: 12px; border: 1px solid #a9b8bf; background: #fff; border-radius: 3px; cursor: pointer; }
.dialog-foot .primary { background: #29353a; border-color: #29353a; color: #fff; }
.dialog-foot .primary:disabled { opacity: 0.45; cursor: default; }
</style>