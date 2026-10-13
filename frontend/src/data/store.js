import { reactive } from 'vue';
import { newsV6Store } from './newsV6Store.js';

export const CLASS_COLORS = {
  A: '#9f4328',
  B: '#8f6b2f',
  C: '#435d73',
  consensus: '#2f7268',
  single: '#8a979d',
  missing: '#b7c1c5',
};

export const CLASS_LABELS = {
  A: 'differ · 待考订',
  B: 'overlap · 部分重叠',
  C: 'suspected · 疑似讹误',
  consensus: '一致',
  single: 'single-source · 仅一源',
  missing: '缺失',
};

export const DECISION_LABELS = {
  adopt: '采信修订',
  compatible: '标注兼容',
  flag_error: '标记讹误',
  keep_open: '存疑待考',
};

const FALLBACK_COLORS = ['#1f5a7a', '#a45c1d', '#6b4e9b', '#2d7a6b', '#9f4328', '#3d6b8f', '#8f6b2f', '#435d73'];

const BOOK_COLORS = {
  Gq: '#1f5a7a',
  Yjzq: '#a45c1d',
  QNQY: '#6b4e9b',
  CQL: '#2d7a6b',
  nasa: '#1f5a7a',
  simbad: '#a45c1d',
  gaia: '#6b4e9b',
  AU: '#1f5a7a',
  UK: '#a45c1d',
  US: '#2d7a6b',
};

export const CASES = [
  { id: 'guqin', label: 'Case 1 · Guqin' },
  { id: 'astro', label: 'Case 2 · Astronomy' },
  { id: 'news', label: 'Case 3 · News' },
];

const CASE_DEFAULTS = {
  guqin: {
    title: 'Guqin Heritage Knowledge Revision',
    note: 'Multisource guqin books with scanned-part evidence',
    label: 'Case 1 · Guqin',
  },
  astro: {
    title: 'NASA, SIMBAD and Gaia Host Catalog Conflicts',
    note: 'v2 host records compared across NASA, SIMBAD, and Gaia on shared attribute slots.',
    label: 'Case 2 · Astronomy',
  },
  news: {
    title: 'Cross-outlet News Claim Conflicts',
    note: 'Articles about the same event compared on shared claims, with report text and images.',
    label: 'Case 3 · News',
  },
};

export const store = reactive({
  loaded: false,
  error: null,
  title: '',
  caseNote: '',
  caseLabel: '',
  caseId: '',
  // Case 3 · News: 'rule' keeps deterministic pipeline colors; 'llm' applies
  // DeepSeek semantic verdicts on top (rule colors remain the fallback).
  judgeMode: 'rule',
  sources: {},
  artifacts: [],
  stats: {},
  evidenceAudit: {},
  qualityIssues: [],
  projection: { ready: false, methods: {} },
  adjudicationRev: 0,
});

export function displayCls(slot) {
  const action = slot?.decision?.action;
  if (action && action !== 'keep_open') return 'consensus';
  // Case 3 · News LLM mode: a semantic verdict overrides the rule class;
  // missing or uncertain verdicts fall back to the deterministic rule color.
  if (store.judgeMode === 'llm' && slot?.llmVerdict) {
    const mapped = LLM_CLS_MAP[slot.llmVerdict];
    if (mapped) return mapped;
  }
  // Case 3 · News DORIS mode: framing verdict overrides the rule color the
  // same way; slots without a framing verdict keep the rule color.
  if (store.judgeMode === 'framing' && slot?.framingVerdict) {
    const mapped = FRAMING_CLS_MAP[slot.framingVerdict];
    if (mapped) return mapped;
  }
  return slot?.cls || 'missing';
}

export function isOpenConflict(slot) {
  return ['A', 'B', 'C'].includes(displayCls(slot));
}

export function worstClass(artifact) {
  const slots = artifact?.slots || [];
  if (!slots.length) return 'consensus';
  if (slots.some((slot) => displayCls(slot) === 'A')) return 'A';
  if (slots.some((slot) => displayCls(slot) === 'C')) return 'C';
  if (slots.some((slot) => displayCls(slot) === 'B')) return 'B';
  return 'consensus';
}

