<script setup>
import { computed, nextTick, ref, watch } from 'vue';
import { newsV6Store, selectNewsEvent, framingVerdictFor } from '../data/newsV6Store.js';

// ---------------------------------------------------------------------------
// NewsFramingView · 叙事框架冲突视图（DORIS Figure 2 / Table 2 · SVG 流程图版）
//
// 每个共享实体一章 = 一张确定性分层流程图（纯 SVG 手写坐标，无 d3-force）：
//   顶部每报道一个域名色码徽章（hover=完整标题，点击=读原文），每链一条垂直
//   主干线，链上依次挂 触发词菱形 → Frame 胶囊 → FE 圆点（证据句全文收进
//   tooltip，不再占独立行），底部经正交折线（主干 → 汇流轨 → 中心下垂 + 箭头）
//   汇聚到实体行：实体名 + 结论徽章 + 逐报道对比点阵。
// 派系可读性增强：
//   - 列按链的主导 Frame 聚类排序：同派链在汇流前即相邻；
//   - 主干线与 Frame 胶囊按 Frame 派系着色（FRAME_PALETTE 哈希，同色=同派），
//     汇流轨保持章节中性色（多色在此合并）；无 Frame 链退回中性色；
//   - hover 任一 Frame 胶囊 → 全视图同派点亮、异派变淡（跨章节）；
//   - 规则章节默认折叠成单行条（点击展开/收起），冲突章节保持展开；
//   - 顶部 mini-map：每章节一个 tone 色点，点击平滑滚动直达；
//   - 入场绘制动画（pathLength 归一化 + stroke-dashoffset），尊重
//     prefers-reduced-motion。
// 文字分级（把"报告感"收敛成"图"）：
//   - 长文本全部收进悬浮 tooltip：证据句全文 / 新闻标题 / DORIS 判定理由 /
//     (Frame·FE) 跨文档对比明细；
//   - 图形编码：域名 → 2 字母色码徽章；FE → 圆点；verdict → 文字型结论徽章
//     （CONFLICT/CONSISTENT/INSUFFICIENT）；跨文档对比 → 逐报道点阵（绿=随
//     多数派、琥珀=同 Frame 异 FE、红=Frame 分歧、空心=缺失）；
//   - 短词保留：trigger 与 Frame 名（编码核心语义，且字符数少）；
//   - 边标签（contains/mentions/evokes/hasFE）删除：节点形状已自解释。
// 无 sidecar 判定的属性降级为规则章节（默认折叠）：文档徽章 → 抽取值胶囊
//   → 属性行（状态徽章）。数据全部来自 newsV6Store
//   （/api/news/v6/comparisons + /api/news/framing-verdicts）。
// ---------------------------------------------------------------------------

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
const STATUS_LABELS = {
  consensus: '一致',
  overlap: '部分重合',
  partial_overlap: '部分重合',
  different: '不同',
  missing_in_some_reports: '部分报道缺失',
  not_available: '未抽取',
};
const FRAMING_LABELS = {
  framing_conflict: '框架冲突',
  framing_consistent: '框架一致',
  insufficient: '证据不足',
};
// verdict 文字徽章：学术界面不用 emoji/符号图标，用小型大写标签承载结论语义。
const VERDICT_TAGS = {
  framing_conflict: 'CONFLICT',
  framing_consistent: 'CONSISTENT',
  insufficient: 'INSUFFICIENT',
};
// 章节排序：框架冲突最前，随后一致/证据不足，再按规则冲突严重度。
const TONE_ORDER = [
  'framing_conflict',
  'framing_consistent',
  'insufficient',
  'different',
  'overlap',
  'partial_overlap',
  'missing_in_some_reports',
  'not_available',
  'consensus',
];
const VERDICT_PRIORITY = ['framing_conflict', 'framing_consistent', 'insufficient'];
const MAX_SECTIONS = 14;
const MAX_RULE_SECTIONS = 10;

// ---- SVG 布局常量：每报道一列的确定性泳道网格 ----
const COL_W = 116; // 每条链占的列宽
const PAD_X = 16;  // 画布左留白（右侧另留触发词文字溢出位）
const TOP_Y = 20;  // 文档徽章中心 y

// 域名色板（Okabe-Ito + Paul Tol muted 色盲安全学术色板）：域名哈希取色，
// 同一域名全视图同色。低饱和、印刷友好，满足论文出版色彩可及性要求。
const DOMAIN_PALETTE = [
  '#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00',
  '#56B4E9', '#882255', '#44AA99', '#332288', '#999999',
];

// Frame 派系色板：与域名色板分离（域名色只属于 doc 徽章）。
// 同一 Frame 全视图同色 → 主干线与胶囊同色，汇流前即可读出派系分组。
const FRAME_PALETTE = [
  '#D55E00', // 朱红
  '#0072B2', // 蓝
  '#009E73', // 蓝绿
  '#CC79A7', // 红紫
  '#E69F00', // 橙
  '#56B4E9', // 天蓝
  '#882255', // 酒红
  '#44AA99', // 青绿
];

const focusedSection = ref('');
const expandedRules = ref(new Set()); // 规则章节默认折叠：已展开章节的 key 集合
const hoverFrame = ref('');           // 跨章节同 Frame 高亮（小写归一，''=未 hover）
const canvasEl = ref(null);           // mini-map 滚动容器引用
const articleText = ref(null);
const articleLoading = ref(false);
// 统一悬浮 tooltip：承载所有被图形化收起的长文本。
const tip = ref({ on: false, x: 0, y: 0, title: '', lines: [] });

const multiReportEvents = computed(() => newsV6Store.comparisons.events
  .filter((event) => Number(event.report_count) > 1)
  .sort((a, b) => b.report_count - a.report_count));

const selectedEvent = computed(() => (
  newsV6Store.comparisons.events.find((event) => event.event_id === newsV6Store.selectedEventId)
  || multiReportEvents.value[0]
  || newsV6Store.comparisons.events[0]
  || null));

const isMultiReport = computed(() => Number(selectedEvent.value?.report_count) > 1);

function toneRank(tone) {
  const index = TONE_ORDER.indexOf(tone);
  return index >= 0 ? index : TONE_ORDER.length;
}

