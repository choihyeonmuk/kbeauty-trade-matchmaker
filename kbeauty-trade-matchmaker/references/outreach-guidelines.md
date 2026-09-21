# Outreach Guidelines

Korean gloss: 아웃리치 초안 작성 지침 — 검증된 사실만, 개인화는 근거와 함께, 발송은 사람이.

This page governs Mode 4 (Outreach Draft). It implements PRD 5.4, PRD 6.5 (OUT-01..OUT-06),
PRD 11.1–11.2 and PRD 15.4, and it is the prose companion to `templates/buyer_outreach.md`,
`templates/seller_outreach.md` and `templates/legal_notices.md`.

---

## 1. The boundary this skill never crosses

```
default_outreach_state = READY_FOR_REVIEW
auto_send             = false
manual_approval_required = true
```

- **Drafting and sending are different capabilities, and only drafting lives here.** The package
  contains no transport of any kind — no mail-transport library, no mail API client, no webhook
  send, no send scheduler
  (INV-10, BUILD-CONTRACT §14.1). This is a build rule, not a setting.
- **Every outreach run terminates at `READY_FOR_REVIEW`.** This skill may set `DISCOVERED`,
  `VERIFIED`, `QUALIFIED`, `MATCH_CANDIDATE` and `READY_FOR_REVIEW`, and nothing after them.
  `APPROVED_FOR_OUTREACH` and every later state belong to the application layer and require a human
  decision recorded outside the skill (BUILD-CONTRACT §9.2, INV-09, INV-37).
- **No bulk anything.** The skill drafts per recipient, for a named human reviewer. It never
  generates a campaign, a mail-merge list, or a send schedule (PRD 11.1).
- If an operator asks for automatic sending, the correct answer is to produce the draft, state that
  sending happens in TradeWith after approval, and stop. Do not offer a workaround.

Korean gloss: 이 스킬의 산출물은 항상 "검토 대기" 상태의 초안이며, 발송은 TradeWith 애플리케이션 계층에서 사람이 승인한 뒤에 이루어진다.

---

## 2. The envelope

Every draft — buyer-side or seller-side, rendered from a template or inline — uses the
`references/output-format.md` §10.4 envelope verbatim:

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

Non-negotiable envelope rules:

| Rule | What it requires |
|---|---|
| **R10.4.1** | `Status: READY_FOR_REVIEW` and `Auto-send: false` are literal, and are the 2nd and 3rd lines. Always. (INV-09) |
| **R10.4.2** | Every sentence of the body that asserts a fact about the recipient appears in `Personalization facts` with a source URL. Unsourced personalization is a blocker, not a style note. |
| **R10.4.3** | A live-demand claim requires a cited `rfq_id` resolving to `status ∈ {qualified, matching, proposal_open}`, and the draft must state that status and the RFQ's `as_of` date. See §5. |
| **R10.4.4** | The CTA is a sourcing request or a proposal submission, never "sign up". See §6. |
| **R10.4.5** | `{{side}}` is `Buyer` or `Seller`. `{{language}}` is the recipient's business language; the envelope labels themselves stay English. |
| **R10.4.6** | When `Advertising label / opt-out required` is `yes` or `unknown`, append the matching block from `templates/legal_notices.md` **verbatim** under a `Required legal notices` heading — or the literal fallback line when no block exists for that jurisdiction and channel. The drafts never invent legal wording. |

**`Claims verified against evidence: blocked`** is a real, usable outcome. When a personalization
fact cannot be traced, the correct move is to remove the sentence and re-render — not to mark the
draft `yes` and hope. If removing it leaves nothing personal to say, the draft is generic, and a
generic draft is still a valid deliverable (PRD 5.4: "사실 근거가 약하면 일반화").

---

## 3. Traceability — the rule that makes the rest work

> Every **specific** claim in a draft traces to an `evidence_id` on the record the draft was built
> from, and that evidence item's `source_url` appears in `Personalization facts`.

Korean gloss: 초안의 모든 구체적 주장은 레코드의 `evidence_id`로 추적되어야 하며, 해당 출처 URL이 `Personalization facts`에 노출된다.

