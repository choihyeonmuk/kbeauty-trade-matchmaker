# Data Contract — entities, fields, states, versions

The shipped summary of `schemas/*.json`: what each document holds, which fields may be unknown, how
records move through the state machine, and how a stored score is re-computed when the rubric
changes.
*Korean gloss: 데이터 계약 — 엔티티/필드/상태/버전 규약.*

| | |
|---|---|
| Audience | Anyone writing against these documents: the runtime agent, a TradeWith backend engineer, a reviewer |
| `schema_version` | `0.1.0` |
| `score_version` | `kbtm-score-0.1.0` |
| Canonical `as_of` in every example | `2026-09-12` |
| **Binding source of document shape** | `schemas/buyer.schema.json`, `seller.schema.json`, `rfq.schema.json`, `evidence.schema.json`, `match-result.schema.json`, `discovery-result.schema.json` |
| **Binding source of every number** | `schemas/scoring.config.json` |
| Companion pages | `references/qualification-rubric.md`, `references/matching-rules.md`, `references/evidence-policy.md` |

> **Precedence.** When two sources disagree, resolve in this order: (1) the **JSON schemas** for
> document *shape* — field names, enums, types, required lists, `additionalProperties`; (2)
> **`scoring.config.json`** for every *number* and scoring rule; (3) this page and the other
> reference pages for narrative. If prose here contradicts a schema, **the schema wins and this page
> is a bug**.

---

## Table of contents