function shortHash(reportId) {
  return String(reportId || '').replace(/^report:/, '').slice(0, 6).toUpperCase() || '?';
}

function trunc(text, n) {
  const s = String(text || '');
  return s.length > n ? `${s.slice(0, Math.max(n - 1, 1))}…` : s;
}

// 域名 → 2 字母缩写（www.x.com → X 的前两个字母）。
function domainAbbr(domain) {
  const label = String(domain || '').split('.').filter((part) => part && part !== 'www')[0] || '?';
  return label.slice(0, 2).toUpperCase();
}

// 域名 → 调色板颜色（字符串哈希，稳定可复现）。
function domainColor(domain) {
  const key = String(domain || '').toLowerCase();
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) % 9973;
  return DOMAIN_PALETTE[hash % DOMAIN_PALETTE.length];
}

// Frame 派系 → 颜色（同款字符串哈希，稳定可复现；空 key 返回 ''=中性色）。
function frameColor(key) {
  const s = String(key || '');
  if (!s) return '';
  let hash = 0;
  for (let i = 0; i < s.length; i += 1) hash = (hash * 31 + s.charCodeAt(i)) % 9973;
  return FRAME_PALETTE[hash % FRAME_PALETTE.length];
}

// 链的主导 Frame（出现最多的 frame，小写归一）；无 frame 返回 ''。
function chainFrameKey(chain) {
  const counts = new Map();
  for (const segment of chain.segments) {
    const key = String(segment.frame || '').trim().toLowerCase();
    if (key) counts.set(key, (counts.get(key) || 0) + 1);
  }
  let best = '';
  let bestN = 0;
  for (const [key, count] of counts) {
    if (count > bestN) { best = key; bestN = count; }
  }
  return best;
}

// 链排序：主导 Frame 聚类（同派相邻），同派内按 reportId 稳定；无 Frame 垫底。
function compareChains(a, b) {
  const ka = chainFrameKey(a) || '\uffff';
  const kb = chainFrameKey(b) || '\uffff';
  return ka.localeCompare(kb) || String(a.reportId).localeCompare(String(b.reportId));
}

// 报道元数据（标题/域名/原文哈希）来自 comparisons 的 event.reports。
const reportInfo = computed(() => {
  const map = new Map();
  for (const report of selectedEvent.value?.reports || []) {
    map.set(report.report_id, report);
  }
  return map;
});

function chainMeta(chain) {
  return reportInfo.value.get(chain.reportId) || {};
}

function chainDomain(chain) {
  return chain.domain || chainMeta(chain).domain || '';
}

// verdict.evidence[] → 每报道一条解码链：句子/触发词/Frame/FE 解码段。
function chainMapFromVerdict(verdict, attributeLabel) {
  const map = new Map();
  for (const entry of verdict.evidence || []) {
    const reportId = entry.report_id;
    if (!reportId) continue;
    if (!map.has(reportId)) {
      map.set(reportId, {
        reportId,
        urlHash: entry.url_hash || String(reportId).replace(/^report:/, ''),
        domain: entry.domain || '',
        segments: [],
      });
    }
    map.get(reportId).segments.push({
      sentence: entry.sentence || '',
      trigger: entry.trigger || '',
      frame: entry.frame || '',
      fe: entry.fe || '',
      attributeLabel,
    });
  }
  return map;
}

// 逐报道对比点阵（Table 2 的图形化）：每属性取多数派 (Frame·FE)，
// 报道随多数派=match（绿），同 Frame 异 FE=fe（琥珀），Frame 分歧=frame（红），
// 无任何证据=missing（空心）。
function reportDotStates(attributes, chains) {
  const perAttribute = new Map();
  for (const [attribute, verdict] of attributes) {
    const byReport = new Map();
    for (const entry of verdict.evidence || []) {
      const pair = [entry.frame, entry.fe].filter(Boolean).join('·');
      if (!pair || !entry.report_id) continue;
      if (!byReport.has(entry.report_id)) byReport.set(entry.report_id, new Set());
      byReport.get(entry.report_id).add(pair);
    }
    perAttribute.set(attribute, byReport);
  }
  const majority = new Map();
  for (const [attribute, byReport] of perAttribute) {
    const counts = new Map();
    for (const pairs of byReport.values()) {
      for (const pair of pairs) counts.set(pair, (counts.get(pair) || 0) + 1);
    }
    let best = '';
    let bestN = 0;
    for (const [pair, count] of counts) {
      if (count > bestN) { best = pair; bestN = count; }
    }
    majority.set(attribute, best);
  }
  return chains.map((chain) => {
    let frameDiverge = false;
    let feDiverge = false;
    let has = false;
    for (const [attribute, byReport] of perAttribute) {
      const pairs = byReport.get(chain.reportId);
      if (!pairs || !pairs.size) continue;
      has = true;
      const maj = majority.get(attribute) || '';
      if (maj && pairs.has(maj)) continue;
      const majFrame = maj ? maj.split('·')[0] : '';
      if (maj && [...pairs].some((pair) => pair.split('·')[0] === majFrame)) feDiverge = true;
      else frameDiverge = true;
    }
    if (!has) return { reportId: chain.reportId, state: 'missing' };
    return { reportId: chain.reportId, state: frameDiverge ? 'frame' : feDiverge ? 'fe' : 'match' };
  });
}

function finalizeFramingSection(section) {
  const verdicts = [...section.attributes.values()];
  section.tone = VERDICT_PRIORITY.find((tone) => verdicts.some((v) => v.verdict === tone))
    || 'insufficient';
  const toneVerdict = verdicts.find((v) => v.verdict === section.tone);
  section.reason = toneVerdict?.reason || verdicts[0]?.reason || '';
  section.attributeNames = [...section.attributes.keys()]
    .map((attribute) => ATTRIBUTE_LABELS[attribute] || attribute);
  section.ruleNote = section.ruleStatus && STATUS_LABELS[section.ruleStatus]
    ? `规则判定：${STATUS_LABELS[section.ruleStatus]}`
    : '';
  // Table-2 式对比明细收进 tooltip：每属性按 (Frame · FE) 去重，标注来源域名。
  const compareTip = [];
  for (const [attribute, verdict] of section.attributes) {
    const seen = new Set();
    const label = ATTRIBUTE_LABELS[attribute] || attribute;
    for (const entry of verdict.evidence || []) {
      const text = [entry.frame, entry.fe].filter(Boolean).join(' · ');
      if (!text || seen.has(text)) continue;
      seen.add(text);
      compareTip.push({ label, value: `${text}〔${entry.domain || shortHash(entry.report_id)}〕` });
    }
  }
  section.compareTip = compareTip;
  // 链按主导 Frame 聚类排序（点阵在排序后计算，顺序自动跟随列序）。
  section.chains = [...section.chainMap.values()].sort(compareChains);
  section.framedCount = section.chains.length;
  section.reportDots = reportDotStates(section.attributes, section.chains);
  return section;
}

