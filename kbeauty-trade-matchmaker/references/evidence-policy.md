# Evidence Policy

Korean gloss: 근거 정책 — 무엇을 사실(fact)로 기록하고, 무엇을 추론(inference)으로 표시하며, 무엇을 `unknown`으로 남길지에 대한 규칙.

This page is the operator- and agent-facing rulebook for how any value earns the right to be
written into a buyer, seller, RFQ or match document. It implements PRD 6.2 (EVID-01..EVID-05),
PRD 10.3 (source priority) and PRD 11.3 (data minimization).

**Where the numbers live.** This page states *rules*, never *constants*. Every weight, point value,
multiplier, bucket edge and penalty used by the evidence-quality sub-score lives in
`schemas/scoring.config.json` under `evidence`, and is described algorithmically in
the repo-internal `SCORING-CONTRACT.md` §4 (a development document; it does **not** ship inside the
installed package, so treat the config as the operative source). Field names, enums and required keys live in `schemas/*.json` and are
restated for the runtime in `references/data-contract.md`. If this page ever appears to disagree
with a schema or with the scoring config, **the schema and the config win**.

---

## 1. The one-line rule

> Write a value only when a source says it. Otherwise write `"unknown"` and let the penalty happen.

Korean gloss: 출처가 말하지 않은 값은 쓰지 않는다. 모르면 `"unknown"`으로 두고 감점을 받는다.

A penalised unknown is a **correct** outcome (PRD 19, PRD 6.4.4). Inventing a plausible value to
avoid a penalty is the single worst failure this skill can commit, because the resulting record
looks *more* trustworthy than an honest one while being false.

---

## 2. The trichotomy — fact, inference, unknown

Every material value resolves to exactly one of three evidence states. Decide the state **before**
writing the field, not afterwards.

### 2.1 Decision table

| | **Fact** | **Inference** | **Unknown** |
|---|---|---|---|
| Korean gloss | 사실 | 추론 | 미확인 |
| When it applies | A source states the value, and the passage you can quote says it **literally** | A source states something from which the value follows, and the quote you can cite genuinely supports that step | No source states it, or the only support is a guess, a vibe, or a strengthened reading |
| Evidence item | REQUIRED; `inferred` absent or `false` | REQUIRED; `inferred: true` | **No evidence item may be created.** Do not file an evidence item whose `value` is your guess |
| What goes in the field | the observed value, normalized | the derived value, normalized | the literal string `"unknown"` (or the key is absent) |
| What goes in `quote_or_summary` | the literal passage | the literal passage the inference rests on — **never** the conclusion | n/a |
| Where the reasoning goes | nowhere (there is none) | the record's `notes[]`, naming the evidence id | the record's `notes[]`, stating what was checked and not found |
| Scoring consequence | scores normally | scores normally, but the record takes `evidence.inferred_penalty` when a material claim's **only** support is inferred | the criterion takes the unknown path, emits an `unknown_penalty_applied[]` entry, and its label surfaces in `missing[]` |
| Rendered as | the value | the value (`notes[]` carries the caveat) | the literal lowercase `unknown` — never `N/A`, `-`, `null`, blank, `false` or `0` |

### 2.2 The fourth state that is not part of the trichotomy

`"unknown"` and **verified negative** are different things, and the schemas keep them apart
(`references/data-contract.md`, BUILD-CONTRACT §3.2, INV-02):

| Encoding | Meaning | Evidence requirement |
|---|---|---|
| field absent, or tri-state `= "unknown"` | not yet determined | none — and none may be fabricated |
| tri-state `= false`, or array **present and empty** | checked; demonstrably not the case | **REQUIRED**: an evidence item on that claim key whose `quote_or_summary` records *what was checked* |
| `true`, or a non-empty array/number | evidenced | REQUIRED |

So `certifications: []` is a claim ("we looked at their certification page; it lists none") and needs
an evidence item. An **absent** `certifications` key is silence and needs nothing. Writing `[]`
because you did not look is a fabrication in the same way that writing `["ISO22716"]` would be.

### 2.3 Ask these five questions, in order

1. **Can I quote a passage that says it?** → Fact. Record the quote verbatim.
2. **Can I quote a passage from which it follows in one step, with no added assumption?** →
   Inference. Set `inferred: true`, quote the passage, put the step in `notes[]`.
3. **Am I about to add a word the source did not use** ("also", "including", "up to", "certified",
   "currently")? → Stop. That is not an inference, that is a rewrite. Go to 5.
