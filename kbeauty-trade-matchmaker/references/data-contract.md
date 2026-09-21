# Data Contract — entities, fields, states, versions

The shipped summary of `schemas/*.json`: what each document holds, which fields may be unknown, how
records move through the state machine, and how a stored score is re-computed when the rubric
changes.
*Korean gloss: 데이터 계약 — 엔티티/필드/상태/버전 규약.*

| | |
|---|---|
| Audience | Anyone writing against these documents: the runtime agent, a TradeWith backend engineer, a reviewer |
| `schema_version` | `0.1.0` |
| `score_version` | `kbtm-score-0.2.0` |
| Canonical `as_of` in every example | `2026-09-12` |
| **Binding source of document shape** | `schemas/buyer.schema.json`, `seller.schema.json`, `rfq.schema.json`, `evidence.schema.json`, `match-result.schema.json`, `discovery-result.schema.json`, `acceptance-report.schema.json` |
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

Five document kinds, two run envelopes, and four operator documents (measurement, comparison,
audit, export):

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
| `acceptance-report` | `acceptance_report.py` | Yes | A **measurement** document (§9.3): operator labels joined back to a scored run. No scorer reads it, so producing one never moves `score_version` |
| `run-diff` | `diff_runs.py` | Yes | A **comparison** document (§9.4): what changed between two scored runs of the same search or RFQ. No scorer reads it, so producing one never moves `score_version` |
| `recheck-queue` | `stale_evidence.py` | Yes | An **audit** document (§9.5): the stored evidence to re-read, most urgent first. It fetches nothing and no scorer reads it, so producing one never moves `score_version` |
| `tradewith-bulk-buyers` | `export_leads.py --format tradewith-json` | Yes | An **export** document (§9.6): the TradeWith admin bulk-import body. It has no version fields, no scorer reads it, and it never carries a contact name, a phone number or notes |

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
| `missing` | array of string | | — | derived: de-duplicated `label` values of `unknown_penalty_applied[]` in **first-appearance order**, then the `verification_gaps` from `scoring.config.json` that apply (an unpublished minimum order, or a requested category evidenced only at a broader level) |
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
| `moq_unit` | string | | absent ⇒ `"units"` | the unit `max_moq` and `quantity` are denominated in. Count spellings — `pcs`, `pieces`, `EA`, `개`, `unit` — are the same unit as `"units"` (`_common.UNIT_SYNONYMS`) and need no special handling. **Set this whenever the catalogue uses a different basis such as `kg` or `sets`**, or every such seller is permanently unit-mismatched |
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
| `moq_unit` | **defaults to `"units"`**. Compared against `seller.moq_unit` after unit-synonym normalisation (`pcs` / `EA` / `개` / `unit` all equal `units`); a remaining mismatch (`kg` against `units`) makes the MOQ criterion unknown, skips HF-03 and fires `moq_unit_mismatch` |
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
| `match_run_id` | string | `MR-<rfq_id>-<as_of>-<NN>` — run provenance; `diff_runs.py` echoes it but joins two match runs on `rfq_id` (§9.4) |
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

### 9.3 The calibration pair — review sheet and `acceptance-report`

Two **measurement** documents. They exist to answer whether the rubric discriminates, and nothing
reads them back into a score: no scorer opens either, so producing one never moves `score_version`.
The protocol is `references/calibration-notes.md` §7.
*Korean gloss: 루브릭 검증용 측정 문서 — 점수에는 절대 영향을 주지 않는다.*

**The review sheet** (CSV, UTF-8, LF, produced by `scripts/make_review_sheet.py`). Ten columns, in
this order; the last five ship **empty** for the operator to fill:

| Column | Filled by | Notes |
|---|---|---|
| `record_id` | script | `buyer_id` / `seller_id` of the scored record; the join key |
| `entity_type` | script | `buyer` \| `seller` |
| `company_name`, `website`, `country` | script | copied from the scored document; the literal `unknown` when it carries none (a `match-result` candidate carries no `country`, so a match sheet's column is all `unknown`), or the literal `not_shown` when the blind rule below neutralises the column |
| `verdict` | operator | `accept` \| `reject` \| `unsure` |
| `reason_code` | operator | **required when `verdict` is `reject`**, optional otherwise: `wrong_company_type` \| `wrong_vertical` \| `wrong_market` \| `inactive_or_unreachable` \| `duplicate` \| `evidence_wrong` \| `other` |
| `note` | operator | free text; **no personal data** — the report refuses a sheet whose `note` carries an email address or a phone-number-like string (INV-31) |
| `reviewer_role` | operator | a **role label** ("trade operator"), never a person's name, address or number |
| `reviewed_on` | operator | `YYYY-MM-DD`, never later than the report's `as_of` |

Three cell states, never conflated (INV-02): an **empty** cell means "the operator has not
answered"; the literal `unknown` means "the scored document did not carry this value"; the literal
`not_shown` means "the sheet is withholding this column on purpose". A row with an empty `verdict`
is simply not reviewed and changes no denominator, and a row in which **every** cell is empty — the
trailing blank line a spreadsheet saves — is skipped rather than treated as a defect.

**Formula guard.** `company_name` and `website` come off a harvested public page, so either can
begin with `=`, `+`, `-`, `@`, a tab or a carriage return, which a spreadsheet reads as the start of
a formula. Any such cell is written with a leading apostrophe (`'=cmd|…`), which neutralises it and
leaves the text readable. `record_id` is **never** rewritten — it is the join key and an apostrophe
would break every row of the report — so a `record_id` that would need the guard is refused with
exit 1 instead.

The sheet is **blind by default**: no score, no rank, no `qualified` flag, no returned-versus-excluded
marker, and rows ordered by `sha256(record_id)` so the reviewer is not anchored by the rubric.
`--no-blind` keeps rank order and appends `rank,score,qualified` after the ten columns.
`--include-excluded` adds the `excluded[]` records, indistinguishable from the rest in blind mode,
which is the only way to measure a **false exclusion**.

**Hiding the score is not enough on its own.** `discovery-result.excluded[]` carries no `country`
field at all, so under `--include-excluded` the excluded rows would be exactly the rows whose
country cell reads `unknown` — the ranking hidden and the exclusions still legible. In blind
`--include-excluded` mode the sheet therefore neutralises any display column that would separate the
two populations, writing `not_shown` on **every** row (blanking only the excluded rows would be the
same tell inverted) and naming the columns on stderr. Two rules decide it: *structural* — a column
the excluded shape cannot carry, which is `country` on a discovery run whatever the data holds — and
*observed* — a column whose cell shapes do not overlap between the two populations, which catches a
run where, say, no excluded record happens to have a website. A match sheet keeps its `country`
column untouched, because neither population carries a country and `unknown` therefore separates
nothing. The ten-column layout never changes. The vocabularies live in `_common.py`
(`REVIEW_SHEET_COLUMNS`, `REVIEW_VERDICTS`, `REVIEW_REASON_CODES`) — they are vocabularies, not
tunable numbers, so INV-29 is unaffected; every number the two scripts use is in
`scoring.config.json` → `calibration`.

**The report** (`schemas/acceptance-report.schema.json`, produced by
`scripts/acceptance_report.py`) · **required**: `report_kind` (const `acceptance-report`),
`schema_version`, `score_version`, `as_of`, `summary`, `by_qualified`, `by_score_band`,
`threshold_sweep`, `discrimination`, `by_country`, `by_reason_code`, `false_exclusions`, `notes`.

| Field | Notes |
|---|---|
| `summary` | counts (`returned`, `excluded`, `reviewed`, `reviewed_excluded`, `accept`, `reject`, `unsure`), `review_coverage`, **`human_acceptance_rate` = accept / (accept + reject)** with `unsure` excluded and reported separately, plus `score_version`, `source_kind`, `entity`, `threshold_used`, `threshold_mode`, `as_of`, `min_sample` and `insufficient_sample` |
| `by_qualified` | `qualified_true` / `qualified_false` / **`qualified_unknown`**, each the shared count block — the precision of the `qualified` flag, what it misses, and the records it never judged. Three buckets, not two: "unknown is not false" (§2), and folding an absent or `"unknown"` flag into `qualified_false` would report a gap as a rejection in the one document whose job is to measure the flag |
| `by_score_band` | one row per `calibration.score_bands` pair (0–49, 50–59, 60–69, 70–79, 80–89, 90–100), in config order |
| `threshold_sweep` | one row per candidate cut-off over `calibration.threshold_sweep` (50…90 step 5). `precision` = accept / (accept + reject) **among the returned records at or above the cut-off**; `recall` = those accepts / **every** accepted record in the report, excluded ones included, because an accepted record the rubric excluded is a lead that cut-off would also have lost. A row **below** the run's own `threshold_used` is only measurable when the run returned its below-threshold records: `score_buyer.py` / `score_seller.py` do (`qualified` is a flag there, not a filter), `score_match.py` does not, and a match report then says so in `notes[]` |
| `discrimination` | `overall` and `by_dimension[]` AUC — P(score of an accepted record > score of a rejected one), ties 0.5 — over the six discovery dimensions or the six match components, each with a `distinct_values` count |
| `by_country`, `by_reason_code` | breakdowns; `by_reason_code` emits all seven codes in vocabulary order whether or not they occurred |
| `false_exclusions` | excluded records an operator accepted, with the `reason_summary` and `failed_rule_ids` that excluded them |
| `notes` | run-level notes, always including the statement that this report measures PRD 17 **Human Acceptance Rate** only |

Rate conventions: every rate is rounded half-up to `calibration.rate_decimals` and is **`null`, never
`0`, when its denominator is zero** — the same unknown-is-not-zero rule as §2. `insufficient_sample`
is true when `accept + reject < min_sample`; the report then carries a `notes[]` line saying it
cannot justify a weight or threshold change.

Counter invariants: `returned >= reviewed >= accept + reject + unsure` is an equality on the last
three.

**Everything the report refuses**, each with exit 1 and one `ERROR:` line, rather than repairing
it — a calibration report is evidence for a weight or threshold decision, and a silently dropped or
silently doubled row changes the denominator that decision rests on:

1. an unknown `verdict`, or an unknown `reason_code`;
2. a `reject` carrying no `reason_code`;
3. a duplicate `record_id` whose rows disagree on `verdict`, **or** agree on `verdict` and disagree
   on `reason_code` (a duplicate that agrees on both is kept once and noted);
4. a review row whose `record_id` matches no scored record;
5. a `reviewed_on` that is malformed, not a real calendar date, or later than the report's `as_of`;
6. a `note` or `reviewer_role` carrying an email address or a phone-number-like string (INV-31);
7. one `record_id` appearing in two `--scored` documents — the same run passed twice multiplies
   every count it appears in and defeats `min_sample`;
8. two `score_version`s in one report (the INV-23 principle, BUILD-CONTRACT 12.3 rule 4);
9. a discovery run mixed with a match run, or a buyer population mixed with a seller one;
10. scored documents carrying no record at all, returned or excluded.

A run that returned **nothing** — a `no_match` match run, or a discovery run that excluded every
candidate — is not a refusal: the report succeeds with every rate `null` and a `notes[]` line saying
it measures false exclusions only, which on such a run is the one thing worth measuring.

**The report is validated before anything is written.** On a schema failure nothing reaches stdout
or `--output`. That is deliberately unlike the scorers, which emit their document even on exit 1
(BUILD-CONTRACT R7.3.2) so an operator can see which record broke: a report is a single aggregate
that is either trustworthy or not, and a file on disk that failed its own schema is the one most
likely to be quoted anyway.

**What the personal-data scan does and does not catch.** It is deliberately asymmetric. An `@`
inside a word is almost never anything but an address, so email detection is broad and also catches
the `name [at] company.example` obfuscation. A long run of digits, by contrast, is usually **not** a
telephone number in this vertical — the discovery playbooks tell an operator to record a CDSCO
registration certificate number, a BPOM notification number, an ISO certificate number, a registry
URL and a trading period, every one of which is a longer digit run than a phone number. A telephone
therefore needs a telephone **signal**: a leading `+`, a trunk-prefix `0` on a run of nine digits or
more, or a `tel` / `phone` / `mobile` / `전화`-style label just in front. Input is NFKC-normalised
first, so full-width digits cannot walk past the scan, and URLs and ISO dates are removed before it.

### 9.4 `run-diff` — comparing two runs

`scripts/diff_runs.py --before <run> --after <run>` compares two scored documents of the same kind
and reports what changed. It is an operator tool outside every mode: it reads two finished runs,
writes one JSON document, and nothing it produces feeds a score.
*Korean gloss: 두 실행 결과 비교 문서 — 점수에 영향을 주지 않는다.*

**What it refuses** (exit 1, exactly one `ERROR:` line, nothing on stdout or `--output`), checked in
this order:

1. a document that is not a run: a bare `--records-only` array, or neither a `discovery-result` nor
   a `match-result`;
2. a document that fails its own schema (discovery records are checked against `buyer` / `seller`);
3. a run whose records disagree with its envelope on `score_version` (INV-23);
4. two runs with different `score_version`s. There is no opt-in: scores from two rubrics are not on
   one scale, and even returned-versus-excluded is a rubric output (§11.3 rule 4);
5. a discovery run against a match run, a buyer run against a seller run, or match runs for
   different `rfq_id`s. A different discovery `query` is **not** refused — widening a query is a
   legitimate reason to diff — and is flagged in `context.query_changed`;
6. a record id that appears twice inside one run, or a match record whose `rank` is not its
   position in `results` plus one (the diff reports position as rank, so a disagreeing `rank` is
   refused rather than silently overridden);
7. an explicit `--as-of` earlier than either input's `as_of`. Without `--as-of` the diff is dated at
   the later of the two.

The rubric-version check comes before the kind check, as in `acceptance_report.py`, because it is
the more fundamental defect. A malformed or empty flag, an unreadable file or both sides on stdin
is a usage error (exit 2).

**Pairing.** Records are paired by identical id first. A still-unpaired record is then paired
through `merged_from`: an after-record (ascending id) takes the smallest unpaired before-id it lists,
then the same the other way round. Such a pair carries `matched_by: "merged_from"` and `before_id`,
and is always listed, even with equal numbers, because the id change is itself news: under
`changed[]` when both sides are returned, otherwise under `newly_excluded[]` / `newly_returned[]`.
Excluded entries carry no `merged_from`, so two excluded records are only ever paired by id. A before-record that some after-record's `merged_from` absorbed is
listed under `gone[]` with `merged_into`: a dedupe merge, not a lost lead. The literal id `"unknown"`
is never paired; such records are listed under `new` / `gone` with a note. `canonical_domain` is not
a pairing key — two legal entities can share one — and `match_run_id` is echoed but not a key either,
because it changes with `as_of`; match runs are joined on `rfq_id`.

**The lists.**

| List | Holds | Item fields |
|---|---|---|
| `new` / `gone` | listed in one run, absent from the other | `record_id`, `company_name`, `canonical_domain`, `state`; `score` / `rank` (match) / `qualified` when returned, `reason_summary` / `failed_rule_ids` when excluded; `gone` adds `merged_into` |
| `newly_excluded` | returned before, excluded after | `before_score`, `before_rank` (match), `before_qualified`, `reason_summary`, `failed_rule_ids` |
| `newly_returned` | excluded before, returned after | `before_reason_summary`, `before_failed_rule_ids`, `after_score`, `after_rank` (match), `after_qualified` |
| `exclusion_changes` | excluded in both, reason or rule set differs | `before_reason_summary`, `after_reason_summary`, `rules_added`, `rules_removed` |
| `changed` | returned in both, a compared field differs | only the changed keys among `score`, `base_score` (match), `rank` (match), `dimensions`, `qualified`, `confidence`, `missing` |

Every paired item starts with `record_id` (the after id), `before_id` (only when not paired by id),
`matched_by` and `company_name`. In `changed[]`, a dimension the scorer dropped on one side reads
`"absent"` with a `null` delta; `qualified.flip` is `gained`, `lost`, or `undetermined` when a side
does not carry the flag (unknown is not false); `confidence.delta` is computed in Decimal and
rounded half-up to two places; `missing` lists labels `added` (after order) and `removed` (before
order), compared only when both sides carry a list. `changed[]` sorts by |score delta| descending,
then `record_id`; every other list sorts by `record_id` in codepoint order.

**Run-level fields.** `before` / `after` echo each run's `as_of`, `score_version`, `skill_version` (`"unknown"` when absent or empty),
`match_run_id` and `rfq_id` (`null` on discovery), `threshold_used`, `threshold_mode`, `partial`
(`"unknown"` when the document omits it) and the returned / excluded counts. `context` flags an
`as_of`, threshold, query (discovery) or `weights_used` (match) change; the inapplicable one is
`null`.

**Notes.** Fixed English sentences: on every match diff, that a seller absent from one run may have
been cut by the threshold or `top_n` rather than dropped, so new / gone mean *listed / not listed*;
that the runs have different `as_of` dates (recency moves scores with no change in evidence); that
`--before` is dated later than `--after`; that the runs came from different skill versions; that
records with id `"unknown"` could not be paired; that a run is `partial`.

**What is never copied.** Contact channels, evidence, websites, observed / required values and whole
`failed_rules` objects stay behind; only the rule ids travel. A diff therefore carries nothing
harvested beyond a company name and a domain.

**Validated before anything is written**, as the acceptance report is: on a schema failure nothing
reaches stdout or `--output`, and the output file is opened only after the diff has passed.

### 9.5 `recheck-queue` — the evidence to re-read

An **audit** document produced by `scripts/stale_evidence.py` from records already on disk: a
`discovery-result`, a golden bundle, dedupe output, a match input (its `rfq` is scanned as one more
record), a list of records or a single record. It fetches nothing, changes no record and no score,
and no scorer reads it. The reason codes and how to work the queue are
`references/evidence-policy.md` §5.6.
*Korean gloss: 재확인 대기열 — 다시 읽을 근거 목록. 점수와 레코드는 바꾸지 않는다.*

| Field | Notes |
|---|---|
| `report_kind` | Always `recheck-queue`; the discriminator `validate_output.py --schema auto` routes on |
| `score_version` | The config whose `evidence` block (buckets, `stale_threshold_days`, material claims) was applied. Input records scored under another rubric are **not refused** — the queue aggregates no score — and `notes[]` names their version |
| `as_of` | The required `--as-of`; every age is measured to it. An `observed_at` later than it is refused (INV-24) |
| `policy` | `due_from_bucket` (`aging`), `stale_threshold_days`, the bucket labels and edges, `reason_order` |
| `summary` | `records_scanned`, `records_queued`, `records_listed` (after `--top`), `records_without_evidence`, `evidence_items_scanned`, `evidence_items_queued`, `evidence_items_undated`, `claims_without_current_evidence`, `by_reason` (every code, records for record-level codes and items for item-level codes), `by_bucket` (every scanned item by `source_date` bucket, plus `unknown`) |
| `queue[]` | `position`, `entity` (`buyer` \| `seller` \| `rfq`), `record_id`, `input_index`, optional `company_name` / `canonical_domain`, `priority_reason`, `record_reasons[]`, `claims[]`, `items[]` |
| `claims[]` | A covered material claim with no current item: `claim`, `reason` (`no_current_evidence`), `evidence_ids`, `newest_source_date` (or `unknown`), `counts{old, undated, flagged}` |
| `items[]` | `evidence_id`, `claim`, `material`, `source_url` (or `unknown`), optional `source_tier`, `source_date`, `age_days`, `recency_bucket`, `last_read` (the `observed_at` date), `days_since_read`, `reasons[]`, and `conflicts_with` on an unresolved conflict. Unknown ages are the string `unknown`, never `0` |
| `notes[]` | At most one note per kind of tolerated input problem (an `evidence` value that is not a list; a `source_date` that is not a date), with the count and the first 10 cases, each echoed value cut to 40 characters. Always ends with the line saying the queue changed no record and no score |

Only locators and dates are copied: never `value`, `quote_or_summary` or `contact_channels`. A
`match-result` is refused with exit 1, because its `evidence_index[]` carries no `source_date` and its
candidates carry no `operational_status` or `conflicts[]`; pass the match input instead. An
`acceptance-report` or another report is refused the same way, and so is a duplicate record id or a
record whose `buyer_id` / `seller_id` / `rfq_id` is missing, blank or not a string, even when the
bundle's `entity` names the kind. A copied locator longer than its input schema allows (id 128,
`evidence_id` 128, `claim` 200, `source_url` 2048, a `conflicts_with` id 128) is refused rather than
cut, because a cut locator points somewhere else; an over-long `company_name` (300) or
`canonical_domain` (253) is only left out. Valid input can therefore never fail the queue's own
schema.