// 有 sidecar 判定的比较：按共享实体合并（同一实体可同时有 attitude/roles 判定）。
function buildFramingSections(event) {
  const byEntity = new Map();
  for (const comparison of event.comparisons || []) {
    const verdict = framingVerdictFor(event, comparison);
    if (!verdict) continue;
    const entityKey = verdict.entity || verdict.entity_label || comparison.attribute;
    if (!byEntity.has(entityKey)) {
      byEntity.set(entityKey, {
        key: `${event.event_id}::framing::${entityKey}`,
        kind: 'framing',
        tone: verdict.verdict,
        entityLabel: verdict.entity_label || verdict.entity || entityKey,
        ruleStatus: verdict.rule_status || '',
        reason: verdict.reason || '',
        attributeNames: [],
        ruleNote: '',
        attributes: new Map(),
        chainMap: new Map(),
        compareTip: [],
        chains: [],
        framedCount: 0,
        reportDots: [],
      });
    }
    const section = byEntity.get(entityKey);
    if (!section.attributes.has(comparison.attribute)) {
      section.attributes.set(comparison.attribute, verdict);
    }
    const attributeLabel = ATTRIBUTE_LABELS[comparison.attribute] || comparison.attribute;
    for (const [reportId, chain] of chainMapFromVerdict(verdict, attributeLabel)) {
      const existing = section.chainMap.get(reportId);
      if (!existing) section.chainMap.set(reportId, chain);
      else existing.segments.push(...chain.segments);
    }
  }
  return [...byEntity.values()].map((section) => finalizeFramingSection(section));
}

// 无 sidecar 判定的属性降级：文档徽章 → 证据句图标 → 抽取值胶囊，汇聚到属性行。
function buildRuleSections(event) {
  const sections = [];
  for (const comparison of event.comparisons || []) {
    if (framingVerdictFor(event, comparison)) continue;
    const chainMap = new Map();
    for (const [reportId, items] of Object.entries(comparison.reports || {})) {
      const list = items || [];
      if (!list.length) continue;
      const meta = reportInfo.value.get(reportId) || {};
      chainMap.set(reportId, {
        reportId,
        urlHash: meta.url_hash || String(reportId).replace(/^report:/, ''),
        domain: meta.domain || '',
        segments: list.map((item) => ({
          sentence: item.evidence || '',
          trigger: '',
          frame: item.display || item.canonical || '',
          fe: '',
          attributeLabel: '',
        })),
      });
    }
    if (!chainMap.size) continue;
    sections.push({
      key: `${event.event_id}::rule::${comparison.attribute}`,
      kind: 'rule',
      tone: comparison.status || 'consensus',
      entityLabel: ATTRIBUTE_LABELS[comparison.attribute] || comparison.attribute,
      ruleStatus: '',
      reason: '',
      attributeNames: [],
      ruleNote: '',
      compareTip: [...chainMap.values()].map((chain) => (
        `${chainDomain(chain) || shortHash(chain.reportId)}：${chain.segments
          .map((segment) => segment.frame).filter(Boolean).join(' | ') || '—'}`
      )),
      chains: [...chainMap.values()].sort(compareChains),
      framedCount: chainMap.size,
      reportDots: [],
    });
  }
  return sections;
}

const framingSections = computed(() => {
  const event = selectedEvent.value;
  if (!event) return [];
  const sections = [
    ...buildFramingSections(event),
    ...buildRuleSections(event).slice(0, MAX_RULE_SECTIONS),
  ];
  sections.sort((a, b) => toneRank(a.tone) - toneRank(b.tone));
  return sections.slice(0, MAX_SECTIONS);
});

const conflictCount = computed(() => framingSections.value
  .filter((section) => section.tone === 'framing_conflict').length);

// ---- SVG 布局引擎：确定性泳道网格（每报道一列，节点等距步进） ----
// 每链独立 y 游标；多 segment（一篇报道多 Frame）向下堆叠；汇流轨取最深列底部。
// 列已按主导 Frame 聚类排序（finalize 阶段），每列携带派系色供主干线/胶囊使用。
function layoutSectionSvg(section) {
  const isRule = section.kind === 'rule';
  const chains = section.chains;
  const n = Math.max(chains.length, 1);
  const width = PAD_X + n * COL_W + 36; // 右侧 36px 留给末列触发词文字
  const cols = chains.map((chain, i) => {
    const cx = PAD_X + i * COL_W + COL_W / 2;
    const rows = [{ type: 'doc', y: TOP_Y, chain }];
    let y = TOP_Y + 28;
    for (const segment of chain.segments) {
      // 证据句不占独立行：全文随 trigger/Frame/FE tooltip 给出。
      if (!isRule) {
        rows.push({ type: 'trigger', y, segment });
        y += 26;
      }
      rows.push({ type: 'frame', y, segment });
      y += 26;
      if (!isRule) {
        rows.push({ type: 'fe', y, segment });
        y += 24;
      }
    }
    const frameKey = chainFrameKey(chain);
    return {
      cx,
      chain,
      rows,
      bottom: y,
      frameKey,
      color: frameColor(frameKey) || 'var(--tone-soft)',
    };
  });
  const railY = (cols.length ? Math.max(...cols.map((col) => col.bottom)) : TOP_Y) + 4;
  const centerX = PAD_X + (n * COL_W) / 2;
  const portY = railY + 26;
  return { width, height: portY + 2, cols, railY, centerX, portY };
}