4. **Did I check and find a definite negative?** → Verified negative (`false` / `[]`) **with** an
   evidence item recording the check.
5. **Otherwise** → `"unknown"`, plus a `notes[]` line saying where you looked.

---

## 3. What counts as a material claim

A **material claim** is one that a human would act on commercially, and one this skill refuses to
carry without a source. The machine-checked subset — the INV-01 obligation and the coverage
denominator of the evidence-quality sub-score — is
`schemas/scoring.config.json → evidence.material_claims`:

| Side | Material claim keys (the INV-01 set) |
|---|---|
| `buyer` | `company_type`, `product_categories`, `korean_products_signal`, `wholesale_signal`, `partnership_signal`, `sourcing_intent`, `country`, `contact_channels` |
| `seller` | `company_type`, `product_categories`, `oem_odm`, `private_label`, `moq`, `certifications`, `export_markets`, `overseas_partner_signal` |
| `rfq` | `product_category`, `commercial_model`, `destination_country`, `max_moq`, `required_certifications` |

**INV-01, stated plainly.** For every key in that list, the record must either carry **at least one
evidence item whose `claim` equals that key**, or carry the value `"unknown"` (or omit the key).
There is no third possibility. A value with no matching evidence item is a build blocker, not a
warning.

### 3.1 The six that operators get wrong most often

| Claim | What must be evidenced | What is **not** evidence |
|---|---|---|
| **MOQ** (`moq`) | A number or an inclusive range published by the seller, with its unit. `moq_unit` wins over any `unit` inside the range (BUILD-CONTRACT §3.3 R3.3.1). | "Low MOQ", "small orders welcome", "flexible MOQ", a competitor's MOQ, a broker's listing. An **open lower bound** ("from 1,000") is **not** a comparable value: the field is `"unknown"` plus a note. |
| **Certifications** (`certifications`) | The scheme is **named** on a source, and the certificate belongs to *this* company and is not expired at `--as-of`. `certifications_verified: true` requires an official, exhaustive certification page. | A logo strip with no issuer or number; "GMP factory"; "international quality standards"; a certificate held by the seller's contract manufacturer, sister company or a plant the product is not made in; "FDA approved" (cosmetics are not FDA-approved — record a risk note, not a certification). |
| **Export markets** (`export_markets`) | Named countries the company states it exports to or has customers in, as ISO-3166-1 alpha-2. | "Worldwide shipping", "global partners", a flag strip with no text, a trade-show booth in a country, an English-language website. |
| **OEM/ODM capability** (`oem_odm`, `private_label`, `brand_export`) | The company offers the service to third parties, in its own words. These three are **independent**: `oem_odm: true` does **not** imply `private_label: true`, and neither implies `brand_export: true`. | "We manufacture" (a brand manufactures its own goods), "our factory", a factory photo, an ODM trade-association membership on its own. |
| **Buyer intent** (`sourcing_intent`, `partnership_signal`, `wholesale_signal`) | A public, company-controlled invitation to suppliers: a brand-submission or distributor-application page, a dated open call, a wholesale/trade-account page. The classification rule is BUILD-CONTRACT §3.4 — dated public sourcing action ⇒ `high`; standing page ⇒ `medium`; B2B-capable with no invitation ⇒ `low`. | A generic "Contact us" page, a careers page, a "Trade" menu item whose target 404s, the fact that the company obviously buys things, a third party asserting that they are "looking for suppliers". |
| **Exclusivity** | In v0.1.0 there is **no structured exclusivity field on any schema.** It may exist only as an evidence item with a free-text `claim` plus a `notes[]` / `risks[]` entry, and it MUST NOT be asserted anywhere else. | "Official distributor", "authorised partner", "sole importer" in marketing copy. None of these establish contractual exclusivity, and this skill never asserts that a territory is open or taken. |

Two further material-in-practice values that carry no canonical claim key: **capacity**
(`monthly_capacity_units`) and **lead time** (`lead_time_days`). They are not in the INV-01
denominator, but the same rule applies — never derived from floor area, staff count or "fast
delivery".

---

## 4. Source tiers and how tier maps to confidence

### 4.1 The tier model (PRD 10.3)

| `source_tier` | Source | Typical `source_type` | `is_official` |
|---|---|---|---|
| **1** | The company's own site, including its contact / wholesale / export / OEM / partnership pages | `official_site` | `true` |
| **2** | Official trade-show, association, government or trade-agency directory; TradeWith's own records | `official_directory`, `trade_show`, `internal_record` | usually `false` |
| **3** | The company's own LinkedIn or other company-controlled social profile | `social` | `true` |
| **4** | Reputable third-party directory or press release | `third_party` | `false` |
| **5** | Community, blog, forum — supporting signal only | `third_party`, `social` | `false` |

