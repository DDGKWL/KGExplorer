<script setup>
import { computed, ref, watch } from 'vue';
import {
  store,
  CLASS_COLORS,
  CLASS_LABELS,
  DECISION_LABELS,
  LLM_VERDICT_LABELS,
  FRAMING_VERDICT_LABELS,
  displayCls,
  isOpenConflict,
} from '../data/store.js';
const ARTIFACTS = store.artifacts;
const SOURCES = store.sources;

const props = defineProps({
  selectedArtifactId: { type: String, default: null },
  selectedSlotId: { type: String, default: null },
});
const emit = defineEmits(['select-slot', 'adjudicated', 'pin-slot']);

const artifact = computed(() => ARTIFACTS.find((a) => a.id === props.selectedArtifactId) || null);
const slot = computed(() => artifact.value?.slots.find((s) => s.id === props.selectedSlotId) || null);
const canAdjudicate = computed(
  () => !slot.value?.reviewReadiness || slot.value.reviewReadiness === 'P1_expert_review',
);
const readinessLabel = computed(() => ({
  P1_expert_review: 'Ready for expert review',
  P2_data_cleanup: 'Data cleanup required',
  P3_alignment_review: 'Alignment review required',
  Exclude_lexical_variant: 'Lexical variant only',
}[slot.value?.reviewReadiness] || 'Not machine-audited'));
const valueGroups = computed(() => {
  if (!slot.value) return [];
  const groups = new Map();
  slot.value.assertions.forEach((assertion) => {
    const value = assertion.raw || 'not recorded';
    const group = groups.get(value) || { value, sources: [], sourceIds: [] };
    group.sourceIds.push(assertion.source);
    group.sources.push(SOURCES[assertion.source]?.title || assertion.source);
    groups.set(value, group);
  });
  return [...groups.values()].sort((a, b) => b.sources.length - a.sources.length);
});
const selectedGroup = computed(
  () => valueGroups.value.find((group) => group.value === selectedValue.value) || null,
);
const needsValue = computed(() => !selectedValue.value);
const openCandidateCount = computed(
  () => artifact.value?.slots.filter((item) => isOpenConflict(item)).length || 0,
);

const selectedValue = ref('');
const draft = ref('');

function slotListLabel(item) {
  if (item.decision?.action) return DECISION_LABELS[item.decision.action] || item.decision.action;
  const base = item.reviewReadiness
    ? item.reviewReadiness.replace('P1_', '').replace('P2_', '').replace('P3_', '').replace('Exclude_', '')
    : CLASS_LABELS[item.cls];
  // ✦ marks slots with a DeepSeek semantic verdict; ◆ marks DORIS
  // framing conflicts (Case 3 · News, third judgment method).
  const marks = `${item.llmVerdict ? '✦' : ''}${item.framingVerdict === 'framing_conflict' ? '◆' : ''}`;
  return marks ? `${marks} ${base}` : base;
}

watch(slot, (current) => {
  selectedValue.value = current?.decision?.value || '';
  if (current?.decision?.text) {
    draft.value = current.decision.text;
    return;
  }
  draft.value = current
    ? `待专家核验: 该属性在${current.assertions.filter((a) => a.raw).length}个来源中出现可疑差异。初步解释: ${current.note}${current.belief[0]?.value ? ` 可作为修订候选: ${current.belief[0].value}。` : ''}`
    : '';
});

function selectGroup(group) {
  selectedValue.value = group.value;
  if (group.value && group.value !== 'not recorded') {
    draft.value = `修订候选: ${group.value}（${group.sources.join(' · ')}）。`;
  }
}

function decide(action) {
  if (!slot.value) return;
  if (!canAdjudicate.value && action !== 'keep_open') return;
  if ((action === 'adopt' || action === 'flag_error') && !selectedValue.value) return;
  emit('adjudicated', {
    slotId: slot.value.id,
    action,
    text: draft.value,
    value: selectedValue.value || null,
    sources: selectedGroup.value?.sourceIds || [],
  });
}
</script>

