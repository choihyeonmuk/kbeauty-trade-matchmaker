# Buyer Outreach Draft Template

| | |
|---|---|
| File | `templates/buyer_outreach.md` |
| Side | `Buyer` — an overseas distributor, importer, wholesaler, retailer, brand or marketplace |
| Envelope | `references/output-format.md` 10.4, reproduced literally (capitalization, punctuation, order) |
| Package | `kbeauty-trade-matchmaker` · `skill_version 0.5.0` |
| Korean gloss | 해외 바이어 대상 아웃리치 **초안** 템플릿. 초안 생성까지가 범위이며 자동 발송은 금지된다. |

**Header note (BUILD-CONTRACT.md 1.4).** The 10.4 envelope is reproduced line for line. This template adds exactly two things the envelope permits but does not print: the `Required legal notices` heading mandated by R10.4.6, and the three-line INV-09 trailer. It adds no other line and removes none.

---

## 0. Non-negotiables (read before drafting)

Korean gloss: 아래 항목은 예외 없이 적용된다.

1. **Draft only.** Every rendered draft ends at `READY_FOR_REVIEW` with `auto_send: false` and `manual_approval_required: true`. This package contains no transport of any kind; approval and delivery happen in the application layer (PRD 11.2, INV-09, INV-10).
2. **Every fact about the recipient is sourced.** Each asserting sentence in `Body` maps to one `Personalization facts` line carrying a click-ready source URL and an observation date. Unsourced personalization is a blocker, not a style note (PRD OUT-02, R10.4.2, INV-34).
3. **No fabricated demand.** Do not write "we have a buyer for", "there is active demand for", "a supplier is holding capacity for you", or any equivalent in any language, unless a cited `rfq_id` resolves to an RFQ whose `status` is `qualified`, `matching` or `proposal_open` **and** the draft states that status and the RFQ's `as_of` date (R10.4.3, PRD test T05).
4. **No "we noticed you are looking for…"** unless an `evidence_id` backs it. The buyer's own dated, public sourcing action is the only thing that licenses that sentence; `sourcing_intent` is a derived score input and is never quotable on its own.
5. **Company level only.** No named individual, personal email address, direct line or personal social handle anywhere — greeting included. Greet a role ("brand partnerships team"), never a person (BUILD-CONTRACT.md 3.6, R3.6.1, INV-31).
6. **Unknown stays unknown.** A missing fact removes its sentence. Never soften it into "we believe", "it appears", "you likely" (PRD EVID-05, R3.2.3).
7. **CTA is a sourcing request or a proposal exchange**, never "sign up" (PRD OUT-03). An account, if one is needed, is mentioned as a mechanism at most once, never as the ask.
8. **No legal determination.** The compliance flags record what must be checked. This skill never concludes that sending is lawful (PRD 11, BUILD-CONTRACT.md 14.6).

---

## 1. How to render

1. Choose the variant in section 4 that matches what you actually hold: no RFQ, a counted internal supply pool, this buyer's own live RFQ, or a follow-up.
2. Pick the contact channel. Use the highest-ranked published company channel (`B-RE1` order: `partnership_form` > `corporate_email` > `form` > `contact_page` > `linkedin` > `phone` > `other`). When `contact_channels` is absent the channel is `unknown` and the draft is blocked until one is found; when it is present and empty the value renders `none published` and the draft is blocked for the same reason.
3. Fill every `{{token}}`. A token you cannot fill from evidence means the sentence around it is deleted, not guessed.
4. Every personalizing clause carries one `[[ev: EV-nnn]]` marker in the template. Markers are authoring aids: each one MUST become a `Personalization facts` line, and **no marker may survive into the rendered body**. A rendered draft containing `[[ev:` is a blocker.
5. Fill `Compliance checks` from `references/compliance-notes.md`. Unresolved flags stay `unknown` — that is a valid, reviewable state.
6. If `Advertising label / opt-out required` is `yes` or `unknown`, resolve `{{legal_notice_block}}` per section 6.
7. Keep the body at or under 150 words and 5 short paragraphs. Trade buyers read on a phone between meetings.
8. Hand the draft to a human. Nothing here advances the state machine past `READY_FOR_REVIEW` (BUILD-CONTRACT.md 9, INV-37).

---

