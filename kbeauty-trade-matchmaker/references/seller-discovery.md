# Seller Discovery Playbook (Korean manufacturers / OEM-ODM / brands)

Operational instructions for **Mode 2 — Seller Discovery** (PRD 5.2, 6.1, 10.2, 10.3, 15.2). Load this file when the request is "find Korean manufacturers / OEM-ODM / suppliers for `<product>` with `<MOQ / certification / export>` conditions". Korean gloss: 한국 셀러·제조사 발굴 실행 지침. Field names are those of `schemas/seller.schema.json`; claim keys and the query surface are defined in `references/data-contract.md`. Run the steps in order; stop when a §10 condition fires.

---

## 1. Non-negotiables

### 1.1 Source priority tiers (PRD 10.3)

| Tier | Source | `source_type` | `is_official` |
|---|---|---|---|
| 1 | The company's own site: 회사소개, 사업분야, 생산시설, 인증현황, 제품, 수출, 문의 | `official_site` | `true` |
| 2 | Government / trade-agency directories (KOTRA buyKOREA, KITA tradeKorea), industry associations (대한화장품협회 and peers), expo exhibitor lists, MFDS(식약처) and certification-body registries | `official_directory`, `trade_show`, `internal_record` | `true` only for the company's own submitted entry |
| 3 | The company's official LinkedIn / company-controlled social or blog channel | `social` | `true` |
| 4 | Reputable third-party directory, trade press (코스인, 뷰티누리 and peers), press release | `third_party` | `false` |
| 5 | Community posts, blogs, sourcing-agent aggregator pages — **supporting signal only** | `third_party`, `social` | `false` |

A sourcing agent's page listing a factory is **not** official for that factory. Confirm on the factory's own domain before writing a tier-1 claim.

### 1.2 Hard-forbidden behaviour (PRD 11.1) — no exceptions

| Forbidden | Instead |
|---|---|
| Inferring or harvesting personal emails / mobile numbers (영업 담당자 직통, `first.last@`) | Company-level channels only: `partnership_form`, `wholesale_form`, `form` (견적/제휴 문의 form), `contact_page`, `corporate_email` (role address), `phone` (대표번호), `linkedin`, `messenger` (official KakaoTalk channel), `other` |
| Naming an individual anywhere, quotes and `notes[]` included | Record the role only ("해외영업팀 / export sales team") with its evidence id |
| Bypassing login, CAPTCHA, paywall, robots.txt (incl. member-only 견적 portals) | Leave the fact `"unknown"` and note that it sits behind a gate |
| **Inventing MOQ, certifications, capacity, lead time, export markets or exclusivity** | `"unknown"` + a note. A penalised unknown is the correct outcome (PRD EVID-05, test T04) |
| Reading "GMP 공장" marketing copy as an `ISO22716` certificate | Certifications are never inferred: only a named certificate, certificate number, certificate image, or registry entry counts |
| Bulk automated outreach | Out of scope for this mode |

### 1.3 Three states, never two (BUILD-CONTRACT 3.2)

`"unknown"` / absent = not determined · `false` or `[]` = checked and demonstrably not the case (needs evidence **of the check**) · `true` / non-empty = evidenced. Korean sites publish little commercial detail, so **most MOQ, lead-time and capacity fields will legitimately be unknown** — expected, not a failure.

#### How "unknown" is spelled, per field — this is not uniform

`schemas/seller.schema.json` accepts the **literal string** `"unknown"` on some fields and rejects it on others. Writing it where the schema does not allow it is a validation failure, not a style issue: `validate_output.py` rejected 15 such fields in a live run over 12 companies.

| Field group | Unknown is written as | Fields |
|---|---|---|
| Quantities and tri-states | the literal `"unknown"` **or** the field absent — both legal | `moq`, `lead_time_days`, `monthly_capacity_units`, `oem_odm`, `private_label`, `brand_export`, `english_site`, `overseas_partner_signal`, `certifications_verified`, `country`, `company_type`, `operational_status` |
| **Arrays** | the field **absent**. The literal `"unknown"` is **not valid** — these are typed `array` with no unknown branch | `certifications`, `export_markets`, `export_regions`, `product_categories`, `product_forms`, `contact_channels`, `regulatory_registrations`, `excluded_markets`, `alias_domains` |

So for an array the three states are: **absent** = not determined · **present and empty `[]`** = checked and none found (needs evidence of the check) · **non-empty** = evidenced. Leaving an array out is the correct way to say "we did not find a certification page"; `[]` says "we read the certification page and it listed nothing", a different and much stronger claim. Everywhere below, and in §9's `★` marking, "evidence-backed or unknown — no third option" means *evidence-backed, or unknown **in the spelling this table gives for that field***.

---

## 2. Step 0 — build the query surface

| Operator says | Query-surface key | Value |
|---|---|---|
| `product="sunscreen"` | `product_categories` | `["sunscreen"]` (`sun_cream`/`sunblock`/`spf` → `sunscreen`) |
| `form="cream"` | `product_forms` | `["cream"]` (omit entirely if not requested → the criterion is *inapplicable*) |
| `oem_odm=true` | `commercial_model` | `"oem_odm"` (`private_label` / `branded` / `either` are the other values) |
| `max_moq=3000` | `max_moq`, `moq_unit` | `3000`, `"units"` — **always set the unit**; a `kg`/`pcs`/`set` catalogue mismatches forever otherwise |
| `certifications="ISO22716"` | `required_certifications` | `["ISO22716"]` (canonical tokens; `ISO 22716`/`ISO-22716` → `ISO22716`, `GMP`/`cGMP` → `CGMP`, `기능성화장품` → `KFDA_FUNCTIONAL`, `할랄` → `HALAL`, `NMPA` → `CFDA` with a note) |
| nice-to-have certs | `preferred_certifications` | e.g. `["HALAL","VEGAN"]` |
| destination of the eventual order | `destination_country`, `region_countries` | `"AE"`, `["AE","BH","KW","OM","QA","SA"]` — drives `S-EX1` and `S-CP3` |
| Korean supply only | `required_seller_countries` | `["KR"]` |
| order size | `quantity` | number or `{min,max}`; compared on its **max** |
| deadline | `max_lead_time_days` | number of days |
| always | `vertical` | `"K-Beauty"` |

Keys the operator did not state stay **absent**: an absent key imposes no constraint and its criterion reports `state: "inapplicable"`, not unknown. A bare Mode 2 run therefore omits `destination_country`, `region_countries` and `preferred_certifications` — see the warning in §11.

### Hard filters that seller **discovery** applies (R6.2.6)

Same predicates as matching, against the query surface. A candidate that fails one leaves the ranked list for `excluded[]` with `failed_rules[]`, and is never rendered under Top Candidates.