const viewSections = computed(() => framingSections.value.map((section) => ({
  section,
  layout: layoutSectionSvg(section),
})));

// 主干线：每列一条贯穿线（节点不透明填充盖在线上方）。
function colLinePath(col, layout) {
  return `M ${col.cx} ${TOP_Y + 12} V ${layout.railY}`;
}

// 汇流轨：≥2 列时从首列水平连到末列。
function railPath(layout) {
  const { cols, railY } = layout;
  if (cols.length < 2) return '';
  return `M ${cols[0].cx} ${railY} H ${cols[cols.length - 1].cx}`;
}

// 中心下垂线：汇流轨中点 → 实体端口（末端留 7px 给手绘箭头）。
function dropPath(layout) {
  return `M ${layout.centerX} ${layout.railY} V ${layout.portY - 7}`;
}

function diamondPoints(cx, y) {
  return `${cx},${y - 6} ${cx + 6},${y} ${cx},${y + 6} ${cx - 6},${y}`;
}

function frameW(frame) {
  return Math.min(Math.max(String(frame || '').length * 6.4 + 12, 40), COL_W - 10);
}

// ---- 统一 tooltip：卡片式键值结构（灰色小标 label + 主文 value） ----
function showTip(evt, title, lines) {
  tip.value = {
    on: true,
    x: evt.clientX,
    y: evt.clientY,
    title: String(title || ''),
    lines: (lines || [])
      .filter(Boolean)
      .map((line) => (typeof line === 'object' && line !== null ? line : { label: '', value: String(line) }))
      .map((pair) => ({ label: pair.label || '', value: trunc(String(pair.value), 400) })),
  };
}

function moveTip(evt) {
  if (!tip.value.on) return;
  tip.value.x = evt.clientX;
  tip.value.y = evt.clientY;
}

function hideTip() {
  tip.value.on = false;
}

// 跟随鼠标并做视口夹紧（右缘翻转、下缘上提）。
const tipStyle = computed(() => {
  const width = 380;
  let x = tip.value.x + 14;
  let y = tip.value.y + 16;
  if (x + width > window.innerWidth) x = Math.max(8, tip.value.x - width - 12);
  if (y + 220 > window.innerHeight) y = Math.max(8, window.innerHeight - 232);
  return { left: `${x}px`, top: `${y}px` };
});

function showDocTip(evt, chain) {
  const meta = chainMeta(chain);
  showTip(evt, meta.title || chain.reportId, [
    { label: '来源', value: chainDomain(chain) || '（未知来源）' },
    { label: '操作', value: '点击查看报道原文' },
  ]);
}

// 证据句行已折叠：句子全文随 Frame tooltip 一并给出（trigger/FE tooltip 本就携带）。
function showFrameTip(evt, segment) {
  showTip(evt, `Frame：${segment.frame || '—'}`, [
    { label: '维度', value: segment.attributeLabel || '—' },
    { label: 'FE', value: segment.fe || '—' },
    { label: '证据句', value: segment.sentence || '—' },
  ]);
}

function showTriggerTip(evt, segment, chain) {
  showTip(evt, `触发词：${segment.trigger || '（无）'}`, [
    { label: '维度', value: segment.attributeLabel || '—' },
    { label: '证据句', value: segment.sentence || '—' },
  ]);
}

function showFeTip(evt, segment) {
  showTip(evt, `FE：${segment.fe || '—'}`, [
    { label: '维度', value: segment.attributeLabel || '—' },
    { label: '证据句', value: segment.sentence || '—' },
  ]);
}

function showEntityTip(evt, section) {
  showTip(evt, section.entityLabel, [
    section.reason ? { label: '判定理由', value: section.reason } : '',
    ...(section.compareTip || []),
    section.ruleNote
      ? { label: '规则判定', value: String(section.ruleNote).replace(/^规则判定：/, '') }
      : '',
  ]);
}

function isSectionOpen(section) {
  return section.kind !== 'rule' || expandedRules.value.has(section.key);
}

// ---- 论文出图：把当前章节 SVG 内联 computed 样式后导出为独立 SVG 文件 ----
// scoped CSS 与 CSS 变量不随 <svg> 序列化走：导出前逐元素内联关键属性，
// 并强制 stroke-dasharray:none（入场动画中间态兜底，保证线条完整）。
const EXPORT_PROPS = [
  'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-dashoffset',
  'stroke-linecap', 'stroke-linejoin', 'font-family', 'font-size', 'font-weight',
  'font-style', 'text-anchor', 'dominant-baseline', 'opacity',
];

async function exportSectionSvg(view) {
  const source = canvasEl.value?.querySelector(`svg[data-section-key="${view.section.key}"]`);
  if (!source) return;
  hoverFrame.value = ''; // 清除派系高亮，避免把 dim/hl 状态固化进导出图
  await nextTick();
  const clone = source.cloneNode(true);
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const srcEls = [source, ...source.querySelectorAll('*')];
  const dstEls = [clone, ...clone.querySelectorAll('*')];
  srcEls.forEach((el, index) => {
    const computedStyle = window.getComputedStyle(el);
    const style = dstEls[index].style;
    for (const prop of EXPORT_PROPS) {
      const value = computedStyle.getPropertyValue(prop);
      if (value) style.setProperty(prop, value);
    }
    style.setProperty('stroke-dasharray', 'none');
    style.setProperty('stroke-dashoffset', '0');
  });
  const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
  bg.setAttribute('x', '0');
  bg.setAttribute('y', '0');
  bg.setAttribute('width', source.getAttribute('width') || '100%');
  bg.setAttribute('height', source.getAttribute('height') || '100%');
  bg.setAttribute('fill', '#ffffff');
  clone.insertBefore(bg, clone.firstChild);
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  const name = String(view.section.entityLabel || 'section').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40);
  link.href = url;
  link.download = `framing_${name}.svg`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function toggleSectionFocus(key) {
  focusedSection.value = focusedSection.value === key ? '' : key;
}

// 实体行点击：framing 章节=聚焦切换；规则章节=展开/收起对比。
function onEntityClick(section) {
  if (section.kind === 'rule') toggleRuleExpand(section.key);
  else toggleSectionFocus(section.key);
}

