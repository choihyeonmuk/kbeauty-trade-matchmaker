# Qualification Rubric — buyer and seller

How a candidate turns into a defensible number, and what evidence earns what.
*Korean gloss: 바이어/셀러 점수 산출 기준 — 어떤 근거가 몇 점을 받는가.*

| | |
|---|---|
| Applies to | `scripts/score_buyer.py`, `scripts/score_seller.py`, and the six match components of `scripts/score_match.py` |
| `score_version` | `kbtm-score-0.1.0` |
| `schema_version` | `0.1.0` |
| Canonical `as_of` in every example | `2026-09-12` |
| **Binding source of every number** | `schemas/scoring.config.json` |
| Companion pages | `references/evidence-policy.md` (what may become evidence), `references/matching-rules.md` (hard filters, match score, rerank), `references/data-contract.md` (field shapes) |

> **The config wins, always.** Every weight, point value, penalty and threshold printed below is a
> copy of `schemas/scoring.config.json` at `score_version kbtm-score-0.1.0`, reproduced so an
> operator can read the rubric without opening JSON. The scripts read the config at run time and
> never a literal from this page. If this page and the config ever disagree, **the config is right
> and this page is a bug**. Changing any number in the config requires a new `score_version`.

---

## Table of contents

- [0. How to read this page](#0-how-to-read-this-page)
- [1. Buyer rubric](#1-buyer-rubric)
- [2. Seller rubric](#2-seller-rubric)
- [3. Evidence quality — the shared sixth dimension](#3-evidence-quality--the-shared-sixth-dimension)
- [4. Worked end-to-end examples](#4-worked-end-to-end-examples)
- [5. Reviewer checklist](#5-reviewer-checklist)

---

## 0. How to read this page

Each side (buyer, seller) has **six dimensions**. Five of them contain **criteria**; each criterion
owns a list of **signals** with point values. The sixth, `evidence_quality`, is a function, not a
criterion list (section 3).

### 0.1 The three states of a criterion

This trichotomy decides more outcomes than any point value. *Korean gloss: 채점됨 / 미상 / 해당없음.*

| State | When | Counts toward the numerator | Counts toward the denominator |
|---|---|---|---|
| `scored` | At least one deciding input is known | the points its fired signals earned — **possibly 0** | its `max_points` |
| `unknown` | **Every** deciding input is unknown or absent | the unknown-path points (0.4) | its `max_points` |
| `inapplicable` | The **query** imposed no such constraint | nothing — the criterion is dropped | nothing — the denominator shrinks |

Three consequences, and they are absolute:

1. **Known-but-nothing-fired is `0`, not `unknown`.** A buyer whose `product_categories` reads
   `["hardware", "stationery"]` scores **0** on B-KF3. The input was read; nothing matched.
2. **Absent is unknown; present-and-empty is known.** `korean_brands_carried: []` means *we checked,
   there are none* and scores 0. An **absent** `korean_brands_carried` is unknown and takes the
   unknown path.
3. **`inapplicable` is a property of the QUERY, never of the record.** A company that simply did not
   disclose something is `unknown`. Only a `condition_keys` entry from `scoring.config.json` can
   produce `inapplicable`, and exactly seven of them are consumed by a v0.1.0 criterion:
   `query.country_absent`, `query.product_categories_absent`, `query.product_forms_absent`,
   `query.channels_absent`, `query.required_certifications_absent`,
   `query.preferred_certifications_absent`, `query.destination_country_absent`. An eighth,
   `query.company_types_absent`, is **reserved**: the config declares it, but no v0.1.0 criterion
   lists it in an `inapplicable_when`, because company type is scored on an absolute table
   (B-CR1) rather than against a requested type.

### 0.2 Aggregation inside one criterion

`aggregation` takes exactly two values:

- **`max`** — the signals are mutually exclusive alternatives. Credit the **highest** fired signal;
  credit 0 if none fired.
- **`sum_capped`** — the signals are independent and additive. Credit
  `min(sum of fired signals, criterion max_points)`.

A criterion never goes below 0 and never exceeds its own `max_points`.

### 0.3 From criteria to a dimension score

```
applicable   = criteria whose state is not "inapplicable"
earned_total = sum of earned points over applicable criteria
max_total    = sum of max_points over applicable criteria
dimension_raw   = 100 * earned_total / max_total
dimension_score = clamp(round_half_up(dimension_raw) + sum of fired adjustments, 0, 100)
```

- Adjustments are **integer point deltas applied after** the normalised value is rounded, and they
  **stack**: two adjustments on the same dimension both apply.
- Rounding is **half up**. Python's built-in `round()` is banker's rounding and is forbidden
  anywhere in the package.
- `evidence_quality` skips all of this: its dimension score is the section 3 function output
  verbatim.

### 0.4 From dimensions to the qualification score

```
qualification_score = round_half_up( sum over dimensions of (dimension_score * weight) / 100 )
qualified           = qualification_score >= threshold.value
```

Weights are point weights summing to 100. The per-dimension display value
`weighted_contribution = round_half_up(dimension_score * weight / 100, 2)` is **for display only**
and MUST NOT be summed to produce the total — sum the un-rounded products, then round once.

Default threshold (`scoring.config.json thresholds`): `mode = "fixed"`, `value = 70`.
Percentile mode exists (`percentile 75`, `percentile_min_population 8`, `percentile_floor 50`,
`nearest_rank`) and falls back to fixed when the scored population is smaller than 8. The resolved
value and mode are always written into the run summary, so a reader never has to guess the cut-off.

> **Open, and deliberately so.** A live 11-record Korean seller population scored 41–79, and the five
> records at or above 70 were *exactly* the five holding a verifiable ISO 22716 / CGMP token — so on
> that run "qualified" meant one thing only. Flipping `mode` to `percentile` would spread the same
> population and needs no new code (the parameters above are implemented, and 11 clears the minimum of
> 8). It is **not** flipped here: PRD Open Question 3 leaves the mode undecided and PRD 12.1 prints
> *"Qualified (≥ 70)"*, so the shipped default is a product decision, not a defect. The weights below
> are fixed by PRD 6.3 for the same reason. Everything this rubric *does* change to widen the spread —
> S-CM4, the S-CP1 verification split, S-EX1's region signals, the undated-evidence multiplier — works
> through point tables and vocabularies instead.

### 0.5 The unknown path — never zero, never a rejection

```
unknown_points(criterion) = round_half_up(criterion.max_points * (neutral_base / 100) * penalty_factor)
                          = round_half_up(criterion.max_points * 0.30)
```

with `unknown.neutral_base = 50`, `unknown.penalty_factor = 0.6`
(`unknown.unknown_points_multiplier = 0.3`).

It is **forbidden** to score an unknown as `0`, to hard-reject on an unknown
(`unknown.never_hard_reject_on_unknown = true`), or to invent a value to dodge the penalty.

Pre-computed unknown points for every v0.1.0 criterion (derived from the config, not independent
constants):

| Criterion | max | unknown | | Criterion | max | unknown |
|---|---|---|---|---|---|---|
| B-KF1 | 55 | **17** | | S-PF1 | 60 | **18** |
| B-KF2 | 30 | **9** | | S-PF2 | 25 | **8** |
| B-KF3 | 15 | **5** | | S-PF3 | 15 | **5** |
| B-CR1 | 60 | **18** | | S-CM1 | 60 | **18** |
| B-CR2 | 25 | **8** | | S-CM2 | 12 | **4** |
| B-CR3 | 15 | **5** | | S-CM3 | 8 | **2** |
| | | | | S-CM4 | 20 | **6** |
| B-SI1 | 45 | **14** | | S-OP1 | 55 | **17** |
| B-SI2 | 45 | **14** | | S-OP2 | 25 | **8** |
| B-SI3 | 10 | **3** | | S-OP3 | 20 | **6** |
| B-MR1 | 55 | **17** | | S-CP1 | 55 | **17** |
| B-MR2 | 35 | **11** | | S-CP2 | 15 | **5** |
| B-MR3 | 10 | **3** | | S-CP3 | 20 | **6** |
| B-RE1 | 70 | **21** | | S-CP4 | 10 | **3** |
| B-RE2 | 15 | **5** | | S-EX1 | 45 | **14** |
| B-RE3 | 15 | **5** | | S-EX2 | 30 | **9** |
| | | | | S-EX3 | 25 | **8** |

**A dimension in which every criterion is applicable and every one is unknown scores exactly 31.**
That 31 is *emergent* (`50 × 0.6 = 30`, plus per-criterion half-up rounding), not a constant. When
some criteria are inapplicable the renormalised value lands in **30..33** — for example
`market_relevance` with only B-MR3 applicable is `3/10` → **30**, and `product_fit` with S-PF1 and
S-PF2 inapplicable is `5/15` → **33**. Forcing any of these to 31 corrupts every renormalised
dimension.

**Partial unknown.** When only *some* deciding inputs of a criterion are unknown
(`unknown.partial_unknown_behaviour = "omit_signal_and_record_missing"`), the criterion stays
`scored`, the signals that need the unknown input simply do not fire, **no** `unknown_penalty_applied[]`
entry is emitted, and the unknown field is appended to the record's `notes[]` — **never** to
`missing[]`, which is derived mechanically from `unknown_penalty_applied[]` labels followed by the configured `verification_gaps` (an unpublished minimum order, or a requested category evidenced only at a broader level).

**Flagging.** When a record's `unknown_penalty_applied[]` count exceeds the entity's limit, the record
additionally carries an `evidence_gap` risk/note and is rendered with a **확인 필요 / needs
verification** marker. It is never auto-rejected. The note **must name the claims that are unknown**,
taken from the labels of the `unknown_penalty_applied[]` entries that triggered it — *"확인 필요 /
needs verification: MOQ, lead time, export markets"*, never a bare *"this record has gaps"*. In this
vertical a handful of unpublished commercial terms is the baseline, so the generic phrasing tells a
reviewer nothing while the named list tells them what to ask the company for. The limit is **per entity**
(`unknown.max_unknown_dimensions_before_flag_by_entity`): **buyer 2**, **seller 4**, **match 4**, with
`unknown.max_unknown_dimensions_before_flag` (2) as the fallback for an entity with no override. The
seller and match limits are higher because in this vertical the *baseline* is that Korean makers do not
publish commercial terms — measured over 12 tier-1-verified companies, `lead_time_days` and
`monthly_capacity_units` were unknown on 100%, `moq` on 92%, `export_markets` and
`certifications_verified` on 83%. At a limit of 2 the marker fired on 11 of 11 records and told a
reviewer nothing; a flag that never distinguishes anything is noise, not caution.

### 0.6 Confidence is not a quality score

```
coverage_factor     = max(0.5, 1.0 - 0.05 * n_unknown_material_claims)
stale_multiplier    = 0.6  when record.stale is true, else 1.0
conflict_multiplier = 0.85 when the record carries >= 1 unresolved conflict, else 1.0
confidence = clamp(round_half_up(evidence_quality / 100
                                 * coverage_factor * stale_multiplier * conflict_multiplier, 2),
                   0.05, 1.0)
```

`n_unknown` counts **record fields in `evidence.material_claims`**, not evidence coverage. A field
that is absent or `"unknown"` counts; a present-and-empty array or an explicit `false` is a **known**
value and does not count; a field outside the material-claim list never counts, however heavily the
score penalises it.

`confidence` answers *"how much should the reviewer trust this record"*, never *"how good is this
company"*. It MUST NOT read `qualification_score` or `match_score`. Rendering
(`output.render_confidence_as`): **HIGH >= 0.75**, **MEDIUM >= 0.5**, **LOW** below 0.5. The stored
value stays numeric.

### 0.7 Five rules that settle most disputes

1. Silence is `unknown`. Silence is never `false`.
2. A verified negative (`false`, `[]`) needs evidence **of the check itself**.
3. Marketing prose is not a fact. A named scheme, a dated action or a published page is.
4. The company's own self-description decides `company_type`, never its assortment.
5. If you had to reason your way to a value, the evidence item carries `inferred: true` — and if the
   quote does not genuinely support the inference, the field stays `"unknown"`.

---

## 1. Buyer rubric

**Weights** (`buyer.weights`, sum 100):

| # | Dimension (config key) | `dimension_scores` key | Weight |
|---|---|---|---|
| 1 | `kbeauty_korea_fit` | `kbeauty_fit` | **20** |
| 2 | `b2b_commercial_role` | `b2b_role` | **20** |
| 3 | `sourcing_intent` | `sourcing_intent` | **25** |
| 4 | `market_relevance` | `market_relevance` | **15** |
| 5 | `reachability` | `reachability` | **10** |
| 6 | `evidence_quality` | `evidence_quality` | **10** |

```
qualification_score = round_half_up(
    ( 20*kbeauty_fit + 20*b2b_role + 25*sourcing_intent
    + 15*market_relevance + 10*reachability + 10*evidence_quality ) / 100 )
```

Every buyer dimension combines **additively within each criterion**, then **normalises to 0..100
over the applicable criterion maxima**, then applies integer adjustments, then clamps. It is not
banded and there are no thresholds between criteria.

---

### 1.1 `kbeauty_korea_fit` — weight 20

*Korean gloss: K-뷰티/한국 적합도.* Does this company actually touch Korean beauty product?

#### B-KF1 — "Korean product carriage" · max 55 · `max`

Deciding inputs: `korean_products_signal`, `korean_brands_carried`, evidence on claim
`korean_products_signal`.

| Signal key | Pts | Fires when |
|---|---|---|
| `korean_brands_named_3_plus` | **55** | `len(korean_brands_carried) >= 3` |
| `korean_brands_named_1_2` | **48** | `1 <= len(korean_brands_carried) <= 2` |
| `korea_sourcing_statement` | **45** | An official evidence item on claim `korean_products_signal` whose quote is an explicit sourcing statement ("we import directly from Korean manufacturers") |
| `korean_products_signal_true` | **40** | `korean_products_signal is true` |
| `korean_products_signal_false` | **0** | `korean_products_signal is false` |

Aggregation is `max`, so the order above is an ordering by **evidential strength**, and a record that
fires several keeps only the best. A named portfolio must outrank a bare boolean: while
`korean_products_signal_true` and `korean_brands_named_3_plus` were both 55, a buyer with 27 named
Korean brands scored exactly what a buyer with an unqualified `true` and no portfolio scored.

#### B-KF2 — "K-Beauty positioning" · max 30 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `kbeauty_specialist_positioning` | **30** | The company's **own** channel positions the business itself as a K-Beauty / Korean-cosmetics specialist (homepage headline, About page, company description, legal trading name) |
| `korean_category_page` | **22** | The company's own site has a dedicated Korean / K-Beauty navigational category or collection page |
| `korean_carriage_with_beauty_categories` | **20** | **Field-derived fallback:** `korean_products_signal is true` **and** at least one normalised `product_categories` slug is in the core beauty vocabulary (the B-KF3 `beauty_categories_present` test) |
| `korean_beauty_marketing_mention` | **12** | Korea / K-Beauty appears in marketing copy, a blog post or a press item, with no dedicated page |

The first three and the last are **agent signals** — they fire only on an evidence item whose `claim`
*is* the signal key. `korean_carriage_with_beauty_categories` is the only route into this criterion
that needs no agent-authored claim, and it exists because without it the criterion fired for **none**
of a live 20-company cohort: every record either scored 0 (because `product_categories` was known and
no claim key had been written) or took the 9-point neutral (because it was unknown) — i.e. disclosing
less scored higher. The unknown state is correspondingly keyed to the same inputs the criterion can
score: **unknown only when no signal fired, no `kbeauty_positioning` evidence exists, the normalised
`product_categories` is unknown *and* `korean_products_signal` is unknown.**

#### B-KF3 — "Beauty category alignment" · max 15 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `beauty_categories_present` | **15** | At least one slug is in the canonical category vocabulary **outside** the `personal_care` and `inner_beauty` subtrees |
| `beauty_adjacent_categories_present` | **8** | No such slug, but at least one is `personal_care` / `inner_beauty` or one of their children |

Membership is tested on the **normalised** slug, never the raw one. Without normalisation the
synonyms `sun_cream`, `colour_cosmetics` and `skin_care` classify as in-vocabulary under one reader
and as unmapped non-beauty under another — a 15-vs-0 swing that also flips the −60 adjustment below.
`k_beauty` / `kbeauty` are **vertical markers, not categories**: they normalise away, so a list made
only of them normalises to empty and is therefore **unknown**, not non-beauty.

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `non_beauty_business` | **−60** | The **normalised** `product_categories` is present and non-empty, **no** slug is in the vocabulary (every slug is an unmapped non-beauty token), **and** the pre-normalisation list contained no vertical marker |
| `korean_products_signal_false` | **−25** | `korean_products_signal is false` |
| `korean_products_signal_unknown` | **−20** | `korean_products_signal` is unknown (absent or the literal). Mutually exclusive with the row above |

`korean_products_signal_unknown` closes the gap `non_beauty_business` cannot reach. That −60 fires only
on a *present* category list, so "we checked and found nothing" was penalised while "we never checked"
was not: in a live run a UAE trading company with `korean_products_signal` **and**
`product_categories` both unknown — no Korean or K-Beauty reference on any page that was read — scored
70, qualified, and outranked seven companies with evidenced named Korean portfolios, because the
unknown neutral floors this dimension at 31 rather than gating it. −20 keeps unknown **penalised, never
rejected** (`unknown.never_hard_reject_on_unknown` still holds) while making an unchecked record cost
more than a small verified one.

#### Worked

| Band | Inputs | Arithmetic | Score |
|---|---|---|---|
| **High** | `korean_products_signal: true`; the site's About page says "the UAE's K-Beauty specialist"; `product_categories: [skincare, sunscreen]` | `40 + 30 + 15 = 85` of 100 → raw 85 | **85** |
| **High, named portfolio** | as above but `korean_brands_carried` names 4 brands | `55 + 30 + 15 = 100` → raw 100 | **100** |
| **Medium** | `korean_brands_carried: ["Brand A", "Brand B"]`, `korean_products_signal: true`; a blog post about K-Beauty trends, no Korean collection page; `product_categories: [skincare]` | `48 + 20 + 15 = 83` of 100 → raw 83 (B-KF2 takes the field-derived fallback, 20, over the 12-point mention) | **83** |
| **Unchecked** | `korean_products_signal` unknown, `product_categories` unknown, nothing else | `17 + 9 + 5 = 31` → raw 31, then `−20` | **11** |
| **Low (T09)** | `product_categories: [hardware, stationery]`; `korean_products_signal: false` | `0 + 0 + 0 = 0` → raw 0, then `−60 −25 = −85` → clamp | **0** |

Note the Low row: B-KF2 is **scored at 0**, not `unknown`, because `product_categories` was read.

#### Unknown path

B-KF1 → 17 only when `korean_products_signal` is unknown/absent **and** `korean_brands_carried` is
absent **and** no sourcing-statement evidence exists. B-KF2 → 9 when `product_categories` is absent
and no `kbeauty_positioning` evidence exists. B-KF3 → 5 when `product_categories` is absent. All
three unknown → dimension **31**.

#### What does NOT count

- "Made in Korea" on an unrelated product line (electronics, food).
- A blog article *about* K-Beauty with no product carriage — at most `korean_beauty_marketing_mention` (12).
- A **marketplace listing page** that merely surfaces Korean third-party sellers. The marketplace does not carry the product.
- A Korean-language version of the site when the products are not Korean.
- "We also carry Asian beauty" with no named Korean brand and no explicit sourcing statement — that is **unknown**, never `false`.
- A single Korean SKU in a product grid with no brand named: it does not make `korean_brands_carried` non-empty.

---

### 1.2 `b2b_commercial_role` — weight 20

*Korean gloss: B2B 상업적 역할.* Can this company actually buy wholesale?

#### B-CR1 — "Company type" · max 60 · `max` · input `company_type`

| Signal key | Pts | | Signal key | Pts |
|---|---|---|---|---|
| `company_type_distributor` | **60** | | `company_type_brand` | **27** |
| `company_type_importer` | **60** | | `company_type_retailer` | **24** |
| `company_type_wholesaler` | **57** | | `company_type_other` | **15** |
| `company_type_marketplace` | **36** | | | |

`"other"` means *classified, none of the above* and scores 15. `"unknown"` means *not yet determined*
and takes the unknown path (18). The two are never interchangeable.

#### B-CR2 — "Wholesale capability" · max 25 · `max` · inputs `wholesale_signal`, `buyer_moq`

| Signal key | Pts | Fires when |
|---|---|---|
| `published_minimum_order` | **25** | `buyer_moq` holds a known number, range or money floor — the buyer publishes the minimum order it will place or accept |
| `wholesale_signal_true` | **25** | `wholesale_signal is true` |
| `b2b_pricing_or_trade_account` | **20** | A published wholesale/trade price list, or a trade-account registration gate stating B2B-only pricing |
| `reseller_program_page` | **18** | A reseller / stockist program page |
| `wholesale_signal_false` | **0** | `wholesale_signal is false` |

`published_minimum_order` **ties** with `wholesale_signal_true` rather than outranking it, because
what it buys is *reach*, not seniority: a company that prints "Minimum Order: 10,000 AED per
Purchase Order" and never uses the word *wholesale* used to take the unknown path here (8 of 25)
while having published the single most material B2B term a Korean seller needs. In a live UAE run
five of 32 companies published a concrete minimum order — a per-PO money floor, a per-line piece
count, or a goods-value floor — and every one of them survived only as free text in `notes[]`.
Store the number in `buyer_moq` (with `buyer_moq_unit`, or `buyer_moq_currency` when it is money),
and evidence it under the claim key `buyer_moq`. A vague "low MOQs" is marketing, not a number:
leave the field unknown.

#### B-CR3 — "Procurement / buying function" · max 15 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `procurement_or_buying_page` | **15** | A procurement / buying / vendor-onboarding page on the buyer's own site |
| `retail_chain_supplier_page` | **12** | A retail chain's "supply to us" / vendor application page |
| `distributor_network_operated` | **10** | `channels` contains `distributor_network`, or the site lists the buyer's own distributor/stockist network |

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `retail_chain_procurement` | **+20** | `company_type == "retailer"`, `wholesale_signal` is **not** an explicit `false`, **and** `channels` names a multi-store buying format (`pharmacy`, `department_store` or `marketplace`) |
| `retail_only_consumer_store` | **−25** | `company_type == "retailer"` **and** `wholesale_signal is false` |
| `wholesale_signal_false` | **−15** | `wholesale_signal is false` |

The two retailer adjustments are **mutually exclusive by construction**: `retail_only_consumer_store`
requires `wholesale_signal is false`, which `retail_chain_procurement` excludes. `retail_chain_procurement`
exists because §6 of `references/buyer-discovery.md` forces `retailer` on anyone with consumer prices and
a store locator — which is every pharmacy chain and department store — and the 20-weight company-type
table (retailer = 24 of 60) then outvoted the 25-weight `sourcing_intent` dimension. In a live UAE run
the one company with a dated, signed, public commitment to onboard K-Beauty brands earned the run's
highest `sourcing_intent` (83) and a perfect `market_relevance`, and still ranked 12th of 20, below
wholesalers carrying no Korean brand at all. A proven central buying function is not a consumer
storefront, and the buyer matrix has a row (11) whose whole purpose is finding exactly that company.

These **stack**: a confirmed retail-only consumer store takes **−40**.

#### Worked

| Band | Inputs | Arithmetic | Score |
|---|---|---|---|
| **High** | `company_type: distributor`, `wholesale_signal: true`, a vendor-onboarding page | `60 + 25 + 15 = 100` → raw 100 | **100** |
| **Medium** | `company_type: wholesaler`, a stockist program page, `channels` includes `distributor_network` | `57 + 18 + 10 = 85` → raw 85 | **85** |
| **Retail chain that buys** | `company_type: retailer`, `wholesale_signal` unknown, `channels: [pharmacy, retail_store]`, B-CR3 unknown | `24 + 8 + 5 = 37` → raw 37, then `+20` | **57** |
| **Low (T01)** | `company_type: retailer`, `wholesale_signal: false`, B-CR3 unknown | `24 + 0 + 5 = 29` → raw 29, then `−25 −15 = −40` → `−11` → clamp | **0** |

With `b2b_role = 0` the 20-point dimension contributes nothing, and the buyer cannot reach the 70
threshold on the remaining 80 points unless it is exceptional everywhere else.

#### Unknown path

B-CR1 → 18 when `company_type` is `"unknown"` or absent. B-CR2 → 8 when `wholesale_signal` **and**
`buyer_moq` are both unknown/absent and no B-CR2 page evidence exists. B-CR3 → 5 when both
`sourcing_signals` and `channels` are absent. All three → **31**.

#### What does NOT count

- **A general retailer that stocks one Korean brand is not a distributor.** Type comes from self-description and business model, never from assortment.
- A consumer checkout with retail prices only; an Instagram/TikTok shop; a plain Shopify storefront.
- "Wholesale inquiries welcome" in a footer with no page, form or price list — this does **not** fire `wholesale_signal_true`.
- A **marketplace listing** by a third-party seller: the marketplace operator is `company_type_marketplace` (36) and is not an importer.
- Being an authorised *reseller of someone else's brand* — that is not a procurement function.
- A LinkedIn job posting for a "Buyer" role — a hiring signal, not a published buying function.

---

### 1.3 `sourcing_intent` — weight 25 (the heaviest buyer dimension)

*Korean gloss: 소싱 의향.* Is this company **currently inviting** new suppliers?

#### B-SI1 — "Declared sourcing intent" · max 45 · `max` · input `sourcing_intent`

| Signal key | Pts | `sourcing_intent` value and its data-entry rule |
|---|---|---|
| `sourcing_intent_high` | **45** | `"high"` — an active, **dated** public sourcing action (open call, posted RFQ, "now accepting new brands") |
| `sourcing_intent_medium` | **29** | `"medium"` — a standing partnership/wholesale page, no dated activity |
| `sourcing_intent_low` | **12** | `"low"` — B2B capable, no sourcing invitation |

`max_points` came down from 55 and the three values were rescaled by the same factor, so the
ordering and spacing are untouched. `sourcing_intent` is a **self-declared** field that real
importers almost never publish: over a live 20-company UAE cohort, 15 of 20 carried no value at
all. At 55 of 100 this one criterion pinned the heaviest dimension in the rubric to its unknown
neutral for three quarters of the population — the dimension resolved to **three** distinct values
across the run while the 10-weight `reachability` dimension produced **eleven**, so the lightest
dimension was doing the ranking. The 10 points moved to B-SI2, which is decided by pages an agent
can actually observe.

#### B-SI2 — "Public sourcing signals" · max 45 · `sum_capped` · input `sourcing_signals`

The signal keys **are** the schema enum values, verbatim — so a value added to the enum without a
row here is unscorable, and `tests/run_tests.py` is where that parity belongs.

| Signal key | Pts | | Signal key | Pts |
|---|---|---|---|---|
| `rfq_posted` | **45** | | `become_a_distributor_page` | **19** |
| `open_call_for_suppliers` | **32** | | `partnership_page` | **15** |
| `new_brand_inquiry` | **28** | | `published_trade_terms` | **15** |
| `active_sourcing_post` | **28** | | `wholesale_inquiry_form` | **13** |
| `brand_submission_form` | **23** | | `trade_show_attendance` | **10** |

Sum every present entry, then cap at 45. Every value was rescaled from the old 35-point table by
the same factor, so the ordering is unchanged; what changed is the **slope**. Two or three real
signals now separate candidates instead of clipping at a low ceiling.

`published_trade_terms` is the new enum value and the one most real B2B importers actually emit: a
buyer that publishes the terms on which it takes supply — a stated minimum order (store the number
in `buyer_moq` as well), a trade price list, or trade-only pricing behind an approval gate. It is
priced level with `partnership_page` and, deliberately, **above this criterion's own unknown
neutral (14)**, so that disclosing it can never score below never having looked.

#### B-SI3 — "Recency of the sourcing action" · max 10 · `max`

Deciding input: the **newest** `source_date` among evidence items whose `claim` is `sourcing_intent`
or any `sourcing_signals` enum value. `age_days = as_of − source_date`.

| Signal key | Pts | Fires when |
|---|---|---|
| `sourcing_signal_within_90d` | **10** | `age_days <= 90` |
| `sourcing_signal_within_365d` | **6** | `90 < age_days <= 365` |
| `standing_sourcing_page_undated` | **5** | No qualifying item carries a known `source_date`, but at least one is a **live supplier-facing page**; the item's `observed_at` decides, and `observed_at` is bounded by the run's `as_of` |
| `sourcing_signal_older_than_365d` | **2** | `age_days > 365` |

`standing_sourcing_page_undated` sits **below** `sourcing_signal_within_365d` because a dated action
strictly dominates an undated standing page, and **above** the criterion's unknown neutral (3) so
recording the page beats never looking. Without it the criterion was `unknown` for 18 of 20 live
candidates and "Recency of the sourcing action" sat on the `Missing:` line of every top candidate,
where it told the reviewer nothing. It is the one place `observed_at` may decide a recency band, and
only because the alternative is a dead criterion; **`observed_at` never sets `source_date`** and
never feeds the section 3.1 recency buckets.

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `sourcing_closed_statement` | **−40** | An explicit public statement that the buyer is **not** accepting new brands/suppliers. Silence is unknown, never a closed statement |
| `partnership_signal_false` | **−10** | `partnership_signal is false` |

#### Worked

| Band | Inputs | Arithmetic | Score |
|---|---|---|---|
| **High** | `sourcing_intent: high`; `sourcing_signals: [rfq_posted]`; newest sourcing evidence `2026-07-15` → age 59 d | `45 + min(45, 45) + 10 = 100` → raw 100 | **100** |
| **Medium** | `sourcing_intent: medium`; `sourcing_signals: [partnership_page, wholesale_inquiry_form]`; newest evidence `2025-11-04` → age 312 d | `29 + (15 + 13 = 28) + 6 = 63` → raw 63 | **63** |
| **Low** | `sourcing_intent: low`; `sourcing_signals: []` (checked, none); B-SI3 unknown (no dated evidence); `partnership_signal: false` | `12 + 0 + 3 = 15` → raw 15, then `−10` | **5** |

#### Unknown path

B-SI1 → 14 when `sourcing_intent` is `"unknown"` or absent. B-SI2 → 14 when `sourcing_signals` is
**absent**; present-and-empty scores **0**. B-SI3 → 3 when no qualifying evidence item carries a
known `source_date` **and** no live supplier-facing page was recorded. All three → **31** — the
redistribution deliberately left that floor where it was, so an unchecked record gains nothing.

#### What does NOT count

- **An e-commerce marketplace listing is not sourcing intent.** Neither is a product page, a wholesale catalogue of current stock, or an affiliate program.
- A generic "Contact us" form. A careers page. A press-enquiry address.
- A press article saying the company "plans to expand its Korean range" — a plan, not a published sourcing action, unless the article quotes an open call.
- Trade-show attendance four years ago still fires `trade_show_attendance` (10) on B-SI2, but on B-SI3 it fires only `sourcing_signal_older_than_365d` (2). **Age never removes a signal; it only lowers B-SI3.**
- A **vague** terms claim — "low MOQs", "competitive wholesale rates" — is not `published_trade_terms`. A number, a price list or a gated trade account is.
- An RFQ posted on a third-party portal by a **different legal entity** with a similar name.
- Inference from hiring, funding or store-count growth.

---

### 1.4 `market_relevance` — weight 15

*Korean gloss: 시장 적합도.* Does the candidate sit in the market the operator asked about?

#### B-MR1 — "Country match against the request" · max 55 · `max` · inapplicable when `query.country_absent`

| Signal key | Pts | Fires when |
|---|---|---|
| `country_exact_match` | **55** | `buyer.country == query.country` (ISO-3166-1 alpha-2, uppercase) |
| `country_in_requested_region` | **35** | `buyer.country` is in `query.region_countries` |
| `country_adjacent_market` | **20** | `buyer.country` is in `query.adjacent_region_countries` |

Region words (`"GCC"`, `"EU"`, `"MENA"`) are expanded by the **agent** into explicit alpha-2 lists
before the query surface is built; the scripts never expand a region. When the query carries no
region list, the two region signals simply **cannot fire** — they are *not* unknown, because the
criterion is still decided by its exact-match signal.

#### B-MR2 — "Category match against the request" · max 35 · `max` · inapplicable when `query.product_categories_absent`

| Signal key | Pts | Category relation between record and query |
|---|---|---|
| `category_exact_match` | **35** | `exact` — the same slug on both sides |
| `category_parent_match` | **25** | `parent` — one side's slug is the other's parent |
| `category_adjacent_match` | **15** | `adjacent` — siblings, or parents in the same adjacency group |

#### B-MR3 — "Channel match against the request" · max 10 · `max` · inapplicable when `query.channels_absent`

| Signal key | Pts | Fires when |
|---|---|---|
| `channel_match` | **10** | `buyer.channels` intersects `query.channels` |
| `channel_partial_match` | **6** | A declared-adjacent pair from `channel_adjacency.pairs`: `retail_store ↔ department_store`, `ecommerce ↔ marketplace`, `pharmacy ↔ salon_spa` (symmetric). A channel in no pair can only fire `channel_match` |

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `country_mismatch` | **−40** | `query.country` is set, `buyer.country` is known, and **no** B-MR1 signal fired |
| `category_no_overlap` | **−20** | `query.product_categories` is set, `buyer.product_categories` is known and non-empty, and **no** B-MR2 signal fired |
| `category_drift_flagged` | **−10** | `buyer.source_query.category_drift is true` — the candidate was only surfaced after the run widened its query |

#### Worked

| Band | Inputs (query: `country AE`, `categories [sunscreen]`, `channels [wholesale]`) | Arithmetic | Score |
|---|---|---|---|
| **High** | `country: AE`; `product_categories: [sunscreen]`; `channels: [wholesale]` | `55 + 35 + 10 = 100` of 100 | **100** |
| **Medium** | `country: SA` (in `region_countries`); `product_categories: [suncare]` (parent); `channels: [ecommerce]` vs query `[marketplace]` | `35 + 25 + 6 = 66` of 100 | **66** |
| **Renormalised** | Same as High, but the operator set **no** channel filter → B-MR3 inapplicable | `90` of `55 + 35 = 90` → raw 100 | **100** |
| **Low** | `country: DE` (no region list); `product_categories: [haircare]`, no overlap; `channels` disjoint | `0 + 0 + 0 = 0`, then `−40 −20 = −60` → clamp | **0** |

The renormalised row is the asymmetry that matters: an **inapplicable** criterion shrinks the
denominator (the buyer is not punished for a filter the operator never set), while an **unknown**
criterion stays in the denominator at 30 % credit.

#### Unknown path

B-MR1 → 17 when `buyer.country` is absent/`"unknown"` and the criterion is applicable. B-MR2 → 11
when `product_categories` is absent. B-MR3 → 3 when `channels` is absent.

#### What does NOT count

- **Shipping to a country is not being in it.** "We ship worldwide" never fires `country_exact_match`.
- A ccTLD alone (`.ae`, `.co.uk`) does not establish `country`; a registered address, a registration number, or an explicit "our head office is in …" does.
- A "Global" or "International" landing page.
- A category the buyer *plans* to add.

---

### 1.5 `reachability` — weight 10

*Korean gloss: 접촉 가능성.* Is there a **company-level** way in?

#### B-RE1 — "Best official contact channel" · max 70 · `max` · input `contact_channels`

One signal per channel `type`; **any type not listed maps to `contact_channel_other`**.

| `contact_channel.type` | Signal key | Pts |
|---|---|---|
| `partnership_form` | `contact_channel_partnership_form` | **70** |
| `wholesale_form` | `contact_channel_wholesale_form` | **68** |
| `corporate_email` | `contact_channel_corporate_email` | **65** |
| `form` | `contact_channel_form` | **55** |
| `contact_page` | `contact_channel_contact_page` | **45** |
| `messenger` | `contact_channel_messenger` | **35** |
| `linkedin` | `contact_channel_linkedin` | **30** |
| `phone` | `contact_channel_phone` | **25** |
| `other` | `contact_channel_other` | **20** |

> **Resolved:** `wholesale_form` and `messenger` were legal `contact_channel.type` values with no
> signal of their own, so the config's fallback sent them to `contact_channel_other` (20) — a live
> trade application form scoring below a *generic* `form` (55) and below a LinkedIn company page (30).
> Measured cost: a self-declared regional K-Beauty distributor with a wholesale application form, an
> official messenger channel and a published MOQ scored `reachability` 30 and missed the threshold at
> 69; relabelling the same two channels and changing nothing else scored it 74. Both keys now exist,
> and `tests/run_tests.py` asserts that **every** `contact_channel.type` enum value has a
> `contact_channel_<type>` signal, so the next added type cannot silently fall through.

#### B-RE2 — "Channel breadth" · max 15 · `max`

Count **distinct `(type, normalised value)` pairs**:

- **URL-valued types**: `canonical_domain(value)` + path with the trailing slash removed, casefolded; query string and fragment dropped. `https://example.com/contact` and `https://example.com/contact/` are **one** pair.
- **`corporate_email`**: the address casefolded, any `mailto:` prefix stripped. `mailto:Info@Example.com` and `info@example.com` are **one** pair.
- **`phone`**: the digits only. `+82-2-123-4567` and `0221234567` are **one** pair.
- A value whose normalised form is **empty** counts as its own pair and MUST produce a note; it is never silently merged.

| Signal key | Pts | Count |
|---|---|---|
| `channels_3_plus` | **15** | `>= 3` |
| `channels_2` | **10** | `== 2` |
| `channels_1` | **5** | `== 1` |

#### B-RE3 — "Corporate-domain email" · max 15 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `corporate_domain_email` | **15** | A `corporate_email` channel whose domain equals `canonical_domain` or is in `alias_domains` |
| `other_corporate_domain_email` | **10** | A `corporate_email` channel whose domain is neither the canonical/alias domain **nor** a free-mail domain — a sibling company domain, a group domain, or a near-identical spelling |
| `free_mail_domain_email` | **6** | A `corporate_email` channel on a configured free-mail domain |

> The free-mail domain list lives in `scripts/_common.py` as `FREE_MAIL_DOMAINS`, not in
> `scoring.config.json` (SCORING-CONTRACT §8 ledger item 4 permits either home). It is a
> **vocabulary**, not a tunable number, so the config-only rule for weights and point values is
> unaffected. Any address that is neither the company's own domain nor on that list now fires
> `other_corporate_domain_email` (10) rather than nothing: before that signal existed a genuine
> corporate address on a sibling company domain scored **0** while a `gmail.com` address scored **6**.

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `stale_contact_page` | **−20** | `buyer.stale is true`, or the best evidence for claim `contact_channels` carries `stale: true` |
| `contact_channels_verified_empty` | **−15** | `contact_channels` is **present and empty** (checked, none exists). An **absent** array is unknown and takes the neutral penalty instead |

#### Worked

| Band | Inputs | Arithmetic | Score |
|---|---|---|---|
| **High** | Brand-partnership form, `sourcing@<own domain>`, a phone number → 3 distinct pairs | `70 + 15 + 15 = 100` of 100 | **100** |
| **Medium** | A contact page and a phone number → 2 pairs; no corporate-domain email | `45 + 10 + 0 = 55` of 100 | **55** |
| **Unknown** | `contact_channels` **absent** — all three criteria unknown | `21 + 5 + 5 = 31` of 100 | **31** |
| **Low** | `contact_channels: []` — checked, nothing published | `0 + 0 + 0 = 0`, then `−15` → clamp | **0** |

The Unknown-vs-Low pair is the whole point of the three-state rule: *we have not looked* scores 31,
*we looked and there is nothing* scores 0.

#### What does NOT count — and is forbidden

- An email address **guessed** from a pattern (`firstname.lastname@domain`). Never generate, never store, never score.
- A named employee's personal email or direct phone number harvested from a directory.
- A LinkedIn profile of an **individual** (only the company profile is a `linkedin` channel).
- A contact form behind a login, CAPTCHA or paywall — retrieving it is prohibited, so it can never become evidence at all.
- A WhatsApp number belonging to a person rather than the business.
- Duplicate entries of the same channel — B-RE2 counts **distinct** pairs.

---

### 1.6 `evidence_quality` — weight 10

The section 3 function, computed over the buyer material claims
(`evidence.material_claims.buyer`): `company_type`, `product_categories`,
`korean_products_signal`, `wholesale_signal`, `partnership_signal`, `sourcing_intent`, `country`,
`contact_channels`.

No criteria, no adjustments, no renormalisation. A record with zero evidence items scores **0** and
is rendered `unverified`. It is **not** rejected.

---

## 2. Seller rubric

**Weights** (`seller.weights`, sum 100):

| # | Dimension | Weight | Match component it becomes |
|---|---|---|---|
| 1 | `product_fit` | **25** | `product_fit` |
| 2 | `commercial_model` | **20** | `model_fit` |
| 3 | `operational_fit` | **20** | `operation_fit` |
| 4 | `compliance_readiness` | **15** | `compliance_fit` |
| 5 | `export_readiness` | **10** | `market_fit` |
| 6 | `evidence_quality` | **10** | `evidence_quality` |

```
qualification_score = round_half_up(
    ( 25*product_fit + 20*commercial_model + 20*operational_fit
    + 15*compliance_readiness + 10*export_readiness + 10*evidence_quality ) / 100 )
```

**One rubric, two query surfaces.** In *seller discovery* the query is the operator's filter set. In
*matching* the RFQ is projected onto the same surface. There is no second signal table, so a seller
can never be scored by two different rubrics. Combination rule: identical to the buyer side.

---

### 2.1 `product_fit` — weight 25

*Korean gloss: 제품 적합도.*

#### S-PF1 — "Category coverage" · max 60 · `max`

| Signal key | Pts | Category relation (seller vs query) |
|---|---|---|
| `category_exact_match` | **60** | `exact` |
| `category_parent_match` | **42** | `parent` |
| `category_adjacent_match` | **24** | `adjacent` |
| `category_no_match` | **0** | `none` |

> When the query or RFQ names **no in-vocabulary category**, S-PF1 is **inapplicable**
> (`inapplicable_when: query.product_categories_absent` in `scoring.config.json`, read by both
> `score_seller.py` and `score_match.py`) rather than firing `category_no_match` (0) — a seller must
> not be punished for a filter the operator never set. An RFQ whose only category is a vertical marker
> (`k_beauty`) counts as absent. A seller slug outside the 8.5 vocabulary is dropped with a note; a
> list that keeps none is **unknown** (S-PF1 18, HF-01 skipped), never a verified mismatch.

#### S-PF2 — "Form / formulation match" · max 25 · `max` · inapplicable when `query.product_forms_absent`

| Signal key | Pts | Fires when |
|---|---|---|
| `form_exact_match` | **25** | `query.product_forms` is a subset of `seller.product_forms` |
| `form_partial_match` | **15** | The two intersect, but not as a superset |

#### S-PF3 — "Product evidence depth" · max 15 · `sum_capped`

Deciding input: evidence items on claim `product_categories`.

| Signal key | Pts | Fires when |
|---|---|---|
| `named_product_page` | **8** | At least one such item whose `source_url` is a specific named product page on the seller's own domain |
| `catalog_or_pdf_published` | **5** | A downloadable catalogue / product PDF published by the seller |
| `multi_category_coverage` | **4** | `len(seller.product_categories) >= 3` |

#### Adjustment

| Adjustment | Delta | Fires when |
|---|---|---|
| `category_no_match_verified` | **−40** | `product_categories` is **present, non-empty** and the relation is `none`. An absent list is unknown and this never fires |

#### Worked

| Band | Inputs (query `[sunscreen]`, forms `[cream]`) | Arithmetic | Score |
|---|---|---|---|
| **High** | `[sunscreen, skincare, cleanser]`, forms `[cream, lotion, stick]`, a named product page + a catalogue PDF + 3 categories | `60 + 25 + min(8+5+4, 15) = 60 + 25 + 15 = 100` | **100** |
| **Medium** | `[suncare]` (parent), forms `[lotion, cream]` overlapping but not a superset of a two-form request, one named product page | `42 + 15 + 8 = 65` of 100 | **65** |
| **Renormalised** | `[sunscreen, skincare, cleanser]`, but the RFQ names **no** form → S-PF2 inapplicable; one named product page only | `60 + 8 = 68` of `60 + 15 = 75` → `100 × 68 / 75 = 90.666…` → round | **91** |
| **Low** | `[haircare]` (present, non-empty, no relation); no form overlap; no product evidence → S-PF3 unknown | `0 + 0 + 5 = 5` → raw 5, then `−40` → clamp | **0** |

#### Unknown path

S-PF1 → 18 when `product_categories` is absent. S-PF2 → 8 when applicable and `product_forms` is
absent. S-PF3 → 5 when no evidence item carries `claim == "product_categories"`.

#### What does NOT count

- A category the seller only **distributes for a third party** while the record claims `company_type: manufacturer` — distribution is not production capability.
- A "coming soon" / "in development" product.
- An **ingredient or raw-material** page: an emulsifier supplier is not a sunscreen manufacturer.
- A category named only in an SEO meta-keyword tag or a footer keyword block.
- A category appearing only in a third-party directory's auto-generated taxonomy.

---

### 2.2 `commercial_model` — weight 20

*Korean gloss: 거래 모델 적합도.* Let `M = query.commercial_model`; the flag it consults is fixed:
`branded → brand_export`, `private_label → private_label`, `oem_odm → oem_odm`.

#### S-CM1 — "Requested commercial model supported" · max 60 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `required_model_supported` | **60** | `M != "either"` and the mapped flag is `true` |
| `either_model_any_supported` | **56** | `M == "either"` and at least one of `oem_odm` / `private_label` / `brand_export` is `true` |
| `related_model_supported` | **36** | `M != "either"`, the mapped flag is **unknown**, and at least one of the other two is `true` — the near miss: the RFQ wants private label, the seller confirms OEM/ODM |

When the mapped flag is `false`, no signal fires (→ 0) and the `required_model_false` adjustment
fires. Hard filter HF-02 normally removes such a seller before scoring; the adjustment exists for
discovery runs with no hard filter.

#### S-CM2 — "Commercial model breadth" · max 12 · `sum_capped`

| Signal key | Pts | Fires when |
|---|---|---|
| `oem_odm_true` | **5** | `oem_odm is true` |
| `private_label_true` | **5** | `private_label is true` |
| `brand_export_true` | **2** | `brand_export is true` |

#### S-CM3 — "Commercial terms published" · max 8 · `sum_capped`

| Signal key | Pts | Fires when |
|---|---|---|
| `published_oem_odm_page` | **5** | An evidence item on claim `oem_odm` with `is_official == true` **and** `source_tier <= 2` |
| `published_moq_or_pricing_terms` | **3** | An evidence item on claim `moq` (or published pricing terms) with `is_official == true` |

#### S-CM4 — "Manufacturer role" · max 20 · `max` · input `seller.company_type`

| Signal key | Pts | Fires when |
|---|---|---|
| `company_type_oem_odm` | **20** | `company_type == "oem_odm"` |
| `company_type_manufacturer` | **20** | `company_type == "manufacturer"` |
| `company_type_brand` | **8** | `company_type == "brand"` |
| `company_type_distributor` | **4** | `company_type == "distributor"` |
| `company_type_other` | **2** | `company_type == "other"` — the agency/intermediary bucket of `references/seller-discovery.md` §6 |

`references/seller-discovery.md` §6 exists to stop a trading company or a brand-only marketer being
promoted as a factory — "the most common discovery error" in its own words — yet before S-CM4 **no
seller dimension and no hard filter read `company_type` at all**, so that whole section had zero
effect on the ranking. In a live Korean run a self-described supply-chain matching partner, typed
`other` from its own page language and publishing no manufacturing registration, scored 63 and
ranked **above** the two tier-1 houses that manufacture the majority of Korean sunscreen. Nothing is
rejected here: a distributor or an agency is **priced**, and can still rank on product fit, export
readiness and evidence quality.

#### Adjustment

| Adjustment | Delta | Fires when |
|---|---|---|
| `required_model_false` | **−75** | `M != "either"` and the mapped flag is `false` |

(The adjustment stays at −75 on the 0..100 **dimension** scale; it is not a criterion point value, so
S-CM1's re-scaling from 75 to 60 does not change it.)

#### Worked

| Band | Inputs (`M = private_label`) | Arithmetic | Score |
|---|---|---|---|
| **High** | `company_type: oem_odm`; `private_label: true`, `oem_odm: true`, `brand_export: true`; an official OEM/ODM page (tier 1) and published MOQ terms | `60 + (5+5+2 = 12) + (5+3 = 8) + 20 = 100` | **100** |
| **Medium (partial unknown)** | `company_type: oem_odm`; `private_label: true`, `oem_odm: true`, `brand_export` **absent**; official OEM/ODM page only | `60 + (5+5 = 10) + 5 + 20 = 95` → raw 95. `brand_export` is a **partial** unknown: it goes to `notes[]`, not `missing[]`, and emits **no** penalty entry | **95** |
| **Broker** | Same flags as High, but `company_type: other` (a matching agency that contracts the work out) | `60 + 12 + 8 + 2 = 82` | **82** |
| **Near miss** | `company_type: manufacturer`; `private_label` **unknown**, `oem_odm: true`; nothing else published → S-CM3 unknown | `36 + 5 + 2 + 20 = 63` of 100 | **63** |
| **Low** | `company_type: brand`; `private_label: false`, `brand_export: true`; S-CM3 unknown | `0 + 2 + 2 + 8 = 12` → raw 12, then `−75` → clamp | **0** |

#### Unknown path

S-CM1 → 18 when `M != "either"` and the mapped flag **and** the other two are all unknown, or when
`M == "either"` and all three are unknown. S-CM2 → 4 when all three flags are unknown (if only
*some* are unknown the criterion stays `scored`). S-CM3 → 2 when neither claim `oem_odm` nor claim
`moq` has any evidence item. S-CM4 → 6 when `company_type` is unknown.

#### What does NOT count

- The string "OEM" in a meta-keyword tag, a footer, or a stock-photo caption.
- "We can customise the packaging / print your logo on the box" — decoration, not private-label **production**. `private_label` requires an offer to manufacture under the customer's brand.
- A **trading company reselling other makers' goods** described as an "OEM partner" — not `oem_odm: true` unless the seller itself manufactures or contracts the manufacture.
- A marketplace storefront's "Business Type: Manufacturer" self-declaration alone (tier 4). It may be recorded as evidence; it never fires `published_oem_odm_page`, which demands tier ≤ 2 **and** official.

---

### 2.3 `operational_fit` — weight 20

*Korean gloss: 운영 조건 적합도 (MOQ / 리드타임 / 생산능력).*

#### MOQ handling — the rule the whole dimension turns on

- **Scalar** `moq: 3000` → compare `3000`.
- **Range** `moq: {"min": 1000, "max": 5000}` → compare the **`min`** (1000). The range states the lowest quantity the seller will accept, which is what a MOQ ceiling tests. Hard filter HF-03 uses the same value, so the filter and the score can never disagree.
- **`{"max": 5000}`** ("up to N") → normalises to `{"min": 0, "max": 5000}`.
- **`{"min": 1000}`** ("from N", open upper bound) → **unknown**, with a note. It is never turned into `{"min":1000,"max":1000}`, into `+inf`, or into the ceiling.
- **Unknown / absent** → S-OP1 takes **17**, HF-03 is **skipped** (never a rejection), `"seller.moq"` enters `missing[]`, and one `unknown_penalty_applied[]` entry is emitted.
- **Unit mismatch** (`moq_unit: "kg"` against a query in `units`) → the comparison cannot be made: S-OP1 is **unknown** (17), HF-03 is skipped, **and** `moq_unit_mismatch` (−10) fires. An absent unit means `"units"`. Units are compared after `_common.UNIT_SYNONYMS`, so `pcs`, `pieces`, `EA`, `개` and `unit` all equal `units` and are **not** a mismatch; only a different basis (`kg`, `sets`) is.
- **"MOQ negotiable" with no number is not a MOQ.** `moq` stays unknown; only `moq_negotiable_statement` (+5) fires.
- **Unit precedence:** when both `moq_unit` and `moq.unit` are present, **`moq_unit` wins** and a note naming both is recorded.

#### S-OP1 — "MOQ against the buyer ceiling" · max 55 · `max`

`v` = the compared MOQ, `C = query.max_moq`, `r = hard_filter_tolerances.moq_overshoot_ratio = 0.0`.

| Signal key | Pts | Fires when |
|---|---|---|
| `moq_at_or_below_half_of_max` | **55** | `C` is a number and `v <= C / 2` |
| `moq_at_or_below_max` | **47** | `C` is a number and `v <= C` |
| `moq_no_constraint_known` | **44** | `C` is null/absent and `v` is known |
| `moq_within_tolerance_of_max` | **25** | `C` is a number and `v <= C × (1 + r)` |
| `moq_above_max` | **0** | `C` is a number and `v > C × (1 + r)` |

With `r = 0.0`, `moq_within_tolerance_of_max` is structurally unreachable. It MUST still be
implemented, because an operator may raise the ratio.

#### S-OP2 — "Lead time against the deadline" · max 25 · `max`

`v = ` the **max** of `lead_time_days` (worst case), `C = query.max_lead_time_days`,
`r = lead_time_overshoot_ratio = 0.0`.

| Signal key | Pts | Fires when |
|---|---|---|
| `lead_time_within_requirement` | **25** | `C` is a number and `v <= C` |
| `lead_time_no_constraint_known` | **20** | `C` is null/absent and `v` is known |
| `lead_time_within_tolerance` | **12** | `C` is a number and `v <= C × (1 + r)` (unreachable at `r = 0.0`) |
| `lead_time_exceeds_requirement` | **0** | `C` is a number and `v > C × (1 + r)` |

#### S-OP3 — "Capacity against the order quantity" · max 20 · `max`

`cap` = the **max** of `monthly_capacity_units`, `qty` = the **max** of `query.quantity`.

| Signal key | Pts | Fires when |
|---|---|---|
| `capacity_covers_quantity_2x` | **20** | `qty` known and `cap >= 2 × qty` |
| `capacity_covers_quantity` | **16** | `qty` known and `cap >= qty` |
| `capacity_no_constraint_known` | **16** | `qty` absent/unknown and `cap` known |
| `capacity_below_quantity` | **0** | `qty` known and `cap < qty` |

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `moq_negotiable_statement` | **+5** | The seller publicly states the MOQ is negotiable / flexible |
| `sample_or_trial_order_accepted` | **+3** | The seller publicly accepts sample or trial orders |
| `moq_unit_mismatch` | **−10** | The MOQ comparison returned a unit mismatch |

#### Worked

| Band | Inputs (query `max_moq 3000`, `quantity 5000`, no lead-time ceiling) | Arithmetic | Score |
|---|---|---|---|
| **High** | `moq: 1200` (≤ 1500), `lead_time_days: 30` against a 60-day ceiling, `monthly_capacity_units: 150000` | `55 + 25 + 20 = 100` | **100** |
| **Medium** | `moq: 2500` (≤ 3000, > 1500), `lead_time_days: 45` with no ceiling, `monthly_capacity_units: 150000` (≥ 2 × 5000); the site accepts trial orders | `47 + 20 + 20 = 87` → raw 87, then **+3** | **90** |
| **Low (T04)** | `moq` **absent**, `lead_time_days: 60` with no ceiling, `monthly_capacity_units` **absent** | `17 + 20 + 6 = 43` of 100 | **43** |

The Low row is exactly what test T04 protects. Under the forbidden "unknown = 0" rule the same
seller would score **20**; under the forbidden "unknown = reject" rule it would not exist at all.

#### Unknown path

S-OP1 → 17, S-OP2 → 8, S-OP3 → 6; all three → **31**. Three `unknown_penalty_applied[]` entries do
**not** by themselves trip the marker on a seller record: the seller limit is **4**
(`unknown.max_unknown_dimensions_before_flag_by_entity.seller`), precisely because MOQ, lead time and
capacity are the three terms Korean makers publish least often, so flagging every record that omits
them tells a reviewer nothing. A fourth unknown entry elsewhere on the record does trip it, and the
`evidence_gap` note must then **name** these claims — *"확인 필요 / needs verification: MOQ, lead time,
capacity"* — not assert a generic gap.

#### What does NOT count

- A **per-SKU** MOQ quoted in kilograms against a query in units — unit mismatch, not a number.
- A **yearly** capacity figure used as `monthly_capacity_units`. Dividing by 12 is permitted **only** as its own evidence item carrying `inferred: true`, which then exposes it to the inferred penalty.
- An MOQ quoted for a *different product line* than the requested category.
- "Small orders welcome" / "no minimum for samples" — neither is an MOQ value.
- A lead time that covers shipping only, presented as production lead time.

---

### 2.4 `compliance_readiness` — weight 15

*Korean gloss: 인증/규제 준비도.* Certification tokens are normalised on **both** sides before any
set operation, so an RFQ asking for `"ISO 22716"` and a seller publishing `"ISO22716 GMP 인증"`
intersect.

#### S-CP1 — "Required certifications held" · max 55 · `max` · inapplicable when `query.required_certifications_absent`

`held` = the seller's normalised tokens, `req` = the RFQ's, `hit = |held ∩ req|`.

| Signal key | Pts | Fires when |
|---|---|---|
| `all_required_certifications_held_verified` | **55** | `req` is a subset of `held` **and** `certifications_verified is true` |
| `all_required_certifications_held_unverified` | **38** | `req` is a subset of `held`, but `certifications_verified` is not `true` |
| `most_required_certifications_held` | **35** | `hit / |req| >= 0.5` and not all |
| `some_required_certifications_held` | **20** | `hit >= 1` and `hit / |req| < 0.5` |
| `no_required_certifications_held` | **0** | `hit == 0` |

The `_verified` / `_unverified` split reads `seller.certifications_verified`, which is `true` only when
an official source presented the list as the company's **complete** set — a dedicated 인증현황 page or a
certifier registry (`references/seller-discovery.md` §8). Before the split, that field had **no effect
on the score at all**: the `certifications_verified_official` adjustment (+5) was clamped away at the
100 ceiling, and `certification_claim_unverified` (−10) is conditioned on tier 4–5 sourcing rather than
on verification. Measured: a maker with a dedicated certificate page listing CGMP (국문·영문) and ISO
22716 as certificate images scored `compliance_readiness` **100**, and so did a maker whose only
evidence was a marketing bullet list introduced as "주요 인증을 보유하여", with no certificate number and
no certificate image. Those are not the same claim and no longer score the same.

#### S-CP2 — "Preferred certifications held" · max 15 · `max` · inapplicable when `query.preferred_certifications_absent`

| Signal key | Pts | Fires when |
|---|---|---|
| `all_preferred_certifications_held` | **15** | Every preferred token is held |
| `some_preferred_certifications_held` | **8** | At least one is held |

#### S-CP3 — "Destination-market registration" · max 20 · `max` · inapplicable when `query.destination_country_absent`

Look up the `regulatory_registrations` entry whose `market == query.destination_country`.

| Signal key | Pts | Fires when |
|---|---|---|
| `registered_in_destination_market` | **20** | An entry exists with `registration_status == "registered"` |
| `registration_in_progress` | **12** | An entry exists with `registration_status == "in_progress"` |
| `not_registered_in_destination_market` | **0** | The array is **present** (empty or not) and holds no `registered` / `in_progress` entry for that market |

`unknown` (→ 6) **only** when `regulatory_registrations` is **absent**, or the matching entry's
status is `"unknown"`.

#### S-CP4 — "Baseline quality certification" · max 10 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `iso22716_or_cgmp_held` | **10** | `held` intersects `{ISO22716, CGMP}` |
| `other_quality_certification_held` | **6** | `held` intersects `{ISO9001, ISO14001, GMP_KOREA, COSMOS, ECOCERT}` |

#### Adjustments

| Adjustment | Delta | Fires when |
|---|---|---|
| `certifications_verified_official` | **+5** | `certifications_verified is true` — the list was read from an official, **exhaustive** certification page |
| `certification_claim_unverified` | **−10** | At least one **held** token whose **best** supporting evidence sits at `source_tier` 4 or 5, **or** that **no** evidence item supports at all (an uncited claim is no more credible than a directory-cited one) |

`certification_claim_unverified` prices the **source**, not the list. It is deliberately **not** keyed
to `certifications_verified is not true`, and that decision is recorded rather than left open:
verification is already priced once, inside S-CP1 (**55** verified against **38** unverified), so a
further −10 on the same input would charge the same fact twice; *"is not true"* is also true of
**unknown**, which was the state of `certifications_verified` on 83% of a 12-company tier-1-verified
Korean population, so the adjustment would fire on most of the field and separate nobody; and it would
reach a record whose `certifications` are absent altogether, which has already paid the S-CP1 unknown
neutral — a second charge on unknown, which the unknown rule forbids. S-CP1 asks *"is the list
exhaustive?"*; this adjustment asks *"is the source credible?"*. Both questions are kept, each priced once.

One residual is **recorded for calibration, not fixed here**: S-CP1 is inapplicable when the query
names no required certification, so in that kind of discovery run the verified/unverified split never
applies and verification reaches the dimension only through the `+5`, which the ceiling usually clamps.
Closing it means making S-CP4 verification-aware — a scorer change, not a table change.

#### Worked

| Band | Inputs (RFQ requires `[ISO22716]`, destination `AE`) | Arithmetic | Score |
|---|---|---|---|
| **High** | `[ISO22716, CGMP]`, preferred `[HALAL]` also held, AE registration `registered`, `certifications_verified: true` | `55 + 15 + 20 + 10 = 100` → raw 100, then **+5** → clamp | **100** |
| **Medium (renormalised)** | `[ISO22716, CGMP, ISO9001]`, `certifications_verified: true`, AE registration `in_progress`; the RFQ names **no** preferred certifications → S-CP2 inapplicable | `55 + 12 + 10 = 77` of `55 + 20 + 10 = 85` → `100 × 77 / 85 = 90.588…` → round 91, then **+5** | **96** |
| **Unknown mix** | `[ISO22716]` on the seller's own tier-1 page, `certifications_verified` **unknown**, `regulatory_registrations` **absent**, no preferred list | `38 (_unverified) + 6(unknown) + 10 = 54` of 85 → `63.529…` → round 64; neither adjustment fires | **64** |
| **Low** | `certifications: []` (checked, none found), registration array present with no AE entry | `0 + 0 + 0 = 0` of the applicable maxima | **0** |

`certifications: []` is **known**, not unknown: `no_required_certifications_held` fires at 0 and
S-CP4 fires nothing. It is still **not** proof of absence for hard filter HF-04, which additionally
requires `certifications_verified == true`.

#### Unknown path

S-CP1 → 17 and S-CP2 → 5 (both when `certifications` is absent), S-CP3 → 6, S-CP4 → 3.

#### What does NOT count

- An ISO 22716 **logo image** with no certificate number, issuer or certification page: it can back a low-tier `certifications` claim, but it can never set `certifications_verified: true`.
- A certificate belonging to the seller's OEM partner, a sister company, or a plant the requested product is not made in.
- An **expired** certificate (expiry earlier than `as_of`).
- "GMP-compliant facility" as prose with no scheme named → ambiguous → **nothing** enters `certifications`, and a risk/conflict note is recorded instead.
- "FDA approved" for a cosmetic (cosmetics are not FDA-approved) → not a certification claim at all.
- A third-party directory's certification checkbox (tier 4) — admissible as evidence, but it fires `certification_claim_unverified` (−10) and never `certifications_verified_official`.

---

### 2.5 `export_readiness` — weight 10

*Korean gloss: 수출 준비도.*

#### S-EX1 — "Export track record" · max 45 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `exports_to_destination_market` | **45** | `query.destination_country` is in `seller.export_markets` |
| `exports_to_same_region` | **32** | `export_markets` intersects `query.region_countries` |
| `exports_to_destination_region` | **28** | A token in `seller.export_regions` covers `query.destination_country`, per `_common.EXPORT_REGION_COUNTRIES` |
| `exports_to_any_overseas_market` | **22** | `export_markets` is non-empty and holds at least one country other than `seller.country` |
| `exports_to_stated_region` | **18** | `export_regions` is non-empty and nothing stronger fired |
| `no_export_evidence` | **0** | Both lists are present and carry nothing usable — `export_markets` empty or holding only `seller.country`, and no region token |

When `query.destination_country` is absent (discovery), only the overseas / stated-region / no-evidence
signals can fire. The criterion is **unknown** only when `export_markets` **and** `export_regions` are
both absent.

The two `_region` signals exist because `export_markets` is alpha-2 only and Korean makers routinely
state export as a region: "중국, 미국, 영국, 체코, 일본, **동남아** 등", or "Asia, Europe, North America"
with no country at all. Writing country codes for a region word would invent facts (`§1.2` of the
seller playbook forbids it), and recording nothing threw the claim away — a 70-year-old exporter scored
`export_readiness` 69 with "Export track record" on its `Missing:` line. A region token is weaker
evidence than a named country, so each `_region` signal sits one tier below its country-level
equivalent. The membership table is a **read-only** vocabulary in `scripts/_common.py`: it answers
"does this region cover this country?" and never returns a country list, so no script can use it to
expand a region onto a record (BUILD-CONTRACT 8.7 — region expansion is the operator's job).

#### S-EX2 — "English or destination-language presence" · max 30 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `english_site_true` | **30** | `english_site is true` — a full English (or destination-language) site |
| `partial_english_materials` | **18** | An English catalogue, PDF or landing page, but not a full site |
| `english_site_false` | **0** | `english_site is false` |

#### S-EX3 — "Overseas partner recruitment" · max 25 · `max`

| Signal key | Pts | Fires when |
|---|---|---|
| `overseas_partner_signal_true` | **25** | `overseas_partner_signal is true` |
| `trade_show_exhibitor` | **18** | An evidence item with `source_type == "trade_show"` listing the seller as an **exhibitor** (not a visitor) |
| `export_inquiry_channel` | **12** | A published export / overseas-inquiry form, or an export-team contact channel |

The last two signals are evidence-derived, so S-EX3 is `unknown` **only** when the flag is unknown
**and** no exhibitor / export-channel evidence exists.

#### Adjustment

| Adjustment | Delta | Fires when |
|---|---|---|
| `excluded_market_includes_destination` | **−45** | `query.destination_country` is in `seller.excluded_markets` — the score-level mirror of HF-05, for discovery runs that skip hard filtering |

#### Worked

| Band | Inputs (destination `AE`, region `[AE, BH, KW, OM, QA, SA]`) | Arithmetic | Score |
|---|---|---|---|
| **High** | `export_markets: [AE, SG]`, full English site, actively recruiting overseas partners | `45 + 30 + 25 = 100` | **100** |
| **Medium** | `export_markets: [SA, KW, SG, JP]` — no AE, but SA and KW are in the region; full English site; overseas partner signal true | `32 + 30 + 25 = 87` | **87** |
| **Medium-low** | `export_markets: [VN, TH]` — overseas, neither AE nor in AE's region; full English site; overseas partner signal true | `22 + 30 + 25 = 77` | **77** |
| **Low** | `export_markets: []` (checked, none), `english_site: false`, S-EX3 unknown | `0 + 0 + 8 = 8` | **8** |

#### Unknown path

S-EX1 → 14 (when `export_markets` is absent), S-EX2 → 9, S-EX3 → 8. All three → **31**.

#### What counts — and what does NOT

**Counts:** a named destination market on the seller's own site or in a government/association trade
directory; an overseas distributor list; a customs/export-licence reference; an exhibitor entry at
an overseas trade show; a published export-inquiry channel; a full English or destination-language
site.

**Does NOT count:**

- An **English-language website is not export experience.** It fires S-EX2 only; it can never fire S-EX1.
- "We export worldwide" / "Global partners welcome" with **no named market** → not `export_markets` evidence. At most it supports `overseas_partner_signal`.
- Being *listed on* an international B2B marketplace.
- Attending an overseas trade show **as a visitor**.
- A domestic-only distributor network.
- An overseas *supplier* relationship (importing raw materials) — that is the wrong direction.

---

### 2.6 `evidence_quality` — weight 10

The section 3 function over the seller material claims (`evidence.material_claims.seller`):
`company_type`, `product_categories`, `oem_odm`, `private_label`, `moq`, `certifications`,
`export_markets`, `overseas_partner_signal`.

---

## 3. Evidence quality — the shared sixth dimension

One function, three callers: buyer dimension 6, seller dimension 6, and match component 6.

### 3.1 Per-item strength

```
item_strength(item) = round_half_up(source_tier_points[item.source_tier] * recency_multiplier(item))
```

| `source_tier` | Meaning | Points |
|---|---|---|
| 1 | The company's own site, including its contact / wholesale / export / OEM pages | **100** |
| 2 | Official trade-show, association, government or trade-agency directory (and `internal_record`) | **82** |
| 3 | Official LinkedIn or company-controlled social profile | **64** |
| 4 | Reputable third-party directory or press release | **46** |
| 5 | Community, blog, forum — supporting signal only | **20** |

| Recency bucket | Condition (`age_days = as_of − source_date`) | Multiplier |
|---|---|---|
| `fresh` | `age_days <= 90` | **1.00** |
| `current` | `90 < age_days <= 365` | **0.92** |
| `aging` | `365 < age_days <= 730` | **0.80** |
| `stale` | `age_days > 730` | **0.60** |
| (no date) | `source_date == "unknown"` | **0.92** |

Buckets are evaluated **in order, first match wins**. A negative age clamps to 0.
**`observed_at` is never used for recency** — it is *our* read time, not the content's date.

**An undated page is not a stale page.** The no-date multiplier is **0.92** — equal to `current`,
below `fresh`, above `aging` and `stale` — because that is the correct ordering: absence of a date
is weaker than a proven-fresh date and stronger than a proven-old one. It was 0.85, and against
this vertical that was a uniform tax rather than a discrimination: Korean company pages (회사소개 /
사업분야 / 인증현황 / 제품 / 문의 — the five the seller playbook sends you to) essentially never
print a date, and over 12 tier-1-verified makers exactly **one** record carried any dated evidence
while the other 11 records' 68 items all took `"unknown"`. The four buckets separated no two
candidates in that run. `stale_penalty` never fires on an unknown `source_date`: unknown is not
"older than 730 days".

**Where a date may come from when the body prints none** (`evidence.source_date_fallback`), in
order — each is a machine-published date *for that page*, so carrying it onto the item is a reading,
not an inference, and `inferred` stays `false`:

1. the HTTP `Last-Modified` response header;
2. the `<lastmod>` entry for that URL in the site's `sitemap.xml`;
3. a `dateModified` / `datePublished` in the page's own metadata (JSON-LD, `article:modified_time`,
   a `<time datetime>` element).

**Forbidden**: a footer `Copyright(c) 2021` (that dates the template, not the claim); the date of a
*different* page; the date of the newest 공지사항 / 뉴스 entry carried onto an undated 회사소개 or
인증현황 page — that date belongs to an evidence item whose `source_url` **is** that entry, which is
precisely why the playbooks send you to the notice board; and the wall clock, which nothing on the
scoring path may read. When no allowed fallback exists the value stays `"unknown"` and takes 0.92.
Inventing a date to dodge that is the defect EVID-05 forbids.

### 3.2 The three weighted components

```
covered         = material claims with >= 1 evidence item on that claim
best(c)         = the highest-strength item for claim c
                  (ties: lower source_tier, then more recent source_date, then smaller evidence_id)
source_strength = mean over covered claims of item_strength(best(c))     # 0 when nothing is covered
coverage        = 100 * |covered| / |material claims|
domains         = distinct canonical registrable domains across ALL evidence items on the record
corroboration   = min(100, max(0, 50 * (|domains| - 1)))                 # 0 with zero or one domain
```

`www.example.com` and `example.com` are **one** domain. That is what makes entity dedupe safe: merging duplicate
records can never inflate the independent-source count.

### 3.3 Bonuses and penalties (additive point deltas)

| Term | Value | Condition |
|---|---|---|
| `official_bonus` | **+10** | At least one item with `is_official == true` **and** `source_tier == 1` |
| `multi_source_bonus` | `min(15, max(0, 7 * (domain_count − 1)))` | The first domain earns nothing |
| `stale_penalty` | **−25** | `record.stale == true`, **or** `covered` is non-empty and every covered claim's best item is older than `stale_threshold_days = 730`. Applied **once**, never per item |
| `conflict_penalty` | **−10 per unresolved conflict**, total floored at **−20** | An evidence item carries a non-empty `conflicts_with` for which the record holds **no** `conflicts[]` entry naming that field. Counted once per **unordered pair** of evidence ids: two items cross-linked to each other are one conflict |
| `inferred_penalty` | **−8** | At least one material claim whose **only** supporting evidence carries `inferred: true` |

A conflict that *is* recorded in `conflicts[]` is a **resolved** conflict and costs nothing — the
entry already carries its `resolution`.

### 3.4 The formula

```
evidence_quality = clamp( round_half_up( 0.50 * source_strength
                                       + 0.35 * coverage
                                       + 0.15 * corroboration
                                       + official_bonus
                                       + multi_source_bonus
                                       + stale_penalty
                                       + conflict_penalty_total
                                       + inferred_penalty ),
                          0, 100 )
```

**No-evidence floor:** zero evidence items → `evidence_quality = 0`
(`evidence.no_evidence_score`). No bonus, no penalty, no partial credit. The record is rendered
**`unverified`** and is **not** rejected.

The bonuses sit *on top of* the weighted core, so a well-corroborated tier-1 record routinely
computes above 100 and is clamped. That is expected and the clamp is mandatory.

### 3.5 Worked

| Case | Evidence | Arithmetic | Score |
|---|---|---|---|
| **Single-domain, fully covered** | 8 items, all on the company's own domain, tier 1, official; 7 fresh, 1 with `source_date: "unknown"` | `Σ strength = 7×100 + 92 = 792`, `source_strength = 792/8 = 99.0`, `coverage = 100`, `corroboration = 0`, `official +10` → `0.50×99 + 0.35×100 + 0.15×0 + 10 = 94.5` | **95** |
| **Three domains, fully covered** | 6 tier-1 own-domain items + 1 tier-2 government directory + 1 tier-2 trade-show entry | `Σ strength = 6×92 + 75 + 75 = 702`, `source_strength = 702/8 = 87.75`, `coverage = 100`, `corroboration = min(100, 50×2) = 100`, `official +10`, `multi_source min(15, 7×2) = +14` → `43.875 + 35 + 15 + 24 = 117.875` → clamp | **100** |
| **Single domain, one claim uncovered** | 7 tier-1 own-domain items covering 7 of the 8 material claims; `moq` has no evidence because there is nothing to evidence; 5 fresh, one aged 661 days, one with no date | `Σ strength = 5×100 + 80 + 92 = 672`, `source_strength = 672/7 = 96.0`, `coverage = 100×7/8 = 87.5`, `corroboration = 0`, `official +10` → `48 + 30.625 + 0 + 10 = 88.625` | **89** |

The first row is the mechanism behind "evidence quality over result count": a single-domain record,
however official, forfeits the corroboration component (up to 15 points) and the multi-source bonus
(up to 15 points).

---

## 4. Worked end-to-end examples

### 4.1 Buyer — UAE K-Beauty distributor → **96/100**

Query surface: `country AE`, `product_categories [sunscreen]`, `vertical K-Beauty`, **no** channel
filter (so B-MR3 is inapplicable). `as_of 2026-09-12`, threshold fixed 70.

Record (abridged): `company_type: distributor`, `product_categories: [skincare, sunscreen, cleanser,
mask_sheet]`, `korean_products_signal: true`, six named Korean brands, `wholesale_signal: true`,
`partnership_signal: true`, `sourcing_intent: high`, `sourcing_signals: [open_call_for_suppliers,
partnership_page, trade_show_attendance]`, `channels: [wholesale, distributor_network, ecommerce]`,
a brand-partnership form plus a corporate-domain sourcing address, `stale: false`.

| Dimension | Criteria | Earned / Max | Adjustments | Score | Weight | Contribution | Un-rounded |
|---|---|---|---|---|---|---|---|
| `kbeauty_fit` | 55 (6 named Korean brands) + 22 (Korean collection page) + 15 | 92 / 100 | — | **92** | 20 | 18.40 | 1840 |
| `b2b_role` | 60 + 25 + 10 (own distributor network) | 95 / 100 | — | **95** | 20 | 19.00 | 1900 |
| `sourcing_intent` | 45 + (32 + 15 + 10 = 57 → capped 45) + 10 (59 days old) | 100 / 100 | — | **100** | 25 | 25.00 | 2500 |
| `market_relevance` | 55 + 35; B-MR3 **inapplicable** | 90 / **90** | — | **100** | 15 | 15.00 | 1500 |
| `reachability` | 70 (partnership form) + 10 (2 channels) + 15 | 95 / 100 | — | **95** | 10 | 9.50 | 950 |
| `evidence_quality` | 8 covered claims on one domain, all tier-1 official: 6 fresh (100), one 312 days old (92), one undated (92) → `784/8 = 98`; `0.50×98 + 0.35×100 + 0.15×0 + 10 = 94` | — | — | **94** | 10 | 9.40 | 940 |
| | | | | | **100** | **96.30** | **9630** |

```
qualification_score = round_half_up(9630 / 100) = round_half_up(96.3) = 96
qualified           = 96 >= 70  ->  true
confidence          = round_half_up(94/100 * 1.00 * 1.00 * 1.00, 2) = 0.94   -> HIGH
```

These are the numbers `tests/fixtures/buyers.golden.json` + `buyers.uae.discovery.expected.json`
actually produce for `BUY-gulfglow-example`, and `docs/SCORING-CONTRACT.md` §6.1 shows the same
record item by item. Section 3.5 row 1 above is a *generic* single-domain illustration with a
different date mix (7 fresh, 1 undated → 95); do not read it as this record.

Rendered (see `references/matching-rules.md` and the discovery output contract):

```
1. Gulf Glow Trading FZ-LLC — 96/100 — HIGH
   Type: Distributor / Wholesaler
   Why: carries 6 named Korean brands + open call for suppliers dated 2026-07-15 + operates its own distributor network
   Contact: Partnership Form — https://gulfglow.example/brand-partnership
   Missing: Published minimum order
```

`Missing: Published minimum order` is load-bearing: **no** criterion took the unknown path, so
`unknown_penalty_applied` is empty and no penalty label reaches `missing[]`; the one line comes from
`verification_gaps` (SCORING-CONTRACT §0.4), because the record publishes no `buyer_moq` and no
penalty entry already names `buyer.buyer_moq`. It changes no score. The one unknown *source date* is
an evidence-quality fact, already priced at ×0.92 inside `evidence_quality`; it is not a missing
material claim.

### 4.2 Seller — Korean sunscreen manufacturer, scored against RFQ #134

Query surface projected from the RFQ: `product_categories [sunscreen]`, **no** `product_forms`
(S-PF2 inapplicable), `commercial_model private_label`, `quantity 5000`, `max_moq 3000`,
`max_lead_time_days null`, `required_certifications [ISO22716]`, **no** preferred certifications
(S-CP2 inapplicable), `destination_country AE`, `region_countries [AE, BH, KW, OM, QA, SA]`.

Record (abridged): `country KR`, `company_type manufacturer`, `product_categories [sunscreen,
skincare, cleanser]`, `product_forms [cream, lotion, stick]`, `oem_odm/private_label/brand_export
all true`, `moq 2500 units`, `lead_time_days 45`, `monthly_capacity_units 150000`,
`certifications [ISO22716, CGMP, ISO9001]`, `certifications_verified true`, an AE registration
`in_progress`, `export_markets [SA, KW, SG, JP]`, `english_site true`,
`overseas_partner_signal true`, `operational_status active`.

| Dimension | Criteria | Earned / Max | Adjustment | Score |
|---|---|---|---|---|
| `product_fit` | S-PF1 60; S-PF2 **inapplicable**; S-PF3 `8 + 5 + 4 = 17` → capped 15 | 75 / **75** | — | **100** |
| `commercial_model` | S-CM1 60; S-CM2 `5+5+2 = 12`; S-CM3 `5+3 = 8`; S-CM4 20 (`oem_odm`) | 100 / 100 | — | **100** |
| `operational_fit` | S-OP1 47 (`2500 <= 3000`, `> 1500`); S-OP2 20 (no ceiling, 45 known); S-OP3 20 (`150000 >= 2×5000`) | 87 / 100 | `sample_or_trial_order_accepted` **+3** | **90** |
| `compliance_readiness` | S-CP1 55 (`_verified`: the certificate page is exhaustive); S-CP2 **inapplicable**; S-CP3 12 (`in_progress`); S-CP4 10 | 77 / **85** → `90.588…` → 91 | `certifications_verified_official` **+5** | **96** |
| `export_readiness` | S-EX1 32 (SA, KW in region; AE not an export market); S-EX2 30; S-EX3 25 | 87 / 100 | — | **87** |
| `evidence_quality` | section 3.5 row 2 | — | — | **100** |

Read through the **seller qualification** weights of section 2
(`25/20/20/15/10/10` — an arithmetic illustration; a standalone discovery score would be computed
against a discovery query surface, not this RFQ):

```
25*100 + 20*100 + 20*90 + 15*96 + 10*87 + 10*100
= 2500 + 2000 + 1800 + 1440 + 870 + 1000 = 9610
qualification_score = round_half_up(9610 / 100) = round_half_up(96.1) = 96
confidence          = round_half_up(100/100 * 1.00 * 1.00 * 1.00, 2) = 1.00  -> HIGH
```

The same six numbers read through the **match** weights (`0.30/0.20/0.15/0.15/0.10/0.10`) give a
`base_score` of **97**; `references/matching-rules.md` carries that calculation and what the rerank
may do to it.

---

## 5. Reviewer checklist

Before you accept a scored record, confirm every line:

- [ ] Every non-`"unknown"` material claim has at least one evidence item whose `claim` is that key.
- [ ] Nothing absent was written as `false`, `0`, `""`, `null` or `"N/A"`.
- [ ] Every criterion that took the unknown path has a matching `unknown_penalty_applied[]` entry, and its label appears in `missing[]`.
- [ ] Every `missing[]` line is either a penalty label or a configured `verification_gaps` label (a **partial** unknown not named there belongs in `notes[]`).
- [ ] Every `inapplicable` criterion appears in `dimension_details[]` with `state: "inapplicable"` and `earned_points: 0`, so the renormalisation is auditable.
- [ ] `qualification_score` reproduces from the recorded `dimension_scores` and the config weights.
- [ ] `confidence` did not read the qualification score, and a stale record is rendered **LOW** even when its score is high.
- [ ] No named individual, personal email, direct line or personal social handle appears anywhere — `notes[]` and `quote_or_summary` included.
- [ ] `score_version` and `as_of` are stamped on the record, and nothing in the run compared two different `score_version`s.