### 9.6 Export files — generic CSV and the TradeWith import

`scripts/export_leads.py` writes the leads of **one scored `discovery-result`** as a file a person
imports. It is a file format, not an adapter capability: it opens no connection, posts nothing and
changes no score, and no scorer reads what it writes. A `match-result` is refused with exit 1 (a
document that parses but is the wrong kind, BUILD-CONTRACT 7.3, as in `diff_runs.py` and
`stale_evidence.py`), because a match candidate carries no company fields.
*Korean gloss: 점수화된 발굴 결과를 CSV 또는 TradeWith 일괄 등록 파일로 내보낸다 — 전송하지 않는다.*

| `--format` | Entity | What it is |
|---|---|---|
| `csv` (default) | buyer, seller | Generic spreadsheet / CRM file, operator-held |
| `tradewith-csv` | buyer | The CSV that TradeWith's admin buyer-import page reads (header by name) |
| `tradewith-json` | buyer | The exact request body of the admin bulk-import endpoint, `{"buyers": [...]}`, for an admin posting through an authenticated API client. The admin page does **not** read this file |

A seller run with a TradeWith format is a usage error: TradeWith has no seller bulk import (sellers
self-register).

**Filters**, applied in this order; only the first that fires is counted on stderr:

1. `operational_status` is `closed` or `unreachable` → skipped in every format;
2. `qualified` is not `true` → skipped unless `--include-unqualified` (an absent flag counts here);
3. `--min-score N` given and `qualification_score` is below N or absent → skipped (a NaN or
   infinite score is refused, not skipped);
