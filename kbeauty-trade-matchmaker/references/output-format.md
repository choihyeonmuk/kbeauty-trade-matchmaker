# references/output-format.md — output rendering contract

Korean gloss: 출력 렌더링 계약 — 모든 결과 블록의 토큰 규칙.

This is the shipped, authoritative rendering contract for every block this skill prints: 10.1 buyer
discovery, 10.2 seller discovery, 10.3 match, 10.4 the outreach envelope, 10.5 the shared line rules,
and the optional Korean label map. `SKILL.md` shows the two PRD-facing skeletons; the token rules
that make them exact live here. It is a condensed restatement of section 10 of the repo-internal
build contract, shipped inside the package so an installed skill can read it without `docs/`
(the same arrangement as `references/data-contract.md`).

Section numbers below are the build contract's own, so a review comment naming "10.5" lands on the
same text in both places.

---

## 10. Output rendering contract

One rendering definition shared by `SKILL.md`, `templates/*.md` and `tests/`. Tokens are `{{dotted.path}}`; everything outside `{{...}}` is literal, including capitalization, punctuation and indentation.

**Global layout rules**

- Section headings (`Summary`, `Top Candidates`, `Matches`, `Excluded`) sit on their own line with no prefix and no trailing colon.
- Exactly **one** blank line between blocks; none inside a candidate block.
- Candidate detail lines are indented **exactly three spaces**.
- The candidate header separator is **U+2014 EM DASH** surrounded by single spaces (` — `), as printed in PRD 12.1/12.2.
- No trailing whitespace; the document ends with a single newline.

### 10.1 Buyer Discovery output (PRD 12.1)

```
Summary
- Query: {{query.country_name}} / {{query.vertical}} / {{query.categories_joined}}
- Candidates found: {{summary.candidates_found}}
- Qualified (>={{summary.threshold_used}}): {{summary.qualified_count}}
- As of: {{as_of}} · Score version: {{score_version}}

Top Candidates
{{rank}}. {{company_name}} — {{qualification_score}}/100 — {{confidence_band}}{{unverified_marker}}
   Website: {{website_display}}
   Country: {{country_display}}
   Type: {{company_type_display}}
   Why: {{why_joined}}
   Contact: {{primary_contact_display}}
   Evidence: {{evidence_urls_joined}}
   Missing: {{missing_joined}}

Excluded / low fit
- {{company_name}}: {{exclusion_reason}}

Recommended next action: {{next_action}}
```

Token rules:

| Token | Rule |
|---|---|
| `query.country_name` | Display name; `All countries` when absent |
| `query.vertical` | `K-Beauty` unless overridden |
| `query.categories_joined` | Category slugs rendered with `_`→space, joined `, `; `all categories` when absent |
| `summary.threshold_used` | The **resolved** numeric threshold (never the literal `70` unless that is what was used) |
| `rank` | 1-based, contiguous |
| `qualification_score` | Integer, no decimals |
| `confidence_band` | `HIGH` / `MEDIUM` / `LOW` (7.5 `confidence_band`) |
| `unverified_marker` | The literal ` — unverified` appended to the header line when **no** evidence item on a material claim has `is_official == true`; omitted otherwise (PRD 15.1 criterion 3). This candidate is **scored, ranked and rendered** — it is a marker, not an exclusion. A candidate with *no* evidenced material claim at all never reaches this block; it is in `Excluded / low fit` with the sub-case named |
| `website_display` | The canonical absolute `website`; the literal `unknown` when `canonical_domain == "unknown"` (PRD 15.1 criterion 2) |
| `country_display` | `country_name` when present, else the alpha-2 `country`; the literal `unknown` when unknown (PRD 15.1 criterion 2) |
| `company_type_display` | `company_type` title-cased with `_`→space; append ` / Wholesaler` when `wholesale_signal == true` and the type is not already `wholesaler`; `Unknown` when unknown |
| `why_joined` | **≥ 2** reasons joined by ` + `, max 4, each ≤ 60 chars, each traceable to a scored signal or an evidenced fact (10.5) |
| `primary_contact_display` | The highest-ranked channel per the `B-RE1` ordering, rendered as `{{type_display}}` (plus ` — {{value}}` when the value is a URL or role email); `unknown` when `contact_channels` is absent; `none published` when it is verified-empty |
| `evidence_urls_joined` | Up to 3 URLs as `[{{url}}]` joined by `, `, then ` (+{{n}} more)`; `none` when there is no evidence |
| `missing_joined` | `missing[]` joined by `; `; the literal `none` when empty |
| `exclusion_reason` | One line, from `excluded[].reason_summary`. A **hard-requirement violation** (R6.2.6) renders `failed_rules[0].reason` prefixed with its `rule_id`, e.g. `HF-03 MOQ minimum 5,000 exceeds requested 3,000`. A **query-fit or evidence-bar** exclusion renders the fit reason alone, e.g. `no K-Beauty signal (T09)`, `no evidenced material claim (no source could be opened)` or `no evidenced material claim (evidence covers no material claim)`. The evidence-bar reason always names its sub-case: "we could not open the site" and "we read the site and nothing material was on it" are different answers for a reviewer, and neither is the `unverified` state below |
| `next_action` | Default `Draft outreach to top {{n}}` where `n = min(5, qualified_count)`; `Widen the query — no qualified candidates` when `qualified_count == 0`; `Widen the query — {{n}} of {{N}} requested candidates met the evidence bar` when `summary.returned < summary.top_n_requested` (R7.8.1, PRD DISC-06) |

