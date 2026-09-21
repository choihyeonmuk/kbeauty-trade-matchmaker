# Update notes

Every feature update to this package gets an entry here, and a short version of it in the
"What's new" section of each README ([English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) ·
[हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md)).

The package carries three independent versions (see "Versions" in the README). Each entry says which of them moved.

## Unreleased

No version has moved yet. This adds one script and one document type and changes no scorer, so at release it is
a `skill_version` MINOR and leaves `schema_version` and `score_version` where they are.

### New: RFQ intake (`intake_rfq.py`)

- Mode 3 needed an RFQ document; a buyer sends one line in a chat window. The agent now writes an `rfq-intake`
  input — each field with the verbatim `quote` it was read from — and `scripts/intake_rfq.py` checks it:
  - a quote that is not in the message refuses the whole input (exit 1, nothing written), so a value the buyer
    never wrote cannot enter the RFQ
  - a stated number must be the number in its quote (`50000` is not "5,000 pcs"), and a quote is a phrase: not
    a letter, not a slice of a word, not the whole message
  - a region word never becomes a country code, a number with no unit is held instead of being read as units,
    a price with no currency is held, and a value the agent inferred is shown back to the buyer
  - what is missing or ambiguous comes back as `questions[]`, in a fixed order, in English and Korean
  - no product category means no RFQ at all (`ready_for_matching: false`)
- The emitted RFQ is `status: draft`, `score_version: unscored`, keeps every quote under `extensions.intake`, and
  is validated against `rfq.schema.json`; the readiness figure is `score_match.py`'s own function, so it is the
  number Mode 3 prints. The report does not copy the message: it keeps its length, its SHA-256 and the quotes
  (2..200 characters each, refused when they carry an address or telephone shape).
- `schemas/rfq-intake.schema.json`, `validate_output.py --schema rfq-intake` (and `auto`), the tool server's
  `validate_output` enum, `references/data-contract.md` §9.7, `tests/cases.md` §18. Tests: 748 → 796.
- Not yet: a `intake_rfq` tool on the optional MCP server, and README updates (they land with the release).

## v0.4.0 (2026-09-21)

`skill_version` 0.3.0 → **0.4.0** · `schema_version` 0.1.0 (unchanged) · `score_version` `kbtm-score-0.1.0` → **`kbtm-score-0.2.0`**

**This release can change a score.** Eight audit fixes landed in the scorers and one golden fixture no longer
reproduces byte for byte (kantoimport: `evidence_quality` 64 → 74, confidence 0.41 → 0.47, score 36 → 37), so by
the package's own bump rule the rubric version moves. Scores stored under `kbtm-score-0.1.0` are stale by
definition: re-score the stored raw records before comparing them with new runs. `diff_runs.py` refuses to
compare the two versions. One new document type (`outreach-draft`) changes no existing shape, so
`schema_version` stays.

### New: draft validation (`validate_output.py --schema outreach-draft`)

- `validate_output.py --input draft.md --schema outreach-draft --record <scored run> --strict` checks a Mode 4
  draft against `schemas/outreach-draft.schema.json` and rules `DRAFT-01` … `DRAFT-12` (listed in
  `references/output-format.md` 10.4.1):
  - the 10.4 envelope, its headings and their order, and an unticked reviewer checklist
  - a subject of 60 characters or fewer, and the subject prefix a notice block mandates (`KR.corporate_email`)
  - every `Personalization facts` line cites a URL that is the `source_url` of an evidence item on the target,
    with the same observed date, and not only stale evidence
  - the target is on the scored run, `qualified: true`, not excluded, and of the right side
  - the channel is one of the target's own company-level `contact_channels`
  - no fake opt-out, no unrendered `{{token}}` or `[[ev:` marker
  - the compliance flags are what `compliance.config.json` resolves for the jurisdiction and channel
- It does **not** judge trade terms or wording. A value typed into the recorded-profile block is the reviewer
  checklist's job, not the validator's.
- `schemas/compliance.config.json` is the jurisdiction × channel lookup behind the last rule. It carries the
  same keys, statuses and blocks as `templates/legal_notices.md`, and a test fails when they drift apart. It
  includes the India, Indonesia and Türkiye rows that v0.2.0 added to `compliance-notes.md`.

