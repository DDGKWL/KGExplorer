<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import KnowledgeGraphView from './components/KnowledgeGraphView.vue';
import NewsMergeDialog from './components/NewsMergeDialog.vue';
import NewsSplitDialog from './components/NewsSplitDialog.vue';
import ProjectionView from './components/ProjectionView.vue';
import NewsFramingView from './components/NewsFramingView.vue';
import AdjudicationPanel from './components/AdjudicationPanel.vue';
import {
  store,
  loadStore,
  CLASS_COLORS,
  CASES,
  applyDecision,
  isOpenConflict,
  DECISION_LABELS,
  refreshArtifactsFromNewsV6,
} from './data/store.js';
import { newsV6Store, openNewsMergeDialog, openNewsSplitDialog, undoLastAction, selectNewsEvent } from './data/newsV6Store.js';

const TREE_LIST_CAP = 48;
const selectedCaseId = ref('guqin');
const loadingCase = ref(false);
const selectedArtifactId = ref(null);
const selectedSlotId = ref(null);
const lensMode = ref('conflict');
const lensBrushedIds = ref([]);
// Case 3 · News: event nodes multi-selected in the graph via Ctrl/Cmd/Shift+
// click, feeding the merge/split/undo graph-correction toolbar.
const graphSelectionIds = ref([]);

function rankArtifact(artifact) {
  let score = 0;
  if (artifact.id === selectedArtifactId.value) score += 8;
  if (artifact.featured) score += 4;
  if (Object.keys(artifact.imagesByBook || {}).length) score += 2;
  return score;
}

async function loadCaseIntoView(caseId = selectedCaseId.value) {
  selectedCaseId.value = caseId;
  loadingCase.value = true;
  viewerImage.value = null;
  viewerText.value = null;
  pinned.value = [];
  ledger.value = [];
  dockImageChoice.value = {};
  lensBrushedIds.value = [];
  graphSelectionIds.value = [];
  resetExplorerFilters();
  try {
    await loadStore(caseId);
    const featured = store.artifacts.filter((artifact) => artifact.featured);
    const pool = featured.length ? featured : store.artifacts;
    const preferred = pool
      .flatMap((artifact) => artifact.slots.map((slot) => ({ artifact, slot })))
      .find(({ slot }) => slot.reviewReadiness === 'P1_expert_review')
      || pool
        .flatMap((artifact) => artifact.slots.map((slot) => ({ artifact, slot })))
      .find(({ slot }) => ['A', 'B', 'C'].includes(slot.cls) && isOpenConflict(slot));
    const first = preferred?.artifact || pool[0] || store.artifacts[0];
    selectedArtifactId.value = first?.id || null;
    selectedSlotId.value = preferred?.slot.id || first?.slots[0]?.id || null;
  } finally {
    loadingCase.value = false;
  }
}

onMounted(() => loadCaseIntoView('guqin'));

const SOURCES = computed(() => store.sources);

// Case 3 · News: slots carrying a DeepSeek semantic verdict (toggle hint).
const llmVerdictCount = computed(() => store.artifacts.reduce(
  (total, artifact) => total + artifact.slots.filter((slot) => slot.llmVerdict).length,
  0,
));

// Case 3 · News: slots carrying a DORIS framing verdict (toggle hint).
const framingVerdictCount = computed(() => store.artifacts.reduce(
  (total, artifact) => total + artifact.slots.filter((slot) => slot.framingVerdict).length,
  0,
));
const CLS_ORDER = { A: 0, C: 1, B: 2 };
const READINESS_LABELS = {
  P1_expert_review: 'Ready for expert review',
  P2_data_cleanup: 'Data cleanup required',
  P3_alignment_review: 'Alignment review required',
  Exclude_lexical_variant: 'Lexical variant',
};

const searchText = ref('');
const sourceFilter = ref('all');
const slotFilter = ref('all');
const readinessFilter = ref('all');

