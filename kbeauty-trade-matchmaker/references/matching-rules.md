# Matching Rules — RFQ to seller

The three-stage pipeline, every hard filter, the weighted formula, the unknown policy, and the
bounded protocol that governs the one place a model may move a number.
*Korean gloss: RFQ ↔ 셀러 매칭 규칙 — 하드 필터 → 정량 점수 → 의미 기반 재정렬 → Unknown 처리.*

| | |
|---|---|
| Applies to | `scripts/score_match.py` (full pipeline) and `scripts/score_buyer.py` / `scripts/score_seller.py` (the discovery subset of stage 1) |
| `score_version` | `kbtm-score-0.1.0` |
| Canonical `as_of` in every example | `2026-09-12` |
| **Binding source of every number** | `schemas/scoring.config.json` |
| Output document | `schemas/match-result.schema.json` |
| Companion pages | `references/qualification-rubric.md` (the six component rubrics), `references/evidence-policy.md`, `references/data-contract.md` |

> **The config wins, always.** Weights, tolerances, rule names, failure-message templates, the
> rerank limits and the threshold below are copies of `schemas/scoring.config.json` at
> `score_version kbtm-score-0.1.0`, reproduced so an operator can read the pipeline without opening
> JSON. The scripts read the config at run time. If this page and the config disagree, **the config
> is right and this page is a bug**.

---

## Table of contents