## 2. Envelope tokens (structural, not personalizing)

| Token | Renders | Rule |
|---|---|---|
| `{{buyer.company_name}}` | the company name exactly as it publishes it | never truncated, never abbreviated (10.5) |
| `{{channel_type}}` | one `contact_channels[].type` value | company-level types only (3.6) |
| `{{channel_value}}` | the channel URL or role address | as published by the company; never pattern-guessed |
| `{{language}}` | the recipient's business language, e.g. `English`, `Arabic` | envelope labels stay English (R10.4.5) |
| `{{subject}}` | the subject line | ≤ 60 characters, specific, no exclamation mark, no ALL CAPS, no emoji, no "Re:" unless it really is a reply |
| `{{team_label}}` | a role, e.g. `brand partnerships team`, `sourcing team` | role only, never a person (INV-31) |
| `{{sender_org}}` | the sending organisation | must match the channel the draft is sent from |
| `{{sender_role_label}}` | our own role, e.g. `K-Beauty sourcing desk` | role, not a person |
| `{{sender_reply_channel}}` | our reply address or page | ours, so no evidence binding applies |
| `{{buyer.country_name}}` | display country name | the literal `unknown` when unknown (10.5) |
| `{{compliance.direct_marketing_review}}` | `required` / `not_required` / `unknown` | one of those three literals |
| `{{compliance.ad_label_required}}` | `yes` / `no` / `unknown` | drives section 6 |
| `{{compliance.claims_verified}}` | `yes` / `blocked` | `blocked` means the draft must not leave review |
| `{{legal_notice_block}}` | section 6 output | verbatim block or the literal fallback line |

---

## 3. Personalization tokens and their evidence binding

Every token below is a factual assertion about the recipient. Each row states the **canonical `evidence.claim` key** (BUILD-CONTRACT.md 4.6) that must back it, the weakest acceptable source, and what to do when the fact is unknown. Korean gloss: 각 개인화 문장은 아래 claim key의 evidence 없이는 쓸 수 없다.

| Token | Renders | Required `evidence.claim` | Weakest acceptable source | If unknown |
|---|---|---|---|---|
| `{{buyer.company_name}}` | the company name | `company_name` | tier 1 official site | block the draft |
| `{{buyer.country_name}}` | market the buyer operates in | `country` | tier ≤ 2 | delete the clause |
| `{{buyer.category_display}}` | the categories they buy | `product_categories` | tier ≤ 2 | delete the clause |
| `{{buyer.kbeauty_signal_statement}}` | one clause: they already handle Korean beauty | `korean_products_signal` | tier 1 official | delete the clause |
| `{{buyer.korean_brands_joined}}` | up to 3 Korean brands they list | `korean_brands_carried` | tier 1 official (their own catalogue) | delete the clause; never generalise to "you carry K-Beauty" |
| `{{buyer.wholesale_statement}}` | one clause: trade or wholesale channel | `wholesale_signal` | tier 1 official | delete the clause |
| `{{buyer.partnership_statement}}` | one clause: they invite brand or supplier submissions | `partnership_signal` | tier 1 official page | delete the clause; a generic contact form is not a partnership invitation |
| `{{buyer.sourcing_signal_statement}}` | the dated public sourcing action, with its date | `sourcing_signals` | tier ≤ 2, and dated | delete the clause — this is the only token that licenses "you are looking for…" |
| `{{buyer.channel_statement}}` | their distribution channels | `channels` | tier ≤ 3 | delete the clause |
| `{{tradewith.pool_statement}}` | `{{n}} verified Korean {{category}} manufacturers` | `internal_record` evidence (4.7) with a resolvable internal URL | `internal_api` | drop the number; never round, never estimate |
| `{{rfq.id}} {{rfq.status}} {{rfq.as_of}}` | this buyer's own live RFQ | `internal_record` (4.7) | `internal_api` | variant B-V3 is unavailable |

**Binding rule.** One marker per clause, one `Personalization facts` line per marker, one source URL per line. Two facts never share a marker, and one marker never covers a sentence the source does not literally support (BUILD-CONTRACT.md 4.1, 4.5).

---

## 4. The template

Render the block below literally. Lines 2 and 3 are fixed by R10.4.1 and may not move.