const availableSlots = computed(() => {
  const map = new Map();
  store.artifacts.forEach((artifact) => {
    artifact.slots.forEach((slot) => map.set(slot.id, slot.label));
  });
  return [...map.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'));
});

const visibleArtifacts = computed(() => {
  const query = searchText.value.trim().toLowerCase();
  return store.artifacts.filter((artifact) => {
    if (sourceFilter.value !== 'all' && !artifact.sources.includes(sourceFilter.value)) return false;
    if (
      readinessFilter.value !== 'all'
      && !artifact.slots.some((slot) => slot.reviewReadiness === readinessFilter.value)
    ) return false;
    if (slotFilter.value !== 'all' && !artifact.slots.some((slot) => slot.id === slotFilter.value)) return false;
    if (!query) return true;
    const text = [
      artifact.name,
      ...artifact.records,
      ...artifact.slots.flatMap((slot) => [
        slot.label,
        ...slot.assertions.flatMap((assertion) => assertion.values),
      ]),
    ].join(' ').toLowerCase();
    return text.includes(query);
  });
});

const visibleArtifactIds = computed(() => visibleArtifacts.value.map((artifact) => artifact.id));

const graphArtifactIds = computed(() => {
  if (!lensBrushedIds.value.length) return visibleArtifactIds.value;
  const allowed = new Set(visibleArtifactIds.value);
  const brushed = lensBrushedIds.value.filter((id) => allowed.has(id));
  return brushed.length ? brushed : visibleArtifactIds.value;
});

function onLensBrush(ids) {
  lensBrushedIds.value = ids;
  if (!ids.length) return;
  if (!ids.includes(selectedArtifactId.value)) {
    selectedArtifactId.value = ids[0];
    const artifact = store.artifacts.find((item) => item.id === ids[0]);
    selectedSlotId.value = artifact?.slots.find((slot) => isOpenConflict(slot))?.id
      || artifact?.slots[0]?.id
      || null;
  }
}

function resetExplorerFilters() {
  searchText.value = '';
  sourceFilter.value = 'all';
  slotFilter.value = 'all';
  readinessFilter.value = 'all';
}

const worklist = computed(() =>
  visibleArtifacts.value
    .flatMap((a) => a.slots.map((s) => ({ artifact: a, slot: s })))
    .filter((x) => isOpenConflict(x.slot))
    .filter(
      (x) => readinessFilter.value === 'all'
        || x.slot.reviewReadiness === readinessFilter.value,
    )
    .filter((x) => slotFilter.value === 'all' || x.slot.id === slotFilter.value)
    .filter(
      (x) => sourceFilter.value === 'all'
        || x.slot.assertions.some((assertion) => assertion.source === sourceFilter.value && assertion.raw),
    )
    .sort((x, y) => CLS_ORDER[x.slot.cls] - CLS_ORDER[y.slot.cls]),
);

const attrFacets = computed(() => {
  const map = new Map();
  worklist.value.forEach((w) => {
    const e = map.get(w.slot.label) || { label: w.slot.label, count: 0 };
    e.count += 1;
    map.set(w.slot.label, e);
  });
  return [...map.values()].sort((a, b) => b.count - a.count);
});

const entityGroups = computed(() => {
  const specs = store.caseId === 'news'
    ? [
      {
        name: 'Relations',
        slotPrefix: 'rel-',
        accept: (value) => !!value,
        normalize: (value) => value,
        limit: 24,
      },
      {
        name: 'Statements',
        slotPrefix: 'attr-',
        accept: (value) => !!value,
        normalize: (value) => value,
        limit: 16,
      },
    ]
    : store.caseId === 'astro'
    ? [
      {
        name: '光谱型',
        slots: ['spectral_type'],
        accept: (value) => !!value,
        normalize: (value) => value,
        limit: 24,
      },
      {
        name: '距离',
        slots: ['distance'],
        accept: (value) => !!value,
        normalize: (value) => value,
        limit: 16,
      },
    ]
    : [
      {
        name: '形制',
        slots: ['form'],
        accept: (value) => /式(?:变体)?$/.test(value),
        normalize: (value) => value,
      },
      {
        name: '年代',
        slots: ['dating', 'dating_assessment'],
        accept: (value) => /唐|宋|元|明|清|民国|五代|年/.test(value),
        normalize: (value) => value
          .replace(/\([^)]*\)/g, '')
          .replace(/^(唐|宋|元|明|清)$/, '$1代'),
      },
      {
        name: '馆藏',
        slots: ['current_holder'],
        accept: (value) => /博物馆|博物院|艺术研究院/.test(value),
        normalize: (value) => value,
      },
    ];
  return specs.map((spec) => {
    const values = new Map();
    visibleArtifacts.value.forEach((artifact) => {
      artifact.slots
        .filter((slot) => (spec.slotPrefix ? slot.id.startsWith(spec.slotPrefix) : spec.slots.includes(slot.id)))
        .flatMap((slot) => slot.assertions.flatMap((assertion) => assertion.normalizedValues))
        .forEach((value) => {
          if (!value || !spec.accept(value)) return;
          const normalized = spec.normalize(value);
          const artifacts = values.get(normalized) || [];
          if (!artifacts.some((item) => item.id === artifact.id)) artifacts.push(artifact);
          values.set(normalized, artifacts);
        });
    });
    const items = [...values.entries()]
      .map(([name, artifacts]) => ({ name, artifacts }))
      .sort((a, b) => b.artifacts.length - a.artifacts.length);
    return {
      name: spec.name,
      items: spec.limit ? items.slice(0, spec.limit) : items,
      hiddenCount: spec.limit ? Math.max(0, items.length - spec.limit) : 0,
    };
  });
});

const checkedAttrs = ref([]);
const checkedEntities = ref([]);

function toggleIn(listRef, value) {
  const i = listRef.value.indexOf(value);
  if (i >= 0) listRef.value.splice(i, 1);
  else listRef.value.push(value);
}

const filteredWorklist = computed(() =>
  worklist.value.filter(
    (w) => !checkedAttrs.value.length || checkedAttrs.value.includes(w.slot.label),
  ),
);

const splitMode = ref(false);
const leftW = ref(300);
const rightW = ref(270);
const paneRatio = ref(0.5);
const dockH = ref(190);
const workbenchEl = ref(null);
const activeStage = ref('overview');

let dragKind = null;
let dragStart = 0;
let dragStartVal = 0;

function startDrag(kind, e) {
  dragKind = kind;
  dragStart = kind === 'dock' ? e.clientY : e.clientX;
  dragStartVal =
    kind === 'left' ? leftW.value
    : kind === 'right' ? rightW.value
    : kind === 'pane' ? paneRatio.value
    : dockH.value;
  window.addEventListener('mousemove', onDragMove);
  window.addEventListener('mouseup', endDrag);
  e.preventDefault();
}

function onDragMove(e) {
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  if (dragKind === 'left') {
    leftW.value = clamp(dragStartVal + e.clientX - dragStart, 220, 460);
  } else if (dragKind === 'right') {
    rightW.value = clamp(dragStartVal - (e.clientX - dragStart), 220, 440);
  } else if (dragKind === 'pane') {
    const w = workbenchEl.value?.getBoundingClientRect().width || 1000;
    paneRatio.value = clamp(dragStartVal + (e.clientX - dragStart) / w, 0.25, 0.75);
  } else if (dragKind === 'dock') {
    dockH.value = clamp(dragStartVal - (e.clientY - dragStart), 120, 380);
  }
}

function endDrag() {
  dragKind = null;
  window.removeEventListener('mousemove', onDragMove);
  window.removeEventListener('mouseup', endDrag);
}

const corpusStats = computed(() => ({
  sources: store.stats.records ? Object.keys(store.sources).length : 0,
  sourceLabel: store.caseId === 'astro' ? 'catalogs' : store.caseId === 'news' ? 'sources' : 'books',
  artifacts: store.stats.artifacts || 0,
  contested: (store.stats.differ || 0) + (store.stats.overlap || 0),
  unresolved: worklist.value.length,
}));

function sourceFolderLabel(source) {
  return store.caseId === 'guqin' ? `《${source.title}》` : source.title;
}

const largeCorpus = computed(() => store.artifacts.length > 200);

