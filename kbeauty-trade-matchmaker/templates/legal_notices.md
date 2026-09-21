# templates/legal_notices.md — per-jurisdiction × per-channel notice blocks (PRD OUT-06)

> ## Verify before sending. This page is not legal advice.
>
> Each block below is **draft notice wording**, compiled 2026-09-12 — the `IN.*`, `ID.*` and `TR.*`
> blocks 2026-09-19 — from the instruments named in `references/compliance-notes.md` §4.1. It is not a clearance, not a determination, and not a
> substitute for reading the current text of the instrument or for advice from qualified counsel in
> the recipient's jurisdiction. A block tells the reviewer *which elements a notice is expected to
> carry*; the reviewer confirms the wording before anything leaves the application layer.
>
> Korean gloss: 이 페이지는 법률 자문이 아닙니다. 각 블록은 검토자가 확인해야 할 고지 요소의
> 초안 문구이며, 발송 전 관할 법령의 현행 조문과 전문가 검토가 필요합니다.

---

## 1. How a block is used

`references/outreach-guidelines.md` R10.4.6 is the binding rule:

1. Resolve `Advertising label / opt-out required` from `references/compliance-notes.md` §4.1 (country)
   and §4.2 (channel).
2. When it is `yes` or `unknown`, look up the key `{{country_alpha2}}.{{channel_type}}` in §3 below.
3. A block exists → copy its fenced body **verbatim**, unedited, under the draft's
   `Required legal notices` heading. Never paraphrase it, never shorten it, never translate it.
4. No block exists → render exactly this line and nothing else, and leave the draft at
   `READY_FOR_REVIEW`:

   ```text
   - No notice block on file for {{country_name}} / {{channel_type}} — obtain wording before sending
   ```

5. When the flag is `no`, render `- Not required for this jurisdiction and channel` and keep the
   heading, so every draft stays diff-comparable.

**The drafts never invent legal wording.** A jurisdiction with no block is a normal, expected
outcome and an escalation trigger (`references/compliance-notes.md` §6), not a gap to improvise over.

### 1.1 `status` values

| `status` | Meaning |
|---|---|
| `required` | §4.1 records the label/opt-out duty as `yes` for this pair. Use the block. |
| `unknown` | The duty is unresolved for this pair. Use the block **and** escalate — the draft stops at human review with the question named. |
| `not_required` | Reserved. Only a human who has actually cleared and recorded the analysis may assign it; nothing in this package assigns it, and no block below carries it (`references/compliance-notes.md` §7). |

### 1.2 Channel families

Blocks are keyed on the BUILD-CONTRACT 3.6 channel vocabulary. Where a country has a
`partnership_form` block, it also governs `wholesale_form`, `form` and `contact_page` — a submission
the company itself invited, on its own page. `corporate_email` always has its own block because it
is unsolicited electronic messaging and a different question (`compliance-notes.md` §4.2).

`phone`, `linkedin` and `messenger` have **no blocks in v0.1.1**: telephone marketing sits under
separate do-not-call regimes this package does not cover, and the other two are platform-terms
questions rather than notice-wording questions. They fall to the §1 step-4 fallback line every time.

---

## 2. Key index

| Key | `status` | Channel family |
|---|---|---|
| `KR.corporate_email` | `required` | role address on the company domain |
| `KR.partnership_form` | `unknown` | company's own form / contact page |
| `GB.corporate_email` | `required` | role address on the company domain |
| `GB.partnership_form` | `unknown` | company's own form / contact page |
| `US.corporate_email` | `required` | role address on the company domain |
| `US.partnership_form` | `unknown` | company's own form / contact page |
| `JP.corporate_email` | `required` | role address on the company domain |
| `SG.corporate_email` | `required` | role address on the company domain |
| `AE.corporate_email` | `unknown` | role address on the company domain |
| `AE.partnership_form` | `unknown` | company's own form / contact page |
| `DE.corporate_email` | `unknown` | role address on the company domain |
| `FR.corporate_email` | `required` | role address on the company domain |
| `IT.corporate_email` | `unknown` | role address on the company domain |
| `IN.corporate_email` | `unknown` | role address on the company domain |
| `IN.partnership_form` | `unknown` | company's own form / contact page |
| `ID.corporate_email` | `unknown` | role address on the company domain |
| `ID.partnership_form` | `unknown` | company's own form / contact page |
| `TR.corporate_email` | `required` | role address on the company domain |
| `TR.partnership_form` | `unknown` | company's own form / contact page |