| Rule | Fires when | Unknown input |
|---|---|---|
| `HF-01` product category | the query names a category and the seller's **known** categories exclude it | pass + unknown penalty |
| `HF-02` commercial model | `commercial_model != "either"` and the matching seller flag is **explicitly `false`** | pass + unknown penalty |
| `HF-03` maximum MOQ | `max_moq` is a number and the seller's **confirmed** MOQ min exceeds it (T03). A stated open lower bound *above* the ceiling counts as confirmed — see §7 | pass + unknown penalty (T04) |
| `HF-04` required certifications | the required list is non-empty **and** `certifications_verified == true` | pass + unknown penalty |
| `HF-05` excluded destination market | destination set and `excluded_markets` non-empty | pass |
| `HF-08` seller country | required/excluded seller-country lists non-empty | pass + unknown penalty |

`HF-06` (operational status) and `HF-07` (lead-time deadline) are **not** applied in discovery: an unreachable company is still surfaced, flagged and ranked LOW (T07), and a discovery query carries no RFQ deadline. **An unknown never rejects** — absence of proof is not proof of absence.

---

## 3. Step 1 — the query expansion matrix

Run English **and** Korean. The Korean rows are what actually surface factories; the English rows surface the export-facing subset. PRD 10.2 seeds are rows 1–5.

| # | Query | Targets |
|---|---|---|
| 1 | `Korea {P} OEM ODM manufacturer` | export-facing OEM/ODM |
| 2 | `Korean cosmetics private label manufacturer MOQ` | private-label with published terms |
| 3 | `K-beauty manufacturer ISO22716 {P}` | certified makers |
| 4 | `"{expo name}" exhibitor cosmetics Korea` | tier-2 exhibitor lists — **run last**, see the §4 caveat |
| 5 | `site:{company-domain} OEM ODM export` | page sweep of a known domain |
| 6 | `화장품 OEM ODM 제조사` | core Korean OEM/ODM query — **highest yield** |
| 7 | `화장품 제조업 등록 자체 공장` | owns a registered factory (not a broker) |
| 8 | `화장품 책임판매업자` | marketer/brand-side registrants (usually **not** a factory) |
| 9 | `{P-ko} 제조 OEM` — e.g. `선케어 제조 OEM`, `선크림 OEM 제조사` | product-specific makers — **highest yield** |
| 10 | `화장품 최소주문수량 MOQ {P-ko}` | MOQ actually published |
| 11 | `화장품 수출 제조사 {destination-ko}` — e.g. `중동 수출` | export track record |
| 12 | `화장품 ISO22716 인증 제조` · `CGMP 화장품 제조` | certification evidence — **highest yield** |
| 13 | `기능성화장품 심사 자외선차단` | KFDA functional (SPF) approvals |
| 14 | `화장품 벌크 반제품 공급` | bulk/semi-finished supply |
| 15 | `해외 총판 모집 화장품` · `해외 바이어 모집` | overseas partner recruitment |
| 16 | `화장품 연구소 처방 개발 ODM` | real R&D/ODM capability |
| 17 | `{company name} 제조원` | who actually manufactures a given brand |
| 18 | `화장품 공장 임가공 충진` | filling/contract-processing capacity |

Product nouns (`{P-ko}`): 선크림 · 선케어 · 자외선차단제 · 스킨케어 · 크림 · 에센스 · 세럼 · 앰플 · 마스크팩 · 클렌저 · 쿠션 · 바디로션 · 샴푸. Record the strings you actually ran in each candidate's `source_query.keywords[]`.

**Run the Korean rows first and give them most of the budget.** Measured on a live sunscreen OEM/ODM run (2026-09-13): rows **6**, **9** and **12** returned real factories with real 선크림 lines on the first page of results. The English rows (1–3) returned mostly sourcing agents and content-marketing pages, all of which §1.1 then correctly discards, and row 4's expo directories returned nothing retrievable at all (§4 caveat). **Run order: 6, 9, 12, 13 → 7 → 1–3 → 5 per domain found → 10, 11, 15 to fill the MOQ and export gaps → 4 last, and only if a readable exhibitor list exists.** §11's trace follows exactly this order; where a worked example and this order ever disagree, §3 wins.

---

## 4. Step 2 — authoritative Korean sources, in preference order

Verify reachability at run time and store the URL you actually read. Never cite a source you did not open.

| Source class | Examples | Tier | What it reliably establishes |
|---|---|---|---|
| Company official site | `회사소개`, `사업분야`, `생산시설`, `인증현황`, `제품`, `수출`, `문의` | 1 | Everything, when stated plainly |
| **Regulatory registries** — open this first after the company site | MFDS 식약처 의약품안전나라 **업체정보 lookup** (`화장품제조` / `화장품책임판매` filter): `https://nedrug.mfds.go.kr/pbp/CCBBA01` — public, no login, searchable by 업체명. The separate 기능성화장품 심사 lookup (`.../pbp/CCBDB01`) indexes the **책임판매업자**, i.e. the marketing-authorisation holder, **not** the factory, so it cannot on its own establish manufacturer status | 2 | **Registered manufacturer** vs only a **responsible marketer**; `KFDA_FUNCTIONAL`; official identity |
| Government / trade agency | KOTRA **buyKOREA** (`https://buykorea.org/cp/cpy/selectCompaniesList.do` browses Korean sellers without login), KITA **tradeKorea**, KOTRA overseas-office supplier lists | 2 | Existence, export orientation, category, contact form. **Verified 2026-09-13: the buyKOREA listing level shows no MOQ, certifications or export markets** — do not expect commercial terms here |
| Industry associations | 대한화장품협회, 대한화장품산업연구원 member directories | 2 | Membership and official identity |
| Expo exhibitor lists | **COSMOBEAUTY SEOUL**, **K-Beauty Expo Korea**, Seoul International Beauty Expo, Cosmoprof Asia, Beautyworld Dubai Korean pavilion. **in-cosmetics Korea is an INGREDIENTS show** — its exhibitors are raw-material suppliers, the wrong population for a finished-goods OEM/ODM query; use it for ingredient sourcing only | 2 | `trade_show_exhibitor` signal, categories, export intent, dated activity — **only when the directory is actually readable**; see the caveat below |
| Certification registries | Certificate-body lookups for ISO 22716 / ISO 9001 (KTR, KTC and peers), halal certifier licensee lists, vegan certifier lists | 2 | `certifications` with a verifiable certificate — the strongest non-site evidence |
| Trade press (4) / aggregators, sourcing agents (5) | 코스인, 뷰티누리 and peer titles, press releases · B2B marketplaces, agent blogs | 4–5 | Dated capacity/export signals; aggregators are lead generation only — re-verify on tier 1 before writing a claim |

**Caveat on exhibitor directories (matrix row 4).** Exhibitor directories are frequently login-gated or client-rendered, and **you must verify retrievability before citing one — never cite a directory you could not open.** Measured 2026-09-13: of the two Korean cosmetics-fair directories this row points at, one rendered no exhibitor names at all and routed to an exhibitor-login hub, and the other served only booth-application forms with zero company names. Row 4 contributed **0 candidates** to that run. When a directory cannot be read, log stop condition **S2 for row 4** and spend the budget on the Korean rows of §3 instead, which is where the factories actually were. A readable published exhibitor PDF or post-show report is an acceptable substitute; a JS-rendered search page is not.