const sourceHierarchy = computed(() =>
  Object.values(store.sources).map((source) => {
    const artifacts = visibleArtifacts.value
      .filter((artifact) => artifact.sources.includes(source.id))
      .sort((a, b) => rankArtifact(b) - rankArtifact(a) || a.name.localeCompare(b.name, 'zh-CN'));
    const cap = largeCorpus.value ? TREE_LIST_CAP : artifacts.length;
    return {
      ...source,
      artifacts: artifacts.slice(0, cap),
      hiddenCount: Math.max(0, artifacts.length - cap),
    };
  }),
);

const artifactHierarchy = computed(() => entityGroups.value);

const conflictHierarchy = computed(() =>
  [
    { cls: 'A', name: '记载不一致', desc: '各来源取值不同,待考订' },
    { cls: 'B', name: '部分重叠', desc: '取值有交集,疑详略差异' },
    { cls: 'C', name: '疑似讹误', desc: '规则触发(待接入)' },
  ].map((group) => {
    const slots = worklist.value
      .filter((w) => w.slot.cls === group.cls)
      .sort((x, y) => rankArtifact(y.artifact) - rankArtifact(x.artifact)
        || x.artifact.name.localeCompare(y.artifact.name, 'zh-CN'));
    return {
      ...group,
      total: slots.length,
      slots: largeCorpus.value ? slots.slice(0, TREE_LIST_CAP) : slots,
    };
  }),
);

const listedWorklist = computed(() => {
  if (!largeCorpus.value || worklist.value.length <= TREE_LIST_CAP) return worklist.value;
  return [...worklist.value]
    .sort((x, y) => rankArtifact(y.artifact) - rankArtifact(x.artifact)
      || x.artifact.name.localeCompare(y.artifact.name, 'zh-CN'))
    .slice(0, TREE_LIST_CAP);
});

function onSelectArtifact(id) {
  graphSelectionIds.value = []; // plain click = single-select focus, drop multi-selection
  selectedArtifactId.value = id;
  // News workspace: artifact ids are event ids, so keep the framing view's
  // event in sync with Global KG / lens selections (bidirectional linkage).
  if (store.caseId === 'news' && newsV6Store.comparisons.events.some((event) => event.event_id === id)) {
    selectNewsEvent(id);
  }
  const artifact = store.artifacts.find((x) => x.id === id);
  selectedSlotId.value = artifact?.slots[0]?.id || null;
  activeStage.value = 'focus';
}

function onSelectSlot(id) {
  selectedSlotId.value = id;
  activeStage.value = 'evidence';
}

function selectCandidate(artifactId, slotId) {
  selectedArtifactId.value = artifactId;
  selectedSlotId.value = slotId;
  activeStage.value = 'evidence';
}

// ---------------------------------------------------------------------------
// Case 3 · News manual graph corrections (merge/split/undo)
// ---------------------------------------------------------------------------
function onToggleGraphSelection(artifactId) {
  const index = graphSelectionIds.value.indexOf(artifactId);
  if (index >= 0) graphSelectionIds.value.splice(index, 1);
  else graphSelectionIds.value.push(artifactId);
}

const canMergeSelection = computed(() => graphSelectionIds.value.length >= 2);
const canSplitSelection = computed(() => graphSelectionIds.value.length === 1);
const canUndoGraphOp = computed(() => newsV6Store.undoStack.length > 0);

function openMergeDialogFromGraph() {
  if (!canMergeSelection.value) return;
  newsV6Store.selectionIds = [...graphSelectionIds.value];
  openNewsMergeDialog();
}

function openSplitDialogFromGraph() {
  if (!canSplitSelection.value) return;
  newsV6Store.selectionIds = [...graphSelectionIds.value];
  openNewsSplitDialog();
}

function undoGraphOp() {
  undoLastAction(); // newsV6Store.graph reference change drives the refresh watcher below
}

function clearGraphSelection() {
  graphSelectionIds.value = [];
}

// News toolbar "Framing": jump to the DORIS framing view for the currently
// selected event (news artifact ids are event ids in the workspace store).
function openFramingView() {
  const current = selectedArtifactId.value;
  if (current && newsV6Store.comparisons.events.some((event) => event.event_id === current)) {
    selectNewsEvent(current);
  }
  activateTab(globalTabs, 'framing');
}

// Any merge/split/undo rewrites newsV6Store.graph; mirror the corrected event
// set into the generic workspace and drop selections/pins that no longer exist.
watch(
  () => newsV6Store.graph,
  () => {
    if (store.caseId !== 'news' || !newsV6Store.loaded) return;
    refreshArtifactsFromNewsV6();
    const alive = new Set(store.artifacts.map((artifact) => artifact.id));
    graphSelectionIds.value = graphSelectionIds.value.filter((id) => alive.has(id));
    pinned.value = pinned.value.filter((pin) => alive.has(pin.artifactId));
  },
);

const globalTabs = ref([
  { id: 'global', title: 'Global KG', active: true },
  { id: 'lens', title: 'Lens View', active: false },
  { id: 'framing', title: 'Framing View', active: false }, // news case only (filtered by centerTabs)
]);

const derivedTabs = ref([
  { id: 'compare', title: 'Attribute Matrix', active: true },
  { id: 'edit', title: 'Editing', active: false },
  { id: 'ledger', title: 'Ledger', active: false },
]);

// The DORIS framing tab exists only in the news workspace; other cases never see it.
const centerTabs = computed(() => (
  store.caseId === 'news'
    ? globalTabs.value
    : globalTabs.value.filter((tab) => tab.id !== 'framing')
));

const dockSlot = computed(() => {
  const a = store.artifacts.find((x) => x.id === selectedArtifactId.value);
  const s = a?.slots.find((x) => x.id === selectedSlotId.value);
  if (!a || !s) return null;
  return { artifact: a, slot: s };
});

const PART_PREFS = {
  duanwen: ['qimian', 'qindi', 'qincemian'],
  lacquer: ['qimian', 'qindi'],
  ash: ['qindi', 'qimian'],
  inscriber: ['mingwen'],
  dating: ['mingwen', 'qintou'],
  form: ['qintou', 'qimian', 'qinwei'],
  material: ['qindi', 'qimian'],
  collection: ['mingwen'],
  craft: ['qimian', 'qintou'],
};

const dockImageChoice = ref({});