The `Excluded / low fit` block is **omitted entirely** (heading included) when there is nothing to list. Every other block is always present.

### 10.2 Seller Discovery output

```
Summary
- Query: {{query.categories_joined}} / {{query.commercial_model_display}} / MOQ <= {{query.max_moq_display}} / {{query.required_certifications_joined}}
- Candidates found: {{summary.candidates_found}}
- Qualified (>={{summary.threshold_used}}): {{summary.qualified_count}}
- As of: {{as_of}} · Score version: {{score_version}}

Top Candidates
{{rank}}. {{company_name}} — {{qualification_score}}/100 — {{confidence_band}}{{unverified_marker}}
   Website: {{website_display}}
   Country: {{country_display}}
   Type: {{company_type_display}}
   Why: {{why_joined}}
   MOQ: {{moq_display}}
   Certifications: {{certifications_joined}}
   Export markets: {{export_markets_joined}}
   Contact: {{primary_contact_display}}
   Evidence: {{evidence_urls_joined}}
   Missing: {{missing_joined}}

Excluded / low fit
- {{company_name}}: {{exclusion_reason}}

Recommended next action: {{next_action}}
```

| Token | Rule |
|---|---|
| `query.commercial_model_display` | `OEM/ODM`, `Private Label`, `Branded`, or `Any commercial model` |
| `query.max_moq_display` | `format_quantity` output, or `no ceiling` |
| `query.required_certifications_joined` | Tokens joined by `, `; `no certification requirement` when empty |
| `company_type_display` | `Manufacturer`, `Brand`, `OEM/ODM`, `Distributor`, `Other`, `Unknown`; append ` / OEM-ODM` when `oem_odm == true` and the type is not already `oem_odm` |
| `moq_display` | `format_quantity(moq, moq_unit)`; `unknown` when unknown |
| `certifications_joined` | Canonical tokens joined by `, `; `unknown` when absent; `none evidenced` when verified-empty; append ` (unverified list)` when `certifications_verified != true` |
| `export_markets_joined` | Alpha-2 codes joined by `, `; `unknown` / `none evidenced` per 3.2 |

`unverified_marker`, `website_display`, `country_display`, `exclusion_reason` and `next_action` follow the 10.1 rules unchanged.

### 10.3 Match output (PRD 12.2)

```
RFQ #{{rfq_id}}
Destination: {{rfq.destination_country_name}}
Product: {{rfq.product_category_display}}
MOQ: <= {{rfq.max_moq_display}}
Commercial model: {{rfq.commercial_model_display}}
{{required_cert}}: Required
Threshold: {{threshold.mode}} {{threshold.value}} · As of: {{as_of}} · Score version: {{score_version}}

Matches
{{rank}}. {{seller_name}} — Match {{match_score}}/100
   Product Fit: {{component_scores.product_fit}}
   Model Fit: {{component_scores.model_fit}}
   MOQ: {{component_scores.operation_fit}}
   Compliance: {{component_scores.compliance_fit}}
   Market Fit: {{component_scores.market_fit}}
   Evidence Quality: {{component_scores.evidence_quality}}
   Type: {{company_type_display}}
   Why: {{why_joined}}
   Contact: {{primary_contact_display}}
   Risks: {{risks_joined}}
   Missing: {{missing_joined}}
   Evidence: {{evidence_urls_joined}}

Excluded
- {{seller_name}}: {{reason_summary}}

Summary: {{summary.candidates_considered}} considered · {{summary.passed_hard_filter}} passed hard filter · {{summary.returned}} returned · {{summary.qualified_count}} qualified
```

| Token | Rule |
|---|---|
| `company_type_display` | From `match_candidate.company_type` (+ `match_candidate.oem_odm`), rendered by the **seller-side** rule of 10.2: `Manufacturer`, `Brand`, `OEM/ODM`, `Distributor`, `Other`, `Unknown`, with ` / OEM-ODM` appended when `oem_odm == true` and the type is not already `oem_odm`. The buyer-side title-case rule of 10.1 is never applied to a seller enum. |
| `primary_contact_display` | From `match_candidate.contact_channels[]`, highest-ranked channel per the `B-RE1` ordering, rendered by the 10.1 rule |
| `missing_joined` | `match_candidate.missing[]` joined by `; `; the literal `none` when empty |
| `risks_joined` | `match_candidate.risks[].statement` joined by `; `, high severity first; the literal `none` when empty |