4. TradeWith formats only: `country` is absent or `unknown` → skipped (the CSV keeps the row, with
   `country` written as `unknown`).

`excluded[]` is never exported: those records were never scored and are not leads. Record order is
the document's own order (INV-30); nothing is re-sorted. `stale: true` records are kept and the flag
is carried in the CSV and in the TradeWith `originalSource`. When two exported records share a normalized name or a `www.`-stripped
domain, stderr prints a `WARNING: possible duplicate …` line — run `dedupe_companies.py` first, or
the import creates two rows for one company.

**Generic CSV.** UTF-8 without BOM, LF, header first. Columns, in this order:

`record_id, entity_type, company_name, canonical_domain, website, country, company_type,
product_categories, qualification_score, qualified, confidence, status, operational_status, stale`,
then six `dim_<name>` columns — buyer `kbeauty_fit, b2b_role, sourcing_intent, market_relevance,
reachability, evidence_quality`; seller `product_fit, commercial_model, operational_fit,
compliance_readiness, export_readiness, evidence_quality` — then `contact_channels, missing,
score_version, as_of` (`as_of` is the record's, else the envelope's).

| Cell | Means |
|---|---|
| `unknown` | the field is absent or holds `"unknown"` (§2) |
| `none` | a present-and-empty list — verified none |
| `not_applicable` | `dim_market_relevance` when the scorer dropped the dimension as inapplicable |
| `withheld` | `contact_channels` when every stored channel was withheld by the rules below |
| `true` / `false` | a boolean |