Every other `{{country_alpha2}}.{{channel_type}}` pair — including every EU/EEA member state not
listed above — has no block and takes the §1 step-4 fallback line. That is the designed behaviour,
not an omission to work around.

---

## 3. The blocks

### 3.1 `KR.corporate_email`

`status: required` · 정보통신망법 §50 · `compliance-notes.md` §4.1 Korea row

```text
- 제목 맨 앞에 (광고) 표시
- 전송자: {{sender_company_name}} ({{sender_company_registration_or_address}})
- 연락처: {{sender_contact_channel}}
- 수신거부: {{opt_out_route}} 로 회신하시면 이후 광고성 정보를 보내지 않습니다.
- 본 메일은 {{recipient_channel_source_url}} 에 공개된 문의 채널로 발송되었습니다.
```

### 3.2 `KR.partnership_form`

`status: unknown` · a submission the company's own page invited

```text
- 본 문의는 {{recipient_channel_source_url}} 의 파트너·입점 문의 양식을 통해 제출되었습니다.
- 문의 주체: {{sender_company_name}} — {{sender_contact_channel}}
- 광고성 정보 전송 해당 여부는 확인되지 않았습니다. 발송 전 검토 필요.
```

### 3.3 `GB.corporate_email`

`status: required` · PECR reg. 23 identification and opt-out · `compliance-notes.md` §4.1 UK row

```text
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- This is a business sourcing enquiry from {{sender_company_name}}.
- Contact us at {{sender_contact_channel}}.
- To opt out of further messages, reply to this message with "opt out", or use
  {{opt_out_route}}. We will stop contacting this address.
- We obtained this address from {{recipient_channel_source_url}}.
- Confirm the recipient is an incorporated entity: a sole trader or unincorporated
  partnership is an individual subscriber and the consent analysis changes.
```

### 3.4 `GB.partnership_form`

`status: unknown` · a submission the company's own page invited

```text
- Submitted through the partner enquiry form published at {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}} — {{sender_contact_channel}}
- Whether PECR reg. 23 duties attach to a form submission is unresolved here. Review before use.
```

### 3.5 `US.corporate_email`

`status: required` · CAN-SPAM · `compliance-notes.md` §4.1 US row

```text
- Sender: {{sender_company_name}}
- Postal address: {{sender_company_postal_address}}
- This is a commercial message about sourcing and supply.
- To stop receiving messages at this address, use {{opt_out_route}}. Opt-out requests
  are honoured within 10 business days.
- The subject line and headers describe this message accurately.
```

### 3.6 `US.partnership_form`

`status: unknown` · a submission the company's own page invited

```text
- Submitted through the partner enquiry form published at {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}}, {{sender_company_postal_address}} — {{sender_contact_channel}}
- Whether CAN-SPAM duties attach to a form submission is unresolved here. Review before use.
```

### 3.7 `JP.corporate_email`

`status: required` · 特定電子メール法 · `compliance-notes.md` §4.1 Japan row

```text
- 送信者: {{sender_company_name}}
- 所在地: {{sender_company_postal_address}}
- 連絡先: {{sender_contact_channel}}
- 配信停止: {{opt_out_route}} からお手続きいただけます。
- 本メールは {{recipient_channel_source_url}} に貴社が公開されている業務用アドレス宛に
  お送りしています。
```

### 3.8 `SG.corporate_email`

`status: required` · Spam Control Act labelling and unsubscribe · `compliance-notes.md` §4.1 SG row

