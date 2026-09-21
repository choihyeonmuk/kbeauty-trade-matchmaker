# Seller Outreach Draft Template

| | |
|---|---|
| File | `templates/seller_outreach.md` |
| Side | `Seller` — a Korean manufacturer, OEM/ODM, brand or exporting distributor |
| Envelope | `references/output-format.md` 10.4, reproduced literally (capitalization, punctuation, order) |
| Package | `kbeauty-trade-matchmaker` · `skill_version 0.5.0` |
| Korean gloss | 한국 셀러 대상 아웃리치 **초안** 템플릿. 초안 생성까지가 범위이며 자동 발송은 금지된다. |

**Header note (BUILD-CONTRACT.md 1.4).** The 10.4 envelope is reproduced line for line. This template adds exactly two things the envelope permits but does not print: the `Required legal notices` heading mandated by R10.4.6, and the three-line INV-09 trailer. It adds no other line and removes none.

---

## 0. Non-negotiables (read before drafting)

Korean gloss: 아래 항목은 예외 없이 적용된다.

1. **Draft only.** Every rendered draft ends at `READY_FOR_REVIEW` with `auto_send: false` and `manual_approval_required: true`. This package contains no transport of any kind (PRD 11.2, INV-09, INV-10).
2. **No implied buyer when there is no RFQ.** This is the single highest-risk failure on the seller side (PRD test T05, PRD Open Question 6). Without a cited `rfq_id` whose `status` is `qualified`, `matching` or `proposal_open`, the draft must not state, hint, or arrange its sentences so as to suggest that a buyer is waiting. Section 5.1 gives the honest alternative and section 8 lists the exact forbidden phrases in English and Korean.
3. **Every fact about the recipient is sourced.** Each asserting sentence maps to one `Personalization facts` line with a click-ready URL and an observation date (PRD OUT-02, R10.4.2, INV-34).
4. **Trade terms are never estimated.** MOQ, lead time, capacity, certifications, regulatory registrations, export markets and exclusivity are stated only from evidence; otherwise the literal `unknown` (PRD 11.1, EVID-05, INV-02).
5. **Company level only.** No named individual, personal address, direct line or personal handle anywhere, greeting included. Greet a role — `수출팀`, `해외영업팀`, `export team` (BUILD-CONTRACT.md 3.6, R3.6.1, INV-31).
6. **CTA is a proposal or capability submission**, never "sign up" (PRD OUT-03). An account is a mechanism mentioned at most once, never the ask.
7. **No legal determination.** Compliance flags record what must be checked; this skill never concludes that sending is lawful (PRD 11, BUILD-CONTRACT.md 14.6).

---

## 1. How to render

1. Choose the variant in section 5 by what you actually hold: nothing but public facts (S-V1), a cited public sourcing posting by a third party (S-V2), a live TradeWith RFQ (S-V3), or a prior approved draft (S-V4).
2. Pick the contact channel — the highest-ranked published company channel (`partnership_form` > `corporate_email` > `form` > `contact_page` > `linkedin` > `phone` > `other`). Korean manufacturer sites usually publish a `contact_page` and a role address such as `export@` or `overseas@`; use what is published, never an address built from a person's name.
3. Fill every `{{token}}`. A token you cannot fill from evidence deletes its sentence — it never becomes a guess or a hedge.
4. Each personalizing clause carries one `[[ev: EV-nnn]]` marker. Every marker becomes one `Personalization facts` line, and **no marker may survive into the rendered body**.
5. Set `Language`. Korean recipients get a Korean body; the envelope labels stay English (R10.4.5). A translated body carries the same markers, the same facts and the same review.
6. Fill `Compliance checks` from `references/compliance-notes.md` §4.1 and `templates/legal_notices.md`. For a Korean recipient the jurisdiction is `KR` and the notice key is `KR.{{channel_type}}`. On `corporate_email` the Korea row already resolves both flags — `direct-marketing review: required` and `Advertising label / opt-out required: yes` (the `KR.corporate_email` block is `status: required`). A flag stays `unknown` only where those pages leave it unresolved, e.g. `KR.partnership_form`.
7. Keep the body at or under 180 words. The capability block in section 4 is a list, not prose.
8. Hand the draft to a human. Nothing here advances the state machine past `READY_FOR_REVIEW` (INV-09, INV-37).

---

