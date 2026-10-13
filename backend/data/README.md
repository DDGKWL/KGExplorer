# KGExplorer Data

Guqin (Case 1) raw OCR, triplet, and image evidence live under `backend/data/raw`.
Astronomy (Case 2) tables and cutouts live under `backend/data/astro/` and must
stay separate; see `backend/data/astro/README.md`.

## Layout

- `raw/triplet/`: extracted entity and relation workbooks.
- `raw/text/`: OCR or Markdown text snippets, grouped by source folder.
- `raw/image/`: cropped image evidence, grouped by artifact id.
- `books.json`: canonical book ids and availability flags.

## Current Source Coverage

| Book ID | Title | Triplets | Text | Images |
| --- | --- | --- | --- | --- |
| QNQY | 千年清音 | yes | yes | yes |
| Yjzq | 一见钟琴 | yes | yes | yes |
| CQL | 藏琴录 | yes | yes | yes |
| Gq | 古琴 | yes | yes | yes |
| Gggq | 故宫古琴图典 | no | yes | no |
| GCGQ | 馆藏古琴整理与研究 | no | yes | no |
| WXAC | 吾心安处 | no | yes | no |
| Zggq | 中国古琴珍萃 | no | no | no |

## Notes

The raw files are intentionally kept unchanged. Backend import scripts should build derived indices into `backend/data/processed` instead of editing files in `raw`.

Artifact ids follow the source prefix plus numeric id, such as `CQL009`, `Gq00014`, `QNQY017`, and `Yjzq00011`.

## Guqin Alignment Contract

`processed/case1_guqin.json` is rebuilt by `scripts/rebuild_case1.py`.
`processed/case1_audit.json` is the machine-checkable audit output. The older
`contested_kg.json` and `guqin_alignment.json` files are retained as historical
derivatives and are not the Case 1 API source.

- Physical/API key: `BOOK00016`, which is also the image-folder and `txt-id` stem.
- Canonical human-readable key: `BOOK-00016`.
- Unnamed instruments use a traceable display name such as `无名琴 · Gq00016`.
- `values` remains the verbatim extracted value.
- `normalized_values` is only used for conservative comparison.
- `value_details.group` is a broader visual aggregation category and never replaces the evidence value.
- Every Guqin artifact record is required to have an exact `txt-id` file and image directory. Extra source pages without an artifact row are retained in `raw` but excluded from the artifact graph.
- `source_catalog` distinguishes four primary multimodal sources from text-only contextual sources.
- `all_slots` preserves every mapped attribute, while `caus` is the curated analysis projection used by the visual conflict views.
- Same-name records are confirmed only when independent identity evidence agrees. Ambiguous matches remain separate source-local records with `alignment_candidates`.

Run the audit from `backend/`:

```bash
python3 scripts/rebuild_case1.py
```