watch([selectedArtifactId, selectedSlotId], () => {
  dockImageChoice.value = {};
});

function dockImageKey(assertion) {
  return `${assertion.source}:${assertion.recordId || assertion.source}`;
}

function dockImageLabel(file) {
  const base = String(file || '').replace(/\.[^.]+$/, '');
  return base.replace(/^(nasa|simbad|gaia)_/i, '') || base || 'image';
}

function astroImageFolder(assertion) {
  const path = assertion.imagePath || '';
  const match = path.match(/^\/api\/astro\/image\/([^/]+)\//);
  if (match) return decodeURIComponent(match[1]);
  return (dockSlot.value?.artifact.name || '')
    .replace(/\s+/g, '_')
    .replace(/'/g, '_');
}

function dockImageList(assertion) {
  if (!assertion) return [];
  const files = assertion.metadata?.image_files
    || assertion.metadata?.imageFiles
    || dockSlot.value?.artifact.imagesByBook?.[assertion.source]
    || [];
  const paths = assertion.metadata?.image_paths || assertion.metadata?.imagePaths || [];
  if (paths.length) {
    return paths.map((src, index) => {
      const file = files[index] || String(src).split('/').pop();
      return { src, file, label: dockImageLabel(file) };
    });
  }
  if (!files.length) {
    if (!assertion.imagePath) return [];
    const file = String(assertion.imagePath).split('/').pop();
    return [{ src: assertion.imagePath, file, label: dockImageLabel(file) }];
  }
  if (store.caseId === 'astro') {
    const host = astroImageFolder(assertion);
    return files.map((file) => ({
      src: `/api/astro/image/${host}/${file}`,
      file,
      label: dockImageLabel(file),
    }));
  }
  if (!assertion.recordId) {
    if (!assertion.imagePath) return [];
    const file = String(assertion.imagePath).split('/').pop();
    return [{ src: assertion.imagePath, file, label: dockImageLabel(file) }];
  }
  return files.map((file) => ({
    src: `/api/image/${assertion.recordId}/${file}`,
    file,
    label: dockImageLabel(file),
  }));
}

function defaultDockImageSrc(assertion) {
  const list = dockImageList(assertion);
  if (!list.length) return null;
  if (assertion.imagePath) {
    const preferred = list.find((item) => (
      item.src === assertion.imagePath || assertion.imagePath.endsWith(`/${item.file}`)
    ));
    if (preferred) return preferred.src;
  }
  if (store.caseId === 'astro') {
    for (const token of ['dss2', '2mass', 'ps1']) {
      const hit = list.find((item) => item.file.toLowerCase().includes(token));
      if (hit) return hit.src;
    }
    return list[0].src;
  }
  const prefs = PART_PREFS[dockSlot.value?.slot.id] || [];
  for (const pref of prefs) {
    const hit = list.find((item) => item.file.toLowerCase().startsWith(pref));
    if (hit) return hit.src;
  }
  return list[0].src;
}

function dockImage(assertion) {
  const chosen = dockImageChoice.value[dockImageKey(assertion)];
  if (chosen) return chosen;
  return defaultDockImageSrc(assertion);
}

function dockImageCurrentLabel(assertion) {
  const src = dockImage(assertion);
  const item = dockImageList(assertion).find((entry) => entry.src === src);
  return item?.label || '';
}

function selectDockImage(assertion, src) {
  dockImageChoice.value = {
    ...dockImageChoice.value,
    [dockImageKey(assertion)]: src,
  };
}

function imageEvidenceLabel(assertion) {
  const stored = assertion.metadata?.image_match || assertion.metadata?.imageMatch;
  if (stored === 'attribute-linked') return 'attribute-linked image';
  if (stored === 'record-level-fallback') return 'record-level fallback image';
  if (stored === 'missing') return 'no image evidence';
  const files = assertion.metadata?.image_files || assertion.metadata?.imageFiles || [];
  if (!files.length) return 'no image evidence';
  const prefs = PART_PREFS[dockSlot.value?.slot.id] || [];
  const matched = files.some((file) => prefs.some((pref) => file.toLowerCase().startsWith(pref)));
  return matched ? 'attribute-linked image' : 'record-level fallback image';
}


const uploadedName = ref('');
const viewerImage = ref(null);
const viewerText = ref(null);

function openEvidenceImage(src, title) {
  if (!src) return;
  viewerImage.value = { src, title };
}

function closeEvidenceImage() {
  viewerImage.value = null;
}

function evidenceTextUrl(assertion) {
  return assertion.metadata?.text_url
    || assertion.metadata?.textUrl
    || (store.caseId === 'guqin' && assertion.recordId ? `/api/text/${assertion.recordId}` : null);
}

async function openEvidenceText(assertion) {
  const url = evidenceTextUrl(assertion);
  if (!url) return;
  viewerText.value = {
    title: `${SOURCES.value[assertion.source]?.title || assertion.source} · ${assertion.recordId}`,
    text: 'Loading article text…',
  };
  try {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`API ${response.status}`);
    viewerText.value.text = await response.text();
  } catch (error) {
    viewerText.value.text = `Unable to load article text: ${error}`;
  }
}

function closeEvidenceText() {
  viewerText.value = null;
}

async function handleUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  uploadedName.value = file.name;
  const form = new FormData();
  form.append('archive', file);
  try {
    await fetch('/api/ingest', { method: 'POST', body: form });
  } catch {
    /* backend not wired yet */
  }
  event.target.value = '';
}

function tabArr(tabs) {
  return Array.isArray(tabs) ? tabs : tabs.value;
}

function activateTab(tabs, id) {
  tabArr(tabs).forEach((tab) => {
    tab.active = tab.id === id;
  });
}

function closeTab(tabs, id) {
  const arr = tabArr(tabs);
  if (arr.length === 1) return;
  const idx = arr.findIndex((tab) => tab.id === id);
  if (idx < 0) return;
  const wasActive = arr[idx].active;
  arr.splice(idx, 1);
  if (wasActive) arr[0].active = true;
}