**Aggregators are not a shortcut.** In the same run the English rows surfaced eight sourcing-agent and content-marketing sites, and all eight were correctly discarded under §1.1. Discarding them is right — but say so: put `"N sourcing-agent/aggregator pages discarded under §1.1 (not official for the factories they list)"` in the run envelope `notes[]`, so a reviewer sees the funnel rather than an unexplained short list.

---

## 5. Step 3 — pages to open on a Korean company site, in order

**Row 0 — open the site root `/` first, always.** It is a tier-1 source in its own right and it is where you get the site's real paths: Korean SMB sites are built on hosted platforms whose section URLs (`/default/<board>/…`, `/sub/…`, `/bbs/…`) cannot be guessed. Read the 메인 strapline and the nav, then open the rows below by the labels the nav actually uses.

| # | Page (typical Korean labels) | Establishes | Fields |
|---|---|---|---|
| 0 | Site root `/` — 메인, 강령/슬로건, nav | Who they are in one line, and the real paths for every row below | `company_name`, `company_type` candidate, `product_categories` candidate |
| 1 | `사업분야` / `OEM·ODM` / `Business` | Whether they take contract manufacturing at all | `oem_odm`, `private_label`, `company_type` |
| 2 | `회사소개` / `About` / `인사말` | Legal name, 설립연도, 사업자/제조업 등록 언급, 소재지 | `company_name`, `company_name_ko`, `country: "KR"`, `company_type` |
| 3 | `생산시설` / `공장` / `설비` / `Facility` | Own factory vs 협력공장, 라인 수, 클린룸, 월 생산능력 | `monthly_capacity_units`, corroborates `company_type` |
| 4 | `인증현황` / `인증서` / `Certification` | Certificate names, numbers, images | `certifications`, `certifications_verified`, `regulatory_registrations` |
| 5 | `제품` / `품목` / `포트폴리오` | Categories and forms actually produced | `product_categories`, `product_forms` |
| 6 | `수출` / `해외영업` / `Global` / `Partners` | 수출국, 수출 지역어, 전시 참가, 총판 모집 | `export_markets`, `export_regions`, `overseas_partner_signal`, `excluded_markets` |
| 7 | `연구소` / `R&D` / `처방개발` | ODM (own formulation) vs OEM (build-to-print) | supports `company_type: oem_odm`, `oem_odm` |
| 8 | `문의` / `견적문의` / `Contact` | Official channels, 대표번호, 이메일 | `contact_channels`, `operational_status` |
| 9 | EN / 다국어 toggle, 영문 카탈로그 PDF; `공지사항` / `뉴스` | Export readiness; dated activity | `english_site`; `evidence.source_date` |

**Page budget.** The minimum viable read is the site root plus one 사업분야/OEM page. Stop when the material claims of §9 are covered, after 6 pages with no new material claim, or at §10's S5.

### 5.1 Three retrieval outcomes, and only one of them is `stale`

