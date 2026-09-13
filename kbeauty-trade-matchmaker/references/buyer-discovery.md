# Buyer Discovery Playbook

Operational instructions for **Mode 1 — Buyer Discovery** (PRD 5.1, 6.1, 10.1, 10.3, 15.1). Load this file when the request is "find K-Beauty buyers / distributors / importers / wholesalers in `<market>`". Korean gloss: 해외 바이어 발굴 실행 지침. Run the steps in order, spell field names exactly as written here (`schemas/buyer.schema.json`; claim keys in `references/data-contract.md`), and stop when a §10 condition fires.

---

## 1. Non-negotiables

### 1.1 Source priority tiers (PRD 10.3)

Satisfy every claim from the lowest tier number available. Tier goes in `evidence.source_tier`; `is_official` is `true` only on channels the company itself controls.

| Tier | Source | `source_type` | `is_official` |
|---|---|---|---|
| 1 | The company's own site: home, about, wholesale, distributor, brands, partnership, stockists, contact | `official_site` | `true` |
| 2 | Official trade-show exhibitor/visitor list, industry association, government or trade-agency directory, chamber-of-commerce member list | `official_directory`, `trade_show`, `internal_record` | `true` only for the company's own exhibitor entry |
| 3 | The company's official LinkedIn / company-controlled social profile | `social` | `true` |
| 4 | Reputable third-party directory, trade press, press release | `third_party` | `false` |
| 5 | Community, blog, forum, aggregator listing — **supporting signal only**, never the sole basis of a material claim | `third_party`, `social` | `false` |

A distributor listing a brand is **not** official for that brand, and a directory entry is **not** official for the company it describes unless that company itself submitted and controls it.

### 1.2 Hard-forbidden behaviour (PRD 11.1) — no exceptions

| Forbidden | What to do instead |
|---|---|
| Inferring or bulk-collecting personal emails / phone numbers (`first.last@domain`, direct dials, personal mobiles) | Store company-level channels only (`partnership_form`, `wholesale_form`, `form`, `contact_page`, `corporate_email` role address, `phone` switchboard, `linkedin` company page, `messenger` official channel, `other`) |
| Naming an individual anywhere — `notes[]`, `quote_or_summary`, evidence, rendered output | Record the **role** only ("brand partnerships team") with its evidence id |
| Bypassing CAPTCHA, login, paywall, robots.txt, rate limits | Leave the fact `"unknown"` and note why it could not be checked |
| Bulk automated outreach of any kind | Out of scope for this mode entirely |
| Fabricated urgency ("a buyer is looking for your product right now") | Only a cited RFQ in `qualified` / `matching` / `proposal_open` supports that sentence (Mode 4) |
| Stating MOQ, certifications, export markets, exclusivity as fact without a source | `"unknown"` + a note; a penalised unknown is the **correct** outcome (PRD 19, EVID-05) |

### 1.3 Three states, never two (BUILD-CONTRACT 3.2)

**unknown** = tri-state `"unknown"` or the field/array **absent** (the wholesale page was never found) · **verified negative** = `false` or a **present-and-empty** array, and it needs evidence *of the check itself* ("we do not sell wholesale" → `wholesale_signal: false`) · **verified positive** = `true` / non-empty / a number. Never write `false`, `0`, `""`, `null` or `"N/A"` for "I did not find it".

---

## 2. Step 0 — build the query surface

Parse the operator request into the query surface (`references/data-contract.md` §3.9) **before searching**. Scripts never expand region words — you do.

| Operator says | Query-surface key | Value |
|---|---|---|
| `country="UAE"` | `country`, `destination_country`, `country_name` | `"AE"` (ISO-3166-1 alpha-2, upper case) + `"United Arab Emirates"` (display only) |
| `"GCC"` / `"EU"` / `"MENA"` | `region_countries` | the explicit alpha-2 list you searched, e.g. `["AE","BH","KW","OM","QA","SA"]` |
| neighbouring markets you also accept | `adjacent_region_countries` | e.g. `["JO","LB","EG"]` for the Gulf |
| `category="sunscreen"` | `product_categories` | `["sunscreen"]` (canonical slug; `sun_cream`/`sunblock`/`spf` → `sunscreen`) |
| `type="distributor,importer"` | `company_types` | `["distributor","importer"]` — a **filter**, never a scoring signal |
| `channel="wholesale"` | `channels` | `["wholesale"]` |
| free-text terms | `keywords` | the literal search strings that surfaced candidates |
| always | `vertical` | `"K-Beauty"` |

`k_beauty` / `kbeauty` are **not** categories: drop them from `product_categories`, set `vertical`, and note the drop. An absent key imposes no constraint (the dependent criterion becomes *inapplicable*, not unknown).

**This table is normative.** It is the only place the query surface is defined and §12's trace merely illustrates it; where the two disagree, §2 wins. In particular, `region_countries` and `adjacent_region_countries` are populated **only when the operator said a region word**. A single-country request leaves both absent, and a candidate in a neighbouring country then scores `country_mismatch` (−40) rather than `country_in_requested_region` (+35) — measured as a 12-point, 9-rank swing on one real cohort. If you want regional reach on a single-country request, say so on the run envelope and fill `region_countries` deliberately; never let it appear by copying an example.

