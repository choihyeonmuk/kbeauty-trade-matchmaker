# tests/cases.md — golden case book

> **Every company, domain, brand, person-free contact channel and evidence URL in
> `tests/fixtures/` is invented.** No fixture asserts anything about a real business. Every host
> sits under the reserved `.example` TLD of RFC 2606 (`gulfglow.example`,
> `hanbitcos.example`, `koreatradeagency.example`, …), brand names are placeholders
> (`Brand A` … `Brand AG`), and `run_tests.py` fails the run if any fixture host stops ending in
> `.example`. Korean gloss: 픽스처의 모든 회사·도메인·브랜드는 가공의 예시이며, 실제 기업에
> 대한 사실을 주장하지 않는다.
>
> The fixtures also carry **no personal data**: only the company-level channel types of
> BUILD-CONTRACT 3.6 appear, no named individual appears in any `notes[]`, `quote_or_summary`,
> `rationale` or `risks` entry, and the harness greps for pattern-guessed addresses
> (`first.last@…`) and fails if one shows up (INV-31).

| | |
|---|---|
| Harness | `tests/run_tests.py` (stdlib only, python3.9 – 3.14) |
| Run from the package root | `python3 tests/run_tests.py` |
| Run from inside `tests/` | `cd tests && python3 run_tests.py` |
| Useful flags | `-v` prints every case; `--allow-missing-scripts` downgrades script-dependent cases to SKIP while `scripts/` is still being written |
| Exit code | `0` when every case passes, `1` otherwise |
| `--as-of` passed to every script | `2026-09-12` (BUILD-CONTRACT 13.1) |
| Threshold used everywhere | `fixed 70` (`scoring.config.json thresholds`) |
| `score_version` | `kbtm-score-0.1.0` |

---

## 1. Fixture inventory

BUILD-CONTRACT 13.2 sketches a fixture naming scheme; this package uses the names below, which
carry the same content. The mapping is:

| BUILD-CONTRACT 13.2 name | File in this package |
|---|---|
| `buyers.raw.json` | `fixtures/buyers.golden.json` (envelope: `{schema_version, as_of, entity, records[]}`) |
| `sellers.raw.json` | `fixtures/sellers.golden.json` |
| `rfq-134.json` | `fixtures/rfq.134.json` |
| `buyers.scored.expected.json` | `fixtures/expected/buyers.uae.discovery.expected.json`, `…/buyers.uk.discovery.expected.json` |
| `sellers.scored.expected.json` | `fixtures/expected/sellers.discovery.expected.json` |
| `match-134.expected.json` | `fixtures/expected/match-134.expected.json` |
| `query-buyer-*.json`, `query-seller-*.json` | `fixtures/query-buyer-uae-kbeauty.json`, `fixtures/query-buyer-uk-sunscreen.json`, `fixtures/query-seller-sunscreen-oem.json` |
| `dedupe.input.json` / `dedupe.expected.json` | the dedupe case runs over `buyers.golden.json`; the expectation is `fixtures/expected/dedupe.expected.json` |
| `T01..T10/<case>.input.json` | the T-cases run over the two golden sets; each case names the record id it asserts on (section 4) |

Other fixtures:

| File | Purpose |
|---|---|
| `fixtures/rfq.no-match.json` | RFQ #901 — T10. Requires `EWG_VERIFIED`, which no seller holds. |
| `fixtures/match-134.input.json`, `fixtures/match-no-match.input.json` | Run envelopes (`rfq` + `query` + `records`) for `score_match.py`. The envelope's `query` carries `region_countries`, which the agent expands from the operator's "GCC" **before** the query surface is built (BUILD-CONTRACT 8.7 — scripts never expand regions). Without it `market_fit` for Seller A is 79 instead of the contract's 87. `run_tests.py` asserts that each envelope embeds its RFQ and the seller set byte-identically, so the duplication cannot drift. |
| `fixtures/rerank-134.json` | The SCORING-CONTRACT 6.2 rerank input (`delta +2`, one evidence id). |
| `fixtures/normalize.cases.json` | Pure string cases for `canonical_domain` / `normalize_company_name`, copied from BUILD-CONTRACT 8.1 and 8.3. These are string transformations and assert nothing about any business. v0.2.0 adds four guard cases for the end-only legal forms: `LLP Cosmetics` and `Pvt Beauty` stay intact (the tokens are a legal form only at the tail), while `Aurora Beauty Pvt Ltd` and `Sunbright LLP` still normalise. |
| `fixtures/expected/rfq.readiness.expected.json` | RFQ readiness per SCORING-CONTRACT 2.9. |
| `fixtures/reviews.buyers.uae.csv`, `fixtures/reviews.match-134.csv` | Filled operator review sheets for the calibration loop (section 11). Reviewers are a **role label**, never a name; no address and no number appears in any cell. |
| `fixtures/expected/review-sheet.*.csv`, `fixtures/expected/acceptance.*.expected.json` | Golden review sheets and golden `acceptance-report` documents (section 11). |
| `fixtures/rfq.id-halal.json`, `fixtures/match-id-halal.input.json` | RFQ #ID-701 — **destination `ID`, `required_certifications: ["HALAL"]`**, six Korean private-label serum makers of their own (not `sellers.golden.json`). Added to show that the v0.1.0 rubric already prices an emerging-market request with a claim certification and a destination registration **without any scorer, `scoring.config.json` or `score_version` change**. `run_tests.py` asserts that the envelope embeds the standalone RFQ byte-identically. |
| `fixtures/expected/match-id-halal.expected.json` | Expectation for R9, same field-level shape as `match-134.expected.json`. |
| `fixtures/expected/mcp.tools-list.expected.json` | The exact bytes of the MCP tool server's `tools/list` result, one compact JSON line (section 16, M-13). Unlike the expectation documents below it is a byte golden: regenerate it only when a tool, schema or annotation changes on purpose. |

**Counts.** 20 buyer records across AE / GB / US / JP / SG / DE and 20 Korean seller records
(19 `KR` + 1 with an unknown country), as the Phase 0 roadmap requires.

### Expectation documents

`fixtures/expected/*.json` are **field-level expectation documents**, not byte-for-byte copies of
script output. Each one names the fields the harness compares (`dimension_scores`,
`qualification_score`, `qualified`, `confidence`, `missing`, the unknown criterion ids, the
hard-filter arrays, `base_score` / `match_score` / `component_scores` /
`weighted_contributions`, the ordering, the exclusions and the summary counters). Everything else
a script emits — `dimension_details`, `notes`, `evidence_index`, rendering carry-over — is left
free, so the suite pins the contract's arithmetic without freezing key order or prose.

Two comparison rules make the pinned fields exact rather than fuzzy:

- **`unknown_criteria`** is compared after dropping entries whose `criterion_id` matches `HF-*`.
  Those are the zero-weight records SCORING-CONTRACT 3.1 requires for a skipped hard filter; the
  expectation names them separately in `zero_weight_unknown_penalty_rule_ids`.
- **`missing`** is compared after dropping the labels of those same zero-weight records, so the
  criterion labels are compared in exact order (INV-03's first-appearance rule).
- **`reason_summary`** is compared as a **substring**: BUILD-CONTRACT R6.2.3 fixes the fields of a
  `failed_rule` and the thousands separator but not the list formatting, so the expectation carries
  the stable core (`"not covered"`, `"not held"`, …). The one exception is the HF-03 message, which
  SCORING-CONTRACT 6.2 pins verbatim: `MOQ minimum 5,000 exceeds RFQ max 3,000`.

### How the expected numbers were derived

1. Every value was computed **from `docs/SCORING-CONTRACT.md` and `schemas/scoring.config.json`
   first**, by an implementation written against the contract alone, never by recording script
   output.
2. That derivation is anchored on the contract's own worked examples, which the fixtures reproduce
   exactly: **`BUY-gulfglow-example` is SCORING-CONTRACT 6.1 example (a)** (92 / 95 / 100 / 100 /
   95 / 94 → **96/100**, confidence **0.94**, `Missing: none`), and **`SEL-hanbitcos-example`,
   `SEL-daehansuncare-example`, `SEL-yeonhwalab-example` are 6.2's Sellers A, B and C**
   (A: 100 / 100 / 90 / 96 / 87 / 100 → base **97**, +2 rerank → **99**; B: hard-filtered on HF-03;
   C: 91 / 95 / 43 / 64 / 77 / 86 → **79**, confidence **0.82**). If any of those anchors moves, the
   derivation is wrong, not the contract.
3. The scripts were then run and reconciled against the derivation. Where they disagreed, the
   disagreement was investigated and either the fixture was fixed (section 7) or the divergence is
   reported as an open item (section 8) — never silently absorbed into `expected/`.

### Fixture conventions that the contracts leave open

| Convention | Why the fixtures use it |
|---|---|
| **Agent-judged signals are recorded as their own sourced claim.** A signal that depends on what a page *says* rather than on a record field — `korean_category_page`, `named_product_page`, `catalog_or_pdf_published`, `sample_or_trial_order_accepted`, `partial_english_materials`, `trade_show_exhibitor`, `sourcing_closed_statement` — is carried by an evidence item whose `claim` **is the signal key**. BUILD-CONTRACT 4.6 allows a free-text claim where no canonical key exists, and this keeps prose judgement out of the deterministic half of the pipeline (PRD 13.2). | Without a convention, `BUY-gulfglow-example` cannot reach the contract's 96 (B-KF2's `korean_category_page` is worth 22) and `SEL-hanbitcos-example` cannot reach `product_fit 100` (S-PF3 caps at 15 only when two of its three signals fire). |
| **Ranges are stored in the normal form.** `{"max": 2000}` is stored as `{"min": 0, "max": 2000}` and an open lower bound ("from 2,000 units") is stored as `"unknown"` plus a note. | `numeric_range` requires both bounds (`schemas/seller.schema.json`), and schemas outrank prose (BUILD-CONTRACT 1.3). BUILD-CONTRACT 3.3's `{"max": N}` and `{"min": N}` spellings are *input* spellings; a stored document carries the normal form. |
| **Raw records carry `status: DISCOVERED` or `VERIFIED` only.** `VERIFIED` is used only where a material claim is evidenced at `source_tier <= 3`. | INV-37. `BUY-northgateimport-example` and `BUY-quietharbour-example` are `DISCOVERED` because their only sources are tier-4 directory rows. |
| **`confidence: 0` and `qualification_score: 0` on every raw record**, with `score_version: "unscored"`. | BUILD-CONTRACT 3.1's raw profile. |

---

## 2. What each fixture record is for

### Buyers (`fixtures/buyers.golden.json`)

| id | Country / type | Why it exists |
|---|---|---|
| `BUY-gulfglow-example` | AE distributor | **SCORING-CONTRACT 6.1 reference record → 96/100** |
| `BUY-dunesourcing-example` | AE importer | A strong importer with a second corroborating domain (tier-4 directory) |
| `BUY-marinaretail-example` | AE retailer | **T01** retail-only: `wholesale_signal false`, `partnership_signal false` |
| `BUY-desertbulk-example` | AE wholesaler | **T09** beauty-unrelated wholesaler, unmapped category slugs, `korean_products_signal false` |
| `BUY-luminaglow-example` | GB distributor | **T06** duplicate pair, non-www side (the merge survivor) |
| `BUY-www-luminaglow-example` | GB distributor | **T06** duplicate pair, `www.` side, thinner evidence |
| `BUY-britsun-example` | GB wholesaler | **T02** suncare carrier with a dated open call — the UK rank 1 |
| `BUY-albionbeautyhall-example` | GB retailer | Retailer *with* wholesale; `korean_products_signal` unknown (no named brand, no sourcing statement) |
| `BUY-palefade-example` | GB importer | **T07(a)** stale but reachable: `stale: true`, `operational_status: active`, all evidence dated 2023 |
| `BUY-northgateimport-example` | GB importer | **T07(b)** evidenced unreachable: `operational_status: unreachable`, tier-4 sources only |
| `BUY-pacificglowdist-example` | US distributor | **T08** resolved conflict: own site says distributor, a directory says retailer |
| `BUY-libertybeautysupply-example` | US wholesaler | A wholesale_form + phone contact set (B-RE1 fallback ranking) |
| `BUY-cascademarket-example` | US marketplace | `company_type_marketplace` (36) and `wholesale_signal false`; Korean carriage stays unknown because third-party sellers are not the operator's carriage |
| `BUY-sakurabeautytrading-example` | JP distributor | A posted buying request (`rfq_posted`, 35 points, capped) |
| `BUY-kantoimport-example` | JP importer | Almost everything unknown, plus an **unresolved** conflict (dangling `conflicts_with`, no `conflicts[]` entry) |
| `BUY-lioncitybeauty-example` | SG distributor | Trade-show corroboration on a second domain |
| `BUY-straitswholesale-example` | SG wholesaler | `contact_channels: []` — present and empty, a verified negative |
| `BUY-rheinkosmetik-example` | DE importer | A German-language site with a wholesale form |
| `BUY-novadrift-example` | GB distributor | `source_query.category_drift: true` (PRD DISC-05) |
| `BUY-quietharbour-example` | unknown | One tier-4 evidence item that covers **no** material claim: `evidence_quality 0`, excluded by DISC-06 |

### Sellers (`fixtures/sellers.golden.json`)

| id | Why it exists |
|---|---|
| `SEL-hanbitcos-example` | **SCORING-CONTRACT 6.2 Seller A** — manufacturer, MOQ 2,500, three evidence domains |
| `SEL-daehansuncare-example` | **6.2 Seller B / T03** — confirmed MOQ 5,000 |
| `SEL-yeonhwalab-example` | **6.2 Seller C / T04** — MOQ not published |
| `SEL-mirinaeoem-example` | OEM maker, `certifications_verified false`, `regulatory_registrations: []` |
| `SEL-saebyeokbrand-example` | Brand-only exporter: `private_label false`, `oem_odm false`, `brand_export true` |
| `SEL-hansolodm-example` | The strongest ODM: four categories, AE registration, HALAL |
| `SEL-baekdutrading-example` | A **distributor**, not a maker: `oem_odm false`, `private_label false` |
| `SEL-chorokfactory-example` | Certifications, export markets, English site and partner signal all **absent** (six unknown criteria) |
| `SEL-nuriskinlab-example` | MOQ as a **range** `{1000, 5000}` — compared on its `min` |
| `SEL-pyeongwhacos-example` | MOQ published as "up to 2,000", stored `{0, 2000}` |
| `SEL-dalbitlabs-example` | MOQ published as "from 2,000" — an open upper bound, stored `"unknown"` |
| `SEL-kkotgilcos-example` | MOQ in **kg** against a query in units — unit mismatch |
| `SEL-haneulbio-example` | `certifications: []` **and** `certifications_verified: true` — the only shape that lets HF-04 reject |
| `SEL-sopoongworks-example` | `operational_status: unreachable` (T07 b, seller side) |
| `SEL-areumfactory-example` | `stale: true` with 2023 sources (T07 a, seller side) |
| `SEL-jinheungcos-example` | `excluded_markets: ["AE"]` — HF-05 |
| `SEL-byeolbitlab-example` | Haircare only — `category_relation` is `none`, HF-01 rejects |
| `SEL-muljilcosmetic-example` | `serum` + `essence` — *adjacent* to sunscreen: scores 24 on S-PF1 but still fails HF-01 |
| `SEL-hwadamglobal-example` | `country: "unknown"` — HF-08 is skipped, never failed |
| `SEL-saeromtrading-example` | One tier-2 directory row covering no material claim — HF-00 evidence gate |