- One `{{required_cert}}: Required` line **per** entry of `rfq.required_certifications`, in the RFQ's own order. The block is omitted when the list is empty.
- The six component lines use exactly the PRD 12.2 labels; the label↔component mapping is fixed in 6.1 (note `MOQ` renders `operation_fit`).
- `Why:` carries **≥ 2** reasons drawn from `rationale[].statement` (shortened to ≤ 60 chars each, joined by ` + `). A candidate that cannot produce two evidenced reasons MUST NOT be rendered as a match (PRD 15.3, INV-20).
- `Risks:` joins `risks[].statement` by `; `, high severity first; the literal `none` when empty.
- `base_score` and the rerank are not rendered by default; when `rerank.applied` is true, append ` (rerank {{+/-delta}}: {{rationale}})` as a fourth detail line labelled `Rerank:`.
- When `no_match.is_no_match` is true, the `Matches` block is replaced by:

```
No qualified match
Reason: {{no_match.reason}}
Binding rules: {{no_match.binding_rule_ids_joined}}
Try:
- {{relaxation.statement}}
```

  The `Excluded` block and the final `Summary:` line are still rendered. An empty result set is **never** rendered as an empty `Matches` heading (PRD test T10).

### 10.4 Outreach draft envelope

Used by `templates/buyer_outreach.md` and `templates/seller_outreach.md`, and by any outreach the agent renders inline.

```
Outreach Draft — {{side}} — {{company_name}}
Status: READY_FOR_REVIEW
Auto-send: false
Approval: required (human)
Recipient channel: {{channel_type}} — {{channel_value}}
Language: {{language}}
Subject: {{subject}}

Body
{{body}}

Personalization facts
- {{fact}} — [{{source_url}}] (observed {{observed_date}})

Compliance checks
- Jurisdiction: {{country_name}} — direct-marketing review: {{required|not_required|unknown}}
- Advertising label / opt-out required: {{yes|no|unknown}}
- Personal data used: none (company-level channel only)
- Claims verified against evidence: {{yes|blocked}}

Reviewer checklist
- [ ] Every personalization fact traces to a cited source
- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited
- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open
- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced
- [ ] Contact channel is an official company channel
- [ ] Jurisdiction rules confirmed before sending

Next action: human review, then APPROVED_FOR_OUTREACH in the application layer
```

Hard rules:

- **R10.4.1** `Status: READY_FOR_REVIEW` and `Auto-send: false` are literal and MUST be the 2nd and 3rd lines. (INV-09)
- **R10.4.2** Every sentence of `{{body}}` that asserts a fact about the recipient MUST appear in `Personalization facts` with a source URL. Unsourced personalization is a blocker, not a style note (PRD OUT-02, 15.4).
- **R10.4.3** A claim of **live buyer demand** — "a buyer is currently looking for", "we have a buyer for", "there is active demand for", and equivalents in any language — is permitted **only** when a cited `rfq_id` resolves to an RFQ whose `status` is one of `qualified`, `matching` or `proposal_open`, **and** the draft states that status and the RFQ's `as_of` date. An RFQ in `draft`, `matched` or `closed` counts as **no RFQ** for this rule: a draft RFQ is not yet a buying request and a matched/closed one no longer is. Otherwise the phrases are **forbidden** (PRD test T05), and the RFQ-absent template variant offers to collect requirements instead.
- **R10.4.4** The CTA is a sourcing request / proposal submission, never "sign up" (PRD OUT-03).
- **R10.4.5** `{{side}}` ∈ `Buyer` | `Seller`. `{{language}}` is the recipient's business language (e.g. `English`, `Korean`); the envelope labels stay English.
- **R10.4.6** When `Advertising label / opt-out required` is `yes` or `unknown`, the draft MUST append the matching block from `templates/legal_notices.md` **verbatim** under a `Required legal notices` heading — or, when no block exists for that jurisdiction/channel, the literal line `- No notice block on file for {{country_name}} / {{channel_type}} — obtain wording before sending`. Blocks are keyed `{{country_alpha2}}.{{channel_type}}` (PRD OUT-06). The outreach templates never inline legal wording of their own, and this skill never concludes that a notice is sufficient.

### 10.5 Shared line rules

