# Calibration Notes — what the live trials actually measured

The honest field report behind `schemas/scoring.config.json`. Read it before you trust a score.
*Korean gloss: 실측 결과와 보정 한계 — 이 루브릭이 아직 검증되지 않은 이유.*

| | |
|---|---|
| Applies to | every number in `schemas/scoring.config.json` and every claim in `references/qualification-rubric.md` |
| Trials | two live public-web runs, both dated **2026-09-13**, at `score_version kbtm-score-0.1.0` |
| Modes exercised | Mode 1 (Buyer Discovery), Mode 2 (Seller Discovery) — **not** Mode 3 or Mode 4 |
| Companion pages | `references/qualification-rubric.md`, `references/buyer-discovery.md`, `references/seller-discovery.md` |

> **Standing warning.** This rubric is calibrated against **synthetic fixtures**, not a scored
> real-world sample. The two live runs showed where it fails to *discriminate*; neither showed whether
> a high score predicts a good counterparty, because no candidate was labelled by a human or converted
> into an RFQ. Scores are **reproducible and traceable**, not yet known to be **predictive** (§6).

Company names, domains and per-company judgements are deliberately absent from this page and from
the whole package — the trials touched real businesses, and the fixtures under `tests/fixtures/` are
fictional companies on `.example` domains for the same reason. Everything below is aggregate.

---

## Table of contents