- [1. The document set](#1-the-document-set)
- [2. Unknown semantics — the rule that decides everything else](#2-unknown-semantics--the-rule-that-decides-everything-else)
- [3. Shared definitions](#3-shared-definitions)
- [4. Buyer](#4-buyer)
- [5. Seller](#5-seller)
- [6. RFQ](#6-rfq)
- [7. Evidence](#7-evidence)
- [8. The query surface](#8-the-query-surface)
- [9. Run envelopes](#9-run-envelopes)
- [10. State machine](#10-state-machine)
- [11. Versioning and the re-computation path](#11-versioning-and-the-re-computation-path)
- [12. Storage principles](#12-storage-principles)
- [13. JSON and determinism conventions](#13-json-and-determinism-conventions)
- [14. Invariants a backend must not break](#14-invariants-a-backend-must-not-break)

---

## 1. The document set

Five document kinds plus two run envelopes:

```
                    +---------------------+
                    |  evidence (embedded |
                    |  by value, never a  |
                    |  standalone row)    |
                    +----------+----------+
                               ^
          embedded in every    |
    +--------------------------+--------------------------+
    |                    |                    |           |
+---+-----+        +-----+----+        +------+---+       |
|  buyer  |        |  seller  |        |   rfq    |       |
| BUY-... |        | SEL-...  |        |  "134"   |       |
+---+-----+        +-----+----+        +-----+----+       |
    |                    ^                   |            |
    | buyer_id           |                   | buyer_id   |
    |                    |                   | (nullable) |
    |                    |                   v            |
    |              +-----+-------------------+------+     |
    |              |        match-result             |    |
    |              |  MR-<rfq_id>-<as_of>-<NN>       +----+
    |              |  results[] / excluded[] /       |  evidence_index[]
    |              |  no_match / evidence_index[]    |
    |              +---------------------------------+
    |
    |   score_buyer.py / score_seller.py wrap their output in
    v
+---------------------------------------------------------+
|  discovery-result  (run envelope: summary, records[],    |
|  excluded[], partial, notes[])                           |
+---------------------------------------------------------+
```

| Document | Produced by | Stands alone? | Notes |
|---|---|---|---|
| `buyer` | discovery → `normalize_company.py` → `dedupe_companies.py` → `score_buyer.py` | Yes | Two profiles: `raw`, `scored` |
| `seller` | same chain with `score_seller.py` | Yes | Two profiles: `raw`, `scored` |
| `rfq` | the TradeWith adapter or an operator | Yes | Carries a readiness score, never a quality score |
| `evidence` | every step | **No** — embedded by value inside the other documents | One claim, one source, one observation time |
| `match-result` | `score_match.py` | Yes | One full RFQ → seller run; scored profile only |
| `discovery-result` | `score_buyer.py` / `score_seller.py` | Yes | The run envelope around scored buyer/seller records |

### 1.1 Profiles

Buyer and seller documents validate in **two profiles**. The profile is selected by
`validate_output.py --profile {raw,scored,auto}`; the schema itself does not branch on it.

| Profile | Meaning | `score_version` | `qualification_score` | `dimension_scores` |
|---|---|---|---|---|
| `raw` | Discovery / normalize / dedupe output, not yet scored | `"unscored"` | MUST be `0` (carries no meaning) | absent |
| `scored` | `score_buyer.py` / `score_seller.py` output | `"kbtm-score-X.Y.Z"` | integer 0..100 | REQUIRED, all six keys |

A `match-result` exists only in the scored profile; `"unscored"` is invalid there.

---

## 2. Unknown semantics — the rule that decides everything else

Three states must survive into the output, and they are **not** interchangeable.
*Korean gloss: Unknown은 Unknown으로 유지한다.*

| State | How it is encoded | Meaning | Scoring consequence |
|---|---|---|---|
| **unknown** | tri-state field `= "unknown"`, **or** the field/array is **absent** | Not yet determined | Neutral-base penalty; recorded in `unknown_penalty_applied[]` and surfaced in `missing[]` |
| **verified negative** | tri-state field `= false`, **or** the array is **present and empty** | Checked; demonstrably not the case | Scores as a real negative; may trigger a hard filter |
| **verified positive** | `true`, or a non-empty array/number | Evidenced | Scores normally |

Rules:

- **Absence MUST NOT be written as** `false`, `0`, `""`, `"N/A"`, `null`, or an omitted-and-forgotten field.
- **`"unknown"` MUST NOT be rendered** as anything other than the literal lowercase word `unknown`.
- **A field MUST NOT be filled by inference to dodge the unknown penalty.** A penalised unknown is the correct outcome.
- **Writing `false` or `[]` REQUIRES evidence of the check itself** — an evidence item whose `claim` is the field's claim key and whose `quote_or_summary` records what was checked.
- **`inapplicable` ≠ `unknown`.** "Inapplicable" means the *query/RFQ* imposed no such constraint; the criterion leaves the denominator entirely. Only the `condition_keys` listed in `scoring.config.json` may produce it.

Every table below carries an **Unknown** column reading exactly one of:

| Marker | Meaning |
|---|---|
| `"unknown"` | the literal string is a legal value of this field |
| absent | omitting the field means unknown |
| absent / `[]` | absent = unknown, present-and-empty = verified negative |
| — | unknown is not representable; the field is structural |

---

## 3. Shared definitions

Every schema inlines these by value, and the copies must agree structurally. `match-result.schema.json`
is the reference copy for `unknown_penalty`, `dimension_detail`, `evidence_id_list` and
`canonical_domain`.

| `$defs` key | Shape |
|---|---|
| `evidence` | the whole of `evidence.schema.json` minus `$schema`/`$id`, inlined by value |
| `numeric_range` | `{min: number >= 0, max: number >= 0, unit?: string}`, both bounds required, `additionalProperties: false` |
| `quantity_value` | `oneOf`: `number >= 0` · `numeric_range` · `const "unknown"` |
| `price_range` | `{min?: number, max?: number, currency: string}` — `currency` (ISO-4217) is **always** required so two prices are never compared across currencies |
| `timeline_value` | `oneOf`: `date` · `{start: date, end: date}` · `const "unknown"` |
| `tri_state` | `oneOf`: `boolean` · `const "unknown"` |
| `country_code` | `oneOf`: `^[A-Z]{2}$` (ISO-3166-1 alpha-2, uppercase) · `const "unknown"` |
| `category_list` | array of `^[a-z][a-z0-9_]{1,39}$`, unique, max 30 |
| `score_0_100` | integer 0..100 |
| `points_0_100` | number 0..100, 2 decimal places |
| `confidence_value` | number 0..1, 2 decimal places |
| `score_version` | `^kbtm-score-[0-9]+\.[0-9]+\.[0-9]+$` or `const "unscored"` |
| `entity_status` | the 12 UPPERCASE states of section 10 |
| `contact_channel` | `{type: enum, value: string, label?: string, evidence_ids?: evidence_id_list}` |
| `unknown_penalty` | `{dimension, criterion_id, label, unknown_inputs[], criterion_max_points, neutral_base, penalty_factor, applied_points, note?}` |
| `dimension_detail` | `{dimension, criterion_id, label, max_points, earned_points, signals_fired?, state?, evidence_ids?, note?}` |
| `conflict` | `{field, winning_value, losing_value, winning_evidence_ids, losing_evidence_ids, resolution, note?}` with `resolution ∈ {official_source, more_recent, higher_confidence, manual}` |
| `evidence_id_list` | array of evidence-id strings, unique, max 50 |
| `canonical_domain` | a registrable-domain string, or `const "unknown"` |

### 3.1 Ranges, quantities and units

Three accepted input spellings, one normal form:

| Input | Normal form |
|---|---|
| `3000` (scalar) | `3000` — left as a number; comparison code sees `{"min": 3000, "max": 3000}` |
| `{"min": 1000, "max": 5000}` | unchanged; `min <= max` is enforced |
| `{"max": 5000}` ("up to N") | `{"min": 0, "max": 5000}` — the absent lower bound is `0` |
| `{"min": 1000}` ("from N", open upper bound) | **unknown** — the field is recorded in `notes[]` and the criterion takes its unknown path |
| `"unknown"` / absent | `"unknown"` |

**Open bounds are forbidden in stored documents.** "MOQ from 1,000" does not establish a comparable
value. It MUST NOT become `{"min":1000,"max":1000}`, `+inf`, or the ceiling.

Comparison sides, fixed so every consumer agrees:

| Field | Compared on | Used by |
|---|---|---|
| `seller.moq` | **`min`** (best case for the seller) | S-OP1, HF-03 |
| `seller.lead_time_days` | **`max`** (worst case) | S-OP2, HF-07 |
| `seller.monthly_capacity_units` | **`max`** | S-OP3 |
| `rfq.quantity` | **`max`** | capacity checks |

**Units.** A comparison happens only when the two unit values are equal, or both absent (default
`"units"`). Otherwise the criterion is **unknown**, a note is recorded, and the hard filter is
skipped. A unit can legally appear in two places — inside the range (`{"min":1000,"max":5000,"unit":"kg"}`)
and in the sibling scalar field (`moq_unit`) — and **`moq_unit` always wins**, with a note naming
both. When only the range's `unit` is present it is copied into `moq_unit` during normalization, so
comparison code reads exactly one field. The same rule applies to `lead_time_days` and
`monthly_capacity_units` against their own unit fields.

### 3.2 Identifiers

| Id | Form | Rule |
|---|---|---|
| `buyer_id` | `BUY-<slug>` | `<slug>` = `canonical_domain` with `.` and `-` collapsed to `-`; when `canonical_domain == "unknown"`, `BUY-<country lowercased>-<normalized_name slug>` |
| `seller_id` | `SEL-<slug>` | same construction |
| `rfq_id` | the TradeWith id | store the **bare** id (`"134"`) consistently; the `#` is a rendering affordance only |
| `evidence_id` | `EV-NNN` | zero-padded counter, **unique within the enclosing document**, assigned in first-seen order, never renumbered after a merge |
| `match_run_id` | `MR-<rfq_id>-<as_of>-<NN>` | e.g. `MR-134-2026-09-12-01` |

Ids are **stable across re-runs of the same inputs**. A merge keeps the surviving record's id and
lists the absorbed ids in `merged_from[]`.

### 3.3 Contact channels — company level only

`contact_channels[]` items are `{type, value, label?, evidence_ids?}`.

| `type` | `value` holds | Notes |
|---|---|---|
| `partnership_form` | absolute URL | "become a distributor" / "brand submission" form |
| `wholesale_form` | absolute URL | trade-account / wholesale enquiry form |
| `form` | absolute URL | generic web form |
| `contact_page` | absolute URL | page with company-level contact details |
| `corporate_email` | a role address on the company domain (`info@`, `sales@`, `partnership@`) | MUST be published by the company itself |
| `phone` | the company switchboard number as published | never a personal mobile |
| `linkedin` | company page URL | the company profile, not a person |
| `messenger` | official WhatsApp-Business / KakaoTalk channel URL | company channel only |
| `other` | URL or company-published address | anything evidenced and company-level |

**Hard prohibition.** Named individuals' email addresses, direct-dial numbers, personal social
handles, and any address produced by **pattern guessing** (`first.last@domain`) MUST NOT be stored,
even when guessable.

**Personal data is out of scope everywhere, not only in `contact_channels[]`.** A named
individual's name, personal email, direct line, personal social handle or photo MUST NOT appear in
`notes[]`, `quote_or_summary`, a rationale or risk statement, an outreach draft, or a fixture.
`notes[]` and `quote_or_summary` are free-text surfaces policed by the same rule as the structured
fields. When a role genuinely matters, record the **role only** (`"export manager"`,
`"brand partnerships team"`) with its evidence id — never the person.

---

## 4. Buyer

`schemas/buyer.schema.json` · `additionalProperties: false` · **required**: `schema_version`,
`score_version`, `buyer_id`, `company_name`, `canonical_domain`, `country`, `company_type`,
`status`, `qualification_score`, `confidence`, `evidence`, `notes`.

| Field | Type | Req | Unknown | Notes |
|---|---|---|---|---|
| `schema_version` | string | ✔ | — | e.g. `"0.1.0"` |
| `score_version` | `score_version` | ✔ | — | `"unscored"` in the raw profile |
| `buyer_id` | string | ✔ | — | `BUY-<slug>` (3.2) |
| `company_name` | string | ✔ | — | exactly as printed on the most authoritative source |
| `normalized_name` | string | | — | NFKC + casefold + legal suffix stripped; the **fallback** dedupe key |
| `website` | string (URL) | | — | the primary official site, canonical absolute form |
| `canonical_domain` | `canonical_domain` | ✔ | `"unknown"` | the **primary merge key**. `"unknown"` blocks domain-based merging |
| `country` | `country_code` | ✔ | `"unknown"` | alpha-2 uppercase; the only value ever compared |
| `country_name` | string | | — | display only, never compared |
| `company_type` | enum `distributor \| importer \| wholesaler \| retailer \| brand \| marketplace \| other \| unknown` | ✔ | `"unknown"` | `"other"` = classified, none of the above. Never conflate the two |
| `product_categories` | `category_list` | | absent / `[]` | canonical slugs; unmapped well-formed slugs are preserved with a note |
| `korean_products_signal` | `tri_state` | | `"unknown"` / absent | never infer `false` from silence |
| `korean_brands_carried` | array of string | | absent / `[]` | brands must be **named and evidenced** |
| `wholesale_signal` | `tri_state` | | `"unknown"` / absent | |
| `partnership_signal` | `tri_state` | | `"unknown"` / absent | |
| `sourcing_intent` | enum `low \| medium \| high \| unknown` | | `"unknown"` | `high` = a dated public sourcing action; `medium` = a standing page; `low` = B2B capable, no invitation |
| `sourcing_signals` | array of enum (9 values) | | absent / `[]` | `new_brand_inquiry`, `partnership_page`, `wholesale_inquiry_form`, `become_a_distributor_page`, `brand_submission_form`, `open_call_for_suppliers`, `active_sourcing_post`, `trade_show_attendance`, `rfq_posted`. Each entry needs ≥ 1 evidence item |
| `channels` | array of enum (10 values) | | absent / `[]` | `retail_store`, `ecommerce`, `marketplace`, `wholesale`, `salon_spa`, `pharmacy`, `department_store`, `distributor_network`, `duty_free`, `other` |
| `contact_channels` | array of `contact_channel` | | absent / `[]` | company-level only (3.3). Absent = unknown (neutral penalty); `[]` = verified none (−15 adjustment) |
| `stale` | boolean | | — | default `false`. Site unreachable / archived / clearly outdated |
| `operational_status` | enum `active \| closed \| unreachable \| unknown` | | `"unknown"` | `closed` / `unreachable` remove a record from outreach but MUST NOT rewrite any other field |
| `status` | `entity_status` | ✔ | — | section 10 |
| `qualification_score` | `score_0_100` | ✔ | — | `0` and meaningless when `score_version == "unscored"` |
| `dimension_scores` | object | scored | — | `kbeauty_fit`, `b2b_role`, `sourcing_intent`, `reachability`, `evidence_quality` are always present. `market_relevance` is present **unless** the query named no country, no product category and no channel: all three of its criteria are then inapplicable, the dimension is dropped from **both** the numerator and the denominator, and the remaining weights are renormalised. Counting a dropped dimension as `0` against a fixed `/100` denominator would dock every candidate its whole 15-point weight |
| `dimension_details` | array of `dimension_detail` | | — | per-criterion audit trail, in config criterion order |
| `unknown_penalty_applied` | array of `unknown_penalty` | | — | one entry per criterion that took the unknown path |
| `confidence` | `confidence_value` | ✔ | — | 0..1, 2 dp. Trust in the **record**, never quality of the company |
| `qualified` | boolean | | — | `qualification_score >= the resolved threshold` |
| `evidence` | array of `evidence` | ✔ | — | embedded by value. **Two distinct states:** evidence present but **no** item with `is_official: true` on a material claim → scored, ranked, rendered with the ` — unverified` marker (PRD 15.1 criterion 3). **No** evidence item on any material claim → `evidence_quality = 0` and the record is routed to `excluded[]` by the DISC-06 bar before scoring |
| `conflicts` | array of `conflict` | | — | a recorded conflict is a **resolved** one |
| `missing` | array of string | | — | derived: de-duplicated `label` values of `unknown_penalty_applied[]` in **first-appearance order** |
| `merged_from` | array of string | | — | absorbed `buyer_id` values |
| `alias_domains` | array of string | | — | other host forms for the same entity (`www.`, IDN/punycode pairs) |
| `source_query` | object `{country?, product_categories?, keywords?, category_drift?}` | | — | echo of the discovery query. `category_drift` is **per candidate**, set `true` only on candidates first seen after a widening pass, and is treated as `false` when absent — never as unknown |
| `as_of` | date `YYYY-MM-DD` | scored | — | the only temporal input to scoring |
| `notes` | array of string | ✔ | — | inference, caveats and 확인 필요 flags live here, never in an evidence quote |
| `extensions` | object | | — | runtime/tenant escape hatch. Never scored, never rendered |

---

## 5. Seller

`schemas/seller.schema.json` · `additionalProperties: false` · **required**: `schema_version`,
`score_version`, `seller_id`, `company_name`, `canonical_domain`, `country`, `company_type`,
`status`, `qualification_score`, `confidence`, `evidence`, `notes`.

Fields shared with the buyer (`schema_version`, `score_version`, `company_name`, `normalized_name`,
`website`, `canonical_domain`, `country`, `stale`, `operational_status`, `status`,
`qualification_score`, `dimension_details`, `unknown_penalty_applied`, `confidence`, `qualified`,
`evidence`, `conflicts`, `missing`, `merged_from`, `alias_domains`, `source_query`, `as_of`,
`notes`, `extensions`) behave identically. The seller-specific fields:

| Field | Type | Req | Unknown | Notes |
|---|---|---|---|---|
| `seller_id` | string | ✔ | — | `SEL-<slug>` |
| `company_name_ko` | string | | — | display only; dedupe uses `normalized_name` |
| `company_type` | enum `manufacturer \| brand \| oem_odm \| distributor \| other \| unknown` | ✔ | `"unknown"` | |
| `product_categories` | `category_list` | | absent / `[]` | what the seller can **produce or supply** |
| `product_forms` | array of slug | | absent | `cream`, `lotion`, `serum`, `essence`, `toner`, `ampoule`, `gel`, `stick`, `cushion`, `spray`, `balm`, `mask_sheet`, `powder`, `oil`, `mist`, `patch` |
| `oem_odm` | `tri_state` | | `"unknown"` / absent | never infer `false` from silence |
| `private_label` | `tri_state` | | `"unknown"` / absent | production under the customer's brand — not "we can print your logo on the box" |
| `brand_export` | `tri_state` | | `"unknown"` / absent | "exports its own brand", **not** "is a brand". Exists so `commercial_model: branded` is scorable |
| `moq` | `quantity_value` | | `"unknown"` / absent | compared on **`min`**. Absent or `"unknown"` never causes a hard rejection |
| `moq_unit` | string | | absent ⇒ `"units"` | the authoritative unit; wins over `moq.unit` |
| `lead_time_days` | `quantity_value` | | `"unknown"` / absent | compared on **`max`** |
| `monthly_capacity_units` | `quantity_value` | | `"unknown"` / absent | compared on **`max`** |
| `certifications` | array of `^[A-Z][A-Z0-9_]{1,39}$` | | absent / `[]` | canonical tokens; an unmapped well-formed token is preserved verbatim with a note and matches **only exactly** |
| `certifications_verified` | `tri_state` | | `"unknown"` / absent | `true` **only** when the list was read from an official, **exhaustive** source. This is what makes HF-04 applicable |
| `regulatory_registrations` | array of `{market, scheme, registration_status, evidence_ids?, note?}` | | absent / `[]` | `registration_status ∈ {registered, in_progress, not_registered, unknown}`. Absent = unknown; present-and-empty = checked, none found |
| `export_markets` | array of alpha-2 | | absent / `[]` | markets the seller **demonstrably** exports to, **named as countries** |
| `export_regions` | array of `ASIA \| SEA \| EA \| SA \| MENA \| EU \| EUROPE \| NA \| LATAM \| AFRICA \| OCEANIA \| CIS` | | absent / `[]` | region tokens the seller states when it names no country (동남아 / 유럽 / "Asia, Europe, North America"). **Never** expand a token into `export_markets` — that invents countries the page did not name; region expansion is the operator's job on the query surface (§8). Scored by `S-EX1` one tier below the country-level signals |
| `excluded_markets` | array of alpha-2 | | absent / `[]` | markets the seller stated it will not supply. Triggers HF-05 |
| `english_site` | `tri_state` | | `"unknown"` / absent | a full English (or destination-language) site |
| `overseas_partner_signal` | `tri_state` | | `"unknown"` / absent | publicly seeks overseas distributors/partners, or evidences existing ones |
| `contact_channels` | array of `contact_channel` | | absent / `[]` | company-level only |
| `dimension_scores` | object, all six keys required | scored | — | `product_fit`, `commercial_model`, `operational_fit`, `compliance_readiness`, `export_readiness`, `evidence_quality`. Unlike the buyer rubric, no seller dimension can become wholly inapplicable: `S-PF1`, `S-CM1`, `S-OP1`, `S-CP4` and `S-EX1` carry no `inapplicable_when` |

---

## 6. RFQ

`schemas/rfq.schema.json` · `additionalProperties: false` · **required**: `schema_version`,
`score_version`, `rfq_id`, `buyer_id`, `destination_country`, `product_category`,
`commercial_model`, `status`, `qualification_score`, `confidence`, `evidence`, `notes`.

| Field | Type | Req | Unknown | Notes |
|---|---|---|---|---|
| `rfq_id` | string | ✔ | — | store the bare id (`"134"`) |
| `buyer_id` | string \| null | ✔ | `null` | `null` when the RFQ is operator-authored and not yet linked |
| `destination_country` | `country_code` | ✔ | `"unknown"` | drives `market_fit`, the registration lookup, HF-05 |
| `product_category` | slug | ✔ | — | the primary category; drives HF-01 |
| `product_categories_extra` | `category_list` | | absent | unioned with `product_category` to form the query category set |
| `product_description` | string | | absent | free text, used **only** by the semantic rerank. Never by the deterministic scorers |
| `product_forms` | array of slug | | absent ⇒ **inapplicable** | absent/empty makes the form criterion inapplicable, not unknown |
| `quantity` | `quantity_value` | | `"unknown"` / absent | compared on **`max`** for capacity checks |
| `max_moq` | number \| null | | `null` = no constraint | drives HF-03 and S-OP1 |
| `moq_unit` | string | | absent ⇒ `"units"` | the unit `max_moq` and `quantity` are denominated in. **Set this whenever the catalogue is in `kg`, `pcs`, `EA` or `sets`**, or every such seller is permanently unit-mismatched |
| `target_price` | `price_range` \| number+currency \| null \| `"unknown"` | | `null` / `"unknown"` | informational in v0.1.0; not scored |
| `commercial_model` | enum `branded \| private_label \| oem_odm \| either` | ✔ | — | `either` makes HF-02 inapplicable |
| `required_certifications` | array of token | | absent ⇒ **inapplicable** | drives HF-04, which fires only when `seller.certifications_verified == true` |
| `preferred_certifications` | array of token | | absent ⇒ **inapplicable** | scored proportionally, never hard-filtered |
| `timeline` | `timeline_value` | | `"unknown"` | `max_lead_time_days` is derived from it against `as_of` |
| `max_lead_time_days` | number \| null | | `null` = no constraint | when present it **overrides** the derived value |
| `required_seller_countries` | array of alpha-2 | | absent / `[]` | normally `["KR"]` for this vertical; drives HF-08 |
| `excluded_seller_countries` | array of alpha-2 | | absent / `[]` | drives HF-08 |
| `status` | enum `draft \| qualified \| matching \| proposal_open \| matched \| closed` | ✔ | — | **lowercase, and a different namespace** from the UPPERCASE entity states of section 10. Never mix them |
| `qualification_score` | `score_0_100` | ✔ | — | **readiness / completeness**, not buyer quality |
| `readiness_detail` | `{filled, total, missing_fields[]}` | | — | emitted by `score_match.py` |
| `confidence` | `confidence_value` | ✔ | — | |
| `unknown_penalty_applied` | array | | — | present for symmetry; RFQ readiness applies no unknown penalty, so normally empty |
| `evidence` | array of `evidence` | ✔ | — | typically one `internal_record` item pointing at the TradeWith RFQ. May be empty for operator-authored RFQs |
| `as_of` | date | | — | used for timeline arithmetic |
| `notes` | array of string | ✔ | — | |
| `extensions` | object | | — | never scored, never rendered |

**Readiness arithmetic.** Ten fields — `destination_country`, `product_category`,
`product_description`, `quantity`, `max_moq`, `commercial_model`, `required_certifications`,
`timeline`, `target_price`, `buyer_id`. A field counts as *filled* when it is present and not
`"unknown"` (for `buyer_id`, not `null`; for arrays, non-empty; `required_certifications` counts as
filled when present at all, including a deliberate empty list).

```
rfq.qualification_score = round_half_up(100 * filled / 10)
```

Worked: an RFQ filling `destination_country`, `product_category`, `product_description`, `quantity`,
`max_moq`, `commercial_model` and `required_certifications` = **7**; `timeline` is `"unknown"` so it
is **not** filled; `target_price` and `buyer_id` are `null` → `round_half_up(70) = 70`, with
`missing_fields = ["buyer_id", "target_price", "timeline"]`.

The RFQ is never "qualified" or "rejected" by this number, and it never enters `match_score`.

---

## 7. Evidence

`schemas/evidence.schema.json` · `additionalProperties: false` · embedded by value, never a
standalone row. **One item = one claim, one source, one observation time.** Never bundle two claims
into one item; never cite an item for a claim it does not literally support.

| Field | Type | Req | Unknown | Notes |
|---|---|---|---|---|
| `schema_version` | string | ✔ | — | |
| `evidence_id` | string | ✔ | — | `EV-NNN`, unique within the enclosing document |
| `claim` | string | ✔ | — | a canonical claim key whenever one applies (7.2) |
| `value` | string \| number \| boolean \| array \| `"unknown"` | ✔ | `"unknown"` | the observed value **exactly as normalized into the parent record** |
| `source_url` | string (absolute http/https) | ✔ | — | must be clickable and re-verifiable. No login-walled, CAPTCHA-bypassed or paywalled URL |
| `source_domain` | string | | — | the canonical registrable domain of `source_url`; re-normalised on read, so a `www.` form can never inflate the independent-source count |
| `source_type` | enum `official_site \| official_directory \| trade_show \| third_party \| social \| internal_record` | ✔ | — | |
| `source_tier` | integer 1..5 | ✔ | — | 7.1 |
| `is_official` | boolean | ✔ | — | `true` **only** when read on a channel the company itself controls. A distributor listing the company is not official *for that company* |
| `observed_at` | RFC3339 date-time | ✔ | — | when **we** read the page. Never the page's own date. MUST NOT be later than the run's `as_of` end-of-day |
| `source_date` | date \| `"unknown"` | ✔ | `"unknown"` | when the **source content** was published/updated. Never guessed. MUST NOT be later than its own `observed_at` |
| `confidence` | number 0..1 | ✔ | — | how strongly **this one source** supports **this one claim** |
| `quote_or_summary` | string ≤ 1000 | ✔ | — | a verbatim quote (preferred, ≤ 300 chars) or a faithful one-sentence summary. **MUST NOT contain our interpretation** — that goes in `notes[]` |
| `inferred` | boolean | | — | default `false`. `true` marks a value derived by reasoning. If the quote does not genuinely support the inference, the field stays `"unknown"` |
| `stale` | boolean | | — | default `false`. URL unreachable, archive copy, or demonstrably outdated |
| `conflicts_with` | array of evidence id | | — | ids in the same document asserting a contradictory value for the same claim |
| `retrieval_method` | enum `web_search \| page_fetch \| sitemap \| internal_api \| manual_entry` | | — | only non-bypassing methods are permitted |
| `notes` | array of string | | — | free-form operator notes about this single item |

### 7.1 Source tiers

| Tier | Source | Typical `source_type` |
|---|---|---|
| 1 | the company's own site, including its contact / wholesale / export / OEM pages | `official_site` |
| 2 | official trade-show, association, government or trade-agency directory | `official_directory`, `trade_show`, `internal_record` |
| 3 | official LinkedIn or company-controlled social profile | `social` |
| 4 | reputable third-party directory or press release | `third_party` |
| 5 | community, blog, forum — supporting signal only | `third_party`, `social` |

`internal_record` defaults to tier 2.

### 7.2 Canonical claim keys

Free text is allowed only for claims with no canonical key.

**Buyer** — `company_name`, `website`, `country`, `company_type`, `product_categories`,
`korean_products_signal`, `korean_brands_carried`, `wholesale_signal`, `partnership_signal`,
`sourcing_intent`, `sourcing_signals`, `channels`, `contact_channels`, `operational_status`.

**Seller** — `company_name`, `website`, `country`, `company_type`, `product_categories`,
`product_forms`, `oem_odm`, `private_label`, `brand_export`, `moq`, `lead_time_days`,
`monthly_capacity_units`, `certifications`, `certifications_verified`, `regulatory_registrations`,
`export_markets`, `export_regions`, `excluded_markets`, `english_site`, `overseas_partner_signal`,
`contact_channels`, `operational_status`.

**RFQ** — `product_category`, `commercial_model`, `destination_country`, `max_moq`,
`required_certifications`, `preferred_certifications`, `quantity`, `timeline`, `target_price`.

### 7.3 Material claims — the coverage denominator

`scoring.config.json evidence.material_claims` is authoritative. Every material claim is either
backed by **≥ 1 evidence item whose `claim` equals that key**, or carries the value `"unknown"` (or
is absent). There is no third possibility.

| Kind | Material claims |
|---|---|
| `buyer` | `company_type`, `product_categories`, `korean_products_signal`, `wholesale_signal`, `partnership_signal`, `sourcing_intent`, `country`, `contact_channels` |
| `seller` | `company_type`, `product_categories`, `oem_odm`, `private_label`, `moq`, `certifications`, `export_markets`, `overseas_partner_signal` |
| `rfq` | `product_category`, `commercial_model`, `destination_country`, `max_moq`, `required_certifications` |

This list is also the denominator of the `coverage` term in the evidence-quality function, and the
set counted by `confidence`'s `coverage_factor`. A field outside it never affects `confidence`,
however heavily the score penalises it.

### 7.4 Internal records (adapter-sourced evidence)

Facts read from TradeWith's own database are still evidence:

- `source_type = "internal_record"`, `source_tier = 2`, `is_official = false` unless the record is a company-submitted profile.
- `source_url` MUST be a resolvable internal URL (e.g. `https://app.tradewith.example/rfqs/134`) — never a bare id, never a DB query string.
- `retrieval_method = "internal_api"`.
- `quote_or_summary` names the fields read, e.g. `"RFQ #134 max_moq = 3000, required_certifications = [ISO22716]"`.
- An internal record never substitutes for public evidence when the claim is about a third-party company's public behaviour.

### 7.5 Conflicts

When two sources disagree on the same claim:

1. Keep **both** evidence items; cross-link them via `conflicts_with`.
2. Resolve in favour of (a) the official source, then (b) the more recent `source_date`, then (c) the higher evidence `confidence`, then (d) the lower `source_tier` number.
3. Record the resolution in the record's `conflicts[]` with `field`, `winning_value`, `losing_value`, the winning/losing evidence ids and a `resolution` enum value.
4. **Never silently drop the losing value.**

A conflict present in `conflicts[]` is by definition **resolved** and costs nothing. "Unresolved"
means a dangling `conflicts_with` with no matching entry, and that costs −10 each (floored at −20) on
evidence quality plus a ×0.85 multiplier on `confidence`.

---

## 8. The query surface

Discovery and matching both score a record *against a request*. That request is normalised into one
object, passed via `--query` (or the envelope's `query` key), and derived from the RFQ by
`score_match.py`.

```json
{
  "country": "AE",
  "country_name": "United Arab Emirates",
  "destination_country": "AE",
  "region_countries": ["AE", "BH", "KW", "OM", "QA", "SA"],
  "adjacent_region_countries": [],
  "product_categories": ["sunscreen"],
  "product_forms": ["cream"],
  "channels": ["wholesale"],
  "company_types": ["distributor", "importer"],
  "commercial_model": "private_label",
  "quantity": 5000,
  "max_moq": 3000,
  "moq_unit": "units",
  "max_lead_time_days": 60,
  "required_certifications": ["ISO22716"],
  "preferred_certifications": ["HALAL"],
  "required_seller_countries": ["KR"],
  "excluded_seller_countries": [],
  "keywords": ["sunscreen", "spf50"],
  "category_drift": false,
  "vertical": "K-Beauty"
}
```

**Every key is optional.** An absent or empty key sets the matching `condition_keys` flag —
`query.country_absent`, `query.product_categories_absent`, `query.product_forms_absent`,
`query.channels_absent`, `query.required_certifications_absent`,
`query.preferred_certifications_absent`, `query.destination_country_absent` — which makes the
dependent criteria **inapplicable**, never unknown. An eighth flag,
`query.company_types_absent`, is declared in the config but **reserved**: no v0.1.0 criterion lists
it in an `inapplicable_when`, so an absent `company_types` changes no score and only relaxes the
discovery filter.

The keys that are load-bearing and easy to miss:

| Key | Meaning and default |
|---|---|
| `quantity` | the order quantity, compared on its **max** by the capacity criterion. Absent/unknown means the capacity criterion can only reach `capacity_no_constraint_known` — it is **not** unknown |
| `moq_unit` | **defaults to `"units"`**. Compared against `seller.moq_unit`; a mismatch makes the MOQ criterion unknown, skips HF-03 and fires `moq_unit_mismatch` |
| `region_countries` | the alpha-2 list the operator's region word expanded to **by the agent**. Scripts never expand regions. Absent ⇒ the region signals cannot fire (not unknown, no penalty) |
| `adjacent_region_countries` | same shape, the neighbouring-market tier |
| `company_types` | the buyer types the operator asked for. A **discovery filter only** in v0.1.0: a candidate whose **known** type falls outside the list is a query-fit exclusion; an `"unknown"` type is never excluded. No criterion scores it |
| `vertical` | defaults to `"K-Beauty"`; rendering only |

**Drift is per candidate, not per run.** `query.category_drift` records only that *the run* widened.
The scorer reads `record.source_query.category_drift`, which discovery MUST set to `true` on
**exactly** the candidates first seen after a widening pass, with the widened query strings in
`record.source_query.keywords[]` and a note naming the original and the widened category. A record
without the field is treated as `false`, never as unknown.

---

## 9. Run envelopes

### 9.1 `discovery-result` — buyer and seller discovery

`schemas/discovery-result.schema.json` · **required**: `schema_version`, `score_version`, `as_of`,
`entity`, `summary`, `records`, `excluded`, `partial`, `notes`.

| Field | Type | Notes |
|---|---|---|
| `entity` | `"buyer"` \| `"seller"` | which schema `records[]` validates against |
| `query` | object | the section 8 query surface, echoed verbatim so every market/product number is auditable |
| `summary` | object | required keys `candidates_found`, `scored_count`, `returned`, `qualified_count`, `excluded_count`, `threshold_used`, `threshold_mode`; optional `top_n_requested`, `unknown_flagged_count`, `score_range` |
| `records` | array of buyer/seller documents in the **scored** profile | ordered by `(qualification_score desc, canonical_domain asc)`, stably |
| `excluded` | array of `{id, company_name, canonical_domain, website?, reason_summary, failed_rules?, qualification_score?, notes?}` | a hard-requirement violation renders `failed_rules[0].reason` prefixed with its `rule_id`; a query-fit or evidence-bar exclusion renders the fit reason alone |
| `partial` | boolean | `true` when any candidate could not be fully evaluated. The reason goes into `notes[]` as `"<id>: <reason>"`, the candidate is still emitted with whatever was scored, and **the exit code stays 0** |
| `notes` | array of string | run-level notes |
| `skill_version`, `extensions` | | optional |

Counter invariants: `candidates_found >= scored_count >= returned` and
`qualified_count == count(r : r.qualified)`.

**`--top N` and the default counts are ceilings, never targets.** A candidate with no evidence item
on any material claim MUST NOT appear in `records[]`; it belongs in `excluded[]` with a
`reason_summary` that names which sub-case it was — `"no evidenced material claim (no source could be
opened)"` when no permitted retrieval method could read the site, or `"no evidenced material claim
(evidence covers no material claim)"` when pages were read and nothing material was on them. That is
**not** the "unverified" state: a record that *has* evidence but no official item on a material claim
is scored, ranked and marked, never excluded. Returning fewer than requested is the correct
outcome, and the run must say so: when `returned < top_n_requested`, append
`"returned <n> of <N> requested: evidence quality prioritised over count (DISC-06)"` to `notes[]`.

### 9.2 `match-result` — one RFQ → seller run

`schemas/match-result.schema.json` · **required**: `schema_version`, `score_version`,
`match_run_id`, `rfq_id`, `as_of`, `weights_used`, `threshold`, `summary`, `results`, `excluded`,
`no_match`, `evidence_index`, `notes`.

| Field | Type | Notes |
|---|---|---|
| `match_run_id` | string | `MR-<rfq_id>-<as_of>-<NN>` — the key that lets an old run be diffed against a re-computed one |
| `rfq_constraints` | object | read-only echo of the RFQ constraints, so the header block renders from this document alone |
| `weights_used` | `{product_fit, model_fit, operation_fit, compliance_fit, market_fit, evidence_quality}` | pins the run so a stored score can be re-derived after a config change |
| `threshold` | `{mode, value, percentile?, population_size?, fallback_reason?, …}` | the **resolved** cut-off and its full derivation |
| `summary` | object | `candidates_considered`, `passed_hard_filter`, `returned`, `threshold_used`, `threshold_mode` required; `excluded_count`, `qualified_count`, `top_n_requested`, `reranked_count`, `unknown_flagged_count`, `score_range` optional |
| `results` | array of `match_candidate` | ordered by `(match_score desc, canonical_domain asc)`, stably |
| `excluded` | array of `excluded_candidate` | each carries `reason_summary` and ≥ 1 `failed_rules` entry, and **no** `match_score` |
| `no_match` | `{is_no_match, reason, relaxation_suggestions[], binding_rule_ids?, nearest_misses?}` | **always present**. `is_no_match: true` forces `results == []` and a non-empty `reason` — enforced by the schema's root `allOf` |
| `evidence_index` | array of `evidence_ref` | REQUIRED whenever any candidate carries a non-empty `evidence_ids`; one entry with a click-ready `source_url` per referenced id. A dangling id is a build blocker |
| `partial` | boolean | same degrade-never-abort rule as 9.1 |
| `skill_version`, `notes`, `extensions` | | |

A `match_candidate` carries `seller_id`, `seller_name`, `canonical_domain`, `match_score`,
`base_score`, `component_scores`, `weighted_contributions`, `hard_filter`,
`unknown_penalty_applied`, `rerank`, `rationale`, `risks`, `confidence` and `evidence_ids` as
required fields, plus the **display carry-over** copied by value from the seller record at match
time — `website`, `company_type`, `oem_odm`, `contact_channels[]`. The carry-over is rendering only;
the match arithmetic reads the seller document, never these copies.

`base_score` is stored **separately** from `match_score` so the deterministic half and the AI half
stay separable, and so determinism is testable on `base_score` alone.

---

## 10. State machine

UPPERCASE `entity_status` applies to buyer and seller records and to a seller inside a match run.
RFQ `status` is a separate lowercase namespace (section 6) and is never mixed with these.

### 10.1 Allowed transitions

| From | Allowed next states | Performed by | Entry condition |
|---|---|---|---|
| *(new)* | `DISCOVERED` | **skill** | A candidate exists with at least one source URL |
| `DISCOVERED` | `VERIFIED`, `CLOSED` | **skill** | `VERIFIED` requires ≥ 1 evidence item on a material claim from a tier ≤ 3 source |
| `VERIFIED` | `QUALIFIED`, `DISCOVERED`, `CLOSED` | **skill** | `QUALIFIED` requires a `scored` profile document (`score_version != "unscored"`) |
| `QUALIFIED` | `MATCH_CANDIDATE`, `READY_FOR_REVIEW`, `VERIFIED`, `CLOSED` | **skill** | `MATCH_CANDIDATE` requires passing the hard filter of a specific match run |
| `MATCH_CANDIDATE` | `READY_FOR_REVIEW`, `QUALIFIED`, `CLOSED` | **skill** | `READY_FOR_REVIEW` requires a complete outreach draft |
| `READY_FOR_REVIEW` | `APPROVED_FOR_OUTREACH`, `QUALIFIED`, `MATCH_CANDIDATE`, `CLOSED` | **application layer** (the approval) / **skill** (only the backward edges) | Approval requires a human decision, recorded outside the skill |
| `APPROVED_FOR_OUTREACH` | `CONTACTED`, `CLOSED` | application layer | An actual message was sent by a system that is **not** this skill |
| `CONTACTED` | `REPLIED`, `CLOSED` | application layer | |
| `REPLIED` | `RFQ_RECEIVED`, `PROPOSAL_RECEIVED`, `CLOSED` | application layer | |
| `RFQ_RECEIVED` | `MATCHED`, `CLOSED` | application layer | |
| `PROPOSAL_RECEIVED` | `MATCHED`, `CLOSED` | application layer | |
| `MATCHED` | `CLOSED` | application layer | |
| `CLOSED` | *(terminal for the skill)* | the application layer may reopen to `DISCOVERED` or `QUALIFIED` | |

Any transition not listed is **forbidden**. Skipping forward (`DISCOVERED → QUALIFIED`) is
forbidden. Regression along the listed backward edges is allowed and is the normal outcome of a
re-run with new evidence.

### 10.2 The skill's boundary

- **The skill may set only** `DISCOVERED`, `VERIFIED`, `QUALIFIED`, `MATCH_CANDIDATE`, `READY_FOR_REVIEW`.
- **The skill MUST NOT set** `APPROVED_FOR_OUTREACH` or anything after it — those belong to TradeWith/CRM and require a human decision.
- The TradeWith adapter may *read* later states and may `PATCH` a status only within the skill-owned range; it MUST refuse to write `APPROVED_FOR_OUTREACH` or later.
- Every outreach-producing run ends at `READY_FOR_REVIEW` with `auto_send: false` and `manual_approval_required: true`.
- **This package has no send capability at all.** The three write capabilities move data into
  TradeWith's review queue; none of them moves a message toward a recipient.

### 10.3 Entry conditions a document must satisfy

| Status | The document must also carry |
|---|---|
| `DISCOVERED` | ≥ 1 evidence item with a resolvable `source_url` |
| `VERIFIED` | ≥ 1 evidence item whose `claim` is a material claim and whose `source_tier <= 3` |
| `QUALIFIED`, `MATCH_CANDIDATE`, `READY_FOR_REVIEW` | `score_version != "unscored"` **and** `dimension_scores` present |
| `MATCH_CANDIDATE` | a `match_run_id` |
| `APPROVED_FOR_OUTREACH` and later | **nothing produced by this package may carry these** |

---

## 11. Versioning and the re-computation path

### 11.1 The three versions

| Version | Where it lives | What it describes |
|---|---|---|
| `schema_version` | every document; the `$id` path segment of each schema | the **shape** contract: field names, enums, required lists |
| `score_version` | `scoring.config.json`; copied into every scored document | the **rubric**: weights, criteria, signals, penalties, thresholds, hard-filter predicates, the evidence-quality function |
| `skill_version` | the `SKILL.md` body (the frontmatter carries only `name` + `description`), `README.md`, `match-result.skill_version` | the **package** as shipped (prompts, references, scripts, templates, tests) |

All three are semver `MAJOR.MINOR.PATCH`; `score_version` is additionally prefixed
(`kbtm-score-0.1.0`).

### 11.2 Bump rules

| Change | `schema_version` | `score_version` | `skill_version` |
|---|---|---|---|
| New **optional** field added to a schema | MINOR | — | MINOR |
| Field renamed / removed / newly required; enum value removed | MAJOR | MAJOR | MAJOR |
| Enum value **added** | MINOR | MINOR if it is scorable | MINOR |
| Weight, point value, penalty constant or default threshold changed | — | MINOR | MINOR |
| Criterion or signal added/removed; hard-filter predicate changed | — | MINOR | MINOR |
| Dimension set or weight semantics changed | possibly MAJOR | MAJOR | MAJOR |
| Rendering template, label, wording, reference prose | — | — | PATCH |
| Vocabulary addition that cannot change an existing score | — | — | PATCH |
| Vocabulary change that **can** change a score (a new synonym, a new adjacency edge) | — | MINOR | MINOR |
| Bug fix that changes a computed number | — | PATCH only if every golden fixture is byte-identical, otherwise MINOR | PATCH/MINOR |

A `score_version` PATCH is permitted **only** when every golden fixture reproduces byte-identically.

### 11.3 What to do when the rubric changes

1. **Store inputs, not just outputs.** Every stored record keeps its `evidence[]`, its
   `source_query` / query surface, and its `as_of`, so a re-score never requires re-crawling the web.
2. **Stamp every score.** `score_version` and `as_of` travel with the numbers; a match run also
   stores `weights_used` and the resolved `threshold`.
3. **When the rubric changes, every stored score is stale by definition.** Re-run
   `score_buyer.py` / `score_seller.py` / `score_match.py` over the stored **raw** records with the
   **new** config:
   - to *reproduce* a historical result, pass the **original** `--as-of`;
   - to *refresh* recency and staleness, pass a **new** `--as-of`.

   The two produce different numbers legitimately; both are reproducible.
4. **Never compare or rank across `score_version`s.** A list mixing two rubric versions is invalid
   output and is reported as a validation failure.
5. **Re-scoring MUST NOT mutate evidence.** It rewrites only `dimension_scores`,
   `dimension_details`, `unknown_penalty_applied`, `missing`, `confidence`,
   `qualification_score` / `match_score`, `qualified`, `score_version` and `as_of`. The AI-authored
   `rationale[]` and `risks[]` are left untouched.
6. **A merge invalidates scores.** A merged record drops back to `score_version = "unscored"`,
   `qualification_score = 0`, and must be re-scored before it is ranked or rendered.
7. **Deprecation window.** Keep the previous `score_version`'s stored documents readable for at
   least one MINOR cycle. `match_run_id` is the key that lets an old run be diffed against a
   re-computed one.

A minimal re-computation:

```bash
python3 scripts/score_seller.py \
  --input storage/sellers.raw.json \
  --query storage/query-seller-sunscreen-oem.json \
  --as-of 2026-09-12 \
  --output out/sellers.scored.json --pretty
```

---

## 12. Storage principles

1. **Store the claim, not the page.** Persist `claim`, `value`, `source_url`, `observed_at`,
   `source_date` and a short quote — not whole page bodies, not unnecessary profiles.
2. **`canonical_domain` is the merge key.** The same company must be mergeable on it. Name + country
   is only a fallback, and it never merges across two known, different countries. Two different
   known domains are never auto-merged, even under identical names — but when two records share a
   non-empty `normalized_name` **and** a known, equal country while differing in domain, both MUST
   carry a "possible duplicate" note and the pair MUST be listed under `merges_suggested[]`.
   Neither an automatic merge nor a silent pass is acceptable.
3. **A merge is reversible in principle.** `merged_from[]` preserves every absorbed id,
   `alias_domains[]` every absorbed domain, `conflicts[]` every losing value, and `evidence[]` every
   original source. A known value is **never** overwritten by `"unknown"`, by an absent field, or by
   an empty array, and every known-vs-known disagreement becomes a `conflicts[]` entry.
4. **Store the score with its `score_version`** (and `as_of`, `weights_used`, `threshold` on a match
   run) so the rubric can change without orphaning history.
5. **Store the AI explanation and the deterministic score components separately.** `base_score` vs
   `match_score`; `rationale[]` / `risks[]` / `rerank.rationale` vs `component_scores` and
   `weighted_contributions`. No narrative field may be read by any scoring, ordering or filtering
   decision.
6. **Data minimization.** No secrets, API keys, tokens or cookies in any shipped file; adapters read
   configuration from environment variables at call time only. No personal data anywhere, including
   free-text fields (3.3).

---

## 13. JSON and determinism conventions

- UTF-8, **no** `\u` escaping of non-ASCII, `\n` line endings, exactly one trailing newline.
- Object key order is the **insertion order emitted by the producing script**, fixed per script so output is byte-stable. Do not sort keys unless a script's contract says so.
- Compact separators `(",", ":")` by default; `indent=2` with `(",", ": ")` under `--pretty`.
- `confidence` and `weighted_contributions` carry exactly **2 decimals**; every `*_score` field is an **integer**.
- Dates are `YYYY-MM-DD`; date-times are RFC3339 with an explicit offset or `Z`.
- **Rounding is half up**, at a closed list of sites only. Banker's rounding is forbidden.
- **No wall clock, ever.** `as_of` is the only temporal input. `datetime.now()`, `date.today()` and `time.time()` appear on no scoring or rendering path, log lines included.
- Ordering: `(score desc, canonical_domain asc)`, stable, with `"unknown"` sorting **last** and the input order as the final tie-break. No third key exists.
- `signals_fired[]`, `unknown_inputs[]`, `rules_evaluated[]` and `rules_skipped_unknown[]` are emitted **sorted ascending**. `missing[]` is **not** sorted — it is first-appearance order.
- Sets are converted to sorted lists before serialisation; a bare set is never serialised.
- Thousands separators (`3,000`) appear **only** in rendered text, always en-US grouping, never in JSON. The decimal separator is always `.`; scientific notation is forbidden in output.
- The same input, the same `--as-of` and the same config produce **byte-identical** output across runs, machines and Python 3.9 through 3.14.

---

## 14. Invariants a backend must not break

These are machine-checked by `scripts/validate_output.py --invariants`. Run it before you persist
anything.

| Area | Invariant |
|---|---|
| Evidence | Every material claim is backed by ≥ 1 evidence item on that claim, or carries `"unknown"` / is absent. No third possibility |
| Unknown | `"unknown"` is never stored or rendered as `false`, `0`, `""`, `null` or `"N/A"`. Unknown and verified-negative stay distinguishable in both JSON and Markdown |
| Unknown | Every criterion that took the unknown path has a matching `unknown_penalty_applied[]` record, and its `label` appears in `missing[]` |
| Unknown | **No hard-filter failure is caused by an unknown or absent input.** Every skipped-for-unknown rule appears in `rules_skipped_unknown` **and** in `unknown_penalty_applied` |
| Scores | `qualification_score` equals the weighted sum of the recorded `dimension_scores`; `sum(weighted_contributions) == base_score` within ±0.5; `match_score == clamp(base_score + rerank.delta, 0, 100)` |
| Filters | Every failure carries `rule_id` (`^HF-[0-9]{2}$`), `rule_name`, `reason`, `observed_value` and `required_value`. An excluded seller never carries a `match_score`, never appears in `results`, and always carries ≥ 1 `failed_rules` entry |
| Rerank | `results[].seller_id ∩ excluded[].seller_id = ∅`; `abs(delta) <= max_delta`; `applied: true` ⇒ non-empty rationale **and** evidence ids; `applied: false` ⇒ `delta == 0` |
| Rendering | Every candidate shows a score, a type, a `Why:` line with ≥ 2 reasons, a contact channel, an `Evidence:` line and a `Missing:` line; every discovery candidate additionally shows `Website:` and `Country:` |
| Rationale | Every candidate with `hard_filter.passed == true` carries ≥ 2 rationale items, each with ≥ 1 resolvable `evidence_id` |
| No-match | An empty result set is an explicit `no_match` object with a non-empty `reason`. `results == []` with `is_no_match == false` is invalid |
| References | Every `evidence_id` cited anywhere resolves inside the enclosing document — or, on a match result, inside `evidence_index`. A dangling id is a build blocker, not a warning |
| Versions | `schema_version` and `score_version` are present on every document and identical across all records of one output; `"unscored"` ⇒ `qualification_score == 0` and no `dimension_scores`; a match result never carries `"unscored"` |
| Time | Every scored document carries `as_of`; no `observed_at` is later than `as_of` end-of-day; no `source_date` is later than its own `observed_at` |
| Identity | `canonical_domain` and name normalization are idempotent; dedupe is idempotent and order-independent; no merge on name alone across countries; no merge of two different known domains |
| Merge | A merge never overwrites a known value with `"unknown"` / absent / empty; every known-vs-known disagreement is recorded; the merge is reversible in principle |
| Safety | No send capability anywhere; no personal-contact harvesting; no robots/login/paywall/CAPTCHA bypass; no secrets in any shipped file; nothing produced here sets `APPROVED_FOR_OUTREACH` or later |
| Resilience | A run that could not evaluate every candidate sets `partial: true` on the run envelope and explains why in its `notes[]`, instead of exiting non-zero |
| Reproducibility | Identical input, `--as-of` and config produce byte-identical output |