**Specific** means anything a reader could check: a category, a brand carried, a certification, an
MOQ, an export market, a page they published, a trade show they exhibited at, a role they stated they
are recruiting for. **Generic** means anything true of the category as a whole: "K-Beauty sunscreen
demand in the GCC", "Korean manufacturers commonly hold ISO 22716". Generic sentences need no
evidence id and must not be dressed up as specific.

Mechanics:

1. Build the draft only from a **scored, qualified** target: a `score_buyer.py` / `score_seller.py`
   record with `qualified: true`, or a `score_match.py` candidate with `hard_filter.passed: true` and
   `qualified: true` (`score_version != "unscored"` in both cases). Drafting from a raw discovery
   record cannot satisfy R10.4.2. The scorers never write `status`: `QUALIFIED` and
   `MATCH_CANDIDATE` are lifecycle states a human or the adapter sets afterwards, so they are not the
   gate for drafting.
2. For each intended personalization sentence, name the `evidence_id` that supports it **before**
   writing the sentence. No id ⇒ no sentence.
3. Write the sentence at **no more than** the strength of the quote (`references/evidence-policy.md`
   §7.1 — never strengthen a paraphrase).
4. Emit the `Personalization facts` line with the evidence item's `source_url` and its
   `observed_at` date, rendered `YYYY-MM-DD`.
5. A fact resting on an item with `inferred: true` may be used only when the sentence itself hedges
   the same way the note does, and the hedge survives into `Personalization facts`.
6. A fact resting on a `stale: true` item is not used. Re-verify or drop it.
7. **Three personalization facts is a good draft; six is a dossier.** Pick the facts that justify
   *this* approach, not every fact you hold.

---

## 4. Buyer-side drafts (`templates/buyer_outreach.md`)

**Who the recipient is.** A distributor, importer, wholesaler, retail chain or procurement function
abroad, reached on a channel they published themselves.

**What the message is for.** Inviting them to state what they are sourcing — a Buying Request — or to
receive a short list of Korean suppliers matched to requirements they have already published.

**Structure that works**

| Block | Content | Sourcing |
|---|---|---|
| Opening | One sentence naming the specific, evidenced thing about them that caused the approach | evidence-backed |
| Relevance | One or two sentences on what TradeWith can put in front of them — Korean manufacturers in the category they carry, with the capability dimensions this skill actually verifies (category, commercial model, MOQ band, certifications) | generic, no claim about a specific supplier unless evidenced |
| Constraint honesty | Where a supplier's attribute is unknown, say "to be confirmed" rather than omitting it | — |
| CTA | A sourcing request: destination market, product, target MOQ, required certifications, timeline | — |
| Close | Human sender identity and the company's own contact route | — |

**Buyer-side specifics**

- Name what they carry only from their own pages. "You carry Korean skincare brands" needs a
  `korean_products_signal` or `korean_brands_carried` evidence item; naming a *specific* brand needs
  an item whose quote names that brand.
- If `sourcing_intent` is `medium` (a standing partnership page, no dated action), the correct
  phrasing is "your brand-partnership page invites submissions", **not** "you are currently
  recruiting".
- Never claim you have already matched them to a supplier unless a match run produced a
  `match_candidate` with `hard_filter.passed == true`, and then cite the supplier's evidenced
  attributes, not a name you cannot support.
- Prefer their `partnership_form` or `wholesale_form` channel over `corporate_email` wherever both
  exist. It is the channel they built for this, and it is the lowest-risk one in every jurisdiction
  in `references/compliance-notes.md`.
- If the only channel is `phone`, the draft is a call-prep note, not an email. Say so in `Subject`
  and keep the same envelope.

---

## 5. Seller-side drafts (`templates/seller_outreach.md`)

**Who the recipient is.** A Korean manufacturer, brand, OEM/ODM house or exporter.

The seller-side template has **two variants**, and picking the wrong one is the single most common
compliance failure in this skill.

### 5.1 RFQ-present variant

Usable **only** when all of the following hold:

- a real `rfq_id` exists in TradeWith;
- its `status` is one of `qualified`, `matching`, `proposal_open` — `draft` is not yet a buying
  request, and `matched` / `closed` is no longer one (R10.4.3);
- the draft **states that status and the RFQ's `as_of` date**;
- the seller passed the hard filter for that RFQ (`hard_filter.passed == true`), so the invitation is
  real rather than speculative.

Then, and only then, the draft may describe live demand — and it describes it in RFQ terms:
destination market, product category, quantity or MOQ ceiling, required certifications, commercial
model, timeline. The CTA is **submit a proposal against RFQ #N**.

Cite the RFQ like a source: `RFQ #134 — status: matching — as of 2026-09-12`.

### 5.2 RFQ-absent variant

When no qualifying RFQ exists, the draft **must not** imply demand in any form. Its job is to offer
capability registration: "if your export terms are on file, you appear in matching when a request
that fits arrives." The CTA is **share your export capability** — categories, OEM/ODM and
private-label terms, MOQ band, certifications, target markets — not "sign up".

This is PRD test **T05** in one line: *no RFQ, no demand claim.*

### 5.3 Seller-side specifics

- Do not restate the seller's MOQ, certifications or export markets back to them unless you are using
  them to explain the fit, and each has an evidence id. Telling a company its own MOQ from a stale
  directory entry is how a draft gets deleted.
- Where a required attribute is `"unknown"`, ask for it. "Your site does not state a minimum order
  quantity for OEM projects — could you confirm the band?" is a strong, honest, evidenced sentence:
  the evidence is the page that did not state it.
- Never assert that a destination-market registration exists. `registration_status: in_progress` is
  written as "registration in progress, per your export page", never "registered".
- Korean-language drafts are normal here. Write the body in Korean, keep the envelope labels in
  English (R10.4.5), and keep the same evidence discipline — `Personalization facts` lines stay in
  the envelope's English label form with the Korean fact after the dash.

---

## 6. CTA policy (PRD OUT-03)

| Side | ✓ Use | ✗ Never |
|---|---|---|
| Buyer | "Reply with the destination market, product, target MOQ and required certifications and we will come back with a short list." | "Sign up", "register an account", "create your free profile", "click here to join" |
| Buyer | "If it is easier, send us your current sourcing brief and we will structure it as a Buying Request." | "Book a demo", "schedule a call this week" |
| Seller (RFQ present) | "Submit a proposal against RFQ #134 — private label sunscreen, UAE, MOQ ceiling 3,000, ISO 22716 required." | "Sign up to see the buyer" |
| Seller (RFQ absent) | "Share your export capability — categories, OEM/ODM terms, MOQ band, certifications, target markets — and we will match it against incoming requests." | "Register now before slots run out" |

Rules:

1. **One CTA per draft.** Two asks halve the reply rate and double the compliance surface.
2. The CTA must be answerable **by replying**. A CTA that requires account creation before any value
   is exchanged is the "가입하세요" pattern PRD 2.1 identifies as the reason cold outreach fails.
3. No calendar pressure, no artificial deadline, no "limited slots". See §7.
4. A registration link may appear as an *option* at the end, never as the ask.

---

## 7. Forbidden phrasing

> **Every quoted string in this section is a NEGATIVE example.** Nothing here may be copied into a
> draft. The INV-34 verifier check targets rendered outreach artifacts (blocks that open with
> `Outreach Draft — …` and carry `Status: READY_FOR_REVIEW`), not this reference page.

### 7.1 Fabricated urgency and fabricated demand (PRD 11.1, test T05, INV-34)