```text
- <ADV> {{subject_line}}
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- To unsubscribe from further commercial messages, use {{opt_out_route}}.
- We obtained this address from {{recipient_channel_source_url}}.
- No Singapore telephone number is contacted from this workflow.
```

### 3.9 `AE.corporate_email`

`status: unknown` · UAE PDPL and TDRA rules · `compliance-notes.md` §4.1 UAE/GCC row

```text
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- This is a business sourcing enquiry. Contact us at {{sender_contact_channel}}.
- To stop receiving messages at this address, use {{opt_out_route}}.
- We obtained this address from {{recipient_channel_source_url}}.
- The current labelling and consent position for this jurisdiction and channel is
  unresolved. Confirm it against the instruments in force before this message is sent,
  and prefer the company's own partner form.
```

### 3.10 `AE.partnership_form`

`status: unknown` · the preferred UAE channel · `compliance-notes.md` §4.2

```text
- Submitted through the distributor or partner application form published at
  {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}} — {{sender_contact_channel}}
- No telephone number is contacted from this workflow.
```

### 3.11 `DE.corporate_email`

`status: unknown` · UWG §7 prior consent, including B2B · `compliance-notes.md` §4.1 EU/EEA row

```text
- Prior consent is required for advertising email in this jurisdiction, including B2B,
  with only a narrow existing-customer exception. If no recorded consent or recorded
  exception exists for this address, this draft is not usable on this channel — use the
  company's own partner form instead.
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- Contact: {{sender_contact_channel}}
- Objection / opt-out: {{opt_out_route}}
- Source of this contact detail: {{recipient_channel_source_url}}
```

### 3.12 `FR.corporate_email`

`status: required` · B2B opt-out basis where the message relates to the recipient's professional
function · `compliance-notes.md` §4.1 EU/EEA row

```text
- Expéditeur : {{sender_company_name}}, {{sender_company_postal_address}}
- Objet : demande de sourcing professionnelle liée à votre fonction.
- Contact : {{sender_contact_channel}}
- Opposition / désinscription : {{opt_out_route}}
- Origine de cette adresse : {{recipient_channel_source_url}}
```

### 3.13 `IT.corporate_email`

`status: unknown` · opt-in extended to legal persons · `compliance-notes.md` §4.1 EU/EEA row

```text
- Opt-in is understood to extend to legal persons in this jurisdiction. If no recorded
  consent exists for this address, this draft is not usable on this channel — use the
  company's own partner form instead.
- Mittente: {{sender_company_name}}, {{sender_company_postal_address}}
- Contatto: {{sender_contact_channel}}
- Opposizione / cancellazione: {{opt_out_route}}
- Origine dell'indirizzo: {{recipient_channel_source_url}}
```

### 3.14 `IN.corporate_email`

`status: unknown` · no email-specific statute; DPDP Act 2023 commencement is date-dependent ·
`compliance-notes.md` §4.1 India row

```text
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- This is a business sourcing enquiry about supply and distribution.
- Contact us at {{sender_contact_channel}}.
- To stop receiving messages at this address, reply with "opt out" or use {{opt_out_route}}.
  We will stop contacting this address.
- We obtained this address from {{recipient_channel_source_url}}.
- India has no email-specific anti-spam statute, and the commencement position of the
  Digital Personal Data Protection Act, 2023 changes with the send date. Confirm what is
  in force on the day this is sent.
- Confirm this address is a company role address and not an individual's. If it
  identifies a person, the data-protection analysis changes and this draft is not usable
  on this channel — use the company's own distributor or partner form instead.
```

### 3.15 `IN.partnership_form`

`status: unknown` · a submission the company's own page invited

```text
- Submitted through the distributor or partner enquiry form published at
  {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}} — {{sender_contact_channel}}
- Whether any notice duty attaches to a form submission in this jurisdiction is
  unresolved here. Review before use.
- No Indian telephone number is contacted from this workflow.
```

### 3.16 `ID.corporate_email`