function toggleRuleExpand(key) {
  const next = new Set(expandedRules.value);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  expandedRules.value = next;
}

// hover Frame 胶囊：记录派系 key（跨章节高亮由模板类绑定消费）并显示 tooltip。
// 仅 framing 章节参与派系高亮（规则章节的抽取值不触发全局变淡）。
function hoverFrameStart(evt, section, segment) {
  if (section.kind === 'framing') {
    hoverFrame.value = String(segment.frame || '').trim().toLowerCase();
  }
  showFrameTip(evt, segment);
}

function hoverFrameEnd() {
  hoverFrame.value = '';
  hideTip();
}

// 胶囊高亮类：hover 派系内=hl，派系外=dim；未 hover 或规则章节时不介入。
function frameHlClass(section, segment) {
  if (!hoverFrame.value || section.kind !== 'framing') return null;
  const key = String(segment.frame || '').trim().toLowerCase();
  if (!key) return 'dim';
  return key === hoverFrame.value ? 'hl' : 'dim';
}

// 主干线高亮类：与 hover 派系同色的链点亮，其余（含无 Frame 链）变淡。
function lineHlClass(section, col) {
  if (!hoverFrame.value || section.kind !== 'framing') return null;
  return col.frameKey === hoverFrame.value ? 'hl' : 'dim';
}