### New: evidence rules under `--invariants`

- `EVI-01` … `EVI-06`: source tier against source type, official domain, the confidence cap on inferred
  evidence, evidence quality, confidence, and stale age. Each has a failing fixture in `tests/fixtures/invalid/`,
  and `tests/fixtures/valid/` holds honest records the policy allows, each with a control that must fail.

### Fixed: audit findings in the scorers (`AR-01` … `AR-08`)

- `AR-01` `--config` now reaches confidence and evidence quality.
- `AR-02` MOQ unit synonyms (`pcs`, `pieces`, `EA`, `개`, `unit`) are all `units`, so they no longer switch
  `HF-03` off through a false unit mismatch.
- `AR-03` a category that normalises to nothing (a vertical marker, an unmapped slug) counts as absent and
  never rejects a seller.
- `AR-04` country names normalise to alpha-2.
- `AR-05` a document that fails its own validation never lands on `--output`. It is written beside it as
  `*.invalid.json`, so a chained command cannot pick it up.
- `AR-06` an uncited certification costs at least what a tier-4 one does.
- `AR-07` a reciprocal conflict (two items cross-linked by `conflicts_with`) is one conflict, not two. This is
  the fix that moves the golden score above.
- `AR-08` Korean legal forms glued to a name (`주식회사한빛코스메틱`) and spacing variants dedupe. They run
  alongside the v0.2.0 legal-form vocabularies.

### Fixed: `NA` is a code, not a stand-in for unknown

- INV-02 rejected `NA` everywhere, including the North America token of `seller.export_regions` and the ISO
  alpha-2 of Namibia, so a seller that states it exports to North America could not be recorded at all. The exact
  upper-case token is now accepted in code-valued fields and in the evidence value that mirrors them. Found on a
  live Mode 2 run.

### Changed: MCP server and failed scorers

- Because of `AR-05`, a scorer that exits 1 leaves `output_path` unwritten. The server reports the
  `*.invalid.json` sibling instead, and refuses an `output_path` whose sibling already exists, so "never
  overwrites a file" still holds.

### Tests

- 536 → 748 cases. Two v0.3.0 goldens were regenerated for the score change (`diff.buyers.uae.asof`,
  `export.sellers.csv`); every other golden reproduces with only the version strings changed.

## v0.3.0 (2026-09-19)

`skill_version` 0.2.0 → **0.3.0** · `schema_version` 0.1.0 (unchanged) · `score_version` `kbtm-score-0.1.0` (unchanged)

No existing score changes in this release. Every golden scored fixture still reproduces byte for byte. The new
scripts are operator tools outside the pipeline: no scorer reads what they write. Three new document types
(`run-diff`, `recheck-queue`, `tradewith-bulk-buyers`) change no existing shape, so `schema_version` stays.

### New: compare two runs (`scripts/diff_runs.py`)

- `scripts/diff_runs.py --before <run> --after <run>` compares two scored runs of the same search or RFQ
  (`discovery-result` or `match-result`) and writes one `run-diff` document (`schemas/run-diff.schema.json`):
  - new and gone companies. A company that a dedupe merged into another reads as `merged_into`, not as a lost lead
  - companies that became excluded or returned, and exclusions whose rule set changed (rule ids only)
  - per company: score, base score and rank (match), changed dimensions, qualified flag, confidence and
    Missing-line changes
  - whether the as_of date, threshold, query (discovery) or weights (match) changed, with fixed notes for partial
    runs, different skill versions and match listings cut by threshold or `top_n`
- Records pair by id, then through `merged_from`. Nothing else is guessed: a rename without `merged_from` is
  gone + new.
- It refuses rather than approximates: runs scored under different `score_version`s (no opt-in), a discovery run
  against a match run, buyer against seller, match runs for different RFQs, a run that fails its own schema, a
  record id listed twice, a match `rank` that is not its position, and an `--as-of` earlier than either run.
- It copies no contact channel, evidence, website or observed value. Only company name, domain and rule ids travel.
- Details: `references/data-contract.md` §9.4.

### New: stale-evidence re-check queue (`scripts/stale_evidence.py`)

