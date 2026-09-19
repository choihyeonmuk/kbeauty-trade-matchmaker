# K-Beauty Trade Matchmaker

**An Agent Skill that finds overseas K-Beauty buyers and Korean sellers on the public web, verifies every material claim against a source you can click, scores both sides with deterministic scripts, matches buying requests to sellers, and stops at an outreach draft for a human to review.**

The same folder runs unmodified in **Claude Code** and **OpenAI Codex**. Python is **standard library only** (3.9–3.14); there is nothing to `pip install`.

[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [Project page](https://kbeauty.tradewith.kr/) · [Contact on LinkedIn](https://www.linkedin.com/in/hm-choi)

![Find the right K-Beauty trade partner](kbeauty-trade-partner-linkedin-cities.png)

> A visual overview of the workflow: discover, verify, match, and keep a human in the loop.

> **Status: v0.2.0.** The pipeline is tested against 252 cases on fictional fixtures and was trialled once against the live web. The scoring rubric is **not yet validated against real outcomes**: scores are reproducible and traceable, not yet known to be predictive. v0.2.0 adds the tooling to measure that ([Validating the scores](#validating-the-scores)), but no labelled sample exists yet. RFQ Matching and Outreach Draft have not been run on live data. Read [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) before trusting a score.

---

## What's new

**v0.2.0 (2026-09-19).** No existing score changes.

- **Score-validation tooling.** A blind review sheet for a trade operator, and a report that compares their accept/reject calls with the scores. See [Validating the scores](#validating-the-scores).
- **Market packs for India, Indonesia and Türkiye.** Local-language buyer search, market-entry rules used in matching (CDSCO, BPOM, Indonesia's mandatory halal certification, TİTCK), direct-marketing checklist rows and notice blocks, and company-name handling for `Pvt Ltd`, `PT`, `A.Ş.` and similar forms. See [Market packs](#market-packs).
- Tests: 179 → 252 cases.

Full notes for every release: [CHANGELOG.md](CHANGELOG.md).

---

## Safety boundary: this skill sends nothing

The most important fact about this package: **no code in it can send a message.**

- **Draft only.** Outreach work always ends in state `READY_FOR_REVIEW`, with `auto_send: false` and `manual_approval_required: true` on the draft.
- **Humans approve, external systems send.** The skill may move a lead through `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` and no further. `APPROVED_FOR_OUTREACH` and later belong to a CRM and a person; the adapter **refuses** those transitions.
- **No SMTP, Gmail, SES or webhook sending code.** No inferring or bulk-collecting personal emails or phone numbers (company-level channels only). No bypassing CAPTCHA, logins, paywalls, robots.txt or terms of service.
- **Nothing is invented.** MOQ, certifications, export markets, exclusivity and capacity are recorded only when a source states them; otherwise they stay `"unknown"`. Unbacked urgency such as "a buyer is waiting for you" is banned at the template level.
- **No legal judgements.** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) marks where a check is needed; it never concludes that sending is allowed.

This is a design decision, not a missing feature. *Drafting* and *sending* are different permissions, and this package only holds the first.

---

## What it does

It turns a day of searching Google, LinkedIn and trade-show directories into **a verifiable shortlist where every claim links to its source**.

1. **Discover** buyer and seller candidates on the public web by country, category, OEM/ODM, MOQ and certification.
2. **Verify** each claim against trustworthy sources, usually the company's own site, and store it with its URL and the time it was observed.
3. **Normalize** domains and company names, merge duplicates, and put buyer requirements and seller capabilities on one schema.
4. **Score** buyers and sellers with deterministic Python scripts. Same input and same `--as-of` give the same output.
5. **Match** a buying request (RFQ) to sellers: hard filters, then weighted score, then a bounded semantic rerank, then unknown handling. It returns the top N **and the reason each rejected seller was rejected**.
6. **Draft** personalised outreach from verified facts only, and stop there.

---

## Four modes

Ask the agent in plain language. The forms below are shorthand for the parameters each mode takes.

### Buyer Discovery

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

Finds distributors, importers and wholesalers that carry K-Beauty in the UAE, and normalises company type, brands carried, wholesale availability, partnership signals, official contact channels and evidence URLs.

### Seller Discovery

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

Finds Korean manufacturers and brands, and verifies OEM/ODM capability, MOQ, certifications, main products and export or partnership signals.

### RFQ Matching

```
/kbeauty-match rfq="#134" top=10
```

Filters sellers against the RFQ's product, MOQ, destination, certifications and private-label requirement, combines quantitative and qualitative scores, and returns the top 10 plus the exclusion reasons. "No qualified match", with its reasons, is a normal output.

### Outreach Draft

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

Writes a subject, body and personalisation points from verified facts only. Weak evidence is generalised or marked "needs verification". **It does not send.**

---

## Example output

An excerpt of RFQ Matching on the bundled test fixture. The sellers are fictional.

```
RFQ #134
Destination: United Arab Emirates
Product: Sunscreen
MOQ: <= 3,000
Commercial model: Private label
ISO22716: Required

Matches
1. Hansol ODM Corp — Match 99/100
   Product Fit: 100
   Model Fit: 98
   MOQ: 95
   Compliance: 100
   Market Fit: 100
   Evidence Quality: 100
   Risks: none
   Missing: none
...
9. Yeonhwa Lab Co., Ltd. — Match 79/100
   MOQ: 43
   Risks: seller.moq not published; hard filter HF-03 skipped.
   Missing: MOQ against the buyer ceiling; Capacity against the order quantity; Destination-market registration

Excluded
- Daehan Sun Care Co., Ltd.: MOQ minimum 5,000 exceeds RFQ max 3,000
- Haneul Bio Co., Ltd.: Required certification ISO22716 not held (verified list: none)
- Jinheung Cosmetics Co., Ltd.: Does not supply AE (market excluded by the seller)

Summary: 20 considered · 11 passed hard filter · 10 returned · 11 qualified
```

Note seller 9: its MOQ is unpublished, so the MOQ hard filter was **skipped, not failed**. It stays in the list with a lower operational score and an explicit "Missing" line, instead of being silently rejected or silently passed.

---

## Package layout

```
kbeauty-trade-matchmaker/
├── SKILL.md                      # Runtime entry point: frontmatter (name/description), four modes, workflow
├── install.sh                    # Installs into either runtime's skills directory (symlink by default)
├── adapters/
│   ├── tradewith_adapter.py      # Internal-data boundary. File and HTTP backends; imports without credentials
│   └── tradewith_adapter.md      # Adapter setup, usage, and what it refuses to do
├── references/                   # Progressive disclosure: loaded only when a mode needs them
│   ├── buyer-discovery.md        # Buyer query expansion, per-country search patterns, stop conditions
│   ├── seller-discovery.md       # Korean seller queries, OEM/ODM and MOQ page patterns, directories
│   ├── qualification-rubric.md   # Narrative explanation of the scoring dimensions
│   ├── matching-rules.md         # Hard filters, weighted score, rerank, unknown handling; HF-00..HF-08
│   ├── calibration-notes.md      # What the live trials measured and changed, and what is unvalidated
│   ├── evidence-policy.md        # Fact vs inference vs unknown, source tiers, observed_at, conflicts
│   ├── outreach-guidelines.md    # Draft-only rules, evidence-backed personalisation, banned phrasing
│   ├── compliance-notes.md       # Per-jurisdiction direct-marketing cautions, data minimisation
│   ├── data-contract.md          # Bundled summary of the schema and normalisation contract
│   ├── output-format.md          # Output rendering contract, bundled
│   └── runtime-adapters.md       # Portability layer; the only file that names vendor tools
├── schemas/                      # Data contracts, checked by the bundled validator
│   ├── buyer.schema.json
│   ├── seller.schema.json
│   ├── rfq.schema.json
│   ├── evidence.schema.json      # The smallest unit: one source, one claim
│   ├── match-result.schema.json  # One complete matching run
│   ├── discovery-result.schema.json
│   ├── acceptance-report.schema.json  # One calibration measurement (v0.2.0)
│   └── scoring.config.json       # Every weight, threshold and penalty lives in this one file
├── scripts/                      # Standard library only. No network, no credentials
│   ├── _common.py                # Config, rounding, normalisation, tri-state helpers, schema validator
│   ├── normalize_company.py
│   ├── dedupe_companies.py
│   ├── score_buyer.py
│   ├── score_seller.py
│   ├── score_match.py
│   ├── validate_output.py        # Schema plus contract invariants
│   ├── make_review_sheet.py      # Blind CSV review sheet for a trade operator (v0.2.0)
│   └── acceptance_report.py      # Review sheets + scored runs -> Human Acceptance Rate report (v0.2.0)
├── templates/
│   ├── buyer_outreach.md
│   ├── seller_outreach.md        # With and without an RFQ
│   └── legal_notices.md          # Per-jurisdiction, per-channel notice blocks
└── tests/
    ├── cases.md                  # Test case specification, including negative and edge cases
    ├── run_tests.py
    └── fixtures/                 # Golden inputs and expected outputs; all companies fictional
```

Only the `kbeauty-trade-matchmaker/` folder is installed. The contract content the runtime needs is bundled in `references/data-contract.md` and `references/output-format.md`.

---

## How scoring works

Scores come from **deterministic scripts**, not from a model's impression.

Buyers are scored on six dimensions: `kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10. Sellers are scored on six: `product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10. Each dimension sums criterion-level points and normalises to 0–100 over the points that apply.

Matching runs four stages in order:

1. **Hard filters** `HF-01..HF-08`, plus the rendering gate `HF-00`. Failures don't short-circuit; **every** exclusion reason is collected.
2. **Weighted score:** `product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10. These are the seller's own six dimensions, re-scored against the RFQ, so a seller is never judged by two different rubrics.
3. **Bounded semantic rerank.** The one model-authored step. It is capped, needs evidence-cited reasons, and can never resurrect a hard-filter failure.
4. **Unknown handling.**

Three rules matter most:

- **Unknown is not zero.** An unknown value earns 30% of that criterion's maximum (`neutral_base` 50 × `penalty_factor` 0.6). Rejecting a candidate merely because a value is unknown is forbidden, and so is guessing a value to avoid the penalty.
- **No clock reads.** All time arithmetic uses `--as-of`, so the same input gives the same numbers whenever you run it.
- **Confidence is not quality.** Confidence answers "how far can this record be trusted", is shown as `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW`, and never reads the qualification score.

A buyer with no K-Beauty evidence at all is still scored and ranked but can never be marked qualified, however strong its other dimensions are. The default qualification threshold is a fixed 70; a percentile mode is also implemented. Every tunable number is in `schemas/scoring.config.json`.

## How evidence works

The unit of output is not "a company" but **a claim with evidence**. An evidence item holds one `claim`, its source URL, the source tier, the content date (`source_date`), when it was observed (`observed_at`), whether it is fact or inference, and a short quote.

| Tier | Source | Points |
|---|---|---|
| 1 | The company's official site | 100 |
| 2 | Official trade-show, association, government or trade-agency directories; internal records | 82 |
| 3 | Official LinkedIn and social profiles | 64 |
| 4 | Reputable third-party directories and press releases | 46 |
| 5 | Community posts and blogs (supporting signal only) | 20 |

Points are multiplied by content age: within 90 days 1.00, within a year 0.92, within two years 0.80, older 0.60, undated 0.85. Age is always measured from `source_date`, never `observed_at`, because when we read a page says nothing about how old its content is.

Two states are deliberately distinct:

- **`unverified`**: the record has evidence, but no material claim has an official (tier 1) source. It is scored, ranked and shown with an `— unverified` marker. It is **not** excluded.
- **No evidence at all** on any material claim: the record is moved to `excluded[]` before scoring, with the reason stated ("site could not be opened" or "read, but no material claims"). Padding a ranked list with evidence-free companies is exactly the failure this prevents.

Storage is minimal: the claim, URL, observation time and a short quote; never whole pages or unnecessary personal profiles. Conflicting sources are recorded in `conflicts[]` for a human to see, never silently resolved, and conflicts lower confidence.

---

## Validating the scores

Scores here are reproducible, but nobody has yet checked them against a person's judgement. Two standalone scripts, added in v0.2.0, let you run that check. They change no score.

```bash
cd kbeauty-trade-matchmaker

# 1. Make a blind sheet from a scored run. No score, rank or qualified flag; rows in a fixed shuffled order.
python3 scripts/make_review_sheet.py --input out/buyers.scored.json --include-excluded --output out/review.csv

# 2. A trade operator fills in verdict (accept / reject / unsure), a reason_code for each reject,
#    their role, and the date. Then:
python3 scripts/acceptance_report.py --scored out/buyers.scored.json --reviews out/review.csv --as-of 2026-09-19 --pretty
```

The report gives the share of reviewed companies the operator accepted, overall and split by the `qualified` flag, score band, country and reject reason. It shows what precision and recall each threshold from 50 to 90 would have given. For the total score and each of the six dimensions, it shows how well the score separates accepted from rejected companies and how many distinct values the dimension produced. It lists excluded companies the operator would have accepted.

Below 30 decided reviews the report marks itself `insufficient_sample` and says it can't justify a weight or threshold change. It refuses sheets with unknown verdicts, rejects without a reason, conflicting duplicates, runs scored under different `score_version`s, and notes that contain an email address or phone number. Reviewers are identified by role, never by name.

[`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) §7 describes how to build the sample (at least two countries and two categories, 100 to 200 reviewed companies) and what result would settle each open calibration question. This measures Human Acceptance Rate only. Whether a contacted lead becomes an RFQ needs outcome data from the application layer.

## Market packs

v0.2.0 adds India, Indonesia and Türkiye. No scoring rule changed; a new test fixture (destination Indonesia, halal required) shows the existing rubric already handles them.

| | India | Indonesia | Türkiye |
|---|---|---|---|
| Buyer search language | English first, plus Hindi | Indonesian | Turkish |
| Market-entry rule used in matching | CDSCO import registration | BPOM notification; mandatory halal certification for cosmetics (date-dependent on `--as-of`) | TİTCK notification through ÜTS |
| Legal forms stripped from company names | `Pvt Ltd`, `Private Limited`, `LLP` | `PT`, `CV`, `Tbk` | `A.Ş.`, `Ltd. Şti.`, `San. ve Tic.` |
| Notice blocks | `IN.corporate_email`, `IN.partnership_form` | `ID.corporate_email`, `ID.partnership_form` | `TR.corporate_email`, `TR.partnership_form` |

- Registrations are recorded per market in `regulatory_registrations` and scored by the existing destination-market criterion: registered beats in progress, which beats not published.
- `Helal`, `Sertifikat Halal` and `हलाल` become the `HALAL` token, with the certifying body and its scope in the notes. Recognition in Indonesia is per body and per scope, so a certificate recognised for food doesn't cover cosmetics.
- The agent never adds a required certification the RFQ didn't state. It raises it as a risk for a person to decide.
- Sources not yet exercised in a live run are marked "not yet field-tested" in [`buyer-discovery.md`](kbeauty-trade-matchmaker/references/buyer-discovery.md). The compliance rows list what a person must check; they are not legal conclusions.

---

## Installation

### claude.ai, no terminal

1. Download [`kbeauty-trade-matchmaker.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker.zip) from the latest release. Keep it zipped.
2. In claude.ai, open Settings → Capabilities and make sure code execution is on. Then open Customize → Skills, choose Upload skill, drop in the ZIP and save.
3. Turn on web search in a new chat and ask in plain language, for example "Find 5 K-Beauty distributors in the UAE that carry sunscreen." Five companies take about 10 to 15 minutes.

Verified end to end on a paid plan on 2026-09-14: the skill was invoked without being named, ran web search and page fetches inside the skill, and executed the scoring scripts in the sandbox. Also verified on a free plan on 2026-09-19 with a small request (3 companies): the skill ran end to end, scoring scripts included, without hitting a usage limit. A larger request may still run into the free plan's limits. Korean step-by-step guide: [https://kbeauty.tradewith.kr/install-ko](https://kbeauty.tradewith.kr/install-ko). Stuck? [Message me on LinkedIn](https://www.linkedin.com/in/hm-choi).

### Claude Code and Codex installer

`install.sh` is POSIX `sh`, uses no network and no sudo, and writes nothing outside the target directory. **Run `--dry-run` first.**

```bash
git clone https://github.com/choihyeonmuk/kbeauty-trade-matchmaker.git
cd kbeauty-trade-matchmaker

sh kbeauty-trade-matchmaker/install.sh --dry-run          # Print the plan, change nothing
sh kbeauty-trade-matchmaker/install.sh --verify           # Claude Code: symlink into ~/.claude/skills/, then run the tests
sh kbeauty-trade-matchmaker/install.sh --runtime codex    # Codex: symlink into ~/.agents/skills/
```

| Option | Meaning |
|---|---|
| *(default)* | **Symlink** to `$HOME/.claude/skills/kbeauty-trade-matchmaker`; edits to the source apply immediately |
| `--runtime claude\|codex` | Choose the runtime. `claude` (default) uses `.claude/skills/`, `codex` uses `.agents/skills/`. `--codex` is short for `--runtime codex` |
| `--copy` | Install an independent copy instead of a symlink |
| `--project DIR` | Install under `DIR` instead of `$HOME`, so the skill travels with that repository |
| `--force` | Overwrite an existing directory this script did not create (refused by default) |
| `--verify` | Run `tests/run_tests.py` after installing; exit 1 on failure |
| `--dry-run` | Print the plan only |

Re-running is safe. Exit codes: `0` success, `1` installation refused or verification failed, `2` usage error.

### Claude Code and claude.ai

```bash
sh kbeauty-trade-matchmaker/install.sh                                  # Personal: every project on this machine
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project  # Project scope
```

- **The folder must be named `kbeauty-trade-matchmaker`**, matching the `name` in `SKILL.md`.
- **`SKILL.md` is already fully conformant; adding frontmatter keys is a regression, not an improvement.** The [Agent Skills open standard](https://agentskills.io/specification) recognises exactly six fields: `name` and `description` (required), and optional `license`, `compatibility`, `metadata` and experimental `allowed-tools`. Outside Claude Code (claude.ai, the Skills API), only those six are accepted, so one Claude-Code-only key blocks upload. This package carries only the two required keys. Versions live in the `SKILL.md` body.
- Surfaces don't sync. Claude Code (filesystem), claude.ai (zip upload in Customize → Skills) and the Skills API (`/v1/skills`) each need the folder uploaded separately.

### OpenAI Codex and ChatGPT

The folder moves over unchanged, but Codex does not read `.claude/skills`. Its skill roots are `.agents/skills`.

```bash
sh kbeauty-trade-matchmaker/install.sh --runtime codex                                # User scope: ~/.agents/skills/
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo   # Repository scope
```

- Codex scans `.agents/skills` in every directory from the working directory up to the repository root. `~/.codex/skills` is deprecated but still supported; use `~/.agents/skills` for new installs.
- Invoke explicitly with `$kbeauty-trade-matchmaker` or `/skills` in the CLI and IDE extension, or `@` in ChatGPT. Implicit invocation is decided by the `description`.
- A standalone skill folder is visible in the **ChatGPT desktop app, Codex CLI and IDE extension** only. ChatGPT **web and mobile** need the skill packaged as a plugin, which v0.2.0 does not include.
- `AGENTS.md` is not a way to install skills. It is a separate Codex feature for always-on repository instructions.

> **Checked against official documentation on 2026-09-13.** If anything here disagrees with the current docs, the docs are right.
> [Agent Skills specification](https://agentskills.io/specification) · [Anthropic Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [Claude Code skills](https://code.claude.com/docs/en/skills) · [OpenAI build skills](https://learn.chatgpt.com/docs/build-skills)
>
> The ChatGPT workspace upload procedure, and whether ChatGPT surfaces other than desktop can execute the bundled `python3` scripts, could **not** be verified; the relevant OpenAI help pages returned HTTP 403 to automated requests. If scripts cannot run, the skill falls back to evidence without scores (see `references/runtime-adapters.md` §4).

### Check the installation

Two things can break independently. These three lines check **the code only**:

```bash
python3 kbeauty-trade-matchmaker/scripts/validate_output.py --version
python3 kbeauty-trade-matchmaker/adapters/tradewith_adapter.py --version
python3 kbeauty-trade-matchmaker/tests/run_tests.py
```

To check that **the runtime sees the skill**, look for `kbeauty-trade-matchmaker` in Claude Code's skill list, or in Codex's `/skills` list. If it is missing, check that the folder sits directly under the skills root, that it is named `kbeauty-trade-matchmaker`, that the first line of `SKILL.md` is exactly `---`, and that no higher-priority scope has a skill with the same name. Restart Codex after changes.

---

## Running the tests

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py       # Exit 0 when everything passes, 1 otherwise
python3 tests/run_tests.py -v    # One line per case, not just failures
```

The runner uses only the standard library, finds fixtures relative to itself, passes `--as-of 2026-09-12` to every script, and compares output with the expected fixtures **byte for byte**. Before the fixture cases it checks the schemas themselves: that they parse, that every `$ref` resolves, that no unsupported keyword is used, that shared `$defs` agree across files, and that embedded versions match `scoring.config.json`.

Scripts can also be run directly. JSON goes to `stdout` and every diagnostic to `stderr`, so pipes are safe.

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py --input tests/fixtures/match-134.input.json --as-of 2026-09-12 --pretty
```

---

## Adapter quickstart: files only, no backend

The whole workflow runs today **without any internal API**. The adapter has one interface and two backends, `file` and `http`, and `file` is the default. It needs no credentials, network or service.

```bash
cd kbeauty-trade-matchmaker
export TRADEWITH_DATA_DIR=./tradewith-data     # Created on first write

python3 adapters/tradewith_adapter.py get-rfq --id 134 --pretty
python3 adapters/tradewith_adapter.py list-sellers --country KR --limit 20
python3 adapters/tradewith_adapter.py save-leads --input out/buyers.scored.json
python3 adapters/tradewith_adapter.py save-matches --input out/match-134.json
python3 adapters/tradewith_adapter.py save-outreach-drafts --input out/drafts.json   # Queues for review; does not send
python3 adapters/tradewith_adapter.py update-lead-status --id BUY-gulfbeauty-example-com --status VERIFIED
```

The data directory is a plain JSON file tree where **the file name is the id**, so re-running with the same input overwrites the same file and results stay byte-stable. Keep it outside the package, and outside version control if it holds real company data; `.gitignore` already excludes `tradewith-data/`.

When an API exists, one flag changes. Scores, schemas and output don't.

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # Environment only; a command-line token lands in shell history
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

Environment variables are read at call time, not import time. Nothing touches the network unless `--backend http` is named, there is no silent fallback from API to files, and the token never appears in errors, warnings or stored documents. Details are in `adapters/tradewith_adapter.md`.

---

## Open decisions

Six questions remain open. Each has a default in this implementation, and every default can be changed in one place.

| # | Question | Default here | Where to change it |
|---|---|---|---|
| 1 | Does an internal seller/RFQ API exist, or start with files? | **Both.** File backend by default; `--backend http` when an API exists | `TRADEWITH_BACKEND` / `--backend` |
| 2 | Save leads straight to the service database, or to staging? | **Staging.** The skill writes only to `POST /research/leads` (or `<data-dir>/leads/`); promotion is an application-layer approval | Adapter write target |
| 3 | Fixed threshold of 70, or per-category percentile? | **Fixed 70.** Percentile mode is implemented; the mode used is always recorded in `summary` | `thresholds` in `scoring.config.json`, `--threshold`, `--threshold-mode` |
| 4 | How far to store role addresses (`info@`, `sales@`) and named contacts? | **Company-level channels only.** No personal contact inference or collection exists in code | `evidence-policy.md`, `compliance-notes.md` |
| 5 | Crawling scope and terms-of-service checks for directories? | **Respect robots and ToS, bypass nothing.** Blocked access stays `"unknown"` | `compliance-notes.md`, discovery source lists |
| 6 | What can seller outreach say when there is no RFQ? | **A separate no-RFQ template.** Implying demand that doesn't exist is banned | `templates/seller_outreach.md`, `outreach-guidelines.md` |

---

## Versions

| Version | Value | Describes | Lives in |
|---|---|---|---|
| `skill_version` | `0.2.0` | The package: prompts, references, scripts, templates, tests | `SKILL.md` body, `match-result.skill_version` |
| `schema_version` | `0.1.0` | The shape contract: field names, enums, required lists | Every document, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | The rubric: weights, criteria, signals, penalties, thresholds, hard filters | `scoring.config.json`, every scored document |

```bash
python3 scripts/validate_output.py --version
```

When the rubric changes, stored scores are stale by definition. Because raw records keep their evidence, query surface and `as_of`, scores can be recomputed without re-crawling. Comparing or ranking results from different `score_version`s in one list is forbidden, and `validate_output.py` catches it.

---

## Requirements

- `python3` 3.9–3.14, standard library only.
- A POSIX shell for the installer.
- Discovery modes need the runtime's web search and page fetching. The scripts are fully offline, so tests and re-scoring run without a network.
- The internal-data integration is optional; the adapter's `file` backend needs nothing.

---

## Data handling

This repository contains no real company data or personal information. Fixtures use fictional companies on `.example` domains. When you start adding operational data, keep `tradewith-data/`, `.env` and `out/` ignored as `.gitignore` already does, and check periodically that what you store stays at claim, URL, observation time and short quote.

**This is not legal advice.** Checking direct-marketing rules in each jurisdiction is a human's job; this package marks where that check is needed.