## 2. Envelope tokens (structural, not personalizing)

| Token | Renders | Rule |
|---|---|---|
| `{{seller.company_name}}` | the company name exactly as published, Korean or English | never truncated (10.5) |
| `{{channel_type}}` | one `contact_channels[].type` value | company-level types only (3.6) |
| `{{channel_value}}` | the channel URL or role address | as published; never pattern-guessed |
| `{{language}}` | `Korean` or `English` | envelope labels stay English (R10.4.5) |
| `{{subject}}` | the subject line | ≤ 60 characters, specific, no exclamation mark, no ALL CAPS, no emoji |
| `{{team_label}}` | a role, e.g. `수출팀`, `해외영업팀`, `export team` | role only, never a person (INV-31) |
| `{{sender_org}}`, `{{sender_role_label}}`, `{{sender_reply_channel}}` | our own identity and reply channel | ours, so no evidence binding applies |
| `{{compliance.direct_marketing_review}}` | `required` / `not_required` / `unknown` | one of those three literals |
| `{{compliance.ad_label_required}}` | `yes` / `no` / `unknown` | drives section 6 |
| `{{compliance.claims_verified}}` | `yes` / `blocked` | `blocked` means the draft must not leave review |
| `{{legal_notice_block}}` | section 6 output | verbatim block or the literal fallback line |

---

## 3. Personalization tokens and their evidence binding

Each row states the canonical `evidence.claim` key (BUILD-CONTRACT.md 4.6) that must back the token, the weakest acceptable source, and the unknown path. Korean gloss: 각 개인화 문장은 아래 claim key의 evidence 없이는 쓸 수 없다.

| Token | Renders | Required `evidence.claim` | Weakest acceptable source | If unknown |
|---|---|---|---|---|
| `{{seller.company_name}}` | the company name | `company_name` | tier 1 official site | block the draft |
| `{{seller.product_focus_statement}}` | the categories and forms they make | `product_categories`, `product_forms` | tier 1 official | delete the clause |
| `{{seller.oem_statement}}` | OEM/ODM capability | `oem_odm` | tier 1 official | render `unknown` inside the capability block; never assert it |
| `{{seller.private_label_statement}}` | private-label capability | `private_label` | tier 1 official | same |
| `{{seller.moq_statement}}` | MOQ with its unit, e.g. `3,000 units` | `moq` (with `moq_unit`) | tier 1 official | render `unknown`; a lower bound alone ("from 1,000") is not a comparable value (3.3) |
| `{{seller.lead_time_statement}}` | lead time in days, worst case | `lead_time_days` | tier 1 official | render `unknown` |
| `{{seller.capacity_statement}}` | monthly capacity | `monthly_capacity_units` | tier 1 official | render `unknown` |
| `{{seller.certifications_statement}}` | canonical certification tokens | `certifications` (+ `certifications_verified`) | tier ≤ 2 | `unknown` when absent, `none evidenced` when verified-empty; append ` (unverified list)` when `certifications_verified != true` (10.2) |
| `{{seller.registrations_statement}}` | regulatory registrations by market | `regulatory_registrations` | tier ≤ 2 | render `unknown` |
| `{{seller.export_markets_statement}}` | alpha-2 markets already exported to | `export_markets` | tier ≤ 2 | `unknown` / `none evidenced` (10.5) |
| `{{seller.english_site_statement}}` | English-language site or catalogue | `english_site` | tier 1 official | delete the clause |
| `{{seller.overseas_partner_statement}}` | an evidenced overseas partnership or expo presence | `overseas_partner_signal` | tier ≤ 2 | delete the clause |
| `{{seller.recorded_profile_block}}` | the transparency block of section 4 | one evidence item per line | per line, as above | print the literal `unknown` — do not omit the line |
| `{{public_call.*}}` | a third party's dated public sourcing posting (S-V2 only) | free-text claim + the posting URL | tier ≤ 2, dated, reachable | variant S-V2 is unavailable |
| `{{rfq.*}}` | a live TradeWith RFQ (S-V3 only) | `internal_record` (4.7) with a resolvable internal URL | `internal_api` | variant S-V3 is unavailable |

**Binding rule.** One marker per clause, one `Personalization facts` line per marker, one source URL per line. The transparency block of section 4 is the one place where `unknown` is printed rather than deleted, because the ask *is* to fill it.

---