**Leaf categories the market does not merchandise.** `sunscreen`, `ampoule` and `cushion` are product slugs, not sections a buyer publishes — in a 32-company UAE cohort exactly one company printed the word "sunscreen" in its own copy. For a leaf category of that kind set `source_query.product_categories` to the **parent** (`suncare`, or `skincare`) at Step 0, record the narrowing in `notes[]` (`"requested category sunscreen narrowed to suncare at Step 0: buyers do not merchandise it as a distinct section"`), and leave `category_drift` **false** — a deliberate Step-0 narrowing is not a mid-run widening. Otherwise §9's ladder fires on pass 1 for the whole cohort and a uniform −10 `category_drift_flagged` moves everyone across the fixed threshold without changing one rank. A leaf-category request is also the case for `--threshold-mode percentile`.

---

## 3. Step 1 — the query expansion matrix

Never run one broad query. Cross **company type × product category × country**, and run each row in English plus the local language of §4. PRD 10.1 seeds are rows 1–6; the rest are required extensions.

| # | Pattern (`{C}` = country name, `{P}` = product noun) | Targets |
|---|---|---|
| 1 | `{C} Korean skincare distributor` | distributor |
| 2 | `{C} K-beauty wholesaler` | wholesaler |
| 3 | `{C} Korean cosmetics importer` | importer |
| 4 | `{C} Korean beauty brand distributor` | distributor / brand-house |
| 5 | `{C} skincare wholesale partnership` | wholesaler inviting brands |
| 6 | `{C} private label Korean cosmetics sourcing` | private-label buyer |
| 7 | `{C} Korean cosmetics "MOQ" OR "minimum order"` | B2B terms published — **high yield** |
| 8 | `{C} beauty "brand submission" OR "brand partnership" form` | brand-intake form |
| 9 | `{C} cosmetics "trade account" OR "wholesale enquiries"` | B2B pricing gate |
| 10 | `{C} cosmetics trading company wholesale Korean` (also `… trading LLC …` where that is the local legal form) | trading/importing SMEs — **high yield** |
| 11 | `{C} pharmacy chain OR department store Korean beauty supplier` | retail chain procurement |
| 12 | `{C} salon spa professional skincare distributor` | `salon_spa` channel |
| 13 | `{C} duty free travel retail Korean cosmetics` | `duty_free` channel |
| 14 | `{C} beauty marketplace seller onboarding Korean brands` | `marketplace` |
| 15 | `site:{found-domain} wholesale OR distributor OR partnership OR stockist` | page sweep of a known domain |
| 16 | `"{expo name}" exhibitor list {C} cosmetics importer` | tier-2 directory harvest — see the §4 caveat |
| 17 | `{C} "authorised distributor" OR "authorized distributor" K-beauty` (+ the §4 local-language form) | regulated-import markets, agency language |
| 18 | `{C} {P} distributor "become a distributor"` | **optional, low yield** — see below |
| 19 | `{C} "now accepting new brands" skincare` | **optional, low yield** — see below |

Run order: rows 1–6 (seeds) → 7, 10, 8, 9 (B2B terms and role) → 11–14 (channel) → 16 (directories) → 15 per domain found → 17 as gap fill → 18–19 only if still short. Record every string you actually ran in `source_query.keywords[]` on the candidates it produced.

**Rows 18 and 19 are optional because they were measured and did not work.** In a live UAE run (34 unique candidates) row 18's exact phrase returned a cleaning-technology firm, a fuel retailer, a motors dealership, a telecoms retailer, a uniform supplier and two encyclopaedia articles — **zero** cosmetics companies; row 19's exact phrase returned **zero** hits at all, only consumer "new beauty brands" landing pages and lifestyle editorial. Both are written for an intent signal that real importers put on a page but do not phrase in those words. Every one of that run's 34 candidates came from rows 1–6, 9, 11–14, 17, the promoted rows 7 and 10, or the §4 local-language rows. Run 18–19 last and log `"query matrix exhausted"` (S2) rather than grinding on them.

---

## 4. Step 2 — regional language pack and local B2B directories

Search in the local commercial language as well as English; many importers publish their trade pages only locally. Verify every directory is reachable at run time and store the **URL you actually read**, never a remembered one.

| Region | Local query variants | Directories / lists worth harvesting (tier 2 unless noted) |
|---|---|---|
| **Gulf / UAE, SA, KW, QA, BH, OM** | `موزع مستحضرات تجميل كورية` (Korean cosmetics distributor) · `مستورد مستحضرات التجميل` (cosmetics importer) · `بيع بالجملة مستحضرات التجميل` (cosmetics wholesale) · `الموزع المعتمد` (authorised distributor — the row-17 local form) · `وكيل حصري` (exclusive agent) | **Beautyworld Dubai** exhibitor list (renamed — the old *Beautyworld Middle East* host 301-redirects to it) · Dubai Derma · Dubai Chamber member directory · free-zone company directories (DMCC, JAFZA, DAFZA, RAKEZ) · Saudi Chambers directory. Legal forms `LLC`, `FZ-LLC`, `FZE`, `DMCC` are stripped by name normalization. **Verified 2026-09-13: none of these produced a K-Beauty importer** — see the caveat below |
| **EU (FR, DE, IT, ES, PL, NL)** | FR `distributeur cosmétiques coréens`, `grossiste cosmétique`, `importateur cosmétiques` · DE `Großhandel koreanische Kosmetik`, `Vertriebspartner werden`, `Markenpartner` · IT `distributore cosmetici coreani`, `grossista cosmetici` · ES `distribuidor cosmética coreana`, `mayorista cosmética`, `importador de cosméticos` | Cosmoprof Worldwide Bologna exhibitor/buyer lists · Beauty Düsseldorf · Europages (tier 4) · national chamber-of-commerce member directories · CPNP responsible-person service firms (signals an import function) |
| **UK** | `wholesale enquiries`, `trade account`, `become a stockist`, `brand submission`, `buying team` | Companies House (entity confirmation only, tier 2) · Pure Beauty / Olympia Beauty exhibitor lists · UK trade press brand-listing pages (tier 4) |
| **US / CA** | `wholesale program`, `become a retailer`, `vendor application`, `line sheet`, `brand partnerships`, `buyer inquiry` | Cosmoprof North America exhibitor list · IndieBeauty / regional trade-show lists · wholesale marketplaces such as Faire/Abound (tier 4, listing only) · retail-chain "supplier/vendor" portals |
| **SEA (ID, MY, TH, VN, PH, SG)** | ID/MY `distributor kosmetik Korea`, `grosir kosmetik`, `importir kosmetik` · TH `ตัวแทนจำหน่ายเครื่องสำอางเกาหลี`, `ขายส่งเครื่องสำอาง` · VN `nhà phân phối mỹ phẩm Hàn Quốc`, `nhập khẩu mỹ phẩm` | Cosmobeaute Asia · Beyond Beauty ASEAN · national importer/registrant listings (BPOM in ID, FDA Thailand) · halal certification bodies' licensee lists |
| **Japan** | `韓国コスメ 卸`, `化粧品 輸入 代理店`, `取扱いブランド募集` (recruiting brands to carry), `卸売 お取引`, `業務用 化粧品` | Beautyworld Japan exhibitor list · COSME Week Tokyo · JETRO buyer lists · industry association member lists |

