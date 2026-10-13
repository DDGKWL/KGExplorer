<script setup>
import { computed, ref } from 'vue';
import { newsV6Store, selectNewsEvent, framingVerdictFor } from '../data/newsV6Store.js';

const expandedAttribute = ref('participants');
const articleText = ref(null);
const articleLoading = ref(false);

const selectedEvent = computed(() => (
  newsV6Store.comparisons.events.find((event) => event.event_id === newsV6Store.selectedEventId)
  || newsV6Store.comparisons.events[0]
));
const selectedEntity = computed(() => (
  newsV6Store.graph.nodes.find((node) => node.id === newsV6Store.selectedEntityId)
));
const entityLinks = computed(() => {
  if (!selectedEntity.value) return [];
  const eventIds = new Set(
    newsV6Store.graph.edges
      .filter((edge) => {
        const source = typeof edge.source === 'object' ? edge.source.id : edge.source;
        const target = typeof edge.target === 'object' ? edge.target.id : edge.target;
        return source === selectedEntity.value.id || target === selectedEntity.value.id;
      })
      .map((edge) => {
        const source = typeof edge.source === 'object' ? edge.source.id : edge.source;
        const target = typeof edge.target === 'object' ? edge.target.id : edge.target;
        return source.startsWith('event:') ? source : target;
      }),
  );
  return newsV6Store.comparisons.events.filter((event) => eventIds.has(event.event_id));
});

const STATUS_LABELS = {
  consensus: '一致',
  partial_overlap: '部分重合',
  different: '不同',
  missing_in_some_reports: '部分报道缺失',
  not_available: '未抽取',
};
const ATTRIBUTE_LABELS = {
  participants: '参与者',
  actions: '动作',
  time: '时间',
  place: '地点',
  cause: '原因',
  manner: '方式',
  result: '结果',
  attributed_to: '消息来源',
  attitude: '报道态度',
  roles: '语义角色',
};

function reportValues(comparison, reportId) {
  return comparison.reports?.[reportId] || [];
}

// ---------------------------------------------------------------------------
// DORIS framing verdicts (third judgment method, additive to rule + LLM).
// Verdict + per-source frame-element evidence come from the sidecar at
// /api/news/framing-verdicts; slots without a sidecar entry stay rule-colored.
// ---------------------------------------------------------------------------
const FRAMING_LABELS = {
  framing_conflict: '框架冲突 (DORIS)',
  framing_consistent: '框架一致 (DORIS)',
  insufficient: '框架证据不足 (DORIS)',
};

function framingInfo(comparison) {
  const verdict = framingVerdictFor(selectedEvent.value, comparison);
  if (!verdict) return null;
  return {
    verdict: verdict.verdict,
    label: FRAMING_LABELS[verdict.verdict] || verdict.verdict,
    reason: verdict.reason || '',
    evidence: Array.isArray(verdict.evidence) ? verdict.evidence : [],
  };
}

function framingEvidenceFor(comparison, reportId) {
  const info = framingInfo(comparison);
  if (!info) return [];
  return info.evidence.filter((entry) => entry.report_id === reportId);
}

async function openArticle(report) {
  articleLoading.value = true;
  articleText.value = { title: report.title, body: '' };
  try {
    const response = await fetch(report.text_url);
    articleText.value.body = response.ok ? await response.text() : `Unable to load article (${response.status})`;
  } finally {
    articleLoading.value = false;
  }
}
</script>