- [1. What was measured](#1-what-was-measured)
- [2. PRD acceptance criteria — met and not met](#2-prd-acceptance-criteria--met-and-not-met)
- [3. Why the rubric barely discriminated on live data](#3-why-the-rubric-barely-discriminated-on-live-data)
- [4. What was deliberately not changed — the Phase-4 agenda](#4-what-was-deliberately-not-changed--the-phase-4-agenda)
- [5. Discovery-playbook findings](#5-discovery-playbook-findings)
- [6. The standing warning, stated plainly](#6-the-standing-warning-stated-plainly)

---

## 1. What was measured

Two agents ran the shipped playbooks against the live public web on **2026-09-13** — no fixture, no
cache, no internal database.

| | Trial A — Buyer Discovery | Trial B — Seller Discovery |
|---|---|---|
| Request | one Gulf country, category `sunscreen`, count 20 | product `sunscreen`, `oem_odm: true`, `max_moq: 3000`, required certification `ISO22716` |
| Unique companies surfaced | **34** | **32 names / 30 unique** |
| Records carrying ≥ 1 tier-1 official-source evidence item | **32** | **12** |
| Records scored | 32 (20 returned in the ranked block) | 11 (1 removed by hard filter `HF-01`) |
| Qualified at the default fixed threshold of **70** | **7** | **5** |
| Observed score range | 56–84 with a query surface, 31–69 without one | 41–79 with a query surface, 43–63 without one |
| Page fetches | roughly 40 for all 34 candidates | comparable, one to three pages per company |

**The sample is small.** Thirty-two buyer records and twelve seller records, one country and one
product category each, one day. Enough to prove a mechanism is broken — a criterion that fires for
0 of 20 records is broken at any sample size — and nowhere near enough to fit a point value. Every
change made in response is a **repair of a mechanism**, never a re-fit of a coefficient.

---

## 2. PRD acceptance criteria — met and not met

### PRD 15.1 — Buyer Discovery

| # | Criterion | Verdict | Reason |
|---|---|---|---|
| 1 | ≥ 20 unique company candidates | **Met** | 34 unique companies, 32 of them with tier-1 evidence, 20 returned |
| 2 | Top 10 each carry website, country, type, qualification score, evidence URL, confidence | **Met, with one defect since fixed** | Every field rendered. One candidate's `Website:` line printed the deep page it was first found on rather than the company site root; `scripts/normalize_company.py` now trims a website to its root and records a note, and the dedupe merge prefers the shallowest website in the group |
| 3 | A candidate with no official-source evidence is marked with a low evidence score or `unverified` | **NOT EXERCISED** | The run produced no candidate of that shape. The only two candidates that could have been one failed the **TLS handshake** from the run machine, so no page could be read at all; with zero evidenced material claims they were routed to `excluded[]` by the `DISC-06` evidence bar and rendered as exclusions, never as unverified candidates. The two states are now distinguished explicitly in `evidence.no_evidence_note`, but the *unverified* path has still never run against live data |
| 4 | Same company's www / non-www and local-language domains deduplicate to one entity | **Met** | The www / non-www merge behaved as specified. A separate defect was found from the other side: the registrable-domain function collapsed companies sharing a **hosting platform** into the platform domain, which would have silently merged unrelated companies. The suffix list now covers the common hosted-site platforms and a normalisation fixture case covers it |

### PRD 15.2 — Seller Discovery

| # | Criterion | Verdict | Reason |
|---|---|---|---|
| 1 | Unconfirmable conditions stay `unknown` | **Met in substance, violated in spelling** | The run kept every unconfirmable term unknown and invented nothing. It wrote the literal string `"unknown"` into array fields exactly as the playbook then instructed, which the schema rejects — 15 validation errors. The playbook now states the spelling per field: quantities and tri-states take the literal `"unknown"`, arrays are **absent** |
| 2 | A seller violating a required condition is not ranked in the top results | **NOT MET as run** | The open-lower-bound rule ("from 5,000 units") sent every such MOQ to `"unknown"`, which disarmed `HF-03` against the way real makers publish MOQ: 4 of 4 companies that published any MOQ used an open lower bound, and one whose stated floor was above the requested ceiling was kept and ranked instead of rejected. §7 now splits the case — a floor **at or below** the ceiling stays unknown because it is genuinely uncomparable, a floor **above** the ceiling is stored as a scalar and `HF-03` rejects on it |
| 3 | Certification names are never inferred without evidence | **Met by the agent, at risk in code** | No certification was invented in the run. But the shared certification normaliser folded a bare marketing token into the Korean regulatory designation, so a marketing phrase would have laundered into a scored certificate. The bare token now maps to a distinct recordable token that earns no baseline-quality points and cannot satisfy the hard-filter superset test |
| — | Count of candidates (15.1's ≥ 20 bar, applied to the seller side for comparison; PRD 15.2 sets none) | **Names yes, evidence no** | 30 unique names cleared 20, but only **12** cleared the tier-1 evidence bar and 11 were scored. Returning fewer because the evidence bar was not met is the specified outcome, not a failure — but it is the honest number |

**Modes 3 and 4 were never run against live data:** no RFQ match and no outreach draft was produced,
so nothing here supports the matching rubric or the outreach validators.

---

## 3. Why the rubric barely discriminated on live data

This is the core finding: on both sides the heaviest dimensions produced the fewest distinct values
and the ranking fell to light dimensions that happened to vary. Spread across the 20 buyer records:

| Dimension | Weight | Distinct values | Note |
|---|---|---|---|
| `kbeauty_korea_fit` | 20 | **2** (70 ×16, 31 ×4) | the defining dimension of a K-Beauty skill |
| `sourcing_intent` | 25 | **3** (31 ×15, 48 ×3, 73 ×2), stdev 13.2 | the heaviest dimension |
| `market_relevance` | 15 | **2** | single-country request |
| `reachability` | 10 | **11**, range 30–100, stdev 19.0 | decided the top-10 order |

On the seller side, `operational_fit` was 31 for 9 of 11 records, and the five records at or above
the threshold were **exactly** the five holding a verifiable baseline quality certification. Passing
70 meant one thing only.

### 3.1 `kbeauty_korea_fit` resolved to two values across 20 real companies

**Mechanism.** `B-KF1` aggregates by `max` and priced a bare boolean identically to a named
portfolio, so a company naming 27 Korean brands and one naming none both scored 55. `B-KF2`
(30 points) fired for **0 of 20** records — all four signals were agent-authored claim keys
documented on a page the Mode 1 load path never opens — and it scored 0 when `product_categories`
was **known** against a 9-point unknown neutral when it was absent, so knowing less scored higher.

**Changed.** `B-KF1`'s ladder is now spaced by evidential strength — named 3+ brands 55, named 1–2
brands 48, a first-party sourcing statement 45, a bare `korean_products_signal: true` 40, explicit
false 0. `B-KF2` gained a **field-derived** fallback, `korean_carriage_with_beauty_categories` (20),
reachable from record fields alone, with its unknown state re-keyed to the inputs it can score.

**Not changed.** The aggregation stays `max` — summing would let a boolean plus a marketing mention
outrank an evidenced named portfolio — and the weight stays 20, fixed by PRD 6.3.

### 3.2 `sourcing_intent` (weight 25) was near-constant

**Mechanism.** `B-SI1` held 55 of the dimension's 100 points and reads a **self-declared** field real
importers almost never publish — **15 of 20** companies carried no value — so the heaviest dimension
sat at its unknown neutral for three quarters of the population. `B-SI3` needs a machine-readable
date that standing supplier-facing pages do not print: unknown for **18 of 20** records, and on the
`Missing:` line of every top candidate.

**Changed.** Ten points moved from `B-SI1` (55 → 45) to `B-SI2` (35 → 45), every value rescaled by
the same factor so ordering, spacing and the all-unknown arithmetic are unchanged. `B-SI2` — the one
criterion in the dimension decided by pages an agent can observe — gained `published_trade_terms`
(15) as both a schema enum value and a signal. `B-SI3` gained `standing_sourcing_page_undated` (5),
decided by the item's `observed_at` (bounded by `as_of`, so it can never reach past the run), priced
below a dated within-365-day signal (6) and above the criterion's unknown neutral (3). The buyer
schema gained `buyer_moq` / `buyer_moq_unit` / `buyer_moq_currency` and `B-CR2` gained
`published_minimum_order` (25): five of the 32 buyer records published a concrete minimum order and
every one survived only as free text.

**Not changed.** The dimension weight stays 25 — see [P4-1](#4-what-was-deliberately-not-changed--the-phase-4-agenda).

### 3.3 The unknown-neutral floor let a zero-K-Beauty candidate qualify

**Mechanism.** `unknown_points = 0.30 × criterion max_points`, so a record with
`korean_products_signal` **and** `product_categories` both unknown — no Korean or K-Beauty reference
on any page read — floors `kbeauty_fit` at 31 instead of gating, and the `T09` `non_beauty_business`
(−60) adjustment fires only on a **present-and-empty** category list, making "never checked" cheaper
than "checked and found nothing". That record scored 70, qualified, and outranked seven companies
with evidenced named Korean portfolios, one carrying 150+ brands.

**Changed.** A `korean_products_signal_unknown` (−20) adjustment prices the gap and a
`vertical_fit_gate` closes it: on a K-Beauty query a record with no positive `B-KF1` / `B-KF2` signal
is still scored, ranked and returned with its full breakdown, but `qualified` is forced to false with
a note naming the gate.

**Not changed.** The unknown parameters — `neutral_base` 50, `penalty_factor` 0.6, the derived 0.30
multiplier — are fixed by PRD 6.4.4 and test T04, and `never_hard_reject_on_unknown` stands.
Re-weighting was unavailable too: at 20 of 100, `kbeauty_fit` floored at 0 still leaves an otherwise
maximal record at 80, so the gate acts on the **qualified flag**, where the PRD leaves it open.

### 3.4 The unknown flag fired on the entire Korean seller population

**Mechanism.** The limit was 2, and this vertical's baseline is that Korean makers do not publish
commercial terms. Unknown/absent rates over the 12 tier-1-verified companies: `lead_time_days`,
`monthly_capacity_units`, `regulatory_registrations` and `excluded_markets` 100%, `moq` 92%,
`private_label`, `certifications_verified` and `export_markets` 83%, `overseas_partner_signal` 75%.
The flag fired on **11 of 11** records with a query surface and 12 of 12 without, so the
"확인 필요 / needs verification" marker separated nobody.

**Changed.** Per-entity limits — buyer 2, seller 4, match 4 — with the global value as fallback, and
`flag_behaviour` now requires the note to **name** the unknown claims, from the labels of the entries
that triggered it, instead of asserting a generic evidence gap.

**Not changed.** The buyer limit stays 2 — that population showed no such baseline, and relaxing one
entity's bar is no reason to relax the other's. The flag still never auto-rejects.

### 3.5 The evidence recency buckets are near-dead against Korean company sites

**Mechanism.** `recency_buckets` key on `evidence.source_date`, and the five page types the seller
playbook opens essentially never print a machine-readable date. **1 of 12** records carried any dated
evidence; the other 11 records' **68** evidence items all took `source_date: "unknown"` and the flat
0.85 multiplier, scaling `source_strength` — half of evidence quality — identically for everyone.

**Changed.** `unknown_source_date_multiplier` 0.85 → **0.92**, equal to the `current` bucket, so an
undated item sits below a verifiably fresh one (1.00) and above a verifiably aging (0.80) or stale
(0.60) one. An `evidence.source_date_fallback` block now permits a date to be **read** from the HTTP
`Last-Modified` header, the URL's `<lastmod>` in `sitemap.xml`, or the page's own metadata, in that
order, and forbids a footer copyright year, another page's date, a notice-board entry carried onto an
undated page, and the wall clock.

**Not changed.** The four buckets and their multipliers: correct when a date exists, and the run
produced far too few dated items to re-fit them.

---

## 4. What was deliberately not changed — the Phase-4 agenda

PRD 6.3 **fixes** the six dimension weights — buyer 20/20/25/15/10/10, seller 25/20/20/15/10/10 —
and PRD 6.4.2 fixes the match weights 30/20/15/15/10/10; PRD 18 defers recalibration to **roadmap
Phase 4**, and the threshold is **PRD Open Question 3**. Each entry below carries its field evidence
so Phase 4 starts from measurement, not memory.

| Id | Proposal | Field evidence behind it | Why it is deferred |
|---|---|---|---|
| **P4-1** | Buyer weights: `sourcing_intent` 25 → 18, `b2b_commercial_role` 20 → 25, `reachability` 10 → 7, `kbeauty_korea_fit` 20 → 25 | The two dimensions carrying 45 of the 100 weight produced **5 distinct values between them** across 20 companies; the 10-weight dimension produced 11 and decided the top-10 order | PRD 6.3 fixes the six weights |
| **P4-2** | Seller weights: `compliance_readiness` 15 → 10, `evidence_quality` 10 → 15, so a well-evidenced maker with unpublished commercial terms can clear the bar on product fit, commercial model and evidence alone | `operational_fit` was 31 for 9 of 11 records; the five records at or above 70 were exactly the five holding a verifiable baseline quality certification | PRD 6.3 fixes the six weights |
| **P4-3** | Threshold mode `fixed 70` → `percentile` (75, floor 50, minimum population 8) | An 11-record population scored 41–79 and the threshold resolved to a single binary predicate. The machinery is already implemented and that population already met the minimum: it is a one-token edit | PRD Open Question 3 leaves the mode undecided and PRD 12.1 prints "Qualified (≥70)", so the shipped default is a product decision, not a defect. **Re-measure on a fresh population before flipping it** |
| **P4-4** | Decide whether K-Beauty fit belongs in a weight or in a gate | A record with zero K-Beauty evidence scored 70, qualified, and outranked seven companies with evidenced named Korean portfolios | Closing it by weight is unavailable (§3.3), so v0.1.0 ships the qualified-flag gate. Whether that is the right instrument is a product question |
| **P4-5** | Price a consumer-facing chain with a real central buying function | The one buyer-run company with a dated, signed public commitment to onboard K-Beauty brands earned the run's highest `sourcing_intent` (83) and `market_relevance` (100) and still ranked **12th of 20**, because a 20-weight company-type table and a 10-weight contact-channel table outvoted the 25-weight dimension that had scored it correctly | Shipped as an adjustment (`retail_chain_procurement` +20, with `retail_only_consumer_store` −25 kept as its counterweight). The weight question stays open |
| **P4-6** | Let verification reach `compliance_readiness` in a discovery run that names no required certification | `S-CP1` carries `inapplicable_when: query.required_certifications_absent`, so the verified / unverified split never applies in a bare discovery run and verification reaches the dimension only through a +5 adjustment the 100 ceiling usually clamps away | Closing it means making `S-CP4` verification-aware — a scorer change as well as a table change |
| **P4-7** | Measure the matching and outreach rubrics at all | Neither trial exercised Mode 3 or Mode 4 | The six match weights, the ±5 rerank band and the outreach validators carry **no field evidence whatsoever** — not even the weak evidence the discovery rubrics now carry |

Everything repairable without touching a PRD-fixed weight was repaired inside the point tables,
signal vocabularies, unknown parameters and evidence multipliers; section 3 is that list.

---

## 5. Discovery-playbook findings

Reported by query family and source category; no company is named here or anywhere in the package.

### 5.1 Buyer side — which query families produced real importers

| Query family | Live result | What `references/buyer-discovery.md` now does |
|---|---|---|
| Role × category seeds (distributor, wholesaler, importer, brand distributor, wholesale partnership, private-label sourcing) | Produced the bulk of the 34 candidates | Kept as seed rows 1–6, run first |
| Published-B2B-term rows (`"MOQ"` / `"minimum order"`; trading-company legal-form rows) | **High yield** — surfaced trading SMEs no role query reached | Promoted to run immediately after the seeds and labelled high yield |
| Local-commercial-language rows | Surfaced candidates no English row found, including the local form of "authorised distributor" | Kept as a per-region language pack, with the local form of the agency row named explicitly |
| Explicit-intent phrases ("become a distributor", "now accepting new brands") | **Zero on-vertical results.** The first returned firms from unrelated industries and two encyclopaedia articles; the second returned no exact-phrase hits at all, only consumer landing pages and lifestyle editorial | Demoted to optional rows run last, with stop condition S2 logged rather than ground on |
| Expo / free-zone / chamber directory harvest | **Zero candidates.** The regional show's exhibitor search returns HTTP 200 with a large body and **no exhibitor record in the HTML**; the only company names present were sponsor perfume houses | Row 16 now requires verifying retrievability first, prefers a published exhibitor PDF, press release or post-show report, logs S2 when neither exists, and forbids citing a directory whose text was never read |

Two further defects, both now fixed: the per-candidate **page-path list matched none of the 32
sites** — the site root, the one page it omitted, supplied `company_type`, `country`,
`wholesale_signal`, `korean_products_signal` and `contact_channels` for **24 of 32** companies, so
the root is now row 0 and real paths come from the site's own navigation; and the query-surface table
and the worked trace disagreed on whether a single-country request expands to a region, a **12-point,
9-rank swing** on one record — §2 is now normative and the trace is an illustration of it.

### 5.2 Seller side — Korean rows beat English rows

- **Korean-language rows outperformed English rows.** Three Korean rows returned real factories with
  Korean-language sun-care lines on the first page. The English rows returned mostly **sourcing
  intermediaries and content-marketing pages** — the population the tier rules then discard, after the
  budget is spent. The playbook now runs Korean-first and gives those rows most of the budget.
- **Expo / exhibitor-directory rows yielded no retrievable listing.** One show's exhibitor directory
  rendered no exhibitor names and routed to a login hub; another showed only booth application forms
  with zero company names. That row contributed **0 candidates**. It now runs last, only against a
  directory verified readable, with S2 logged otherwise — and the playbook marks the personal-care
  **ingredients** show as the wrong population for a finished-goods OEM/ODM query.
- **Government and trade-agency registries are the reliable tier-2 route.** Both the national
  drug-safety agency's company lookup and the trade agency's seller directory are public and
  login-free. Two corrections went in: the trade-agency listing level shows **no MOQ, certifications
  or export markets**, contrary to the earlier "sometimes MOQ" claim, and the functional-cosmetics
  review lookup indexes the **marketing-authorisation holder**, not the factory, so it cannot by
  itself establish manufacturer status.

---

## 6. The standing warning, stated plainly

**The scoring rubric is calibrated against synthetic fixtures. It has never been validated against a
scored real-world sample.**

They established where criteria fail to fire, where a dimension collapses to one or two values,
where a field the schema cannot hold throws away real evidence, and where a playbook instruction
cannot be followed against live pages — **mechanism** findings, worth what they cost at any sample
size. They did **not** establish, and no run of this package has yet established:

- that a candidate scoring 84 is a better counterparty than one scoring 70;
- that the fixed threshold of 70 separates leads worth contacting from leads worth skipping;
- that the six dimension weights reflect what actually predicts a trade;
- that the match components, the rerank band or the outreach validators do anything useful at all.

**What would be needed to claim otherwise.** PRD 17 names both metrics; both need a **labelled set**
this package does not have:

| Metric (PRD 17) | Definition | What it would take |
|---|---|---|
| **Human Acceptance Rate** | the share of returned candidates an operator marks valid | A run's full ranked output reviewed company by company by a trade operator, with accept / reject recorded against the score the rubric gave — on a population large enough and drawn from more than one country and category |
| **RFQ Conversion** | the share of contacted buyers that convert to an RFQ | Outcomes fed back from the application layer after real outreach, joined to the score and dimension breakdown that produced the lead |

Until both exist, treat every score as **an audit trail, not a verdict**: it says what evidence was
found, where it came from and how it was weighed, reproducibly from the same inputs on the same
`as_of` date. It does not say the company is a good one. Report it that way, and read the `Missing:`
line before the score.
