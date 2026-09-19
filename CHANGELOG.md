# Update notes

Every feature update to this package gets an entry here, and a short version of it in the
"What's new" section of each README ([English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) ·
[हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md)).

The package carries three independent versions (see "Versions" in the README). Each entry says which of them moved.

## Unreleased

- Docs: the claude.ai install path is now also verified on a **free plan** (2026-09-19, a 3-company request ran end to
  end, scoring scripts included, without hitting a usage limit). READMEs and the project page say so.

## v0.2.0 (2026-09-19)

`skill_version` 0.1.1 → **0.2.0** · `schema_version` 0.1.0 (unchanged) · `score_version` `kbtm-score-0.1.0` (unchanged)

No existing score changes in this release. Every golden scored fixture still reproduces byte for byte.

### New: tooling to check whether the scores mean anything

Until now, scores were reproducible but had never been compared with a person's judgement. This release adds
the tooling to start doing that. It does **not** change any weight or threshold; those decisions wait for data.

- `scripts/make_review_sheet.py` turns a scored run into a CSV sheet for a trade operator to fill in:
  accept, reject or unsure for each company, with a reason code for a reject. The sheet is **blind by default**.
  It shows no score, rank or qualified flag, and rows are in a fixed shuffled order, so the reviewer isn't steered
  by the rubric. `--include-excluded` mixes excluded companies in, which is the only way to find wrong exclusions.
- `scripts/acceptance_report.py` joins filled sheets back to the scored runs and reports:
  - the share of reviewed companies the operator accepted (Human Acceptance Rate), overall and split by the
    `qualified` flag, score band, country and reject reason
  - what precision and recall each threshold from 50 to 90 would have given
  - how well the total score, and each of the six dimensions, separates accepted from rejected companies,
    and how many distinct values each dimension actually produced
  - excluded companies the operator would have accepted
- The report flags itself as `insufficient_sample` below 30 decided reviews and says it can't justify a weight
  or threshold change.
- It refuses bad input instead of repairing it: unknown verdicts, a reject with no reason, conflicting duplicate
  rows, rows that match no company, the same company in two scored files, runs scored under different
  `score_version`s, and notes that contain an email address or phone number. Registration numbers, year ranges
  and URLs in a note are fine. Reviewers are identified by role, never by name.
- When excluded companies are mixed into a blind sheet, any column that would give them away (the country
  column, on a discovery run) reads `not_shown` on every row.
- Cells that a spreadsheet would run as a formula (a company name starting with `=`, `+`, `-` or `@`) are
  written with a leading apostrophe.
- New schema `schemas/acceptance-report.schema.json`; the numbers the tooling uses live in a new `calibration`
  block in `schemas/scoring.config.json`, which no scorer reads.
- The protocol (how to build the sample, how to read each block, and what result would settle each open
  calibration question) is in `references/calibration-notes.md` §7.

This measures Human Acceptance Rate only. Whether a contacted lead turns into an RFQ still needs outcome data
from the application layer.

### New: market packs for India, Indonesia and Türkiye

- **Buyer search** (`references/buyer-discovery.md` §4): local-language queries (Hindi, with English first for
  India; Indonesian; Turkish), trade shows, and regulator registries that reveal who imports what. Sources not
  yet exercised in a live run are marked "not yet field-tested".
- **Market-entry rules used in matching** (`references/compliance-notes.md` §5): India CDSCO import
  registration, Indonesia BPOM notification, Indonesia's mandatory halal certification for cosmetics (written as
  date-dependent on `--as-of`; sources give 17 October 2026 as the deadline), and Türkiye TİTCK notification
  through ÜTS. `references/seller-discovery.md` §8.2 shows how Korean seller sites evidence each one.
- **Direct-marketing checklist rows** for IN, ID and TR (`compliance-notes.md` §4.1) and six new notice blocks in
  `templates/legal_notices.md`. As everywhere in this package, these list what a person must check. They are
  not legal conclusions, and five of the six blocks carry status `unknown`.
- **Halal spellings**: `Helal`, `Sertifikat Halal` and `हलाल` are mapped to the `HALAL` token by the agent, with
  the certifying body and its scope named in the notes. Recognition in Indonesia is per body and per scope.
- The agent never adds a required certification that the RFQ didn't state. It raises the question as a risk.
- **Company names**: Indian (`Pvt Ltd`, `Private Limited`, `LLP`), Indonesian (`PT`, `CV`, `Tbk`) and Turkish
  (`A.Ş.`, `Ltd. Şti.`, `San. ve Tic.`) legal forms are now stripped during normalisation, each only from the
  end of the name it really appears at. All-caps Turkish names with `İ` now normalise correctly.
  **If you stored records with an earlier version, re-run normalisation on them before deduplicating against
  new records**: the normalised name is a fallback dedupe key and it changes for these names
  (`references/data-contract.md` §11.4).
- **Drafts** for recipients in these markets may be written in English, Hindi, Indonesian or Turkish. The
  legal-notice block is still copied verbatim.
- A new matching fixture (destination Indonesia, halal required) shows the existing rubric handles these
  markets with no scoring change.

### Tests

179 → **252** cases.

### Docs

- The workflow overview image now appears at the top of all six READMEs.
- This changelog, and a "What's new" section in every README.

## v0.1.1 (2026-09-14)

- Trade terms a seller hasn't published now appear on the `Missing:` line instead of being dropped.
- Added the claude.ai install path (upload the release ZIP; no terminal needed).

## v0.1.0 (2026-09-14)

First release: Buyer Discovery, Seller Discovery, RFQ Matching and Outreach Draft, with deterministic scoring,
the evidence contract, the file/HTTP adapter and the test suite.
