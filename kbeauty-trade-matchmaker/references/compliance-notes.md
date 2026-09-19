# Compliance Notes

> ## This is not legal advice.
>
> This page is an **operational guardrail** for a research-and-drafting skill. It exists to make the
> agent stop and flag, not to decide anything. It does not establish that any message may lawfully be
> sent, that any company may lawfully be contacted, or that any product may lawfully be placed on any
> market. Rules change, differ by member state and by channel, and turn on facts this skill cannot
> see. **Every item below must be re-verified against current primary sources and, where it matters,
> with qualified counsel in the relevant jurisdiction, before anything is sent or relied on.**
>
> Korean gloss: 이 문서는 법률 자문이 아니라 운영상 가드레일입니다. 발송 전에는 반드시 최신 규정 확인과 법률 검토가 필요합니다.

Status of the content on this page: compiled **2026-09-12**, for the v0.1.0 package (current package: see `SKILL.md`), **except** the
India (IN), Indonesia (ID) and Türkiye (TR) rows of §4.1 and the four schemes added to §5.1 for
those markets, which were compiled **2026-09-19**. Treat every row as *"this is the question to
ask"*, never as *"this is the answer"*.

---

## 1. What this page does and does not do

| Does | Does not |
|---|---|
| Tells the agent **which** jurisdictional question applies to a draft | Answer that question |
| Sets the `Compliance checks` block values to `required` / `not_required` / `unknown` | Conclude that sending is lawful — a `not_required` still means "review this" |
| Names the market-entry schemes that matter for **matching**, and what each implies for a seller's `compliance_readiness` score | Assess whether a specific product is compliant, registrable, or safe |
| Points at `templates/legal_notices.md` for notice wording | Draft legal wording of its own |
| Says when to escalate to a human lawyer (§6) | Replace that escalation |

This restates BUILD-CONTRACT §14 item 6: this page flags **that** a jurisdictional check is needed;
it never concludes that sending is lawful. PRD 3.2 puts "법률 자문을 자동으로 확정하는 기능" out of
scope explicitly.

**Default when in doubt: `unknown`.** An `unknown` in the `Compliance checks` block routes the draft
to human review with the question attached, which is the correct behaviour. Guessing `not_required`
to make a draft look clean is a safety failure.

---

## 2. Retrieval boundaries — robots, ToS, and the no-bypass rule

### 2.1 The rule

**Never bypass an access control.** No CAPTCHA solving, no login automation, no paywall
circumvention, no `robots.txt` override, no rate-limit evasion, no scraping a site that forbids it in
its terms (PRD 3.2, PRD 11.1, BUILD-CONTRACT §14.3, INV-12).

Korean gloss: robots.txt·이용약관·로그인·페이월·CAPTCHA를 우회하지 않는다. 막히면 그 사실은 `"unknown"`으로 남긴다.

### 2.2 What that means mechanically

- `evidence.retrieval_method` is restricted to `web_search`, `page_fetch`, `sitemap`,
  `internal_api`, `manual_entry`. There is no sixth value, and no headless-browser automation ships
  in this package.
- If a page cannot be read without bypassing something, **the fact stays `"unknown"`** and the
  criterion takes its unknown penalty. That is the designed outcome, not a failure
  (`references/evidence-policy.md` §2).
- A directory whose terms forbid automated collection may still be used the way a person uses it:
  open the page a search result points at, read it, cite it. Do not enumerate it, do not page
  through it programmatically, do not mirror it.
- Respect `Retry-After` and back off on 429/503. Being rate-limited is a signal to slow down, never
  to rotate an identifier.
- Login-walled content (including most of LinkedIn beyond a public company page) is out of reach.
  Cite only what is publicly visible.
- Before leaning on any directory as a recurring source, someone must read its terms of use once and
  record the outcome. PRD 21 Open Question 5 leaves this process undecided at v0.1.0 — until it is
  decided, treat an unreviewed directory as tier 4 and prefer the company's own site.

### 2.3 Archives and caches

An archived or cached copy is legitimate evidence of what a page *used to say*, and must be marked
`stale: true` with the archive URL as `source_url`. It is **never** evidence of the current state,
and it is never a way around a live page that blocks access.

---

## 3. Data minimization (PRD 11.3)