`internal_record` defaults to tier 2 (BUILD-CONTRACT §4.7) and must carry a resolvable internal URL,
`retrieval_method: "internal_api"`, and a `quote_or_summary` naming the fields read. An internal
record never substitutes for public evidence about a third-party company's **public** behaviour.

**`is_official` is about control, not quality** (PRD EVID-02). It is `true` only when the claim was
read on a channel the company itself controls. A distributor listing a brand is **not** official for
that brand, however accurate the listing is. A tier-2 government directory is authoritative and
still not `is_official` for the company.

### 4.2 Tier → score

Tier drives `evidence.source_tier_points` in the evidence-quality sub-score, and the ordering is
monotone: tier 1 > 2 > 3 > 4 > 5. A record whose material claims rest on tier 4/5 sources will score
lower on evidence quality than an identical record sourced from the company's own pages, and that is
the intended discrimination (PRD DISC-06: evidence quality outranks result count). Certification
tokens whose **best** supporting evidence is tier 4 or 5, or that **no** evidence item supports at
all, additionally fire the `certification_claim_unverified` adjustment on `compliance_readiness`
(SCORING-CONTRACT §2.4).

### 4.3 Tier → evidence-item `confidence` (data-entry guidance)

`evidence.confidence` answers one narrow question: *how strongly does **this one source** support
**this one claim**?* It is a judgement, not a formula. Use these bands (SHOULD, not MUST):

| Situation | `confidence` |
|---|---|
| Tier 1, the passage states the value literally and unambiguously | 0.90 – 1.00 |
| Tier 1, the passage is company-written but hedged or ambiguous | 0.60 – 0.85 |
| Tier 2, a structured directory field maintained by the organiser/agency | 0.65 – 0.85 |
| Tier 3, a company social post stating the value | 0.50 – 0.75 |
| Tier 4, a third-party directory entry or press release | 0.30 – 0.60 |
| Tier 5, community or blog mention | 0.10 – 0.30 |
| Any item with `inferred: true` | cap at **0.60**, whatever the tier — a MUST, enforced as `EVI-03` (§4.5) |
| Any item with `stale: true` | reduce by roughly one band |

Only one mechanism reads this number: the conflict-resolution ladder, step (c) in §6 below. It is
**not** an input to the evidence-quality sub-score, and it is **not** the record's confidence.

### 4.4 Item confidence vs record confidence

| | `evidence[].confidence` | record `confidence` |
|---|---|---|
| Question it answers | how strongly this source supports this claim | how much a reviewer should trust this record |
| Who sets it | the agent, at data-entry time | computed by `scripts/_common.py` (SCORING-CONTRACT §0.7) |
| Inputs | judgement about one passage | evidence quality, count of unknown material fields, `record.stale`, unresolved conflicts |
| Rendered as | numeric | `HIGH` / `MEDIUM` / `LOW` bands per `config.output.render_confidence_as`; the stored value stays numeric |

Record confidence **never** reads `qualification_score` or `match_score`. A confident record can be a
bad fit and a shaky record can be a great one; the two axes are independent by design.

### 4.5 What `validate_output.py` enforces on every buyer / seller record

| Rule | Fails when |
|---|---|
| `INV-01` | a material claim's value is not what its evidence says: a scalar no item's value equals (compared after `_common.normalize_unit` / `normalize_country` / `normalize_category` / `normalize_certification`), a list token no item contains, or an item asserting a value while the record field is absent or `"unknown"` — unless a `conflicts[]` entry for that field has `winning_value: "unknown"` (§6.4), or every asserting item is `stale` or below tier 3 (§8 rows 9 and 11: kept as supporting signal, never forcing a value) |
| `INV-22` | a duplicate `evidence_id`, or a `conflicts_with` / `evidence_ids` reference that resolves to no item on the record |
| `INV-37` | `VERIFIED` without a tier ≤ 3 material-claim item that is neither `inferred` nor `stale`; `QUALIFIED`, `MATCH_CANDIDATE` or `READY_FOR_REVIEW` on an unscored record |
| `EVI-01` | `source_tier` 1 without `source_type: official_site` and `is_official: true`, or the reverse; a `third_party` item marked `is_official` |
| `EVI-02` | `source_domain` is not the domain of `source_url`, or an `official_site` item was read on a domain that is not the record's `canonical_domain`, `website` or `alias_domains` |
| `EVI-03` | an `inferred` item above confidence 0.60 (§4.3) |
| `EVI-04` | a scored record's `dimension_scores.evidence_quality` is more than 1 point from `_common.evidence_quality` over its own evidence |
| `EVI-05` | its `confidence` is more than 0.01 from `_common.record_confidence` |
| `EVI-06` | *(warning; fails under `--strict`)* an item older than `stale_threshold_days` that is not `stale`, on a record that is neither `stale` nor `closed` / `unreachable` |
| `VAL-01` | the document kind cannot be detected (pass `--schema`) |