Lists are joined with `"; "`. `contact_channels` is `type=value` per channel joined with `" | "`, in
stored order; **labels are never exported**. A `corporate_email` value is kept only when it is a role
mailbox: an exact local part from the script's role list (`info`, `sales`, `sourcing`, `partners`,
`trade`, `export` …) on the record's own `canonical_domain` / `alias_domains` (`www.` stripped), not
a free-mail provider. Any other address is dropped and counted on stderr, and so is a `linkedin`
value that is not an organisation page — a member profile names a person. The URL is parsed, not
prefix-matched: its first path segment must be `company`, `showcase` or `school`, followed by a
name, and no segment may be empty, `.` or `..` (percent-encoded dots included), so
`/company/../in/<name>` is withheld. A `phone` or `messenger` value may be a published company
number (BUILD-CONTRACT 3.6), so the phone shape does not refuse those two types **when the whole
value is a number** (digits, spaces, `+ ( ) . / -`) or, for `messenger`, a `wa.me` /
`api.whatsapp.com` link to one; any other text in them — a name, "mobile" — gets the full scan, and
an address always refuses. Every cell goes through the spreadsheet formula guard (§9.3): a leading
`=`, `+`, `-`, `@`, tab or CR gets an apostrophe, and so does one that follows leading whitespace or
is a full-width form (`＝`), because an importer that trims cells would store the bare formula.