```text
Outreach Draft — Buyer — {{buyer.company_name}}
Status: READY_FOR_REVIEW
Auto-send: false
Approval: required (human)
Recipient channel: {{channel_type}} — {{channel_value}}
Language: {{language}}
Subject: {{subject}}

Body
Hello {{buyer.company_name}} {{team_label}},

{{opening_fact_sentence}} [[ev: {{ev.opening}}]]
{{second_fact_sentence}} [[ev: {{ev.second}}]]

{{who_we_are_sentence}}
{{offer_sentence}} [[ev: {{ev.offer}}]]

{{cta_sentence}}
{{exit_sentence}}

{{sign_off}}
{{sender_org}} — {{sender_role_label}}
{{sender_reply_channel}}

Personalization facts
- {{fact}} — [{{source_url}}] (observed {{observed_date}})

Compliance checks
- Jurisdiction: {{buyer.country_name}} — direct-marketing review: {{compliance.direct_marketing_review}}
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
- [ ] No supplier is described as holding capacity, stock or exclusivity for this buyer
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

**Sentence slots.** `{{opening_fact_sentence}}` and `{{second_fact_sentence}}` are the only personalizing slots that open the mail; both take tokens from section 3. `{{who_we_are_sentence}}` describes us and needs no evidence binding. `{{offer_sentence}}` needs a binding only when it states a number or a verified property of our supply side. `{{cta_sentence}}` and `{{exit_sentence}}` assert nothing about the recipient.

---

## 5. Variants

Korean gloss: 보유한 근거의 종류에 따라 변형을 고른다. 근거가 없으면 상위 변형을 쓰지 않는다.

### 5.1 B-V1 — First touch, no RFQ

**Use when** the buyer was found by discovery, we hold public evidence about their assortment or trade channel, and no RFQ exists on either side.
**Precondition** ≥ 2 personalization facts from section 3, at least one from a tier 1 official page.

Subject patterns (≤ 60 characters):
- `Korean {{category}} suppliers for {{buyer.short_name}}`
- `K-Beauty {{category}} sourcing — {{buyer.country_name}}`

Body skeleton:

```text
Hello {{buyer.company_name}} {{team_label}},

{{buyer.partnership_statement}} [[ev: {{ev.partnership}}]]
{{buyer.wholesale_statement}} [[ev: {{ev.wholesale}}]]

We are {{sender_org}}, a Korea-based B2B sourcing desk for K-Beauty.
We verify a manufacturer's MOQ, certifications and export markets against its own published pages before we name it.

If {{category}} is on your buying list, reply with your volume range, MOQ ceiling and certification requirements and we will send a shortlist with the source for every claim.
If it is not, tell us which categories are and we will keep the note on file.

{{sign_off}}
```

Forbidden here: any suggestion that a Korean supplier is waiting, that stock is held, or that the buyer is currently searching.

### 5.2 B-V2 — No RFQ yet, counted supply pool

**Use when** B-V1 holds **and** an internal record supports a counted, verified set of Korean suppliers in the buyer's category.
**Precondition** one `internal_record` evidence item with a resolvable internal URL and an exact count. No count, no variant.

Subject patterns:
- `{{n}} verified Korean {{category}} manufacturers`
- `Korean {{category}} supply — MOQ and certification data`

Body adds one sentence to B-V1:

```text
We currently hold {{n}} Korean {{category}} manufacturers whose MOQ, certification status and export markets we verified against their own pages. [[ev: {{ev.pool}}]]
```

The count is the counted number on the stated date, never rounded up and never described as growing, scarce or reserved.

### 5.3 B-V3 — RFQ in hand (this buyer's own request)

**Use when** the buyer already has an RFQ in TradeWith whose `status` is `qualified`, `matching` or `proposal_open`. An RFQ in `draft`, `matched` or `closed` counts as no RFQ (R10.4.3) — use B-V1 or B-V2 instead.

Subject patterns:
- `RFQ #{{rfq.id}} — {{n}} Korean suppliers matched`
- `RFQ #{{rfq.id}} {{rfq.product_category_display}} — shortlist ready`

Required sentence, rendered with all three facts present:

```text
Your RFQ #{{rfq.id}} ({{rfq.product_category_display}}, {{rfq.destination_country_name}}) is in status {{rfq.status}} as of {{rfq.as_of}}. [[ev: {{ev.rfq}}]]
{{n}} Korean suppliers cleared its hard requirements: MOQ at or below {{rfq.max_moq_display}} and {{rfq.required_certifications_joined}}. [[ev: {{ev.match_run}}]]
```

Stating the status and the `as_of` date is mandatory, not optional. MOQ, certifications and export markets appear only where the seller records evidence them (PRD 11.1).

### 5.4 B-V4 — Follow-up

**Use when** a prior draft for this company was approved and sent, no reply arrived, and the cadence rule below is satisfied.
**Cadence** at most one follow-up per company per 30 days, and never after any negative reply, opt-out or channel complaint. A second follow-up requires a new, dated, evidenced reason — not a new adjective.

Subject pattern: `Following up: Korean {{category}} suppliers` (never "Re:" unless it is a genuine reply).

Rules:
- Restate no fact that was not in the approved first draft, and add none that lacks its own evidence line.
- Never escalate: no "last chance", no "closing the list", no deadline we did not already state.
- Carry the same `Personalization facts`, `Compliance checks` and `Required legal notices` blocks — a follow-up is a full draft, not a fragment.
- Offer an explicit close: `If this is not relevant, reply "no" and we will not follow up again.`

---

## 6. Required legal notices and the opt-out slot (PRD OUT-06)

When `Advertising label / opt-out required` is `yes` or `unknown`, `{{legal_notice_block}}` resolves in exactly one of two ways (R10.4.6):

1. A block exists in `templates/legal_notices.md` under the key `{{country_alpha2}}.{{channel_type}}` — for example `AE.corporate_email`. Copy it **verbatim**, unedited, under the `Required legal notices` heading.
2. No block exists for that key — render this literal line and nothing else:

```text
- No notice block on file for {{country_name}} / {{channel_type}} — obtain wording before sending
```

When the flag is `no`, render the single line `- Not required for this jurisdiction and channel` and keep the heading, so every draft is diff-comparable.

This template never writes opt-out, unsubscribe, sender-identification or advertising-label wording of its own, never paraphrases a block, and never concludes that a notice is sufficient. Korean gloss: 수신거부·광고표시 문구는 관할·채널별 블록을 그대로 인용할 뿐, 이 템플릿이 작성하거나 충분하다고 판단하지 않는다.

---

## 7. Worked example (fictional company)

Variant B-V2. Every name, domain and URL below is fictional and uses the reserved `.example` TLD.

```text
Outreach Draft — Buyer — Dune & Petal Trading LLC
Status: READY_FOR_REVIEW
Auto-send: false
Approval: required (human)
Recipient channel: partnership_form — https://www.dunepetal.example/brand-partnerships
Language: English
Subject: Korean sunscreen suppliers for Dune & Petal

Body
Hello Dune & Petal Trading LLC brand partnerships team,

Your brand-partnerships page, updated 2026-07-30, states that you accept new skincare and sun-care submissions.
Your wholesale page lists trade accounts for the UAE and Oman, and your skincare catalogue lists the Korean brands Soomi Lab and Yeondew.

We are TradeWith, a Korea-based B2B sourcing desk for K-Beauty.
We currently hold 14 Korean sunscreen manufacturers whose MOQ, ISO 22716 status and export markets we verified against their own published pages.

If sunscreen is on your buying list for the coming season, reply with your volume range, MOQ ceiling and certification requirements, and we will send a shortlist with the source for every claim.
If it is not, tell us which categories are and we will keep the note on file.

Best regards,
TradeWith — K-Beauty sourcing desk
partnerships@tradewith.example

Personalization facts
- Brand-partnerships page dated 2026-07-30 states that new skincare and sun-care submissions are accepted — [https://www.dunepetal.example/brand-partnerships] (observed 2026-09-12)
- Wholesale page lists trade accounts for the UAE and Oman — [https://www.dunepetal.example/wholesale] (observed 2026-09-12)
- Skincare catalogue lists the Korean brands Soomi Lab and Yeondew — [https://www.dunepetal.example/brands/korean-skincare] (observed 2026-09-12)
- 14 Korean sunscreen manufacturers verified in the TradeWith research pool — [https://app.tradewith.example/research/pools/kr-sunscreen] (observed 2026-09-12)

Compliance checks
- Jurisdiction: United Arab Emirates — direct-marketing review: required
- Advertising label / opt-out required: unknown
- Personal data used: none (company-level channel only)
- Claims verified against evidence: yes

Reviewer checklist
- [ ] Every personalization fact traces to a cited source
- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited
- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open
- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced
- [ ] Contact channel is an official company channel
- [ ] Jurisdiction rules confirmed before sending
- [ ] No supplier is described as holding capacity, stock or exclusivity for this buyer
- [ ] No named individual anywhere in the draft, greeting included
- [ ] No [[ev: ...]] marker survived into the body
- [ ] Subject is 60 characters or fewer, specific, and free of urgency wording

Required legal notices
- Submitted through the distributor or partner application form published at
  https://www.dunepetal.example/brand-partnerships.
- Sender: TradeWith — partners@tradewith.example
- No telephone number is contacted from this workflow.

Next action: human review, then APPROVED_FOR_OUTREACH in the application layer

status: READY_FOR_REVIEW
auto_send: false
manual_approval_required: true
```