---

## 5. Time, recency and staleness

### 5.1 Two dates that are never the same field

| Field | Meaning | Rule |
|---|---|---|
| `observed_at` | when **we** read the page (RFC3339) | Never the page's own date. Must not be later than the run's `--as-of` end of day (INV-24). |
| `source_date` | when the **source content** was published or last updated | `"unknown"` when the page exposes no date. **Never guess.** Must not be later than its own `observed_at`. |

Korean gloss: `observed_at` = 우리가 본 시각, `source_date` = 그 문서 자체의 작성·갱신일. 둘을 섞지 않는다.

A copyright year in a footer is not a `source_date`. A CMS "last modified" header, a dated press
release, a dated post, a dated exhibitor listing, a dated catalogue revision are.

### 5.2 Age arithmetic

All age arithmetic uses the run's `--as-of` date and nothing else. Reading the wall clock anywhere on
a scoring or rendering path is a build blocker (INV-14). Recency enters the score through
`evidence.recency_buckets` in `scoring.config.json`, whose labels are `fresh`, `current`, `aging`,
`stale`, evaluated in order with the first match winning; a missing `source_date` takes
`evidence.unknown_source_date_multiplier` instead — which sits between `current` and `aging`, so an
undated page is treated as neither fresh nor rotten. The bucket edges live in the config; do not
memorise them and never hardcode them.

### 5.3 The stale flag — triggers (PRD EVID-04, test T07)

Two independent flags exist. Set the narrowest one that is true.

**`evidence[].stale = true`** — this *source* is no longer reliable:

- the URL is unreachable at read time (DNS failure, connection refused, or a 404/410 that means the page is gone) after a retry;
- the content was served from an archive or cache copy rather than the live page;
- the page is explicitly superseded ("2023 catalogue", "previous price list", a redirect to a
  successor page);
- the content is demonstrably outdated on its face — a dated announcement about a past event
  presented as current, a certificate whose stated expiry precedes `--as-of`;
- the domain is parked, for sale, or serving a registrar placeholder.

**`record.stale = true`** — this *entity* looks dead:

- the company site is unreachable across the whole run, not just one page;
- `operational_status` is `closed` or `unreachable`;
- every tier-1 item on the record is itself stale;
- a credible source states closure, merger-out, or deregistration.

### 5.4 What staleness does, and what it must never do

**Does:** fires `evidence.stale_penalty` **once** on the evidence-quality sub-score (never once per
item); applies the stale multiplier to record `confidence`; makes the record render with a visible
caveat. The score-side condition is either `record.stale == true`, or the covered material-claim set
being non-empty with every covered claim's best item older than `evidence.stale_threshold_days`.

**Must never:** delete a field value; rewrite a known value to `"unknown"`; flip a tri-state to
`false`; remove the record from the output. Staleness is a *confidence* signal, not a *truth* signal
(BUILD-CONTRACT §4.4).

**Hard-filter boundary.** `HF-06` rejects `operational_status ∈ {closed, unreachable}`. It does
**not** reject `stale`. A stale-but-alive company stays a candidate with a lower score — this is
exactly what PRD test T07 asserts.

### 5.5 A refused retrieval is neither stale nor unreachable

The commonest real failure is not a dead site. It is a **live site that refuses this retrieval
method**: HTTP 403 or 429, a bot filter, a WAF challenge, a TLS handshake failure, or a certificate
whose names cover only the hosting platform. The host answered. Nothing about the **content's age** or
the **company's existence** was learned, so neither flag applies:

| Observation | Flag | Why |
|---|---|---|
| DNS does not resolve | `operational_status: "unreachable"` | The name itself is gone |
| Page loads, content is years old or archived | `stale: true` | A claim about content age, evidenced by a date |
| **Host answers and refuses this method** (403 / 429 / bot filter / TLS failure) | **neither** — record the failure in `notes[]` | Nothing was learned about age or existence |