| ✗ Banned pattern | Why | ✓ Compliant rewrite |
|---|---|---|
| "We have a buyer currently looking for your sunscreen." | Asserts live demand with no RFQ. | *(RFQ absent)* "We work with GCC distributors sourcing Korean sun care. If your export terms are on file, you appear in matching when a request that fits arrives." |
| "There is active demand for your products in the UAE right now." | Same claim, market-level wording. | "UAE distributors we speak with ask most often about SPF 50+ formulations stable in heat and humidity." |
| "A distributor asked us specifically about your brand." | Fabricates a named third-party action. | *(only if true and evidenced)* "RFQ #134 — status: matching, as of 2026-09-12 — asks for private-label sunscreen into the UAE." |
| "Several buyers are waiting for a supplier like you." | Unquantified, unevidenced demand. | "Requests in this category typically specify MOQ under 3,000 and ISO 22716." |
| "Only 3 supplier slots left for the UAE market." | Invented scarcity. | "We are putting together the Korean supplier side for GCC requests this quarter." |
| "Respond within 48 hours to be included." | Invented deadline. | "If it is useful, a reply any time this month is fine — RFQs stay open until the buyer closes them." |
| "Your competitors have already joined." | Unverifiable social pressure, and a claim about third parties. | *(delete — there is no compliant version)* |
| "We can guarantee you orders." | Promises a commercial outcome the platform does not control. | "We can put your capability in front of buyers whose requirements it matches." |

### 7.2 Invented trade terms (PRD EVID-05, BUILD-CONTRACT §14.5)

| ✗ Banned pattern | Why | ✓ Compliant rewrite |
|---|---|---|
| "Since your MOQ is around 1,000 units…" | `moq` was `"unknown"`; the number was guessed. | "Your site does not state an OEM minimum order quantity — could you confirm the band?" |
| "Your ISO 22716 certification means…" | No `certifications` evidence item names the scheme. | "If the plant holds ISO 22716, that clears the most common requirement in this category." |
| "You already export to the UAE." | `export_markets` did not contain `AE`. | "Your export page names Saudi Arabia, Kuwait, Singapore and Japan — the UAE would be adjacent to markets you already serve." |
| "As the exclusive distributor for Korea in your territory…" | Exclusivity is contractual and unobservable. | *(delete — v0.1.1 asserts nothing about exclusivity)* |
| "Your OEM service can handle private label." | `oem_odm` and `private_label` are independent flags. | "Your site describes OEM projects; could you confirm whether private-label programmes are also available?" |
| "You are FDA approved." | Cosmetics are not FDA-approved. | "If the facility carries a US FDA establishment registration, that covers the MoCRA question for US-bound requests." |

### 7.3 Personal data and channel misuse (PRD 11.3, INV-11, INV-31)

| ✗ Banned pattern | Why | ✓ Compliant rewrite |
|---|---|---|
| Addressing a named individual found on LinkedIn and mailing a guessed `first.last@` address | Pattern-guessed personal addresses are forbidden outright; the name must not appear in any artifact. | Address the published company channel; open with "Hello — " or the team role the company itself published. |
| "I saw on your profile that you handle export." | Uses a named individual's personal profile as personalization. | "Your export page invites overseas partner enquiries." |
| Quoting a person's post as evidence of company intent | A person is not a company channel, and the quote would carry personal data. | Cite the company page or company social profile that states the same thing. |
| Re-using a contact from a previous campaign with no record of its source | Breaks traceability and data minimization. | Re-derive the channel from a current, cited company page. |

### 7.4 Tone and platform overreach

| ✗ Banned pattern | ✓ Compliant rewrite |
|---|---|
| "Following up again — did you see my last email?" (no prior contact recorded in TradeWith) | *(delete — the skill has no send history and must not imply one)* |
| "As discussed…" | *(delete — no conversation happened)* |
| "This is not a sales email." | "This is a sourcing enquiry from TradeWith." (Identify the message honestly; see `references/compliance-notes.md`.) |
| "Unsubscribe by ignoring this message." | Use the opt-out wording from `templates/legal_notices.md` for that jurisdiction and channel, verbatim. |

---

## 8. Pre-send human review checklist

The skill produces this checklist; **a person completes it.** No item may be pre-ticked by the agent.

**Truth**