let tabSeq = 0;
function addTab(tabs) {
  const arr = tabArr(tabs);
  tabSeq += 1;
  arr.forEach((tab) => {
    tab.active = false;
  });
  arr.push({ id: `custom-${tabSeq}`, title: `View ${tabSeq}`, active: true });
}

function activeTabId(tabs) {
  return tabArr(tabs).find((t) => t.active)?.id;
}

const pinned = ref([]);

function pinArtifact(id) {
  const a = store.artifacts.find((x) => x.id === id);
  if (!a || pinned.value.some((p) => p.key === `a:${id}`)) return;
  pinned.value.push({ key: `a:${id}`, kind: 'artifact', label: a.name, artifactId: id });
}

function pinSlot(artifactId, slotId) {
  const a = store.artifacts.find((x) => x.id === artifactId);
  const s = a?.slots.find((x) => x.id === slotId);
  if (!s || pinned.value.some((p) => p.key === `s:${artifactId}:${slotId}`)) return;
  pinned.value.push({
    key: `s:${artifactId}:${slotId}`,
    kind: 'slot',
    label: `${a.name} · ${s.label}`,
    cls: s.cls,
    artifactId,
    slotId,
  });
}

function unpin(key) {
  pinned.value = pinned.value.filter((p) => p.key !== key);
}

function recallPin(p) {
  selectedArtifactId.value = p.artifactId;
  selectedSlotId.value = p.slotId || null;
  activeStage.value = p.slotId ? 'evidence' : 'focus';
}

const ledger = ref([]);

function onAdjudicated(payload) {
  const a = store.artifacts.find((x) => x.id === selectedArtifactId.value);
  const s = applyDecision(selectedArtifactId.value, payload.slotId, {
    action: payload.action,
    value: payload.value || null,
    sources: payload.sources || [],
    text: payload.text,
    time: new Date().toISOString(),
    artifactId: selectedArtifactId.value,
    slotId: payload.slotId,
  }) || a?.slots.find((x) => x.id === payload.slotId);
  ledger.value.unshift({
    id: `adj-${ledger.value.length + 1}`,
    time: new Date().toLocaleString(),
    artifact: a?.name || '',
    slot: s?.label || '',
    cls: s?.cls,
    action: payload.action,
    value: payload.value || '',
    text: payload.text,
    evidence: pinned.value.map((p) => p.label),
    supersedes:
      ledger.value.find((e) => e.artifact === a?.name && e.slot === s?.label)?.id || null,
  });
  activeStage.value = 'decision';
}
</script>