**Retrying the same URL with a different permitted method is allowed and is not a bypass.** INV-12
forbids *defeating a control* — signing in, solving a CAPTCHA, paying past a paywall, overriding
`robots.txt`, evading a rate limit, spoofing an identity to get past a bot filter, or driving a
headless browser. It does not require you to accept one tool's failure as the site's answer. A plain
page fetch where a reader-style tool returned 403, or `http://` where the certificate does not cover
the host, are ordinary requests to a public URL. Record which one worked in
`evidence.retrieval_method` (still restricted to `web_search`, `page_fetch`, `sitemap`,
`internal_api`, `manual_entry`) and name the failure in `notes[]`.

Measured 2026-09-13 while working this vertical: one UAE company site returned 403 to a reader-style
fetch and 200 with full readable text to a plain GET of the same URL in the same minute; four Korean
maker sites returned 403 to a reader-style fetch while serving `robots.txt` with `Allow: /` for those
paths, and answered a plain GET with 200; one 카페24-hosted site presented a certificate covering only
the platform's names, so every HTTPS client refused it while `http://` served the page.

If **no** permitted method works, that is an honest dead end: the facts stay `"unknown"`, the note
says which method failed, and if the candidate ends with no evidenced material claim it leaves for
`excluded[]` with the reason naming the retrieval failure (`references/output-format.md`).

### 5.6 The re-check queue

`scripts/stale_evidence.py` lists what to re-read on records that are already stored. It fetches
nothing, changes no record and no score, and no scorer reads its output
(`schemas/recheck-queue.schema.json`). Run it with the re-check date as `--as-of`; it is required,
because falling back to the newest `observed_at` would make every item look as fresh as its last
reading.

Korean gloss: 재확인 대기열 — 저장된 근거 중 다시 읽어야 할 것을 이유와 함께 우선순위로 나열한다.

An evidence item is **current** when it is not flagged `stale` and either its `source_date` falls in a
bucket before `aging`, or it is undated and its `observed_at` does. An undated page is not a stale page
(`evidence.unknown_source_date_note` in `scoring.config.json`): Korean company pages almost never print
a date, and queueing every one of them would bury the records that really are old. The bucket edges
and `stale_threshold_days` come from the `evidence` block, so the queue and the score always agree on
what "old" means.

| Reason (priority order) | Level | Means |
|---|---|---|
| `site_unreachable` | buyer / seller record | `operational_status` is `unreachable`. `closed` is a determined fact for `HF-06`, and `unknown` is not a finding, so neither is listed |
| `record_flagged_stale` | buyer / seller record | `record.stale` is `true` (§5.3) |
| `past_stale_threshold` | item | Dated, more than `stale_threshold_days` old (exactly that many days is still `aging`, as in the scorer), and its claim has no current item |
| `source_flagged_stale` | item | `evidence[].stale` is `true` (§5.3) |
| `unresolved_conflict` | item | `conflicts_with` names at least one evidence id (a lone string counts as one) and the claim has no `conflicts[]` entry (§6) |
| `aging` | item | Dated, in the `aging` bucket or later but within the threshold, and its claim has no current item |
| `undated` | item | No `source_date`, its last reading is itself due (or unreadable), and its claim has no current item |

A covered **material** claim with no current item is reported once more as `no_current_evidence`, with
its evidence ids, the newest dated `source_date` and counts of old, undated and flagged items. An
item whose claim already has a current item gets no age reason: a superseded old page no longer drives
the score, so it is not worth a trip. A resolved conflict is not listed. Record-level reasons never
apply to an RFQ, which has no `stale` flag or `operational_status`. Records rank by their most urgent
reason (a non-material item ranks after every material one), then by the number of material claims
without current evidence, then by the oldest queued item; `--top N` lists only the first N while the
summary still counts all of them. Every queued record must be findable again, so a record whose id is
missing, blank or not a string is refused (exit 1) rather than queued as `unknown`.

Working the queue: re-open each `source_url` with a permitted method (§5.5), write a new evidence item
with a new `observed_at` and whatever `source_date` the page now shows, set or clear the flags of §5.3,
then re-run normalize → dedupe → score. The queue copies only locators and dates, never a value, a
quote or a contact channel. A `source_url` is echoed as stored; if a personal profile URL was stored
against §7 and `references/compliance-notes.md`, fix the record rather than following the link.
**Staleness never deletes a value** (§5.4), and neither does re-checking: a page that no longer says
something makes the field `"unknown"` only through a new evidence reading, never through the queue.

---

## 6. Conflicts — when two sources disagree (PRD test T08)