- [ ] Every specific sentence in the body appears in `Personalization facts` with a source URL (R10.4.2).
- [ ] Every cited URL opens and still says what the draft says it says.
- [ ] No sentence is stronger than its quote (`references/evidence-policy.md` §7.1).
- [ ] MOQ, certifications, export markets, capacity and exclusivity appear only where evidenced; everything else is asked about, not asserted.
- [ ] No demand claim without a cited `rfq_id` whose status is `qualified` / `matching` / `proposal_open`, with that status and date stated (R10.4.3, INV-34).
- [ ] No claim about a third party the recipient could check and find false.

**People and channel**

- [ ] The recipient channel is a company-level channel the company published (BUILD-CONTRACT §3.6).
- [ ] No named individual, personal email, direct line or personal social handle appears anywhere in the draft (INV-31).
- [ ] The channel was re-verified as live at the time of review, not only at discovery.

**Jurisdiction**

- [ ] `Compliance checks` reflects the recipient's country, and `references/compliance-notes.md` was consulted for it.
- [ ] Where an advertising label or opt-out is required or unknown, the `Required legal notices` block was appended verbatim from `templates/legal_notices.md`, or the no-block-on-file line is present and someone is obtaining the wording (R10.4.6).
- [ ] A sender identity and a working reply route are present.

**Process**

- [ ] The state is still `READY_FOR_REVIEW`; nothing in this package set anything later (INV-09).
- [ ] Sending happens in the application layer, by a named human, after this checklist is complete.
- [ ] The approval decision is recorded outside the skill, with who approved it and when.

---

## 9. Annotated good draft

Built from the worked example in the repo-internal `SCORING-CONTRACT.md` §6.2 (a development
document that does not ship inside the installed package): RFQ #134 (private-label sunscreen
into the UAE, MOQ ceiling 3,000, ISO 22716 required, status `matching`) and Seller A, Hanbit
Cosmetics Co., Ltd. The `➊`-style markers are annotations for this page only and never appear in a
real draft.

```
Outreach Draft — Seller — Hanbit Cosmetics Co., Ltd.            ➊
Status: READY_FOR_REVIEW                                        ➋
Auto-send: false                                                ➋
Approval: required (human)
Recipient channel: corporate_email — export@hanbitcos.example     ➌
Language: Korean
Subject: (광고) RFQ #134 — UAE private-label sunscreen, MOQ 3,000 이하  ➍

Body
안녕하세요, TradeWith 소싱팀입니다.

TradeWith에 등록된 RFQ #134 (상태: matching, 기준일 2026-09-12)는 UAE 시장용 private label
선크림을 찾고 있으며, 최소주문수량 3,000개 이하와 ISO 22716 인증을 요구합니다.            ➎

귀사 OEM 페이지에 공개된 최소주문수량 2,500개와 ISO 22716 인증이 이 조건에 부합하여
연락드립니다. 영문 수출 페이지에 사우디아라비아·쿠웨이트·싱가포르·일본 수출 실적이
기재되어 있어 GCC 지역 대응 경험도 확인했습니다.                                       ➏

UAE MoHAP 제품 등록은 귀사 페이지 기준 "진행 중"으로 확인되며, 바이어가 등록 일정을
물을 가능성이 있습니다.                                                               ➐

RFQ #134에 제안서를 제출해 주시겠습니까? 단가 구간, 리드타임, 샘플 가능 여부를 회신에
포함해 주시면 바이어 측에 그대로 전달합니다.                                           ➑

감사합니다.
TradeWith 소싱팀 · partners@tradewith.example                                        ➒

Personalization facts
- MOQ 2,500 units per SKU, OEM programme — [https://hanbitcos.example/oem] (observed 2026-09-12)   ➓
- ISO 22716 listed on the certification page — [https://hanbitcos.example/quality] (observed 2026-09-12)
- Export markets: SA, KW, SG, JP — [https://hanbitcos.example/en/export] (observed 2026-09-12)
- UAE MoHAP registration in progress — [https://hanbitcos.example/en/export] (observed 2026-09-12)
- RFQ #134, status matching, as of 2026-09-12 — [https://app.tradewith.example/rfqs/134] (observed 2026-09-12)

Compliance checks
- Jurisdiction: Korea — direct-marketing review: required                              ⓫
- Advertising label / opt-out required: yes
- Personal data used: none (company-level channel only)
- Claims verified against evidence: yes

Required legal notices                                                                 ⓬
- 제목 맨 앞에 (광고) 표시
- 전송자: TradeWith (서울특별시 · 사업자등록번호는 발송 계정 설정값)
- 연락처: partners@tradewith.example
- 수신거부: partners@tradewith.example 로 회신하시면 이후 광고성 정보를 보내지 않습니다.
- 본 메일은 https://hanbitcos.example/en/contact 에 공개된 문의 채널로 발송되었습니다.

Reviewer checklist
- [ ] Every personalization fact traces to a cited source
- [ ] No claim that a buyer is "currently looking" unless an RFQ id is cited
- [ ] Any demand claim cites an `rfq_id` whose status is qualified / matching / proposal_open
- [ ] MOQ, certifications, exclusivity and export markets stated only where evidenced
- [ ] Contact channel is an official company channel
- [ ] Jurisdiction rules confirmed before sending

Next action: human review, then APPROVED_FOR_OUTREACH in the application layer          ⓭
```