// mini-map：平滑滚动到对应章节。
function scrollToSection(index) {
  const canvas = canvasEl.value;
  if (!canvas) return;
  const node = canvas.querySelectorAll('.framing-section')[index];
  if (node) node.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function openArticle(chain) {
  const meta = chainMeta(chain);
  articleLoading.value = true;
  articleText.value = { title: meta.title || chain.domain || 'Article', body: '' };
  try {
    const response = await fetch(`/api/news/text/${chain.urlHash || meta.url_hash}`);
    articleText.value.body = response.ok ? await response.text() : `Unable to load article (${response.status})`;
  } finally {
    articleLoading.value = false;
  }
}

watch(
  () => newsV6Store.selectedEventId,
  () => {
    focusedSection.value = '';
    expandedRules.value = new Set();
    hoverFrame.value = '';
    articleText.value = null;
    tip.value.on = false;
  },
);
</script>

<template>
  <div class="news-framing">
    <header class="framing-head">
      <div class="framing-title">
        <h2>{{ selectedEvent?.canonical_name || 'Framing View' }}</h2>
        <small>叙事框架流程图 · 每报道一条编码链汇聚为跨文档结论 · 长文本见悬浮提示</small>
      </div>
      <div v-if="multiReportEvents.length" class="event-switcher" role="tablist" aria-label="多报道事件切换">
        <button
          v-for="event in multiReportEvents"
          :key="event.event_id"
          type="button"
          :class="['event-pill', { active: event.event_id === selectedEvent?.event_id }]"
          :title="`${event.canonical_name} · ${event.report_count} 篇报道${event.event_id === selectedEvent?.event_id ? ' · 当前事件' : ''}`"
          @click="selectNewsEvent(event.event_id)"
        >
          {{ event.canonical_name }}<small>{{ event.report_count }} 报道</small>
        </button>
      </div>
      <div class="framing-legend">
        <span class="legend-cap">FIG · LEGEND</span>
        <span class="legend-item"><i class="sw sw-doc" />报道</span>
        <span class="legend-item">句全文见悬浮提示</span>
        <span class="legend-item"><i class="sw sw-trig" />触发词</span>
        <span class="legend-item"><i class="sw sw-frame" />Frame</span>
        <span class="legend-item"><i class="sw sw-fe" />FE</span>
        <span class="legend-item"><b class="hl-note">同色 = 同 Frame 派系</b></span>
        <span class="legend-sep" />
        <span class="legend-item"><i class="dot dot-match" />随多数派</span>
        <span class="legend-item"><i class="dot dot-fe" />FE 分歧</span>
        <span class="legend-item"><i class="dot dot-frame" />Frame 分歧</span>
        <span class="legend-item"><i class="dot dot-missing" />缺失</span>
        <span class="legend-sep" />
        <span class="legend-item"><i class="legend-dot v-conflict" />冲突</span>
        <span class="legend-item"><i class="legend-dot v-ok" />一致</span>
        <span class="legend-item"><i class="legend-dot v-ins" />不足</span>
        <span class="legend-item"><i class="legend-dot v-rule" />规则对照</span>
        <strong v-if="conflictCount" class="conflict-count">{{ conflictCount }} 处框架冲突</strong>
      </div>
      <!-- mini-map：每章节一个 tone 色点，点击平滑滚动直达 -->
      <nav v-if="viewSections.length > 1" class="framing-minimap" aria-label="章节导航">
        <button
          v-for="(view, index) in viewSections"
          :key="view.section.key"
          type="button"
          :class="['minimap-dot', `mtone-${view.section.tone}`]"
          :title="`${view.section.entityLabel} · ${FRAMING_LABELS[view.section.tone] || STATUS_LABELS[view.section.tone] || view.section.tone}`"
          @click="scrollToSection(index)"
        />
      </nav>
    </header>

    <p v-if="newsV6Store.loading" class="framing-empty">Loading news graph…</p>
    <p v-else-if="newsV6Store.error" class="framing-empty">加载失败：{{ newsV6Store.error }}</p>
    <p v-else-if="!selectedEvent" class="framing-empty">暂无事件数据。</p>
    <p v-else-if="!isMultiReport" class="framing-empty">
      该事件仅有 {{ selectedEvent.report_count }} 篇报道，无法展开跨报道叙事冲突，请从上方切换到多报道事件。
    </p>
    <div v-else ref="canvasEl" class="framing-canvas">
      <section
        v-for="(view, sectionIndex) in viewSections"
        :key="view.section.key"
        :class="['framing-section', `tone-${view.section.tone}`, { focused: focusedSection && focusedSection !== view.section.key }]"
      >
        <!-- 章节头：编号 overline + 实体标题 + 论文出图按钮（折叠的规则章节不显示） -->
        <header v-if="isSectionOpen(view.section)" class="section-head">
          <span class="section-index">{{ String(sectionIndex + 1).padStart(2, '0') }}</span>
          <span class="section-kind">{{ view.section.kind === 'rule' ? 'RULE · EXTRACTED VALUES' : 'FRAMING · NARRATIVE DECODE' }}</span>
          <button class="export-btn" type="button" title="导出本章节为独立 SVG（论文用图）" @click="exportSectionSvg(view)">SVG ↓</button>
        </header>
        <strong v-if="isSectionOpen(view.section)" class="section-title">{{ view.section.entityLabel }}</strong>
        <svg
          v-if="isSectionOpen(view.section)"
          class="chain-svg"
          :data-section-key="view.section.key"
          :width="view.layout.width"
          :height="view.layout.height"
          :viewBox="`0 0 ${view.layout.width} ${view.layout.height}`"
          role="img"
          :aria-label="`${view.section.entityLabel} 编码流程图`"
        >
          <!-- 主干线：每报道一条垂直链，颜色=该链主导 Frame 派系 -->
          <path
            v-for="col in view.layout.cols"
            :key="`line-${col.chain.reportId}`"
            :class="['chain-line', lineHlClass(view.section, col)]"
            :style="{ stroke: col.color }"
            :d="colLinePath(col, view.layout)"
            pathLength="1"
          />
          <!-- 汇流轨 + 中心下垂 + 箭头（轨保持章节中性色：多色在此"合并"） -->
          <path v-if="railPath(view.layout)" class="chain-line rail" :d="railPath(view.layout)" pathLength="1" />
          <path class="chain-line drop" :d="dropPath(view.layout)" pathLength="1" />
          <polygon
            class="drop-arrow"
            :points="`${view.layout.centerX - 4},${view.layout.portY - 7} ${view.layout.centerX + 4},${view.layout.portY - 7} ${view.layout.centerX},${view.layout.portY}`"
          />
          <!-- 链上节点：形状编码语义，长文本进 tooltip；按列错峰浮现 -->
          <g
            v-for="(col, colIndex) in view.layout.cols"
            :key="col.chain.reportId"
            :style="{ animationDelay: `${0.7 + colIndex * 0.06}s` }"
          >
            <g v-for="(row, rowIndex) in col.rows" :key="rowIndex">
              <template v-if="row.type === 'doc'">
                <g
                  class="doc-badge"
                  role="button"
                  :aria-label="`打开报道：${chainMeta(row.chain).title || row.chain.reportId}`"
                  @click.stop="openArticle(row.chain)"
                  @mouseenter="showDocTip($event, row.chain)"
                  @mousemove="moveTip"
                  @mouseleave="hideTip"
                >
                  <rect
                    :x="col.cx - 12"
                    :y="row.y - 12"
                    width="24"
                    height="24"
                    rx="6"
                    :fill="domainColor(chainDomain(row.chain))"
                  />
                  <text :x="col.cx" :y="row.y + 3.5" class="doc-abbr">{{ domainAbbr(chainDomain(row.chain)) }}</text>
                </g>
              </template>
              <template v-else-if="row.type === 'trigger'">
                <g
                  class="hit"
                  @mouseenter="showTriggerTip($event, row.segment, col.chain)"
                  @mousemove="moveTip"
                  @mouseleave="hideTip"
                >
                  <polygon class="trigger-diamond" :points="diamondPoints(col.cx, row.y)" />
                  <text :x="col.cx + 10" :y="row.y + 3" class="trigger-text">{{ trunc(row.segment.trigger, 12) || '—' }}</text>
                </g>
              </template>
              <template v-else-if="row.type === 'frame'">
                <g
                  :class="['hit', frameHlClass(view.section, row.segment)]"
                  @mouseenter="hoverFrameStart($event, view.section, row.segment)"
                  @mousemove="moveTip"
                  @mouseleave="hoverFrameEnd"
                >
                  <rect
                    class="frame-capsule"
                    :x="col.cx - frameW(row.segment.frame) / 2"
                    :y="row.y - 8.5"
                    :width="frameW(row.segment.frame)"
                    height="17"
                    rx="8.5"
                    :style="{ stroke: col.color }"
                  />
                  <rect
                    class="frame-tab"
                    :x="col.cx - frameW(row.segment.frame) / 2 + 2"
                    :y="row.y - 5.5"
                    width="2.5"
                    height="11"
                    rx="1.25"
                    :style="{ fill: col.color }"
                  />
                  <text :x="col.cx" :y="row.y + 3" class="frame-text">{{ trunc(row.segment.frame, 14) || '—' }}</text>
                </g>
              </template>
              <template v-else>
                <g
                  class="hit"
                  @mouseenter="showFeTip($event, row.segment)"
                  @mousemove="moveTip"
                  @mouseleave="hideTip"
                >
                  <circle class="fe-dot" :cx="col.cx" :cy="row.y" r="4.5" />
                </g>
              </template>
            </g>
          </g>
        </svg>
        <!-- 实体行：跨文档结论（Figure 2 汇聚点 + Table 2 点阵） -->
        <button
          type="button"
          class="entity-line"
          :aria-label="`${view.section.entityLabel}：${FRAMING_LABELS[view.section.tone] || STATUS_LABELS[view.section.tone] || view.section.tone}${view.section.kind === 'rule' ? '（点击展开/收起对比）' : ''}`"
          @click="onEntityClick(view.section)"
          @mouseenter="showEntityTip($event, view.section)"
          @mousemove="moveTip"
          @mouseleave="hideTip"
        >
          <span v-if="view.section.kind === 'rule'" :class="['rule-caret', { open: expandedRules.has(view.section.key) }]" aria-hidden="true" />
          <span class="entity-dot" aria-hidden="true" />
          <strong class="entity-name">{{ trunc(view.section.entityLabel, 30) }}</strong>
          <span v-if="view.section.kind === 'framing'" class="verdict-badge">{{ VERDICT_TAGS[view.section.tone] || FRAMING_LABELS[view.section.tone] || view.section.tone }}</span>
          <span v-else class="verdict-badge rule-badge">{{ STATUS_LABELS[view.section.tone] || view.section.tone }}</span>
          <span v-if="view.section.kind === 'framing'" class="rule-note-tag" title="规则对照注记">RULE NOTE</span>
          <span v-if="view.section.reportDots.length" class="dot-matrix" aria-hidden="true">
            <i v-for="dot in view.section.reportDots" :key="dot.reportId" :class="['dot', `dot-${dot.state}`]" />
          </span>
          <small v-if="view.section.kind === 'framing'" class="entity-count">{{ view.section.framedCount }}/{{ selectedEvent.reports.length }} 篇</small>
          <small v-else class="entity-count">{{ view.section.framedCount }} 篇对比</small>
        </button>
      </section>
    </div>

    <div v-if="tip.on" class="framing-tip" :style="tipStyle">
      <strong class="tip-title">{{ tip.title }}</strong>
      <div class="tip-body">
        <div v-for="(pair, index) in tip.lines" :key="index" class="tip-row">
          <span v-if="pair.label" class="tip-label">{{ pair.label }}</span>
          <span class="tip-value">{{ pair.value }}</span>
        </div>
      </div>
    </div>

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
.news-framing { height: 100%; display: flex; flex-direction: column; overflow: hidden; color: #29343a; }
.framing-head { padding: 10px 14px 8px; border-bottom: 1px solid #d8dddf; background: #fff; flex: none; }
.framing-title h2 { margin: 0; font-size: 15px; }
.framing-title small { color: #718087; }
.event-switcher { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.event-pill { border: 1px solid #c8d0d4; background: #f7f8f8; border-radius: 14px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.event-pill.active { background: #29343a; color: #fff; border-color: #29343a; }
.event-pill small { margin-left: 5px; opacity: 0.65; font-size: 10px; }

/* ---- 紧凑形状图例：形状即语义，替代原文字说明段 ---- */
.framing-legend { display: flex; flex-wrap: wrap; gap: 9px; margin-top: 8px; font-size: 11px; color: #5a6870; align-items: center; }
.legend-cap { font-size: 9.5px; font-weight: 700; letter-spacing: 0.08em; color: #a4adb2; }
.legend-item { display: inline-flex; align-items: center; gap: 4px; }
.legend-sep { width: 1px; height: 12px; background: #d8dddf; margin: 0 2px; }
.sw { width: 11px; height: 11px; display: inline-block; }
.sw-doc { background: #0072b2; border-radius: 3px; }
.sw-trig { width: 8px; height: 8px; background: #fff; border: 1px dashed #8a949a; transform: rotate(45deg); }
.sw-frame { height: 11px; background: #fff; border: 1.25px solid #8a949a; border-radius: 6px; box-sizing: border-box; }
.sw-fe { width: 8px; height: 8px; border-radius: 50%; background: #fff; border: 1.2px solid #7a8691; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.dot-match { background: #009e73; }
.dot-fe { background: #e69f00; }
.dot-frame { background: #d55e00; }
.dot-missing { background: #fff; border: 1.5px solid #b9c2c8; }
.legend-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.v-conflict { background: #d55e00; }
.v-ok { background: #009e73; }
.v-ins { background: #8a949a; }
.v-rule { background: #fff; border: 1.5px solid #8a949a; }
.conflict-count { color: #d55e00; }
.framing-empty { padding: 24px 14px; color: #718087; }
.framing-canvas { flex: 1; overflow: auto; padding: 12px 14px 18px; }

/* ---- Figure-2 章节：tone 变量统一驱动链路、汇聚与结论徽章 ---- */
.framing-section { position: relative; display: flex; flex-direction: column; align-items: center; margin-bottom: 20px; scroll-margin-top: 8px; --tone: #7d8b93; --tone-soft: #c3ccd1; --band-bg: #f6f8f9; }
.framing-section.tone-framing_conflict { --tone: #d55e00; --tone-soft: #eab28c; --band-bg: #fdf6f0; }
.framing-section.tone-framing_consistent { --tone: #009e73; --tone-soft: #8fc6b6; --band-bg: #f1f8f5; }
.framing-section.tone-insufficient { --tone: #7d8b93; --tone-soft: #c3ccd1; --band-bg: #f6f8f9; }
.framing-section.tone-different { --tone: #b0722f; --tone-soft: #dcb389; --band-bg: #fbf4ec; }
.framing-section.tone-overlap,
.framing-section.tone-partial_overlap { --tone: #8f8a3d; --tone-soft: #cfcb9d; --band-bg: #f8f7ec; }
.framing-section.tone-missing_in_some_reports { --tone: #7d8b93; --tone-soft: #c3ccd1; --band-bg: #f6f8f9; }
.framing-section.tone-not_available { --tone: #a4adb2; --tone-soft: #d0d5d8; --band-bg: #f8fafa; }
.framing-section.tone-consensus { --tone: #3d8a76; --tone-soft: #a3c4bc; --band-bg: #f0f6f4; }
.framing-section.focused { opacity: 0.28; }
.framing-section.focused:hover { opacity: 0.7; }

/* ---- 章节头：编号 overline + 实体标题 + 论文出图按钮 ---- */
.section-head { display: flex; align-items: baseline; gap: 8px; width: 94%; max-width: 760px; }
.section-index { font-family: ui-monospace, 'Cascadia Mono', Consolas, monospace; font-size: 11px; font-weight: 600; color: #a4adb2; }
.section-kind { font-size: 10px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #8a949a; }
.export-btn { margin-left: auto; border: 1px solid #d5dbe0; background: #fff; color: #5a6870; font-size: 10px; line-height: 1.4; padding: 1px 7px; border-radius: 3px; cursor: pointer; opacity: 0; transition: opacity 0.15s ease; }
.framing-section:hover .export-btn { opacity: 1; }
.export-btn:hover { color: #29343a; border-color: #a4adb2; }
.section-title { width: 94%; max-width: 760px; margin: 1px 0 8px; padding-bottom: 5px; border-bottom: 1px solid #e6eaec; font-size: 14.5px; font-weight: 600; text-align: left; }

/* ---- SVG 流程图：主干线 / 汇流轨 / 节点形状 ---- */
.chain-svg { display: block; overflow: visible; }
.chain-line { fill: none; stroke: var(--tone-soft); stroke-width: 1.5; }
.chain-line.drop { stroke: var(--tone); }
.drop-arrow { fill: var(--tone); }
.doc-badge { cursor: pointer; }
.doc-badge:hover rect { stroke: #0072b2; stroke-width: 2; }
.doc-abbr { fill: #fff; font-size: 9px; font-weight: 700; text-anchor: middle; pointer-events: none; font-family: system-ui, sans-serif; }
.hit { cursor: default; }
.trigger-diamond { fill: #fff; stroke: #7d8b93; stroke-width: 1; stroke-dasharray: 2.5 2; }
.trigger-text { fill: #4a565d; font-size: 9.5px; font-style: italic; font-family: system-ui, sans-serif; }
/* 胶囊：白底 + 派系色描边（:style stroke）+ 左侧色条（.frame-tab），期刊图低饱和画法 */
.frame-capsule { fill: #fff; stroke-width: 1.25; }
.frame-tab { pointer-events: none; }
.frame-text { fill: #2c3e50; font-size: 9.5px; font-weight: 600; text-anchor: middle; pointer-events: none; font-family: system-ui, sans-serif; }
.fe-dot { fill: #fff; stroke: #7a8691; stroke-width: 1.2; }

/* ---- Frame 派系高亮：hover 同色点亮、异派变淡（跨章节） ---- */
.chain-line.hl { stroke-width: 2.5; }
.chain-line.dim { opacity: 0.22; }
.hit.dim { opacity: 0.22; }
.hit.hl .frame-capsule { stroke-width: 2; filter: drop-shadow(0 1px 2px #00000026); }
.hl-note { color: #5a6870; font-weight: 600; }

/* ---- 规则章节折叠指示 ---- */
.rule-caret { width: 0; height: 0; border-left: 5px solid #66757c; border-top: 4px solid transparent; border-bottom: 4px solid transparent; flex: none; transition: transform 0.15s ease; }
.rule-caret.open { transform: rotate(90deg); }

/* ---- mini-map：章节 tone 色点导航 ---- */
.framing-minimap { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; align-items: center; }
.minimap-dot { width: 10px; height: 10px; border-radius: 50%; border: 1px solid #0002; padding: 0; cursor: pointer; }
.minimap-dot:hover { transform: scale(1.3); }
.mtone-framing_conflict { background: #d55e00; }
.mtone-framing_consistent { background: #009e73; }
.mtone-insufficient { background: #7d8b93; }
.mtone-different { background: #b0722f; }
.mtone-overlap, .mtone-partial_overlap { background: #8f8a3d; }
.mtone-missing_in_some_reports { background: #7d8b93; }
.mtone-not_available { background: #a4adb2; }
.mtone-consensus { background: #3d8a76; }

/* ---- 入场绘制：链 → 汇流轨 → 下垂依次画出，节点随后浮现 ---- */
.chain-line { stroke-dasharray: 1; stroke-dashoffset: 1; animation: framing-draw 0.55s ease forwards; }
.chain-line.rail { animation-delay: 0.35s; }
.chain-line.drop { animation-delay: 0.5s; }
.chain-svg > g { animation: framing-fade 0.35s ease 0.7s backwards; }
.drop-arrow { animation: framing-fade 0.3s ease 0.9s backwards; }
@keyframes framing-draw { to { stroke-dashoffset: 0; } }
@keyframes framing-fade { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .chain-svg path { animation: none !important; stroke-dasharray: none !important; stroke-dashoffset: 0 !important; }
  .chain-svg > g, .drop-arrow { animation: none !important; }
}

/* ---- 实体行：跨文档结论（一行，细节在 tooltip） ---- */
.entity-line { display: inline-flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 4px 8px; max-width: 94%; margin-top: 2px; background: #fff; border: 1px solid var(--tone); border-radius: 6px; padding: 5px 12px; cursor: pointer; color: inherit; font: inherit; box-shadow: 0 1px 0 #0000000a; }
.entity-line:hover { box-shadow: 0 0 0 3px var(--band-bg); }
.entity-dot { width: 10px; height: 10px; border-radius: 50%; border: 2.5px solid var(--tone); flex: none; }
.entity-name { font-size: 12.5px; }
.verdict-badge { flex: none; border: 1px solid var(--tone); border-radius: 3px; padding: 1px 7px; font-size: 10px; font-weight: 700; letter-spacing: 0.06em; color: var(--tone); background: #fff; }
.rule-note-tag { flex: none; border: 1px solid #dbe0e3; border-radius: 3px; padding: 1px 5px; font-size: 9.5px; font-weight: 600; letter-spacing: 0.06em; color: #8a949a; background: #fff; }
.dot-matrix { display: inline-flex; gap: 3px; align-items: center; }
.entity-count { color: #8a949a; font-size: 10.5px; }

/* ---- 统一 tooltip：承载被图形化收起的长文本 ---- */
.framing-tip { position: fixed; z-index: 80; max-width: 380px; background: #fff; color: #29343a; border: 1px solid #d5dbe0; border-radius: 4px; padding: 8px 11px 9px; font-size: 11.5px; line-height: 1.55; box-shadow: 0 4px 18px #0000001f; pointer-events: none; }
.tip-title { display: block; margin-bottom: 5px; padding-bottom: 4px; border-bottom: 1px solid #e6eaec; font-size: 12px; }
.tip-row { display: flex; gap: 8px; margin-top: 3px; }
.tip-row:first-child { margin-top: 0; }
.tip-label { flex: none; min-width: 52px; color: #8a949a; font-size: 10.5px; padding-top: 1px; }
.tip-value { flex: 1; white-space: pre-wrap; word-break: break-word; }

.article-reader { position: fixed; z-index: 60; inset: 8% 10%; background: #fff; border: 1px solid #7e8b91; box-shadow: 0 12px 42px #0003; display: flex; flex-direction: column; }
.article-reader header { display: flex; justify-content: space-between; padding: 12px; border-bottom: 1px solid #d9dfe1; }
.article-reader pre { padding: 16px; margin: 0; overflow: auto; white-space: pre-wrap; font: 13px/1.55 system-ui, sans-serif; }
</style>






