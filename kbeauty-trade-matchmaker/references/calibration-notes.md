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
- [7. Closing the loop — the labelled-set protocol](#7-closing-the-loop--the-labelled-set-protocol)

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

Section 7 is the protocol for building the first of those two labelled sets; until a report produced
by it exists, every sentence above still stands exactly as written.

---

## 7. Closing the loop — the labelled-set protocol

Section 6 says what is missing: a **labelled set**. This section says how to build one, with the two
scripts that ship for exactly this purpose and nothing else.
*Korean gloss: 사람이 채점한 라벨 세트를 만들어 루브릭의 변별력을 실제로 측정하는 절차.*

| | |
|---|---|
| Scripts | `scripts/make_review_sheet.py` (scored run → blind CSV), `scripts/acceptance_report.py` (filled CSVs + scored runs → report) |
| Report shape | `schemas/acceptance-report.schema.json`, field notes in `references/data-contract.md` §9.3 |
| Tunables | `schemas/scoring.config.json` → `calibration` (minimum sample, band edges, sweep range) |
| What it measures | **PRD 17 Human Acceptance Rate only** |
| What it cannot measure | **PRD 17 RFQ Conversion.** That needs outcome data fed back from the application layer after real outreach — who was contacted, who replied, which reply became an RFQ. No document this package produces carries it, and no report here may be read as evidence about it. |

Neither script can change a score. Nothing reads the `calibration` block except these two, so a
report never moves `score_version` (`kbtm-score-0.2.0`) or `schema_version` (`0.1.0`).

### 7.1 Why the sheet is blind

A reviewer who can see the rubric's answer tends to agree with it, and a label that only confirms
the score measures nothing at all. So the default sheet carries **no score, no rank, no `qualified`
flag and no excluded-versus-returned marker**, and its rows are ordered by the sha256 digest of the
record id rather than by rank. *Korean gloss: 점수를 보여주면 사람이 점수에 끌려가므로, 기본 시트는
점수·순위·적격 여부를 숨기고 순서도 섞는다.*

Two consequences worth stating out loud:

- **Excluded records are indistinguishable from returned ones** under `--include-excluded`. That is
  the only way to measure a **false exclusion** — a candidate the rubric threw away that the
  operator would have contacted. A hard filter that is quietly discarding business shows up here
  and nowhere else. Hiding the score does not achieve this on its own: an excluded discovery record
  has no `country` at all (the schema has no such field on it), so the excluded rows would have
  been exactly the rows whose country cell read `unknown`. In blind `--include-excluded` mode the
  sheet therefore writes the literal `not_shown` into any column that would separate the two
  populations, on **every** row — blanking it only on the excluded rows would be the same tell
  inverted — and names those columns on stderr. A column the reviewer cannot see is never a column
  they are not told about. A match sheet keeps its country column as it is: neither population
  carries a country there, so `unknown` separates nothing.
- `--no-blind` exists for auditing one specific ranking, not for building a labelled set. A sheet
  produced with it appends `rank,score,qualified` and keeps rank order; a report built from one is
  an audit record, not evidence about the rubric.

### 7.2 Composing the sample

The sample decides what the report can be used for. A number computed over the wrong population is
worse than no number, because it looks like evidence.

| Rule | Why |
|---|---|
| **≥ 2 countries** | §3.1 found `market_relevance` resolving to **2 distinct values** on a single-country request. One country cannot tell you whether a market dimension works. |
| **≥ 2 product categories** | Same failure mode on `product_fit` / `kbeauty_fit`: one category collapses the dimension that is supposed to separate. |
| **Target 100–200 reviewed records** | The two live trials had 32 and 12. That was enough to prove a mechanism broken and nowhere near enough to fit a coefficient — the whole finding of §1. `calibration.min_sample` (30) is a *floor below which the report refuses to be evidence*, not a target. |
| **One reviewer role per sheet** | So a disagreement between two reviewers shows up as two sheets with two rates, not as one averaged number that hides it. The `reviewer_role` column takes a **role label** ("trade operator", "category buyer") — never a person's name, and never an address or a number (INV-31; the report refuses a sheet whose `note` or `reviewer_role` carries either). |
| **Review the whole returned list, not the top slice** | Reviewing only the top 10 makes every rate a rate about high scorers, and leaves the threshold sweep with nothing below the cut-off to measure. |
| **`unsure` is a real answer** | It is counted, reported separately, and excluded from both sides of the acceptance rate. A reviewer forced to guess produces a label that measures the forcing, not the company. |

### 7.3 The commands

```bash
# 1. score the run as usual
python3 scripts/score_buyer.py --input tmp.buyers.deduped.json --query query-surface.json \
    --as-of 2026-09-12 --pretty --output buyers.ae.scored.json

# 2. cut a blind review sheet, excluded records included
python3 scripts/make_review_sheet.py --input buyers.ae.scored.json --as-of 2026-09-12 \
    --include-excluded --output reviews.ae.csv

# 3. the operator fills verdict / reason_code / note / reviewer_role / reviewed_on and returns it.
#    Repeat 1-3 per country and per category until the sample rules of 7.2 are met.

# 4. join every sheet back to every run and measure
python3 scripts/acceptance_report.py \
    --scored buyers.ae.scored.json --scored buyers.gb.scored.json \
    --reviews reviews.ae.csv --reviews reviews.gb.csv \
    --as-of 2026-09-20 --pretty --output acceptance.2026-09-20.json

python3 scripts/validate_output.py --input acceptance.2026-09-20.json \
    --schema acceptance-report --invariants --strict
```

`--min-sample N` overrides the configured floor for one run. Lower it to look at a small sample; do
not lower it to make a decision, which is what `insufficient_sample` exists to stop.

The report **refuses** rather than repairing, with exit 1 and one `ERROR:` line, on: an unknown
verdict or reason code; a `reject` with no reason code; a duplicate `record_id` whose rows disagree
on the verdict, or agree on the verdict and disagree on the reason code; a review row that joins to
no scored record; a malformed or impossible `reviewed_on`, or one later than `--as-of`; a `note` or
`reviewer_role` carrying an email address or a phone-number-like string; **one `record_id` reaching
the report from two `--scored` documents** — the same run passed twice doubles every count and
defeats `min_sample`; two `score_version`s in one report (the INV-23 principle — scores from two
rubrics are not on one scale); a discovery run mixed with a match run, or a buyer population mixed
with a seller one; and scored documents carrying no record at all. Silently dropping or silently
doubling a row would change the denominator of the very rate the decision rests on. On a schema
failure the report writes nothing at all, to stdout or to `--output`.

What it does **not** refuse: a trailing all-empty CSV row (a spreadsheet's blank line, skipped), a
partially filled sheet (an empty verdict is simply not reviewed), and a run that returned nothing —
a `no_match` match run reports every rate as `null` and measures false exclusions only, which on
such a run is the one thing worth measuring.

The personal-data scan is tuned to pass the notes this protocol asks for. An operator recording a
CDSCO registration certificate number, a BPOM notification number, an ISO certificate number, a
registry URL or a trading period writes a longer digit run than any telephone number, so a bare
number is not a refusal: a telephone needs a telephone **signal** — a leading `+`, a trunk-prefix
`0` on nine digits or more, or a `tel` / `phone` / `mobile` / `전화`-style label in front. Email
detection stays broad and also catches `name [at] company.example`.

### 7.4 How to read each block

| Block | The question it answers | How it misleads if read carelessly |
|---|---|---|
| `summary.human_acceptance_rate` | Of the candidates an operator *decided on*, what share did they accept? | `unsure` is excluded from both sides. A run with many `unsure`s has a rate over a small population — read `reviewed`, `accept`, `reject` and `unsure` together, and `review_coverage` beside them. |
| `summary.insufficient_sample` | May this report justify a change? | `true` means **no**, whatever the rates look like. The accompanying `notes[]` line says so in words. |
| `by_qualified` | Is the `qualified` flag worth anything? `qualified_true.acceptance_rate` is its precision; `qualified_false.acceptance_rate` is what it is throwing away; `qualified_unknown` is what it never judged. | Two rates close together mean the flag is not separating, even if both are high. A non-zero `qualified_unknown` is a defect in the scored run, not a finding about the rubric — read it before the other two, because those records are in neither. |
| `by_score_band` | Does acceptance rise with the score, monotonically? | A band with `n: 0` reports `null`, not 0. Bands with two or three records are noise. |
| `threshold_sweep` | What would a different cut-off have returned? `precision` is acceptance among the returned records at or above it; `recall` is the share of **every** accepted record it keeps, an accepted record the rubric *excluded* included — that record sits at or above no threshold, so it is a lead every cut-off loses. | Precision always improves as the threshold rises; the question is what recall it costs. Read the pair. And read `notes[]`: on a **match** report, rows below the run's own threshold are not measurable, because `score_match.py` keeps only candidates at or above it in `results[]`. A discovery report has no such caveat — `score_buyer.py` / `score_seller.py` return their below-threshold records and only flag them unqualified. |
| `discrimination.overall.auc` | Does a higher score actually mean a better counterparty? 0.5 is "this number tells the reviewer nothing"; 1.0 is perfect separation. | An AUC over a handful of pairs moves a long way on one label. `n_accept` and `n_reject` are printed beside it for that reason. |
| `discrimination.by_dimension[].distinct_values` | Can this dimension separate anything at all? | This is §3's finding made visible. A dimension at 1–2 distinct values cannot discriminate **whatever its weight** and **whatever its AUC** — an AUC computed over two values is a coin flip dressed up. Read this column before the AUC beside it. |
| `by_country` | Does the rubric work outside the market it was written against? | One country's rate is the single-country failure of §3.1 all over again. |
| `by_reason_code` | *Why* are candidates being rejected? | This is the most actionable block: `wrong_vertical` concentrated in one country points at a query family, `evidence_wrong` at the evidence policy, `wrong_company_type` at `B-CR1`. |
| `false_exclusions` | Which hard filter or evidence bar is discarding business? | A non-empty list is a defect report, not a statistic. One entry is worth investigating; it does not need a sample size. |

### 7.5 What each Phase-4 entry needs before it can be decided

The §4 agenda is deferred for lack of measurement. This is the measurement each entry needs, from
the blocks above, over a sample that satisfies §7.2 and does **not** carry `insufficient_sample`.

| Id | Decide it when the report shows | Decide the other way when |
|---|---|---|
| **P4-1** (buyer weights: `sourcing_intent` 25→18, `b2b_commercial_role` 20→25, `reachability` 10→7, `kbeauty_korea_fit` 20→25) | `discrimination.by_dimension` gives `sourcing_intent` an AUC at or near 0.5 with a low `distinct_values`, while `b2b_role` and `kbeauty_fit` carry the separation — the weight is on the dimensions that do not discriminate | `sourcing_intent` separates once the population spans several countries. Its collapse may have been an artefact of one market's publishing habits. |
| **P4-2** (seller weights: `compliance_readiness` 15→10, `evidence_quality` 10→15) | Among *sellers*, `evidence_quality` out-AUCs `compliance_fit`, and `by_reason_code` shows few `evidence_wrong` rejections among well-evidenced makers with unpublished terms | `compliance_fit` predicts acceptance and the rejected makers are the ones without a baseline certification — then the current weighting is right and §3's observation was about the population, not the rubric |
| **P4-3** (threshold mode `fixed 70` → `percentile`) | `threshold_sweep` shows precision materially better at a cut-off other than 70 **on more than one population**, and `by_score_band` shows the run's scores bunched rather than spread | The sweep is flat around 70, or the best cut-off differs per country — a percentile mode would then move the bar for reasons unrelated to quality |
| **P4-4** (K-Beauty fit: weight or gate?) | `by_qualified` shows the gate's precision clearly above `qualified_false`'s, and `false_exclusions` / low-scoring accepts show the gate is not costing real leads | Accepted records keep landing in `qualified_false` because the gate fired — the gate is then over-reaching and the question returns to weighting |
| **P4-5** (price a consumer-facing chain with a central buying function) | Accepted records cluster in the `by_score_band` rows *below* the threshold and carry the retail-chain shape — i.e. the rubric is ranking real buyers too low | Those records are rejected too; the 12th-of-20 ranking was then correct and the +20 adjustment already shipped is enough |
| **P4-6** (let verification reach `compliance_readiness` in a bare discovery run) | On seller discovery runs naming **no** required certification, `compliance_fit` shows 1–2 `distinct_values` and near-0.5 AUC — the dimension is inert exactly as predicted | It already separates, meaning the +5 adjustment path is doing the work |
| **P4-7** (measure the matching and outreach rubrics at all) | Needs **match-result reviews**: run steps 7.3 over `score_match.py` output, one sheet per RFQ. `--scored` accepts a `match-result` and reports per-component AUC over the six match components. Outreach has no scored document at all and so no report here can reach it | — |

Two standing cautions. First, a report answers "does the rubric agree with this operator", not "is
this company good": a reviewer's `reject` is itself a judgement, which is why one reviewer role per
sheet and several sheets beat one averaged number. Second, **P4-1 … P4-6 all change a weight or a
threshold, so deciding any of them is a `score_version` MINOR bump** (BUILD-CONTRACT 12.2) and every
stored score goes stale by definition (12.3). The report is the evidence for that bump; it is not
the bump.