**Why it passes**

| Marker | What it does right |
|---|---|
| ➊ | `{{side}}` is `Seller`; the company name is written in full and never truncated (`references/output-format.md` §10.5). |
| ➋ | Lines 2 and 3 are the literal status and auto-send lines, in order (R10.4.1, INV-09). |
| ➌ | A company-level channel the seller published. No named individual anywhere (INV-31). |
| ➍ | The subject states the actual request. No urgency, no "quick question". It opens with the `(광고)` marker because the `KR.corporate_email` block at ⓬ mandates it (R10.4.6). |
| ➎ | The demand claim is permitted because a real `rfq_id` is cited **and** its status and `as_of` date are stated in the body (R10.4.3, INV-34). |
| ➏ | Three specific facts, each at exactly the strength of its source, each appearing below with a URL (R10.4.2). |
| ➐ | `registration_status: in_progress` is written as "진행 중", never as registered — and it is surfaced as the buyer's likely question rather than hidden. |
| ➑ | One CTA: submit a proposal against a named RFQ. Not "sign up" (R10.4.4). |
| ➒ | A real sender identity and a working reply route. |
| ➓ | Every personalization line carries a click-ready URL and the observation date (R10.4.2). |
| ⓫ | Jurisdiction is the **recipient's** (Korea, seller-side), resolved against `references/compliance-notes.md`. |
| ⓬ | The label/opt-out requirement is `yes`, so the `KR.corporate_email` block of `templates/legal_notices.md` (`status: required`) is copied **verbatim** with its tokens filled — never summarised, never paraphrased. The `(광고)` marker it mandates is why ➍ carries it too (R10.4.6). |
| ⓭ | Terminates at review. Nothing in this artifact advances the state (INV-09, INV-37). |

---

## 10. Annotated bad draft

```
EXAMPLE ONLY — NON-COMPLIANT DRAFT — DO NOT SEND, DO NOT COPY
Recipient: [named individual] — [guessed personal address]                         ✗1
Subject: Quick question — buyer waiting for your sunscreen (24h)                   ✗2

Hi [named individual],                                                             ✗1

I saw on your LinkedIn profile that you handle exports at Hanbit.                  ✗3

We have a UAE distributor currently looking for exactly your sunscreen, and        ✗4
several other buyers are waiting for a supplier like you. Only 3 supplier slots
remain for the GCC this quarter, so please reply within 24 hours.                  ✗5

Since your MOQ is around 1,000 units and you're ISO 22716 and FDA approved, you    ✗6
already qualify. You also export to the UAE, so registration won't be an issue.    ✗7

Your competitors have already joined TradeWith. Sign up here to see the buyer:     ✗8
https://tradewith.example/signup                                                   ✗9

We can guarantee you orders in the first quarter.                                  ✗10

Unsubscribe by ignoring this email.                                                ✗11

Status: APPROVED_FOR_OUTREACH — queued for automatic send                          ✗12
```

**What is wrong with it**