| What happened | Record | Never |
|---|---|---|
| The page loaded and its newest dated content is years old, or the site is archived | `stale: true`, keep the record (T07) | Delete the candidate |
| **The domain itself does not resolve** (DNS failure) | `operational_status: "unreachable"` | Use this for any other failure |
| **HTTP 403 / 429 / a WAF or bot challenge**, while `robots.txt` permits the path | **`blocked`**: re-read with another permitted method (a plain page fetch). Record the method in `evidence.retrieval_method` and the failure in `notes[]` | `stale` or `operational_status: "unreachable"` — `stale` is a claim about **content age**, evidenced by a date on the page |
| **TLS handshake fails or the certificate does not cover the host** (the certificate's names belong to the hosting platform) while the domain resolves normally | The site is **reachable**. Re-read over the `http://` URL if `robots.txt` permits it and record that exact URL in `evidence.source_url` with a note naming the failure. If no permitted method works, the facts stay `"unknown"` and the note says which methods failed | `stale`, `unreachable`, or dropping the candidate silently |

This is the most common failure against live Korean SMB sites, and getting it wrong is expensive: `stale` costs `evidence.stale_penalty` −25 and multiplies confidence by 0.6. Measured 2026-09-13: four Korean maker sites returned HTTP 403 to a reader-style fetch while serving `robots.txt` with `Allow: /` for the same paths — one of them explicitly allow-listing agent crawlers — and a plain HTTP GET returned 200 with full content for all four; three of the four ended up in the qualified top five. Separately, one 카페24-hosted site presented a certificate covering only the hosting platform's names, so every HTTPS client refused it while `http://` served the full page.

Retrying with a different permitted method is **not** an access-control bypass. Still forbidden and unchanged: no login, no CAPTCHA solving, no paywall circumvention, no `robots.txt` override, no identity spoofing to defeat a bot filter, no headless-browser automation (§1.2, Core rule 9). A candidate no permitted method can read ends with zero evidence and is excluded by §10's evidence bar, with the retrieval failure named in the exclusion line.

---

## 6. Step 4 — OEM/ODM vs brand-only vs distributor

The most common discovery error is promoting a **trading company** or a **brand-only marketer** as a factory. Korean cosmetics law separates two registrations and the site usually shows which one is held: **화장품제조업** (manufacturing licence) ⇒ a real factory → `manufacturer`, or `oem_odm` when contract work is advertised; **화장품책임판매업** (responsible marketing/distribution) ⇒ brand/marketer role and **no** implied factory; both ⇒ `manufacturer` / `oem_odm`.

| Signals on the page | `company_type` | `oem_odm` / `private_label` |
|---|---|---|
| "OEM/ODM 전문", 수탁제조, 처방개발, 자체 연구소, 생산라인/클린룸 photos, 제조업 등록번호, 공장등록증명서, CGMP 적합업소, 벌크·반제품 공급, "귀사의 브랜드로 생산" | `oem_odm` | `oem_odm: true`; `private_label: true` only if white-label/자사 처방 판매 is stated |
| Registered manufacturing, but only its own catalogue; no contract-manufacturing invitation | `manufacturer` | both `"unknown"` unless stated |
| Own brand story, 자사 브랜드 only, 쇼핑몰/구매 buttons, 제조원 is a **different** company | `brand` | `brand_export: true` when it states export of its own brand; `oem_odm` stays `"unknown"` unless stated |
| 수입/유통/총판, 취급 브랜드 리스트, no production language | `distributor` | usually `"unknown"` |
| "협력 공장에서 생산" / 임가공 brokering, no own 제조업 registration | `other` (agency/intermediary) + a note naming the observed brokering language | `oem_odm` only if they state they contract-manufacture on your behalf |
| Nothing decisive | `unknown` | `"unknown"` |

**A published 화장품책임판매업 등록필증 is not a negative signal** when a manufacturing certificate is published alongside it. The two registrations are held by different legal roles and a mid-size Korean OEM routinely publishes both, or publishes the factory evidence without the 제조업 등록필증 itself. Treat each of these as **independent factory evidence**, at the same weight as a 제조업 등록번호: **공장등록증명서** (factory registration certificate) · **CGMP 적합업소 지정** or a CGMP certificate in the company's own name · 생산실 / 제조실 / 충진실 photographs on the company's own 생산시설 page with 라인 수 or 클린룸 detail.

Read literally, the table above would type as `brand` a company whose own certification page publishes 공장등록증명서 + CGMP (국문·영문) + ISO 22716 + 원산지증명센터 and whose site title is "화장품 턴키 생산·OEM/ODM 전문 제조사", purely because the registration it printed was 책임판매업 — that is wrong. Record **both** registrations, type it on the manufacturing evidence, and note which registration document is missing rather than downgrading the type.

Cross-checks worth one query each: `{company} 제조원` (who actually makes a product), the MFDS 업체정보 lookup (manufacturer vs responsible marketer), and an exhibitor entry on a readable list (self-declared category). If two sources disagree, keep both evidence items, cross-link via `conflicts_with`, and record a `conflicts[]` entry resolved by: official source → more recent `source_date` → higher evidence confidence → lower tier number.

---

## 7. Step 5 — MOQ, lead time, capacity: how they are stated, and how often they are not

Most Korean makers publish **no** MOQ; "견적 문의" (ask for a quote) is the norm. Default to `"unknown"`.

| Printed on the page | Store | Why |
|---|---|---|
| `최소주문수량(MOQ): 3,000개` | `moq: 3000`, `moq_unit: "units"` | 개 / EA / ea / pcs / 매 → `"units"` |
| `MOQ 1,000 ~ 5,000개` | `moq: {"min":1000,"max":5000}`, `moq_unit: "units"` | compared on **`min`** (best case for the seller) |
| `MOQ 1,000개부터` / `1,000개 이상` ("from 1,000"), **and the floor is at or below `query.max_moq`** | `moq: "unknown"` + note `"lower bound only: MOQ 1,000개부터"` | The true MOQ may be above or below the ceiling, so an open lower bound establishes no comparable value (BUILD-CONTRACT 3.3). `numeric_range` requires both bounds, so never store `{"min":1000}`, `{"min":1000,"max":1000}`, `+inf`, or the ceiling |
| `MOQ 5,000개부터` ("from 5,000"), **and the floor is above `query.max_moq`** | `moq: 5000` + note `"open lower bound: 5,000개부터; the true minimum is at or above this value"` | **This one IS comparable.** Every possible value is ≥ 5,000 > the 3,000 ceiling, so `HF-03` can reject on it without inference. The rejection line reads `HF-03 MOQ minimum 5,000 exceeds requested 3,000`, exactly what the page said |
| MOQ **conditional on a spec the query does not fix** — e.g. `100ml 이상 2,000개, 100ml 이하 3,000개`, or `품목별 상이` with two named numbers | the **worst applicable branch** as a scalar (here `moq: 3000` for a ≤100ml SKU) + a note quoting **both** branches; or `moq: "unknown"` + the same note when you cannot tell which branch applies | **Never store the two branches as `{min,max}`.** The range form means *one negotiable band* and is compared on `min`, so `{"min":2000,"max":3000}` scores the branch that does **not** apply — measured: 47/55 on `moq_at_or_below_max` and rank 1 for a company that never earned it |
| `벌크 최소 500kg` | `moq: 500`, `moq_unit: "kg"` | Unit mismatch against a `units` query → `S-OP1` unknown, `HF-03` skipped, `moq_unit_mismatch` fires, note recorded — correct behaviour, not a bug |
| `MOQ 협의 가능` / `문의 바랍니다` / `품목별 상이` | `moq: "unknown"` + a note quoting the phrase; evidence `value: "unknown"` | The page explicitly declines to state it — evidence of the check, not a guess. Also record the `moq_negotiable_statement` claim key (§9.1) |
| `납기 30~45일` | `lead_time_days: {"min":30,"max":45}` | compared on **`max`** (worst case) |
| `납기 30 영업일` (business days) | `lead_time_days: 30` + note `"stated as 영업일 (business days); not converted to calendar days"` | Converting is inference; record as printed and flag |
| Weeks: `납기 6~10주` → `{"min":42,"max":70}` · `4~8주` → `{"min":28,"max":56}` · `약 2~3주` → `{"min":14,"max":21}` | `lead_time_days` as shown + note `"stated in weeks (주); converted at 7 calendar days per week"` | A week is a **fixed calendar quantity**, unlike 영업일, so this is arithmetic and not inference. Weeks are how most Korean makers state 납기 — in a live 12-company run three of the four makers that published any lead time used them, and without this row `lead_time_days` was unknown on **12 of 12** records |
| `월 생산능력 100만 개` | `monthly_capacity_units: 1000000` | compared on **`max`** |
| Nothing on any page | field **absent** or `"unknown"` | Surfaces on the `Missing:` / `확인 필요` line. Expected outcome |

If both a range `unit` and the sibling `moq_unit` exist, **`moq_unit` wins** and a note names both; when only the range carries a unit it is copied into `moq_unit` during normalization. Same rule for `lead_time_days` and `monthly_capacity_units`.

---

## 8. Step 6 — certifications and registrations

| Printed | Canonical token | Notes |
|---|---|---|
| `ISO 22716`, `ISO-22716`, 우수화장품 제조 및 품질관리기준 certificate | `ISO22716` | The baseline certificate buyers ask for |
| `CGMP`, `cGMP`, `GMP` (as a named certificate) · Korean MFDS GMP | `CGMP` · `GMP_KOREA` | A bare marketing phrase "GMP 시설" is **not** a certificate |
| `기능성화장품` 심사/보고 (SPF, 미백, 주름개선) | `KFDA_FUNCTIONAL` | Verifiable in the MFDS registry |
| `ISO 9001` / `ISO 14001` · COSMOS / ECOCERT | `ISO9001` / `ISO14001` · `COSMOS` / `ECOCERT` | |
| `할랄` HALAL · 비건 인증 · Leaping Bunny / 크루얼티프리 | `HALAL` · `VEGAN` · `CRUELTY_FREE` | HALAL is critical for Gulf/SEA destinations |

### 8.1 Destination-market registrations do **not** go in `certifications[]`

EU **CPNP**, 중국 **NMPA / 위생허가**, 미국 **FDA** facility/product listing (**MoCRA**), UAE **MoHAP** and their peers are **per-destination-market product or facility registrations**, not quality-system certificates. They belong in `regulatory_registrations[]`, keyed by market — the field `S-CP3` actually reads:

```json
{"market": "EU", "scheme": "CPNP", "registration_status": "registered", "evidence_ids": ["EV-011"]}
```

Putting them in `certifications[]` is a double mislocation: the certification namespace fills with market notifications that `HF-04`'s superset test and `certifications_verified` then reason over, while the field `S-CP3` reads stays empty and takes the unknown penalty. Measured 2026-09-13: one maker published a four-item "국제 인증 현황" block — ISO 22716, ISO 9001, CFDA ("중국 화장품 위생 허가") and CPNP ("유럽연합 화장품 신고 포털") — and routing all four into `certifications[]` left `regulatory_registrations` absent despite CPNP evidence sitting on the page.

| Goes in `certifications[]` (quality **systems**) | Goes in `regulatory_registrations[]` (destination **markets**) |
|---|---|
| `ISO22716`, `CGMP`, `GMP_KOREA`, `ISO9001`, `ISO14001`, `COSMOS`, `ECOCERT`, `HALAL`, `VEGAN`, `CRUELTY_FREE`, `KFDA_FUNCTIONAL` | `{market:"EU", scheme:"CPNP"}`, `{market:"CN", scheme:"NMPA"}`, `{market:"US", scheme:"FDA_MOCRA"}` or `{market:"US", scheme:"FDA_REGISTERED"}`, `{market:"AE", scheme:"MoHAP"}` |

Record the original printed token in a note either way. `CFDA` remains a legal `certifications[]` token for a record that predates this rule, but new records put the China registration in `regulatory_registrations[]` with `scheme: "NMPA"` and the original token quoted.

**"We hold it" vs "we help you get it".** A maker's page that offers `CPNP 등록 파트너 대행` or `MoCRA 시설등록/제품리스트 … 지원` is advertising a **service sold to clients**, not a registration it holds. That is neither a certification nor a `regulatory_registrations[]` entry for that maker — it is a capability, and it goes in `notes[]` with its evidence id. Reading it as a held registration is exactly the inference §1.2 forbids.

Rules: **`certifications_verified: true` only** when the list came from an official source presenting it as the company's complete set (a dedicated `인증현황` page, or a certifier registry) — only then can `HF-04` reject for a missing certificate; a blog mention, a banner logo strip or a partial list ⇒ `false` / `"unknown"`. An unmapped but well-formed token is preserved verbatim **with a note** and matches only exactly. `regulatory_registrations[]` is per destination market (`{"market":"AE","scheme":"MoHAP","registration_status":"registered|in_progress|not_registered|unknown","evidence_ids":[...]}`); absent = unknown, present-and-empty = checked and none found. Never read a *destination* registration from another market's certificate.

### 8.2 Readiness for India, Indonesia and Türkiye — what a Korean seller's site actually says

These three markets are new to `references/compliance-notes.md` §4.1 and §5.1, and a Korean maker that has done the work says so in a short, recognisable set of phrases. Every row below maps to a field; nothing here is a new token and nothing here changes the rubric.

| Printed on the seller's own page (KO / EN) | Where it goes | Why |
|---|---|---|
| `인도 CDSCO 등록`, `CDSCO 등록 완료`, `COS-2`, `수입 등록증(RC)`, "CDSCO import registration" | `regulatory_registrations` entry `{"market": "IN", "scheme": "CDSCO cosmetic import registration (Form COS-2)", "registration_status": "registered"}` | A per-market registration, so it belongs in `regulatory_registrations` and not in `certifications[]` (§8.1). `S-CP3` reads it when the RFQ's destination is `IN` |
| `BPOM 등록`, `BPOM 노티피케이션`, `인도네시아 BPOM 허가`, "BPOM notification" | `regulatory_registrations` entry `{"market": "ID", "scheme": "BPOM cosmetic notification (notifikasi kosmetika)", "registration_status": "registered"}` | Same. Note who holds it: the notification is held by an **Indonesian** notification holder, so a maker saying "our Indonesian partner holds the BPOM notification" is evidencing a registration that exists **for its product in ID** — record it, and put the holder relationship in `notes[]` |
| `튀르키예 ÜTS 등록`, `터키 ÜTS 신고`, `TİTCK 등록`, "notified to TİTCK through ÜTS" | `regulatory_registrations` entry `{"market": "TR", "scheme": "TİTCK cosmetic product notification (ÜTS)", "registration_status": "registered"}` | Same. The Turkish responsible person is a company fact for `notes[]`, never a named individual |
| `할랄 인증`, `인도네시아 할랄`, `BPJPH`, `KMF 할랄`, `MUI 할랄`, `Helal`, `Sertifikat Halal` | `HALAL` in `certifications[]`, **with the certifying body and the scope named in `notes[]`** | `HALAL` is a claim certification, not a market registration (§8.1). The body matters: recognition is **per body and per scope**, and a body recognised for food is not thereby recognised for cosmetics (`compliance-notes.md` §5.1). The agent normalises the local spelling; **no code synonym is added** (`compliance-notes.md` §5.2a) |
| A **BPJPH registration of a foreign halal certificate** stated by the maker ("our halal certificate is registered with BPJPH") | **Both**: `HALAL` in `certifications[]` **and** a `regulatory_registrations` entry with `market: "ID"` and `scheme` naming BPJPH | They are two different facts — holding a certificate, and that certificate being recognised in the destination market. Recording only one of them loses the half `S-CP3` prices |

**What does NOT count, in any of the three:**

| Seen on the page | Why it is not the claim |
|---|---|
| `인도 수출 실적`, `인도네시아 수출`, `튀르키예 수출 경험` — export track record | That is `export_markets[]` (§9), not a registration. "We export to Indonesia" is not a BPOM notification (§5.3 rule 3 of `compliance-notes.md`) |
| `CDSCO 등록 대행`, `BPOM 등록 지원`, `할랄 인증 컨설팅`, `ÜTS 신고 대행` | A **service sold to clients**, not a registration the maker holds. `notes[]` with its evidence id, exactly as for `CPNP 등록 대행` above |
| `할랄 친화적`, "halal-friendly", `무슬림 친화`, `돼지 유래 원료 미사용` (no porcine-derived ingredients) | Marketing copy and an ingredient statement, not a certification. Never `HALAL` |
| `인도네시아 파트너사 보유`, "we have a Türkiye partner" | A commercial relationship. It may support `overseas_partner_signal`; it is not a registration |
| A halal certificate whose **stated expiry precedes `--as-of`**, or whose scope covers a different product line | Not held, or not held for this product. Record a `conflicts[]` or risk entry instead (`compliance-notes.md` §5.3 rules 1–2) |
| The **buyer's** market making halal effectively mandatory | Not a fact about the seller at all. It never becomes a `required_certifications` token the RFQ did not state — raise it for the human (`references/matching-rules.md` HF-04) |

Korean gloss: `인도 CDSCO 등록`·`BPOM 등록`·`튀르키예 ÜTS`는 `regulatory_registrations`에, `할랄`은 인증기관을 `notes[]`에 적고 `HALAL` 토큰으로. 수출 실적·등록 대행·"할랄 친화적"은 어느 쪽도 아니다.

---

## 9. Step 7 — write the record: observed text → field → claim key

★ = material claim (INV-01: evidence-backed, or unknown **in the spelling §1.3 gives for that field** — no third option). For an array, "unknown" means the field is **absent**; the literal string is invalid.

| Observed | Seller field | `evidence.claim` |
|---|---|---|
| Company name (EN / KO as printed); the site itself | `company_name`, `company_name_ko`, `normalized_name`; `website`, `canonical_domain`, `alias_domains[]` | `company_name`, `website` |
| Korean address / 소재지 | `country: "KR"` ★ | `country` |
| §6 role signals; product catalogue | `company_type` ★; `product_categories[]` ★, `product_forms[]` | `company_type`, `product_categories`, `product_forms` |
| OEM/ODM/수탁제조 language; 화이트라벨 / 자사 처방 판매; export of its **own** brand | `oem_odm` ★; `private_label` ★; `brand_export` | `oem_odm`, `private_label`, `brand_export` |
| §7 statements | `moq` ★, `moq_unit`, `lead_time_days`, `monthly_capacity_units` | `moq`, `lead_time_days`, `monthly_capacity_units` |
| §8 certificates | `certifications[]` ★, `certifications_verified` | `certifications`, `certifications_verified` |
| Destination registration (§8.1) | `regulatory_registrations[]` | `regulatory_registrations` |
| 수출국 list / 해외 거래처 (**named countries**); "we do not supply {market}" / exclusivity granted | `export_markets[]` ★ (alpha-2); `excluded_markets[]` | `export_markets`, `excluded_markets` |
| 수출 **지역어** with no country named — 동남아 / 동아시아 / 남아시아 / 중동 / 유럽·EU / 북미 / 중남미 / 아프리카 / 오세아니아 / CIS / 아시아, or "Asia, Europe, North America" | `export_regions[]` — tokens `SEA`, `EA`, `SA`, `MENA`, `EU`, `EUROPE`, `NA`, `LATAM`, `AFRICA`, `OCEANIA`, `CIS`, `ASIA` (the schema enum; `EUROPE` and `ASIA` are the coarse spellings, `EU` only when the page names the EU) | `export_regions` |
| EN site or EN catalogue | `english_site` | `english_site` |
| 해외 총판·바이어 모집, exhibitor entry on a readable list | `overseas_partner_signal` ★ | `overseas_partner_signal` |
| 견적문의 form, 대표 이메일, 대표번호, company LinkedIn; dead domain / 폐업 | `contact_channels[]` ★; `operational_status`, `stale` | `contact_channels`, `operational_status` |

**Region-level export, and why it is a separate field.** `export_markets[]` is alpha-2 only, so a region word cannot go in it — writing `["VN","TH","ID","PH","MY","SG"]` for 동남아 invents six countries the page never named, which §1.2 forbids. Recording nothing is also wrong: it discards a clearly evidenced export track record and the seller then takes the `S-EX1` unknown penalty as if it had never exported. `export_regions[]` records exactly what the page said, and `S-EX1` prices it one tier below a named country. Measured 2026-09-13: one 70-year-old exporter states its export only as "Asia, Europe, North America" with zero named countries, and another lists "중국, 미국, 영국, 체코, 일본, **동남아** 등" — the named countries went to `export_markets`, 동남아 to `export_regions`. **Never expand a region token into countries yourself**; region expansion is the operator's job on the query surface (BUILD-CONTRACT 8.7).

**Two names on one site.** When the site prints a trading brand in the title or menu and a **different legal entity** in the footer 사업자정보 / 상호명, `company_name` takes the **legal entity**, `company_name_ko` its Korean form, and the trading brand goes to `notes[]` with its evidence id. Never merge two records on the trading brand alone. `normalized_name` is a dedupe input (BUILD-CONTRACT 8.3) and the two names routinely share no token at all — one real site titled with an English trading brand carried an unrelated Korean 법인명 in its footer, with separate 본사/연구소 and 공장 addresses.

### 9.1 Agent-signal claim keys — priced by the scorer, unreachable through the field table

`score_seller.py` fires **field signals** (from a record field), **claim signals** (a canonical claim plus a structural predicate) and **agent signals**, which fire when the record carries an evidence item whose `claim` **equals the signal key itself**. No script can judge what a page *says*, so recording the key is how the evidence pass hands that judgement to the deterministic pass (PRD 13.2). Write each one as an ordinary evidence item — one claim, the URL you read, a verbatim quote, `observed_at`. An agent signal with no evidence item behind it does not exist, and inventing one to lift a score is a Core-rule-1 violation.

| Observed page text | `evidence.claim` to write | What it feeds |
|---|---|---|
| A named product / 품목 page for the requested category (제품 상세, 라인업) | `named_product_page` | S-PF3, 8 pts |
| A published catalogue or PDF (카탈로그 / 제품 소개서 / line sheet) | `catalog_or_pdf_published` | S-PF3, 5 pts |
| A dedicated OEM·ODM / 수탁제조 page on the company's own site | `published_oem_odm_page` | S-CM3, 5 pts — also fires from an `oem_odm` evidence item with `is_official: true` and `source_tier <= 2` |
| MOQ or pricing terms actually printed (최소주문수량 안내, 단가 조건) on an official page | `published_moq_or_pricing_terms` | S-CM3, 3 pts — also fires from an official `moq` evidence item |
| An English catalogue or a few EN pages, with no full English site | `partial_english_materials` | S-EX2, 18 pts (a full EN site is `english_site: true`, 30 pts) |
| The company's own exhibitor entry on a **readable** exhibitor list (§4) | `trade_show_exhibitor` | S-EX3, 18 pts |
| A 수출/해외영업 enquiry route — 수출 문의 form, export sales 창구 | `export_inquiry_channel` | S-EX3, 12 pts |
| `MOQ 협의 가능` / `수량 협의` on an official page | `moq_negotiable_statement` | `operational_fit` adjustment **+5**. It never turns an unknown MOQ into a number |
| `샘플 주문 가능` / 소량 시험생산 accepted | `sample_or_trial_order_accepted` | `operational_fit` adjustment **+3** |

**Record scaffold (raw profile).** `score_version: "unscored"`, `qualification_score: 0`, no `dimension_scores`, and `confidence: 0`, which `score_seller.py` overwrites.

```json
{
  "schema_version": "0.1.0", "score_version": "unscored", "seller_id": "SEL-hanbit-cosmetic-example",
  "company_name": "Hanbit Cosmetic Co., Ltd.", "company_name_ko": "한빛코스메틱",
  "website": "https://hanbit-cosmetic.example", "canonical_domain": "hanbit-cosmetic.example",
  "country": "KR", "company_type": "oem_odm",
  "product_categories": ["suncare", "sunscreen", "skincare"],
  "product_forms": ["cream", "lotion", "essence", "stick"],
  "oem_odm": true, "private_label": true,
  "moq": 3000, "moq_unit": "units", "lead_time_days": {"min": 30, "max": 45},
  "monthly_capacity_units": 1000000,
  "certifications": ["ISO22716", "CGMP", "KFDA_FUNCTIONAL"], "certifications_verified": true,
  "export_markets": ["US", "JP", "VN", "AE"], "english_site": true, "overseas_partner_signal": true,
  "contact_channels": [{"type": "form", "value": "https://hanbit-cosmetic.example/inquiry", "label": "견적문의", "evidence_ids": ["EV-009"]}],
  "operational_status": "active", "status": "VERIFIED",
  "qualification_score": 0, "confidence": 0, "evidence": [],
  "source_query": {"product_categories": ["sunscreen"], "commercial_model": "oem_odm", "max_moq": 3000,
    "required_certifications": ["ISO22716"], "keywords": ["선크림 OEM 제조사"], "category_drift": false},
  "notes": []
}
```

`seller_id` = `SEL-` + `canonical_domain` with `.` and `-` collapsed to `-`; when the domain is `"unknown"`, `SEL-kr-<normalized_name slug>`. `status` = `DISCOVERED` once ≥ 1 evidence item has a resolvable URL, `VERIFIED` once ≥ 1 material claim rests on `source_tier <= 3`; never write `QUALIFIED` or later here.

**Evidence record** — one claim, one source, one observation time:

```json
{
  "schema_version": "0.1.0", "evidence_id": "EV-003", "claim": "moq", "value": 3000,
  "source_url": "https://hanbit-cosmetic.example/oem", "source_domain": "hanbit-cosmetic.example",
  "source_type": "official_site", "source_tier": 1, "is_official": true,
  "observed_at": "2026-09-12T11:02:00Z", "source_date": "unknown", "confidence": 0.90,
  "quote_or_summary": "최소주문수량(MOQ): 3,000개", "retrieval_method": "page_fetch"
}
```

Quote the Korean verbatim; the reading ("3,000 units, at the ceiling") belongs in `notes[]`, never in `quote_or_summary`. `observed_at` is when **you** read it, never later than `--as-of` end-of-day; `source_date` only when the page prints one.

---

## 10. Step 8 — drift, stop conditions, hand-off

**Category drift (DISC-05).** Widen one level per pass: child → parent (`sunscreen` → `suncare`), parent → adjacent parent (`suncare` → `skincare`), then category-free (`화장품 OEM ODM 제조사`). Every candidate first seen after a widening pass gets `source_query.category_drift = true`, the widened strings in `source_query.keywords[]`, and the note `"category drift: requested sunscreen, found via skincare query (DISC-05)"`.

**Stop the run** at the first of:

| # | Condition | Report |
|---|---|---|
| S1 | Requested count reached with candidates that clear the evidence bar | normal output |
| S2 | Matrix §3 exhausted in EN + KO **and** 3 consecutive queries yield no new `canonical_domain` | note `"query matrix exhausted"` (also used per row for an unreadable directory: `"S2 for row 4"`) |
| S3 | Both widening levels exhausted and still short | `next_action` = `Widen the query — {n} of {N} requested candidates met the evidence bar` |
| S4 | Tool/time budget reached | envelope `partial: true` + one note per unfinished candidate; the run still succeeds |
| S5 | **Total pages opened exceeds `4 × count`** | Stop opening pages, set `partial: true`, and note which candidates were worked from the site root only. Score what you have |

Per candidate: stop when the material claims are covered, after 6 pages with no new material claim, or when §5.1 leaves no permitted way to read the site. **Evidence quality outranks count (DISC-06):** the count is a ceiling, never a target, and a candidate with no evidenced material claim goes to `excluded[]`, never into the ranked list. Say **which** of the two cases it was: `no evidenced material claim (no source could be opened)` when §5.1 left no permitted way in, or `no evidenced material claim (evidence covers no material claim)` when pages were read but nothing material was on them.

**"Unverified" is a different state and is not an exclusion.** A candidate that *has* evidence but no **official** item (`is_official: true`) on a material claim is scored, ranked, and rendered with the literal ` — unverified` appended to its header line (`references/output-format.md`, PRD 15.1 criterion 3). Returning 11 ranked candidates against a request for 20 is likewise **correct** under DISC-06, not a shortfall — but the envelope must say so, including how many aggregator pages were discarded (§4).

**Hand-off checklist**

- [ ] Required fields present: `schema_version`, `score_version: "unscored"`, `seller_id`, `company_name`, `canonical_domain`, `country`, `company_type`, `status`, `qualification_score: 0`, `confidence: 0`, `evidence`, `notes`.
- [ ] Every material claim evidenced, or unknown **in the spelling §1.3 gives for that field** (arrays: the field is absent, never the literal `"unknown"`); **no** MOQ / certificate / export market inferred.
- [ ] The §9.1 agent-signal claim keys are written wherever the page text licenses them — they are the only route to those points, and omitting them silently deflates the cohort.
- [ ] `certifications_verified` is `true` only against an exhaustive official list — and it is **priced**: an unverifiable certificate list no longer scores the same as a verified one (`S-CP1`).
- [ ] Destination-market registrations are in `regulatory_registrations[]`, not `certifications[]` (§8.1).
- [ ] Units set on every quantity; open lower bounds handled per §7 — `"unknown"` + note when the floor is at or below the ceiling, the floor as a scalar + note when it is above it; conditional MOQs stored as the worst applicable branch, never as a `{min,max}` range.
- [ ] Region-level export claims are in `export_regions[]` using the schema's tokens; **no** region word expanded into countries.
- [ ] No named individual or personal contact anywhere, `notes[]` and quotes included.
- [ ] Conflicts recorded, not silently resolved; drift flagged per candidate.
- [ ] `normalize_company.py` → `dedupe_companies.py` (`(주)`, `주식회사`, `Co., Ltd.` are stripped by name normalization; `www`/non-www variants collapse by `canonical_domain` per test T06, and IDN hosts fold to their punycode form per the BUILD-CONTRACT 8.1 cases in `tests/fixtures/normalize.cases.json`).
- [ ] Then `score_seller.py --query <query-surface.json> --as-of 2026-09-12` and render per §10.2 of `references/output-format.md`.

---

## 11. Worked trace — `product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716"`

**Query surface** — §2 is normative; this illustrates it.

```json
{
  "product_categories": ["sunscreen"], "commercial_model": "oem_odm",
  "max_moq": 3000, "moq_unit": "units",
  "required_certifications": ["ISO22716"], "required_seller_countries": ["KR"],
  "keywords": ["선크림 OEM 제조사", "화장품 OEM ODM 제조사", "Korea sunscreen OEM ODM manufacturer"],
  "category_drift": false, "vertical": "K-Beauty"
}
```

> The operator stated product, `oem_odm`, `max_moq` and `certifications` and **nothing else**, so `destination_country`, `region_countries` and `preferred_certifications` are **absent** — and `S-EX1` / `S-CP2` / `S-CP3` therefore report `state: "inapplicable"` ("the query imposes no such constraint"), not unknown. Adding them to a run nobody asked for adds two unknown penalties per seller: in a live 11-record run every record would have taken them, since `regulatory_registrations` was absent on 12 of 12 and no candidate published HALAL. Add a destination key only when the operator names a destination.

**Queries run (order, per §3)** — rows 6, 9, 12, 13 in Korean first, since they are what surface factories; then row 7; then rows 1–3 in English (which returned mostly sourcing agents and content-marketing pages, all discarded under §1.1 and counted in the envelope note); then row 5 per domain found; then rows 10, 11 and 15 to fill the MOQ and export gaps. Row 4 ran last against the expo directories, which rendered no exhibitor names — logged **S2 for row 4**, 0 candidates (§4).

**Candidate walk-through** — `hanbit-cosmetic.example`, found via `선크림 OEM 제조사`.

| Page | Read (verbatim) | Written |
|---|---|---|
| `/` (site root, opened first — §5 row 0) | "화장품 턴키 생산·OEM/ODM 전문 제조사" + nav: 회사소개 · 사업분야 · 생산시설 · 인증현황 · 수출 · 문의 | `company_type` candidate and the **real paths** for every row below → `EV-011` (tier 1) |
| `사업분야 / OEM·ODM` | "선케어 전문 OEM·ODM 수탁제조. 처방 개발부터 완제품 생산까지." | `oem_odm: true`, `company_type: "oem_odm"`, `product_categories += ["suncare","sunscreen"]` → `EV-001` (tier 1), plus the agent signal `claim: "published_oem_odm_page"` (§9.1) |
| 〃 | "최소주문수량(MOQ): 3,000개" | `moq: 3000`, `moq_unit: "units"` → `EV-003`, plus `claim: "published_moq_or_pricing_terms"`; note: "3,000 equals the 3,000 ceiling; `HF-03` passes" |
| 〃 | "자사 처방을 귀사 브랜드로 공급 (화이트라벨)" | `private_label: true` → `EV-004` |
| `인증현황` | Certificate list with images: ISO 22716, CGMP, 기능성화장품 심사필 | `certifications: ["ISO22716","CGMP","KFDA_FUNCTIONAL"]`, `certifications_verified: true` → `EV-005` |
| `생산시설` | "월 생산능력 100만 개, 클린룸 3개 라인" | `monthly_capacity_units: 1000000` → `EV-006` |
| 〃 | "납기 6~10주" | `lead_time_days: {"min":42,"max":70}` + note "stated in weeks (주); converted at 7 calendar days per week" → `EV-007` |
| `수출 / Global` | "수출국: 미국, 일본, 베트남, UAE. 해외 총판 및 바이어를 모집합니다." | `export_markets: ["US","JP","VN","AE"]`, `overseas_partner_signal: true` → `EV-008` |
| EN toggle + English catalogue PDF | English product catalogue published | `english_site: true` → `EV-002`, plus `claim: "catalog_or_pdf_published"` |
| `문의` | 견적문의 form, 대표번호, `info@` role address | `contact_channels[form, phone, corporate_email]` → `EV-009`; no individual recorded |
| MFDS 업체정보 lookup (`nedrug.mfds.go.kr/pbp/CCBBA01`), 업체명 search | 화장품제조업 registration in the company's own name | corroborates `company_type: "oem_odm"` → `EV-010`, `source_type: "official_directory"`, tier 2 |

HALAL is nowhere on the site → `certifications` stays as read and "HALAL certification" appears on the `Missing:` line only if a destination made it applicable; no market registration is mentioned → `regulatory_registrations` absent = unknown. **Neither is invented.** Two things this trace gets right that a real run gets wrong by default: the `인증현황` page was a dedicated certificate page with images, so `certifications_verified: true` is earned — and that **scores** (`S-CP1` prices a verified list above an unverifiable bullet list). And the export line names countries, so it goes to `export_markets`; had it said only "동남아 수출" it would go to `export_regions` as `SEA` and **not** be expanded into country codes (§9).

**Same run, instructive outcomes**

| Candidate | Observation | Outcome |
|---|---|---|
| `daon-sun.example` | `인증현황`: ISO 22716 listed; MOQ page: "MOQ 1,000개부터" | `moq: "unknown"` + lower-bound note; `HF-03` skipped, unknown penalty recorded, candidate **kept** (T04) |
| `bulkcos.example` | "최소 생산 단위 5,000개 (협의 불가)" | `moq: 5000` confirmed > 3,000 ceiling ⇒ `HF-03` failure, moved to `excluded[]`: `HF-03 MOQ minimum 5,000 exceeds requested 3,000` (T03) |
| `floorcos.example` | FAQ: "MOQ는 5,000개부터 시작합니다" — an **open lower bound above** the ceiling | `moq: 5000` + note `"open lower bound: 5,000개부터; the true minimum is at or above this value"` ⇒ `HF-03` failure. Comparable because every possible value exceeds the ceiling (§7) |
| `volumecos.example` | FAQ: "특이사항이 없는 경우 100ml 이상 2,000개, 100ml 이하 3,000개" (conditional on fill volume) | A sunscreen SKU is ≤100ml, so the applicable branch is 3,000: `moq: 3000` + a note quoting **both** branches. Stored as `{"min":2000,"max":3000}` it would compare on 2,000 — the branch that does not apply — and score `moq_at_or_below_max` it never earned (§7) |
| `seoulbrandhouse.example` | Own-brand shop only; "당사는 수탁제조를 하지 않습니다" | `oem_odm: false` (verified negative, with the evidence of that sentence) ⇒ `HF-02` failure: `OEM/ODM not offered (confirmed unavailable)` |
| `cafe24maker.example` | HTTPS refused — certificate covers the hosting platform only; `http://` served the full page | Read over `http://`, that URL recorded in `evidence.source_url` with a note. **Not** `stale`, **not** `unreachable` (§5.1) |

**Rendered result (shape per `references/output-format.md` §10.2)**

```
Summary
- Query: sunscreen / OEM/ODM / MOQ <= 3,000 / ISO22716
- Candidates found: 24 · Qualified (>=70): 8
- As of: 2026-09-12 · Score version: kbtm-score-0.2.0

Top Candidates
1. Hanbit Cosmetic Co., Ltd. — 91/100 — HIGH
   Website: https://hanbit-cosmetic.example · Country: KR · Type: OEM/ODM
   Why: suncare OEM/ODM contract manufacturing + MOQ 3,000 within ceiling + ISO22716 verified
   MOQ: 3,000 units · Certifications: ISO22716, CGMP, KFDA_FUNCTIONAL · Export markets: US, JP, VN, AE
   Contact: 견적문의 form — https://hanbit-cosmetic.example/inquiry
   Evidence: [https://hanbit-cosmetic.example/oem], [https://hanbit-cosmetic.example/certification] (+9 more)
   Missing: UAE market registration

2. Daon Sun Co., Ltd. — 74/100 — MEDIUM
   Website: https://daon-sun.example · Country: KR · Type: OEM/ODM
   Why: sunscreen OEM production + ISO22716 held
   MOQ: unknown · Certifications: ISO22716 (unverified list) · Export markets: unknown
   Contact: 견적문의 form — https://daon-sun.example/contact
   Evidence: [https://daon-sun.example/oem], [https://daon-sun.example/about]
   Missing: MOQ against the buyer ceiling; export track record

Excluded / low fit
- Bulk Cosmetic Korea: HF-03 MOQ minimum 5,000 exceeds requested 3,000
- Seoul Brand House: HF-02 OEM/ODM not offered (confirmed unavailable)

Recommended next action: Draft outreach to top 5
```

Candidate 2 ranks below candidate 1 purely because unknowns are penalised, not rejected — exactly the behaviour PRD test T04 and acceptance 15.2 require.