Notes on the render: the notices block is the `AE.partnership_form` block of `templates/legal_notices.md`, copied verbatim with its tokens filled; its `status` is `unknown`, so the draft also carries the §6 escalation and stops at review. The channel is a web form, so `Subject` is the form's subject field; the sign-off block is kept because the form has a free-text body. No RFQ exists, so no sentence implies a waiting supplier or an active search — the buyer's own published invitation is the only intent fact used, and it is quoted with its page date.

### 7.1 Evidence table for the worked example

| `evidence_id` | `claim` | Value used in the body | `source_url` | `source_type` / tier | `is_official` | `observed_at` | `source_date` |
|---|---|---|---|---|---|---|---|
| `EV-001` | `partnership_signal` | accepts new skincare and sun-care submissions | https://www.dunepetal.example/brand-partnerships | `official_site` / 1 | true | 2026-09-12T04:10:00Z | 2026-07-30 |
| `EV-002` | `wholesale_signal` | trade accounts for the UAE and Oman | https://www.dunepetal.example/wholesale | `official_site` / 1 | true | 2026-09-12T04:12:00Z | unknown |
| `EV-003` | `korean_brands_carried` | Soomi Lab, Yeondew | https://www.dunepetal.example/brands/korean-skincare | `official_site` / 1 | true | 2026-09-12T04:15:00Z | unknown |
| `EV-004` | `contact_channels` | partnership form URL used as the channel | https://www.dunepetal.example/brand-partnerships | `official_site` / 1 | true | 2026-09-12T04:10:00Z | 2026-07-30 |
| `EV-005` | `internal_record` | 14 verified Korean sunscreen manufacturers | https://app.tradewith.example/research/pools/kr-sunscreen | `internal_record` / 2 | false | 2026-09-12T05:02:00Z | 2026-09-12 |

Marker map for the draft above: `[[ev: EV-001]]` on the partnership sentence, `[[ev: EV-002]]` and `[[ev: EV-003]]` on the second sentence (split into two facts), `[[ev: EV-005]]` on the pool sentence. The "we are TradeWith" and CTA sentences carry no marker because they assert nothing about the recipient.

---

## 8. Blockers — do not hand these to review

Korean gloss: 아래에 해당하면 초안을 제출하지 않는다.

1. A `Body` sentence with no matching `Personalization facts` line (R10.4.2).
2. Any live-demand or "we noticed you are looking for…" wording without a qualifying `rfq_id`, its status and its `as_of` date (R10.4.3, INV-34, PRD test T05).
3. A `[[ev:` marker, an unfilled `{{token}}`, or a fact whose source URL does not resolve.
4. A named individual, a personal address, a personal number, or an address built by pattern from a name (INV-11, INV-31).
5. MOQ, certification, exclusivity, capacity or export-market wording that is not backed by a seller record's evidence (PRD 11.1).
6. A contact channel that is not company-published, or a channel reached by bypassing a login, paywall or CAPTCHA (INV-12).
7. `Claims verified against evidence: blocked`, or any compliance flag the reviewer has not seen.
8. A state later than `READY_FOR_REVIEW` written anywhere in the draft (INV-09, INV-37).