<template>
  <div class="news-comparison">
    <section v-if="selectedEntity" class="entity-focus">
      <small>{{ selectedEntity.type }}</small>
      <h2>{{ selectedEntity.label }}</h2>
      <p>{{ selectedEntity.aliases?.join(' · ') }}</p>
      <h3>相关事件</h3>
      <button
        v-for="event in entityLinks"
        :key="event.event_id"
        type="button"
        @click="selectNewsEvent(event.event_id)"
      >{{ event.canonical_name }}</button>
    </section>

    <template v-if="selectedEvent">
      <header class="comparison-head">
        <small>{{ selectedEvent.event_type }}</small>
        <h2>{{ selectedEvent.canonical_name }}</h2>
        <p>{{ selectedEvent.report_count }} 篇报道 · {{ selectedEvent.times || '时间未确认' }}</p>
      </header>

      <section class="attribute-list">
        <article
          v-for="comparison in selectedEvent.comparisons"
          :key="comparison.attribute"
          :class="['attribute-card', comparison.status]"
        >
          <button
            type="button"
            class="attribute-head"
            @click="expandedAttribute = expandedAttribute === comparison.attribute ? '' : comparison.attribute"
          >
            <strong>{{ ATTRIBUTE_LABELS[comparison.attribute] || comparison.attribute }}</strong>
            <span>
              <span
                v-if="framingInfo(comparison)"
                :class="['framing-badge', framingInfo(comparison).verdict]"
                :title="framingInfo(comparison).reason"
              >{{ framingInfo(comparison).label }}</span>
              {{ STATUS_LABELS[comparison.status] || comparison.status }}
            </span>
          </button>
          <div v-if="expandedAttribute === comparison.attribute" class="attribute-reports">
            <div v-for="report in selectedEvent.reports" :key="report.report_id" class="attribute-source">
              <b>{{ report.domain }}</b>
              <template v-if="reportValues(comparison, report.report_id).length">
                <p v-for="(item, index) in reportValues(comparison, report.report_id)" :key="index">
                  {{ item.display }}
                  <small v-if="item.evidence">{{ item.evidence }}</small>
                </p>
                <p
                  v-for="(entry, feIndex) in framingEvidenceFor(comparison, report.report_id)"
                  :key="`framing-${feIndex}`"
                  class="fe-evidence"
                >
                  ◆ {{ entry.frame }} · {{ entry.fe }}
                  <small v-if="entry.sentence">{{ entry.sentence }}</small>
                </p>
              </template>
              <em v-else>该报道未提及</em>
            </div>
          </div>
        </article>
      </section>

      <section class="report-list">
        <h3>报道与证据</h3>
        <article v-for="report in selectedEvent.reports" :key="report.report_id" class="report-card">
          <img v-if="report.image_paths?.length" :src="report.image_paths[0]" :alt="report.title" loading="lazy" />
          <div>
            <small>{{ report.domain }}</small>
            <strong>{{ report.title }}</strong>
            <button type="button" @click="openArticle(report)">查看正文</button>
            <a :href="report.url" target="_blank" rel="noreferrer">原始链接</a>
          </div>
        </article>
      </section>
    </template>

    <div v-if="articleText" class="article-reader" role="dialog" aria-modal="true">
      <header>
        <strong>{{ articleText.title }}</strong>
        <button type="button" @click="articleText = null">×</button>
      </header>
      <p v-if="articleLoading">Loading…</p>
      <pre v-else>{{ articleText.body }}</pre>
    </div>
  </div>
</template>

<style scoped>
.news-comparison { height: 100%; overflow: auto; padding: 14px; color: #29343a; }
.comparison-head, .entity-focus { padding-bottom: 12px; border-bottom: 1px solid #d8dddf; }
h2, h3, p { margin: 4px 0; }
.entity-focus button { display: block; width: 100%; margin-top: 6px; padding: 7px; text-align: left; }
.attribute-list { display: grid; gap: 8px; margin-top: 12px; }
.attribute-card { border: 1px solid #d5dcdf; border-radius: 6px; overflow: hidden; background: #fff; }
.attribute-card.different { border-left: 4px solid #b86f3d; }
.attribute-card.partial_overlap { border-left: 4px solid #b29a62; }
.attribute-card.consensus { border-left: 4px solid #5d8c82; }
.attribute-head { width: 100%; display: flex; justify-content: space-between; padding: 9px; border: 0; background: #f7f8f8; }
.attribute-head span { color: #66757c; }
.attribute-reports { padding: 4px 9px 9px; }
.attribute-source { padding: 7px 0; border-top: 1px solid #edf0f1; }
.attribute-source p { font-size: 12px; }
.attribute-source small { display: block; margin-top: 3px; color: #718087; }
.attribute-source em { color: #9aa5aa; font-size: 12px; }
.framing-badge { display: inline-block; margin-right: 6px; padding: 1px 7px; border-radius: 9px; font-size: 11px; }
.framing-badge.framing_conflict { background: #f4dcd2; color: #9f4328; }
.framing-badge.framing_consistent { background: #d9e8e3; color: #2f7268; }
.framing-badge.insufficient { background: #e6eaec; color: #718087; }
.fe-evidence { color: #9f4328; font-size: 11px; }
.fe-evidence small { display: block; color: #718087; margin-top: 1px; }
.report-list { margin-top: 16px; }
.report-card { display: grid; grid-template-columns: 72px 1fr; gap: 9px; padding: 9px 0; border-top: 1px solid #dfe4e6; }
.report-card img { width: 72px; height: 54px; object-fit: cover; }
.report-card strong { display: block; font-size: 12px; margin: 3px 0 7px; }
.report-card button, .report-card a { margin-right: 8px; font-size: 11px; }
.article-reader { position: fixed; z-index: 50; inset: 8% 10%; background: #fff; border: 1px solid #7e8b91; box-shadow: 0 12px 42px #0003; display: flex; flex-direction: column; }
.article-reader header { display: flex; justify-content: space-between; padding: 12px; border-bottom: 1px solid #d9dfe1; }
.article-reader pre { padding: 16px; margin: 0; overflow: auto; white-space: pre-wrap; font: 13px/1.55 system-ui, sans-serif; }
</style>