**TradeWith mapping** (`tradewith-json` rows; `schemas/tradewith-bulk-buyers.schema.json`). A key is
written only when the rule yields a real value — never `null`, `""`, `"unknown"` or `[]` — because
an omitted key leaves the stored TradeWith value alone on re-import, while an explicit one
overwrites an admin's edit.

| DTO key | Source |
|---|---|
| `sourceId` | `kbtm:` + `canonical_domain` with `www.` stripped (`kbtm:gulfglow.example`), or `kbtm:id:` + `buyer_id` when the domain is unknown. The domain is the merge key and survives dedupe survivor-id churn, so re-importing a later run updates the same row; the `kbtm:` prefix keeps these ids apart from every other CSV import. Two exported records on one domain are refused |
| `companyName` | `company_name` |
| `country` | `country` (ISO alpha-2) |
| `website` | `website`, when it is an `http(s)://` URL |
| `industry` | `BEAUTY` (the IndustryCode TradeWith's buyer matching filters on) |
| `category` | `company_type`, unless `unknown` |
| `productsSummary` | `product_categories` joined `", "`, then `"Korean brands: "` + `korean_brands_carried`, joined `"; "` |
| `originalSource` | `kbeauty-trade-matchmaker \| score_version=<v> \| as_of=<d> \| record_id=<id> \| stale=<true\|false\|unknown>` — the provenance, and the only place a TradeWith row says the company may be stale |
| `sourceUrl` | the first evidence item with `is_official: true` and `source_tier: 1`, else `website` |
| `social` | the first `linkedin` channel that is an organisation page (parsed as in the generic CSV above: `/company/`, `/showcase/` or `/school/` plus a name, no empty, `.` or `..` segment). TradeWith shows `social` to sellers unmasked, so a member profile is withheld and counted on stderr |

`tradewith-csv` carries `sourceId, companyName, country, website, industry` — the only columns of
that set the admin page maps. It therefore **drops** `originalSource`, `sourceUrl`, `category`,
`productsSummary` and `social`: rows imported through the page arrive with no provenance, and their
matching embedding is built from name, industry and country only. stderr says so on every such
export; prefer `tradewith-json` whenever an authenticated API client is available. Because that page splits on newlines and toggles on every
double quote, a value holding `"` or a line break is refused, and so is one that would need the
formula guard, including after leading whitespace (an apostrophe would be stored as part of the
name); use `tradewith-json` instead. `tradewith-json` keeps such a value verbatim and prints a
`WARNING` naming the rows, because a later spreadsheet export from TradeWith would evaluate it.
`validate_output.py --schema tradewith-bulk-buyers` (or `--schema auto`, which detects a document
whose only discriminator is `buyers`) re-checks a `tradewith-json` file edited by hand; only the
generic invariants apply to it.

**Never sent, and why.** `contactName`, `contactEmail`, `contactPhone` — not even a company role
mailbox (INV-11, INV-31; they are the fields TradeWith masks). A present `contactEmail` makes
TradeWith store `contactConfidence: high`, a verified-contact signal this package never produced,
and re-importing an explicit address would overwrite one an admin corrected by hand. `qualityTierLabel`: without it an insert lands as **tier C**, which TradeWith's buyer matching
leaves out by default, and an update keeps the admin's label — promoting a row to A or B is a human
decision after review. `notes` and `extraNotes`: re-import would overwrite an admin's notes, and the
matching endpoints show both to sellers. `hsCodes`, `annualVolumeUsd`, `city`, `logoUrl`,
`imageUrl`, `scaleRevenue`, `displayFlag`, `postedDate`, `altWebsites`: the record has no source for
them (`alias_domains` are bare domains, not URLs). The DTO has no `tags` field, so tags are also
added by the admin; until then a row cannot category-match.

**Know before importing.** Every exported row lands with `contactConfidence: low`, and a re-import
resets it to `low` even when an admin has since added a contact by hand; the contact values
themselves survive, because an omitted key is not written. On import TradeWith builds an embedding from
`companyName`, `industry`, `category`, `productsSummary` and `country` — company-level fields only.
The default JSON body limit is about 100 KB; a larger body prints a `WARNING` and should be split
(for example with a higher `--min-score`).

**Refusals** (exit 1, one `ERROR:` line, nothing written): a schema-invalid input (the envelope and
each record against its entity schema; `--no-validate` skips only this step — here it skips *input*
validation, unlike BUILD-CONTRACT 7.2 where it skips output self-validation); an unscored run or a
record whose `score_version` differs from the envelope's (INV-23); a missing or duplicate record id;
two TradeWith rows with one `sourceId`; a wrong-typed field or a NaN / infinite number (typed guards
hold even under `--no-validate`); an email address or a phone-number-like string in any exported
text — URLs included, and read again after percent-decoding, so `%40` and `%2B` do not hide an
address or a `+` prefix, and a `tel`/`phone`/`mobile`/`whatsapp` label joined to a number by `-` or
`_` in a URL slug counts — except the phone shape of a bare-number `phone` or `messenger` channel in
the generic CSV; a TradeWith body that fails its own schema
(checked always, even under `--no-validate`); a `tradewith-csv` value the admin page cannot parse;
a `match-result` or other input that parses but is not a scored `discovery-result`.
Exit 2: a bad flag, `--pretty` with a CSV format, `--min-score` outside 0–100, a malformed,
impossible (`2026-13-01`) or empty `--as-of`, a seller run with a TradeWith format, an unreadable
input, an unwritable `--output`. An export in which no record passes the filters is exit 0 with a
header-only CSV or `{"buyers":[]}` and a `WARNING`. Everything is checked before the first byte is
written; only a disk error during the write can leave a partial file.

---

### 9.7 `rfq-intake` — from a buyer's message to an RFQ

`scripts/intake_rfq.py` sits in front of Mode 3. The agent reads a free-text request and writes the
**input**; the script checks it and emits the **report** (`schemas/rfq-intake.schema.json`). It scores
nothing, no scorer reads it, and it cannot move `score_version`.

**Input** (agent-written):

```json
{
  "schema_version": "0.1.0",
  "rfq_id": "DRAFT-UAE-01",
  "buyer_id": null,
  "message": { "text": "…the buyer's words, verbatim…", "language": "en", "channel": "chat" },
  "fields": {
    "destination_country": { "value": "AE", "quote": "distributor in Dubai", "inferred": true },
    "destination_region":  { "value": "GCC", "quote": "Shipping to the GCC" },
    "quantity":            { "value": 5000, "unit": "pcs", "quote": "Around 5,000 pcs" },
    "required_certifications": { "value": ["ISO 22716"], "quote": "Need ISO 22716" }
  }
}
```

`fields` may carry only: `destination_country`, `destination_region`, `product_category`,
`product_categories_extra`, `product_description`, `product_forms`, `quantity`, `max_moq`, `target_price`,
`commercial_model`, `required_certifications`, `preferred_certifications`, `timeline`,
`max_lead_time_days`, `required_seller_countries`, `excluded_seller_countries`. `status`, scores and
`evidence` are the script's to set (`draft`, `unscored`, `0`, `[]`); offering them is exit 1. `quote` is a
string or a list of strings; `unit` exists on `quantity` and `max_moq` only.

**Rules the script enforces**

| Rule | Behaviour |
|---|---|
| Quote must be in the message | Compared after NFKC, casefold and whitespace collapse. One absent quote refuses the whole input: exit 1, nothing written |
| A quote is a phrase | 2..200 characters, and not cut out of the middle of an ASCII word ("on" inside "toner"). Korean is exempt from the word test because particles attach |
| A stated number is the number in its quote | `quantity`, `max_moq`, `target_price`, `max_lead_time_days`: the value's digits must occur in the quote (`50000` is not "5,000 pcs"); beside a number word (천, 만, k, thousand, ribu…) trailing zeros are not expected. `inferred: true` skips this and is confirmed with the buyer instead. Amounts are finite and at most 10^12 |
| No personal data | A quote, description, region word or `buyer_id` carrying an address or telephone shape is exit 1 (INV-31). The report does not copy `message.text`; it keeps `length`, `sha256` and the quotes. A personal *name* inside a quote cannot be detected here: quote the requirement, not the greeting |
| Region is not a country | A region word offered as `destination_country` is exit 1. In `destination_region` it becomes a note and, when no country is known, a `region_not_country` question. Nothing expands it (BUILD-CONTRACT 8.7) |
| Region word | `destination_region.value` is at most 80 characters and must occur inside its own quote |
| Number without a unit | `quantity` → `"unknown"`, `max_moq` → `null`, the figure kept in a note, `unit_unstated` asked. `unit: "unknown"` or an empty token is unstated too; any other unit must be in the package vocabulary or be a currency code. "5천" may be pieces or money; the script does not choose |
| Two units | `moq_unit` denominates both fields, so `quantity` is held and `unit_mismatch` asked |
| Price without a currency | `target_price` → `"unknown"`, `currency_unstated` asked |
| No commercial model | `"either"`, the schema's no-constraint value, with a note and a `missing` question |
| No product category | `missing_required`, which is **blocking**: `rfq` is `null`, `ready_for_matching` is `false`, `readiness` is absent |
| Empty list | Refused, except `required_certifications: []` ("no certificate needed"), which is accepted and shown back as `confirm_inferred` |
| Unknown vocabulary | A category or certification outside the package vocabulary is kept verbatim (INV-33) and asked about |
| `inferred: true` | Accepted, and a `confirm_inferred` question shows the buyer the quote and the reading |

**Report.** `questions[]` is ordered by reason — `missing_required`, `region_not_country`,
`unit_unstated`, `unit_mismatch`, `moq_exceeds_quantity`, `currency_unstated`, `timeline_in_past`,
`unmapped_category`, `unmapped_certification`, `confirm_inferred`, `missing` — then by field, and
numbered `Q-01`…; each carries `text_en` and `text_ko`. `readiness` is `score_match.py`'s own readiness
function applied to the emitted RFQ, so it is the figure Mode 3 prints; `buyer_id` counts towards it and
is the operator's link to make, so it is a note, not a question. Every stated field's quote is kept on the
RFQ under `extensions.intake`. The RFQ is validated against `rfq.schema.json` before the report is
written.

## 10. State machine

UPPERCASE `entity_status` applies to buyer and seller records and to a seller inside a match run.
RFQ `status` is a separate lowercase namespace (section 6) and is never mixed with these.

### 10.1 Allowed transitions

| From | Allowed next states | Performed by | Entry condition |
|---|---|---|---|
| *(new)* | `DISCOVERED` | **skill** | A candidate exists with at least one source URL |
| `DISCOVERED` | `VERIFIED`, `CLOSED` | **skill** | `VERIFIED` requires ≥ 1 evidence item on a material claim from a tier ≤ 3 source that is neither `inferred` nor `stale` |
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
| `VERIFIED` | ≥ 1 evidence item whose `claim` is a material claim, whose `source_tier <= 3`, and that is neither `inferred` nor `stale` |
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
(`kbtm-score-0.2.0`).

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
   least one MINOR cycle, so they can be re-scored under the new version and compared with
   `diff_runs.py` among same-version runs. Runs are joined on `rfq_id`; `match_run_id` is echoed
   only as provenance.

`scripts/diff_runs.py` (§9.4) compares two runs only when both carry the same `score_version`; a
diff across a rubric change is refused, not approximated. After a rubric change, re-score the stored
raw records on the new config and diff runs of the new version against each other (for example the
original `--as-of` against a new one). Runs of the old version stay comparable among themselves.

A minimal re-computation:

```bash
python3 scripts/score_seller.py \
  --input storage/sellers.raw.json \
  --query storage/query-seller-sunscreen-oem.json \
  --as-of 2026-09-12 \
  --output out/sellers.scored.json --pretty
```

### 11.4 Migration — `normalized_name` changed in v0.2.0

`skill_version 0.2.0` changes what `normalize_company_name` returns for two families of input.
Neither is a schema change and neither touches a score, so `schema_version` stays `0.1.0` and
`score_version` stays `kbtm-score-0.1.0` — but `normalized_name` is **dedupe key 2** (§12 rule 2),
so a stored record normalised by an earlier version and a fresh one can fail to merge when they are
the same company.
*Korean gloss: v0.2.0에서 `normalized_name` 규칙이 바뀌었으므로, 이전 버전으로 저장한 레코드는 새
레코드와 중복 제거하기 전에 반드시 다시 정규화해야 한다.*

| What changed | Before → after |
|---|---|
| Turkish dotted capital `İ` | NFKC + casefold now folds it, so `İPEK Kozmetik` → `ipek kozmetik` rather than keeping a combining dot |
| Indian / Singaporean forms | `pvt`, `llp` and `private limited` are stripped: `Aurora Beauty Pvt Ltd` → `aurora beauty`, `Sunbright LLP` → `sunbright` |
| Indonesian forms | leading `PT` / `CV` and trailing `Tbk` are stripped: `PT Cantik Indonesia` → `cantik indonesia` |
| Turkish forms | `A.Ş.`, `Ltd. Şti.`, `San. ve Tic.`, `Anonim Şirketi` are stripped: `ABC Kozmetik San. ve Tic. Ltd. Şti.` → `abc kozmetik` |

**Required action.** Re-run `normalize_company.py` over every stored record before de-duplicating it
against records produced by v0.2.0. Comparing an old `normalized_name` with a new one is comparing
two different functions, and the failure is silent: two records for one company sit side by side
with no `merges_suggested[]` entry, because on the stored spelling they genuinely do not match.
`canonical_domain` is unaffected, so any record carrying a known domain merges as before; the risk
is confined to the domainless fallback path.

**A known consequence, accepted.** Stripping a leading `PT` / `CV` means `PT Cantik Indonesia` and
`CV Cantik Indonesia` both normalise to `cantik indonesia` and so share the fallback name key. They
are different legal entities. This is accepted because the fallback key is **always** paired with a
known, equal country (§12 rule 2) and is used **only** when no `canonical_domain` is available, and
because the rule never merges on its own: it raises a `merges_suggested[]` entry and a "possible
duplicate" note for a human to resolve. Keeping the prefix instead would have left every Indonesian
record un-mergeable against the same company written without its form, which is the commoner case.

Three tokens are deliberately **end-only** for the mirror-image reason — `llp`, `pvt` and the
Turkish `tic` / `aş` family. Tried at the start they would eat the first real word of `LLP
Cosmetics`, `Pvt Beauty`, `Tic Tac Beauty` or `Aş Kozmetik`, and §12 rule 2 prefers a missed merge
to a wrong one. `tests/fixtures/normalize.cases.json` pins all four.

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