| Marker | Violation | Rule broken |
|---|---|---|
| ✗1 | Addresses a named individual at a pattern-guessed personal address. | PRD 11.3, BUILD-CONTRACT §3.6, INV-11, INV-31 |
| ✗2 | Fabricated urgency in the subject line, and a fabricated demand claim. | PRD 11.1, test T05, INV-34 |
| ✗3 | Personalization built on a person's profile; the name would enter the artifact. | R3.6.1, INV-31 |
| ✗4 | Live-demand claim with no `rfq_id`, no status, no date. | R10.4.3, INV-34 |
| ✗5 | Invented scarcity and an invented deadline. | PRD 11.1, §7.1 above |
| ✗6 | MOQ guessed (`moq` was `"unknown"`); "ISO 22716" asserted with no certification evidence item; "FDA approved" is not a thing for cosmetics. | PRD EVID-05, BUILD-CONTRACT §14.5, SCORING-CONTRACT §2.5 rule 1 |
| ✗7 | Asserts an export market not in `export_markets`, then draws a regulatory conclusion from it. | PRD EVID-05; `references/compliance-notes.md` §5 |
| ✗8 | Unverifiable claim about third parties, used as social pressure. | §7.1 above |
| ✗9 | CTA is "sign up", and it gates the value behind registration. | PRD OUT-03, R10.4.4 |
| ✗10 | Guarantees a commercial outcome the platform does not control. | §7.1 above |
| ✗11 | Fake opt-out. Korea requires a real opt-out method and an advertising label. | PRD OUT-06, R10.4.6, `references/compliance-notes.md` §4 |
| ✗12 | Sets a state after `READY_FOR_REVIEW` and implies automatic sending. | PRD 11.2, BUILD-CONTRACT §9.2, INV-09, INV-37 |

Also missing entirely: the `Status` / `Auto-send` lines (R10.4.1), the whole
`Personalization facts` block (R10.4.2), `Compliance checks`, the reviewer checklist, and any
sender identity. Twelve violations in fifteen lines is not an exaggeration of the failure mode —
it is what an unconstrained model produces when asked for "a persuasive cold email".

---

## 11. Failure modes and what to do instead

| Situation | Do this |
|---|---|
| A personalization fact cannot be traced to an `evidence_id` | Delete the sentence, re-render, set `Claims verified against evidence: yes` only when the remaining body is clean. |
| No qualifying RFQ, but the operator wants a seller draft | Use the RFQ-absent variant. Offer capability registration, claim nothing about demand. |
| The only contact route is a personal address | No draft. Report `contact_channels` as `none published` and recommend the company's own form if one appears later. |
| The record is `stale` or `operational_status` is `closed` / `unreachable` | No draft. `HF-06` already removed it from matching; outreach follows. |
| The operator asks for 200 drafts | One run produces at most `config.output.max_outreach_drafts_per_run` drafts — the PRD 11.1 bulk guardrail, and the same per-call cap the TradeWith adapter enforces. Draft the reviewed short list, one per recipient, and say plainly that bulk generation is out of scope. |
| The jurisdiction has no block in `templates/legal_notices.md` | Emit the literal `- No notice block on file for … — obtain wording before sending` line and leave the draft at `READY_FOR_REVIEW` (R10.4.6). |
| The operator asks the skill to send | Hand over the draft and the checklist; state that sending is an application-layer capability requiring human approval. Offer no workaround. |

---

## 12. Related pages

| Page | What it owns |
|---|---|
| `templates/buyer_outreach.md` | The buyer-side draft skeleton |
| `templates/seller_outreach.md` | The seller-side skeleton, RFQ-present and RFQ-absent variants |
| `templates/legal_notices.md` | Per-jurisdiction × per-channel notice blocks, keyed `{{country_alpha2}}.{{channel_type}}` |
| `references/evidence-policy.md` | What may be claimed at all, and how strongly |
| `references/compliance-notes.md` | Jurisdictional caveats, data minimization, escalation |
| `references/data-contract.md` | Field names, contact-channel vocabulary, rendering rules |