### 3.1 Company first, always

| Store | Do not store |
|---|---|
| Company-level channels only: `partnership_form`, `wholesale_form`, `form`, `contact_page`, `corporate_email` (role address on the company domain), `phone` (switchboard), `linkedin` (company page), `messenger` (official company channel), `other` | A named individual's email, direct-dial number, personal mobile, personal social handle, or photo |
| The claim, the source URL, the observation time, a short quote | Whole pages, scraped profiles, unnecessary personal detail |
| A **role** where the role genuinely matters ("export manager", "brand partnerships team"), with its evidence id | The person occupying it |

**Pattern-guessed addresses are forbidden outright.** `first.last@domain` is never stored even when
it is obviously correct. No email-pattern generator, no personal-phone extraction, no bulk contact
enumeration exists in this package, and none may be added (BUILD-CONTRACT §14.2, INV-11).

### 3.2 The rule covers free text too

R3.6.1 / INV-31: a named individual must not appear in `notes[]`, `quote_or_summary`,
`rationale[].statement`, `risks[].statement`, an outreach draft, or a test fixture. Free-text fields
are policed exactly like structured ones. If a quote you want to cite contains a person's name,
redact to the role and record it as a summary, not a verbatim quote
(`references/evidence-policy.md` §7).

### 3.3 When a named business contact is genuinely necessary

PRD 11.3 allows the minimum, with a source — but v0.1.0's schemas deliberately give it nowhere to
live, so in practice: **do not**. If an operator insists, that is a product decision (PRD 21 Open
Question 4), not something the agent may decide mid-run. Escalate (§6).

### 3.3a Exported files carry company-level data only

`scripts/export_leads.py` (data-contract §9.6) is the one place this package writes leads into a file
meant for another system, so the minimisation rule is enforced there by code, not by habit. A generic
CSV carries only the stored company-level channels, with every channel `label` dropped and every
`corporate_email` that is not a role mailbox on the company's own domain withheld, and every LinkedIn
member profile withheld. A TradeWith import file carries no contact field at all — no name, phone or
email, not even a role mailbox — no LinkedIn member profile, and no free-text notes, because
TradeWith shows the non-contact fields to sellers unmasked. Every exported text value, URLs included, passes the
personal-data scan; a hit refuses the whole export rather than silently dropping the value. On
import TradeWith sends `companyName`, `industry`, `category`, `productsSummary` and `country` to its
embedding provider — company-level fields only.

### 3.4 Retention and purpose

- Store a claim because it feeds a score or a match rationale, not because it was there.
- Anything stored must be re-verifiable by a human from the `source_url` alone.
- Where data-subject rights apply (any record containing personal data — which, under this policy,
  should be none), the application layer owns access, correction and deletion. This skill holds no
  durable store of its own.

---

## 4. B2B direct-marketing checklist, by jurisdiction

**Read §4.3 before using this table.** It is a prompt-list of questions, compiled 2026-09-12 (the
IN, ID and TR rows 2026-09-19), not a compliance determination. The "what this means for a draft" column maps onto the `Compliance checks`
block in the `references/output-format.md` §10.4 envelope and onto the `{{country_alpha2}}.{{channel_type}}` keys in
`templates/legal_notices.md`.

### 4.1 The table