- [1. The pipeline at a glance](#1-the-pipeline-at-a-glance)
- [2. Stage 0 — projecting the RFQ onto the query surface](#2-stage-0--projecting-the-rfq-onto-the-query-surface)
- [3. Stage 1 — hard filters HF-01…HF-08](#3-stage-1--hard-filters-hf-01hf-08)
- [4. Stage 2 — the weighted score](#4-stage-2--the-weighted-score)
- [5. Stage 3 — semantic rerank](#5-stage-3--semantic-rerank)
- [6. Stage 4 — unknown handling](#6-stage-4--unknown-handling)
- [7. Threshold, ordering, and no-match](#7-threshold-ordering-and-no-match)
- [8. Behaviour table — T01…T10](#8-behaviour-table--t01t10)
- [9. Operator checklist](#9-operator-checklist)

---

## 1. The pipeline at a glance

Four stages, executed **strictly in this order** (`match.stages`):

```
hard_filter  ->  weighted_score  ->  semantic_rerank  ->  unknown_handling
```

| Stage | What it does | Who decides | Can it remove a candidate? |
|---|---|---|---|
| 1 · `hard_filter` | Applies absolute conditions (category, model, MOQ, certifications, markets, status, lead time, country) | Deterministic code | **Yes** — into `excluded[]` |
| 2 · `weighted_score` | Re-scores the six seller dimensions against this RFQ and weights them | Deterministic code | No |
| 3 · `semantic_rerank` | Adjusts the score by at most ±5 on evidence-quoted qualitative grounds | The model, under the section 5 protocol | **Never** |
| 4 · `unknown_handling` | Reports every unknown that was priced during stages 1 and 2 | Deterministic code | **Never** |

Stage 4 is not a post-pass that re-opens the arithmetic. The unknown penalty is applied *inside*
stages 1 and 2, at the moment an unknown input is met. Stage 4 is the **reporting and rendering
contract** for those applications, plus the two absolute prohibitions in section 6.

Every run produces exactly one `match-result` document, and that document **always** carries a
`no_match` object — even when results are plentiful.

---

## 2. Stage 0 — projecting the RFQ onto the query surface

Both discovery and matching score a record *against a request*. That request is normalised into one
**query surface** object. `score_match.py` derives it from the RFQ:

| Query-surface key | Source |
|---|---|
| `product_categories` | `rfq.product_category` ∪ `rfq.product_categories_extra` |
| `product_forms` | `rfq.product_forms` |
| `commercial_model` | `rfq.commercial_model` |
| `quantity` | `rfq.quantity` (compared on its **max**) |
| `max_moq` | `rfq.max_moq` |
| `moq_unit` | `rfq.moq_unit` when present; else `rfq.quantity.unit` when `rfq.quantity` is a range carrying a unit; else `"units"` |
| `required_certifications` / `preferred_certifications` | the same-named RFQ fields |
| `destination_country` / `country` | `rfq.destination_country` |
| `required_seller_countries` / `excluded_seller_countries` | the same-named RFQ fields |
| `max_lead_time_days` | `rfq.max_lead_time_days` when it is a number; **otherwise derived from `rfq.timeline`** |
| `region_countries` / `adjacent_region_countries` | the alpha-2 lists **the agent** expanded a region word into before the surface was built |

**Timeline derivation.** Let `D` be `rfq.timeline` if it is a date, or `rfq.timeline.end` if it is
`{start, end}`. Then `max_lead_time_days = max(0, days_between(as_of, D))`. If `rfq.timeline` is
`"unknown"` or absent, `max_lead_time_days = null` — no constraint, HF-07 inapplicable. An explicit
`rfq.max_lead_time_days` always **overrides** the derived value. When the derivation clamps a past
deadline to `0`, a note MUST be appended to `match-result.notes`.

**Regions are data, never code.** `"GCC"`, `"EU"` and `"MENA"` are expanded by the agent into
explicit alpha-2 lists *before* they enter the query surface; the scripts never expand a region. An
absent region list means the region-dependent signals simply **cannot fire** — that is not
`"unknown"` and carries no penalty, because their criteria are still decided by their exact-match
signals.

**Absent keys make criteria inapplicable, never unknown.** An absent or empty key sets the matching
condition flag (`query.country_absent`, `query.product_categories_absent`,
`query.product_forms_absent`, `query.channels_absent`, `query.required_certifications_absent`,
`query.preferred_certifications_absent`, `query.destination_country_absent`), which drops the
dependent criterion out of both numerator and denominator.

**RFQ readiness** is computed here too, and it is **not** a quality judgement of the buyer:

```
total  = 10   # destination_country, product_category, product_description, quantity, max_moq,
              # commercial_model, required_certifications, timeline, target_price, buyer_id
filled = count of those fields that are present and not "unknown"
rfq.qualification_score = round_half_up(100 * filled / total)
rfq.readiness_detail    = {"filled": filled, "total": total, "missing_fields": [sorted names]}
```

It never enters `match_score`, applies no unknown penalty, and never makes an RFQ "qualified" or
"rejected".

---

## 3. Stage 1 — hard filters HF-01…HF-08

*Korean gloss: 절대 조건 필터.*

**All applicable rules are evaluated for every seller. There is no short-circuit** — evaluation
continues past the first failure so the `Excluded` block can list every reason.

### 3.1 Per-rule bookkeeping

| Outcome | `rules_evaluated` | `rules_skipped_unknown` | `failed_rules` |
|---|---|---|---|
| Not applicable (`applies_when` false) | — | — | — |
| Applicable, passed | ✔ | — | — |
| Applicable, failed | ✔ | — | ✔ (one `failed_rule`) |
| Applicable, deciding input unknown | — | ✔ | — |

`unknown_behavior` has exactly two values in v0.1.0:

- **`pass`** — skip silently, record nothing.
- **`pass_with_unknown_penalty`** — skip the rule, append the `rule_id` to `rules_skipped_unknown`, **and** make sure a matching `unknown_penalty_applied[]` entry exists.

Every hard-filter failure carries `rule_id` (matching `^HF-[0-9]{2}$`), `rule_name` copied verbatim
from the config, a rendered `reason`, `observed_value` and `required_value`.

### 3.2 The eight rules

| Rule | Name | Applies when | Predicate (PASS when true) | On unknown input |
|---|---|---|---|---|
| `HF-01` | Product category | `rfq.product_category` is set (always — the field is required) | the category relation between `seller.product_categories` and `{product_category} ∪ product_categories_extra` is `exact` **or** `parent` | pass + unknown penalty |
| `HF-02` | Commercial model | `rfq.commercial_model != "either"` | the seller flag named by the model (`oem_odm` / `private_label` / `brand_export`) is **not `false`** | pass + unknown penalty |
| `HF-03` | Maximum MOQ | `rfq.max_moq` is a number | `moq <= rfq.max_moq × (1 + moq_overshoot_ratio)`, the MOQ compared on its **`min`** | pass + unknown penalty |
| `HF-04` | Required certifications | the required list is non-empty **and** `seller.certifications_verified == true` | the seller's normalised token set is a **superset** of the required set | pass + unknown penalty |
| `HF-05` | Excluded destination market | destination set **and** `seller.excluded_markets` non-empty | `rfq.destination_country` is not in `seller.excluded_markets` | pass (silent) |
| `HF-06` | Operational status | always | `seller.operational_status` is not `"closed"` and not `"unreachable"` | pass (silent) |
| `HF-07` | Hard lead-time deadline | `max_lead_time_days` is a number (explicit or derived) | `lead_time <= max_lead_time_days × (1 + lead_time_overshoot_ratio)`, the lead time compared on its **`max`** | pass + unknown penalty |
| `HF-08` | Seller country | the required or excluded seller-country list is non-empty | the country is in `required_seller_countries` (when set) **and** not in `excluded_seller_countries` | pass + unknown penalty |

Tolerances (`hard_filter_tolerances`): `moq_overshoot_ratio = 0.0`,
`lead_time_overshoot_ratio = 0.0` — both ceilings are **strict** in v0.1.0.

### 3.3 Rule by rule

#### HF-01 — Product category

```
required_value = {rfq.product_category} + rfq.product_categories_extra
observed_value = seller.product_categories

if seller.product_categories is absent or "unknown":
    SKIP -> rules_skipped_unknown += "HF-01"; unknown penalty pairs with S-PF1
else:
    PASS iff category_relation(observed_value, required_value) in {"exact", "parent"}
```

`{"exact", "parent"}` is exactly the config's "intersects after parent/child expansion". A
**sibling** category (`adjacent`, e.g. `serum` against a `sunscreen` request) does **not** clear the
hard filter — it is scored at 24 of 60 on S-PF1 instead. A **present-and-empty**
`product_categories` yields `none` and **FAILS**: it is a verified "handles no such category".

Message: `Product category {required_value} not covered (handles {observed_value})`

#### HF-02 — Commercial model

```
flag = {"branded": "brand_export", "private_label": "private_label", "oem_odm": "oem_odm"}[rfq.commercial_model]
observed_value = seller[flag]          # true | false | "unknown" | absent

if observed_value is unknown or absent:
    SKIP -> rules_skipped_unknown += "HF-02"
else:
    PASS iff observed_value is not false
```

Message: `{required_value} not offered (confirmed unavailable)`

#### HF-03 — Maximum MOQ · **T03 and T04 both live here**

```
v = the seller MOQ, normalised: scalar -> itself; {min,max} -> min; {max: N} -> 0; {min: N} -> UNKNOWN
    (a unit mismatch between seller.moq_unit and query.moq_unit also yields UNIT_MISMATCH)

if v is UNKNOWN or UNIT_MISMATCH:
    SKIP -> rules_skipped_unknown += "HF-03"; observed_value = "unknown"
else:
    PASS iff v <= rfq.max_moq * (1 + moq_overshoot_ratio)
```

- **T03:** a confirmed `moq = 5000` against `max_moq = 3000` → `5000 > 3000` → **FAIL**.
- **T04:** `moq` absent or `"unknown"` → **SKIP**; S-OP1 takes 17 of 55; the seller stays in `results`.

The difference between the two is *entirely* `v is UNKNOWN`. Coercing an absent MOQ to `0`, to
`+inf`, or to the ceiling violates this contract. The hard filter and the S-OP1 score read the
**same** normalised value, so they can never disagree.

Message: `MOQ minimum {observed_value} exceeds RFQ max {required_value}`

#### HF-04 — Required certifications · the "absence of proof is not proof of absence" rule

```
if seller.certifications_verified is not true:     # false, "unknown" or absent
    NOT APPLICABLE          # neither evaluated nor skipped; add a risk_item (category "compliance"):
                            # "certification list not verified as exhaustive"
elif seller.certifications is absent or "unknown":
    SKIP -> rules_skipped_unknown += "HF-04"
else:
    PASS iff required_tokens is a subset of observed_tokens
```

This is the **only** way a confirmed-absent certification rejects a seller: the list must be
officially exhaustive. Everything else is penalised, not rejected. Note the applicability branch —
an unverified list makes `applies_when` literally false, so HF-04 appears in **neither** array and
needs no unknown-penalty pairing.

Message: `Required certification {required_value} not held (verified list: {observed_value})`

**The RFQ's list is the RFQ's.** A destination market may make a certification effectively
mandatory in its own right — Indonesia's halal regime is the live example
(`references/compliance-notes.md` §5.1) — but the agent **never adds a token to
`required_certifications` that the RFQ did not state**. Doing so would hard-filter sellers on a
constraint the buyer never set. Raise it as a `risks[]` entry and a question for the human instead,
and leave HF-04 to price what the RFQ actually asked for.

#### HF-05 — Excluded destination market

```
applies_when: rfq.destination_country is set AND seller.excluded_markets is non-empty
PASS iff rfq.destination_country not in seller.excluded_markets
```

`unknown_behavior: pass` — silent skip, nothing recorded. In a discovery run that skips hard
filtering, the `excluded_market_includes_destination` adjustment (−45 on `export_readiness`) is the
score-level mirror of this rule.

Message: `Does not supply {required_value} (market excluded by the seller)`

#### HF-06 — Operational status · **"dead" rejects, "stale" does not**

```
applies_when: always
PASS iff seller.operational_status not in {"closed", "unreachable"}
```

`"unknown"` and `"active"` both pass. **`seller.stale == true` NEVER rejects.** Confirmed-dead means
the evidenced `operational_status`, not staleness:

- *Stale but reachable* — old content, `record.stale = true`, status `active` or `"unknown"`: **passes**, is penalised (`stale_penalty` −25 on evidence quality, `confidence × 0.6`) and renders **LOW**.
- *Evidenced unreachable* — `operational_status = "unreachable"`: a confirmed-dead business. HF-06 **fails** it into `excluded[]` in a match run.

Message: `Business {observed_value}`

#### HF-07 — Hard lead-time deadline

```
applies_when: rfq.max_lead_time_days is a number (explicit, or derived from rfq.timeline against --as-of)
v = the seller lead time compared on its MAX (worst case)

if v is UNKNOWN: SKIP -> rules_skipped_unknown += "HF-07"
else: PASS iff v <= rfq.max_lead_time_days * (1 + lead_time_overshoot_ratio)
```

Message: `Lead time {observed_value} days exceeds the {required_value} day deadline`

#### HF-08 — Seller country

```
if seller.country is absent or "unknown":
    SKIP -> rules_skipped_unknown += "HF-08"
            zero-weight unknown-penalty record keyed "HF-08"
            risk_item(evidence_gap, "seller country not established")
else:
    PASS iff (required_seller_countries is empty or seller.country in required_seller_countries)
             and seller.country not in excluded_seller_countries
```

Message: `Seller country {observed_value} is outside {required_value}`

### 3.4 Every skipped rule must be visible twice

A rule skipped for unknown input MUST appear in `rules_skipped_unknown` **and** be paired with an
`unknown_penalty_applied[]` entry. Normally the pairing is satisfied by the **existing** entry of
the criterion that consumes the same input:

| Rule | Entry that carries the penalty |
|---|---|
| HF-01 | criterion **S-PF1** (`seller.product_categories`) |
| HF-02 | criterion **S-CM1** when S-CM1 itself took the unknown path; **otherwise a zero-weight record** |
| HF-03 | criterion **S-OP1** (`seller.moq`) |
| HF-04 | criterion **S-CP1** (`seller.certifications`) |
| HF-07 | criterion **S-OP2** (`seller.lead_time_days`) |
| HF-08 | **zero-weight record** — no criterion consumes `seller.country` |

That criterion's `note` must additionally name the skipped rule, e.g.
`"MOQ not published; hard filter HF-03 skipped."` A **second** entry for the same `criterion_id`
MUST NOT be appended: it would inflate `len(unknown_penalty_applied)`, and with it the
`> max_unknown_dimensions_before_flag` test and `summary.unknown_flagged_count`, even though no
extra fact is unknown.

**Fallback.** When the criterion named above did **not** itself take the unknown path, the pairing is
satisfied by a **zero-weight record keyed on the `rule_id`** — it withholds no points, so the
arithmetic is untouched while the reviewer still sees the gap:

```json
{"dimension": "hard_filter", "criterion_id": "HF-08", "label": "Seller country",
 "unknown_inputs": ["seller.country"], "criterion_max_points": 0,
 "neutral_base": 50, "penalty_factor": 0.6, "applied_points": 0,
 "note": "Seller country not established; HF-08 skipped. No criterion consumes this input, so no points were withheld."}
```

This is not hypothetical for HF-02: with `commercial_model = private_label`,
`private_label = "unknown"` and `oem_odm = true`, HF-02 is skipped while S-CM1 is **scored** at 36
(`related_model_supported`) and emits no penalty entry at all.

**No hard filter reads `company_type`,** and that is deliberate: a trading company or a brand-only
marketer is *priced*, not rejected. It is priced by **S-CM4** ("Manufacturer role", max 20) inside
`commercial_model` — the criterion that finally gives `references/seller-discovery.md` §6 an effect on
the ranking. Before it existed, a self-described supply-chain matching partner outranked the two
largest Korean ODM houses in a live run.

A zero-weight record's `label` still flows into `missing[]` and is still rendered **확인 필요**. A
`risk_item` of category `evidence_gap` should accompany it.

### 3.5 Outcome

`hard_filter.passed = (failed_rules == [])`. A seller with `passed == false` moves into `excluded[]`
with `reason_summary` set to the **first** failed rule's `reason` in config rule order, carries
**no** `match_score`, and is never silently dropped.

Excluded example (T03):

```json
{
  "seller_id": "SEL-daehansuncare-example",
  "seller_name": "Daehan Sun Care Co., Ltd.",
  "canonical_domain": "daehansuncare.example",
  "reason_summary": "MOQ minimum 5,000 exceeds RFQ max 3,000",
  "failed_rules": [{
    "rule_id": "HF-03",
    "rule_name": "Maximum MOQ",
    "reason": "MOQ minimum 5,000 exceeds RFQ max 3,000",
    "observed_value": 5000,
    "required_value": 3000,
    "evidence_ids": ["EV-201"]
  }],
  "unknown_penalty_applied": []
}
```

`unknown_penalty_applied` is empty precisely so an operator can see at a glance that the rejection
was driven by a **known** violating value (T03), not by silence (T04).

Numbers inside a rendered `reason` are thousands-separated with `,` in en-US grouping only
(`3,000`), never locale-derived. JSON numbers never carry separators.

### 3.6 Seller discovery applies the same filters, minus two — buyer discovery applies none

`score_seller.py` evaluates **HF-01, HF-02, HF-03, HF-04, HF-05 and HF-08** against the discovery
query surface with identical predicates, applicability and unknown behaviour — the same code path,
not a second implementation. A record failing an applicable rule leaves `records[]` and enters the
discovery envelope's `excluded[]` with a `reason_summary` and `failed_rules[]`, and is never ranked
or rendered under `Top Candidates`.

Two rules are deliberately **not** applied in seller discovery:

- **HF-06** — an unreachable record must still be surfaced, flagged, and rendered `LOW` rather than dropped.
- **HF-07** — a discovery query carries no RFQ deadline; lead time is priced by S-OP2 instead.

**Buyer discovery applies no `HF-xx` rule at all.** Every configured predicate in
`scoring.config.json hard_filters` reads a `seller.*` or `rfq.*` input, so none is even expressible
against a buyer record, and PRD 15.2 — the clause the build contract cites here — is headed "Seller
Discovery". `score_buyer.py` therefore excludes on exactly two grounds: a query `company_type`
mismatch, and the DISC-06 no-evidenced-material-claim gate. Buyer category and country fit are
**priced**, not filtered — by B-MR2, B-MR1 and B-KF3 — which is what PRD 15.1 (≥ 20 candidates) and
PRD test T02 ("prioritise", not "exclude") require. An exclusion from a buyer run carries an empty
`failed_rule_ids`, and that is correct, not a gap.

`--no-hard-filter` disables the pass for exploratory runs and MUST append a note saying so — in both
scorers.

### 3.7 HF-00 — Evidence sufficiency (a rendering gate, not a config rule)

A candidate that cannot produce **two** evidence-backed rationale items is moved out of `results`
into `excluded[]` with `reason_summary` `"fewer than two evidenced fit reasons (PRD 15.3)"` and one
`failed_rule` carrying `rule_id "HF-00"`, `rule_name "Evidence sufficiency"`.

`HF-00` is evaluated **after stage 3**, is defined in the build contract rather than in
`scoring.config.json`, and appears in no config table — do not look for it there. Emitting an
invalid document instead of applying the gate is a build blocker.

Rationale and risks themselves are the **AI-authored half** of a candidate and MUST NOT influence
any number. When the agent supplies none, `score_match.py` synthesises them deterministically: one
rationale per component in descending `weighted_contributions` order whose top fired signal carries
at least one evidence id (take the first two that qualify), and one `evidence_gap` risk of severity
`medium` per `unknown_penalty_applied[]` entry.

---

## 4. Stage 2 — the weighted score

```
match_score_base_raw = product_fit       * 0.30
                     + model_fit         * 0.20
                     + operation_fit     * 0.15
                     + compliance_fit    * 0.15
                     + market_fit        * 0.10
                     + evidence_quality  * 0.10
base_score = round_half_up(match_score_base_raw)          # integer 0..100
```

`match.weights` sums to `1.0` and is copied into `weights_used` on the result document, so an old
match can be re-derived after the config changes.

### 4.1 The six components ARE the six seller dimensions

There is no second signal table. Each component is the seller dimension of
`references/qualification-rubric.md` section 2, **re-scored** with the RFQ projected onto the query
surface — never copied from a previously stored `seller.dimension_scores`, which was computed
against a different query.

| Component | Seller dimension | RFQ → query projection | Render label |
|---|---|---|---|
| `product_fit` | `product_fit` | `product_categories`, `product_forms` | `Product Fit` |
| `model_fit` | `commercial_model` | `commercial_model` | `Model Fit` |
| `operation_fit` | `operational_fit` | `max_moq`, `quantity`, `max_lead_time_days` | `MOQ` |
| `compliance_fit` | `compliance_readiness` | `required_certifications`, `preferred_certifications`, `destination_country` | `Compliance` |
| `market_fit` | `export_readiness` | `destination_country` (+ `region_countries`) | `Market Fit` |
| `evidence_quality` | `evidence_quality` | none — the RFQ does not change it | `Evidence Quality` |

Consequences that MUST hold:

- Each `component_scores[k]` is an **integer 0..100**, produced by normalise → round → adjust → clamp.
- `weighted_contributions[k] = round_half_up(component_scores[k] × weights_used[k], 2)` is **display only**. The sum of the **un-rounded** products is what gets rounded into `base_score`; never sum the rounded contributions.
- A component whose criteria are partly inapplicable **renormalises** — `product_fit` with no requested form is scored over `60 + 15 = 75` points, not 100.

A match-result document embeds no seller documents, so each candidate additionally carries, copied
**by value** at match time, the fields the output block renders: `company_type`, `oem_odm`,
`contact_channels[]` and `website`. They are display carry-over only and are never read by the match
arithmetic.

### 4.2 Worked — a passing seller

Components for the Korean sunscreen manufacturer of `references/qualification-rubric.md` section 4.2,
scored against RFQ #134:

| Component | Score | Weight | `weighted_contribution` |
|---|---|---|---|
| `product_fit` | 100 | 0.30 | **30.00** |
| `model_fit` | 100 | 0.20 | **20.00** |
| `operation_fit` | 90 | 0.15 | **13.50** |
| `compliance_fit` | 96 | 0.15 | **14.40** |
| `market_fit` | 87 | 0.10 | **8.70** |
| `evidence_quality` | 100 | 0.10 | **10.00** |
| | | **1.00** | **96.60** |

```
base_score = round_half_up(96.60) = 97
```

Re-check: `30.00 + 20.00 = 50.00`; `+13.50 = 63.50`; `+14.40 = 77.90`; `+8.70 = 86.60`;
`+10.00 = 96.60`.

### 4.3 Worked — the T04 seller, kept and priced

A seller with **no published MOQ** and **no published capacity**, otherwise strong:

| Component | Score | Weight | `weighted_contribution` |
|---|---|---|---|
| `product_fit` | 91 | 0.30 | **27.30** |
| `model_fit` | 95 | 0.20 | **19.00** |
| `operation_fit` | **43** | 0.15 | **6.45** |
| `compliance_fit` | 64 | 0.15 | **9.60** |
| `market_fit` | 77 | 0.10 | **7.70** |
| `evidence_quality` | 86 | 0.10 | **8.60** |
| | | **1.00** | **78.65** |

```
base_score = round_half_up(78.65) = 79
```

`operation_fit = 43` is `17 (S-OP1 unknown) + 20 (S-OP2 scored) + 6 (S-OP3 unknown)` over 100. Under
the forbidden "unknown = 0" rule the component would have been 20 and the match score 76; under the
forbidden "unknown = reject" rule the seller would not appear at all. `compliance_fit = 64` is the
S-CP1 verified/unverified split biting: this seller lists `ISO22716` in prose and never says the
list is complete, so it fires `all_required_certifications_held_unverified` (38 of 55), not the
verified 55.

Each of the three unknown entries emits its own `evidence_gap` risk item naming the criterion.
Three does **not** trip the record-level **확인 필요** flag — the match limit is
`max_unknown_dimensions_before_flag_by_entity.match = 4` — so this candidate is **not** counted in
`summary.unknown_flagged_count`. A fourth unknown would flip both.

---

## 5. Stage 3 — semantic rerank

*Korean gloss: 의미 기반 재정렬 — 근거 인용 필수, ±5점 한계.*

The rerank is the **only** place a model may move a number, and it is bounded on every side.

| Constraint | Value / rule |
|---|---|
| `max_delta` | **5** — `abs(delta) <= 5` |
| `delta` type | **integer**. A fractional delta is invalid, because `match_score` is an integer |
| `require_rationale` | **true** — non-empty `rationale` whenever `applied` is true |
| `require_evidence_ids` | **true** — at least one `evidence_id` whenever `applied` is true |
| `applies_to_top_n` | **20** — eligible candidates are the first 20 in `(base_score desc, canonical_domain asc)` order. Every other candidate gets `applied: false, delta: 0` |
| `allowed_inputs` | `product_description`, `formulation_and_texture_language`, `brand_positioning`, `distribution_channel_language`, `case_studies_and_references` |
| `forbidden_inputs` | `moq`, `certifications`, `export_markets`, `lead_time_days`, `monthly_capacity_units` |
| `rounding_after_rerank` | `match_score = clamp(base_score + delta, 0, 100)` |

### 5.1 What the rerank may adjust

Only what the deterministic stage genuinely cannot read: the **language** of the offer.

- Formulation and texture wording that matches the RFQ's product description ("SPF50+ PA++++, no white cast, stable in hot and humid climates").
- Brand positioning and target-segment language.
- Distribution-channel language (the seller describes the exact channel model the buyer runs).
- Named case studies and references that show comparable work.

### 5.2 What the rerank may NOT adjust

Anything with its own deterministic criterion. Reading a `forbidden_inputs` field into a rerank
rationale is invalid, because that fact is already priced:

- MOQ, lead time, monthly capacity — S-OP1 / S-OP2 / S-OP3 own them.
- Certifications — S-CP1…S-CP4 and HF-04 own them.
- Export markets — S-EX1 owns them.

A rerank note that **asserts** a certification, an MOQ or an export market is a **blocker**, not a
style issue.

### 5.3 The five hard rules

1. **Never crosses a hard filter.** The rerank runs **only** over candidates with
   `hard_filter.passed == true`. Candidates in `excluded[]` are not visible to it. A delta can
   neither resurrect an excluded candidate nor push a passing one out. **A score change is never a
   filter decision.**
2. **Never manufactures or contradicts a hard fact.** See 5.2.
3. **Mandatory record.** `applied: true` requires a concrete `rationale` tied to the seller's own
   wording **and** at least one `evidence_id`. `applied: false` forces `delta: 0`. Both are enforced
   by the output schema and re-checked by `validate_output.py`.
4. **Ordering guard.** A rerank may never move a candidate **above** one it does not beat on
   components by more than `max_delta`:

   ```
   order = stable_sort(candidates, [("base_score", "desc"), ("canonical_domain", "asc")])
   for k in order:                                  # best base first; predecessors already final
       proposed = clamp(base_score[k] + delta_proposed[k], 0, 100)
       blockers = [p for p in finalised if base_score[p] - base_score[k] > max_delta]
       ceiling  = min(match_score[p] for p in blockers) if blockers else 100
       match_score[k]  = min(proposed, ceiling)
       rerank.delta[k] = match_score[k] - base_score[k]     # the APPLIED delta, possibly clipped
       if clipped: rerank.model_note += " (proposed {d}, clipped by the ordering guard)"
   ```

   Equal final scores are permitted — the rule forbids moving *above*, not *level with*. Because the
   pass walks candidates in descending base order, every blocker is already final, so the result
   does not depend on iteration accidents.
5. **Determinism of the record, not of the judgement.** The rerank is the one non-deterministic step
   in the pipeline, so `base_score` is stored **separately** from `match_score` on every candidate.
   Determinism is testable on `base_score` alone, and the AI narrative stays separable from the
   deterministic numbers. `summary.reranked_count` counts candidates with `rerank.applied == true`.

### 5.4 A valid rerank record

```json
"rerank": {
  "applied": true,
  "delta": 2,
  "rationale": "The seller's own product page describes an SPF50+ PA++++ hybrid filter formulated for hot, humid climates with no white cast, which is the exact formulation language of RFQ #134's product_description.",
  "evidence_ids": ["EV-102"],
  "model_note": "allowed_inputs: product_description, formulation_and_texture_language"
}
```

The candidate held the highest `base_score`, so `blockers = []`, `ceiling = 100`, and
`match_score = clamp(97 + 2, 0, 100) = 99`.

When nothing qualitative survives the protocol, the correct record is the empty one:

```json
"rerank": {"applied": false, "delta": 0, "rationale": "", "evidence_ids": []}
```

### 5.5 Anti-patterns — each of these is a blocker

| Anti-pattern | Why it is forbidden |
|---|---|
| A delta that lifts a candidate **above** an excluded one, or that "gives them a chance despite the MOQ" | Rule 1. The rerank cannot resurrect a hard-filter failure |
| `"Likely holds ISO 22716 given their factory photos"` | Rule 2 — a `forbidden_inputs` fact, and an invented certification |
| `"Their MOQ is probably negotiable"` | Rule 2. MOQ negotiability is an evidenced adjustment (+5) on S-OP1, not a rerank input |
| `applied: true` with `"rationale": "Good overall fit"` | Rule 3 — not tied to the seller's own wording, and no specific fit named |
| `applied: true` with `"evidence_ids": []` | Rule 3 — every rerank assertion must be chained to a source |
| `"delta": 7` or `"delta": 2.5` | `max_delta = 5`, and the delta is an integer |
| A delta applied to candidate #35 of 40 | `applies_to_top_n = 20` |
| Re-scoring a component instead of adjusting the total | The rerank moves `match_score`, never a component. Components belong to stage 2 |
| Rewriting `base_score` to "bake in" the delta | Rule 5. `base_score` must stay the pre-rerank number |
| `"A buyer is actively looking for this product"` with no cited RFQ | A fabricated demand signal. Nothing in the pipeline can produce one |

---

## 6. Stage 4 — unknown handling

*Korean gloss: 정보 없음 처리 — 0점 금지, 탈락 금지.*

When a criterion's deciding inputs are **all** unknown, it earns
`round_half_up(max_points × neutral_base/100 × penalty_factor)` = `round_half_up(max_points × 0.30)`
with `neutral_base = 50` and `penalty_factor = 0.6`, and **one** entry is appended to
`unknown_penalty_applied[]`:

| Field | Value |
|---|---|
| `dimension` | the **component** key on a match result (`operation_fit`), the **dimension** key on a buyer/seller record (`operational_fit`) |
| `criterion_id` | e.g. `"S-OP1"` |
| `label` | the criterion label, reused verbatim on the `Missing:` line |
| `unknown_inputs` | dotted paths, e.g. `["seller.moq"]` |
| `criterion_max_points` | e.g. `55` |
| `neutral_base` | `50` |
| `penalty_factor` | `0.6` |
| `applied_points` | e.g. `17` |
| `note` | e.g. `"MOQ not published; hard filter HF-03 skipped."` |

**Rendering.** Every entry produces one line in `missing[]` — de-duplicated labels in
**first-appearance order**, never sorted — and surfaces as **확인 필요 / needs verification**. When
`len(unknown_penalty_applied) > 2`, the candidate also carries an `evidence_gap` risk and is counted
in `summary.unknown_flagged_count`. It is **never** auto-rejected.

**Partial unknowns do not appear here.** When only *some* inputs of a criterion are unknown, the
criterion stays `scored`, the dependent signals do not fire, the field is appended to `notes[]`, and
**no** penalty entry is emitted — so no `missing[]` line is created either.

> ❌ **Scoring an unknown as `0` is a contract violation.** `0` means "we checked and the answer is
> no". Unknown means "we do not know". A `0` here fabricates a negative fact and will sink a good
> candidate.
>
> ❌ **Hard-rejecting on an unknown is a contract violation.**
> `unknown.never_hard_reject_on_unknown = true`. Every hard filter whose deciding input is unknown
> is skipped, recorded and penalised — never failed. This is the difference between a usable
> pipeline and one that only ever surfaces companies that over-publish.

---

## 7. Threshold, ordering, and no-match

### 7.1 Threshold

Default `thresholds`: `mode = "fixed"`, `value = 70`. In `percentile` mode the cut-off is the
**nearest-rank** 75th-percentile value over the `base_score` values of candidates that passed the
hard filter, raised to at least `percentile_floor = 50`; the run falls back to `fixed` when that
population is smaller than `percentile_min_population = 8`, writing a `fallback_reason`. Nearest
rank: `idx = ceil(percentile/100 × N)` over the ascending score list, 1-based.

**Hard-filter-excluded candidates carry no score and are never counted**, so
`threshold.population_size` must equal `summary.passed_hard_filter`. The resolved value and mode are
always stored and rendered, so a reader never has to guess which cut-off was applied.

### 7.2 Ordering

```
stable_sort(candidates, [("match_score", "desc"), ("canonical_domain", "asc")])
```

1. `match_score` descending (`qualification_score` descending for discovery output).
2. `canonical_domain` ascending as a plain ASCII string; the literal `"unknown"` sorts **last**.
3. **Input order**, supplied for free by the stable sort. No third key exists and none may be invented.

`excluded[]` is ordered by `(first failed rule_id asc, canonical_domain asc)`, again stably.
`results` is truncated to `top_n_requested` — default `output.match_default_top_n = 10`, hard cap
`output.max_top_n = 50`.

### 7.3 Summary counters

```
candidates_considered >= passed_hard_filter >= returned
excluded_count == candidates_considered - passed_hard_filter
```

### 7.4 No-match — always explicit, never a silent empty list

`no_match` is **always present** on the result document. `is_no_match = true` when no seller both
passed the hard filter and reached the threshold; then `results` MUST be empty and `reason` MUST be
non-empty and name the binding constraint. An empty `results` array with `is_no_match = false` is a
contract violation.

- `binding_rule_ids[]` lists the hard-filter rules that rejected the most sellers, most frequent first.
- Up to `max_relaxation_suggestions = 5` `relaxation_suggestions[]`, ordered by `expected_additional_candidates` descending. That count is **counted over sellers already seen in this run, never estimated**.

**Two reachable cases carry no binding hard-filter rule and MUST still be explicit.** Fabricating a
`rule_id` or emitting an empty `reason` is a contract violation:

1. **No candidates supplied** (`candidates_considered == 0`):
   `reason = "No seller candidates were supplied to this run."`, `binding_rule_ids = []`,
   `relaxation_suggestions = []`.
2. **Threshold-bound** (`passed_hard_filter > 0`, nobody reached the threshold):
   `reason = "{passed} of {considered} sellers passed every hard filter; the highest match score was {max_score} against a threshold of {threshold}."`,
   `binding_rule_ids = []`, and the **first** relaxation suggestion MUST be a threshold relaxation
   (`constraint: "threshold.value"`) whose `expected_additional_candidates` is the counted number of
   passing sellers at or above the suggested lower value.

### 7.5 Partial runs degrade, they never abort

A run that could not evaluate every candidate sets `partial: true` on the run envelope and explains
why in that envelope's `notes[]`, instead of exiting non-zero. The candidate is still emitted with
whatever was scored.

---

## 8. Behaviour table — T01…T10

The ten acceptance cases, and the exact mechanism in this pipeline that satisfies each.

| Case | Input | Expected behaviour | Mechanism |
|---|---|---|---|
| **T01** Buyer, broad query | UAE, K-Beauty | Distributor/importer-led; **retail-only companies score low** | `b2b_commercial_role`: `company_type_retailer` = 24 (vs distributor 60) + `wholesale_signal_false` = 0 + B-CR3 unknown 5 = 29, then the **stacked** adjustments `retail_only_consumer_store` (−25) and `wholesale_signal_false` (−15) → `29 − 40 = −11` → clamp **0**. The 20-point dimension is forfeited |
| **T02** Buyer, product query | UK, sunscreen | Sun/skin-care carriers rank first | `market_relevance` B-MR2 `category_exact_match` (35) vs `category_adjacent_match` (15) vs the `category_no_overlap` adjustment (−20); plus B-KF3 `beauty_categories_present` (15) vs `beauty_adjacent_categories_present` (8). B-MR1 `country_exact_match` (55) pins the UK. A candidate found only after query widening additionally takes `category_drift_flagged` (−10) |
| **T03** Seller, strict MOQ | sunscreen, OEM, MOQ ≤ 3000 | **Confirmed MOQ 5,000 → hard reject** | HF-03 with a **known** value: `5000 > 3000 × (1 + 0.0)` → `failed_rules += HF-03`, the candidate moves to `excluded[]` with `reason_summary` "MOQ minimum 5,000 exceeds RFQ max 3,000" and **no** `match_score`. `moq_overshoot_ratio = 0.0` makes the ceiling strict |
| **T04** Unknown MOQ | OEM seller, no MOQ published | **Do not reject; apply the unknown penalty** | The MOQ resolves to UNKNOWN → HF-03 **skipped** (`rules_skipped_unknown += HF-03`), S-OP1 takes `round_half_up(55 × 0.30) = 17` of 55, one `unknown_penalty_applied[]` entry, one `missing[]` line rendered **확인 필요**. The seller survives (section 4.3: 79/100) |
| **T05** False-claim prevention | no RFQ, outreach requested | Must not write "a buyer is currently looking" | Nothing in the pipeline can produce a demand signal: `sourcing_intent_high` (45) requires an evidenced, **dated** public sourcing action, and every `rationale_item` requires at least one `evidence_id`, so every rendered "Why" line is chained to a source. A `risk_item` may have empty `evidence_ids` **only** when the risk *is* the absent evidence |
| **T06** Duplicate domains | `company.com` / `www.company.com` | One entity | Dedupe runs **before** scoring, on `canonical_domain`. Scoring then stays invariant under the merge because the evidence-quality `domains` set is counted on the **canonical registrable domain**, so `www.example.com` and `example.com` are one domain and can never inflate `corroboration` (+15) or `multi_source_bonus` (+15) |
| **T07** Stale site | old / unreachable (오래된/접속불가) | `stale` flag + **low confidence** | **Two distinct inputs, two outcomes.** (a) *Stale but reachable* — `record.stale = true`, status `active` or `"unknown"`: kept, `stale_penalty` (−25) once on evidence quality, `confidence × 0.6` (e.g. EQ 80 → `0.80 × 0.6 = 0.48` → **LOW**) even when the score is high. `stale` **never** rejects. (b) *Evidenced unreachable* — `operational_status = "unreachable"`: HF-06 **fails** it into `excluded[]` in a match run, while discovery keeps it flagged and LOW because HF-06 is not among the discovery rules. Silence about reachability is `"unknown"` and passes |
| **T08** Evidence conflict | official site vs directory | Official / most recent wins, conflict note kept | Resolution order: official source → more recent `source_date` → higher evidence `confidence` → lower `source_tier`. Both items are kept and cross-linked by `conflicts_with`; the losing value leaves the scored field but survives in `conflicts[].losing_value`. A conflict recorded in `conflicts[]` is **resolved** and costs nothing; a dangling `conflicts_with` with no entry is **unresolved** and costs −10 each (floored at −20) on evidence quality plus a ×0.85 multiplier on `confidence` |
| **T09** Non-K-Beauty drift | beauty-unrelated wholesaler | Low K-Beauty fit → excluded | `kbeauty_korea_fit` adjustment `non_beauty_business` (−60), stacking with `korean_products_signal_false` (−25): `0 + 0 + 0 = 0` → `0 − 85` → clamp **0**. Note B-KF2 is **scored at 0**, not `unknown`, because `product_categories` was read |
| **T10** No match | no seller holds the required certification | Return an explicit "no qualified match", never a silent empty list | `no_match` is always present; `is_no_match = true` forces `results == []` and a non-empty `reason`, enforced by the output schema and re-checked by `validate_output.py`. Plus `binding_rule_ids` (most-rejecting rules first) and up to 5 `relaxation_suggestions` whose `expected_additional_candidates` are **counted**, never estimated. The two zero-rejection cases have their own mandatory wording (7.4) |

---

## 9. Operator checklist

Before you hand a match run to a human reviewer:

- [ ] Every applicable rule appears in `rules_evaluated`, so "passed" is distinguishable from "not applicable".
- [ ] Every rule in `rules_skipped_unknown` is paired with an `unknown_penalty_applied[]` entry (existing criterion entry, or a zero-weight record) — and no rule failed because an input was unknown.
- [ ] Every excluded candidate carries at least one `failed_rules` entry and **no** `match_score`.
- [ ] `results` and `excluded` share no `seller_id`.
- [ ] Every candidate with `hard_filter.passed == true` has ≥ 2 rationale items, each with ≥ 1 resolvable `evidence_id`; anything short of that went to `excluded[]` under HF-00.
- [ ] `abs(rerank.delta) <= 5`; `applied: true` has both a rationale and evidence ids; `applied: false` has `delta == 0`; no rerank rationale mentions MOQ, certifications, export markets, lead time or capacity.
- [ ] `match_score == clamp(base_score + rerank.delta, 0, 100)`, and `base_score` was not overwritten.
- [ ] `sum(weighted_contributions)` reproduces `base_score` within ±0.5.
- [ ] `no_match` is present, and if `results` is empty then `is_no_match` is true with a non-empty `reason`.
- [ ] `candidates_considered >= passed_hard_filter >= returned`, and `excluded_count == candidates_considered − passed_hard_filter`.
- [ ] `weights_used`, `threshold`, `as_of` and `score_version` are stamped on the document, so the run can be re-derived after a rubric change.
- [ ] Every `evidence_id` cited anywhere resolves inside the document's `evidence_index`.