## 4. The template

Render the block below literally. Lines 2 and 3 are fixed by R10.4.1 and may not move.

```text
Outreach Draft — Seller — {{seller.company_name}}
Status: READY_FOR_REVIEW
Auto-send: false
Approval: required (human)
Recipient channel: {{channel_type}} — {{channel_value}}
Language: {{language}}
Subject: {{subject}}

Body
{{greeting}} {{seller.company_name}} {{team_label}},

{{opening_fact_sentence}} [[ev: {{ev.opening}}]]
{{who_we_are_sentence}}

{{no_rfq_disclosure_sentence}}

{{recorded_profile_intro}}
{{seller.recorded_profile_block}}

{{value_sentence}}
{{cta_sentence}}
{{exit_sentence}}

{{sign_off}}
{{sender_org}} — {{sender_role_label}}
{{sender_reply_channel}}

Personalization facts
- {{fact}} — [{{source_url}}] (observed {{observed_date}})

Compliance checks
- Jurisdiction: {{seller.country_name}} — direct-marketing review: {{compliance.direct_marketing_review}}
- Advertising label / opt-out required: {{compliance.ad_label_required}}
- Personal data used: none (company-level channel only)
- Claims verified against evidence: {{compliance.claims_verified}}

Reviewer checklist
- [ ] Every personalization fact traces to a cited source
- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited
- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open
- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced
- [ ] Contact channel is an official company channel
- [ ] Jurisdiction rules confirmed before sending
- [ ] Without an RFQ, the no-RFQ disclosure sentence is present and unhedged
- [ ] No named individual anywhere in the draft, greeting included
- [ ] No [[ev: ...]] marker survived into the body
- [ ] Subject is 60 characters or fewer, specific, and free of urgency wording

Required legal notices
{{legal_notice_block}}

Next action: human review, then APPROVED_FOR_OUTREACH in the application layer

status: READY_FOR_REVIEW
auto_send: false
manual_approval_required: true
```

**The transparency block.** `{{seller.recorded_profile_block}}` prints what we already recorded from public pages, each line with its source, and prints `unknown` where we have nothing. It is the honest core of a no-RFQ mail: it shows our work, it lets the company correct us, and the fields we cannot fill are exactly the fields a buyer will ask for.

```text
- Categories: {{seller.categories_joined}} — [{{url}}]
- OEM/ODM: {{seller.oem_display}} — [{{url}}]
- Private label: {{seller.private_label_display}} — [{{url}}]
- MOQ: {{seller.moq_display}} — [{{url}}]
- Lead time: {{seller.lead_time_display}} — [{{url}}]
- Certifications: {{seller.certifications_display}} — [{{url}}]
- Export markets: {{seller.export_markets_display}} — [{{url}}]
```

Display rules follow 10.5: numbers ≥ 1,000 carry a thousands separator, ranges use an ASCII hyphen with no spaces (`1,000-5,000 units`), an undetermined value is the literal lowercase `unknown`, and a checked-and-empty list is `none evidenced`. A line whose value is `unknown` carries no URL.

**The no-RFQ disclosure sentence** is mandatory in S-V1 and S-V2 and must not be softened, buried or moved below the CTA:

```text
EN: We do not hold a buying request for {{category}} on your behalf today; this mail is an invitation to be listed and comparable when one arrives.
KO: 현재 귀사 제품에 대한 구매 요청(RFQ)은 보유하고 있지 않습니다. 이 메일은 향후 요청이 접수될 때 비교 가능한 형태로 등록해 두시길 제안드리는 것입니다.
```

---

## 5. Variants

### 5.1 S-V1 — First touch, no RFQ (PRD Open Question 6)

**Use when** the seller came out of discovery and no buying request exists. This is the default seller mail.
**Precondition** ≥ 1 personalization fact from a tier 1 official page, plus the transparency block.

**The honest value proposition.** Without an RFQ the offer is not demand; it is *comparability*. State only these, and only the ones that are true of your deployment:

1. A structured capability profile — categories, forms, OEM/ODM, private label, MOQ and unit, lead time, monthly capacity, certifications and their verification status, regulatory registrations, export markets — so incoming buying requests can be matched on facts rather than on a sales call.
2. A correction opportunity: we already recorded the fields above from public pages and we show the source for each. Anything wrong or out of date can be corrected in one reply.
3. Notification when a matching buying request is posted — include this sentence **only** if the operator actually runs that notification; otherwise delete it.
4. What it costs and what it does not commit them to — state it plainly, once.