| Jurisdiction | Primary instruments to check | Consent posture for unsolicited B2B email, as commonly understood | What this means for a draft |
|---|---|---|---|
| **Korea (KR)** — 정보통신망법 | 정보통신망 이용촉진 및 정보보호 등에 관한 법률 §50 및 시행령; 개인정보 보호법 | **Opt-in leaning.** 영리목적 광고성 정보의 전자적 전송에는 원칙적으로 사전 동의가 필요하며, 거래관계를 통해 직접 수집한 연락처에 대한 예외가 좁게 존재한다. 법인 대표 주소(info@ 등)에의 적용 범위는 다툼의 여지가 있으므로 보수적으로 본다. | Set `direct-marketing review: required` and `Advertising label / opt-out required: yes`. Expect a **(광고)** label in the subject line, sender identity and contact details in the body, and a working 수신거부 method. Use the `KR.corporate_email` block from `templates/legal_notices.md` verbatim; never improvise the wording. Prefer the company's own 문의/파트너 form over email. |
| **EU / EEA** | GDPR Art. 6(1)(f) legitimate interest + Art. 13/14/21; ePrivacy Directive 2002/58 Art. 13 as transposed **per member state** | **Member-state dependent.** ePrivacy Art. 13 protects "natural persons"; several member states extend it to legal persons. Germany (UWG §7) requires prior consent for advertising email including B2B, with a narrow existing-customer exception; Italy extends opt-in to legal persons; France (CNIL) permits B2B on an opt-out basis where the message relates to the recipient's professional function. A role address at a company can still be personal data. | `direct-marketing review: required` for every EU/EEA recipient. Resolve the **specific member state** before drafting, not after. If a legitimate-interest basis is claimed, someone must have done and recorded a balancing test — the agent never asserts one. `Advertising label / opt-out required: yes` unless a country block says otherwise. Identify the sender, state the source of the contact detail, and include an objection/opt-out route. |
| **United Kingdom (UK)** | PECR reg. 22 and reg. 23; UK GDPR | **Corporate subscribers are outside PECR reg. 22's consent rule** as commonly understood — but sole traders and unincorporated partnerships are treated as individual subscribers, reg. 23 identification and opt-out duties apply to **all** recipients, and UK GDPR still applies to any business contact data that identifies a person. | `direct-marketing review: required`. `Advertising label / opt-out required: yes` — identification and a working opt-out are the safe default even where consent is not. Confirm the recipient is an incorporated entity before relying on the corporate-subscriber position; a sole trader flips the analysis. |
| **UAE and GCC (AE, SA, KW, QA, BH, OM)** | UAE Federal Decree-Law 45/2021 (PDPL) and its implementing instruments; TDRA rules on unsolicited electronic communications; KSA PDPL (SDAIA) and its regulations; equivalent local rules per state | **Consent-leaning and in flux.** Direct marketing generally requires a lawful basis plus a clear opt-out; unsolicited commercial messaging to local numbers and addresses is regulated separately from data protection. Implementing regulations have moved repeatedly — verify the current text, not a summary. | `direct-marketing review: required`, `Advertising label / opt-out required: unknown` unless a current, checked block exists. Strongly prefer the company's own published partnership or distributor-application form over cold email. Do not send to phone numbers from this workflow at all. |
| **United States (US)** | CAN-SPAM Act, 15 U.S.C. §7701 et seq. and 16 CFR Part 316; state law (e.g. California) | **Opt-out regime.** No prior consent is required for commercial email, including B2B, but header and subject lines must not be deceptive, the message must be identifiable as an advertisement where applicable, a valid physical postal address is required, and opt-out requests must be honoured — commonly cited as within 10 business days. | `direct-marketing review: required` (the duties are real even without consent). `Advertising label / opt-out required: yes`. The draft needs a sender postal address and an opt-out route; the subject line must describe the message honestly. |
| **Singapore (SG)** | PDPA (incl. Do Not Call provisions); Spam Control Act | **Split by channel.** DNC provisions attach to Singapore telephone numbers (voice, SMS, fax), not to email. Business contact information is treated differently from personal data under the PDPA. The Spam Control Act governs unsolicited commercial **electronic messages** sent in bulk, with labelling and unsubscribe duties. | `direct-marketing review: required`. `Advertising label / opt-out required: yes` for email sent at any scale — expect an `<ADV>`-style subject marker and a working unsubscribe facility, and confirm the current requirement. Never send to a Singapore phone number from this workflow. |
| **Japan (JP)** | 特定電子メールの送信の適正化等に関する法律 (特定電子メール法); 特定商取引法 | **Opt-in with a published-business-address exemption.** Prior consent is the rule, with a well-known exemption where the recipient has **publicly published** the address for business purposes and has not indicated refusal of advertising email. Required sender display and an opt-out route apply regardless. | `direct-marketing review: required`. Where the address was read from the company's own published inquiry page, record that page as the evidence for the exemption question — the evidence item is the compliance artifact. `Advertising label / opt-out required: yes`: include sender name and address and a working 配信停止 route. |
| **India (IN)** | Digital Personal Data Protection Act, 2023 (No. 22 of 2023) §3(b), §3(c)(ii), and the DPDP Rules, 2025; Information Technology Act, 2000 and the SPDI Rules, 2011; TRAI TCCCPR 2018 | **No email-specific statute, as commonly understood.** India has no CAN-SPAM/PECR analogue. TCCCPR 2018 reaches commercial **voice calls and SMS** through the access providers, the DLT platform and the preference (DND) register — its definitions do not cover email — and IT Act §66A was struck down in 2015. The DPDP Act reaches processing **outside** India that is connected with offering goods or services to data principals in India (§3(b)); its "publicly available" exemption (§3(c)(ii)) turns on **who** made the data public, not on whether it can be found. The Act's substantive obligations were notified on a phased timetable and the consent and rights provisions commence later than this row's compile date — check what is in force on your send date. Whether a pure role address (`info@`, `export@`) is personal data at all is unsettled: there is no business-contact carve-out in the Act and no guidance either way. | `direct-marketing review: required`, `Advertising label / opt-out required: unknown`. Use the `IN.corporate_email` block from `templates/legal_notices.md` verbatim. Resolve the DPDP commencement position **as of the send date**, not as of this page. Prefer the company's own distributor/partner form, and prefer a role address over anything that identifies a person — that is the cleanest available position, not a workaround. |
| **Indonesia (ID)** | Law No. 27 of 2022 on Personal Data Protection (UU PDP), incl. its lawful bases and extraterritorial reach; its implementing Government Regulation (GR 33/2026, promulgated July 2026, with a stated six-month run-in); Law No. 11 of 2008 (UU ITE) as amended by Law 19/2016 and Law 1/2024; GR 71/2019 on Electronic Systems and Transactions | **Consent-leaning, and the machinery is still arriving.** UU PDP's two-year transition is reported to have ended in October 2024, so treat the Law's obligations as applying and confirm that against the current text; it lists lawful bases besides consent. But the implementing regulation was promulgated only in mid-2026 with a run-in into 2027, and as at this row's compile date the dedicated supervisory institution the Law requires had **not** been established — so there is almost no enforcement practice to read, and a summary written a few months either side of this date will say something different. There is no Indonesian email-labelling statute of the Singapore `<ADV>` kind. Whether a corporate role address is personal data, and which lawful basis a foreign sender may rely on, are both unresolved here. | `direct-marketing review: required`, `Advertising label / opt-out required: unknown`. Use the `ID.corporate_email` block. Re-check the run-in date and whether the supervisory institution now exists **before sending** — this row deliberately states neither as settled. Strongly prefer the company's own partner or distributor form. |
| **Türkiye (TR)** | Law No. 6563 on the Regulation of Electronic Commerce §6; the Regulation on Commercial Communication and Commercial Electronic Messages (*Ticari İletişim ve Ticari Elektronik İletiler Hakkında Yönetmelik*), incl. the İYS (*İleti Yönetim Sistemi*) provisions; Law No. 6698 on the Protection of Personal Data (KVKK) | **Opt-out for merchants and tradesmen, as commonly understood.** Prior consent is the rule for commercial electronic messages, **but** the Regulation provides that prior consent is not sought where the recipient is a *tacir* (merchant) or *esnaf* (tradesman). That is not a free pass: as commonly understood the recipient's electronic address must be recorded on **İYS** before the message is sent and İYS checked for an exercised right of refusal (*ret hakkı*), and sender-identification and refusal-route duties apply to every message. **Unresolved: whether, and how, a sender with no establishment in Türkiye registers on İYS** — the system is built around Turkish-registered service providers, and this package has found no authority resolving the point for a foreign sender. | `direct-marketing review: required`, `Advertising label / opt-out required: yes`. Use the `TR.corporate_email` block. The *tacir*/*esnaf* position must be confirmed for the **specific** recipient before it is relied on, and the İYS question above is an escalation trigger (§6) in its own right, not a detail. Prefer the company's own *bayilik*/*distribütörlük başvurusu* form. |
| **Anywhere else / unresolved** | — | — | `direct-marketing review: unknown`, `Advertising label / opt-out required: unknown`, and the draft stops at human review with the question named. This is a valid, expected outcome. |

### 4.2 Channel matters as much as country

Risk falls sharply as you move down this list, in every jurisdiction above:

1. **The company's own partnership / distributor-application / wholesale form** — they built it to
   receive exactly this. Prefer it whenever it exists.
2. **A published role address** (`partners@`, `sourcing@`, `export@`) whose page states what it is for.
3. **A generic `info@`** with no stated purpose.
4. **Phone** — a different regulatory regime (do-not-call registries), and out of scope for drafting
   here beyond a call-prep note.
5. **Anything personal** — forbidden (§3).

`templates/legal_notices.md` is keyed `{{country_alpha2}}.{{channel_type}}` precisely because the
answer changes with the channel. `KR.partnership_form` and `KR.corporate_email` are different
questions.

### 4.3 How to use the table honestly

- It lists **instruments to check**, not conclusions. "As commonly understood" is doing real work in
  every row and is not a substitute for reading the current text.
- Dates matter: UAE/KSA implementing regulations, EU member-state transpositions and Korean
  enforcement guidance have all moved within the life of this document.
- Sub-national and sectoral rules exist and are not covered here.
- The **recipient's** jurisdiction drives the analysis — and the sender's may add duties of its own.
  A Korean sender mailing an EU buyer has both to consider.
- If the row and the facts disagree, the facts win and the answer is `unknown` plus escalation (§6).

---

## 5. Cosmetics market-entry compliance — what it means for matching

This section is about **matching quality**, not legal clearance: which regulatory facts change a
seller's fit for a destination market, how they must be evidenced, and where they land in the score.

### 5.1 The schemes

| Scheme | Market | What it actually requires (verify current text) | Canonical token / field | Evidence that counts |
|---|---|---|---|---|
| **EU CPNP notification + Responsible Person** | EU/EEA | Regulation (EC) 1223/2009: a Responsible Person established in the EU, a Product Information File, and CPNP notification **per product** before placing on the market | `CPNP` in `certifications`; the RP arrangement belongs in `regulatory_registrations` with `market` = the EU country | The company's own regulatory/export page naming CPNP and the RP arrangement; a certificate or notification reference |
| **UK SCPN** | UK | Post-Brexit the UK runs its own submission portal and requires a UK-based Responsible Person; an EU CPNP notification does **not** carry over | `regulatory_registrations` entry with `scheme: "UK SCPN"` | The company's own statement of a UK RP and SCPN submission |
| **US MoCRA / FDA facility registration** | US | MoCRA obligations include facility registration and product listing, plus responsible-person labelling duties | `FDA_MOCRA` (registration/listing) and `FDA_REGISTERED` (establishment registration) — **two different tokens** | An official page naming the registration. "FDA approved" is **not** a registration claim at all: cosmetics are not FDA-approved. Record a risk note (SCORING-CONTRACT §2.5 rule 1) |
| **UAE MoHAP / ESMA product registration** | AE | Product registration through the UAE health authority's system, typically requiring a locally licensed importer or agent and product-level dossiers | `regulatory_registrations` entry with `market: AE`, `scheme: "UAE MoHAP cosmetic product registration"`, `registration_status` | The seller's export page or a registration certificate. `in_progress` is written as in progress, never as registered |
| **China NMPA (ex-CFDA) filing/registration** | CN | Filing for general cosmetics and registration for special cosmetics, with a domestic responsible person; safety-assessment and (for some categories) testing obligations | `CFDA` is the canonical token (BUILD-CONTRACT §8.6); an `NMPA` original is preserved in `notes[]` | The company's own China page or a filing reference |
| **ASEAN Cosmetic Directive notification** | SG, MY, TH, ID, VN, PH, … | Per-country product notification on the ASEAN harmonised scheme, with a local notification holder | `regulatory_registrations` entry per market, `scheme` naming the country's authority | The seller's own statement per market; a notification number |
| **India CDSCO import registration** | IN | Cosmetics Rules, 2020 (under the Drugs and Cosmetics Act, 1940): as commonly read, rule 12 requires a cosmetic to be registered with the Central Licensing Authority before it is imported (confirm against the current rules). Application on **Form COS-1** through the CDSCO online (SUGAM) portal; the certificate issued is **Form COS-2**. The application is made by the manufacturer, **or** its authorised agent in India, **or** the importer in India, **or** an Indian subsidiary the manufacturer has authorised — so the registration normally identifies the Indian trade partner. Fees are per category / per variant / per manufacturing site, and the certificate is retained by a periodic retention fee rather than re-applied for. Separately: conformity to the Indian Standards named in the rules' Ninth Schedule where the product type is listed (as checked 2026-09-19, the regulator's compulsory-certification list showed **no** ISI-mark or CRS certification scheme for cosmetics), Legal Metrology (Packaged Commodities) Rules, 2011 declarations on the retail pack, the RC number on the unit-pack label, and an animal-testing prohibition | `regulatory_registrations` entry with `market: "IN"`, `scheme: "CDSCO cosmetic import registration (Form COS-2)"`, `registration_status` | The seller's own India/export page naming the registration, or an RC number. A statement that the seller's **Indian distributor** holds the registration is evidence about the distributor, not the seller — record it in `notes[]` (§5.3 rule 1) |
| **Indonesia BPOM notification** | ID | Pre-market **notification** (*notifikasi kosmetika*) to BPOM on the ASEAN Cosmetic Directive model, held by a **locally established notification holder** — the importer or local distributor, appointed in writing by the brand owner. A foreign company does not hold it directly, so the notification holder on a Korean brand's BPOM record **is** its Indonesian importer. Indonesian-language labelling applies | `regulatory_registrations` entry with `market: "ID"`, `scheme: "BPOM cosmetic notification (notifikasi kosmetika)"`, `registration_status` | The seller's own Indonesia page, a notification number, or the BPOM public product record. Naming the local holder is a company fact about that holder, never a personal one |
| **Indonesia mandatory halal** | ID | **Date-dependent — resolve against `--as-of`, never from memory.** Law No. 33 of 2014 on Halal Product Assurance, as amended, with the phase-in timed by its implementing Government Regulation (GR 42/2024 supersedes GR 39/2021). Cosmetics sit in a later phase than food and beverages, and as compiled the cosmetics date reported by regulator coverage and by multiple advisory summaries is **17 October 2026**. For matching, treat halal as a claim when `--as-of` is before the date in force, and as a likely market-entry condition a person must confirm when it is on or after it. Article 26 of the Law is the other half: a product made from or containing non-halal material is **exempt from certification** but must carry non-halal information on the label. Certification is issued by **BPJPH**, with inspection by an LPH and a fatwa step. A halal certificate issued abroad is recognised by **registering it with BPJPH**, and only where the issuing foreign body (LHLN) holds a mutual-recognition arrangement — **per body and per scope**: a Korean body recognised for food and slaughtering is not thereby recognised for cosmetics | `HALAL` in `certifications`, **with the certifying body named in `notes[]`** (§5.1 last row); the BPJPH-side registration, where the seller states one, is a `regulatory_registrations` entry with `market: "ID"` and `scheme` naming BPJPH | The certificate with its issuing body and scope, or BPJPH's own record. **The agent never adds `HALAL` to an RFQ's `required_certifications` because the destination is ID.** Doing so hard-filters sellers on a constraint the buyer never stated (`references/matching-rules.md` HF-04). Raise it as a `risks[]` entry and a question for the human: *"the destination market's own halal regime may apply on the delivery date — confirm whether HALAL should be a requirement"* |
| **Türkiye TİTCK notification** | TR | Cosmetics Law No. 5324 and the Cosmetic Products Regulation (*Kozmetik Ürünler Yönetmeliği*, Resmî Gazete 8 May 2023, No. 32184), aligned with the EU regime: a **responsible person established in Türkiye**, a product information file, and **notification through ÜTS** (*Ürün Takip Sistemi*) to TİTCK before the product is placed on the market. Turkish-language labelling applies. As under CPNP, the responsible person is normally the importer, so the ÜTS record identifies the Turkish trade partner | `regulatory_registrations` entry with `market: "TR"`, `scheme: "TİTCK cosmetic product notification (ÜTS)"`, `registration_status` | The seller's own Türkiye/export page naming the notification and the responsible-person arrangement. "We export to Türkiye" is not a notification (§5.3 rule 3) |
| **ISO 22716 (cosmetics GMP)** | Global baseline | A GMP standard for manufacture, control, storage and shipment. Widely treated as a de-facto entry requirement and frequently named in RFQs | `ISO22716`; `CGMP` and `GMP_KOREA` are the sibling baseline tokens | An official certification page with issuer or certificate number. A logo image alone can back a low-tier claim but can **never** set `certifications_verified: true` |
| **Halal / vegan / cruelty-free claims** | GCC, MY, ID, and claim-driven segments elsewhere | Scheme-specific certification by a recognised body; a halal claim in one market may not be recognised in another, and ingredient/process scope varies | `HALAL`, `VEGAN`, `CRUELTY_FREE` | The certifying body and scope must be named. "Halal-friendly", "no animal testing policy" and "plant-based" are marketing copy, not certifications |

### 5.2 What each implies for `compliance_readiness`

The seller `compliance_readiness` dimension (weight 15) is built from four criteria — the full
algorithm is described in the repo-internal `SCORING-CONTRACT.md` §2.4, which does not ship inside the
installed package; every point value is in `schemas/scoring.config.json`, which does. What matters operationally:

| Criterion | What it reads | Practical consequence |
|---|---|---|
| **S-CP1** Required certifications held | `seller.certifications` ∩ the RFQ's `required_certifications`, after `cert_token` normalisation | Inapplicable when the query names no required certifications. This is the criterion an RFQ's ISO 22716 line drives. |
| **S-CP2** Preferred certifications held | same against `preferred_certifications` | Inapplicable when the query names none. |
| **S-CP3** Destination-market registration | the `seller.regulatory_registrations` entry whose `market` equals the destination country | This is where CPNP, SCPN, MoHAP, NMPA and ASEAN notifications actually score. `registered` > `in_progress` > not registered. **Absent array = unknown; present-and-empty = checked, none found.** |
| **S-CP4** Baseline quality certification | `ISO22716` / `CGMP` first, then the other quality schemes | A Korean manufacturer with no named GMP scheme is a real gap, not a formality. |
| Adjustment: `certifications_verified_official` | `seller.certifications_verified == true` | Only an official, exhaustive certification page earns this. |
| Adjustment: `certification_claim_unverified` | any token whose **best** evidence is tier 4 or 5 | A directory checkbox is admissible evidence and still costs the seller points. |

And in the hard filter: **`HF-04` (required certifications) only applies when
`certifications_verified == true`.** An unverified certification list never causes a rejection — the
unknown is penalised, not fatal (INV-07, PRD test T04).

### 5.2a Local spellings of a certification are normalised by the **agent**, not by code

A halal certificate is printed in the market's own language: `Helal` (TR), `Sertifikat Halal` /
`Halal MUI` / `Halal BPJPH` (ID), `हलाल` (IN), `할랄` (KR). All of them are the canonical token
**`HALAL`**, and the agent writes that token while naming the **certifying body and the scope** in
`notes[]` with the evidence id — `"printed as 'Sertifikat Halal', issued by <body>, scope as stated
on the certificate"`. The same applies to any other local spelling of a token already in the
BUILD-CONTRACT §8.6 vocabulary.

**Do not add these spellings to `_common.normalize_certification` as code synonyms.** BUILD-CONTRACT
§12.2 makes a new certification synonym a **`score_version` bump**, because every stored score goes
stale the moment the token mapping changes. This package deliberately does not take that bump for a
spelling the agent can resolve while it is reading the page. If a synonym genuinely has to become
code, that is a versioned rubric change and a human decision, not a run-time one.

Korean gloss: `Helal`·`Sertifikat Halal`·`हलाल`은 모두 `HALAL` 토큰으로 기록하고, 인증기관과 범위는
`notes[]`에 적는다. 코드에 동의어를 추가하면 `score_version`을 올려야 하므로 하지 않는다.

### 5.3 Three rules that stop the most common errors

1. **Scope is not modelled in v0.1.0.** A certificate held by the seller's contract manufacturer,
   sister company, or a plant that does not make the requested product MUST NOT be recorded as the
   seller's (SCORING-CONTRACT §2.5 rule 5).
2. **Expiry counts.** A certificate whose stated expiry precedes `--as-of` is not held. Record a
   conflict or risk instead.
3. **A registration is per product and per market.** "We export to the EU" is not CPNP. "We have a
   UAE partner" is not a MoHAP registration. Record what the source says, at its own granularity.

### 5.4 What this section never does

It never says a product may be placed on a market, never assesses ingredient compliance or claim
substantiation, and never assesses labelling. Those need the actual formulation, the actual dossier,
and a regulatory professional.

---

## 6. Escalation — when to demand real legal review

Stop and route to a human with legal authority when **any** of these holds. Name the trigger in the
draft's `notes` and leave the run at `READY_FOR_REVIEW`.

**Outreach triggers**

- The recipient is in the EU/EEA or Korea and the intended channel is email rather than the company's
  own form.
- `direct-marketing review` resolves to `unknown`, or `Advertising label / opt-out required` is
  `unknown`.
- `templates/legal_notices.md` has **no block** for the `{{country_alpha2}}.{{channel_type}}` pair —
  the fallback line is present and someone must obtain the wording (R10.4.6).
- More than a handful of recipients in one jurisdiction in one cycle: volume changes the analysis,
  and it also changes how a regulator reads it.
- Anyone proposes to use a named individual's contact details, or to keep a contact whose source is
  not recorded (§3.3).
- The recipient, or anyone at the recipient, has asked not to be contacted. Honour it immediately and
  permanently, in the application layer; no draft is produced.
- The draft would assert anything about exclusivity, territory rights, regulatory status, or a
  guaranteed commercial outcome (`references/outreach-guidelines.md` §7).

**Data triggers**

- Any personal data has entered a record, a note, a quote or a fixture, however incidentally.
- A data-subject request, a takedown request, or a complaint reaches the operator.
- A source's terms of use appear to forbid the use being made of it, or an access control was
  encountered (§2).

**Market-entry triggers**

- A match's viability depends on a regulatory status that is `in_progress` or `unknown` and the
  buyer's timeline does not accommodate the gap.
- A seller's certification evidence conflicts across sources, or an expiry is at or near `--as-of`.
- The destination market is one this page does not cover.
- Any statement is about to be made to a buyer about whether a product **may** be sold somewhere.

Escalation is cheap, and a flagged draft is a normal deliverable. A wrongly-sent message is neither.

---

## 7. Quick reference — filling the `Compliance checks` block

```
Compliance checks
- Jurisdiction: {{country_name}} — direct-marketing review: {{required|not_required|unknown}}
- Advertising label / opt-out required: {{yes|no|unknown}}
- Personal data used: none (company-level channel only)
- Claims verified against evidence: {{yes|blocked}}
```

| Field | How to resolve it |
|---|---|
| `Jurisdiction` | The **recipient's** country, from `record.country`, rendered with its display name. `unknown` country ⇒ `unknown` review. |
| `direct-marketing review` | `required` for every jurisdiction in §4.1 as currently written; `unknown` for anything else. `not_required` is reserved for a case someone has actually cleared and recorded — the agent does not assign it on its own. |
| `Advertising label / opt-out required` | From §4.1's row for that country **and** §4.2's channel. `unknown` whenever either half is unresolved — which then triggers the `Required legal notices` block under R10.4.6. |
| `Personal data used` | Always the literal `none (company-level channel only)`. If that line cannot be written truthfully, there is no draft (§3). |
| `Claims verified against evidence` | `yes` only when every specific sentence traces to an `evidence_id` with a URL; otherwise `blocked`, and the untraceable sentences are removed before re-rendering. |

---

## 8. Related pages

| Page | What it owns |
|---|---|
| `references/evidence-policy.md` | Fact / inference / unknown, source tiers, staleness, conflicts, quoting |
| `references/outreach-guidelines.md` | Draft rules, forbidden phrasing, the reviewer checklist |
| `templates/legal_notices.md` | The actual per-jurisdiction × per-channel notice wording |
| `references/matching-rules.md` | `HF-00..HF-08`, including `HF-04` required certifications |
| `references/qualification-rubric.md`, `schemas/scoring.config.json` | `compliance_readiness`, and certification-token normalisation |

---

> ## Closing reminder: this is not legal advice.
>
> Nothing on this page clears a message for sending or a product for a market. It tells the agent
> which question to raise and where to stop. Re-verify every instrument named here against its
> current text, and obtain qualified legal advice in the relevant jurisdiction before acting.
>
> Korean gloss: 다시 한 번, 이 문서는 법률 자문이 아닙니다. 실제 발송·진출 전에는 관할 법률 전문가의 검토를 받으십시오.