Language note: a page in the local language is still tier 1 when it is the company's own site. Quote it verbatim in `quote_or_summary` in the original language; the interpretation goes in `notes[]`, never in the quote.

**Caveat on exhibitor directories (matrix row 16).** Most show directories are now **client-rendered**: the page returns HTTP 200 with a large body and no exhibitor name anywhere in the HTML, so a page fetch harvests nothing and Core rule 9 forbids driving a headless browser to get at it. Measured 2026-09-13: the Gulf trade-fair exhibitor search this row points at returned 200 with a ~148 KB body in which the word "exhibitor" appeared 53 times and **not one exhibitor record** was present — the only company names in the markup belonged to event sponsors — and row 16 contributed **0 of 34** candidates. So prefer a **published exhibitor PDF, press release or post-show report** over the JS exhibitor search; if neither exists, log stop condition **S2 for row 16** and move on. Never cite a directory URL you did not read text from, and never count one you could not read as "searched". The same applies to the free-zone company directories and most national chamber directories — verify reachability *and* that names are in the returned text before relying on one as a tier-2 source.

---

## 5. Step 3 — once a domain is found, which pages to open, in order

**Row 0 — open the site root `/` first, always.** It is a tier-1 source in its own right (§1.1) and in a live 32-company run it supplied `company_type`, `country`, `wholesale_signal`, `korean_products_signal` and `contact_channels` for **24 of the 32** companies. It is also where you get the site's real paths.

**Rows 1–9 below are page *purposes*, not literal URLs.** Take the actual paths from the site's own navigation. Measured against 32 live UAE company sites on 2026-09-13, **none** used the literal paths this table used to prescribe; following them verbatim produces a run of 404s and zero evidence. The shapes you will actually meet:

| Platform shape | Typical real paths |
|---|---|
| Hosted storefront | `/pages/about-us`, `/pages/contact`, `/pages/brands`, `/pages/wholesale`, `/collections/<brand>` |
| CMS / blog engine | `/contact-us/`, `/about-us/`, `/top-brands/`, `/post/<slug>` |
| Site builder | `/b2b-enquiry`, `/service-detail/<slug>`, `/<section>/<slug>` |
| Localised | `/en/whole-sale`, `/<lang>/<section>` |
| Custom | `/sell-with-us`, `/<market>-importer`, `/become-a-partner` |

When the navigation is JS-rendered, or the site is large, **do not guess paths**: run matrix row 15 (`site:{domain} wholesale OR distributor OR partnership OR stockist`) and open what it returns.

| # | Purpose | Establishes | Fields it can fill |
|---|---|---|---|
| 0 | **Site root** `/` | Who they are, what they sell, whether there is a trade gate, how to reach them | `company_name`, `country`, `company_type`, `korean_products_signal`, `wholesale_signal`, `channels`, `contact_channels`, `product_categories` |
| 1 | **B2B gate** (wholesale / trade / b2b / trade-account) | B2B selling function, trade pricing, a published minimum order | `wholesale_signal`, `buyer_moq` (+ `buyer_moq_unit` / `buyer_moq_currency`), `channels`, `contact_channels(wholesale_form)`, `sourcing_signals(wholesale_inquiry_form, published_trade_terms)` |
| 2 | **Distribution role** (distributors / become-a-distributor / partners) | Distribution role, invitation to supply | `company_type`, `sourcing_signals(become_a_distributor_page, partnership_page)`, `channels(distributor_network)` |
| 3 | **Brand portfolio** (brands / our-brands / portfolio / a brand collection page) | Which brands are carried → Korean carriage | `korean_products_signal`, `korean_brands_carried`, `product_categories` |
| 4 | **Brand intake** (partnership / brand-submission / work-with-us / sell-with-us / suppliers) | Active brand intake | `partnership_signal`, `sourcing_signals(brand_submission_form, new_brand_inquiry, open_call_for_suppliers)`, `contact_channels(partnership_form)` |
| 5 | **Downstream network** (stockists / where-to-buy / retailers / store locator) | Downstream network → wholesale reality, channel mix | `channels`, corroborates `company_type` |
| 6 | **Reachability** (contact / contact-us / enquiry) | Official reachability | `contact_channels(contact_page, corporate_email, phone, wholesale_form, messenger)`, `operational_status` |
| 7 | **Identity** (about / company / who-we-are) | Legal name, country, function, founding, markets served | `company_name`, `country`, `company_type`, `country_name` |
| 8 | **Dated action** (news / blog / press / an official social post) | **Dated** sourcing actions and recency | `sourcing_intent` = `high`, `sourcing_signals(active_sourcing_post, rfq_posted, trade_show_attendance)`, `evidence.source_date` |
| 9 | **Catalogue** (catalogue / products / category nav) | Category coverage | `product_categories` |