Subject patterns (≤ 60 characters):
- KO `{{category}} 수출 프로필 등록 안내` · EN `Listing your {{category}} export profile`
- KO `공개 정보 기반 {{category}} 수출 프로필 확인 요청` · EN `Confirm your {{category}} export profile`

Body skeleton (Korean):

```text
안녕하세요, {{seller.company_name}} {{team_label}}님.

{{seller.product_focus_statement}} [[ev: {{ev.product}}]]
저희는 {{sender_org}}, K-Beauty 해외 B2B 소싱을 중개하는 팀입니다.

현재 귀사 제품에 대한 구매 요청(RFQ)은 보유하고 있지 않습니다. 이 메일은 향후 요청이 접수될 때 비교 가능한 형태로 등록해 두시길 제안드리는 것입니다.

공개된 페이지에서 아래와 같이 확인했습니다. 사실과 다르거나 최신이 아닌 항목은 회신으로 정정해 주십시오.
{{seller.recorded_profile_block}}

해외 바이어는 MOQ, 인증, 수출 가능 국가, 리드타임을 기준으로 후보를 좁힙니다. 위 항목이 unknown으로 남아 있으면 비교 대상에서 빠지기 쉽습니다.
{{cta_sentence}}
관심이 없으시면 "관심 없음"이라고 회신해 주십시오. 이후로는 연락드리지 않겠습니다.

감사합니다.
```

CTA options (choose one; all are submissions, not signups):
- KO `수출용 라인카드 또는 위 항목이 채워진 회신을 보내주시면 프로필에 반영하겠습니다.`
- EN `Reply with your export line card, or with the fields above filled in, and we will record it.`

Exit sentence (`{{exit_sentence}}`) — a close the recipient can act on, never "ignore this mail":
- KO `관심이 없으시면 "관심 없음"이라고 회신해 주십시오. 이후로는 연락드리지 않겠습니다.`
- EN `If this is not relevant, reply "no" and we will not contact you again.`

"무시하셔도 됩니다" / "ignore this email" is not an opt-out (`references/outreach-guidelines.md` §7.4), and a flat "추가 연락은 드리지 않습니다" would contradict the S-V4 follow-up. The jurisdiction's own 수신거부 wording still comes only from the `Required legal notices` block (section 6).

Forbidden here: any sentence that a buyer, an order, a quantity or a market opportunity is waiting; any urgency built from scarcity, deadlines or "지금이 적기" wording.

### 5.2 S-V2 — No RFQ yet, cited public sourcing posting

**Use when** S-V1 holds **and** a third party has published a dated, reachable, public sourcing call in the seller's category — a distributor's open brand-submission page, a trade-show buyer programme, a public tender.
**Precondition** one evidence item, tier ≤ 2, with the posting's own date and a URL that resolves. No posting, no variant.

This variant still carries the no-RFQ disclosure sentence. The posting belongs to someone else and we do not represent them, so the draft must say so:

```text
EN: {{public_call.publisher}} published an open {{category}} sourcing call dated {{public_call.date}}. [[ev: {{ev.public_call}}]]
    We are not representing {{public_call.publisher}} and have no request from them; the posting is public and you can apply directly.
KO: {{public_call.publisher}}가 {{public_call.date}}자로 {{category}} 공개 소싱 공고를 게시했습니다. [[ev: {{ev.public_call}}]]
    저희는 해당 회사를 대리하지 않으며 별도의 요청도 받지 않았습니다. 공고는 공개되어 있으므로 직접 지원하실 수 있습니다.
```

Subject patterns: KO `{{market}} {{category}} 공개 소싱 공고` · EN `Public {{market}} {{category}} sourcing call`.

Forbidden here: describing the posting as ours, as exclusive, as expiring sooner than it says, or as evidence of general demand.

### 5.3 S-V3 — RFQ in hand

**Use when** a TradeWith RFQ exists whose `status` is `qualified`, `matching` or `proposal_open`, and this seller passed its hard filters. An RFQ in `draft`, `matched` or `closed` counts as **no RFQ** (R10.4.3) — use S-V1 instead.

This is the only variant where a demand statement is permitted, and it must carry the id, the status and the `as_of` date:

```text
EN: A buying request is open in TradeWith: RFQ #{{rfq.id}} ({{rfq.product_category_display}}, {{rfq.destination_country_name}}), status {{rfq.status}} as of {{rfq.as_of}}. [[ev: {{ev.rfq}}]]
    Requirements: quantity {{rfq.quantity_display}}, MOQ ceiling {{rfq.max_moq_display}}, {{rfq.required_certifications_joined}}, commercial model {{rfq.commercial_model_display}}.
KO: TradeWith에 공개된 구매 요청이 있습니다. RFQ #{{rfq.id}} ({{rfq.product_category_display}}, {{rfq.destination_country_name}}), 상태 {{rfq.status}}, 기준일 {{rfq.as_of}}. [[ev: {{ev.rfq}}]]
    요구 조건: 수량 {{rfq.quantity_display}}, MOQ 상한 {{rfq.max_moq_display}}, {{rfq.required_certifications_joined}}, 거래 형태 {{rfq.commercial_model_display}}.
```

Subject patterns: `RFQ #{{rfq.id}} {{rfq.destination_country_name}} {{rfq.product_category_display}}` (≤ 60 characters).

CTA: submit a proposal carrying unit price at the stated quantity, MOQ, lead time and certification documents, by a stated date. Say what the buyer's identity disclosure rules are; never imply the buyer has chosen them, and never name the buyer unless the RFQ's own terms allow it.

Rules: state the gap honestly. If the seller's record shows an unknown certification or an MOQ above the ceiling, the mail asks for it rather than assuming it, and the reviewer sees the same gap in `Personalization facts`.

### 5.4 S-V4 — Follow-up

**Use when** a prior draft for this company was approved and sent, no reply arrived, and the cadence rule holds.
**Cadence** at most one follow-up per company per 30 days, and never after a negative reply, an opt-out or a channel complaint. A second follow-up requires a new dated, evidenced reason — a proposal deadline that was already stated, or a new RFQ.

Subject: KO `RFQ #{{rfq.id}} 제안 마감 {{rfq.deadline}}` · EN `RFQ #{{rfq.id}} — proposal window closes {{rfq.deadline}}`, or for a no-RFQ follow-up KO `{{category}} 수출 프로필 등록 재안내` · EN `Following up: {{category}} export profile`.

Rules:
- Add no fact that lacks its own evidence line, and repeat the no-RFQ disclosure whenever there is still no RFQ.
- Never escalate: no "마지막 기회", no "곧 마감", no deadline we did not already state and cite.
- Carry the full envelope — a follow-up is a complete draft, not a fragment.
- Offer an explicit close: KO `회신이 필요 없으시면 "관심 없음"으로 회신해 주시면 더 이상 연락드리지 않겠습니다.` · EN `Reply "no" and we will not follow up again.`

---

## 6. Required legal notices and the opt-out slot (PRD OUT-06)

When `Advertising label / opt-out required` is `yes` or `unknown`, `{{legal_notice_block}}` resolves in exactly one of two ways (R10.4.6):

1. A block exists in `templates/legal_notices.md` under the key `{{country_alpha2}}.{{channel_type}}` — for a Korean recipient on a role address, `KR.corporate_email`. Copy it **verbatim**, unedited, under the `Required legal notices` heading.
2. No block exists for that key — render this literal line and nothing else:

```text
- No notice block on file for {{country_name}} / {{channel_type}} — obtain wording before sending
```

When the flag is `no`, render the single line `- Not required for this jurisdiction and channel` and keep the heading, so every draft is diff-comparable.

This template never writes opt-out, unsubscribe, sender-identification or advertising-label wording of its own, never paraphrases a block, and never concludes that a notice is sufficient. Korean gloss: 광고 표시·수신거부 문구는 관할·채널별 블록을 그대로 인용할 뿐, 이 템플릿이 작성하거나 충분하다고 판단하지 않는다.

---

## 7. Worked example (fictional company)

Variant S-V1 — no RFQ, the case PRD test T05 guards. Every name, domain and URL is fictional and uses the reserved `.example` TLD.