### 6.1 The rule

When two sources assert different values for the **same claim key**:

1. **Keep both evidence items.** Cross-link them: each item's `conflicts_with` names the other's
   `evidence_id`. Never delete the loser.
2. **Resolve in this order, stopping at the first step that decides:**
   1. the **official** source (`is_official: true`) wins;
   2. else the more recent `source_date` wins (`"unknown"` never beats a real date);
   3. else the higher evidence `confidence` wins;
   4. else the **lower** `source_tier` number wins.
3. **Record the resolution** on the record's `conflicts[]`.
4. **Never silently drop the losing value.**

Korean gloss: 공식 출처 → 더 최신 → 더 높은 확신도 → 더 낮은 tier 순으로 결정하고, 진 값도 반드시 `conflicts[]`에 남긴다.

### 6.2 The required conflict note

A `conflicts[]` entry (shape fixed by `schemas/match-result.schema.json`, inlined identically in the
buyer and seller schemas) carries:

| Key | Content |
|---|---|
| `field` | the claim key in dispute, e.g. `moq` |
| `winning_value` | the value that was written into the record |
| `losing_value` | the value that was not |
| `winning_evidence_ids` | the evidence id(s) supporting the winner |
| `losing_evidence_ids` | the evidence id(s) supporting the loser |
| `resolution` | exactly one of `official_source`, `more_recent`, `higher_confidence`, `manual` |
| `note` | optional free text — the one place the human-readable reason belongs |

**Recorded means resolved.** The presence of a `conflicts[]` entry naming that field *is* the
resolution, and a resolved conflict costs nothing on the evidence-quality sub-score. An evidence item
carrying a non-empty `conflicts_with` for which the record holds **no** matching `conflicts[]` entry
is an **unresolved** conflict and fires `evidence.conflict_penalty`. So the penalty is not for
disagreeing sources — it is for **undocumented** disagreement.

### 6.3 Worked example

The company's own OEM page states a minimum order of 3,000 units, updated 2026-06-30. A third-party
sourcing directory lists 10,000 units, page dated 2024-02-11.

```yaml
evidence:
  - evidence_id: EV-007
    claim: moq
    value: 3000
    source_url: "https://sorimcos.example/oem"
    source_type: official_site
    source_tier: 1
    is_official: true
    observed_at: "2026-09-12T02:14:00Z"
    source_date: "2026-06-30"
    confidence: 0.95
    quote_or_summary: "Minimum order quantity: 3,000 units per SKU."
    conflicts_with: ["EV-021"]
  - evidence_id: EV-021
    claim: moq
    value: 10000
    source_url: "https://example-directory.example/kr/sorim-cosmetics"
    source_type: third_party
    source_tier: 4
    is_official: false
    observed_at: "2026-09-12T02:19:00Z"
    source_date: "2024-02-11"
    confidence: 0.40
    quote_or_summary: "MOQ: 10,000 pcs"
    conflicts_with: ["EV-007"]

moq: 3000                 # the value EV-007 supports; EV-021's 10000 is not written
moq_unit: units
conflicts:
  - field: moq
    winning_value: 3000
    losing_value: 10000
    winning_evidence_ids: ["EV-007"]
    losing_evidence_ids: ["EV-021"]
    resolution: official_source
    note: "Company OEM page (2026-06-30) over third-party directory entry (2024-02-11)."
```

Step 1 of the ladder decides it: `EV-007` is official. Steps 2–4 are never consulted, though here
they would agree. The conflict is recorded, so no `conflict_penalty` applies — but `missing[]` and
the reviewer both still see that a second source disagrees.

### 6.4 When the ladder cannot decide

If both items are official, equally dated, equally confident and the same tier, the ladder is
exhausted. Do **not** pick one at random and do **not** average them. Set the field to `"unknown"`,
keep both items, and record:

```yaml
conflicts:
  - field: export_markets
    winning_value: "unknown"
    losing_value: ["AE", "SA"]
    winning_evidence_ids: []
    losing_evidence_ids: ["EV-031", "EV-032"]
    resolution: manual
    note: "EV-031 lists [AE, SA, KW, QA]; EV-032 lists [AE, SA]. Both official, both dated 2026-05-04, same tier, same confidence. Held as unknown pending human adjudication."
```

No evidence won, so `winning_evidence_ids` is empty — an empty list means "no source backs this
value", which is exactly true here. Both candidate values survive, one in `losing_value` and the
other named in `note`, and the field takes the unknown penalty, which is the honest outcome. Flag it
for the operator in `notes[]`.