**Page budget.** The minimum viable read is **the site root plus one B2B page** (row 1, 2 or 4). Stop opening pages as soon as the eight material claims (§8) are covered, or after 6 pages with no new material claim. Nine rows × a 30-candidate run is 270+ fetches for one request; §10's S5 caps the run.

### 5.1 Three retrieval outcomes, and only one of them is `stale`

| What happened | Record | Never |
|---|---|---|
| The page loaded and its newest dated content is clearly obsolete (older than the staleness threshold), or the site is archived | `stale: true`, keep the record (PRD test T07 requires a flagged low-confidence record, not a deletion) | Delete the candidate |
| **The domain itself does not resolve** (DNS failure) | `operational_status: "unreachable"` | Use this for any other failure |
| **The host answered but refused this retrieval method** — HTTP 403 / 429, a bot filter, a TLS handshake failure, or a certificate whose names do not cover the host — while `robots.txt` permits the path | **`blocked`**: retry the *same* URL with a different non-bypassing method, record the method that worked in `evidence.retrieval_method`, and name the failure in `notes[]`. If nothing permitted works, the facts stay `"unknown"` and the note says which method failed | Set `stale` or `operational_status: "unreachable"`. `stale` is a claim about **content age**; `unreachable` means **DNS** failed. Neither describes a transport refusal |

**Retrying with a different permitted method is not an access-control bypass.** Core rule 9 forbids defeating a control — login, CAPTCHA, paywall, `robots.txt`, rate limits — not accepting one tool's failure as the site's answer. Measured 2026-09-13: one UAE company site returned HTTP 403 to a reader-style fetch and HTTP 200 with 2,541 characters of readable Arabic to a plain HTTP GET of the same URL in the same minute, and it only became a scoreable record because a second permitted method was tried. Two others failed at the **TLS handshake** while live; with no permitted route to any page they ended with zero evidence and were dropped by DISC-06 — the honest outcome, not a defect, and §10 gives the exclusion line to write. Still forbidden and unchanged: no sign-in, no CAPTCHA solving, no identity spoofing to defeat a bot filter, no `robots.txt` override, no headless browser.

---

## 6. Step 4 — classify `company_type` and `channels`

`company_type` ∈ `distributor | importer | wholesaler | retailer | brand | marketplace | other | unknown`. `"other"` means *classified, none of the list fits*; `"unknown"` means *not yet determined*. Never interchangeable.

| Observed on the site | `company_type` | Corroborating fields |
|---|---|---|
| "We distribute X brands across {C}", exclusive-agency language, brand portfolio + retailer network | `distributor` | `channels += distributor_network` |
| "Importer of record", customs/registration language, "we import and supply" | `importer` | often also `wholesale_signal: true` |
| Trade-only pricing, case packs, MOQ, "trade account required" | `wholesaler` | `channels += wholesale` |
| Consumer prices, cart/checkout, store locator, no trade gate | `retailer` | `channels += retail_store` / `ecommerce` |
| Sells its **own** brand only, brand story, no third-party portfolio | `brand` | low B2B role unless a wholesale page exists |
| Third-party sellers onboard and list their own products | `marketplace` | `channels += marketplace` |
| Salon/spa supplier, pharmacy chain, duty-free operator that does not fit above | `other` + the matching `channels` value | `salon_spa`, `pharmacy`, `department_store`, `duty_free` |
| Nothing on the site says what they do | `unknown` | takes the unknown penalty — correct |

**Retail-with-a-wholesale-arm** is common: set `company_type` from the dominant function, add `wholesale` to `channels` with its own evidence, and the renderer appends ` / Wholesaler` when `wholesale_signal == true`. **Query-fit filter:** when `query.company_types` is non-empty and the candidate's **known** type is outside it, the candidate goes to `excluded[]` with a fit reason (`retailer, not a requested company type`); a candidate whose type is `"unknown"` is **never** excluded on that basis.

---

## 7. Step 5 — real sourcing intent vs. generic retail

This is the single highest-weight buyer dimension (25/100). Classify from evidence only.

| `sourcing_intent` | Entry condition | Typical page text |
|---|---|---|
| `high` | A **dated** public sourcing action within the evidence | "Now accepting new brands for 2026", a posted RFQ/tender, "open call for suppliers", a dated LinkedIn sourcing post, "meet us at {expo} to present your brand" with a date |
| `medium` | A **standing** partnership / wholesale page with no dated activity | "Become a distributor", "Brand submission form", "Wholesale enquiries" |
| `low` | B2B-capable but no invitation to supply | Trade account exists, but nothing invites new brands |
| `unknown` | Not determined | No such page found, or only a generic contact form |

