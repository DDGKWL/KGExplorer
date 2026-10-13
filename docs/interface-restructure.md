# KGExplorer Interface Restructure

## Paper-Oriented System Framing

KGExplorer should not be presented as a knowledge graph browser. Its paper-facing role is:

**An evidence-centered visual analytics system for discovering, inspecting, and adjudicating uncertain cultural-heritage knowledge claims extracted from scanned catalogues.**

The system should therefore avoid directly presenting conflicts as final facts. It should guide experts through a workflow from corpus evidence to candidate issues, then to side-by-side evidence comparison, and only finally to adjudicated KG updates.

## Proposed View Content

### 1. Global KG Overview

Purpose: help experts explore the full cultural-heritage KG before selecting a suspicious region.

Current implementation:
- `KnowledgeGraphView.vue`
- Global graph with artifacts, semantic entities, source coverage, and contention glyphs.
- The graph is the overview, because the target data is graph-structured and expected to scale.

Design intent:
- This should become the first view experts see.
- It frames conflicts as suspicious graph regions and uncertain evidence patterns, not as precomputed conclusions.
- It should support graph aggregation, filtering, semantic zoom, neighborhood expansion, and evidence-aware glyphs.
- Small pairwise relation summaries are not suitable for the global view because they do not scale well to large, heterogeneous KG topology.

### 2. Knowledge Hierarchies

Purpose: provide analytical entry points, not a static dump of all database contents.

Current organization:
- Sources: catalogue/book -> artifacts.
- Entities: form, period, collection.
- Conflicts: candidate issue classes.

Design intent:
- The hierarchy is meaningful only if it helps experts enter the analysis from different scholarly contexts.
- It should remain a navigation lens, not a result view.

### 3. Candidate Issue Queue

Purpose: collect suspicious or high-uncertainty items for expert inspection.

Design intent:
- Avoid wording that implies the system has already fully determined the conflict.
- Queue items are candidates created by extraction, OCR confidence, source disagreement, missing evidence, or domain-rule triggers.
- Future versions should include uncertainty scores and explanations of why an item is surfaced.

### 4. Global KG / Local Comparison

Purpose: connect overview patterns to entity-level and attribute-level inspection.

Current implementation:
- Global KG shows artifacts, entities, and contention glyphs.
- Comparison KG shows a selected artifact and its candidate attribute issues.

Design intent:
- The global KG is for orientation and selection.
- The comparison view is for narrowing from artifact to claim.
- The views should not replace evidence inspection; they should route users into the Evidence Tray.

### 5. Evidence Tray

Purpose: core contribution area for evidence-grounded adjudication.

Current content:
- Source-level cards.
- OCR/raw text.
- Placeholder scan page and local detail views.
- Pinning selected evidence into a tray.

Design intent:
- This should become visually and functionally central.
- It should support scan pages, OCR blocks, Markdown chunks, image crops, texture/detail comparison, and source metadata.
- Experts should be able to cite exact evidence used in a final decision.

### 6. Decision Workspace

Purpose: transform inspected evidence into a provenance-preserving KG update.

Current content:
- Source assertions.
- Interpretation hypotheses.
- Evidence-grounded revision draft.
- Adjudication actions.

Design intent:
- This should not feel like an automatic answer panel.
- It should be a structured authoring and provenance workspace.
- The adjudication log should be secondary and append-only, not the main right-panel logic.

## Near-Term Visualization Directions

1. Scalable global KG overview:
   - Use graph aggregation, semantic zoom, local expansion, filtering, and uncertainty glyphs.
   - The global view should reveal suspicious regions without enumerating all conflicts as final answers.
   - Source coverage and evidence provenance should be encoded on graph nodes/edges, not separated into a small pairwise summary view.

2. Evidence-quality overview:
   - Encode OCR confidence, missing images, image-text alignment, and extraction uncertainty.
   - Helps avoid directly showing conflict classes as final labels.

3. Artifact contention glyph:
   - Current ring glyph can be developed into a compact uncertainty glyph.
   - Encode number of sources, missingness, candidate conflict classes, and adjudication state.

4. Evidence tray comparison:
   - Add scan page, OCR block, Markdown segment, image crop, and detail texture comparison.
   - This should be the strongest application-specific contribution.

5. Provenance ledger:
   - Store decisions with cited evidence and supersession links.
   - Move ledger into a secondary tab, not the primary adjudication interface.

## Case 3 · News Framing View (implemented)

The news workspace adds a third center tab, `Framing View`
(`frontend/src/components/NewsFramingView.vue`), rendering the DORIS-paper
Figure-2 style narrative conflict graph for the selected multi-report event.

Layout decision (entity-centric, deterministic SVG flowchart): Figure 2 in the
DORIS paper is two vertical decode chains converging on a shared conceptual
entity node, and the final conflict result lives not in the figure but in
Query 2 / Table 2 (the cross-document `(Frame, FE)` comparison). The view
therefore renders one section per shared entity as a deterministic layered DAG
(not force-directed): a pure-JS layout function (`layoutSectionSvg`) emits
hand-written SVG path strings — one 116px column per report, a per-chain y
cursor with fixed row steps (doc → trigger → frame → FE; the full evidence
sentence lives in the tooltip instead of its own row; multi-segment chains
stack downward), then orthogonal convergence (per-column
main line → full-span rail at the deepest column's bottom → centered drop with
an arrowhead) onto the entity line. No d3 is required (d3 ^7.9.0 remains only
for `KnowledgeGraphView`). The earlier property×report matrix rows were
dropped, as were the former CSS convergence rail and text-heavy report bands.

Structure per section:

- One chain per report with evidence, drawn as SVG shapes on a vertical main
  line: a 2-letter domain color badge (colorblind-safe Okabe-Ito/Tol `DOMAIN_PALETTE`,
  hashed from the domain so one outlet keeps one color across the whole view;
  hover shows outlet + full title, click opens the article via
  `/api/news/text/<url_hash>`) → trigger as a dashed diamond with the word
  beside it → `Frame` as a white capsule outlined in the faction color with a small color tab on its left edge (journal-figure low-saturation style) → `FE` as a hollow dot. Columns are sorted
  by dominant frame (`chainFrameKey`: most frequent frame per chain,
  lowercase-normalized, reportId tie-break, frameless chains last) so
  same-faction chains sit adjacent before converging, and both the main line
  and the Frame capsules carry the chain's faction color from a separate
  `FRAME_PALETTE` hash (Okabe-Ito colorblind-safe academic palette; same frame → same color across the whole view;
  frameless chains fall back to the section's neutral tone) — the convergence
  rail stays neutral, so it reads as several colors merging. Node shape
  encodes the ontology type, so the edge labels
  (`contains` / `mentions` / `evokes` / `hasFE`) were dropped entirely. A
  chain can hold several decode segments (one report may carry multiple
  frames; sentences are deduped).
- Long text hides behind one mouse-following tooltip: the full evidence
  sentence (no longer a dedicated row — it rides on the trigger/Frame/FE
  tooltips), the article title, the DORIS reason line, and the Table-2-style
  `(Frame · FE)` comparison deduped per attribute and labeled by outlet
  domain. Only short words (trigger, frame names, entity name) stay as node
  labels. Hovering a Frame capsule lights every same-frame capsule and chain
  across all sections while fading the rest (cross-section faction
  highlight).
- The shared entity line under the drop carries the conclusion: a text verdict
  badge (`CONFLICT` vermillion / `CONSISTENT` bluish-green / `INSUFFICIENT`
  grey — 10px uppercase, letter-spaced, outlined in the tone color; no emoji
  anywhere in the view), the `RULE NOTE` cross-note tag ("rule consensus
  but framing conflict" — the Figure-2 point that surface agreement can hide
  narrative divergence), and a per-report dot matrix that replaces the former
  comparison text chips (green = follows the majority `(Frame·FE)`, amber =
  same frame different FE, red-orange = frame divergence, hollow = missing).
- Verdicts (`/api/news/framing-verdicts`, looked up via `framingVerdictFor`)
  are grouped by `verdict.entity` across attributes: one entity holding both
  an attitude and a roles verdict renders as a single section with
  per-attribute segment tooltips; the section tone is the worst verdict
  present.
- Comparisons without a sidecar verdict degrade to rule sections: attribute
  line instead of the entity line, per-report extracted values as value
  capsules, rule status tones (`different` / `overlap` / `partial_overlap` /
  `missing_in_some_reports` / `consensus`). Rule sections start collapsed to
  a single entity-line bar (a CSS-triangle caret; click to expand/collapse, state resets
  on event switch) while framing sections always render their graph.
  Degradation is a view feature, not a data gap: the sidecar only annotates
  slots where frame signals exist.
- Navigation and motion: the header carries a mini-map (one tone-colored dot
  per section, native tooltip with entity + verdict, smooth-scroll on click)
  so the conflict map and section count are readable at a glance; on entry,
  chains draw first, then the rail and drop (normalized `pathLength` +
  stroke-dashoffset), with nodes fading in staggered per column — fully
  disabled under `prefers-reduced-motion`.
- The former legend paragraphs and per-section band notes were replaced by a
  compact figure-caption-style legend row in the header (FIG · LEGEND, color dots instead of emoji); section focus toggling, event
  pills, conflict count, and the article-reader modal are unchanged.
- Academic look-and-feel pass (the system targets a paper demo, not a flashy
  dashboard): both palettes were switched to the Okabe-Ito / Paul Tol
  colorblind-safe academic set and the semantic verdict colors follow it
  (conflict #D55E00 vermillion, consistent #009E73 bluish-green, rule tones
  desaturated); capsules and dots are white-filled with colored strokes
  instead of solid fills; every section opens with an overline header
  (01 - FRAMING · NARRATIVE DECODE / RULE · EXTRACTED VALUES, monospace index
  plus letter-spaced small caps) above the entity name and a hairline;
  tooltips are white key-value cards (grey label column, hairline under the
  title) instead of a dark text dump; the section index and short codes use
  monospace.
- Paper-figure export: each expanded section header carries an SVG export
  button (visible while hovering the section) that serializes that
  section's SVG with all computed styles inlined (scoped CSS and CSS
  variables do not survive serialization), forces stroke-dasharray: none so
  the entry animation can never ship a half-drawn line, clears the faction
  highlight first, and downloads an SVG file named after the entity on a
  white background — one section, one figure, ready for LaTeX/slides.

Wiring:

- Tab strip: news-only third tab via the `centerTabs` computed in `App.vue`
  (other cases never see it); `NewsFramingView` renders in the graph pane as a
  `v-else-if` branch next to the lens pane.
- Toolbar: the news "Framing" button jumps to the tab for the currently
  selected event; `onSelectArtifact` syncs `newsV6Store.selectedEventId`, so
  Global KG / lens selections drive the framing canvas bidirectionally.
- The view follows `newsV6Store.selectedEventId` with a built-in switcher over
  the multi-report events; single-report events show an explanatory empty state.
- The graph-op toast became `v-show` so it no longer participates in the
  pane's `v-if` chain (previously a merge/split toast could unmount the lens
  pane for 4 seconds; the same wart would have unmounted the framing view).