<template>
  <main class="app-shell">
    <header class="topbar">
      <h1>KGExplorer: Knowledge Graph Visual Analytics System</h1>
      <div class="case-switcher">
        <label for="case-select">Case</label>
        <select
          id="case-select"
          v-model="selectedCaseId"
          :disabled="loadingCase"
          @change="loadCaseIntoView(selectedCaseId)"
        >
          <option v-for="item in CASES" :key="item.id" :value="item.id">{{ item.label }}</option>
        </select>
      </div>
    </header>

    <!-- Case 3 · News uses this same generic workspace. Event-node
         merge/split/undo corrections live in the graph toolbar above
         (Ctrl/Cmd/Shift+click to multi-select); the dedicated NewsCaseView
         has been retired. -->
    <section class="workspace" aria-label="Main workspace">
      <div v-if="loadingCase || store.error" class="workspace-mask">
        <span v-if="loadingCase">Loading {{ CASES.find((item) => item.id === selectedCaseId)?.label || 'case' }}…</span>
        <span v-else>Failed to load case: {{ store.error }}</span>
      </div>
      <aside class="panel left-panel" aria-label="Workspace panel">
        <header class="panel-title">KG Workspace</header>

        <div class="left-content">
          <section class="control-card explorer-card" aria-label="Corpus explorer">
            <div class="explorer-head">
              <h2>Corpus Explorer</h2>
              <button type="button" title="Reset filters" @click="resetExplorerFilters">Reset</button>
            </div>
            <input
              v-model="searchText"
              class="explorer-search"
              type="search"
              placeholder="Search object, record, or value"
            />
            <div class="explorer-filters">
              <select v-model="sourceFilter" aria-label="Filter by source">
                <option value="all">All sources</option>
                <option v-for="source in Object.values(SOURCES)" :key="source.id" :value="source.id">
                  {{ source.title }}
                </option>
              </select>
              <select v-model="slotFilter" aria-label="Filter by attribute">
                <option value="all">All attributes</option>
                <option v-for="slot in availableSlots" :key="slot.id" :value="slot.id">
                  {{ slot.label }}
                </option>
              </select>
              <select
                v-if="store.caseId === 'guqin'"
                v-model="readinessFilter"
                aria-label="Filter by audit readiness"
              >
                <option value="all">All audit states</option>
                <option v-for="(label, id) in READINESS_LABELS" :key="id" :value="id">
                  {{ label }}
                </option>
              </select>
            </div>
            <div class="explorer-result">
              <strong>{{ visibleArtifacts.length }}</strong> / {{ store.artifacts.length }} objects
              <span>· {{ worklist.length }} conflict candidates</span>
              <span v-if="listedWorklist.length < worklist.length">
                · listing {{ listedWorklist.length }}
              </span>
            </div>
          </section>

          <section class="control-card stats-card" aria-label="Corpus statistics">
            <h2>Corpus</h2>
            <p v-if="store.caseLabel" class="case-label">{{ store.caseLabel }}</p>
            <p v-if="store.title" class="case-caption">{{ store.title }}</p>
            <div class="stat-grid">
              <div><b>{{ corpusStats.sources }}</b><span>{{ corpusStats.sourceLabel }}</span></div>
              <div><b>{{ corpusStats.artifacts }}</b><span>artifacts</span></div>
              <div><b>{{ corpusStats.contested }}</b><span>contested</span></div>
              <div><b>{{ corpusStats.unresolved }}</b><span>open</span></div>
            </div>
            <div
              v-if="store.caseId === 'guqin' && store.evidenceAudit.ready_for_expert_review !== undefined"
              class="audit-strip"
            >
              <span><b>{{ store.evidenceAudit.ready_for_expert_review }}</b> expert-ready</span>
              <span><b>{{ store.evidenceAudit.requires_data_cleanup }}</b> cleanup</span>
              <span><b>{{ store.qualityIssues.length }}</b> OCR checks</span>
            </div>
          </section>

          <section class="control-card hierarchy-card" aria-label="Knowledge hierarchies">
            <h2>Knowledge Hierarchies</h2>
            <div class="hierarchy-columns">
              <section>
                <h3>Sources</h3>
                <ul class="folder-tree">
                  <li v-for="source in sourceHierarchy" :key="source.id" class="open">
                    <span class="folder-name" :style="{ color: source.color }">{{ sourceFolderLabel(source) }}</span>
                    <ul>
                      <li
                        v-for="artifact in source.artifacts"
                        :key="source.id + artifact.id"
                        :class="{ 'tree-active': artifact.id === selectedArtifactId }"
                      >
                        <span class="tree-artifact" @click="onSelectArtifact(artifact.id)">{{ artifact.name }}</span>
                      </li>
                      <li v-if="source.hiddenCount" class="tree-more">
                        +{{ source.hiddenCount }} more · search to browse
                      </li>
                    </ul>
                  </li>
                </ul>
              </section>
              <section>
                <h3>Entities</h3>
                <ul class="folder-tree">
                  <li v-for="group in artifactHierarchy" :key="group.name" class="open">
                    <span class="folder-name">{{ group.name }}</span>
                    <ul>
                      <li v-for="item in group.items" :key="item.name" class="open">
                        <span>{{ item.name }}<i class="tree-badge">{{ item.artifacts.length }}</i></span>
                      </li>
                      <li v-if="group.hiddenCount" class="tree-more">
                        +{{ group.hiddenCount }} more values
                      </li>
                    </ul>
                  </li>
                </ul>
              </section>
              <section>
                <h3>Issue Types</h3>
                <ul class="folder-tree conflict-tree">
                  <li v-for="group in conflictHierarchy" :key="group.cls" class="open">
                    <span>
                      <i class="chip-dot" :data-cls="group.cls"></i>
                      {{ group.cls }} · {{ group.name }}
                      <i class="tree-badge">{{ group.total }}</i>
                    </span>
                    <ul>
                      <li
                        v-for="w in group.slots"
                        :key="group.cls + w.artifact.id + w.slot.id"
                        :class="{ 'tree-active': w.artifact.id === selectedArtifactId && w.slot.id === selectedSlotId }"
                      >
                        <span
                          class="tree-artifact"
                          @click="selectCandidate(w.artifact.id, w.slot.id)"
                        >{{ w.artifact.name }} · {{ w.slot.label }}</span>
                      </li>
                      <li v-if="group.total > group.slots.length" class="tree-more">
                        +{{ group.total - group.slots.length }} more · search to browse
                      </li>
                    </ul>
                  </li>
                </ul>
              </section>
            </div>
          </section>

          <section class="control-card worklist-card" aria-label="Conflict worklist">
            <h2>Candidate Queue</h2>
            <p v-if="listedWorklist.length < worklist.length" class="explorer-result">
              Showing featured / selected first
            </p>
            <ul class="worklist">
              <li
                v-for="w in listedWorklist"
                :key="w.artifact.id + w.slot.id"
                :class="{ active: w.artifact.id === selectedArtifactId && w.slot.id === selectedSlotId }"
                @click="selectCandidate(w.artifact.id, w.slot.id)"
              >
                <i class="chip-dot" :data-cls="w.slot.cls"></i>
                <span class="wl-name">{{ w.artifact.name }} · {{ w.slot.label }}</span>
                <em
                  v-if="w.slot.decision?.action === 'keep_open'"
                  class="readiness-tag"
                >{{ DECISION_LABELS.keep_open }}</em>
                <em
                  v-else-if="w.slot.reviewReadiness"
                  class="readiness-tag"
                  :data-state="w.slot.reviewReadiness"
                >
                  {{ w.slot.reviewReadiness.replace('P1_', '').replace('P2_', '').replace('P3_', '').replace('Exclude_', '') }}
                </em>
                <em v-else>candidate</em>
              </li>
            </ul>
          </section>
        </div>
      </aside>

      <section class="panel center-panel" aria-label="Main visualization view">
        <header class="panel-toolbar">
          <h2>Global KG Explorer</h2>
          <div class="graph-legend" aria-label="Graph legend">
            <span title="One individual artifact record from the corpus"><i class="legend-object"></i>artifact record</span>
            <span title="Normalized attribute value shared by at least two artifact records"><i style="background:#7c9aae"></i>attribute value</span>
            <span title="Outer ring sectors encode which books contain this record; books are not graph nodes"><i style="background:#b29a62"></i>source coverage ring</span>
            <span v-for="source in Object.values(SOURCES)" :key="source.id" :title="source.title"><i :style="{ background: source.color }"></i>{{ source.id }}</span>
            <span title="Aligned across multiple sources"><i style="background:#5d8c82"></i>confirmed</span>
            <span title="Potential cross-source match"><i style="background:#b29a62"></i>candidate</span>
            <span title="Present in one source only"><i style="background:#aab4ba"></i>single-source</span>
            <span title="Unresolved or conflicting evidence"><i style="background:#b86f3d"></i>unresolved</span>
            <span><i :style="{ background: CLASS_COLORS.A }"></i>A</span>
            <span><i :style="{ background: CLASS_COLORS.C }"></i>C</span>
            <span><i :style="{ background: CLASS_COLORS.B }"></i>B</span>
            <span><i :style="{ background: CLASS_COLORS.consensus }"></i>OK</span>
          </div>
          <div v-if="store.caseId === 'news'" class="judge-mode-switch" role="group" aria-label="判定方法">
            <span class="judge-mode-label">判定方法</span>
            <button
              type="button"
              class="judge-mode-btn"
              :class="{ active: store.judgeMode === 'rule' }"
              @click="store.judgeMode = 'rule'"
            >规则</button>
            <button
              type="button"
              class="judge-mode-btn"
              :class="{ active: store.judgeMode === 'llm' }"
              :title="llmVerdictCount ? `${llmVerdictCount} 个槽位有大模型判定` : '暂无大模型判定结果，颜色与规则一致'"
              @click="store.judgeMode = 'llm'"
            >大模型</button>
            <button
              type="button"
              class="judge-mode-btn"
              :class="{ active: store.judgeMode === 'framing' }"
              :title="framingVerdictCount ? `${framingVerdictCount} 个槽位有框架冲突判定` : '暂无框架判定结果，颜色与规则一致'"
              @click="store.judgeMode = 'framing'"
            >框架冲突</button>
          </div>
          <div class="toolbar-actions">
            <template v-if="store.caseId === 'news'">
              <span class="graph-ops-count" :data-active="graphSelectionIds.length > 0">
                {{ graphSelectionIds.length }} selected
              </span>
              <button
                type="button"
                class="graph-op"
                :disabled="!canMergeSelection"
                title="Merge the selected event nodes into one (Ctrl+Click to select ≥ 2)"
                @click="openMergeDialogFromGraph"
              >Merge</button>
              <button
                type="button"
                class="graph-op"
                :disabled="!canSplitSelection"
                title="Split the selected event node into two (select exactly 1)"
                @click="openSplitDialogFromGraph"
              >Split</button>
              <button
                type="button"
                class="graph-op"
                :disabled="!canUndoGraphOp"
                title="Undo the last merge/split correction"
                @click="undoGraphOp"
              >Undo</button>
              <button
                v-if="graphSelectionIds.length"
                type="button"
                class="graph-op"
                title="Clear the multi-selection"
                @click="clearGraphSelection"
              >Clear</button>
              <button
                type="button"
                class="graph-op"
                title="Open the DORIS framing view for the selected event"
                @click="openFramingView"
              >Framing</button>
            </template>
            <button
              v-if="lensBrushedIds.length"
              class="save-button"
              type="button"
              @click="lensBrushedIds = []"
            >Clear lens ({{ lensBrushedIds.length }})</button>
            <button class="save-button" type="button">Save</button>
          </div>
        </header>
        <div class="graph-workbench">
          <section class="graph-pane" aria-label="Global knowledge graph">
            <div class="browser-tabs">
              <button
                v-for="tab in centerTabs"
                :key="tab.id"
                :class="['browser-tab', { active: tab.active }]"
                type="button"
                @click="activateTab(globalTabs, tab.id)"
              >
                <span>{{ tab.title }}</span>
                <i @click.stop="closeTab(globalTabs, tab.id)">×</i>
              </button>
              <button class="new-tab" type="button" @click="addTab(globalTabs)">＋</button>
            </div>
            <KnowledgeGraphView
              v-if="activeTabId(globalTabs) === 'global'"
              variant="global"
              :selected-artifact-id="selectedArtifactId"
              :selected-slot-id="selectedSlotId"
              :visible-artifact-ids="graphArtifactIds"
              :selected-artifact-ids="graphSelectionIds"
              @select-artifact="onSelectArtifact"
              @select-slot="onSelectSlot"
              @pin-artifact="pinArtifact"
              @toggle-select="onToggleGraphSelection"
            />
            <span
              v-show="store.caseId === 'news' && newsV6Store.lastAction"
              class="graph-op-toast"
              role="status"
            >{{ newsV6Store.lastAction }}</span>
            <div v-if="activeTabId(globalTabs) === 'lens'" class="lens-pane">
              <div class="lens-toolbar">
                <span>Cluster by</span>
                <button
                  type="button"
                  :class="{ active: lensMode === 'conflict' }"
                  @click="lensMode = 'conflict'"
                >Conflict pattern</button>
                <button
                  type="button"
                  :class="{ active: lensMode === 'attribute' }"
                  @click="lensMode = 'attribute'"
                >Catalogue attributes</button>
                <em v-if="store.projection?.ready">
                  {{ store.projection.point_count || store.artifacts.length }} objects
                </em>
              </div>
              <ProjectionView
                :mode="lensMode"
                :selected-artifact-id="selectedArtifactId"
                :visible-artifact-ids="visibleArtifactIds"
                :brushed-artifact-ids="lensBrushedIds"
                @select-artifact="onSelectArtifact"
                @brush-artifacts="onLensBrush"
              />
            </div>
            <NewsFramingView
              v-else-if="store.caseId === 'news' && activeTabId(globalTabs) === 'framing'"
            />
            <div v-else class="tab-placeholder">
              Empty view
            </div>
          </section>

          <section class="graph-pane" aria-label="Interaction-derived knowledge graph">
            <div class="browser-tabs">
              <button
                v-for="tab in derivedTabs"
                :key="tab.id"
                :class="['browser-tab', { active: tab.active }]"
                type="button"
                @click="activateTab(derivedTabs, tab.id)"
              >
                <span>{{ tab.title }}</span>
                <i @click.stop="closeTab(derivedTabs, tab.id)">×</i>
              </button>
              <button class="new-tab" type="button" @click="addTab(derivedTabs)">＋</button>
            </div>
            <KnowledgeGraphView
              v-if="activeTabId(derivedTabs) === 'compare'"
              variant="derived"
              :selected-artifact-id="selectedArtifactId"
              :selected-slot-id="selectedSlotId"
              :visible-artifact-ids="visibleArtifactIds"
              @select-slot="onSelectSlot"
            />
            <div v-else-if="activeTabId(derivedTabs) === 'ledger'" class="ledger-view">
              <p v-if="!ledger.length" class="ledger-empty">No adjudications yet. Decisions made in the right panel are recorded here, append-only.</p>
              <article v-for="e in ledger" :key="e.id" class="ledger-entry">
                <header>
                  <i class="chip-dot" :data-cls="e.cls"></i>
                  <strong>{{ e.artifact }} · {{ e.slot }}</strong>
                  <span class="ledger-action">{{ DECISION_LABELS[e.action] || e.action }}</span>
                  <span class="ledger-time">{{ e.time }}</span>
                </header>
                <p class="ledger-text">{{ e.text }}</p>
                <p v-if="e.value" class="ledger-evidence">Selected value: {{ e.value }}</p>
                <p v-if="e.evidence.length" class="ledger-evidence">Evidence cited: {{ e.evidence.join(' / ') }}</p>
                <p v-if="e.supersedes" class="ledger-supersedes">supersedes {{ e.supersedes }}</p>
              </article>
            </div>
            <div v-else class="tab-placeholder">
              {{ activeTabId(derivedTabs) === 'edit' ? 'Contested assertion workspace (V3) — planned' : 'Empty view' }}
            </div>
          </section>
        </div>

        <div class="evidence-dock" aria-label="Evidence tray" :style="{ height: dockH + 'px' }">
          <div class="dock-resizer" title="Resize evidence tray" @mousedown="startDrag('dock', $event)"></div>
          <div class="dock-head">
            <span class="tray-title">Evidence Tray</span>
            <span v-if="dockSlot" class="dock-context">{{ dockSlot.artifact.name }} · {{ dockSlot.slot.label }}</span>
            <div class="dock-pins">
              <div v-for="p in pinned" :key="p.key" class="tray-chip" @click="recallPin(p)">
                <i v-if="p.cls" class="chip-dot" :data-cls="p.cls"></i>
                {{ p.label }}
                <b @click.stop="unpin(p.key)">×</b>
              </div>
            </div>
            <button v-if="pinned.length" class="tray-clear" type="button" @click="pinned = []">Clear</button>
          </div>
          <div v-if="dockSlot" class="dock-body">
            <article
              v-for="a in dockSlot.slot.assertions"
              :key="`${a.source}:${a.recordId}`"
              class="dock-card"
              :class="{ silent: !a.raw }"
            >
              <header :style="{ color: SOURCES[a.source]?.color }">
                {{ SOURCES[a.source]?.title }}
                <small v-if="a.metadata?.outlet"> · {{ a.metadata.outlet }}</small>
              </header>
              <img
                v-if="dockImage(a)"
                class="dock-photo"
                :src="dockImage(a)"
                :alt="`${SOURCES[a.source]?.title} · ${dockImageCurrentLabel(a) || dockSlot.slot.label}`"
                loading="lazy"
                title="Open full-size evidence image"
                @click="openEvidenceImage(dockImage(a), `${SOURCES[a.source]?.title} · ${dockImageCurrentLabel(a) || dockSlot.slot.label}`)"
              />
              <div v-else class="dock-thumb part" :data-kind="a.raw ? 'part' : 'none'">
                <span v-if="a.raw">no image evidence for this record</span>
                <span v-else>not recorded</span>
              </div>
              <div
                v-if="dockImageList(a).length > 1"
                class="dock-thumbs"
                role="listbox"
                :aria-label="`${SOURCES[a.source]?.title} images`"
              >
                <button
                  v-for="item in dockImageList(a)"
                  :key="item.src"
                  type="button"
                  class="dock-thumb-btn"
                  role="option"
                  :class="{ active: dockImage(a) === item.src }"
                  :aria-selected="dockImage(a) === item.src"
                  :title="item.file"
                  @click="selectDockImage(a, item.src)"
                >
                  <img :src="item.src" :alt="item.label" />
                  <span>{{ item.label }}</span>
                </button>
              </div>
              <small v-if="a.raw" class="dock-image-status" :data-kind="imageEvidenceLabel(a)">
                {{ dockImageCurrentLabel(a) || imageEvidenceLabel(a) }}
              </small>
              <div v-if="a.audit" class="dock-audit">
                <span :data-state="a.audit.text_support">{{ a.audit.text_support }}</span>
                <span :data-state="a.audit.image_support">{{ a.audit.image_support }}</span>
              </div>
              <p class="dock-raw">{{ a.raw || (store.caseId === 'guqin' ? '该书未记载' : 'not recorded in this report') }}</p>
              <p
                v-if="store.caseId === 'news' && a.metadata?.evidence_sentences?.length"
                class="dock-snippet"
              >{{ a.metadata.evidence_sentences[0] }}</p>
              <p v-if="a.audit?.text_snippet" class="dock-snippet">{{ a.audit.text_snippet }}</p>
              <div v-if="a.raw" class="dock-card-actions">
                <button v-if="evidenceTextUrl(a)" type="button" @click="openEvidenceText(a)">
                  {{ store.caseId === 'guqin' ? 'OCR text' : 'Article text' }}
                </button>
                <button type="button" @click="pinSlot(dockSlot.artifact.id, dockSlot.slot.id)">Pin evidence</button>
              </div>
            </article>
          </div>
          <div v-else class="dock-empty">No evidence selected.</div>
        </div>
      </section>

      <aside class="panel right-panel" aria-label="Evidence and adjudication">
        <header class="panel-title">Evidence &amp; Adjudication</header>
        <AdjudicationPanel
          :selected-artifact-id="selectedArtifactId"
          :selected-slot-id="selectedSlotId"
          @select-slot="onSelectSlot"
          @pin-slot="pinSlot"
          @adjudicated="onAdjudicated"
        />
      </aside>
    </section>

    <Teleport to="body">
      <div v-if="viewerImage" class="evidence-lightbox" role="dialog" aria-modal="true" @click.self="closeEvidenceImage">
        <div class="lightbox-panel">
          <header>
            <strong>{{ viewerImage.title }}</strong>
            <button type="button" aria-label="Close image viewer" @click="closeEvidenceImage">×</button>
          </header>
          <div class="lightbox-stage">
            <img :src="viewerImage.src" :alt="viewerImage.title" />
          </div>
          <p>Click outside the image or close the viewer to return to the evidence tray.</p>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="viewerText" class="evidence-lightbox" role="dialog" aria-modal="true" @click.self="closeEvidenceText">
        <div class="lightbox-panel text-viewer">
          <header>
            <strong>{{ viewerText.title }}</strong>
            <button type="button" aria-label="Close OCR viewer" @click="closeEvidenceText">×</button>
          </header>
          <pre>{{ viewerText.text }}</pre>
        </div>
      </div>
    </Teleport>

    <!-- Case 3 · News manual graph-correction dialogs (rendered over the
         generic workspace; state lives in newsV6Store) -->
    <NewsMergeDialog v-if="store.caseId === 'news' && newsV6Store.mergeDialogOpen" />
    <NewsSplitDialog v-if="store.caseId === 'news' && newsV6Store.splitTargetId" />
  </main>
</template>