`sourcing_signals[]` enumerates which distinct signals fired; each entry needs **≥ 1 evidence item** (INV-01). Allowed values — this list is the `buyer.schema.json` enum and every value is priced by `B-SI2` under the identical key: `rfq_posted`, `open_call_for_suppliers`, `new_brand_inquiry`, `active_sourcing_post`, `brand_submission_form`, `become_a_distributor_page`, `partnership_page`, `published_trade_terms`, `wholesale_inquiry_form`, `trade_show_attendance`.

**`published_trade_terms`** is the one a real importer emits most often and the one a discovery run most often misses: the buyer publishes the **terms on which it takes supply** — a stated minimum order (put the number in `buyer_moq` too, §8), a trade price list, or trade-only pricing behind an approval gate. A vague "low MOQs" or "competitive wholesale rates" is marketing, not terms: no entry.

**Retail noise — do not mistake these for sourcing intent:**

| Looks like intent | Actually | Correct handling |
|---|---|---|
| "Contact us" / generic enquiry form | Reachability only | `contact_channels(form \| contact_page)`; no `sourcing_signals` entry |
| "Careers", "Affiliate programme", "Influencer collaboration" | Not supply-side | Ignore; no field |
| "Stockists" list | Downstream retailers, i.e. they **sell** to shops | Supports `wholesale_signal` / `channels`, not `partnership_signal` |
| A consumer "Korean skincare" collection page on a pure retailer | K-Beauty fit, not B2B role | `korean_products_signal: true`, `product_categories`, but `company_type: retailer` |
| Marketplace seller listing on a third-party platform | Tier 4/5 signal about a seller, not a buyer's own intent | Supporting evidence only |
| "We are not accepting new brands at this time" | A **verified negative** | `partnership_signal: false` with evidence of that sentence; never silence-inferred |

PRD test T09: a wholesaler with no beauty business must end up **excluded** as a query-fit exclusion (`no K-Beauty signal (T09)`), not merely mid-scored — record `product_categories: []` (verified empty) with the evidence of that check, so the non-beauty adjustment can fire.

---

## 8. Step 6 — write the record: observed text → field → claim key

Every row: what you read → the buyer field → the `evidence.claim` key that must back it. Fields marked ★ are **material claims** (INV-01: backed by evidence or `"unknown"`, no third option).

| Observed page text | Buyer field | `evidence.claim` |
|---|---|---|
| Legal/company name as printed | `company_name` (+ `normalized_name` via `normalize_company.py`) | `company_name` |
| The site itself | `website`, `canonical_domain`, `alias_domains[]` | `website` |
| "Based in Dubai, UAE" / registered address | `country` ★ (alpha-2), `country_name` | `country` |
| Role language (§6) | `company_type` ★ | `company_type` |
| Category nav, portfolio, product pages | `product_categories[]` ★ (canonical slugs) | `product_categories` |
| "Korean skincare", named Korean brands | `korean_products_signal` ★, `korean_brands_carried[]` | `korean_products_signal`, `korean_brands_carried` |
| "Korea sourcing office", "we source from Korea" | `korean_products_signal` ★ | `korean_products_signal` |
| Trade pricing / case packs / trade account | `wholesale_signal` ★ | `wholesale_signal` |
| A published minimum order **as a number** — "Minimum Order: 10,000 AED per Purchase Order", "a minimum goods value of $5,000", "12 pieces per line for wholesale pricing" | `buyer_moq` + `buyer_moq_unit`, or `buyer_moq_currency` when the floor is **money** | `buyer_moq` |
| "Become a distributor", "Submit your brand" | `partnership_signal` ★, `sourcing_signals[]` | `partnership_signal`, `sourcing_signals` |
| Dated sourcing action | `sourcing_intent` ★ (`high`) | `sourcing_intent` |
| Channel evidence (stores, e-shop, salons, pharmacy, duty free, distributor network) | `channels[]` | `channels` |
| Partnership/wholesale/contact form URL, role email, switchboard, company LinkedIn | `contact_channels[]` ★ | `contact_channels` |
| "Permanently closed", dead domain, dead storefront | `operational_status`, `stale` | `operational_status` |
| Two sources disagree | keep both, add `conflicts[]` entry | both claims |

### 8.1 Agent-signal claim keys — the part the scorer prices and the fields above cannot carry

`score_buyer.py` fires three flavours of signal: a **field signal** read from a record field, a **claim signal** read from a canonical claim plus a structural predicate, and an **agent signal** that fires when the record carries an evidence item whose `claim` **equals the signal key itself**. Agent signals exist because no script can judge what a page *says*; recording one hands that judgement to the deterministic pass (PRD 13.2).

**They are worth up to 30 points on `kbeauty_korea_fit`, 45 on B-KF1, 20 on B-CR2 and 15 on B-CR3, and none can be reached through the field table above.** Measured on a live 20-company cohort: adding only these claim keys to evidence items **already in the records** — same URLs, same verbatim quotes, no new page read, no new fact — moved four companies by +5 to +7 and took the qualified count from 7 to 9. Two agents working the same evidence must not return different verdicts; write them.