- `scripts/stale_evidence.py --input <records> --as-of <date>` lists which stored records, material claims and
  evidence pages to re-read, most urgent first, as a `recheck-queue` document
  (`schemas/recheck-queue.schema.json`). It reads a discovery-result, golden bundle, dedupe output, match input,
  list of records or single record. It fetches nothing and changes no record or score.
- Reasons, most urgent first: `site_unreachable`, `record_flagged_stale`, `past_stale_threshold`,
  `source_flagged_stale`, `unresolved_conflict`, `aging`, `undated`. A material claim with no current evidence
  item is also reported as `no_current_evidence`. An old item whose claim already has a current item is not queued.
- `--as-of` is required; the clock is never read. Bucket edges, `stale_threshold_days` and the material claims
  come from the existing `evidence` block of `scoring.config.json`, so the queue and the score agree on what
  "old" means. Both edges are inclusive, as in the scorer.
- An undated page is not a stale page: it counts as current while its last reading (`observed_at`) does.
- Refused with exit 1: a match-result (pass the match input instead), a report, a duplicate or missing record id,
  a locator longer than its input schema allows, and an `observed_at` later than `--as-of`. Only locators and
  dates are copied, never a value, quote or contact channel.
- Details: `references/evidence-policy.md` §5.6 and `references/data-contract.md` §9.5.

### New: export leads to a spreadsheet, CRM or TradeWith (`scripts/export_leads.py`)