```text
Outreach Draft — Seller — 한빛코스메틱 주식회사 (Hanbit Cosmetic Co., Ltd.)
Status: READY_FOR_REVIEW
Auto-send: false
Approval: required (human)
Recipient channel: corporate_email — export@hanbitcos.example
Language: Korean
Subject: (광고) 선케어 수출 프로필 확인 요청 (TradeWith)

Body
안녕하세요, 한빛코스메틱 주식회사 수출팀님.

귀사 영문 사이트의 OEM 페이지에서 선스크린과 선쿠션 자체 생산과 ODM 개발을 확인했습니다.
저희는 TradeWith, K-Beauty 해외 B2B 소싱을 중개하는 팀입니다.

현재 귀사 제품에 대한 구매 요청(RFQ)은 보유하고 있지 않습니다. 이 메일은 향후 요청이 접수될 때 비교 가능한 형태로 등록해 두시길 제안드리는 것입니다.

공개된 페이지에서 아래와 같이 확인했습니다. 사실과 다르거나 최신이 아닌 항목은 회신으로 정정해 주십시오.
- Categories: sunscreen, sun_cushion — [https://www.hanbitcos.example/en/oem]
- OEM/ODM: yes — [https://www.hanbitcos.example/en/oem]
- Private label: unknown
- MOQ: 3,000 units — [https://www.hanbitcos.example/en/oem/moq]
- Lead time: unknown
- Certifications: ISO22716 (unverified list) — [https://www.hanbitcos.example/en/company/certification]
- Export markets: JP, VN — [https://www.hanbitcos.example/en/company/global]

해외 바이어는 MOQ, 인증, 수출 가능 국가, 리드타임을 기준으로 후보를 좁힙니다. 위 항목이 unknown으로 남아 있으면 비교 대상에서 빠지기 쉽습니다.
수출용 라인카드 또는 위 항목이 채워진 회신을 보내주시면 프로필에 반영하겠습니다. 별도 비용이나 독점 계약은 없습니다.
관심이 없으시면 "관심 없음"이라고 회신해 주십시오. 이후로는 연락드리지 않겠습니다.

감사합니다.
TradeWith — K-Beauty 소싱 데스크
partnerships@tradewith.example

Personalization facts
- OEM page states in-house production of sunscreen and sun cushion, plus ODM development — [https://www.hanbitcos.example/en/oem] (observed 2026-09-12)
- MOQ page states a minimum order of 3,000 units per item — [https://www.hanbitcos.example/en/oem/moq] (observed 2026-09-12)
- Certification page lists ISO 22716; the list is not stated to be exhaustive — [https://www.hanbitcos.example/en/company/certification] (observed 2026-09-12)
- Global page names Japan and Vietnam as current export markets — [https://www.hanbitcos.example/en/company/global] (observed 2026-09-12)
- Contact page publishes the role address export@hanbitcos.example — [https://www.hanbitcos.example/en/contact] (observed 2026-09-12)

Compliance checks
- Jurisdiction: Korea, Republic of — direct-marketing review: required
- Advertising label / opt-out required: yes
- Personal data used: none (company-level channel only)
- Claims verified against evidence: yes

Reviewer checklist
- [ ] Every personalization fact traces to a cited source
- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited
- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open
- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced
- [ ] Contact channel is an official company channel
- [ ] Jurisdiction rules confirmed before sending
- [ ] Without an RFQ, the no-RFQ disclosure sentence is present and unhedged
- [ ] No named individual anywhere in the draft, greeting included
- [ ] No [[ev: ...]] marker survived into the body
- [ ] Subject is 60 characters or fewer, specific, and free of urgency wording

Required legal notices
- 제목 맨 앞에 (광고) 표시
- 전송자: TradeWith (서울특별시 · 사업자등록번호는 발송 계정 설정값)
- 연락처: partners@tradewith.example
- 수신거부: partners@tradewith.example 로 회신하시면 이후 광고성 정보를 보내지 않습니다.
- 본 메일은 https://www.hanbitcos.example/en/contact 에 공개된 문의 채널로 발송되었습니다.

Next action: human review, then APPROVED_FOR_OUTREACH in the application layer

status: READY_FOR_REVIEW
auto_send: false
manual_approval_required: true
```