| Observed page text | `evidence.claim` to write | What it feeds |
|---|---|---|
| "We source directly from Korea", "our Seoul sourcing office", "we import from Korean manufacturers" | `korea_sourcing_statement` | B-KF1, 45 pts. **Must be on a tier-1 official page** — it does not fire from third-party text |
| The company positions itself *as* a K-Beauty specialist ("the UAE's K-Beauty distributor", a K-Beauty-only storefront) | `kbeauty_specialist_positioning` | B-KF2, 30 pts |
| A dedicated Korean / K-Beauty **category or collection page** in the site's own navigation | `korean_category_page` | B-KF2, 22 pts |
| K-Beauty named in marketing copy without a dedicated section ("K-beauty innovations like…") | `korean_beauty_marketing_mention` | B-KF2, 12 pts |
| Any of the three above, when you cannot tell which | `kbeauty_positioning` | B-KF2 — marks the criterion **known** (no points) instead of taking the unknown penalty |
| Trade-only pricing, a price list behind a trade gate, "trade account required", a case-pack rule on a **buyer's** page | `b2b_pricing_or_trade_account` | B-CR2, 20 pts. A minimum order stated as a **number** is not this key — see the box below |
| A reseller / retailer programme page | `reseller_program_page` | B-CR2, 18 pts |
| A procurement, buying-team, vendor-application or supplier-portal page | `procurement_or_buying_page` | B-CR3, 15 pts |
| A retail chain's "supply to us" / vendor onboarding page | `retail_chain_supplier_page` | B-CR3, 12 pts |
| They state they operate their own distributor network | `distributor_network_operated` | B-CR3, 10 pts (also fires from `channels` containing `distributor_network`) |
| An explicit public statement that they are **not** taking new brands | `sourcing_closed_statement` | `sourcing_intent` adjustment, **−40**. Silence is never a closed statement |

Each is an ordinary evidence item: one claim, the URL you read, a verbatim quote, `observed_at`. An agent signal with no evidence item behind it does not exist — inventing one to lift a score is a Core-rule-1 violation, not a scoring technique. The full rubric is `references/qualification-rubric.md`.

**A published minimum order is a *field*, not an agent signal — and it is the highest-paying thing on a B2B buyer's page.** `buyer_moq` holds the three shapes real buyers print: a per-PO **money** floor (`buyer_moq: 10000`, `buyer_moq_currency: "AED"`), a per-line **piece** count (`buyer_moq: 12`, `buyer_moq_unit: "pieces_per_line"`), and a plain unit count. A known value fires `published_minimum_order` — **25 pts on B-CR2, level with `wholesale_signal_true` and above `b2b_pricing_or_trade_account`** — and also licenses `sourcing_signals += ["published_trade_terms"]` (B-SI2, 15 pts, §7). Evidence it under the claim key `buyer_moq`, quoting the sentence verbatim. Without the field a company that prints "Minimum Order: 10,000 AED per Purchase Order" but never writes the word *wholesale* takes the **unknown** path on B-CR2 while having published the single most material term a Korean seller needs — in a live 32-company cohort five companies published a concrete floor and every one of them survived only as free text. Two limits: a vague "low MOQs" is marketing, so leave the field unknown and put the phrase in `notes[]`; and a money floor is **never** converted to a quantity or to another currency (no rate is available here, and inventing one fabricates a term).

**Record scaffold (raw profile).** `score_version` is `"unscored"`, `qualification_score` is `0`, `dimension_scores` is absent, and `confidence` carries `0`, which `score_buyer.py` overwrites.

```json
{
  "schema_version": "0.1.0", "score_version": "unscored", "buyer_id": "BUY-gulfradiance-example",
  "company_name": "Gulf Radiance Trading LLC", "website": "https://gulfradiance.example",
  "canonical_domain": "gulfradiance.example", "country": "AE", "country_name": "United Arab Emirates",
  "company_type": "distributor", "product_categories": ["skincare", "sunscreen"],
  "korean_products_signal": true, "korean_brands_carried": ["Brand A", "Brand B"],
  "wholesale_signal": true, "buyer_moq": 5000, "buyer_moq_currency": "AED",
  "partnership_signal": true, "sourcing_intent": "high",
  "sourcing_signals": ["become_a_distributor_page", "active_sourcing_post", "published_trade_terms"],
  "channels": ["wholesale", "distributor_network", "pharmacy"],
  "contact_channels": [{"type": "partnership_form", "value": "https://gulfradiance.example/partner", "label": "Brand Partnership", "evidence_ids": ["EV-004"]}],
  "operational_status": "active", "status": "VERIFIED",
  "qualification_score": 0, "confidence": 0, "evidence": [],
  "source_query": {"country": "United Arab Emirates", "product_categories": ["sunscreen"],
    "keywords": ["UAE Korean skincare distributor"], "category_drift": false},
  "notes": []
}
```

`buyer_id` = `BUY-` + `canonical_domain` with `.` and `-` collapsed to `-`; when the domain is `"unknown"`, `BUY-<country lowercased>-<normalized_name slug>`. `status`: `DISCOVERED` once ≥ 1 evidence item with a resolvable URL exists; `VERIFIED` once ≥ 1 material claim rests on `source_tier <= 3`. Never write `QUALIFIED` or later from this mode.

**Evidence record — one claim, one source, one observation time:**

```json
{
  "schema_version": "0.1.0", "evidence_id": "EV-001", "claim": "korean_products_signal", "value": true,
  "source_url": "https://gulfradiance.example/brands", "source_domain": "gulfradiance.example",
  "source_type": "official_site", "source_tier": 1, "is_official": true,
  "observed_at": "2026-09-12T09:14:00Z", "source_date": "unknown", "confidence": 0.90,
  "quote_or_summary": "Korean Skincare — our portfolio of Korean brands for the GCC market.",
  "retrieval_method": "page_fetch"
}
```