---

## 3. Runs the harness performs

| # | Command (all with `--as-of 2026-09-12 --pretty`) | Expectation file |
|---|---|---|
| R1 | `normalize_company.py --input buyers.golden.json --entity buyer` | inline assertions + `normalize.cases.json` |
| R2 | `dedupe_companies.py --input buyers.golden.json --entity buyer` | `expected/dedupe.expected.json` |
| R3 | `score_buyer.py --input buyers.golden.json --query query-buyer-uae-kbeauty.json` | `expected/buyers.uae.discovery.expected.json` |
| R4 | `score_buyer.py --input buyers.golden.json --query query-buyer-uk-sunscreen.json` | `expected/buyers.uk.discovery.expected.json` |
| R5 | `score_seller.py --input sellers.golden.json --query query-seller-sunscreen-oem.json` | `expected/sellers.discovery.expected.json` |
| R6 | `score_match.py --input match-134.input.json --rerank-input rerank-134.json` | `expected/match-134.expected.json` |
| R7 | `score_match.py --input match-no-match.input.json` | `expected/match-no-match.expected.json` |
| R8 | `validate_output.py --strict --invariants` over one fixture of each of the six document kinds | exit `0` (BUILD-CONTRACT 13.1) |
| R9 | `score_match.py --input match-id-halal.input.json` | `expected/match-id-halal.expected.json` |

Every run is executed **twice** and the two stdout byte strings must be identical (INV-13).
`normalize_company.py` and `dedupe_companies.py` are additionally re-applied to their own output
(INV-15).

Headline expected numbers:

- R3 (UAE, K-Beauty, no category filter): 20 found, 19 scored, **5 qualified**, 1 excluded; rank 1
  `BUY-gulfglow-example` **96/100**.
- R4 (UK, sunscreen): rank 1 `BUY-britsun-example` **92/100**, then `BUY-luminaglow-example` 82.
- R5 (sunscreen + OEM/ODM + MOQ ≤ 3,000): 20 found, 13 scored, 12 qualified, **7 excluded**
  (HF-01 ×2, HF-02 ×2, HF-03 ×1, HF-05 ×1, plus one DISC-06 evidence exclusion).