| Concern | Rule |
|---|---|
| Score | `{{n}}/100`, integer, no padding |
| Percent-like components | bare integer (`Product Fit: 100`) |
| Numbers ≥ 1,000 | thousands separator `,` (`3,000`) |
| Ranges | ASCII hyphen, no spaces (`1,000-5,000 units`) |
| Unknown | the literal lowercase `unknown` — never `N/A`, `-`, `null`, blank, `false` or `0` (INV-02) |
| Verified-empty | `none evidenced` (lists) / `none published` (contact channels) — distinct from `unknown` |
| Empty `Missing` | the literal `none` |
| URLs | wrapped in square brackets, unshortened, click-ready, max 3 + `(+n more)` |
| Dates | `YYYY-MM-DD` |
| Reason text | ≤ 60 chars per reason, no trailing period, lower-case start unless a proper noun |
| Truncation | Company names are **never** truncated |
| Ordering | Candidates follow the producing script's sort (score desc, `canonical_domain` asc); rendering never re-sorts |

**Required per-candidate lines (INV-19).** Every rendered candidate in 10.1, 10.2 and 10.3 MUST show: the **score**, the **type**, a **`Why:`** line with **≥ 2** reasons, a **contact channel**, an **`Evidence:`** line, and a **`Missing:`** line. Every **discovery** candidate (10.1, 10.2) MUST additionally show a **`Website:`** line and a **`Country:`** line, because PRD 15.1 criterion 2 requires the company website and country on each of the top 10. None of these lines may be omitted, even when the value is `unknown` / `none`. The match block (10.3) keeps the six: a `match_candidate` carries `website` as display carry-over but no country, and PRD 12.2's block does not ask for one.

**Korean labels.** When the operator writes in Korean or asks for Korean output, render **every** label in the block in Korean using this fixed map — never a mix of Korean and English labels in one block. The JSON documents never change.

- Summary and headings: `Summary`→`요약`, `Query`→`조회 조건`, `Candidates found`→`발견 후보`, `Qualified`→`적격`, `As of`→`기준일`, `Score version`→`점수 버전`, `Top Candidates`→`상위 후보`, `Excluded / low fit`→`제외 / 적합도 낮음`, `Recommended next action`→`다음 권장 조치`, `Matches`→`매칭 결과`, `Excluded`→`제외`, `No qualified match`→`적격 매칭 없음`, `Reason`→`사유`, `Binding rules`→`걸린 규칙`, `Try`→`시도해 볼 것`.
- Candidate lines: `Website`→`웹사이트`, `Country`→`국가`, `Type`→`유형`, `Why`→`근거`, `MOQ`→`MOQ`, `Certifications`→`인증`, `Export markets`→`수출 시장`, `Contact`→`연락 채널`, `Evidence`→`출처`, `Missing`→`확인 필요`, `Risks`→`위험`, `Rerank`→`재정렬`, `Match`→`매칭`.
- RFQ header and components: `Destination`→`목적국`, `Product`→`제품`, `Commercial model`→`거래 방식`, `Required`→`필수`, `Threshold`→`기준 점수`, `Product Fit`→`제품 적합도`, `Model Fit`→`거래 방식 적합도`, `Compliance`→`규제 대응`, `Market Fit`→`시장 적합도`, `Evidence Quality`→`근거 품질`. The 10.3 closing line renders as `요약: {{considered}}곳 검토 · {{passed}}곳 하드 필터 통과 · {{returned}}곳 반환 · {{qualified}}곳 적격`.
- Fixed phrases: `Draft outreach to top {{n}}`→`상위 {{n}}곳 아웃리치 초안 작성`, `Widen the query — no qualified candidates`→`조회 조건 넓히기 — 적격 후보 없음`, `Widen the query — {{n}} of {{N}} requested candidates met the evidence bar`→`조회 조건 넓히기 — 요청한 {{N}}곳 중 {{n}}곳만 근거 기준 충족`, `none`→`없음`, `unknown`→`미상`, `none published`→`공개된 채널 없음`, `none evidenced`→`근거 없음`, `(unverified list)`→`(검증 안 된 목록)`, ` — unverified`→` — 미검증`, `All countries`→`전체 국가`, `all categories`→`전체 카테고리`, `no ceiling`→`상한 없음`.
- Type values: `Distributor`→`유통사`, `Importer`→`수입사`, `Wholesaler`→`도매상`, `Retailer`→`소매업체`, `Brand`→`브랜드`, `Marketplace`→`마켓플레이스`, `Manufacturer`→`제조사`, `Other`→`기타`, `Unknown`→`미상`; `OEM/ODM` stays.
- `Why` reasons, `Risks` statements and `missing[]` labels are translated faithfully, one item for one item, never adding, dropping or strengthening an item. Company names, URLs, email addresses, rule ids (`HF-03`), certification tokens, country codes and `HIGH` / `MEDIUM` / `LOW` stay exactly as they are.