Checklist per evidence item: one claim only · `observed_at` is when **you** read it (never the page's date, never later than `--as-of` end-of-day) · `source_date` only if the page prints one, else `"unknown"` · quote verbatim ≤ 300 chars with **no interpretation** · `inferred: true` when the value was reasoned rather than read · `evidence_id` unique in the document, first-seen order, never renumbered.

---

## 9. Step 7 — category drift logging (DISC-05)

Widen only after the matrix rows for the requested category are exhausted, one level per pass: child → parent (`sunscreen` → `suncare`), then parent → adjacent parent in the same group (`suncare` → `skincare`, group G1), then a category-free B2B query in the same market (`{C} Korean cosmetics distributor`).

For **every candidate first seen after a widening pass** (drift is per candidate, not per run): set `source_query.category_drift = true`; put the widened strings in `source_query.keywords[]`; and append a note naming both categories — `"category drift: requested sunscreen, found via suncare/skincare query (DISC-05)"`. A candidate found before any widening keeps `category_drift: false`. Never leave it absent-and-forgotten; absent is read as `false`, so an unlogged drift silently hides the discount.

**Do not use this ladder to compensate for a leaf category.** If the requested category is one buyers do not merchandise as a section (`sunscreen`, `ampoule`, `cushion`), narrow it at **Step 0** instead (§2) — a Step-0 narrowing is recorded in `notes[]` and leaves `category_drift` false. Drifting the whole cohort on pass 1 applies `category_drift_flagged` (−10) uniformly: it changes no relative order and moves every candidate across the fixed threshold at once (measured: qualified 7 → 5, rank order unchanged).

---

## 10. Step 8 — stop conditions

Stop the **whole run** when the first of these fires, then report:

| # | Condition | Report |
|---|---|---|
| S1 | The requested count of candidates that clear the evidence bar is reached | normal output |
| S2 | Matrix §3 exhausted (English + local language) **and** 3 consecutive queries returned no new `canonical_domain` | note: `"query matrix exhausted"` |
| S3 | Both widening levels exhausted and the count is still short | `next_action` = `Widen the query — {n} of {N} requested candidates met the evidence bar` |
| S4 | Tool/time budget reached mid-run | envelope `partial: true` + a note per unfinished candidate (`"<id>: <reason>"`), exit stays successful |
| S5 | **Total pages opened exceeds `4 × count`** | Stop opening pages. Set `partial: true` and note which candidates were worked from the site root only (`"<id>: site root only (S5 page budget)"`). Score what you have |

`4 × count` is a real bound, not a formality: a live 34-candidate run was completed on roughly 40 page fetches by reading the site root plus at most one B2B page each. Past the budget and still short is an S3 result — say so — not a reason to keep fetching. Per-candidate stop: all eight material claims covered, **or** 6 pages opened without a new material claim, **or** §5.1 leaves you with no permitted way to read the site.

**Evidence quality outranks count (PRD DISC-06, R7.8.1).** The requested count is a ceiling, never a target. A candidate with **no evidence item on any material claim** goes to `excluded[]`, never into the ranked list to make the number look better. Two different things end up there, and the reason line must say which:

| Case | `reason_summary` | Why it is excluded and not "unverified" |
|---|---|---|
| No evidence item at all — §5.1 left no permitted way to read the site | `no evidenced material claim (no source could be opened)` | There is nothing to score and nothing to show a reviewer. The exclusion line is the honest report: the company may well be real |
| Evidence exists, but none of it covers a material claim | `no evidenced material claim (evidence covers no material claim)` | Same bar; the gap is coverage, not access |

**"Unverified" is a different state and it is not an exclusion.** A candidate that *has* evidence but no **official** item (nothing with `is_official: true`) on a material claim is **scored and ranked**, and the renderer appends the literal ` — unverified` to its header line (`references/output-format.md` §10.1, PRD 15.1 criterion 3). That is the case PRD 15.1 is about: third-party-sourced candidates are kept and marked, not dropped.

---

## 11. Step 9 — hand-off checklist before scoring

- [ ] Every record carries `schema_version`, `score_version: "unscored"`, `buyer_id`, `company_name`, `canonical_domain`, `country`, `company_type`, `status`, `qualification_score: 0`, `confidence: 0`, `evidence`, `notes`.
- [ ] Every material claim is evidence-backed **or** `"unknown"` / absent. Nothing inferred to dodge a penalty.
- [ ] The §8.1 agent-signal claim keys are written wherever the page text licenses them — they are the only route to those points, and omitting them changes the qualified set.
- [ ] Every published minimum order is in `buyer_moq` (with `buyer_moq_unit`, or `buyer_moq_currency` when it is money) and evidenced under `claim: "buyer_moq"` — not left in a quote or a note — and the matching `published_trade_terms` entry is in `sourcing_signals[]`.
- [ ] No named individual, personal email, direct dial or personal handle anywhere — `notes[]` and quotes included.
- [ ] `country` is alpha-2 upper case; categories are canonical slugs, unmapped ones preserved **with a note**; verified negatives (`false`, `[]`) each carry evidence **of the check itself**.
- [ ] Duplicates: run `normalize_company.py` then `dedupe_companies.py`; `www`/non-www variants collapse by `canonical_domain` (test T06) and IDN hosts fold to their punycode form (the BUILD-CONTRACT 8.1 `canonical_domain` cases in `tests/fixtures/normalize.cases.json`); same-name+country but different domains are **not** auto-merged — both records get the "possible duplicate" note.
- [ ] Drift flagged per candidate; `source_query.keywords[]` holds the strings actually run.
- [ ] Then: `score_buyer.py --query <query-surface.json> --as-of 2026-09-12`, and render per §10.1 of `references/output-format.md`.

---

## 12. Worked trace — `country="UAE" category="sunscreen" count=30`

**Query surface** — §2 is normative; this is an illustration of it, not a template to copy.

```json
{
  "country": "AE", "country_name": "United Arab Emirates", "destination_country": "AE",
  "product_categories": ["sunscreen"], "channels": ["wholesale"],
  "company_types": ["distributor","importer","wholesaler"],
  "keywords": ["UAE Korean skincare distributor","UAE K-beauty wholesaler","موزع مستحضرات تجميل كورية"],
  "category_drift": false, "vertical": "K-Beauty"
}
```

`region_countries` and `adjacent_region_countries` are **absent**, because the operator said `"UAE"` and not `"GCC"`: a single-country request expands nothing. A Saudi company found by this run therefore takes `country_mismatch` (−40) and ranks accordingly — the requested behaviour. Had the operator said "GCC", §2 row 2 would fill `region_countries` and the same company would score `country_in_requested_region` (+35) instead.

**Queries run (order, per §3)** — rows 1–6 in English, rows 2/3 repeated in Arabic; then the promoted rows 7 and 10 (`UAE Korean cosmetics "MOQ"`, `UAE cosmetics trading company wholesale Korean`); then 8, 9 and 11–14; then row 16 against the Beautyworld Dubai exhibitor search, which returned no exhibitor names in the HTML (§4 caveat) and was logged **S2 for row 16**; then row 15 per domain found. Rows 18–19 were not needed.

**Candidate walk-through** — search row 1 returns `gulfradiance.example`.

| Page opened | Read | Written |
|---|---|---|
| `/` (site root, opened first — §5 row 0) | Nav: Brands · Wholesale · Brand Partnership · Contact. Strapline "Korean beauty for the GCC trade." | `korean_products_signal: true`, `company_type` candidate, and the **real paths** for every row below → `EV-009` (tier 1) |
| `/about` | "Gulf Radiance Trading LLC, Dubai-based importer and distributor for the GCC beauty market." | `country: "AE"`, `company_type: "distributor"` → `EV-002` (tier 1) |
| `/brands` | "Korean Skincare — our portfolio of Korean brands for the GCC market." + two named Korean brands | `korean_products_signal: true`, `korean_brands_carried`, `product_categories += ["skincare"]` → `EV-001`, plus the agent signal `claim: "korean_category_page"` (§8.1) |
| `/products/suncare` | SPF50+ listings from Korean brands | `product_categories += ["sunscreen"]` → `EV-003` |
| `/wholesale` | "Trade accounts. Wholesale price list on request. Minimum order AED 5,000 per order." | `wholesale_signal: true`, `channels += ["wholesale"]`, **plus the agent signal** `claim: "b2b_pricing_or_trade_account"` with the sentence quoted verbatim (§8.1) → `EV-005`. The **number** is a field, not a quote: `buyer_moq: 5000`, `buyer_moq_currency: "AED"` under `claim: "buyer_moq"` → `EV-010`, and `sourcing_signals += ["published_trade_terms"]` (§7). That one field is `published_minimum_order`, 25 pts |
| `/partner` | "Brand Partnership — submit your brand for our 2026 GCC portfolio." | `partnership_signal: true`, `contact_channels(partnership_form)`, `sourcing_signals += ["become_a_distributor_page"]` → `EV-004` |
| `/news` (dated 2026-08-21) | "We are now accepting new suncare brands for Q4 listings." | `sourcing_intent: "high"`, `sourcing_signals += ["active_sourcing_post"]`, `source_date: "2026-08-21"` → `EV-006` |
| `/contact` | `info@gulfradiance.example`, switchboard number, Dubai address | `contact_channels(corporate_email, phone)` → `EV-007`; **no** individual's name or direct line recorded |
| Beautyworld Dubai **published exhibitor PDF** (the JS exhibitor search carried no names — §4) | the company's own exhibitor entry, 2026 edition | `sourcing_signals += ["trade_show_attendance"]` → `EV-008`, `source_type: "trade_show"`, tier 2, `is_official: true` |

Annual import volume is nowhere published → it stays out of the record, the criterion it would feed reports `unknown`, and it surfaces on the `Missing:` line as `확인 필요`. Do **not** estimate it.

**Drift case in the same run.** After the sunscreen rows ran out at 11 candidates, pass 1 widened to `suncare` and pass 2 to `skincare`. `emirateskin.example` was first seen on a `skincare` query, so it carries `source_query.category_drift = true`, the widened keywords, and the note `"category drift: requested sunscreen, found via skincare query (DISC-05)"`.

**Rendered result (shape per `references/output-format.md` §10.1)**

```
Summary
- Query: United Arab Emirates / K-Beauty / sunscreen
- Candidates found: 26 · Qualified (>=70): 9
- As of: 2026-09-12 · Score version: kbtm-score-0.1.0

Top Candidates
1. Gulf Radiance Trading LLC — 92/100 — HIGH
   Website: https://gulfradiance.example · Country: United Arab Emirates · Type: Distributor / Wholesaler
   Why: Korean skincare portfolio + wholesale trade accounts + dated new-brand call
   Contact: Brand Partnership form — https://gulfradiance.example/partner
   Evidence: [https://gulfradiance.example/brands], [https://gulfradiance.example/partner] (+6 more)
   Missing: annual import volume

Excluded / low fit
- Desert General Trading LLC: no K-Beauty signal (T09)
- Arabian Glow Retail: retailer, not a requested company type

Recommended next action: Draft outreach to top 5
```

Twenty-six candidates against a request for thirty is the **correct** outcome when four candidates could not clear the evidence bar: the run says so in `notes[]` and in `Recommended next action`, and never pads the list.