<template>
  <div class="adjudication-body">
    <p v-if="!artifact" class="adj-empty">No selection.</p>

    <template v-else>
      <div class="adj-artifact">
        <strong>{{ artifact.name }}</strong>
        <span>{{ artifact.sources.length }} source(s) · {{ openCandidateCount }} open candidate(s)</span>
      </div>

      <ul class="adj-slot-list">
        <li
          v-for="s in artifact.slots"
          :key="s.id"
          :class="{
            active: s.id === selectedSlotId,
            decided: s.decision && s.decision.action !== 'keep_open',
          }"
          @click="emit('select-slot', s.id)"
        >
          <i :style="{ background: CLASS_COLORS[displayCls(s)] }"></i>
          {{ s.label }}
          <em>{{ slotListLabel(s) }}</em>
        </li>
      </ul>

      <template v-if="slot">
        <h3>Current attribute</h3>
        <div class="adj-focus-card">
          <strong>{{ slot.label }}</strong>
          <span>{{ slot.assertions.filter((a) => a.raw).length }} recorded · {{ slot.assertions.filter((a) => !a.raw).length }} missing</span>
          <p>{{ slot.note }}</p>
        </div>

        <div v-if="slot.llmVerdict" class="adj-llm-card" :data-verdict="slot.llmVerdict">
          <header>
            <strong>大模型判定 · {{ LLM_VERDICT_LABELS[slot.llmVerdict] || slot.llmVerdict }}</strong>
            <span>DeepSeek</span>
          </header>
          <p>{{ slot.llmReason || '未返回判定理由。' }}</p>
        </div>

        <div v-if="slot.framingVerdict" class="adj-framing-card" :data-verdict="slot.framingVerdict">
          <header>
            <strong>框架判定 · {{ FRAMING_VERDICT_LABELS[slot.framingVerdict] || slot.framingVerdict }}</strong>
            <span>DORIS · FrameNet</span>
          </header>
          <p>{{ slot.framingReason || '未返回判定理由。' }}</p>
          <ul v-if="slot.framingEvidence?.length" class="adj-framing-evidence">
            <li v-for="(entry, index) in slot.framingEvidence.slice(0, 6)" :key="index">
              ◆ {{ entry.frame }} · {{ entry.fe }}
              <small v-if="entry.sentence">“{{ entry.sentence }}”</small>
              <small v-if="entry.domain">{{ entry.domain }}</small>
            </li>
          </ul>
        </div>

        <div v-if="slot.decision" class="adj-decision-banner" :data-action="slot.decision.action">
          <strong>{{ DECISION_LABELS[slot.decision.action] || slot.decision.action }}</strong>
          <span v-if="slot.decision.value">{{ slot.decision.value }}</span>
          <span v-else>No value group selected.</span>
        </div>

        <div v-if="slot.audit" class="adj-audit-card" :data-state="slot.reviewReadiness">
          <header>
            <strong>{{ readinessLabel }}</strong>
            <span>{{ slot.audit.identity_grade }}</span>
          </header>
          <p>{{ slot.audit.machine_audit_note }}</p>
          <div v-if="slot.auditFlags.length">
            <span v-for="flag in slot.auditFlags" :key="flag">{{ flag }}</span>
          </div>
        </div>

        <h3>Observed value groups</h3>
        <p class="adj-select-hint">
          {{ store.caseId === 'news'
            ? 'Select a group to inspect which outlet made this description.'
            : 'Select a group before 采信修订 or 标记讹误. Detection text stays unchanged.' }}
        </p>
        <ul class="adj-value-groups">
          <li
            v-for="group in valueGroups"
            :key="group.value"
            :class="{ selected: selectedValue === group.value }"
            @click="selectGroup(group)"
          >
            <strong>{{ group.value }}</strong>
            <span>{{ group.sources.length }} source(s): {{ group.sources.join(' · ') }}</span>
          </li>
        </ul>

        <p class="adj-evidence-hint">
          {{ store.caseId === 'news'
            ? 'The matrix compares how each outlet describes this event. Select a value group and inspect its source sentence in the Evidence Tray.'
            : 'Full source text and image evidence are shown in the Evidence Tray below. The matrix is the comparison overview; this panel is for adjudication.' }}
        </p>

        <h3 v-if="store.caseId !== 'news'">Interpretation hypotheses</h3>
        <div v-if="store.caseId !== 'news'" class="adj-belief">
          <div
            v-for="b in slot.belief"
            :key="b.value"
            :style="{ width: Math.round(b.p * 100) + '%', background: CLASS_COLORS[slot.cls] }"
            :class="{ minor: b.p < 0.5 }"
          >
            {{ b.value }} {{ Math.round(b.p * 100) }}%
          </div>
        </div>

        <h3 v-if="store.caseId !== 'news'">Evidence-grounded revision draft</h3>
        <textarea v-if="store.caseId !== 'news'" v-model="draft" rows="4"></textarea>

        <div class="adj-actions">
          <button v-if="store.caseId !== 'news'" type="button" :disabled="!canAdjudicate || needsValue" @click="decide('adopt')">采信修订</button>
          <button v-if="store.caseId !== 'news'" type="button" :disabled="!canAdjudicate" @click="decide('compatible')">标注兼容</button>
          <button v-if="store.caseId !== 'news'" type="button" :disabled="!canAdjudicate || needsValue" @click="decide('flag_error')">标记讹误</button>
          <button v-if="store.caseId !== 'news'" type="button" @click="decide('keep_open')">存疑待考</button>
          <button type="button" @click="emit('pin-slot', artifact.id, slot.id)">加入证据托盘</button>
        </div>
      </template>

    </template>
  </div>
</template>