export function applyDecision(artifactId, slotId, decision) {
  const artifact = store.artifacts.find((item) => item.id === artifactId);
  const slot = artifact?.slots.find((item) => item.id === slotId);
  if (!slot) return null;
  slot.decision = decision;
  store.adjudicationRev += 1;
  return slot;
}

const STATUS_TO_CLS = {
  differ: 'A',
  overlap: 'B',
  evidence: 'consensus',
  consensus: 'consensus',
  single_source: 'single',
};

// Case 3 · News DeepSeek semantic verdicts (additive to the rule status).
// uncertain / missing verdicts map to null so the rule color stays in effect.
export const LLM_CLS_MAP = {
  consistent: 'consensus',
  conflict: 'A',
  uncertain: null,
};

export const LLM_VERDICT_LABELS = {
  consistent: '语义一致',
  conflict: '语义冲突',
  uncertain: '无法判定',
};

// Case 3 · News DORIS/FrameNet framing verdicts (third judgment method,
// additive to rule + LLM). insufficient keeps the rule color in effect.
export const FRAMING_CLS_MAP = {
  framing_conflict: 'A',
  framing_consistent: 'consensus',
  insufficient: null,
};

export const FRAMING_VERDICT_LABELS = {
  framing_conflict: '框架冲突',
  framing_consistent: '框架一致',
  insufficient: '证据不足',
};