---

## 7. Quoting rules

`quote_or_summary` is the load-bearing field of this whole policy: it is what a reviewer reads to
decide whether to believe the record.

**MUST**

- Prefer a **verbatim quote**, kept short (≤ 300 characters is the working ceiling; the schema's hard
  limit is 1000). Copy the source's own words, including its own hedging.
- Otherwise write a **faithful one-sentence summary** that a reader of the source would accept as
  accurate — same scope, same strength, same hedging.
- Keep the quote **about the claim on this evidence item**. One item = one claim = one source = one
  observation time. Never bundle two claims into one item.
- Put a translated passage in the **summary** form, not the quote form, and keep the original phrase
  inside it. A translation is an interpretation step, however small.

**MUST NOT**

- Contain **our** interpretation. Interpretation belongs in the record's `notes[]`, or in
  `rationale[].statement` on a match candidate — never in the quote (BUILD-CONTRACT §4.5).
- **Strengthen** the claim. Removing a hedge is falsification.
- Stitch fragments from two pages, or use an ellipsis that changes the meaning.
- Contain a **named individual**, a personal email, a direct-dial number or a personal social handle.
  Redact to the role: "the export manager", "the brand partnerships team" (R3.6.1, INV-31).

### 7.1 Paraphrase pairs — never strengthen

| Source says | ✗ Paraphrase that strengthens | ✓ Faithful |
|---|---|---|
| "We can discuss OEM projects case by case." | "Offers OEM services." | "States that OEM projects are discussed case by case." |
| "MOQ from 1,000 units." | "MOQ 1,000 units." | "States a minimum order starting at 1,000 units; no upper bound given." |
| "Our factory follows GMP standards." | "ISO 22716 certified." | "States that the factory follows GMP standards; no scheme or certificate named." |
| "We ship worldwide." | "Exports to UAE, Saudi Arabia and Singapore." | "States worldwide shipping; no countries named." |
| "Interested in Korean brands." | "Actively sourcing Korean sunscreen suppliers." | "States interest in Korean brands." |
| "Partnership enquiries welcome." | "Currently recruiting new distributors." | "Invites partnership enquiries via the partnership form." |
| "Established 2015, 3,000 pyeong facility." | "Monthly capacity 150,000 units." | "States a facility size of 3,000 pyeong; no capacity figure given." |

---

## 8. Forbidden inferences

Each of these is a real, recurring failure mode. The left column is a **build blocker**; the right
column is what the same source actually supports.

| # | ✗ Forbidden inference | ✓ Correct handling | Why |
|---|---|---|---|
| 1 | "We manufacture our own products" ⇒ `oem_odm: true` | `oem_odm: "unknown"`; note "own-brand manufacturing stated; third-party OEM service not stated" | Making your own goods is not offering OEM to others (PRD 15.2). |
| 2 | `oem_odm: true` ⇒ `private_label: true` | evaluate each flag against its own evidence | The three commercial-model flags are independent (BUILD-CONTRACT §3.4). |
| 3 | "GMP factory" / "international quality standards" ⇒ `certifications: ["ISO22716"]` | nothing enters `certifications`; the phrase goes in `notes[]` | The scheme must be **named**, not implied (PRD EVID-05). |
| 4 | An ISO 22716 logo image ⇒ `certifications_verified: true` | the logo may back a low-tier `certifications` claim; `certifications_verified` stays `"unknown"` | Verified means an official, exhaustive certification page with issuer or number. |
| 5 | "FDA approved" ⇒ `FDA_REGISTERED` | record a `risks[]` note; no certification token | Cosmetics are not FDA-approved; the phrase signals a compliance misunderstanding, not a registration. |
| 6 | "We ship worldwide" ⇒ `export_markets: ["US","AE","SG"]` | `export_markets: "unknown"` | Shipping capability is not a named export market. |
| 7 | "Small orders welcome" / "low MOQ" ⇒ `moq: 500` | `moq: "unknown"` + note | No number was published; a guessed number defeats `HF-03` entirely. |
| 8 | "MOQ from 1,000" ⇒ `moq: {min: 1000, max: 1000}` | `moq: "unknown"` + note recording the open bound | An open upper bound is not a comparable value (BUILD-CONTRACT §3.3). |
| 9 | Company appears in a "Top 10 K-Beauty importers" blog ⇒ `korean_products_signal: true` | keep the tier-5 item as a supporting signal; the field needs a tier 1–3 source | Community listicles are supporting signal only (PRD 10.3). |
| 10 | A "Contact us" page exists ⇒ `sourcing_intent: high` | `sourcing_intent: "low"` if B2B-capable with no invitation, or `"unknown"` | Contactability is not buying intent (BUILD-CONTRACT §3.4). |
| 11 | A "Trade / Wholesale" menu item whose target 404s ⇒ `wholesale_signal: true` | `wholesale_signal: "unknown"`; set `stale: true` on the item if it was reachable before | A dead link evidences nothing. |
| 12 | "Official distributor of Brand X" ⇒ exclusivity in that territory | free-text evidence item + `risks[]` note; no exclusivity assertion anywhere | Exclusivity is contractual and not observable from marketing copy. |
| 13 | A dated 2023 "looking for suppliers" post ⇒ "they are currently looking" | record the post with its `source_date`; the intent claim is bounded by that date | Live demand requires a live RFQ (PRD test T05, `references/outreach-guidelines.md`). |
| 14 | Company name pattern ⇒ `contact_channels: [{type: corporate_email, value: "sales@..."}]` | only addresses the company itself published | Pattern-guessed addresses are forbidden outright (INV-11, INV-31). |
| 15 | "We bring the best of Korea to you" ⇒ `company_type: importer` | `company_type: "unknown"` unless the company states its role | Marketing copy is not a corporate-role declaration; `"other"` means *classified and none fit*, not *unsure*. |
| 16 | 3,000 pyeong facility ⇒ `monthly_capacity_units: 150000` | `monthly_capacity_units: "unknown"` | Floor area does not convert to units. |
| 17 | A certificate held by the seller's contract manufacturer ⇒ the seller's `certifications` | record it as a note about the partner; not on the seller's `certifications` | Certification scope is not modelled in v0.1.0 (SCORING-CONTRACT §2.5 rule 5). |
| 18 | Nothing found on a page ⇒ `certifications: []` | `certifications` stays **absent** unless the check itself is evidenced | Present-and-empty is a positive claim and needs its own evidence (R3.2.4). |