- `scripts/export_leads.py` writes the leads of one scored discovery-result as a file a person imports. It writes
  a file and nothing else: no network, no send, no score change. It is a file format, not a seventh adapter
  capability.
  - `--format csv` (default): a generic spreadsheet / CRM file for buyers or sellers, with fixed columns, the
    literals `unknown` / `none` / `not_applicable` / `withheld`, and a formula guard on every cell.
  - `--format tradewith-json`: the `{"buyers":[...]}` body of TradeWith's admin bulk-import endpoint. Buyers only.
  - `--format tradewith-csv`: the five-column file TradeWith's admin buyer-import page reads (`sourceId,
    companyName, country, website, industry`). Buyers only. It drops provenance and the matching fields, and
    says so on stderr.
- Filters: qualified records only by default, `--include-unqualified`, and `--min-score N`. Closed and unreachable
  companies are never exported, and `excluded[]` never is.
- TradeWith rows:
  - carry no quality tier label, so they land as **tier C** (unreviewed). An admin reviews them in TradeWith,
    promotes a row to A or B and adds its tags;
  - carry no contact field at all: no `contactName`, `contactEmail` or `contactPhone`, not even a company role
    mailbox;
  - carry no notes and no HS codes;
  - use `sourceId` = `kbtm:<canonical domain>`, so re-importing a later run updates the same row;
  - carry provenance and `stale=` in `originalSource`;
  - carry a LinkedIn organisation page only, never a member profile.
- The generic CSV keeps company-level channels only: a `corporate_email` survives only as a role mailbox on the
  company's own domain, and a LinkedIn member profile is withheld.
- Every exported text value passes a personal-data scan, and a hit refuses the whole export. The scan reads
  percent-decoded text and URL slugs (`%40`, `tel-…`), LinkedIn URLs are parsed rather than prefix-matched (so
  `/company/../in/<name>` is withheld), and a `phone` or `messenger` channel skips the phone check only when the
  whole value is a number or a WhatsApp link to one.
- The formula guard also catches a formula behind leading whitespace or in full-width form (`＝1+1`).
  `tradewith-csv` refuses such a value; `tradewith-json` keeps it and prints a WARNING.
- A match-result, or any other input that is not a scored discovery-result, is refused with exit 1. A seller run
  with a TradeWith format is exit 2.
- New schema `schemas/tradewith-bulk-buyers.schema.json` (`additionalProperties: false` forbids every contact
  field). The TradeWith output is always checked against it, even under `--no-validate`.
- Details: `references/data-contract.md` §9.6 and `references/compliance-notes.md` §3.3a.

### New: the scripts as MCP tools (`scripts/mcp_server.py`)

- An optional stdio MCP (JSON-RPC) tool server exposes eleven scripts as tools: `normalize_company`,
  `dedupe_companies`, `score_buyer`, `score_seller`, `score_match`, `validate_output`, `make_review_sheet`,
  `acceptance_report`, `diff_runs`, `stale_evidence` and `export_leads`. Each tool runs its script as a child
  process with a subset of that script's flags; for those flags the result is what the command line prints.
  The rest (`--config`, `--pretty` and others) stay CLI-only.
- `--root DIR` is required and must not be `/`, the home directory or a parent of it. Every read and write
  resolves inside it after realpath, so `../` and symlinks cannot lead out.
- `as_of` is required on every call and checked as a real date. The server never supplies a date.
- `output_path` must be a new `.json` or `.csv` file outside the skill package and not under a hidden directory.
  Nothing is overwritten. Results above `--max-inline-bytes` (default 32,768) must use `output_path`.
- Children run with `python3 -I`, without `TRADEWITH_*` variables and without the server's stdin. Nothing sends or
  fetches, and the internal-data adapter is not exposed.
- `score_match` is refused before the script runs unless it gets `input` / `input_path`, or both `rfq_path` and
  `sellers_path`. Refused calls are logged on stderr as `kbtm-mcp: <tool> refused`.
- No single message can stop the server: deep nesting, lone surrogates, oversized frames and a closed stderr are
  all answered or absorbed.
- Client setup for Claude Code, Codex and Cursor: `references/runtime-adapters.md` §5.6.

### New: plugin packaging and reproducible release archives

- **Claude Code plugin.** `.claude-plugin/marketplace.json` at the repository root makes this repository a plugin
  marketplace with one plugin, the package folder itself (`strict: false`, `skills: ["./"]`, so the package gains
  no file). Install with `/plugin marketplace add choihyeonmuk/kbeauty-trade-matchmaker`, then
  `/plugin install kbeauty-trade-matchmaker@kbeauty-trade-matchmaker`. The plugin also starts the MCP server
  (`mcpServers.kbtm`) with the project folder as its root. Don't also run `install.sh` in the same runtime: both
  copies would fire on the same requests.
- **ChatGPT and Codex plugin.** Each release now carries a second asset, `kbeauty-trade-matchmaker-plugin.zip`:
  a skills-only portable plugin (`plugin.json` from `packaging/openai/plugin.json`, plus
  `skills/kbeauty-trade-matchmaker/`). It is the way to reach ChatGPT on the web and mobile, once a workspace admin
  publishes it. It declares no MCP server, because that would make the plugin desktop-only.
- **Release builder.** `tools/build_release.py` (repository root, not shipped) builds both ZIPs from the HEAD
  commit, never from the working tree. It refuses while the package or a manifest has uncommitted changes or
  untracked files, and refuses a symlink, a plugin component inside the package, or a manifest version that
  disagrees with `skill_version`. Entry order, timestamps and permissions are fixed, so the same commit builds
  the same bytes. Maintainer checklist: `references/runtime-adapters.md` §5.5 g.
- `skill_version` is now also stated in both plugin manifests; the test suite and the builder check that they
  agree.

### Changed

- Every script now treats an impossible or malformed `--as-of` (`2026-13-01`, `garbage`) as a usage error, exit 2.
  `normalize_company.py` and `validate_output.py` now check the flag too. An empty `--as-of` is exit 2 in
  `diff_runs.py`, `stale_evidence.py`, `export_leads.py` and `acceptance_report.py`; the pipeline scripts still
  treat it as "not given".
- `validate_output.py` knows the new kinds `run-diff`, `recheck-queue` and `tradewith-bulk-buyers`, and
  `--schema-file` now validates a document whose kind can't be detected (it used to pass with only a warning). A
  document that only looks like a match-result now gets an error instead of a crash.
- `references/calibration-notes.md` was shipped in v0.2.0 but missing from the test suite's file manifest; it is
  now listed.

### Tests

252 → **536** cases. New phases for the run diff, the re-check queue, the export, the plugin manifests and
builder, and the MCP server, plus a review follow-up phase (R-01..R-17). An installed copy (no repository
manifests) reports two SKIPs.

### Docs

- The claude.ai install path is now also verified on a **free plan** (2026-09-19, a 3-company request ran end to
  end, scoring scripts included, without hitting a usage limit). READMEs and the project page say so.
- `SKILL.md` has new sections for comparing runs, the re-check queue, exporting leads and the tool server.
- `references/runtime-adapters.md` §5.5 (plugin packaging) and §5.6 (tool server) are new.

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
