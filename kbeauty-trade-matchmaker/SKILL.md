---
name: kbeauty-trade-matchmaker
description: Discovers, verifies, scores and matches K-Beauty trade counterparties in four modes — Buyer Discovery (K뷰티 바이어 발굴), Seller Discovery (한국 화장품 제조사·OEM/ODM 셀러 소싱), RFQ Matching (RFQ 매칭) and Outreach Draft (아웃리치 초안) — for requests about finding overseas K-Beauty buyers, distributors or importers, sourcing Korean cosmetics suppliers and OEM/ODM manufacturers, matching a buying request to sellers, or preparing evidence-backed outreach; evidence-first, ends at READY_FOR_REVIEW, never sends.
---

# K-Beauty Trade Matchmaker

Find overseas K-Beauty buyers and Korean sellers from public sources, verify every material claim against a
citable URL, score both sides with deterministic scripts, match a buying request (RFQ) to sellers, and prepare
outreach drafts that a human reviews before anything is sent.
*Korean gloss: K뷰티 바이어·셀러를 근거 기반으로 발굴·검증·점수화·매칭하고, 사람이 검토할 아웃리치 초안까지만 만든다.*

`skill_version 0.1.1` · `schema_version 0.1.0` · `score_version kbtm-score-0.1.0`. Runtime tool names live **only**
in `references/runtime-adapters.md`; this file names capabilities ("the runtime's web-search capability", "the
runtime's internal-data connector") so the folder runs unchanged in every runtime. `install.sh` installs it.

## The mandatory pipeline

**Evidence-first → Normalize → Dedupe → Score → Verify → Output.**
*Korean gloss: 근거 우선 → 정규화 → 중복 제거 → 점수화 → 검증 → 출력.* Never skip or reorder a stage, in any mode.

| Stage | What happens | Owner |
|---|---|---|
| **Evidence-first** | Each material claim gets `claim`, `value`, `source_url`, `source_type`, `observed_at`, `confidence`. No claim, no field: it stays `"unknown"` or absent. | agent · `references/evidence-policy.md` |
| **Normalize** | `canonical_domain`, `normalized_name`, `alias_domains`, canonical `website`, alpha-2 `country`, `moq_unit` precedence. Canonical category slugs and certification tokens are the **agent's** job — the script never touches `product_categories` or `certifications`; `_common.normalize_category` / `_common.normalize_certification` are the reference mappings, applied by the scorers at comparison time. | `scripts/normalize_company.py` |
| **Dedupe** | One company, one record: `www`/non-www, IDN and alias domains collapse; conflicts recorded, never silently resolved. | `scripts/dedupe_companies.py` |
| **Score** | Rubric scoring and hard filters, deterministically — never eyeballed. | `scripts/score_buyer.py`, `score_seller.py`, `score_match.py` |
| **Verify** | Schema plus contract invariants (INV-01…INV-37, evidence rules EVI-01…EVI-06, draft rules DRAFT-01…DRAFT-12). Exit 1 means not shippable. | `scripts/validate_output.py` |
| **Output** | Render exactly the blocks below; report `unknown` and `Missing:` honestly. | this file |

The one model-authored step — the semantic rerank and the rationale/risks narrative — is bounded, is
handed back to the script as a file, and can never resurrect a hard-filter failure.

## Core rules

1. **Evidence first.** Never invent MOQ, certifications, export markets, lead times, capacity, exclusivity or
   buyer intent. A number the source does not state does not exist.
2. **Prefer official sources.** Company site and official directories outrank third-party listings; store
   `source_url` and `observed_at` per material claim, and mark third-party facts as such.
3. **Unknown is not false.** `"unknown"` is never stored or rendered as `false`, `0`, `""`, `null`, `N/A` or a
   dropped line. Unknown is penalised, never rejected; a *verified* empty list is `none evidenced`.
4. **Deterministic scoring only.** Scores come from the scripts and every weight from
   `schemas/scoring.config.json`; never round, adjust or re-judge a score by hand.
5. **Normalize and dedupe before scoring.** Scoring un-deduplicated candidates is a contract violation.
6. **Evidence quality outranks count.** Requested counts are ceilings, never targets; returning fewer is
   correct, padding the list with unevidenced companies is not.
7. **Never auto-send.** No mode transmits a message. Outreach terminates at `READY_FOR_REVIEW` with
   `auto_send: false`; the skill never sets `APPROVED_FOR_OUTREACH` or any later state.
8. **No fabricated urgency.** "A buyer is currently looking for…" is allowed only when a cited `rfq_id`
   resolves to an RFQ whose `status` is `qualified`, `matching` or `proposal_open`, with that status and date
   stated. Otherwise it is forbidden, in any language.
9. **Never bypass access control.** No login, CAPTCHA, paywall or `robots.txt` bypass, no headless browser
   automation. A page that will not open is recorded `stale`, not worked around.
10. **No personal-contact harvesting.** Company-level channels only: no named individuals, guessed address
    patterns, personal mobiles or handles — in outputs, notes, quotes or fixtures.
11. **Degrade, never abort; keep no secrets.** A candidate that cannot be evaluated sets `partial: true` plus a
    note on the run envelope and the run still succeeds; credentials are read from the environment by the
    adapter at call time only, never written into a file, a log or an output document.

## Modes

| Trigger (either language) | Mode | Load first | Script |
|---|---|---|---|
| "find K-Beauty buyers / distributors / importers in `<market>`" · 바이어 발굴 | 1 · Buyer Discovery | `references/buyer-discovery.md` (§8.1 for the claim keys the scorer prices) | `score_buyer.py` |
| "find Korean manufacturers / OEM-ODM / suppliers for `<product>`" · 셀러 소싱 | 2 · Seller Discovery | `references/seller-discovery.md` | `score_seller.py` |
| "match RFQ #N to sellers" · "이 요청에 맞는 셀러" | 3 · RFQ Matching | `references/matching-rules.md` | `score_match.py` |
| "draft outreach to X" · 아웃리치 초안 | 4 · Outreach Draft | `references/outreach-guidelines.md` | `validate_output.py --schema outreach-draft` |

Pass `--as-of YYYY-MM-DD` (the operator's run date) on every command: it is the only time source, and
scripts never read a clock. `--help` prints each script's full flag set.

### Mode 1 — Buyer Discovery

- **Required:** `country` (or an explicitly global scope). **Optional:** `category` (default: all K-Beauty
  categories), `company_type`, `channels`, `count` (default 30, a ceiling), `threshold` (default fixed 70).
- **Steps:** build the query surface (`references/data-contract.md` §8 — expand a region word such as "GCC"
  into `region_countries` yourself; scripts never expand regions) → run the query-expansion matrix → open
  contact/wholesale/brands/partnership/about pages in that order → write one `raw` buyer record per company
  with evidence per claim → normalize → dedupe → score → validate → render.

```bash
python3 scripts/normalize_company.py --input candidates.buyers.json --entity buyer --envelope --as-of 2026-09-12 --output tmp.buyers.normalized.json
python3 scripts/dedupe_companies.py --input tmp.buyers.normalized.json --entity buyer --as-of 2026-09-12 --output tmp.buyers.deduped.json
python3 scripts/score_buyer.py --input tmp.buyers.deduped.json --query query-surface.json --as-of 2026-09-12 --top 30 --pretty --output buyers.scored.json
python3 scripts/validate_output.py --input buyers.scored.json --schema discovery-result --invariants
```

- **Output:** one `discovery-result` envelope (`summary`, `records`, `excluded`, `partial`, `notes`) rendered as
  the 10.1 block. A buyer run applies **no `HF-xx` rule** — every configured predicate reads `seller.*`
  or `rfq.*` — so the only two exclusions are a query `company_type` mismatch and the DISC-06
  no-evidenced-material-claim gate; category and country fit are priced by B-MR2 / B-MR1 / B-KF3
  instead. An excluded candidate leaves `records[]` for `excluded[]` and is never ranked.
- **Two states that are easy to confuse.** A candidate with evidence but **no official** item on a
  material claim is scored, ranked and rendered with ` — unverified` (PRD 15.1). A candidate with **no
  evidenced material claim at all** is excluded, with a reason naming the sub-case. Omitting `--query`
  is legal but drops `market_relevance` entirely and skips the `company_type` exclusion: the run sets
  `partial: true` and says so, and its scores are not comparable to a run that named a market.
- **Vertical fit gate (a third state, and not an exclusion).** When the query names the K-Beauty
  vertical, a candidate on which **no** positive Korean signal fired — no `korean_products_signal:
  true`, no named Korean brand, no Korea sourcing statement, no K-Beauty positioning or category page
  — is still scored, still ranked and still returned, but `qualified` is forced to **false** and a note
  says why (`scoring.config.json vertical_fit_gate`, PRD T09). Nothing is rejected and
  `never_hard_reject_on_unknown` is untouched; the gate only stops a high B2B score being reported as a
  *K-Beauty lead* when nothing on any page you read ties the company to Korean product. A run with no
  `vertical` on the query does not apply it. Read the score and the `Missing:` line together: the gate
  is telling you which page you still have to find, not that the company is worthless.

### Mode 2 — Seller Discovery

- **Required:** `product` (category or description). **Optional:** `oem_odm`, `private_label`, `max_moq` +
  `moq_unit` (default `units`), `certifications`, `export_markets`, `count` (default 30, a ceiling),
  `threshold` (default fixed 70).
- **Steps:** the same pipeline against Korean manufacturers, brands and OEM/ODM houses — normalize and dedupe
  exactly as in Mode 1 with `--entity seller`. MOQ compares on its `min` bound: a *confirmed* MOQ above the
  ceiling is rejected by `HF-03`, an unknown MOQ is penalised and kept. Certifications are never inferred.

```bash
python3 scripts/score_seller.py --input tmp.sellers.deduped.json --query query-surface.json --as-of 2026-09-12 --top 30 --pretty --output sellers.scored.json
python3 scripts/validate_output.py --input sellers.scored.json --schema discovery-result --invariants
```

- **Output:** one `discovery-result` envelope rendered as the 10.2 seller block — the 10.1 layout plus
  `MOQ:`, `Certifications:` and `Export markets:` lines.

### Mode 3 — RFQ Matching

- **Required:** one RFQ document (`schemas/rfq.schema.json`; load it through the internal-data connector or
  from a file) **and** seller candidates (internal list, Mode 2 output, or both merged). **Optional:** `top`
  (default 10, max 50), `threshold`, rerank and rationale inputs.
- **Steps:** project the RFQ onto the query surface → `hard_filter` → `weighted_score` → `semantic_rerank`
  (bounded; needs a rationale plus `evidence_ids`, cannot touch excluded sellers) → `unknown_handling`. Write
  the model's rerank and narrative passes to JSON files and hand them to the script; never edit the numbers.
- **Pass one envelope, not two files.** `--input` takes a document carrying `rfq`, `records` **and** the
  projected `query` block; `--rfq` + `--sellers` is the fallback for when no query surface exists. Only the
  envelope can carry `query.region_countries`, so the split form silently forfeits region credit on
  `market_fit` — the same RFQ scores lower through it. `tests/fixtures/match-134.input.json` is the shape.

```bash
python3 scripts/score_match.py --input match.134.input.json --as-of 2026-09-12 --top 10 --rationale-input rationale.134.json --rerank-input rerank.134.json --pretty --output match.134.json
python3 scripts/validate_output.py --input match.134.json --schema match-result --invariants
```

- **Output:** one `match-result` document rendered as the 10.3 block. `no_match` is always present: an
  empty result set is reported as `No qualified match` with a reason, the binding rules and relaxation
  suggestions — never as a silent empty list.

### Mode 4 — Outreach Draft

- **Required:** a scored target plus its evidence — a `score_buyer.py` / `score_seller.py` record with
  `qualified: true`, or a `score_match.py` candidate with `hard_filter.passed: true` and `qualified: true`.
  Scorers never write `status`; `QUALIFIED` / `MATCH_CANDIDATE` are set later by a human or the adapter, so
  they are not the gate here. **Optional:** a
  cited `rfq_id`, recipient `language` (default: the recipient's business language), `channel` (company-level
  only). Cap: 20 drafts per run.
- **Steps:** pick the template → personalize only with evidence-backed facts → list every fact with its source
  URL → fill the compliance block from `references/compliance-notes.md` §7 → stop. When an advertising label
  or opt-out is `yes`/`unknown`, add the jurisdiction-and-channel notice block the template names, verbatim;
  when none is on file, print the literal line `- No notice block on file for {{country_name}} /
  {{channel_type}} — obtain wording before sending`.
- **Command:** render `templates/buyer_outreach.md` or `templates/seller_outreach.md`, then validate the draft —
  exit 1 means it does not go to review:

```bash
python3 scripts/validate_output.py --input draft.md --schema outreach-draft --record buyers.scored.json --strict
```

  `--record` is the scored envelope, record or match result the draft was built from (repeat it with the RFQ
  document when the draft cites one); rule ids are listed in `references/output-format.md` §10.4.1.

- **Output:** the 10.4 envelope (`references/output-format.md`) whose 2nd and 3rd lines are literally `Status: READY_FOR_REVIEW` and
  `Auto-send: false`, ending with the reviewer checklist and `Next action: human review, then
  APPROVED_FOR_OUTREACH in the application layer`.

## Load this file when…

| File | Load it when |
|---|---|
| `references/buyer-discovery.md` | Running Mode 1: query matrix, page order, drift logging, stop conditions |
| `references/seller-discovery.md` | Running Mode 2: Korean sources, OEM/ODM vs brand, MOQ and certification pages |
| `references/matching-rules.md` | Running Mode 3, or explaining a hard filter (`HF-00`…`HF-08`), the rerank or a no-match |
| `references/qualification-rubric.md` | Asked *why* a score is what it is, or which evidence earns which dimension |
| `references/calibration-notes.md` | Asked whether a score is *validated*: what the two live trials measured, what changed in response, what is deferred to calibration |
| `references/evidence-policy.md` | Deciding fact vs. inference vs. unknown, source tier, staleness, conflicting sources |
| `references/outreach-guidelines.md` | Running Mode 4, or asked what a draft may and may not claim |
| `references/compliance-notes.md` | Filling the compliance block, or asked about robots/ToS, data minimization, marketing rules |
| `references/data-contract.md` | Writing a record by hand: field names, unknown semantics, query surface, envelopes, states |
| `references/output-format.md` | Rendering any result block: 10.1 buyer / 10.2 seller discovery, 10.3 match, 10.4 outreach envelope, 10.5 shared line rules, Korean label map |
| `references/runtime-adapters.md` | Binding a capability to this runtime's actual tool, or a capability is missing |
| `schemas/*.json` | Binding shapes for `buyer`, `seller`, `rfq`, `evidence`, `match-result`, `discovery-result`, `outreach-draft`, every number in `scoring.config.json`, and the jurisdiction × channel lookup in `compliance.config.json` |
| `scripts/*.py` | Running the pipeline (`scripts/_common.py` is the shared library the six CLI scripts import) |
| `templates/buyer_outreach.md`, `templates/seller_outreach.md` | Rendering a draft; the seller template has RFQ-present and RFQ-absent variants |
| `templates/legal_notices.md` | Appending the `Required legal notices` block to a draft: the per-jurisdiction x per-channel wording, copied verbatim, keyed `{{country_alpha2}}.{{channel_type}}` (R10.4.6) |
| `adapters/tradewith_adapter.md`, `adapters/tradewith_adapter.py` | Reading an RFQ or internal sellers, or queueing leads / matches / drafts for review |
| `tests/cases.md`, `tests/run_tests.py`, `tests/fixtures/` | Checking expected behaviour on cases T01–T10, or self-testing the package |

## Output formats

**`references/output-format.md` is the rendering contract** — 10.1 buyer and 10.2 seller discovery, 10.3 match, the
10.4 outreach envelope, the 10.5 shared line rules and the optional Korean label map (요약 / 상위 후보 /
매칭 결과 / 제외 / 근거 / 출처 / 확인 필요). Load it before rendering any result block.

Everything outside `{{…}}` is literal: headings on their own line, one blank line between blocks,
detail lines indented exactly three spaces, ` — ` an em dash, unknowns the lowercase word `unknown`.

- **Discovery (10.1 / 10.2)** renders `Summary` → `Top Candidates` → `Excluded / low fit` → `Recommended
  next action`. Every candidate carries `Website:`, `Country:`, `Type:`, `Why:` (≥ 2 reasons),
  `Contact:`, `Evidence:` and `Missing:`; a seller run adds `MOQ:`, `Certifications:` and
  `Export markets:` after `Why:`. `Excluded / low fit` is omitted only when there is nothing to list.
- **Match (10.3)** renders the RFQ header (one `{{required_cert}}: Required` line per required
  certification, in the RFQ's order, dropped when the list is empty) → `Matches` → `Excluded` → the
  one-line `Summary:`. When `no_match.is_no_match` is true the `Matches` block is replaced by
  `No qualified match` with `Reason:`, `Binding rules:` and a `Try:` list; `Excluded` and `Summary:`
  still render.

## Self-check before returning results

Run every line; a `no` is a blocker, not a caveat.

1. `python3 scripts/validate_output.py --input <document>.json --invariants --strict` exits `0` for every document produced. Exit `1` means fix the data, not the wording.
2. **Evidence coverage:** every material claim on every rendered candidate has ≥ 1 evidence item with a click-ready `source_url`, or the value is `"unknown"`; no dangling `evidence_id`.
3. **Unknown is not false:** nothing unknown rendered as `false`, `0`, `N/A`, blank or a dropped line; verified-empty shows `none evidenced` / `none published`.
4. **Scores are script output:** rendered numbers match the JSON exactly, `score_version` and `as_of` are stated, nothing was re-judged by hand.
5. **Required lines present:** score, type, a `Why:` with ≥ 2 evidenced reasons, a contact channel, `Evidence:` and `Missing:` on every candidate — plus `Website:` and `Country:` in discovery.
6. **Empty means explicit:** zero qualified is `No qualified match` (match) or a widened-query next action (discovery), never a silent empty list.
7. **No fabricated urgency:** no live-demand claim without a cited `rfq_id` in `qualified` / `matching` / `proposal_open`, with its status and date stated.
8. **Outreach stops at review:** `Status: READY_FOR_REVIEW`, `Auto-send: false`, every personalization fact listed with its source URL, nothing sent, no later state set.
9. **No personal data:** no named individual, personal address, direct dial or handle anywhere, notes and quotes included.
10. **Shortfalls stated:** `partial`, category drift, stale or conflicting sources and "returned n of N requested" appear in the output rather than being quietly absorbed.

## When NOT to use this skill

- **Sending anything** — no email, message or campaign; the package has no send capability and must not be paired with one.
- **Contact-list building** — harvesting personal emails, phone numbers or profiles.
- **Legal, customs, tax or regulatory rulings** — this skill flags where review is needed; registration, labelling and import approval go to qualified counsel.
- **Non-K-Beauty verticals** (food, medical devices, electronics) in v0.1.1 — rubric, categories, certifications and query matrices are cosmetics-specific; say so rather than scoring out of scope.
- **Trade execution** — contracts, payment, escrow, logistics, customs filing.
- **A generic company lookup or one known URL** — this is a multi-stage sourcing pipeline, not a fact check.
- **Scoring without evidence** — if no source can be opened, report that the evidence bar was not met instead of producing numbers anyway.