`status: unknown` · Law 27/2022 (UU PDP) and UU ITE · `compliance-notes.md` §4.1 Indonesia row

```text
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- This is a business sourcing enquiry about supply and distribution.
- Contact us at {{sender_contact_channel}}.
- To stop receiving messages at this address, use {{opt_out_route}}.
- We obtained this address from {{recipient_channel_source_url}}.
- The lawful basis and any notice duty for unsolicited business email in this
  jurisdiction are unresolved here. Confirm them against the instruments in force, and
  the current status of the supervisory authority, before this message is sent, and
  prefer the company's own partner form.
```

### 3.17 `ID.partnership_form`

`status: unknown` · the preferred Indonesian channel · `compliance-notes.md` §4.2

```text
- Submitted through the distributor or partner application form published at
  {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}} — {{sender_contact_channel}}
- No Indonesian telephone number is contacted from this workflow.
```

### 3.18 `TR.corporate_email`

`status: required` · Law 6563 §6 and the Commercial Electronic Messages Regulation: sender
identification and a right of refusal · `compliance-notes.md` §4.1 Türkiye row

```text
- Sender: {{sender_company_name}}, {{sender_company_postal_address}}
- This is a commercial electronic message: a business sourcing enquiry about supply and
  distribution.
- Contact: {{sender_contact_channel}}
- Refusal / opt-out: reply with "opt out", or use {{opt_out_route}}. We will stop
  contacting this address, and we will not contact it again after a refusal.
- We obtained this address from {{recipient_channel_source_url}}.
- Prior consent is not sought for a recipient that is a merchant (tacir) or tradesman
  (esnaf), but that status must be confirmed for this recipient before the message is
  sent, and the message-registry (İYS) position must be checked for an exercised refusal.
- Unresolved: whether and how a sender with no establishment in Türkiye registers with
  İYS. Obtain local advice before sending on this channel, and prefer the company's own
  distributor application form.
```

### 3.19 `TR.partnership_form`

`status: unknown` · a submission the company's own page invited

```text
- Submitted through the distributor or dealership application form published at
  {{recipient_channel_source_url}}.
- Sender: {{sender_company_name}} — {{sender_contact_channel}}
- Whether the commercial-electronic-message duties of this jurisdiction attach to a form
  submission, and whether any İYS obligation is engaged, are unresolved here. Review
  before use.
- No Turkish telephone number is contacted from this workflow.
```

---

## 4. Tokens these blocks use

Every token is filled from the **sender's** configured identity or from an evidenced field on the
recipient record. A token that cannot be filled is a blocker: the draft stops at review with the
gap named, and the block is never shortened to hide it.

| Token | Filled from |
|---|---|
| `{{sender_company_name}}` | The operator's own configured sender identity |
| `{{sender_company_postal_address}}` | The operator's own registered postal address |
| `{{sender_company_registration_or_address}}` | The operator's business registration number or registered address |
| `{{sender_contact_channel}}` | The operator's own published reply route |
| `{{opt_out_route}}` | The operator's working opt-out mechanism, handled in the application layer |
| `{{recipient_channel_source_url}}` | The `source_url` of the evidence item that established the recipient's `contact_channels[]` entry |
| `{{subject_line}}` | The draft's own `Subject:` line |
| `{{country_name}}`, `{{channel_type}}` | The recipient record's `country` display name and the chosen channel type |

---

## 5. Related pages

| Page | What it owns |
|---|---|
| `references/compliance-notes.md` | §4.1 the jurisdiction table these blocks key off, §4.2 the channel ranking, §6 escalation |
| `references/outreach-guidelines.md` | R10.4.6, the rule that decides when a block is appended and what happens when none exists |
| `templates/buyer_outreach.md`, `templates/seller_outreach.md` | The `{{legal_notice_block}}` slot in the 10.4 envelope |

> Closing reminder: nothing on this page clears a message for sending. Re-verify every instrument
> named here against its current text, and obtain qualified legal advice in the relevant
> jurisdiction before acting.