Notes on the render: the notices block is the `KR.corporate_email` block of `templates/legal_notices.md`, copied verbatim with its tokens filled — `status: required`, so it is mandatory on this channel and the `(광고)` marker belongs in the subject line the reviewer sends. No buyer, no order and no demand is mentioned anywhere, and the disclosure sentence sits above the offer rather than below it. `Private label` and `Lead time` print the literal `unknown` with no URL, because printing the gap is the point of the block. The certification line carries ` (unverified list)` because `certifications_verified` is not `true` (10.2), so the mail never implies we confirmed the list is complete. English reference translation of the offer sentence, for a reviewer who does not read Korean: "Overseas buyers shortlist on MOQ, certifications, export markets and lead time; fields left unknown tend to drop out of comparison."

### 7.1 Evidence table for the worked example

| `evidence_id` | `claim` | Value used in the body | `source_url` | `source_type` / tier | `is_official` | `observed_at` | `source_date` |
|---|---|---|---|---|---|---|---|
| `EV-001` | `oem_odm` | in-house sunscreen and sun-cushion production, ODM development | https://www.hanbitcos.example/en/oem | `official_site` / 1 | true | 2026-09-12T02:20:00Z | unknown |
| `EV-002` | `product_categories` | sunscreen, sun_cushion | https://www.hanbitcos.example/en/oem | `official_site` / 1 | true | 2026-09-12T02:20:00Z | unknown |
| `EV-003` | `moq` | 3,000 units per item | https://www.hanbitcos.example/en/oem/moq | `official_site` / 1 | true | 2026-09-12T02:24:00Z | 2026-03-11 |
| `EV-004` | `certifications` | ISO22716, list not stated to be exhaustive | https://www.hanbitcos.example/en/company/certification | `official_site` / 1 | true | 2026-09-12T02:27:00Z | unknown |
| `EV-005` | `export_markets` | JP, VN | https://www.hanbitcos.example/en/company/global | `official_site` / 1 | true | 2026-09-12T02:31:00Z | 2026-01-09 |
| `EV-006` | `contact_channels` | role address `export@hanbitcos.example` | https://www.hanbitcos.example/en/contact | `official_site` / 1 | true | 2026-09-12T02:33:00Z | unknown |

Fields with no evidence item — `private_label`, `lead_time_days`, `monthly_capacity_units`, `regulatory_registrations`, `certifications_verified` — stay `unknown` and are exactly what the mail asks for. Marker map: `[[ev: EV-001]]` and `[[ev: EV-002]]` on the opening sentence, and one marker per transparency line (`EV-002`, `EV-001`, none, `EV-003`, none, `EV-004`, `EV-005`).

---

## 8. Forbidden phrases and blockers

**Forbidden without a cited `rfq_id` in status `qualified` / `matching` / `proposal_open`** (R10.4.3, INV-34, PRD 11.1, PRD test T05). The list is illustrative, not exhaustive; the rule is the intent, not the wording.

| English | Korean |
|---|---|
| a buyer is currently looking for your products | 현재 귀사 제품을 찾는 바이어가 있습니다 |
| we have a buyer for this category | 해당 카테고리 바이어를 보유하고 있습니다 |
| there is active demand for | 지금 수요가 많습니다 |
| orders are waiting / a buyer is waiting | 바이어가 대기 중입니다 |
| we can place an order immediately | 즉시 발주가 가능합니다 |
| exclusive supply opportunity | 독점 공급 기회입니다 |
| limited slots, closing soon | 마감 임박, 자리가 얼마 남지 않았습니다 |

Do not hand a draft to review when any of the following is true. Korean gloss: 아래에 해당하면 초안을 제출하지 않는다.

1. A `Body` sentence with no matching `Personalization facts` line (R10.4.2).
2. Any phrase from the table above, in any language, without the qualifying RFQ id, its status and its `as_of` date.
3. S-V1 or S-V2 rendered without the no-RFQ disclosure sentence, or with it hedged, shortened or placed after the CTA.
4. A `[[ev:` marker, an unfilled `{{token}}`, or a source URL that does not resolve.
5. MOQ, lead time, capacity, certification, registration, exclusivity or export-market wording that exceeds what the evidence states — including a lower-bound MOQ presented as a firm number (3.3).
6. A named individual, a personal address, a personal number, or an address built by pattern from a name (INV-11, INV-31).
7. A contact channel that is not company-published, or one reached by bypassing a login, paywall or CAPTCHA (INV-12).
8. `Claims verified against evidence: blocked`, or a compliance flag the reviewer has not seen.
9. A state later than `READY_FOR_REVIEW` written anywhere in the draft (INV-09, INV-37).
