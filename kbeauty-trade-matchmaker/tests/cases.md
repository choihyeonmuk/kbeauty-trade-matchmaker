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
| `fixtures/normalize.cases.json` | Pure string cases for `canonical_domain` / `normalize_company_name`, copied from BUILD-CONTRACT 8.1 and 8.3. These are string transformations and assert nothing about any business. |
| `fixtures/expected/rfq.readiness.expected.json` | RFQ readiness per SCORING-CONTRACT 2.9. |

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
| **E16** Normalization cases | `normalize.cases.json` | The twenty-two `canonical_domain` cases and eight `normalize_company_name` cases of BUILD-CONTRACT 8.1 / 8.3, including `co.uk → unknown`, an IPv4 literal → `unknown`, `www.www.example.com → example.com` and `ABC Trading LLC → abc` | INV-15, INV-33 |
| **E16(b)** IDN folding | `normalize.cases.json`, four IDN cases | `https://마이브랜드.kr/about`, `https://www.마이브랜드.kr` and the already-punycode `https://xn--hy1b45cw6b22g85n.kr/` all fold to `xn--hy1b45cw6b22g85n.kr`, and `https://例え.jp/` to `xn--r8jz45g.jp` — which is what lets `dedupe_companies.py` merge a local-language duplicate. T06 covers only the `www`/non-www pair; the reference pages previously cited T06 for the IDN half too, and now cite these cases | PRD 15.1 criterion 4; INV-15, INV-16 |
| **E17** Domain beats name | `expected/dedupe.expected.json must_not_merge` | Two records with different known `canonical_domain` values never merge, whatever their names | INV-16 (R8.4.2) |
| **E18** Determinism | every run | Two identical invocations produce byte-identical stdout; `normalize_company.py` and `dedupe_companies.py` are stable when re-applied to their own output | INV-13, INV-15 |
| **E19** Renderable candidate | R3 and R6 output with `website`, `contact_channels`, `missing`, `company_type` and `oem_odm` stripped | `validate_output.py --strict --invariants` exits **1** and reports `INV-19` on both the discovery and the match document. Presence, not non-emptiness: a present-and-empty list is a verified `none` (E08) and stays legal. `website` may be absent only when `canonical_domain` is `"unknown"`, which BUILD-CONTRACT 8.1 makes the only case in which no website was discoverable | **INV-19**, PRD 15.1 criterion 2, PRD 12.2 |

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
| Envelope consistency | `match-134.input.json` / `match-no-match.input.json` embed their RFQ and the seller set byte-identically | prevents fixture drift |

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
| No network client | No `socket` / `ssl` / `http` / `urllib.request` import in `scripts/*.py` | R7.11.1 |
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

`python3 tests/run_tests.py` → **PASS 175 / FAIL 0 / SKIP 0**, exit 0.

Every scored record, every component score, every hard-filter decision, every exclusion, the whole
ordering of all four ranked runs, T01–T10, E01–E19, the eleven field regressions of section 6,
the package and adapter guards of section 10 and every safety and fixture case pass. Q4–Q6 remain open as documented divergences; none of them
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
| `package: R7.3.3 a non-UTF-8 input is a clean usage error, not a traceback` | All six CLI scripts fed a file with a raw `0xff` byte exit **2** with exactly one `ERROR:` line and empty stdout. `UnicodeDecodeError` is a `ValueError`, so it used to escape `read_input`'s `except (IOError, OSError)` and four scripts died with a bare traceback | R7.3.2, R7.3.3, **INV-35** |
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