- R6 (RFQ #134): 20 considered, **11 passed the hard filter**, 10 returned, 9 excluded, 1 reranked;
  `SEL-hanbitcos-example` base **97** → match **99**, `SEL-yeonhwalab-example` **81**.
  RFQ readiness **70/100** (7 of 10 fields; `buyer_id`, `target_price`, `timeline` missing).
- R7 (RFQ #901): **every** candidate excluded, `is_no_match: true`, binding rule **HF-04**
  (12 rejections, ahead of HF-07's 6).
- R9 (RFQ #ID-701, Indonesia / HALAL): 6 considered, 4 passed the hard filter, **2 excluded**
  (HF-04 ×1, HF-05 ×1); `compliance_fit` **100 / 97 / 91** across `registered` / `in_progress` /
  absent `regulatory_registrations`, and the seller with no HALAL on an **unverified** list is
  ranked last at **81** rather than rejected. RFQ readiness **60/100** (6 of 10 fields).

---

## 4. PRD 16 — T01 … T10

| Case | Input | Command / run | Expectation asserted | Invariant it protects |
|---|---|---|---|---|
| **T01** Buyer broad | `buyers.golden.json` + `query-buyer-uae-kbeauty.json` (UAE, K-Beauty, no category filter) | R3 | `BUY-marinaretail-example` (retailer, `wholesale_signal false`) scores **`b2b_role` 0** — `company_type_retailer` 24 + `wholesale_signal_false` 0 + B-CR3 unknown 5 = 29, then the stacked `retail_only_consumer_store` (−25) and `wholesale_signal_false` (−15) clamp the dimension to 0 — is **not qualified** (50/100), and is outranked by both the AE distributor (96) and the AE importer (81) | PRD 6.3 weighting; the adjustment-stacking rule of SCORING-CONTRACT 1.2 |
| **T02** Buyer product | `buyers.golden.json` + `query-buyer-uk-sunscreen.json` (GB, sunscreen) | R4 | `BUY-britsun-example` (sunscreen / suncare / after_sun carrier, dated open call) is **rank 1** at 92; the GB sunscreen distributor is in the top three; no buyer that carries no suncare signal outranks it; the unevidenced candidate is not ranked at all | B-MR1 `country_exact_match`, B-MR2 `category_exact_match`, INV-30 ordering |
| **T03** Seller strict MOQ | `rfq.134.json` (`max_moq 3000`) + `SEL-daehansuncare-example` (`moq 5000`, tier-1 official) | R6 | The seller is in `excluded[]` with `rule_id HF-03`, `observed_value 5000`, `required_value 3000`, reason exactly `MOQ minimum 5,000 exceeds RFQ max 3,000`; it carries **no** `match_score`, **no** `unknown_penalty_applied` entry, and does not appear in `results[]` | INV-06, INV-32, INV-08; `hard_filter_tolerances.moq_overshoot_ratio = 0.0` |
| **T04** Unknown MOQ | `rfq.134.json` + `SEL-yeonhwalab-example` (`moq` absent) | R6 | The seller **survives** in `results[]` at 79/100; `HF-03` is listed in `rules_skipped_unknown` and **not** in `failed_rules`; an `unknown_penalty_applied` entry for `S-OP1` carries `applied_points 17` (= `round_half_up(55 × 0.30)`) and `unknown_inputs ["seller.moq"]`; `MOQ against the buyer ceiling` appears on the `Missing:` line; `operation_fit` is **43**, not 20 | INV-07, INV-03; `unknown.never_hard_reject_on_unknown` |
| **T05** False-claim prevention | every produced document | R3–R7 | No output matches `currently looking` / `actively looking` / `we have a buyer` / `active demand` / `지금 찾고 있` / `바이어가 찾`; every candidate that passed the hard filter carries **≥ 2** `rationale` items and **every** rationale item carries at least one `evidence_id` | INV-20, INV-34, PRD 11.1 |
| **T06** Duplicate domains | `BUY-luminaglow-example` + `BUY-www-luminaglow-example` | R2, R3 | Dedupe emits **19** records; the `www.` record is gone; the survivor carries `merged_from` and `alias_domains` containing `www.luminaglow.example`, returns to `score_version "unscored"` with `qualification_score 0`, unions the categories (`serum` joins) and the contact channels (3), and keeps every evidence id collision-free. Neither duplicate reaches a corroboration-inflated `evidence_quality`, because `canonical_domain` folds `www.x` and `x` into one domain | INV-15, INV-16, INV-17, INV-18; SCORING-CONTRACT 4.3 |
| **T07(a)** Stale but reachable | `BUY-palefade-example` (`stale: true`, `active`, 2023 sources) | R3 | The record is **kept**, `stale` stays `true`, and `confidence` is **0.26** — below 0.5, so it renders `LOW` even though the score is 55. The `stale_penalty` (−25) and `stale_multiplier` (0.6) both apply | PRD EVID-04; SCORING-CONTRACT 0.7, 4.4 |
| **T07(b)** Evidenced unreachable | `BUY-northgateimport-example` (discovery) and `SEL-sopoongworks-example` (match) | R3, R6 | Discovery **keeps** the unreachable buyer (`operational_status: unreachable`, confidence 0.19) because HF-06 is not among the discovery rules; the match run **excludes** the unreachable seller with `rule_id HF-06` | BUILD-CONTRACT R6.2.6; HF-06 |
| **T08** Evidence conflict | `BUY-pacificglowdist-example` (own site: distributor, tier-4 directory: retailer) | R3 | `company_type` stays `distributor`; `conflicts[0]` records `losing_value "retailer"` with `resolution "official_source"`; **both** evidence items survive (`EV-001` and `EV-009`); the resolved conflict costs nothing — `evidence_quality` is **100** | BUILD-CONTRACT 4.8; INV-17; SCORING-CONTRACT 4.4 |
| **T08(b)** Unresolved conflict | `BUY-kantoimport-example` (dangling `conflicts_with`, no `conflicts[]` entry) | R3 | `evidence_quality` is **64** (one `conflict_penalty` of −10) and `confidence` is **0.41** (`0.64 × 0.75 coverage × 0.85 conflict`) | SCORING-CONTRACT 4.4, 0.7 |
| **T09** Non-K-Beauty drift | `BUY-desertbulk-example` (`industrial_cleaning`, `packaging_materials`, `korean_products_signal false`) | R3 | `kbeauty_fit` is **0**: B-KF1 0 (verified false), B-KF2 **scored** 0 (the categories were read, nothing fired — not unknown), B-KF3 0, then `non_beauty_business` (−60) and `korean_products_signal_false` (−25) clamp the dimension. The record is **not qualified** (53/100) and ranks 12th of 19, so it never reaches a top-10 list | SCORING-CONTRACT 0.2 rule 1; the `non_beauty_business` adjustment; INV-33 (the unmapped slugs are preserved and noted, never dropped) |
| **T10** No match | `rfq.no-match.json` (`required_certifications: ["EWG_VERIFIED"]`, `max_moq 3000`, `max_lead_time_days 45`) over all 20 sellers | R7 | `no_match.is_no_match` is **true**, `results` is empty, `reason` is non-empty, `binding_rule_ids[0]` is **HF-04** (12 rejections — the certification really is the binding constraint), and all 20 candidates are listed in `excluded[]` | INV-21; SCORING-CONTRACT 3.5; INV-33 (`EWG_VERIFIED` is an unmapped token that matches only exactly, so it cannot be satisfied by accident) |

---

## 5. Extra negative / edge cases

PRD 22 asks for ten or more negative/edge tests beyond T01–T10. These run inside the same runs.

| Case | Input | Expectation | Invariant it protects |
|---|---|---|---|
| **E01** MOQ range | `SEL-nuriskinlab-example`, `moq {min 1000, max 5000}` vs a 3,000 ceiling | `operational_fit` **95**: the ceiling test reads the **min** (1,000 ≤ 1,500), so `moq_at_or_below_half_of_max` (55) fires — reading the `max` would have scored 0 | BUILD-CONTRACT 3.3, 5.3.S3 |
| **E02** Open-ended MOQ | `SEL-dalbitlabs-example`, "from 2,000 units" | `moq` is `"unknown"`, S-OP1 takes the unknown path, `operational_fit` **57**; the value is never coerced to 2,000, to `+inf` or to the ceiling | BUILD-CONTRACT 3.3 ("open bounds are forbidden in stored documents") |
| **E03** MOQ unit mismatch | `SEL-kkotgilcos-example`, `moq_unit "kg"` against a query in `units` | S-OP1 is **unknown** (not 0), HF-03 is skipped, and the `moq_unit_mismatch` adjustment (−10) fires: `operational_fit` **47** | SCORING-CONTRACT 2.3; BUILD-CONTRACT 3.3 R3.3.1 |
| **E04** Verified-empty certifications | `SEL-haneulbio-example`, `certifications: []` + `certifications_verified: true` | HF-04 **rejects**. This is the only shape in the package that lets an absent certification reject anything | BUILD-CONTRACT R6.2.4 |
| **E05** Unverified certifications | `SEL-yeonhwalab-example`, `certifications_verified` absent | HF-04 appears in **neither** `rules_evaluated` nor `rules_skipped_unknown`: `applies_when` is literally false, so it needs no unknown pairing | SCORING-CONTRACT 3.1; ledger item 14 |
| **E06** Unknown seller country | `SEL-hwadamglobal-example`, `country "unknown"` | HF-08 is in `rules_skipped_unknown` **and** a **zero-weight** `unknown_penalty` record keyed `HF-08` exists, so the skip is visible in both arrays | INV-07, INV-03; SCORING-CONTRACT 3.1 |
| **E07** Sibling category | `SEL-muljilcosmetic-example`, `serum` + `essence` vs `sunscreen` | `category_relation` is `adjacent`, which scores 24 of 60 on S-PF1 but does **not** clear HF-01 (only `exact` and `parent` do) | BUILD-CONTRACT 8.5; HF-01 |
| **E08** Present-and-empty list | `BUY-straitswholesale-example`, `contact_channels: []` | All three reachability criteria are **scored at 0** (no unknown penalty is emitted) and `contact_channels_verified_empty` (−15) fires, so `reachability` is **0** — distinguishable from an absent array, which would have scored 31 | BUILD-CONTRACT 3.2; INV-02 |
| **E09** Evidence that covers no material claim | `BUY-quietharbour-example`, one tier-4 directory row | `evidence_quality` 0 and the record is in `excluded[]` with `no evidenced material claim`. It also proves the `stale_penalty` non-empty guard: `covered` is empty, so the "every covered claim is old" clause must **not** fire vacuously | BUILD-CONTRACT R7.8.1 (PRD DISC-06); SCORING-CONTRACT 4.4 |
| **E10** Evidence sufficiency gate | `SEL-saeromtrading-example` | Passes all eight configured hard filters, then leaves `results[]` through **HF-00** with `fewer than two evidenced fit reasons (PRD 15.3)` | BUILD-CONTRACT 6.6.4; PRD 15.3 |
| **E11** Bounded rerank | `rerank-134.json` | `abs(delta) <= 5`; `applied: true` requires a non-empty `rationale` and ≥ 1 `evidence_id`; `applied: false` forces `delta 0`; `match_score == clamp(base_score + delta, 0, 100)`; the 6.2 example moves 97 → **99** and never reorders past `max_delta` | INV-05, INV-08; SCORING-CONTRACT 3.3 |
| **E12** Evidence ids resolve | every candidate of R6 | Every id referenced by `evidence_ids`, `rationale`, `risks` or `rerank` resolves inside `evidence_index` | INV-22 |
| **E13** One rubric per run | R3–R7 | A single `score_version` across every envelope and record, and never `"unscored"` in a scored output | INV-23 |
| **E14** Partial degrades | R3, R5, R6 | Every run envelope carries a `partial` flag rather than aborting | INV-35 |
| **E15** Summary counters | R6, R7 | `candidates_considered >= passed_hard_filter >= returned`, `excluded_count == candidates_considered − passed_hard_filter`, and `excluded_count == len(excluded)` | SCORING-CONTRACT 3.5 |
| **E16** Normalization cases | `normalize.cases.json` | The twenty-two `canonical_domain` cases and twenty `normalize_company_name` cases of BUILD-CONTRACT 8.1 / 8.3, including `co.uk → unknown`, an IPv4 literal → `unknown`, `www.www.example.com → example.com` and `ABC Trading LLC → abc` | INV-15, INV-33 |
| **E16(c)** Emerging-market legal forms | `normalize.cases.json`, twelve name cases | India `Pvt. Ltd.` / `Private Limited` / `LLP`, Indonesia `PT …` / `CV …` / `… Tbk` and Türkiye `A.Ş.` / `Ltd. Şti.` / `San. ve Tic.` / `Anonim Şirketi` all reduce to the bare name, **including the all-caps Turkish spelling** whose `İ` casefolds to `i` + U+0307. Four guard cases prove the restricted edges hold: `Private Label Studio`, `Tic Beauty Studio`, `Aş Kozmetik` and `Beauty PT` are returned unchanged | BUILD-CONTRACT 8.3 step 5; INV-15 |
| **E16(b)** IDN folding | `normalize.cases.json`, four IDN cases | `https://마이브랜드.kr/about`, `https://www.마이브랜드.kr` and the already-punycode `https://xn--hy1b45cw6b22g85n.kr/` all fold to `xn--hy1b45cw6b22g85n.kr`, and `https://例え.jp/` to `xn--r8jz45g.jp` — which is what lets `dedupe_companies.py` merge a local-language duplicate. T06 covers only the `www`/non-www pair; the reference pages previously cited T06 for the IDN half too, and now cite these cases | PRD 15.1 criterion 4; INV-15, INV-16 |
| **E17** Domain beats name | `expected/dedupe.expected.json must_not_merge` | Two records with different known `canonical_domain` values never merge, whatever their names | INV-16 (R8.4.2) |
| **E18** Determinism | every run | Two identical invocations produce byte-identical stdout; `normalize_company.py` and `dedupe_companies.py` are stable when re-applied to their own output | INV-13, INV-15 |
| **E19** Renderable candidate | R3 and R6 output with `website`, `contact_channels`, `missing`, `company_type` and `oem_odm` stripped | `validate_output.py --strict --invariants` exits **1** and reports `INV-19` on both the discovery and the match document. Presence, not non-emptiness: a present-and-empty list is a verified `none` (E08) and stays legal. `website` may be absent only when `canonical_domain` is `"unknown"`, which BUILD-CONTRACT 8.1 makes the only case in which no website was discoverable | **INV-19**, PRD 15.1 criterion 2, PRD 12.2 |
| **E20** Destination registration ladder (ID) | R9, `SEL-ahyeonlab-example` / `SEL-durimcos-example` / `SEL-maruhwabio-example` — identical except for `regulatory_registrations` | `compliance_fit` is strictly decreasing **100 → 97 → 91** for `registered` → `in_progress` → **absent**, and the three keep that ranked order. The absent array takes the **S-CP3 unknown penalty** and surfaces `Destination-market registration` on the `Missing:` line; it is never read as "checked, none found" | S-CP3; BUILD-CONTRACT 3.2 |
| **E21** HALAL required, ID destination | R9, `SEL-cheonglimoem-example` (verified list, no HALAL) vs `SEL-baraecos-example` (tier-4 directory list, no HALAL) | The **verified** list fails **HF-04** and leaves `results[]`; the **unverified** list puts HF-04 in **neither** hard-filter array, stays ranked, and is priced down instead — `compliance_fit` **21** against 91 for a held HALAL, total **81** against 92. An unknown is penalised, never fatal | INV-07; PRD test T04; BUILD-CONTRACT R6.2.4 |
| **E22** Seller-declared market exclusion | R9, `SEL-hanaraexport-example`, `excluded_markets: ["ID"]` | **HF-05** rejects, with `Does not supply ID (market excluded by the seller)`; no `match_score` is emitted | INV-32; HF-05 |

### Fixture-level cases (no script needed)

| Case | Expectation | Invariant |
|---|---|---|
| Schema self-check | Every `schemas/*.json` parses, every `$ref` resolves in-file, every keyword is inside the `_common.validate` subset of BUILD-CONTRACT 7.5, the shared `$defs` of 3.7 agree structurally between files, and the versions agree with `scoring.config.json` | BUILD-CONTRACT 13.1 (PRD 22 DoD 3) |
| Fixture validation | Every buyer, seller, RFQ and embedded evidence item validates | PRD 22 DoD 3 |
| Material-claim coverage | Every material claim of `scoring.config.json evidence.material_claims` is either evidenced or `unknown`/absent — no third state | INV-01 |
| Observation times | No `observed_at` after `--as-of`, no `source_date` after its own `observed_at` | INV-24 |
| Entity status | `DISCOVERED` needs a resolvable `source_url`; `VERIFIED` needs a material claim at `source_tier <= 3`; no raw fixture carries a later state | INV-37, INV-09 |
| Fictional domains | Every website and evidence host ends in `.example` | fixture policy (top of this file) |
| No personal data | No `first.last@`-shaped address anywhere in `tests/fixtures/` | INV-31 |
| Envelope consistency | `match-134.input.json` / `match-no-match.input.json` embed their RFQ and the seller set byte-identically; `match-id-halal.input.json` embeds `rfq.id-halal.json` byte-identically (its six seller records are its own) | prevents fixture drift |

### Safety cases (static, no script needed)

| Case | Expectation | Invariant |
|---|---|---|
| No send capability | No `smtplib`, `SMTP`, `sendmail`, `send_email`, `sendgrid`, `mailgun`, `postmark`, `ses.send`, `send_message(` or Gmail client anywhere in the package, except on a line that explicitly forbids it | **INV-10** |
| No auto-send path | No line assigns `auto_send` a truthy value anywhere | INV-10, PRD 11.2 |
| No approval bypass | Nothing sets `APPROVED_FOR_OUTREACH` or any later state | INV-09 |
| No contact harvesting | No email-pattern generator (`{first}.{last}@`, `first.last@`, `"%s.%s@"`) in any `.py` | INV-11 |
| No placeholders | No `TODO`, `FIXME`, `XXX`, `NotImplementedError`, `pass # stub`, `test.skip`, `.only(` or `__INLINE_` token in any shipped file | INV-28 |
| No wall clock | No `datetime.now`, `datetime.utcnow`, `date.today` or `time.time(` in `scripts/*.py` | INV-14 |
| Stdlib only | Every import in `scripts/*.py` is stdlib, `__future__`, or a sibling script | INV-26 |
| No network client | No `socket` / `ssl` / `http` / mail-protocol import, no `urllib.request` in any spelling (`from urllib import request` included), no `__import__` / `importlib`, and `subprocess` only in `mcp_server.py`, in `scripts/*.py` | R7.11.1 |
| No transport module | No shipped file is named like a mailer or dispatcher | BUILD-CONTRACT 2.3 |

---

## 6. Field regressions — the 2026-09-13 live web trials

On **2026-09-13** the skill was run twice against the **real public web**: a buyer-discovery trial
for a Gulf destination market, and a Korean seller-discovery trial for an OEM/ODM sourcing brief.
Four blockers came back that no fixture could have produced on its own, because each one needed a
real query surface, a real hosting arrangement, a real marketing vocabulary or a real evidence gap.
The cases below are the machine half of those four fixes. They live in
`phase_field_regressions()` in `run_tests.py`, apart from T01–T10 and E01–E19, so a later reader can see at a
glance which assertions are paid for in field evidence rather than in reasoning.

What the trials found, in aggregate — no candidate is named here, and none is nameable from
anything in this package:

1. **A run with no `--query` produced a confident document.** Every hard filter was skipped, every
   query-reading criterion scored 0 inside a full denominator, and the envelope still read
   "*N* qualified at >= 70". An operator had no way to see that the brief had never been applied.
2. **Two unrelated Korean manufacturers collapsed into one record.** Several first-page results in
   that vertical publish on site-builder and storefront platforms that hand every tenant a
   subdomain; reducing those to the platform domain merged two companies and deleted one real
   manufacturer from the run.
3. **A bare "GMP" in marketing copy became the Korean MFDS CGMP designation.** Factory pages in
   that vertical print the bare token as a quality claim far more often than they hold the
   designation; the normalizer promoted it, which then fired the baseline-quality signal and
   satisfied a hard filter that required CGMP — inferring a certification, inside the normalizer.
4. **A buyer candidate with no K-Beauty evidence anywhere qualified anyway.** Strong B2B, sourcing
   and reachability evidence carried an otherwise-empty record over the threshold on a K-Beauty
   brief, above several candidates with evidenced Korean portfolios.

Every fixture these cases use is built inside `run_tests.py` from invented companies on `.example`
hosts, on the same policy as `tests/fixtures/` (top of this file). Each case was confirmed to
**fail** when the fix it protects is reverted, so none of them is decorative.

| Case | Input / run | Expectation asserted | Invariant it protects |
|---|---|---|---|
| **FR-01** No `--query`, envelope | `score_seller.py` and `score_buyer.py` over the golden sets with **no** `--query` | Both exit **0** (degrade, never abort), both set `partial: true`, and the envelope note names the missing `--query` **and** the rules and criteria that were skipped by id (`HF-01`, `HF-03`, `HF-08`, `S-PF1`, `S-CP1`; `market_relevance`) | **INV-35**; BUILD-CONTRACT R6.2.6 — a skipped filter must be visible, not inferable |
| **FR-01b** No `--query`, dimension | `score_buyer.py`, same run | `market_relevance` is **absent from `dimension_scores`** — dropped from both sides of the fraction, never scored 0 — and every record carries a renormalisation note | `scoring.config.json config_notes` INAPPLICABLE vs UNKNOWN; PRD 6.3 (the six weights are fixed, so a silent 0 docks every candidate) |
| **FR-01c** No `--query`, criterion | both scripts, same run | Every query-reading criterion (`S-PF1`, `S-PF2`, `S-CP1`, `S-CP2`, `S-CP3`; `B-MR1`–`B-MR3`) is `state: "inapplicable"` — never `"scored"`, never `"unknown"` — takes **no** `unknown_penalty_applied` entry, and each dimension's `raw_score` is the fraction over its **applicable** criteria only, not the whole denominator. The partial-run **`WARNING` line is on stderr**, where the operator reads it | **INV-35**, INV-03, INV-07; SCORING-CONTRACT 0.4 — "the query never asked" and "the company never published it" are different states with different arithmetic |
| **FR-02** Shared hosting, key | `_common.canonical_domain` over four tenant pairs on platform hosts | Two tenants of one platform never reduce to the same key; the bare platform host is **never** a `canonical_domain` (it returns `unknown`); `www`/non-www on an ordinary domain still collapses (T06 stays green) | **INV-16**; BUILD-CONTRACT 8.1 |
| **FR-02b** Shared hosting, end to end | `dedupe_companies.py` over two tenants of one listed platform, `canonical_domain` **not** declared in the input so it must be derived | **2** records survive, `merges` is empty, and the derived keys are the two tenant hosts — the record count alone would pass even if the key had collapsed, because dedupe also guards on the name | **INV-16** (R8.4.2) |
| **FR-02c** Unlisted hosting suffix | `dedupe_companies.py` over two differently-named tenants of a platform the embedded suffix list does **not** know, plus a control pair (one company, two of its own pages) | The two tenants **both survive**, are reported in `merges_suggested` under key `canonical_domain` for operator confirmation, and carry a "NOT merged" note — while the control pair still **merges**, so the refusal is narrow and not a dedupe outage | **INV-16**, BUILD-CONTRACT 8.4 R8.4.2 — the embedded list can never be the full PSL, so an unlisted platform must fail towards a missed merge, never a false one |
| **FR-03** Bare GMP, normalizer | `_common.normalize_certification` | `"GMP"` does **not** return `CGMP` (it is recordable as its own token), case-folds idempotently, and is absent from both of `score_seller.py`'s quality-token groups; `CGMP`, `cGMP` and `ISO 22716` still normalise as before | BUILD-CONTRACT 8.6; `references/evidence-policy.md` — never infer a certification, including inside a normalizer |
| **FR-03b** Bare GMP, record | `score_seller.py` over a fictional Korean maker whose entire certification text is the bare token | The record is kept and scored, fires **neither** `iso22716_or_cgmp_held` **nor** `other_quality_certification_held`, and earns **0** on S-CP4; against a query that **requires** CGMP with a verified certification list it is excluded by **HF-04**, and the reported held list does not contain `CGMP` | BUILD-CONTRACT R6.2.4, SCORING-CONTRACT 2.1; the same rule one layer out, where an operator meets it |
| **FR-04** Zero K-Beauty evidence | `score_buyer.py` over a maximal fictional Gulf distributor with **no** Korean or K-Beauty evidence anywhere, against the K-Beauty query | The record is **kept, scored and ranked** (never rejected), `qualified` is **false**, a note names the vertical fit gate, and `summary.qualified_count` is **0**. The fixture is pushed to its ceiling on every other dimension, so the case tests the **gate**, not a weak record | `scoring.config.json vertical_fit_gate`; PRD T09, PRD 6.2 DISC-02; `unknown.never_hard_reject_on_unknown` is untouched |
| **FR-04b** The gate is vertical-scoped | the same record against a query with **no** `vertical` | It qualifies normally — the gate is a K-Beauty-run rule, not a global hard reject that would quietly drop candidates out of every other run | `vertical_fit_gate.applies_when_query_vertical_matches` |
| **FR-04c** Unknown is not a loophole | the same record scored twice: once with the K-Beauty dimension **absent**, once with `korean_products_signal` **declared** `"unknown"` | Both are kept, both score **at or above the run's threshold**, and **neither qualifies**; and declaring the dimension unknown never scores higher than saying nothing at all, in `qualification_score` or in `kbeauty_fit` | BUILD-CONTRACT 3.2 (absence and `"unknown"` are one state); closes the "knowing less scores more" shape the buyer trial found |
| **FR-05** Unpublished minimum order | `score_buyer.py` over the golden buyers with the UK sunscreen query | Every record's `missing[]` starts with its penalty labels; `Published minimum order` appears exactly once when `buyer_moq` is unpublished and no penalty entry lists `buyer.buyer_moq`, never when one does, and never as a penalty entry | SCORING-CONTRACT 0.4 `verification_gaps`, INV-03; found in the claude.ai run of 2026-09-14, where three buyers with no published minimum order rendered `Missing: none` |
| **FR-06** Requested category evidence | same run, plus `_common.verification_gaps` on a parent/child category pair | An exact category match adds no line; a parent-level match adds `Requested category confirmed only at a broader level: …`; anything weaker adds `Requested category not evidenced: …`; unknown categories add neither, because they take the B-MR2 unknown path | SCORING-CONTRACT 0.4; the same run, where a login-gated catalogue left suncare unconfirmed while the Missing line read none |
| **FR-07** Seller minimum order | `_common.verification_gaps` for `seller` | An absent or `"unknown"` `moq` yields `Minimum order quantity`; a published `moq` yields nothing; a penalty entry already listing `seller.moq` suppresses the gap | SCORING-CONTRACT 0.4; one fact never produces two lines |
| **FR-08** Korean labels | `references/output-format.md` | Every English line label used in 10.1–10.3 has a fixed Korean mapping, and the map forbids mixing Korean and English labels in one block | BUILD-CONTRACT §10; the same run rendered `유형` and `근거` beside `Website` and `Country` |

---

## 7. Fixture defects this suite found in itself

Recorded so the reasoning is auditable; all are fixed in the current fixtures.

1. `SEL-pyeongwhacos-example` and `SEL-dalbitlabs-example` stored `{"max": 2000}` / `{"min": 2000}`.
   `numeric_range` requires both bounds, so a stored document cannot carry a half-open range: the
   first is stored in normal form, the second as `"unknown"` plus a note.
2. `BUY-www-luminaglow-example` asserted `sourcing_intent` with no backing evidence item (INV-01).
   The field was removed; the duplicate crawl legitimately knows less than the primary record.
3. `BUY-northgateimport-example` was `VERIFIED` while every source was tier 4 (INV-37). It is now
   `DISCOVERED`, which is what a directory-only record deserves.

---

## 8. Open questions and current divergences

These are **reported, not absorbed**. Each one is a live failure or a documented judgement call.

| # | Item | Status |
|---|---|---|
| **Q1** | **Does buyer discovery apply hard filters?** An earlier revision of BUILD-CONTRACT R6.2.6 named `score_buyer.py` as applying HF-01…HF-05 and HF-08, but every predicate in `scoring.config.json hard_filters` is keyed on `seller.*` / `rfq.*` inputs, so none is applicable to a buyer record — and PRD 15.2, the clause R6.2.6 cited, is headed "Seller Discovery". Applying HF-01 to `buyer.product_categories` would hard-exclude 8 of the 20 golden buyers from the UK run, contradicting PRD 15.1 (≥ 20 candidates) and PRD T02 ("prioritise", not "exclude"). **Resolved in favour of the narrow reading, and the contract was corrected, not the scorer:** R6.2.6, `references/matching-rules.md` §3.6 and `SKILL.md` Mode 1 now state that buyer discovery applies no `HF-xx` rule and excludes only on a query `company_type` mismatch and the DISC-06 evidence gate. `score_buyer.py` also now appends the `--no-hard-filter` note R6.2.6 requires. `expected/` was not touched. | resolved — contract corrected |
| **Q2a** | **`dimension_details` was capped at 12 items** in `buyer.schema.json` and `seller.schema.json`, while a buyer has 15 criteria (3+3+3+3+3) and a seller 16 (3+3+3+4+3), so every scored record failed its own schema. **Resolved:** the cap is now `maxItems: 40` in both schemas — BUILD-CONTRACT 5.5 requires an entry for every criterion, inapplicable ones included. | resolved |
| **Q2b** | **The in-process self-validation saw `Decimal`, not `number`.** `neutral_base` and `penalty_factor` are echoed straight out of the config, where SCORING-CONTRACT 0.1 requires them as `Decimal`; `_common.write_output`'s encoder turns them back into `50` / `0.6`, but the self-validation ran **before** that encoder. **Resolved:** `_common.validate` accepts `Decimal` wherever `number` is allowed, so the self-validation and the written document now agree. | resolved |
| **Q3** | **Did `summary.passed_hard_filter` count HF-00-gated candidates?** It used to (12 passed / 9 excluded on RFQ #134), which broke SCORING-CONTRACT 3.5's `excluded_count == candidates_considered − passed_hard_filter`. **Resolved in favour of the identity:** `score_match.py` takes `passed_hard_filter` **after** the HF-00 rendering gate has moved its candidates into `excluded[]` (`scripts/score_match.py`, the `passed_hard_filter = len(works)` line), so RFQ #134 reports 20 / 11 / 9 and the identity holds exactly. SCORING-CONTRACT 3.5 needed no restatement, and `validate_output.py` now enforces the identity as an **equality** (it previously allowed `excluded_count` to exceed the difference on the strength of a carve-out no contract grants). | resolved — identity is exact |
| **Q4** | **`score_match.py` and `score_seller.py` fire S-PF3 differently.** `score_seller.py` fires `named_product_page` / `catalog_or_pdf_published` from a claim-keyed evidence item (the convention its own header documents); `score_match.py` infers `named_product_page` from any non-empty URL path on the seller's own official tier-1 domain, and `catalog_or_pdf_published` from a text match. SCORING-CONTRACT 2.1 defines one rubric for both ("one rubric, two query surfaces"), so the two must agree. The fixtures carry the claim-keyed form, under which the two currently coincide on every compared candidate; a seller whose product evidence sits on a generic path (`SEL-chorokfactory-example`) would diverge. **Owner: `scripts`.** | reported, not currently failing |
| **Q5** | **`failed_rule.reason` formatting is unpinned.** `score_seller.py` renders `HF-01 Product category sunscreen not covered (handles shampoo, hair_treatment)` while `score_match.py` renders `Product category ['sunscreen'] not covered (handles ['shampoo', 'hair_treatment'])`. BUILD-CONTRACT R6.2.3 fixes the fields and the thousands separator but not list rendering, and 10.3 says the rendered `Excluded` bullet is the `reason_summary` alone — so a `rule_id` prefix inside `reason_summary` double-prints the rule id. The expectations compare the stable core of the message. | reported |
| **Q6** | **RFQ readiness has no home in the match output.** BUILD-CONTRACT 5.4 says `score_match.py` emits the readiness score "into `rfq.readiness_detail`", but the script emits exactly one `match-result` document and `match-result.schema.json`'s `rfq_constraints` has no readiness fields. The harness therefore derives readiness from the RFQ fixture itself (7 of 10 → 70) and additionally checks any readiness block the script chooses to echo. | reported |

---

## 9. Current status

`python3 tests/run_tests.py` → **PASS 536 / FAIL 0 / SKIP 0**, exit 0, in a repository checkout. An installed
copy has no repository manifests, so section 15 reports one SKIP there instead of its repository cases
and section 16 reports M-36 as one SKIP.

Every scored record, every component score, every hard-filter decision, every exclusion, the whole
ordering of all four ranked runs, T01–T10, E01–E19, the eleven field regressions of section 6,
the package and adapter guards of section 10, the calibration cases of section 11, the run-diff
cases of section 12, the re-check queue cases of section 13, the export cases of section 14, the plugin
packaging cases of section 15, the MCP tool server cases of section 16, the v0.3.0 review follow-ups of section 17 and every safety and fixture case pass. Q4–Q6 remain open as documented
divergences; none of them
currently produces a wrong number on the golden set, which is why they are recorded here rather than
absorbed into an expectation.

---

## 10. Package and adapter guards

Two phases run after the safety scan. They protect the invariants that had **no machine half** until
the post-audit pass, each of which had been demonstrated to be bypassable.

| Case | What it asserts | Invariant |
|---|---|---|
| `package: every BUILD-CONTRACT 2.2 shipped file exists` | Every package-relative path in the ownership table is on disk. `templates/legal_notices.md` was contracted, referenced from 15 places in four files, and absent — nothing caught it | BUILD-CONTRACT 2.2 |
| `package: README.md exists at the repository root` | `README.md` and `docs/` are repository files, not package files (2.2 note); the README documents the repo tree and `install.sh` ships only the package folder | BUILD-CONTRACT 2.2 |
| `package: INV-36 SKILL.md names no provider-specific tool, model or API` | The case-insensitive grep list of INV-36 (`WebSearch`, `WebFetch`, `web.run`, `browser.`, `mcp__`, `claude`, `anthropic`, `openai`, `codex`, `gpt-`, `chatgpt`) matches nothing in `SKILL.md` outside a pointer at `references/runtime-adapters.md` | **INV-36** |
| `package: BUILD-CONTRACT 2.2 row 1 SKILL.md frontmatter shape` | Frontmatter keys are exactly `name` + `description`; `name` is the `^[a-z0-9]+(-[a-z0-9]+)*$` slug `kbeauty-trade-matchmaker`, ≤ 64 chars; `description` is one line ≤ 1024 chars | BUILD-CONTRACT 2.2 row 1, INV-36 |
| `package: R7.3.3 a non-UTF-8 input is a clean usage error, not a traceback` | All eight `--input`-taking CLI scripts (the six pipeline scripts plus `make_review_sheet.py` and `export_leads.py`) fed a file with a raw `0xff` byte exit **2** with exactly one `ERROR:` line and empty stdout. `UnicodeDecodeError` is a `ValueError`, so it used to escape `read_input`'s `except (IOError, OSError)` and four scripts died with a bare traceback | R7.3.2, R7.3.3, **INV-35** |
| `package: INV-35 a non-string canonical_domain degrades, never tracebacks` | `score_buyer.py` / `score_seller.py` fed a record whose `canonical_domain` is a list still write a document and one `ERROR:` line. The `excluded[]` sort key used to raise `TypeError` from outside every `try` | R7.3.2, R7.3.3, **INV-35** |
| `adapter: a clean draft is still accepted` | The guards below did not simply break the adapter: a normal draft still stores at `READY_FOR_REVIEW` / `auto_send false` / `manual_approval_required true` | INV-09 |
| `adapter: INV-10/INV-25 a NESTED dispatch or credential block is refused` | `delivery.{transport,recipients,schedule_at}` and `auth_block.{token,password}` are refused with their JSON paths named. The scan used to read only top-level keys, so a nested dispatch instruction and a cleartext credential were written to disk while the run reported "queued for human review" and exited 0 | **INV-10, INV-25** |
| `adapter: INV-25 every dispatch/credential key name is refused at top level` | 19 key names including `to`, `reply_to`, `envelope_from`, `smtp_host`, `send_at`, `webhook_url`, `sendgrid_key`, `api_token`, `bearer` and `cookie` — all of which the original denylist missed | **INV-25** |
| `adapter: T05/INV-34 an uncited live-demand claim is refused (EN / KO)` | A draft saying "we have a buyer currently looking … limited slots, closing soon", or `현재 귀사 제품을 찾는 바이어가 있습니다`, is refused when no `rfq_id` is cited | **INV-34**, R10.4.3, PRD T05 |
| `adapter: INV-34 a demand claim citing an open RFQ … is allowed` | The check is a gate, not a ban: the same sentence passes when `rfq_id` / `rfq_status` (`qualified` / `matching` / `proposal_open`) / `rfq_as_of` are present **and** the status and date are printed in the draft | **INV-34**, R10.4.3 |
| `adapter: R10.4.3 a cited RFQ whose status and date are not stated in the draft is refused` | Citing the RFQ in the record but omitting it from the body is not enough — R10.4.3 requires the draft itself to state the status and the date | R10.4.3 |

`E19` (section 5) covers the third invariant that had no machine half: `validate_output.py` now fails
a document whose candidates cannot render their INV-19 lines. Before it, stripping `website`,
`contact_channels`, `missing`, `company_type` and `oem_odm` off every record left a document that
passed `--strict --invariants` at exit 0 — the shipping gate `SKILL.md` self-check #1 names.

---

## 11. Calibration tooling — the labelled-set loop

`phase_calibration` runs after the field regressions. It covers the two scripts that measure the
rubric instead of computing it: `scripts/make_review_sheet.py` (scored run → blind operator CSV) and
`scripts/acceptance_report.py` (filled CSVs + scored runs → an `acceptance-report` document). Neither
can change a score — no scorer reads them and no scorer reads the `calibration` block of
`scoring.config.json` — so every case here is about **measurement discipline**, not arithmetic. The
protocol they implement is `references/calibration-notes.md` §7.

### Fixtures

| File | What it is |
|---|---|
| `fixtures/reviews.buyers.uae.csv` | The R3 blind sheet (19 returned + 1 excluded, `--include-excluded`) as a trade operator handed it back: 5 `accept`, 6 `reject`, 1 `unsure` on returned records, plus `accept` on the excluded `BUY-quietharbour-example`. `reviewer_role` is the role label `trade operator` on every filled row; no personal data anywhere |
| `fixtures/reviews.match-134.csv` | The same for R6 (10 returned + 9 excluded): 4 `accept`, 3 `reject`, 1 `unsure`, plus `accept` on the `HF-03`-excluded `SEL-daehansuncare-example` |
| `fixtures/expected/review-sheet.buyers.uae.blind.csv` | Golden blind sheet for R3 with `--include-excluded` |
| `fixtures/expected/review-sheet.match-134.ranked.csv` | Golden `--no-blind` sheet for R6 (rank order, `rank,score,qualified` appended) |
| `fixtures/expected/acceptance.buyers.uae.expected.json` | Golden report for R3 + `reviews.buyers.uae.csv` |
| `fixtures/expected/acceptance.match-134.expected.json` | Golden report for R6 + `reviews.match-134.csv` |

Both expected reports were generated by the script and then **recomputed by hand** before being
trusted. The numbers below are those hand checks; if a change makes one of them move, the change is
what needs explaining.

| Report | Hand check |
|---|---|
| Buyers | accept 5, reject 6 → `human_acceptance_rate` = 5 / 11 = **0.4545**; coverage 12 / 19 = **0.6316**; `by_qualified` 4/5 = **0.8** against 1/6 = **0.1667**; overall AUC over accepts {96, 84, 81, 71, 53} vs rejects {82, 63, 57, 53, 36, 32} = (6 + 6 + 5 + 5 + 2.5) / 30 = 24.5 / 30 = **0.8167**, the 2.5 being the 53-vs-53 tie counted 0.5; `kbeauty_fit` AUC = 20.5 / 30 = **0.6833** |
| Match | accept 4, reject 3 → 4 / 7 = **0.5714**; coverage 8 / 10 = **0.8**; AUC over accepts {99, 97, 91, 82} vs rejects {93, 80, 75} = (3 + 3 + 2 + 2) / 12 = 10 / 12 = **0.8333** |
| Recall denominator (both) | Every accepted record, **excluded ones included**. Match: 4 returned accepts + the accepted `HF-03` exclusion = 5, so recall is 4 / 5 = **0.8** at thresholds 50–80 (all four accepted scores 99/97/91/82 clear 80) and 3 / 5 = **0.6** at 85 and 90. Buyer: 5 returned accepts + the accepted `DISC-06` exclusion = 6, so 5 / 6 = **0.8333** at 50, 4 / 6 = **0.6667** at 55–70 (96/84/81/71), 3 / 6 = **0.5** at 75–80, 1 / 6 = **0.1667** at 85 and 90 |

`market_relevance` reports `distinct_values: 2` on the buyer report — the single-country collapse of
`references/calibration-notes.md` §3.1, now visible in a document instead of a prose finding. The
match report groups every candidate under `country: unknown`, because a `match_candidate` carries no
country; that is reported rather than papered over.

### Cases

| Case | What it asserts |
|---|---|
| `calibration: make_review_sheet.py (buyer run, blind) exits 0` · `(match run, --no-blind) exits 0` | Both modes run clean over the R3 and R6 documents |
| `calibration: the blind buyer sheet matches its golden byte for byte` · `the ranked match sheet …` | Byte-for-byte CSV comparison, LF line endings included |
| `calibration: a blind sheet carries no rank/score/qualified column` | The three anchoring columns are absent from the header in blind mode |
| `calibration: no blind sheet cell carries a qualification score` | Stronger than the header check: no cell anywhere holds any of the run's 19 score values, so the score cannot leak through another column |
| `calibration: blind row order is independent of rank` | The blind order is **not** the document's ranked order. A sheet that reproduces the ranking anchors the reviewer even with the numbers hidden |
| `calibration: blind row order is the sha256 order of the record ids` | And it is that specific order, so the shuffle is deterministic (INV-13) rather than merely different |
| `calibration: --include-excluded lists excluded[] and the default does not` | `BUY-quietharbour-example` appears only under the flag; false exclusions cannot be measured without it |
| `calibration: INV-13 make_review_sheet.py is byte-identical on a re-run` | **INV-13** |
| `calibration: acceptance_report.py (buyer / match) exits 0` | Both documents kinds report clean |
| `calibration: the buyer / match acceptance report matches its golden byte for byte` | The full report, every count and every rate |
| `calibration: INV-13 acceptance_report.py (buyer / match) is byte-identical on a re-run` | **INV-13** |
| `calibration: the buyer / match acceptance report validates --strict` | `validate_output.py --schema acceptance-report --invariants --strict` exits 0 against `schemas/acceptance-report.schema.json` |
| `calibration: the report says it measures Human Acceptance Rate only` | `notes[]` names both PRD 17 metrics and says RFQ Conversion is out of reach. A calibration report that does not state its own limit is how one gets quoted as evidence for something it never measured |
| `calibration: a small sample is flagged and refuses to be evidence` | 11 decided reviews against `calibration.min_sample` 30 sets `insufficient_sample: true` **and** writes the note saying the report cannot justify a weight or threshold change |
| `calibration: --min-sample lowers the bar and clears the flag` | The override works and is visible in the summary — lowering the bar is a deliberate, recorded act |
| `calibration: refuses an unknown verdict` | `verdict: maybe` → exit 1 |
| `calibration: refuses an unknown reason_code` | `reason_code: vibes` → exit 1 |
| `calibration: refuses a reject with no reason_code` | A rejection with no reason is a data point nobody can act on |
| `calibration: refuses a malformed reviewed_on` · `an impossible reviewed_on` · `a reviewed_on later than as_of` | `12/09/2026`, `2026-02-31` and a date after the report's `--as-of` are each refused |
| `calibration: refuses an email address in a note` · `a phone number in reviewer_role` | **INV-31.** The review sheet is the one path by which a personal contact could enter the calibration loop; the report refuses the whole sheet rather than aggregating it |
| `calibration: refuses a duplicate record_id with conflicting verdicts` | Two rows for one record saying `accept` and `reject` → exit 1. A duplicate with the *same* verdict is kept once and noted |
| `calibration: refuses a review row that matches no scored record` | The sheet and the run have drifted apart; the join would silently change the denominator |
| `calibration: refuses two score_versions in one report` | The **INV-23** principle applied to a report: scores from two rubrics are not on one scale (BUILD-CONTRACT 12.3 rule 4) |
| `calibration: refuses a discovery and a match document in one report` | Different rubrics on different populations. A buyer population mixed with a seller one is refused by the same check |
| `calibration: refuses one record_id carried by two --scored documents` | The same run passed twice (or a copy under a second name) multiplies every count it appears in and would let 30 decided reviews report as 60, defeating `min_sample`. The check runs **after** the score_version / kind / entity checks, so a rubric mismatch is still named first |
| `calibration: refuses a duplicate record_id with conflicting reason_codes` | Two rows agreeing on `reject` but disagreeing on *why* are as unresolved as two disagreeing verdicts. A duplicate agreeing on both is kept once and noted |

Every refusal additionally asserts the shape of the failure: exit **1**, exactly one `ERROR:` line
(R7.3.3), no traceback, and a message that names the defect rather than the exception.

### Cases from the v0.2.0 review

An independent review of the first cut found that hiding the score was not enough to make a sheet
blind, and that several guards were tuned for the fixture rather than for the field. These cases
pin the repairs; each was mutation-checked (the fix was reverted and the case observed to fail).

| Case | What it asserts |
|---|---|
| `calibration: H1 no column of the blind buyer sheet separates excluded from returned rows` · `… blind match sheet …` | The general property, not the specific leak: for **every** column, the set of cell shapes (`filled` / `unknown` / `not_shown` / `empty`) on the excluded rows must overlap the set on the returned rows. `discovery-result.excluded[]` has no `country` field, so before the fix the excluded rows were exactly the rows whose country cell read `unknown` — the ranking hidden and the exclusions still legible |
| `calibration: H1 the discovery country column is neutralised for every row` | `not_shown` on **all** 20 rows, not only the excluded one — blanking one population is the same tell inverted |
| `calibration: H1 the neutralisation is announced on stderr` | A column the reviewer cannot see must not be a column they are not told about |
| `calibration: H1 without --include-excluded the country column is real` | The neutralisation is scoped: with one population there is nothing to separate, so `AE` / `GB` / `JP` stay. A match sheet keeps its country column too (both populations carry none, so `unknown` separates nothing) |
| `calibration: H3 a formula-leading company name is escaped, not executed` | `=cmd\|'/c calc'!A1` and `@SUM(1+1)*cmd` as `company_name` are written with a leading apostrophe. Names come off harvested public pages; the operator opens the sheet in a spreadsheet |
| `calibration: refuses a record_id that would need a formula guard` | The join key is never rewritten — an apostrophe would break every row of the report — so an id starting with a formula character is refused instead |
| `calibration: M4 recall counts accepted-but-EXCLUDED records in its denominator` | Asserted on a **fresh run**, not on the golden: recall@50 = 0.8 and recall@85 = 0.6 over 5 accepted records of which 4 were returned. Measuring recall against the run's own output could never fall below 1.0 at the bottom of the sweep |
| `calibration: M4 a match report says its sub-threshold sweep rows are unmeasurable` · `… a discovery report does NOT …` | Worded per kind, because the kinds differ: `score_match.py` keeps only `match_score >= threshold` in `results[]`, while `score_buyer.py` / `score_seller.py` return their below-threshold records and merely flag them unqualified |
| `calibration: M5 registration numbers, certificate numbers, year ranges and registry URLs are not read as personal data` | A CDSCO RC number, a BPOM notification number, `active 2019 - 2024, ISO 22716 cert 9001-2015`, a registry URL with a numeric id and `15 000 000 KRW over 2020/2021/2022` all pass. The discovery playbooks tell operators to record exactly these, and a digit-count rule refused them — aborting the whole report over a note the protocol asked for |
| `calibration: refuses an obfuscated email in a note` · `a full-width phone number in a note` · `a phone number glued to a URL in a note` | The other direction: `name [at] company.example` and `ＴＥＬ ０１０１２３４５６７８` are caught. Input is NFKC-normalised first, and a telephone now needs a telephone *signal* (`+`, a trunk `0` on ≥ 9 digits, or a `tel`/`phone`/`전화`-style label) rather than merely being long URLs are scrubbed before the telephone pass so a registry URL with a numeric id passes; a separator-formatted trunk-prefix number or a `tel=`/`phone=` label INSIDE a URL (`www.x.example/010-1234-5678`) is therefore checked first and still refused. |
| `calibration: M6 a record with no qualified flag lands in qualified_unknown, never qualified_false` | "Unknown is not false" is the package's headline rule. Folding an unjudged record into `qualified_false` would invent a negative out of a gap, in the one document whose job is to measure the flag's precision |
| `calibration: M7 a trailing all-empty CSV row is skipped, not fatal` | A sheet with a trailing `,,,,,,,,,` produces byte-identical output to the same sheet without it. A row with content but no `record_id` is still a defect |
| `calibration: L10 a report that fails its schema is not written at all` | Run against a deliberately impossible schema: exit 1, empty stdout, `--output` not created. Unlike a scorer (R7.3.2), a report is a single aggregate that is either trustworthy or not, and a file on disk that failed its own schema is the one most likely to be quoted |
| `calibration: L15 an empty reject class yields a null AUC with a stated reason, and distinct_values is still reported` | An all-accept sheet: `overall.auc` is `null` with the reason naming the empty class, every per-dimension AUC likewise, and `distinct_values` (17 overall, 2 on `market_relevance`) is still counted — the collapse finding does not depend on having both classes |
| `calibration: L15 the no-match fixture really returns nothing` · `… still yields a review sheet of its exclusions` · `… a no-match report succeeds, reports null rates and measures false exclusions only` | The pinned behaviour for a `no_match` match run: **not** a refusal. Every rate is `null`, `false_exclusions` carries the one accepted exclusion, and `notes[]` says the report measures false exclusions only — which on such a run is the one thing worth measuring |
| `calibration: refuses a scored document with no record at all` | The boundary of the above: no returned record *and* no excluded record means there was never anything for a sheet to label |

One case in section 10 grew out of this work:

| Case | What it asserts | Invariant |
|---|---|---|
| `package: BUILD-CONTRACT 12.1 skill_version agrees everywhere it is stated` | The `skill_version` in the **body** of `SKILL.md`, `_common.SKILL_VERSION` and `adapters/tradewith_adapter.py`'s `SKILL_VERSION` are one value. Nothing checked this before, so a bump that reached two of the three would have shipped a package reporting a version it was not | BUILD-CONTRACT 12.1 |

---

## 12. Run diff — comparing two scored runs

`phase_run_diff` runs after the calibration phase. It covers `scripts/diff_runs.py`, which reads two
scored `discovery-result` or `match-result` documents and writes one `run-diff` document
(`schemas/run-diff.schema.json`). Like the calibration scripts it changes no score, so the cases are
about **pairing, refusal and privacy discipline**, not arithmetic. The field notes are
`references/data-contract.md` §9.4.

The inputs are scored in-phase from the existing fixtures and held in temp files: B1 = the UAE buyer
run at `AS_OF`, B2 = the same at `2028-03-01`, B3 = the UK sunscreen query at `AS_OF`, BR = B1 with
`--records-only`, S1 = the seller run, M1 = match-134, M2 = match-134 with `rerank-134.json`, MN =
the no-match RFQ (`rfq_id` 901), MT = match-134 with `--threshold 90 --top 3`.

### Fixtures

| File | What it is |
|---|---|
| `fixtures/expected/diff.buyers.uae.asof.expected.json` | B1 → B2, **no `--as-of`**: the diff defaults to the later input date (2028-03-01). This is the one run in the suite that does not pass `AS_OF`, because an explicit `--as-of` earlier than an input run is refused by design |
| `fixtures/expected/diff.match-134.rerank.expected.json` | M1 → M2 at `AS_OF` |
| `fixtures/expected/diff.buyers.uae-uk.expected.json` | B1 → B3 at `AS_OF` |

All three were generated by the script and then **checked field by field** against the two input
runs before being trusted:

| Golden | Hand check |
|---|---|
| UAE as-of | 20 paired (19 returned + 1 excluded), 17 changed, 3 unchanged (`palefade`, `northgateimport`, the excluded `quietharbour`). Every score falls (17 down, 0 up). Qualified lost on exactly `luminaglow` 71 → 69 and `lioncitybeauty` 70 → 69 at threshold 70. `kantoimport` confidence 0.41 → 0.15, delta **-0.26**; `gulfglow` 96 → 93 with `sourcing_intent` 100 → 92 and `evidence_quality` 94 → 85. No Missing-line change |
| Match rerank | 19 paired, 2 changed: `hanbitcos` score 97 → 99 and rank 2 → 1 with **no** `base_score` key (its base did not move), `hansolodm` rank 1 → 2 with no score key. `weights_changed` false, `query_changed` null |
| UAE vs UK | `query_changed` true; 15 changed, 11 score changes (7 up, 4 down), 9 Missing-line changes (`Requested category not evidenced: sunscreen` added on 8 records, `Category match against the request` added on `kantoimport`, nothing removed), qualified gained on `palefade` 57 → 72 and `novadrift` 63 → 74, lost on `dunesourcing` 84 → 69 |

### Cases

| Case | What it asserts |
|---|---|
| `run diff: <golden> exits 0` · `matches <file> byte for byte` · `INV-13 … is byte-identical on a re-run` · `passes validate_output --schema run-diff --strict` | The three goldens above, each four ways |
| `run diff: a run diffed against itself reports no change` | Identity: every list empty, every count 0, `paired == unchanged == 20`, no note |
| `run diff: swapping --before and --after negates every score delta and swaps qualified gained / lost` | Antisymmetry, plus the note that `--before` is dated later than `--after` |
| `run diff: changed[] is ordered by \|score delta\| desc, then record_id` | The documented sort, never dict or set order |
| `run diff: re-scoring at 2028-03-01 loses qualified on exactly luminaglow and lioncitybeauty` · `a confidence delta is computed in Decimal` · `the diff is dated at the later input run …` | The as-of golden's key facts, asserted on the parsed document |
| `run diff: the rerank moves two ranks, and base_score is absent where the base did not change` | A key is present only when that field changed |
| `run diff: the listed / not-listed note is on a match diff only` | `score_match.py` lists only results above the threshold and within `top_n`, so a match `gone` can mean *cut*, not *dropped*; the note says so on every match diff |
| `run diff: a discovery diff has rank_changed null …` · `a changed query is flagged and Missing-line changes are reported` | Inapplicable fields are `null`, not 0; a widened query is reported, not refused |
| `run diff: no contact channel, evidence, website or observed value is copied into a diff` | No key named `contact_channels`, `evidence`, `website`, `observed_value`, `required_value` or `failed_rules` anywhere in the three goldens |
| `run diff: --before - reads stdin and yields the same diff` | One side may come from stdin |
| `run diff: a record missing from --after is gone …` · `… missing from --before is new` | Mutation (a) |
| `run diff: returned -> excluded is newly_excluded with the failed rule ids only` · `excluded -> returned is newly_returned with the old rule ids` | Mutation (b): only `HF-nn` ids travel, never the rule objects |
| `run diff: an exclusion whose rule changes is an exclusion_change with rules_added / rules_removed` | Mutation (c) on M1: `HF-02` → `HF-03` |
| `run diff: a re-keyed record pairs through merged_from and an absorbed one is gone with merged_into` | Mutation (d): the re-keyed pair is listed in `changed[]` with `matched_by: merged_from` even though its numbers are equal, and the absorbed record carries `merged_into` |
| `run diff: an id renamed without merged_from is gone + new, not paired` | Mutation (e): the diff never guesses a pairing, not even by `canonical_domain` |
| `run diff: a record with id "unknown" is listed as new, never paired, with a note` · `a --before record with id "unknown" is listed as gone …` | Mutation (f), on each side; the note names the side |
| `run diff: a before record whose merged_from names the after id pairs through merged_from` | The reverse `merged_from` pass: before X lists Y, after carries Y. Excluded entries carry no `merged_from`, so an excluded-excluded pair is only ever joined by id |
| `run diff: a base_score change is reported with its delta` | M1 with `results[0].base_score` lowered by 1: `base_score` is listed, `score` is not |
| `run diff: a changed weights_used sets context.weights_changed` · `a different skill_version is echoed and noted` · `an empty skill_version on an input reads as unknown, not a failed diff` | Run-level context and notes; `skill_version: ""` passes the input schemas, so it must not fail the diff's own `minLength` |
| `run diff: a changed threshold is flagged and cut sellers are gone with their rank` | M1 → MT: `threshold_changed` true, and every returned seller in `gone` carries its before rank |
| `run diff: a Missing-line change lists added / removed labels in order` | Mutation (g) |
| `run diff: a partial run is echoed and noted` | `partial: true` reaches `after.partial` and `notes[]` |
| `run diff: a real dedupe merge reads as merged_into, not as a lost lead` | The raw B1 against `dedupe_companies.py` → `score_buyer.py`: `BUY-www-luminaglow-example` is gone with `merged_into: BUY-luminaglow-example`, nothing is new |
| `run diff: refuses two runs scored with different score_versions` | **INV-23**, BUILD-CONTRACT 12.3 rule 4. There is no opt-in flag |
| `run diff: refuses a run whose records disagree with its envelope` · `an unscored run` · `an input that fails its own schema` | Each input is schema-checked (discovery records against `buyer` / `seller`) and must carry one `score_version` |
| `run diff: refuses a discovery run against a match run` · `a buyer run against a seller run` · `match runs for two different RFQs` | Different rubrics, populations or questions |
| `run diff: refuses a bare --records-only array` · `a record id that appears twice in one run` · `an empty object` · `an --as-of earlier than an input run` · `a match record whose rank is not its position` | The remaining refusals. The diff reports list position as rank, so a present `rank` that is not index + 1 is refused, never silently overridden |
| `run diff: usage error (missing --after)` · `(malformed --as-of)` · `(empty --as-of)` · `(non-UTF-8 input)` · `(both sides on stdin)` | Exit **2**, one `ERROR:` line, no traceback, empty stdout. `diff_runs.py` takes no `--input`, so it is not in `CLI_SCRIPTS`; its non-UTF-8 case lives here |
| `run diff: a diff that fails its schema is not written at all` | Against a deliberately impossible `run-diff` schema: exit 1, empty stdout, `--output` not created |
| `run diff: --version exits 0 and names the three versions` | The standard version line |

Every refusal additionally asserts exit **1**, exactly one `ERROR:` line (R7.3.3), no traceback, a
message naming the defect, and an **empty stdout** — unlike a scorer (R7.3.2), a refused diff writes
nothing.
## 13. Re-check queue — which stored evidence to re-read

`phase_recheck` runs after the calibration phase. It covers `scripts/stale_evidence.py`, which reads
records already on disk and emits a `recheck-queue` document (`schemas/recheck-queue.schema.json`):
the records, material claims and evidence items to re-read, most urgent first, measured to the
required `--as-of`. It fetches nothing and changes no record or score; no scorer reads it
(INV-NEW-stale). The reason codes are `references/evidence-policy.md` §5.6.

### Fixtures

No new input fixture. The two goldens are generated from the existing bundles at `--as-of
2026-09-12`:

| File | What it is |
|---|---|
| `fixtures/expected/recheck.buyers.golden.expected.json` | Queue over `buyers.golden.json`: 3 of 20 records, 17 of 144 items |
| `fixtures/expected/recheck.sellers.golden.expected.json` | Queue over `sellers.golden.json`: 3 of 20 records, 19 of 171 items |

### How the goldens were checked by hand

| Record | Why it is (or is not) queued |
|---|---|
| `BUY-northgateimport-example` | Position 1: `operational_status: unreachable` → `site_unreachable`. Every material item is dated 2024-08-14, 759 days before as_of and past `stale_threshold_days` 730 → `past_stale_threshold`, so all seven covered material claims are `no_current_evidence`. EV-006 (`operational_status`, not material, `stale: true`) ranks last |
| `BUY-palefade-example` | Record `stale: true` → `record_flagged_stale`; every item is 2023-01-05 (1346 days) and flagged, so each carries `past_stale_threshold` + `source_flagged_stale` |
| `BUY-kantoimport-example` | EV-002 (2026-02-14, 210 days, `current`) and EV-003 (2025-06-01, 468 days, `aging`) conflict on `country` with no `conflicts[]` entry → both `unresolved_conflict`. EV-003 gets **no** age reason: `country` already has a current item (EV-002), so the claim is not `no_current_evidence` |
| `BUY-pacificglowdist-example` | Not queued. Its `company_type` conflict is resolved in `conflicts[]`, and the older EV-009 (569 days) is superseded by the current EV-001 |
| `BUY-dunesourcing-example` | Not queued. EV-009 (2025-09-01, 376 days) is `aging`, but `company_type` also has the current EV-001 |
| `BUY-gulfglow-example` | Not queued. EV-004 is 312 days (`current`). EV-008 is undated but was read on 2026-09-10, 2 days before as_of, so it is current by its reading |
| `SEL-sopoongworks-example` | Position 1: `site_unreachable`; every item 2025-05-04 (496 days) → `aging`; EV-1409 also `source_flagged_stale` |
| `SEL-areumfactory-example` | `record_flagged_stale`; items 2023-03-12 (1280 days) → `past_stale_threshold` + `source_flagged_stale`; the non-material EV-1509 → `past_stale_threshold` only, ranked last |
| `SEL-yeonhwalab-example` | `certifications` rests only on EV-305 (2024-11-20, 661 days, `aging`) → `no_current_evidence`, counts `{old 1, undated 0, flagged 0}` |
| `SEL-hanbitcos-example` | Not queued: its undated `moq` item was read 2026-09-10 |

### Cases

| Case | What it asserts |
|---|---|
| `recheck: stale_evidence.py (buyer / seller bundle) exits 0` · `the buyer / seller queue matches its golden byte for byte` | Byte-for-byte JSON comparison |
| `recheck: INV-13 the buyer / seller queue is byte-identical on a re-run` | **INV-13** |
| `recheck: the buyer / seller queue passes validate_output.py --strict` · `validate_output.py --schema auto routes a queue to recheck-queue` | `report_kind: recheck-queue` is the discriminator |
| `recheck: an unreachable buyer is queued first …` · `evidence older than the stale threshold is past_stale_threshold …` · `a record flagged stale is record_flagged_stale …` · `an unresolved conflict queues both sides …` | The hand checks above |
| `recheck: a resolved conflict, a superseded old item, fresh evidence and an undated item read recently queue nothing` | pacificglowdist, dunesourcing, marinaretail and gulfglow are absent. Old items that no longer drive the score are not worth a trip |
| `recheck: undated items read recently are counted and named in notes[]` | Undated evidence cannot flood the queue: Korean company pages almost never print a date (`evidence.unknown_source_date_note`), so an undated item is current while its `observed_at` is, and the count is still reported |
| `recheck: an unreachable seller …` · `a seller flagged stale …` · `a material claim resting only on aging evidence is no_current_evidence` · `a seller whose undated items were read recently is not queued` | The seller hand checks |
| `recheck: the queue copies no value, quote_or_summary or contact channel` | Only locators and dates leave the record |
| `recheck: --as-of alone drives age …` | At 2027-09-12 gulfglow EV-004 is `aging` and the undated EV-008 (read 367 days earlier) is `undated` |
| `recheck: an undated item last read long ago is queued as undated` | EV-008 with `observed_at` 2025-01-10 → `undated`, and `contact_channels` is `no_current_evidence` with `newest_source_date: unknown` |
| `recheck: a scored discovery-result is accepted and notes its excluded[]` | The UAE run: its queued ids are a subset of the bundle's, and `notes[]` says the excluded candidate carries no evidence |
| `recheck: a match input is accepted and its RFQ is scanned too` | `records_scanned` = 20 records + 1 RFQ |
| `recheck: an RFQ is classified as an RFQ …` | `rfq.134.json` carries `buyer_id`; it is still entity `rfq` with `record_id` `134`, and a `stale`/`unreachable` set on it adds no record-level reason, because no scorer penalises an RFQ for them |
| `recheck: --top lists the first N and the summary still counts all` | `--top 1` on the seller bundle lists sopoongworks only; `records_queued` stays 3 |
| `recheck: records scored under an older rubric are queued, not refused` | `score_version: kbtm-score-0.0.9` → exit 0 and a note. The queue aggregates no score, so the INV-23 refusal does not apply; re-checking old records is the point |
| `recheck: an empty records[] is an empty queue, exit 0` · `--output writes the same bytes and leaves stdout empty` | |
| `recheck: refuses a match-result` · `an acceptance report` · `a duplicate record id` · `a record without an id` · `an --as-of earlier than an observed_at (INV-24)` | Exit 1, one `ERROR:` line, empty stdout |
| `recheck: a missing --as-of` · `a malformed --as-of` · `--top 0` · `a config without the due bucket` | Exit 2. `--as-of` is required: falling back to the newest `observed_at` would make every item look as fresh as its last reading |
| `recheck: age 730 (= stale_threshold_days) is aging, 731 is past_stale_threshold; 365 is current and 366 is aging` | Both edges are inclusive, as in the scorer: an item exactly `stale_threshold_days` old is not yet past it, and the first bucket whose `max_age_days` is `>=` the age wins (`evidence.recency_note`) |
| `recheck: an undated item with no observed_at is queued as undated, and a flagged undated item is source_flagged_stale only` | A reading with no usable date cannot prove the page current. A flagged item already has a reason, so `undated` is not added to it |
| `recheck: claim counts.old counts only items in a due bucket` | `country` with one fresh flagged item (10 days) and one 800-day item → `{old 1, undated 0, flagged 1}` |
| `recheck: records tied on priority rank by more claims without current evidence, then by the oldest queued item` | Four `aging` records: two claims beats one, then 600 days beats 500 beats 400, whatever the id order |
| `recheck: a 200-character claim (the evidence schema's limit) is queued, and an over-long company_name is left out instead of failing the run` | The queue's `claim` limit matches `evidence.schema.json` (200), so valid input can never fail the queue's own schema. A display label over its limit is dropped; the id still names the record |
| `recheck: refuses a claim longer than the input schema allows` | Exit 1. An over-long id, `evidence_id`, `claim`, `source_url` or `conflicts_with` id is refused, not cut: a cut locator points somewhere else |
| `recheck: 320 unreadable source_dates, 12 non-list evidence values and over-long echoed values make one note of at most 1000 characters per kind` | Worst case with 128-character ids: tolerated bad data is summarised once per kind (count + first 10 cases, each echoed value at most 40 characters), so `notes[]` stays inside its 200-item / 1000-character schema limits |
| `recheck: refuses a missing / integer / blank buyer_id even when the bundle names the entity` | Exit 1. Every queued record must be findable again; no record is queued as `unknown` |
| `recheck: a non-string conflicts[].field is ignored, and a string conflicts_with is named on the item it flags` | A list `field` used to crash with an unhashable-type error. A bare string `conflicts_with` counts as one id, so the item that raises `unresolved_conflict` always says what it conflicts with |
| `recheck: --top with a non-ASCII digit is a usage error (exit 2)` | `"²".isdigit()` is true but `int("²")` fails; the flag is checked as ASCII digits |
| `recheck: a non-UTF-8 input is reported as such even without --as-of` | The input is read before the flag check, so the R7.3.3 harness check reaches the UTF-8 error |
| `recheck: INV-NEW-stale no scoring path reads the re-check tool` | No scorer, `normalize_company.py`, `dedupe_companies.py` or `_common.py` names `stale_evidence`, its constants or (outside `_common.SCHEMA_NAMES`) `recheck-queue` |
## 14. Lead export — `export_leads.py`

`phase_export` runs after the calibration cases. It covers `scripts/export_leads.py`, which writes
one scored `discovery-result` as a generic CSV (buyers or sellers), as the CSV TradeWith's admin
buyer-import page reads, or as the admin bulk-import JSON body (buyers only). The script writes a
file and nothing else, and no scorer reads what it writes, so every case here is about **what may
leave the package**, not about arithmetic. The contract is `references/data-contract.md` §9.6.

Inputs are the R3 UAE buyer run (19 records, 7 qualified, `BUY-northgateimport-example`
unreachable) and the R4 sunscreen seller run (13 records, all qualified,
`SEL-sopoongworks-example` unreachable, `SEL-hwadamglobal-example` country `unknown`), both
re-scored at `--as-of 2026-09-12`.

### Fixtures

| File | What it is |
|---|---|
| `fixtures/expected/export.buyers.uae.csv` | Default generic CSV of R3: the 7 qualified buyers in document order |
| `fixtures/expected/export.buyers.uae.tradewith.json` | `--format tradewith-json --pretty` of R3: 7 rows, countries AE / JP / GB / US / SG, `sourceId` `kbtm:<domain>`, `stale=false` in every `originalSource`, no `@` anywhere and one organisation-page `social` (sakura) |
| `fixtures/expected/export.buyers.uae.tradewith.csv` | `--format tradewith-csv` of R3: the same 7 rows, five columns (`sourceId, companyName, country, website, industry`) |
| `fixtures/expected/export.sellers.csv` | Default generic CSV of R4: 12 rows, the unreachable maker skipped, `hwadamglobal` kept with `country=unknown` |

### Cases

| Case | What it asserts |
|---|---|
| `export: X1 buyer csv …` · `X2 buyer tradewith-json …` · `X3 seller csv …` · `X4 buyer tradewith-csv …` | Each exits 0, matches its golden byte for byte, and a second run is identical (INV-13). X1 holds exactly the qualified records in document order, LF only, header first; X3 skips the unreachable maker, keeps the country-unknown one as the literal `unknown`, and names `operational_status=1` on stderr |
| `export: X4 the admin import page's parser reads the tradewith-csv into exactly the tradewith-json rows` | The page's `parseCSV` + `mapRowToPayload` are transcribed into the harness; parsing the CSV yields the JSON rows projected onto its five columns. A comma inside a company name survives that parser. stderr notes that `tradewith-csv` drops provenance and points at `tradewith-json`; `tradewith-json` prints no such note |
| `export: X5 …` | The body passes `tradewith-bulk-buyers.schema.json`; its only top-level key is `buyers`; every row's keys are a subset of the 25 BulkBuyerRowDto keys; no row carries `contactName`, `contactEmail`, `contactPhone`, `notes`, `extraNotes`, `qualityTierLabel` or `hsCodes`; no value is `null`, `""`, `"unknown"` or `[]`; no text value trips the personal-data scan; no `@` appears anywhere in the body; provenance and `stale=false` ride in `originalSource`, and `sourceUrl` is set |
| `export: X6 …` | `sourceId` is `kbtm:` + the `www.`-stripped `canonical_domain`, unique. `--include-unqualified` is **refused** (exit 1) because `BUY-luminaglow-example` and `BUY-www-luminaglow-example` share `kbtm:luminaglow.example`; with the `www.` twin removed, the wider export carries the byte-same row for every record the default export has |
| `export: X7 …` | `--include-unqualified` gives 18 rows (19 minus the unreachable one); `--min-score 80` gives 5; `BUY-kantoimport-example` has `product_categories=unknown` while `BUY-straitswholesale-example` has `contact_channels=none`; no `excluded[]` id appears in any export |
| `export: X8 a dropped market_relevance is the literal not_applicable` | Unknown and not-applicable are different statements (INV-02) |
| `export: X9 …` | A `=HYPERLINK(…)` company name is apostrophe-guarded in the generic CSV, kept raw in the JSON body (not a spreadsheet), and **refused** in `tradewith-csv`, where an apostrophe would be imported as part of the name |
| `export: X10 …` | No address of any kind — role mailbox included — reaches a TradeWith row. In the generic CSV, `jane.doe@` on the company domain, `info@` on another domain and `sales@gmail.com` are dropped with `withheld 3 corporate_email` on stderr, while `partners@luminaglow.example` is kept; `sales@gmail.com` is withheld even when `gmail.com` is the record's own `canonical_domain` |
| `export: refuses … (exit 2)` | A seller run as `tradewith-json` or `tradewith-csv`; `--pretty` with csv; `--min-score 101` and `abc`; `--as-of 2026-13-01`. Exactly one `ERROR:` line, empty stdout |
| `export: refuses … (exit 1)` | An unscored run; a record whose `score_version` differs; a duplicated record id; an address in a company name (JSON and CSV); a phone number inside a website URL; under `--no-validate`, a non-string `canonical_domain`, a non-list `product_categories`, a non-object contact channel and a non-object record (typed guards, no traceback); a schema-invalid record without `--no-validate`; a `match-result` and an RFQ document (parse, but not a scored `discovery-result`: BUILD-CONTRACT 7.3). Each: one `ERROR:` line, empty stdout, and the `--output` file is **not** created |
| `export: X13 …` | `--min-score 100` exits 0 with a header-only CSV or `{"buyers":[]}` and `WARNING: no record passed the filters` |
| `export: X14 a body over TradeWith's JSON limit exits 0 with a WARNING` | 260 rows exceed the ~100 KB default body limit; the export still succeeds and says to split it |
| `export: X15 …` | Under `--include-unqualified`, `BUY-luminaglow-example` and `BUY-www-luminaglow-example` are named in a `possible duplicate` WARNING; the default export warns of none |
| `export: X16 --output writes the file and stdout stays empty` | The file equals the stdout export byte for byte |
| `export: X18 …` (review follow-ups) | A qualified buyer with country `unknown` is skipped and counted (`country_unknown=1`) in `tradewith-json` and kept as `unknown` in the CSV; a `"` in a name is refused in `tradewith-csv`; a `partial: true` input exports with a `WARNING`; a LinkedIn `/in/` profile never becomes `social` and is withheld from the CSV too (`withheld 1 linkedin`); a `messenger` number `+971 4 555 0111` exports in the CSV, while an address in a `messenger` or `phone` channel is refused; a NaN `qualification_score` is refused with and without `--min-score`; a `company_type` outside the TradeWith enum is refused by the output schema even under `--no-validate`; an unknown domain gives `kbtm:id:<buyer_id>` and `stale: true` gives `stale=true` |
| `export: X17 --version prints the standard version line` | `export_leads.py skill_version=… schema_version=0.1.0 score_version=kbtm-score-0.1.0` |

`export_leads.py` also joins the non-UTF-8 input case of section 10, and
`tradewith-bulk-buyers.schema.json` joins the schema self-check (parses, every `$ref` resolves,
keyword subset only).
## 15. Plugin packaging

Phase `plugins` (`phase_plugins`, run after the package guards). It reads files at the
**repository** root, outside the package: `.claude-plugin/marketplace.json`,
`packaging/openai/plugin.json` and `tools/build_release.py`. An installed copy (a skills
directory, the claude.ai sandbox, a plugin cache) has none of them, so the phase records one SKIP
there and the PASS count of section 9 is a checkout count. In a checkout without `.git` (a source
tarball) P-07..P-10 collapse into one SKIP, because the builder archives committed files only.
P-07..P-10 never run the builder on the working checkout: the builder refuses a dirty or untracked
package on purpose, and the suite must not depend on commit state (a stray `out.json`, a feature
not yet committed). They copy every git-tracked package file plus every `MANIFEST` file, as they
are on disk, together with `tools/build_release.py` and both manifests, into a throwaway
repository, commit it with user and system git config shut out, and run the copied builder
there. Each refusal runs in its own `git clone` of that repository with one mutation.
Expected values come from the package itself: `skill_version` from `_common.SKILL_VERSION`, the
plugin name from the `SKILL.md` frontmatter, the archive allowlist from `MANIFEST`
(BUILD-CONTRACT 2.2). Every case was mutation-checked by breaking the manifest it guards.

| Case | What it asserts |
|---|---|
| `plugins: P-01 marketplace.json lists the package folder as one single-skill plugin` | Known top-level and entry keys only; kebab-case `name`; non-empty `owner.name`; exactly one plugin whose `name` is the `SKILL.md` name, `source` is `./kbeauty-trade-matchmaker`, `strict` is `false` and `skills` is `["./"]`. With `strict: false` the entry is the whole definition, so the package needs no `plugin.json` and the claude.ai ZIP keeps its v0.2.0 shape |
| `plugins: P-02 BUILD-CONTRACT 12.1 every plugin manifest states skill_version` | The marketplace entry's `version` and `packaging/openai/plugin.json`'s `version` equal `_common.SKILL_VERSION`. A Claude Code user receives an update only when the entry's `version` changes, so a forgotten bump strands them |
| `plugins: P-03 the package root holds no plugin manifest or component dir` | None of `.claude-plugin`, `.codex-plugin`, `.agent-plugin`, `plugin.json`, `.mcp.json`, `mcp.json`, `.app.json`, `skills`, `agents`, `commands`, `hooks`, `bin`, `workflows`, `output-styles`, `monitors` exists in the package. Any of them would change plugin discovery and leak into the skill ZIP |
| `plugins: P-04 the marketplace entry declares the stdio MCP server exactly when the package ships scripts/mcp_server.py` | The pairing goes both ways: `mcpServers` present requires `scripts/mcp_server.py` on disk **and** in `MANIFEST`, and a shipped script requires the declaration. The server is one entry, `command` `python3`, `args` exactly `["${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py", "--root", "${CLAUDE_PROJECT_DIR}"]`, no `url`/`env`/`headers`, stdio only. The case also self-checks the rule on six synthetic entries (declared and shipped, neither, script missing, script not in `MANIFEST`, shipped but undeclared, no `--root`), so it cannot pass vacuously while no server exists |
| `plugins: P-05 the OpenAI manifest is a skills-only portable plugin for this skill` | `$schema` set; `name` equals the skill name; non-empty `description`, `author.name` and `interface.displayName`; only the `com.openai` extension; no `skills`, `apps`, `hooks`, `mcpServers`, `screenshots`, `license` or `email` key anywhere. Structural only: the portal's length limits and category list are vendor policy, checked by hand at release (`references/runtime-adapters.md` §5.5 g) |
| `plugins: P-06 manifests carry no personal contact and are UTF-8/LF` | Both manifests: LF with one final newline, no trailing whitespace, no `auto_send`, no `email` key, and `_common.personal_data_hits` finds nothing in any non-URL string |
| `plugins: P-07 the skill ZIP holds exactly the 2.2 manifest under one top folder` | `build_release.py --list` exits 0 with empty stderr in the fixture repository. Every skill-ZIP entry sits under `kbeauty-trade-matchmaker/`, `SKILL.md` is there, no `__pycache__`/`.pyc`/`.DS_Store`/`.omc/`/adapter data. Allowlist both ways: every `MANIFEST` file is archived, and every archived file (so every git-tracked package file) is a `MANIFEST` row or under `tests/fixtures/`. A `MANIFEST` file nobody `git add`ed is caught at release time instead, where the builder refuses the untracked file |
| `plugins: P-08 the plugin ZIP is plugin.json plus the same files under skills/<name>/` | Archive root is exactly `plugin.json` and `skills/`; everything else is under `skills/kbeauty-trade-matchmaker/`; no `mcp.json`, `.mcp.json` or `.app.json` at the plugin or skill root; no `..`, absolute path or path over 20 segments; the package file set equals the skill ZIP's |
| `plugins: P-09 INV-13 identical input builds byte-identical archives; --as-of sets every entry date` | Two `--list` runs print identical bytes; two `--out` builds into separate temp dirs report identical sha256, and each reported sha256 matches the file on disk; in both ZIPs every entry is dated 1980-01-01 00:00:00 with mode 0755 for directories and `install.sh`, 0644 otherwise; the written order equals the `--list` order. A third build with `--as-of 2026-09-19` dates every entry of both ZIPs 2026-09-19 00:00:00, reports that timestamp and gives different bytes |
| `plugins: P-10 every refusal exits cleanly, only committed bytes ship, and --out never writes through a symlink or half a pair` | Every refusal gives the expected exit, exactly one `ERROR:` line naming the cause, empty stdout and no traceback. **Exit 2** (usage, BUILD-CONTRACT 7.3): neither `--out` nor `--list`; both; a missing `--out` dir; `--out` inside the package; a malformed `--as-of`; an impossible `--as-of 2026-02-31`; a committed manifest that is not JSON; a manifest missing from HEAD. **Exit 1**: an untracked package file; an edited, a deleted, and an edited-manifest uncommitted change; an empty `agents/` dir in the package; a marketplace `version` that drifts; a list-valued `version`; a committed symlink. A tracked file edited under `git update-index --assume-unchanged` still ships its committed bytes (the builder reads blobs from HEAD). With the plugin target pre-created as a directory the run exits 1 and writes neither ZIP. No refused run leaves a file in `tests/`. A pre-existing symlink at the target path is replaced by the archive and its target stays untouched; no temp file is left behind |
| `plugins: P-11 tools/*.py pass the send, clock, placeholder, stdlib and network scans` | The `scripts/*.py`-only scans of section 5 (`SEND_PATTERNS`, `WALL_CLOCK_PATTERNS`, `PLACEHOLDER_PATTERNS`, `STDLIB_OK`, and the shared `_network_problem` scan: no network module, no `urllib.request` in any spelling, no `__import__`/`importlib`, and `subprocess` only in `build_release.py`) and a compile check, applied to the repository's `tools/` directory, which the package-wide walk never reaches |

Every builder refusal in P-09 and P-10 was mutation-checked on 2026-09-19: disabling the untracked,
uncommitted-change, component, version-drift, version-type, symlink, calendar-date, target and
missing-manifest checks, ignoring `--as-of`, reading bytes from the working tree instead of HEAD,
flattening the mode table, or mapping manifest errors to exit 1 each turns P-09 or P-10 red.

---

## 16. MCP tool server — `mcp_server.py`

`scripts/mcp_server.py` serves the eleven deterministic scripts as tools over stdio JSON-RPC
(`references/runtime-adapters.md` §5.6). The `mcp` phase drives it offline with scripted stdin
transcripts. Fixture paths are absolute and machine-specific, so the transcripts are built in code
inside a throwaway `--root` that holds copies of `tests/fixtures/*` plus three scored runs the CLI
produces first (UAE buyers, UK buyers, match-134 with the rerank). Every tool result is compared
with the direct CLI run of the same script and, where one exists, with the existing golden; the
only new golden is `expected/mcp.tools-list.expected.json`, the exact `tools/list` result bytes.
One read-only transcript (M-01..M-24) runs twice with `--quiet --max-inline-bytes 16777216`; the
write cases (M-25..M-32) run once with the defaults. The three stdin-isolation, path and overwrite
guards were mutation-checked on 2026-09-19: letting a child inherit stdin, dropping the
root check on reads or dropping the no-overwrite check each turns this phase red. M-37..M-45 cover
the two 2026-09-19 review passes; each of their twelve fixes was reverted on its own and each revert
turned at least one of them red (the per-frame catch-all in `serve` is defence in depth behind M-38
and has no case of its own).

| Case | What it asserts |
|---|---|
| `mcp: M-01 initialize echoes 2025-11-25 …` | `protocolVersion` echoed, `capabilities` exactly `{"tools": {}}`, `serverInfo.version` equals `_common.SKILL_VERSION`, `instructions` present, no `resultType` on a legacy result |
| `mcp: M-02 version negotiation …` | `2025-11-25`, `2025-06-18`, `2025-03-26` and `2024-11-05` are each echoed; `1999-01-01` and `2026-07-28` sent through `initialize` are answered with `2025-11-25`, never an error |
| `mcp: M-03 ping answers {} and notifications get no response` | `notifications/initialized`, an unknown notification and `foo/bar` sent as a notification produce no line; every output line carries an `id` |
| `mcp: M-04 a malformed line is -32700 and a batch or a bad id is -32600, each with id null` | Exactly five `id: null` errors: garbage and a bare `NaN` (-32700), a batch array, `id: true`, `id: 1.5` (-32600) |
| `mcp: M-05 a bad jsonrpc keeps the request id; an unknown method is -32601` | `"jsonrpc": "1.0"` with id 601 answers -32600 with id 601; `foo/bar` with an id answers -32601 |
| `mcp: M-06 an unknown tool, non-object params and non-object arguments are -32602` | `send_everything`, `params: [...]`, `arguments: "x"` |
| `mcp: M-07 a stray response is ignored and requests after errors are still answered` | A `{"id": 607, "result": {}}` message gets no reply; the final ping is answered |
| `mcp: M-08 the 2026-07-28 path …` | `server/discover` returns `resultType: complete`, `supportedVersions: ["2026-07-28"]`, `capabilities`, `_meta` serverInfo; `tools/list` with `_meta` adds `resultType`, `ttlMs: 300000`, `cacheScope: public` and the same tools; `_meta` version `2099-01-01` is -32022 with `data.supported`; `_meta` without client capabilities is -32602; a modern `tools/call` carries `resultType`; the legacy `tools/list` carries only `tools` |
| `mcp: M-09 stdout carries only JSON-RPC frames …` | Every stdout line is one `jsonrpc: "2.0"` object, stderr is empty under `--quiet`, end of input exits 0 |
| `mcp: M-10 tools/list names the package scripts in pipeline order …` | Names equal `MCP_TOOL_NAMES` in order, match `^[A-Za-z0-9_.-]{1,128}$` and contain none of send, mail, fetch, http, post, dispatch, upload |
| `mcp: M-11 every inputSchema is a closed object … honest annotations` | `type: object`, `additionalProperties: false`, `as_of` required, no top-level `oneOf`/`anyOf`/`allOf`, every keyword in the `_common.validate` subset, every `*_path` a string; all four hints set, `openWorldHint` and `destructiveHint` false, `readOnlyHint` and `idempotentHint` true exactly when no `output_path` is offered |
| `mcp: M-12 the validate_output schema enum equals validate_output.KINDS + auto` | The server pins the enum instead of importing a script per request; this keeps the two in step |
| `mcp: M-13 tools/list is byte-identical to mcp.tools-list.expected.json` | The raw response bytes, so any tool, schema or annotation change is a deliberate golden update |
| `mcp: M-14 every tool returns exactly what its CLI prints (12 calls)` | normalize (`--envelope`), dedupe (`--no-strict-country`), score_buyer (`--top 5`), score_seller (`--threshold 60 --threshold-mode fixed`), score_match (`--rerank-input`, `--no-include-excluded`), validate_output (`--strict --json`), make_review_sheet, acceptance_report, diff_runs, stale_evidence, export_leads csv and tradewith-json: `isError` false, `exit_code` 0, `document` equals the parsed CLI stdout or `csv` equals it byte for byte, and the text block is the serialized `structuredContent`. All eleven tools are exercised |
| `mcp: M-15 tool results match the existing goldens …` | `review-sheet.buyers.uae.blind.csv` and `export.buyers.uae.csv` byte for byte; `acceptance.buyers.uae.expected.json`, `diff.buyers.uae-uk.expected.json`, `recheck.buyers.golden.expected.json` and `export.buyers.uae.tradewith.json` parse equal |
| `mcp: M-16 an inline document and a path give the same result …` | score_buyer with inline `input` + `query` equals the path call; diff_runs with inline `before` equals the path call |
| `mcp: M-17 as_of is required …` | Missing, `2026-02-30`, `2026-09-12\n` and `20260912` are each `isError` with `exit_code: null` (no script ran) |
| `mcp: M-18 argument errors are tool errors …` | Both `input` and `input_path`; neither; two inline documents on diff_runs; an unknown key (`pretty`); `top: "5"`; an empty `scored_paths` |
| `mcp: M-19 an input path outside --root is refused …` | An absolute fixture path, the same path as `../…`, and a symlink inside the root that points at it: each refused naming `--root` |
| `mcp: M-20 a directory, a NUL byte and output_path on the read-only validate_output are refused` | `input_path: "out"`, a path with `\u0000`, and `output_path` on a tool that offers none |
| `mcp: M-21 a script data error (exit 1) …` | The poisoned `canonical_domain` input: `isError` true, `exit_code` 1, `error` set, an `ERROR:` line in `diagnostics`, and the document the scorer still wrote (R7.3.2) |
| `mcp: M-22 validate_output exit 1 is a successful call …` | `isError` false, `exit_code` 1, `document.valid` false |
| `mcp: M-23 score_match without input / input_path, or with only one of rfq_path / sellers_path, is refused …` | Both calls are `isError` with `exit_code: null` (no script ran) and an error naming `rfq_path together with sellers_path`, never an empty-stdin error blaming `<stdin>`; a 256 KiB ping sent between them is still answered. The stdin isolation itself is M-48 |
| `mcp: M-24 INV-13 the same read-only transcript prints byte-identical stdout twice` | The whole read-only transcript, run a second time against the same root, prints the same bytes and exit code |
| `mcp: M-25 output_path writes a new file inside the root …` | `output` equals `{path, bytes, sha256}` of the file on disk, no `document` is returned, and the file parses equal to the CLI output |
| `mcp: M-26 an existing output_path is refused and the file is unchanged` | The same `output_path` again, and an input file as `output_path`: refused with "already exists", bytes unchanged |
| `mcp: M-27 a script that exits 1 after writing …` | The poisoned input with `output_path`: `isError`, `exit_code` 1, `output.path` reported and the error says a retry needs a new `output_path` |
| `mcp: M-28 output_path refusals …` | A dangling symlink (its target is not created), a missing directory (not created), a symlinked directory leading out of the root (nothing written outside) and an existing directory named `existing-dir.json` |
| `mcp: M-29 nothing but the two requested files was written under the root` | `out/` holds exactly the test's own symlink plus the two written files |
| `mcp: M-30 a result above the default inline cap is refused …` | The UAE buyer run (about 180 KB) without `output_path` under the default 32,768-byte cap: `isError`, `exit_code` 0, the error names `output_path`, no `document` |
| `mcp: M-31 a message above 16 MiB is -32600 …` | The oversized line is answered -32600 with id null and the next ping is answered |
| `mcp: M-32 without --quiet each call logs one stderr line …` | Every stderr line starts `kbtm-mcp: `, no traceback, exit 0 |
| `mcp: M-33 output_path inside the skill package is refused …` | With `--root` set to the package itself, `output_path: scripts/…` is refused and nothing is created |
| `mcp: M-34 --version prints the standard version line` | `mcp_server.py skill_version=… schema_version=0.1.0 score_version=kbtm-score-0.1.0` |
| `mcp: M-35 the server refuses to start without a safe --root …` | No `--root`, a missing directory, `/`, a parent of `HOME`, `HOME` itself, `--tool-timeout 0`, `--max-inline-bytes 10` (exit 2, exactly one `ERROR:` line, empty stdout, no traceback) and an unknown flag (argparse exit 2) |
| `mcp: M-36 the marketplace entry's server command starts the server` | The `mcpServers` entry of `.claude-plugin/marketplace.json` has `command` `python3`; its `args` with `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}` substituted name an existing file that answers `initialize` and `tools/list` with the eleven tools and exits 0. SKIP in an installed copy (no repository manifests) |
| `mcp: M-37 a lone surrogate echoed in an id, method, tool name or error …` | A request id `"\ud800"`, method `"\ud800x"`, tool name `"\ud800"`, `schema: "\udc80"` and `as_of: "\ud800"` are each answered (the frame falls back to `\u` escapes), stdout stays pure JSON and the final ping is answered |
| `mcp: M-38 JSON nested deeper than the interpreter allows …` | 200,000 `[`, 200,000 nested objects and 200,000 levels inside `arguments` (CPython 3.12+ decodes a few thousand levels without error): at least two -32700 with id null, no traceback, the final ping answered, exit 0 |
| `mcp: M-39 a closed stderr never stops the server …` | Launched with fd 2 closed and without `--quiet`: a `normalize_company` call succeeds and the next ping is answered, exit 0 |
| `mcp: M-40 an inline query too large for the command line …` | A 2 MiB and a 70,000-byte inline `query` are tool errors naming `query_path` with `exit_code: null`; in process, a child that cannot start is `isError` "could not be started", not -32603 |
| `mcp: M-41 an inline document or path holding a lone surrogate is a tool error …` | A lone surrogate in inline `input`, in inline `query` and in `input_path`: each `isError` naming UTF-8; the final ping is answered |
| `mcp: M-42 a stray response is never answered, even with id null` | Responses with id `null`, `1.5` and `77` produce no line; the only frame is the ping reply |
| `mcp: M-43 the package is recognised by file identity …` | With `--root` one level above the package, `output_path` spelt with the package folder's case swapped is refused and nothing is created (on a case-insensitive file system the error names the skill package); in process, `_within_package` recognises a symlinked alias of the package by `(st_dev, st_ino)` and rejects an unrelated folder |
| `mcp: M-44 output_path must end in .json or .csv …` | `probe.py`, `.probe.json` and `.cfg/probe.json` are refused; `probe.JSON` is written; nothing else is created |
| `mcp: M-45 a child runs isolated …` | With `PYTHONPATH` pointing at a `sitecustomize.py` that prints a marker, the child's diagnostics never show it (`-I`); in process, `_child_env()` drops `TRADEWITH_*` |
| `mcp: M-47 without --quiet a refused call still logs one 'kbtm-mcp: <tool> refused' line …` | A `../` input path and an overwrite are each refused and each leaves exactly one `kbtm-mcp: score_buyer refused` line on stderr |
| `mcp: M-48 a child started without a stdin document gets an empty stdin …` | A driver process whose own stdin holds the valid buyer golden calls `call_tool` with no stdin document: the child exits 2 with "empty"; had it inherited the driver's stdin it would have scored the document and exited 0 |

---

## 17. v0.3.0 final-review follow-ups

`phase_review_followups` runs after section 16. Each case pins one fix from the three final
reviews of v0.3.0 (cross-feature, safety, docs); every fix was reverted on its own on 2026-09-19 and
each revert turned at least one case here (or the renumbered M-23 / M-47 / M-48 and the export
exit-1 rows of section 14) red. No scorer, schema, golden or version changed.

| Case | What it asserts |
|---|---|
| `review: R-01 …` | `validate_output.py` knows `tradewith-bulk-buyers`: the export golden is detected as that kind and passes `--strict`; `{"buyers":[{"foo":1}]}` fails (exit 1, `sourceId` required) under `--schema tradewith-bulk-buyers` and under auto detection |
| `review: R-02 --schema-file validates a document whose kind is not detectable …` | `{"foo":1}` with `--schema-file schemas/tradewith-bulk-buyers.schema.json` is exit 1 with schema errors, not exit 0 with a "could not detect" warning |
| `review: R-03 the export schema's social pattern refuses …` | `tradewith-bulk-buyers.schema.json` `social` now requires `/company|showcase|school/<name>` with no empty, `.` or `..` segment (percent-encoded dots included) and no userinfo, so a hand-edited body with `/company/../in/…` fails `validate_output.py` (exit 1, error on `social`) |
| `review: R-04 an impossible or malformed --as-of is exit 2 …` | `--as-of 2026-13-01` and `--as-of garbage` give exit 2 naming `--as-of` in all eleven scripts (normalize, dedupe, the three scorers, validate, review sheet, acceptance report, diff, re-check queue, export) |
| `review: R-05 … refuses an empty --as-of` | `export_leads.py` and `acceptance_report.py` with `--as-of ''`: exit 2, "--as-of is empty" (the acceptance report used to blame a valid `reviewed_on`) |
| `review: R-06 a document that only looks like a match-result is reported invalid, not a crash` | `expected/match-134.expected.json` (a test digest with `no_match`) is exit 1 with no `AttributeError`, with and without `--no-validate` |
| `review: R-07 …` | A `linkedin` channel `/company/../in/…`, `/company/%2e%2e/in/…` or `/company//in/…` never becomes TradeWith `social` and is withheld from the generic CSV (`withheld 1 linkedin`) |
| `export: refuses … (R-08)` · `(R-09)` · `(R-10)` · `(R-11)` | Exit 1, nothing written: `%40` in the tier-1 `sourceUrl`; `…/ahmed-khan-mobile-0501234567` in the `sourceUrl`; `%40` in a `partnership_form` URL (CSV); a `phone` channel `+44 20 7946 0100 (Mr. Ahmed Khan, mobile)` (CSV) |
| `review: R-12 an official WhatsApp business link still exports …` | `https://api.whatsapp.com/send?phone=97145550111` in a `messenger` channel is kept in the CSV |
| `export: refuses … (R-13)` | `tradewith-csv` refuses a company name ` =1+1` (leading space) and `＝1+1` (full width) |
| `review: R-14 tradewith-json keeps such a name verbatim and warns on stderr` | Exit 0, `"companyName":" =1+1"`, a `WARNING` naming the formula character |
| `review: R-15 the generic csv guards a formula behind leading whitespace` | The CSV cell is `' =1+1` |
| `review: R-16 the widened URL scan still passes …` | `hotel-20240101`, a registry id `0123456789` in a path, a `%20`-encoded BPOM number and `/tel-plans/2024` give no personal-data hit |
| `review: R-17 the network scan flags …` | The shared scan helper flags `from urllib import request`, `__import__(…)`, `importlib`, a mail-protocol module and `subprocess` outside its allow-list (`scripts/mcp_server.py`, `tools/build_release.py`), and passes `urllib.parse` and comments |