function beliefFrom(assertions) {
  const present = assertions.filter((a) => (a.values || []).length);
  if (!present.length) return [];
  const counts = new Map();
  present.forEach((a) => {
    const key = a.values.join('、');
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return [...counts.entries()]
    .map(([value, n]) => ({ value, p: n / present.length }))
    .sort((x, y) => y.p - x.p);
}

function adaptArtifact(raw) {
  const sourceAssertions = (a) => {
    const scopedValues = a.scope
      ? a.scope.object_values || []
      : a.audit?.object_values
        ? a.audit.object_values
        : null;
    const values = scopedValues === null ? a.values || [] : scopedValues;
    return {
      source: a.source ?? a.book,
      recordId: a.record_id ?? a.recordId,
      recordName: a.record_name ?? a.recordName,
      values,
      normalizedValues: scopedValues || a.normalized_values || a.normalizedValues || values,
      valueDetails: a.value_details || a.valueDetails || [],
      raw: values.length ? values.join('、') : null,
      imagePath: a.image_path || a.imagePath || null,
      metadata: a.metadata || {},
      audit: a.audit || null,
    };
  };
  const caus = raw.caus || raw.slots || [];
  const consensusSlots = caus.filter((c) => c.status === 'consensus');
  const entities = [];
  ['form', 'dating'].forEach((slotId) => {
    const cau = caus.find((c) => c.slot === slotId);
    const firstPresent = cau?.assertions.find((a) => (a.values || []).length);
    if (firstPresent) entities.push(firstPresent.values[0]);
  });
  return {
    id: raw.artifact_id,
    name: raw.name,
    aligned: raw.aligned,
    alignmentStatus: raw.alignment_status || (raw.aligned && raw.books?.length > 1
      ? 'confirmed'
      : raw.books?.length > 1
        ? 'candidate'
        : raw.books?.length === 1
          ? 'source-local'
          : 'unresolved'),
    sources: raw.books,
    records: raw.records,
    featured: !!raw.featured,
    imagesByBook: raw.images || {},
    entities: raw.entities?.length ? raw.entities : entities,
    consensusCount: consensusSlots.length,
    alignmentCandidates: raw.alignment_candidates || [],
    auditIdentityGrade: raw.audit_identity_grade || 'source_local',
    auditReadinessCounts: raw.audit_readiness_counts || {},
    projection: raw.projection || null,
    slots: caus.map((c) => ({
      id: c.slot,
      label: c.label,
      status: c.status,
      conflictType: c.conflict_type || c.conflictType || null,
      cls: STATUS_TO_CLS[c.status],
      assertions: c.assertions.map(sourceAssertions),
      audit: c.audit || null,
      reviewReadiness: c.audit?.review_readiness || null,
      auditFlags: c.audit?.audit_flags || [],
      belief: beliefFrom(c.assertions),
      decision: null,
      // DeepSeek semantic verdict (Case 3 · News) is stamped on the raw slots
      // by adaptNewsV6; it must survive this field-by-field rebuild or the
      // 判定方法 toggle (store.judgeMode / displayCls / llmVerdictCount) and
      // the AdjudicationPanel reason card never see it. Rule status and cls
      // stay untouched (additive design).
      llmVerdict: c.llmVerdict || null,
      llmReason: c.llmReason || null,
      // DORIS framing verdict fields ride the same field-by-field rebuild
      // (additive; framingEvidence is the per-source FE evidence array).
      framingVerdict: c.framingVerdict || null,
      framingReason: c.framingReason || null,
      framingEvidence: c.framingEvidence || null,
      note:
        c.audit?.machine_audit_note
          ? c.audit.machine_audit_note
          : c.status === 'differ'
          ? '自动检测:不同标本来源的名称存在差异,进入冲突候选。'
          : c.status === 'single_source'
            ? '仅有一个来源记录该属性，不代表跨来源共识。'
            : c.status === 'consensus'
              ? '多个来源取值一致，可作为当前共识观察。'
              : c.status === 'evidence'
                ? '对象证据:该对象已关联多个来源记录,可逐一查看图像、原文和来源元数据。'
                : '自动检测:取值部分重叠,疑为详略差异,可考虑合并。',
    })),
  };
}

function sourceId(value) {
  return String(value || 'source')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'source';
}

// Case 3 · News sidecar verdicts fetched from /api/news/llm-verdicts. Kept in
// a module variable so manual graph corrections (refreshArtifactsFromNewsV6)
// re-apply them without a second request.
let newsLlmVerdicts = {};

// Case 3 · News DORIS/FrameNet framing sidecar (third judgment method) fetched
// from /api/news/framing-verdicts. Same module-variable pattern so
// refreshArtifactsFromNewsV6 re-applies it; empty until the batch script runs.
let newsFramingVerdicts = {};

function newsVerdictKey(eventId, comparison) {
  // Must match llm_judge.verdict_key() in backend/llm_judge.py exactly:
  // event_id::attribute::sorted "report_id:canonical" joined by ';'.
  const parts = [];
  Object.entries(comparison.reports || {}).forEach(([reportId, items]) => {
    (items || []).forEach((item) => {
      const canonical = String(item?.canonical || '').trim().toLowerCase();
      if (canonical) parts.push(`${reportId}:${canonical}`);
    });
  });
  parts.sort();
  return `${eventId}::${comparison.attribute}::${parts.join(';')}`;
}

function adaptNewsV6(graph, comparisons, verdicts = newsLlmVerdicts, framingVerdicts = newsFramingVerdicts) {
  const entityLabels = new Map(
    (graph.nodes || []).filter((node) => node.type !== 'event').map((node) => [node.id, node.label]),
  );
  const canonicalDisplay = (attribute, value) => {
    if (attribute === 'participants') return entityLabels.get(value) || value;
    if (attribute === 'roles' && value.includes('::')) {
      const [entityId, role] = value.split('::');
      return `${entityLabels.get(entityId) || entityId} · ${role}`;
    }
    return value;
  };
  const labelByAttribute = {
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
  const slotId = (attribute) => (
    ['participants', 'actions', 'roles'].includes(attribute)
      ? `rel-${attribute}`
      : `attr-${attribute}`
  );
  const oldStatus = (status, reports) => {
    if (status === 'different') return 'differ';
    if (status === 'partial_overlap' || status === 'missing_in_some_reports') return 'overlap';
    if (status === 'consensus') return 'consensus';
    const present = Object.values(reports || {}).filter((values) => values.length).length;
    return present > 1 ? 'overlap' : 'single_source';
  };
  const sources = {};
  const artifacts = comparisons.events.map((event) => {
    const reportSource = {};
    event.reports.forEach((report) => {
      const id = sourceId(report.domain);
      reportSource[report.report_id] = id;
      sources[id] = report.domain;
    });
    const caus = event.comparisons.map((comparison) => {
      const verdict = verdicts[newsVerdictKey(event.event_id, comparison)] || null;
      const framing = framingVerdicts[newsVerdictKey(event.event_id, comparison)] || null;
      return {
      slot: slotId(comparison.attribute),
      label: labelByAttribute[comparison.attribute] || comparison.attribute,
      status: oldStatus(comparison.status, comparison.reports),
      conflict_type: comparison.status,
      assertions: event.reports.map((report) => {
        const items = comparison.reports?.[report.report_id] || [];
        const values = items.map((item) => item.display).filter(Boolean);
        const normalized = items
          .map((item) => canonicalDisplay(comparison.attribute, item.canonical))
          .filter(Boolean);
        const evidence = items.map((item) => item.evidence).filter(Boolean);
        return {
          source: reportSource[report.report_id],
          record_id: report.url_hash,
          record_name: report.title,
          values,
          normalized_values: normalized,
          value_details: items.map((item) => ({
            raw: item.display,
            canonical: canonicalDisplay(comparison.attribute, item.canonical),
            value_type: comparison.attribute,
          })),
          image_path: report.image_paths?.[0] || null,
          metadata: {
            outlet: report.domain,
            url: report.url,
            text_url: report.text_url,
            image_paths: report.image_paths || [],
            image_files: (report.image_paths || []).map((path) => path.split('/').pop()),
            evidence_sentences: evidence,
          },
        };
      }),
      // Additive DeepSeek semantic verdict; the rule status is never overwritten.
      llmVerdict: verdict?.verdict || null,
      llmReason: verdict?.reason || null,
      // Additive DORIS framing verdict (framing_conflict / framing_consistent /
      // insufficient) with per-source frame-element evidence; same additive
      // contract - rule status and llmVerdict stay untouched.
      framingVerdict: framing?.verdict || null,
      framingReason: framing?.reason || null,
      framingEvidence: framing?.evidence || null,
      };
    });
    const sourceIds = event.reports.map((report) => reportSource[report.report_id]);
    const images = {};
    event.reports.forEach((report) => {
      if (report.image_paths?.length) images[reportSource[report.report_id]] = report.image_paths;
    });
    return {
      artifact_id: event.event_id,
      name: event.canonical_name,
      aligned: sourceIds.length > 1,
      alignment_status: sourceIds.length > 1 ? 'confirmed' : 'source-local',
      books: sourceIds,
      records: event.reports.map((report) => report.url_hash),
      featured: true,
      images,
      entities: event.comparisons
        .find((comparison) => comparison.attribute === 'participants')
        ?.canonical_values || [],
      caus,
    };
  });
  return {
    books: sources,
    artifacts,
    stats: {
      ...comparisons.summary,
      // Recomputed from the live event list so the counts stay correct after
      // manual merge/split corrections (comparisons.summary is static).
      artifacts: comparisons.events.length,
      records: comparisons.events.reduce((total, event) => total + (event.reports?.length || 0), 0),
      differ: (comparisons.summary?.status_counts?.different || 0),
      overlap: (comparisons.summary?.status_counts?.partial_overlap || 0)
        + (comparisons.summary?.status_counts?.missing_in_some_reports || 0),
      cross_event_links: graph.summary?.cross_event_links || 0,
      entities: graph.summary?.entities || 0,
    },
    case: {
      id: 'news',
      label: 'Case 3 · News',
      title: 'Cross-source News Narrative Analysis',
      note: 'Real-world events compared across outlets and connected through normalized entities.',
    },
  };
}

function resetStore() {
  store.loaded = false;
  store.error = null;
  store.title = '';
  store.caseNote = '';
  store.caseLabel = '';
  store.caseId = '';
  Object.keys(store.sources).forEach((key) => delete store.sources[key]);
  store.artifacts.splice(0, store.artifacts.length);
  store.stats = {};
  store.evidenceAudit = {};
  store.qualityIssues = [];
  store.projection = { ready: false, methods: {} };
  store.adjudicationRev = 0;
}

export async function loadStore(caseId = 'guqin') {
  const id = CASES.some((item) => item.id === caseId) ? caseId : 'guqin';
  resetStore();
  try {
    let data;
    if (id === 'news') {
      // The verdict sidecars are optional: an empty or failed response just
      // leaves every slot on rule colors.
      const [graphResponse, comparisonResponse, verdictResponse, framingResponse] = await Promise.all([
        fetch('/api/news/v6/graph'),
        fetch('/api/news/v6/comparisons'),
        fetch('/api/news/llm-verdicts').catch(() => null),
        fetch('/api/news/framing-verdicts').catch(() => null),
      ]);
      if (!graphResponse.ok || !comparisonResponse.ok) {
        throw new Error(`API graph=${graphResponse.status} comparisons=${comparisonResponse.status}`);
      }
      const graph = await graphResponse.json();
      const comparisons = await comparisonResponse.json();
      newsLlmVerdicts = verdictResponse?.ok
        ? (await verdictResponse.json())?.verdicts || {}
        : {};
      newsFramingVerdicts = framingResponse?.ok
        ? (await framingResponse.json())?.verdicts || {}
        : {};
      // Hydrate the shared graph-correction store (merge/split/undo dialogs
      // and the workspace toolbar read from it) with the same payload — no
      // second fetch and no separate load path.
      newsV6Store.graph = graph;
      newsV6Store.comparisons = comparisons;
      newsV6Store.selectionIds = [];
      newsV6Store.mergeDialogOpen = false;
      newsV6Store.splitTargetId = null;
      newsV6Store.lastAction = '';
      newsV6Store.undoStack = [];
      newsV6Store.loaded = true;
      data = adaptNewsV6(graph, comparisons, newsLlmVerdicts, newsFramingVerdicts);
    } else {
      const res = await fetch(`/api/case-data/${id}`);
      if (!res.ok) throw new Error(`API ${res.status}`);
      data = await res.json();
    }
    Object.entries(data.books).forEach(([bookId, title]) => {
      store.sources[bookId] = {
        id: bookId,
        title,
        color: BOOK_COLORS[bookId] || FALLBACK_COLORS[Object.keys(store.sources).length % FALLBACK_COLORS.length],
      };
    });
    const adapted = data.artifacts.map(adaptArtifact);
    // Keep the complete corpus in memory. Unaligned records are still
    // valid source-local knowledge and provide the context around contested
    // objects; alignment is an analytic state, not a visibility filter.
    store.artifacts.splice(0, store.artifacts.length, ...adapted);
    const defaults = CASE_DEFAULTS[id] || CASE_DEFAULTS.guqin;
    store.title = data.case?.title || defaults.title;
    store.caseNote = data.case?.note || defaults.note;
    store.caseLabel = data.case?.label || defaults.label;
    store.caseId = data.case?.id || id;
    store.unalignedCount = adapted.filter((artifact) => artifact.alignmentStatus !== 'confirmed').length;
    store.stats = data.stats;
    store.evidenceAudit = data.evidence_audit || {};
    store.qualityIssues = data.quality_issues || [];
    store.projection = data.projection || { ready: false, methods: {} };
    store.loaded = true;
  } catch (err) {
    store.error = String(err);
  }
}

// ---------------------------------------------------------------------------
// Case 3 · News manual graph corrections (merge/split/undo)
// ---------------------------------------------------------------------------
// After a correction mutates newsV6Store.graph, re-run the v6 adapter so every
// workspace surface (left panel, graph, evidence, worklist) reflects the new
// event set. Session adjudication decisions on surviving artifacts/slots are
// preserved, keyed by (artifactId, slotId); stale graph selections disappear
// naturally because removed artifacts no longer exist.
export function refreshArtifactsFromNewsV6() {
  if (store.caseId !== 'news' || !newsV6Store.loaded) return false;
  const previousDecisions = new Map();
  store.artifacts.forEach((artifact) => {
    const slotDecisions = new Map();
    artifact.slots.forEach((slot) => {
      if (slot.decision) slotDecisions.set(slot.id, slot.decision);
    });
    if (slotDecisions.size) previousDecisions.set(artifact.id, slotDecisions);
  });
  const data = adaptNewsV6(newsV6Store.graph, newsV6Store.comparisons, newsLlmVerdicts, newsFramingVerdicts);
  const adapted = data.artifacts.map(adaptArtifact);
  adapted.forEach((artifact) => {
    const slotDecisions = previousDecisions.get(artifact.id);
    if (!slotDecisions) return;
    artifact.slots.forEach((slot) => {
      const decision = slotDecisions.get(slot.id);
      if (decision) slot.decision = decision;
    });
  });
  store.artifacts.splice(0, store.artifacts.length, ...adapted);
  store.unalignedCount = adapted.filter((artifact) => artifact.alignmentStatus !== 'confirmed').length;
  store.stats = data.stats;
  store.adjudicationRev += 1;
  return true;
}