---

## 9. Before a record leaves `VERIFIED`

Run this checklist on every record you are about to score.

- [ ] Every material claim is either evidence-backed on its own claim key, or `"unknown"` / absent (INV-01).
- [ ] No `"unknown"` was written as `false`, `0`, `""`, `null`, `N/A` or a silently omitted line (INV-02).
- [ ] Every `false` and every `[]` has an evidence item recording what was checked (R3.2.4).
- [ ] Every `inferred: true` item's quote supports the inference **without** added assumptions; the reasoning is in `notes[]`.
- [ ] No `quote_or_summary` contains interpretation, a strengthened reading, or a named individual.
- [ ] Every `observed_at` ≤ the run's `--as-of` end of day; every `source_date` ≤ its own `observed_at` (INV-24).
- [ ] `source_date` is `"unknown"` wherever the page exposed no date — never a guessed or copyright year.
- [ ] Every unreachable, archived or superseded source carries `stale: true`; the record carries `stale: true` only if the **entity** looks dead.
- [ ] Every disagreeing pair is cross-linked via `conflicts_with` **and** resolved in `conflicts[]` with a `resolution` value.
- [ ] At least one evidence item on a material claim comes from a tier ≤ 3 source — the entry condition for `VERIFIED` (BUILD-CONTRACT §9.1, INV-37).
- [ ] No page was reached by bypassing a login, paywall, CAPTCHA or `robots.txt`; `retrieval_method` is one of `web_search`, `page_fetch`, `sitemap`, `internal_api`, `manual_entry` (INV-12, `references/compliance-notes.md`).
- [ ] Every `source_url` is click-ready and re-verifiable by a human without special access (PRD 22).

Validate mechanically before shipping the run:

```
python3 scripts/validate_output.py --input <document>.json --invariants
```

---

## 10. Related pages

| Page | What it owns |
|---|---|
| `references/data-contract.md` | Field names, enums, unknown encoding, normalization and dedupe |
| `references/qualification-rubric.md` | What evidence earns what score, narratively |
| `references/matching-rules.md` | Hard filters, weighted score, rerank, unknown handling |
| `references/outreach-guidelines.md` | How evidence becomes a claim in a draft, and what may never be claimed |
| `references/compliance-notes.md` | Retrieval boundaries, data minimization, jurisdictional caveats |
| `schemas/scoring.config.json` → `evidence` | The evidence-quality sub-score, exactly — every multiplier, bucket edge and penalty (the repo-internal `SCORING-CONTRACT.md` §4 describes the same numbers algorithmically but does not ship) |
