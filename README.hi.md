# K-Beauty Trade Matchmaker

**एक Agent Skill जो public web पर विदेशी K-Beauty buyers और कोरियाई sellers को ढूँढ़ता है, हर अहम दावे को ऐसे source से verify करता है जिस पर आप क्लिक कर सकें, deterministic scripts से दोनों पक्षों को score करता है, खरीद अनुरोधों (buying requests) को sellers से match करता है, और एक outreach draft पर रुक जाता है ताकि कोई व्यक्ति उसे review कर सके।**

यही folder बिना किसी बदलाव के **Claude Code** और **OpenAI Codex** दोनों में चलता है। Python में **केवल standard library** (3.9–3.14) का उपयोग होता है; `pip install` करने के लिए कुछ भी नहीं है।

[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [प्रोजेक्ट पेज](https://kbeauty.tradewith.kr/) · [LinkedIn](https://www.linkedin.com/in/hm-choi)

![सही K-Beauty trade partner खोजें](kbeauty-trade-partner-linkedin-cities.png)

> Workflow की एक झलक: खोजें, verify करें, match करें, और अंतिम समीक्षा इंसान के हाथ में रखें।

> **स्थिति: v0.3.0.** Pipeline को काल्पनिक fixtures पर 536 cases के साथ test किया गया है और live web पर एक बार trial किया गया है। Scoring rubric को **अभी तक वास्तविक नतीजों के आधार पर validate नहीं किया गया है**: scores reproducible और traceable हैं, पर अभी यह ज्ञात नहीं कि वे भविष्यवाणी करने में सक्षम हैं। v0.2.0 ने इसे मापने के लिए tooling जोड़ा ([Scores को validate करना](#scores-को-validate-करना)), पर अभी तक कोई labelled sample मौजूद नहीं है। RFQ Matching और Outreach Draft को live data पर नहीं चलाया गया है। किसी score पर भरोसा करने से पहले [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) पढ़ें।

---

## नया क्या है

**v0.3.0 (2026-09-19).** किसी भी मौजूदा score में कोई बदलाव नहीं। सब कुछ नया scoring pipeline के बाहर है।

- **Run diff.** `scripts/diff_runs.py` एक ही search या RFQ के दो scored runs की तुलना करता है और नई तथा हट चुकी companies, बदले हुए exclusions, और score, rank, qualified-flag, confidence तथा Missing पंक्ति में हुए बदलावों की सूची देता है। अलग-अलग rubric versions से score किए गए runs को यह अस्वीकार कर देता है और कोई contact details copy नहीं करता। देखें [दो runs की तुलना](#दो-runs-की-तुलना)।
- **Re-check queue.** `scripts/stale_evidence.py` बताता है कि कौन-से stored records और evidence pages दोबारा पढ़ने हैं, सबसे ज़रूरी पहले। यह कुछ भी fetch नहीं करता और किसी record या score को नहीं बदलता। देखें [Re-check queue](#re-check-queue)।
- **Lead export.** `scripts/export_leads.py` किसी scored run को spreadsheet/CRM CSV के रूप में या TradeWith admin bulk-import file के रूप में लिखता है। यह केवल एक file लिखता है। TradeWith rows में कोई contact fields नहीं होते और वे tier C के रूप में आती हैं ताकि कोई admin उनकी review करे। देखें [Spreadsheet, CRM या TradeWith में export](#spreadsheet-crm-या-tradewith-में-export)।
- **MCP tools के रूप में scripts.** एक वैकल्पिक local tool server (MCP, stdio) किसी agent को ग्यारह scripts को tools के रूप में call करने देता है। यह केवल एक project folder के भीतर पढ़ता और लिखता है, कभी किसी file को overwrite नहीं करता और कुछ भी नहीं भेजता। देखें [Scripts को MCP tools के रूप में उपयोग करें](#scripts-को-mcp-tools-के-रूप-में-उपयोग-करें)।
- **Plugins.** Claude Code इस repository से skill को plugin के रूप में install कर सकता है। ChatGPT और Codex को हर release के साथ एक skills-only plugin ZIP मिलता है, और इसी तरह skill web और mobile पर ChatGPT तक पहुँचता है। देखें [Plugin के रूप में install करें](#plugin-के-रूप-में-install-करें)।
- Tests: 252 → 536 cases.

v0.2.0 ने [score-validation tooling](#scores-को-validate-करना) और भारत, इंडोनेशिया तथा तुर्किये के लिए [market packs](#market-packs) जोड़े थे। हर release के पूरे notes: [CHANGELOG.md](CHANGELOG.md) (changelog केवल अंग्रेज़ी में है)।

---

## सुरक्षा सीमा: यह skill कुछ भी नहीं भेजता

इस package के बारे में सबसे महत्वपूर्ण तथ्य: **इसमें ऐसा कोई code नहीं है जो message भेज सके।**

- **केवल draft.** Outreach का काम हमेशा `READY_FOR_REVIEW` state पर ख़त्म होता है, और draft पर `auto_send: false` तथा `manual_approval_required: true` रहता है।
- **मंज़ूरी इंसान देते हैं, भेजने का काम बाहरी systems करते हैं।** Skill किसी lead को `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` तक ले जा सकता है, इससे आगे नहीं। `APPROVED_FOR_OUTREACH` और उसके बाद के चरण किसी CRM और व्यक्ति के अधिकार में हैं; adapter इन transitions को **अस्वीकार** कर देता है।
- **SMTP, Gmail, SES या webhook से भेजने का कोई code नहीं।** निजी emails या phone numbers का अनुमान लगाना या उन्हें bulk में इकट्ठा करना नहीं (केवल company-level channels)। CAPTCHA, logins, paywalls, robots.txt या terms of service को bypass करना नहीं।
- **कुछ भी गढ़ा नहीं जाता।** MOQ, certifications, export markets, exclusivity और capacity तभी दर्ज किए जाते हैं जब कोई source उन्हें बताता है; अन्यथा वे `"unknown"` रहते हैं। "एक buyer आपका इंतज़ार कर रहा है" जैसी बिना आधार वाली जल्दबाज़ी template स्तर पर ही प्रतिबंधित है।
- **कोई कानूनी निर्णय नहीं।** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) बताता है कि कहाँ जाँच ज़रूरी है; यह कभी यह निष्कर्ष नहीं निकालता कि भेजना अनुमत है।

यह एक design निर्णय है, कोई छूटा हुआ feature नहीं। *Draft बनाना* और *भेजना* अलग-अलग permissions हैं, और इस package के पास केवल पहली permission है।

---

## यह क्या करता है

यह Google, LinkedIn और trade-show directories में पूरे दिन की खोज को **एक verifiable shortlist में बदल देता है, जिसमें हर दावा अपने source से linked होता है**।

1. **Discover:** देश, category, OEM/ODM, MOQ और certification के आधार पर public web पर buyer और seller candidates ढूँढ़ता है।
2. **Verify:** हर दावे को भरोसेमंद sources से जाँचता है, आम तौर पर company की अपनी site से, और उसे उसके URL और देखे जाने के समय के साथ store करता है।
3. **Normalize:** domains और company names को normalize करता है, duplicates को merge करता है, और buyer की requirements तथा seller की capabilities को एक ही schema पर लाता है।
4. **Score:** deterministic Python scripts से buyers और sellers को score करता है। एक ही input और एक ही `--as-of` पर हमेशा एक ही output मिलता है।
5. **Match:** किसी खरीद अनुरोध (RFQ) को sellers से match करता है: पहले hard filters, फिर weighted score, फिर एक सीमित semantic rerank, फिर unknown handling। यह top N लौटाता है **और हर अस्वीकृत seller को अस्वीकार करने का कारण भी**।
6. **Draft:** केवल verified तथ्यों से personalised outreach तैयार करता है, और वहीं रुक जाता है।

---

## चार modes

Agent से सामान्य भाषा में पूछें। नीचे दिए गए रूप हर mode के parameters का संक्षिप्त रूप हैं।

### Buyer Discovery

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

UAE में K-Beauty बेचने वाले distributors, importers और wholesalers को ढूँढ़ता है, और company type, उनके पास मौजूद brands, wholesale उपलब्धता, partnership signals, आधिकारिक contact channels और evidence URLs को normalise करता है।

### Seller Discovery

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

कोरियाई manufacturers और brands को ढूँढ़ता है, और OEM/ODM क्षमता, MOQ, certifications, मुख्य products तथा export या partnership signals को verify करता है।

### RFQ Matching

```
/kbeauty-match rfq="#134" top=10
```

RFQ के product, MOQ, destination, certifications और private-label requirement के आधार पर sellers को filter करता है, quantitative और qualitative scores को जोड़ता है, और top 10 के साथ exclusion के कारण लौटाता है। "कोई qualified match नहीं", अपने कारणों के साथ, एक सामान्य output है।

### Outreach Draft

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

केवल verified तथ्यों से subject, body और personalisation points लिखता है। कमज़ोर evidence को सामान्य रूप में लिखा जाता है या "needs verification" के रूप में चिह्नित किया जाता है। **यह भेजता नहीं है।**

---

## उदाहरण output

Bundled test fixture पर RFQ Matching का एक अंश। Sellers काल्पनिक हैं।

```
RFQ #134
Destination: United Arab Emirates
Product: Sunscreen
MOQ: <= 3,000
Commercial model: Private label
ISO22716: Required

Matches
1. Hansol ODM Corp — Match 99/100
   Product Fit: 100
   Model Fit: 98
   MOQ: 95
   Compliance: 100
   Market Fit: 100
   Evidence Quality: 100
   Risks: none
   Missing: none
...
9. Yeonhwa Lab Co., Ltd. — Match 79/100
   MOQ: 43
   Risks: seller.moq not published; hard filter HF-03 skipped.
   Missing: MOQ against the buyer ceiling; Capacity against the order quantity; Destination-market registration

Excluded
- Daehan Sun Care Co., Ltd.: MOQ minimum 5,000 exceeds RFQ max 3,000
- Haneul Bio Co., Ltd.: Required certification ISO22716 not held (verified list: none)
- Jinheung Cosmetics Co., Ltd.: Does not supply AE (market excluded by the seller)

Summary: 20 considered · 11 passed hard filter · 10 returned · 11 qualified
```

Seller 9 पर ध्यान दें: इसका MOQ प्रकाशित नहीं है, इसलिए MOQ hard filter **skip किया गया, fail नहीं**। यह चुपचाप अस्वीकार या चुपचाप पास किए जाने के बजाय, कम operational score और एक स्पष्ट "Missing" पंक्ति के साथ सूची में बना रहता है।

---

## Package की संरचना

```
kbeauty-trade-matchmaker/
├── SKILL.md                      # Runtime entry point: frontmatter (name/description), four modes, workflow
├── install.sh                    # Installs into either runtime's skills directory (symlink by default)
├── adapters/
│   ├── tradewith_adapter.py      # Internal-data boundary. File and HTTP backends; imports without credentials
│   └── tradewith_adapter.md      # Adapter setup, usage, and what it refuses to do
├── references/                   # Progressive disclosure: loaded only when a mode needs them
│   ├── buyer-discovery.md        # Buyer query expansion, per-country search patterns, stop conditions
│   ├── seller-discovery.md       # Korean seller queries, OEM/ODM and MOQ page patterns, directories
│   ├── qualification-rubric.md   # Narrative explanation of the scoring dimensions
│   ├── matching-rules.md         # Hard filters, weighted score, rerank, unknown handling; HF-00..HF-08
│   ├── calibration-notes.md      # What the live trials measured and changed, and what is unvalidated
│   ├── evidence-policy.md        # Fact vs inference vs unknown, source tiers, observed_at, conflicts
│   ├── outreach-guidelines.md    # Draft-only rules, evidence-backed personalisation, banned phrasing
│   ├── compliance-notes.md       # Per-jurisdiction direct-marketing cautions, data minimisation
│   ├── data-contract.md          # Bundled summary of the schema and normalisation contract
│   ├── output-format.md          # Output rendering contract, bundled
│   └── runtime-adapters.md       # Portability layer; the only file that names vendor tools
├── schemas/                      # Data contracts, checked by the bundled validator
│   ├── buyer.schema.json
│   ├── seller.schema.json
│   ├── rfq.schema.json
│   ├── evidence.schema.json      # The smallest unit: one source, one claim
│   ├── match-result.schema.json  # One complete matching run
│   ├── discovery-result.schema.json
│   ├── acceptance-report.schema.json  # One calibration measurement (v0.2.0)
│   ├── run-diff.schema.json      # One comparison of two scored runs (v0.3.0)
│   ├── recheck-queue.schema.json # The evidence to re-read (v0.3.0)
│   ├── tradewith-bulk-buyers.schema.json  # TradeWith bulk-import body; no contact fields (v0.3.0)
│   └── scoring.config.json       # Every weight, threshold and penalty lives in this one file
├── scripts/                      # Standard library only. No network, no credentials
│   ├── _common.py                # Config, rounding, normalisation, tri-state helpers, schema validator
│   ├── normalize_company.py
│   ├── dedupe_companies.py
│   ├── score_buyer.py
│   ├── score_seller.py
│   ├── score_match.py
│   ├── validate_output.py        # Schema plus contract invariants
│   ├── make_review_sheet.py      # Blind CSV review sheet for a trade operator (v0.2.0)
│   ├── acceptance_report.py      # Review sheets + scored runs -> Human Acceptance Rate report (v0.2.0)
│   ├── diff_runs.py              # Two scored runs -> what changed between them (v0.3.0)
│   ├── stale_evidence.py         # Stored records -> evidence to re-read, most urgent first (v0.3.0)
│   ├── export_leads.py           # Scored run -> CSV or TradeWith import file; never sends (v0.3.0)
│   └── mcp_server.py             # Optional stdio MCP server over the scripts above (v0.3.0)
├── templates/
│   ├── buyer_outreach.md
│   ├── seller_outreach.md        # With and without an RFQ
│   └── legal_notices.md          # Per-jurisdiction, per-channel notice blocks
└── tests/
    ├── cases.md                  # Test case specification, including negative and edge cases
    ├── run_tests.py
    └── fixtures/                 # Golden inputs and expected outputs; all companies fictional
```

केवल `kbeauty-trade-matchmaker/` folder install होता है। Runtime को जिस contract सामग्री की ज़रूरत होती है, वह `references/data-contract.md` और `references/output-format.md` में bundled है।

Repository स्तर पर, package के बाहर (ship नहीं होता):

```
.claude-plugin/marketplace.json   # Claude Code plugin marketplace: one plugin, the package folder
packaging/openai/plugin.json      # Manifest of the ChatGPT/Codex plugin ZIP
tools/build_release.py            # Builds both release ZIPs from the HEAD commit, deterministically
```

---

## Scoring कैसे काम करती है

Scores **deterministic scripts** से आते हैं, किसी model की धारणा से नहीं।

Buyers को छह dimensions पर score किया जाता है: `kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10। Sellers को भी छह पर: `product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10। हर dimension criterion-स्तर के points को जोड़ता है और लागू होने वाले points के आधार पर 0–100 पर normalise करता है।

Matching चार चरणों में, इसी क्रम में चलती है:

1. **Hard filters** `HF-01..HF-08`, साथ में rendering gate `HF-00`। किसी failure पर प्रक्रिया बीच में नहीं रुकती; exclusion का **हर** कारण इकट्ठा किया जाता है।
2. **Weighted score:** `product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10। ये seller के अपने छह dimensions ही हैं, जिन्हें RFQ के सापेक्ष फिर से score किया जाता है, ताकि किसी seller को कभी दो अलग-अलग rubrics से न आँका जाए।
3. **सीमित semantic rerank.** यही एक चरण है जिसे model तय करता है। इसकी एक सीमा है, इसके लिए evidence सहित कारण देने ज़रूरी हैं, और यह hard filter में fail हुए किसी candidate को कभी वापस नहीं ला सकता।
4. **Unknown handling.**

तीन नियम सबसे अहम हैं:

- **Unknown का मतलब शून्य नहीं है।** Unknown value को उस criterion के अधिकतम points का 30% मिलता है (`neutral_base` 50 × `penalty_factor` 0.6)। केवल इसलिए किसी candidate को अस्वीकार करना कि कोई value unknown है, वर्जित है, और penalty से बचने के लिए कोई value अनुमान से भरना भी वर्जित है।
- **घड़ी से समय नहीं पढ़ा जाता।** समय से जुड़ी सारी गणना `--as-of` का उपयोग करती है, इसलिए एक ही input आप कभी भी चलाएँ, वही numbers देता है।
- **Confidence का मतलब quality नहीं है।** Confidence इस सवाल का जवाब देता है कि "इस record पर कितना भरोसा किया जा सकता है", इसे `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW` के रूप में दिखाया जाता है, और यह कभी qualification score को नहीं पढ़ता।

जिस buyer के पास K-Beauty का कोई भी evidence नहीं है, उसे फिर भी score और rank किया जाता है, पर उसके बाकी dimensions कितने भी मज़बूत हों, उसे कभी qualified चिह्नित नहीं किया जा सकता। Default qualification threshold एक निश्चित 70 है; percentile mode भी implement किया गया है। हर tunable number `schemas/scoring.config.json` में है।

## Evidence कैसे काम करता है

Output की इकाई "एक company" नहीं, बल्कि **evidence सहित एक दावा** है। एक evidence item में एक `claim`, उसका source URL, source tier, सामग्री की तारीख़ (`source_date`), उसे कब देखा गया (`observed_at`), वह तथ्य है या अनुमान, और एक छोटा quote होता है।

| Tier | Source | Points |
|---|---|---|
| 1 | Company की आधिकारिक site | 100 |
| 2 | आधिकारिक trade-show, association, सरकारी या trade-agency directories; internal records | 82 |
| 3 | आधिकारिक LinkedIn और social profiles | 64 |
| 4 | प्रतिष्ठित third-party directories और press releases | 46 |
| 5 | Community posts और blogs (केवल सहायक signal) | 20 |

Points को सामग्री की उम्र से गुणा किया जाता है: 90 दिनों के भीतर 1.00, एक साल के भीतर 0.92, दो साल के भीतर 0.80, उससे पुराना 0.60, बिना तारीख़ 0.85। उम्र हमेशा `source_date` से मापी जाती है, `observed_at` से कभी नहीं, क्योंकि हमने कोई page कब पढ़ा, इससे यह पता नहीं चलता कि उसकी सामग्री कितनी पुरानी है।

दो states को जान-बूझकर अलग रखा गया है:

- **`unverified`**: record के पास evidence है, पर किसी भी अहम दावे का आधिकारिक (tier 1) source नहीं है। इसे score और rank किया जाता है और `— unverified` marker के साथ दिखाया जाता है। इसे **बाहर नहीं** किया जाता।
- किसी भी अहम दावे पर **कोई evidence ही नहीं**: scoring से पहले record को `excluded[]` में भेज दिया जाता है, कारण के साथ ("site खोली नहीं जा सकी" या "पढ़ी गई, पर कोई अहम दावा नहीं मिला")। Ranked list को बिना evidence वाली companies से भर देना ठीक वही गड़बड़ी है जिसे यह रोकता है।

Storage न्यूनतम है: दावा, URL, देखे जाने का समय और एक छोटा quote; पूरे pages या अनावश्यक personal profiles कभी नहीं। परस्पर विरोधी sources को `conflicts[]` में दर्ज किया जाता है ताकि कोई व्यक्ति उन्हें देख सके, उन्हें कभी चुपचाप सुलझाया नहीं जाता, और conflicts से confidence घटता है।

---

## Scores को validate करना

यहाँ scores reproducible हैं, पर अभी तक किसी ने उन्हें किसी व्यक्ति के निर्णय से मिलाकर नहीं जाँचा है। v0.2.0 में जोड़े गए दो standalone scripts से आप यह जाँच कर सकते हैं। ये किसी score को नहीं बदलते।

```bash
cd kbeauty-trade-matchmaker

# 1. Make a blind sheet from a scored run. No score, rank or qualified flag; rows in a fixed shuffled order.
python3 scripts/make_review_sheet.py --input out/buyers.scored.json --include-excluded --output out/review.csv

# 2. A trade operator fills in verdict (accept / reject / unsure), a reason_code for each reject,
#    their role, and the date. Then:
python3 scripts/acceptance_report.py --scored out/buyers.scored.json --reviews out/review.csv --as-of 2026-09-19 --pretty
```

Report बताती है कि review की गई companies में से operator ने कितना हिस्सा accept किया: कुल मिलाकर, और `qualified` flag, score band, देश तथा reject reason के अनुसार अलग-अलग। यह दिखाती है कि 50 से 90 तक का हर threshold कितना precision और recall देता। Total score और छह dimensions में से हर एक के लिए, यह दिखाती है कि score accept की गई और reject की गई companies को कितनी अच्छी तरह अलग करता है, और उस dimension ने कितनी अलग-अलग values दीं। यह उन excluded companies की सूची भी देती है जिन्हें operator accept कर लेता।

30 से कम decided reviews होने पर report ख़ुद को `insufficient_sample` चिह्नित करती है और कहती है कि वह किसी weight या threshold में बदलाव को उचित नहीं ठहरा सकती। यह ऐसी sheets को अस्वीकार कर देती है जिनमें unknown verdicts हों, बिना reason वाले rejects हों, परस्पर विरोधी duplicates हों, अलग-अलग `score_version` के अंतर्गत score किए गए runs हों, या ऐसे notes हों जिनमें कोई email address या phone number हो। Reviewers की पहचान role से होती है, नाम से कभी नहीं।

[`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) §7 बताता है कि sample कैसे बनाया जाए (कम से कम दो देश और दो categories, 100 से 200 reviewed companies) और कौन-सा नतीजा हर खुले calibration प्रश्न को सुलझाएगा। यह केवल Human Acceptance Rate (इंसानी स्वीकृति दर) को मापता है। कोई contacted lead RFQ बनता है या नहीं, यह जानने के लिए application layer के outcome data की ज़रूरत है।

## Market packs

v0.2.0 भारत, इंडोनेशिया और तुर्किये को जोड़ता है। कोई scoring नियम नहीं बदला; एक नया test fixture (destination इंडोनेशिया, halal आवश्यक) दिखाता है कि मौजूदा rubric इन्हें पहले से ही संभाल लेता है।

| | भारत | इंडोनेशिया | तुर्किये |
|---|---|---|---|
| Buyer search की भाषा | पहले अंग्रेज़ी, साथ में हिन्दी | इंडोनेशियाई | तुर्की |
| Matching में उपयोग होने वाला market-entry नियम | CDSCO import registration | BPOM notification; cosmetics के लिए अनिवार्य halal certification (तारीख़ `--as-of` पर निर्भर) | ÜTS के ज़रिए TİTCK notification |
| Company names से हटाए जाने वाले legal forms | `Pvt Ltd`, `Private Limited`, `LLP` | `PT`, `CV`, `Tbk` | `A.Ş.`, `Ltd. Şti.`, `San. ve Tic.` |
| Notice blocks | `IN.corporate_email`, `IN.partnership_form` | `ID.corporate_email`, `ID.partnership_form` | `TR.corporate_email`, `TR.partnership_form` |

- Registrations हर market के लिए `regulatory_registrations` में दर्ज किए जाते हैं और मौजूदा destination-market criterion से score किए जाते हैं: registered, in progress से बेहतर है, और in progress, not published से बेहतर है।
- `Helal`, `Sertifikat Halal` और `हलाल` को `HALAL` token में बदला जाता है, और certifying body तथा उसका scope notes में दर्ज होता है। इंडोनेशिया में मान्यता हर body और हर scope के लिए अलग होती है, इसलिए food के लिए मान्य certificate cosmetics को cover नहीं करता।
- Agent कभी ऐसा required certification नहीं जोड़ता जो RFQ में नहीं बताया गया था। वह इसे एक risk के रूप में उठाता है ताकि कोई व्यक्ति निर्णय ले सके।
- जिन sources को अभी तक किसी live run में नहीं आज़माया गया है, उन्हें [`buyer-discovery.md`](kbeauty-trade-matchmaker/references/buyer-discovery.md) में "not yet field-tested" चिह्नित किया गया है। Compliance rows बताती हैं कि किसी व्यक्ति को क्या जाँचना है; वे कानूनी निष्कर्ष नहीं हैं।

---

## दो runs की तुलना

एक महीने बाद वही search या RFQ फिर से चलाएँ, और `diff_runs.py` बताता है कि क्या बदला। यह दो पूरे हो चुके runs को पढ़ता है और किसी score को नहीं बदलता।

```bash
cd kbeauty-trade-matchmaker
python3 scripts/diff_runs.py --before out/buyers.2026-09-12.json --after out/buyers.2026-10-12.json --pretty --output out/diff.json
```

Diff नई और हट चुकी companies, excluded हुई या वापस आई companies, जिन exclusions के नियम बदले, और हर company के लिए score, rank (match runs), dimensions, qualified flag, confidence तथा Missing पंक्ति में हुए बदलावों की सूची देता है। यह बदले हुए `as_of`, threshold, query या weights को भी चिह्नित करता है।

- Records पहले id से, फिर `merged_from` के ज़रिए जोड़े जाते हैं। जिस company को dedupe ने किसी दूसरी company में merge कर दिया, वह `merged_into` के रूप में दिखती है, खोई हुई lead के रूप में नहीं। इसके अलावा कुछ भी अनुमान से नहीं जोड़ा जाता, इसलिए `merged_from` के बिना हुआ नाम-परिवर्तन gone + new के रूप में दिखता है।
- Match run में "gone" का मतलब है "सूची में नहीं"। कोई seller threshold या `--top` cut के कारण बाहर हो सकता है; diff यह बताता है।
- अलग-अलग `score_version` के अंतर्गत score किए गए दो runs, seller run के सामने buyer run, match run के सामने discovery run, और अलग-अलग RFQs के match runs: इन्हें यह अनुमान से मिलाने के बजाय अस्वीकार कर देता है।
- यह कोई contact channel, evidence या website copy नहीं करता। केवल company name, domain और rule ids आगे जाते हैं।

विवरण: [`data-contract.md`](kbeauty-trade-matchmaker/references/data-contract.md) §9.4।

## Re-check queue

Evidence पुराना होता जाता है। `stale_evidence.py` stored records को पढ़ता है और बताता है कि क्या दोबारा पढ़ना है, सबसे ज़रूरी पहले। यह कुछ भी fetch नहीं करता और किसी record या score को नहीं बदलता।

```bash
cd kbeauty-trade-matchmaker
python3 scripts/stale_evidence.py --input out/buyers.scored.json --as-of 2026-09-19 --top 20 --pretty
```

- `--as-of` अनिवार्य है: उम्र re-check की तारीख़ तक मापी जाती है, और घड़ी से समय कभी नहीं पढ़ा जाता। `--top N` पहले N records की सूची देता है; summary फिर भी सभी को गिनती है।
- कारण, सबसे ज़रूरी पहले: site तक पहुँच नहीं, record stale चिह्नित, evidence stale threshold (730 दिन) से पुराना, source stale चिह्नित, अनसुलझा conflict, पुराना होता हुआ (एक साल से अधिक), बिना तारीख़। जिस अहम दावे का कोई मौजूदा evidence नहीं है, उसे अलग से report किया जाता है।
- उम्र की सीमाएँ `scoring.config.json` से आती हैं, ताकि queue और score "पुराना" के अर्थ पर सहमत रहें। कोई पुराना page queue में नहीं डाला जाता यदि उसी दावे का पहले से कोई मौजूदा source है। बिना तारीख़ वाला page तब तक मौजूदा माना जाता है जब तक उसकी आख़िरी reading मौजूदा है।
- यह एक scored run, एक golden bundle, dedupe output, एक match input, records की एक list या एक record स्वीकार करता है। Match-result अस्वीकार किया जाता है; उसकी जगह match input दें।

Queue पर कैसे काम करें: [`evidence-policy.md`](kbeauty-trade-matchmaker/references/evidence-policy.md) §5.6।

## Spreadsheet, CRM या TradeWith में export

`export_leads.py` किसी एक scored discovery run की leads को एक ऐसी file के रूप में लिखता है जिसे कोई व्यक्ति import करता है। **यह केवल एक file लिखता है।** यह कोई connection नहीं खोलता और कभी post, upload या send नहीं करता।

```bash
cd kbeauty-trade-matchmaker
python3 scripts/export_leads.py --input out/buyers.scored.json --output out/leads.csv                     # Spreadsheet / CRM
python3 scripts/export_leads.py --input out/buyers.scored.json --format tradewith-json --output out/tw.json # TradeWith bulk-import body
python3 scripts/export_leads.py --input out/buyers.scored.json --format tradewith-csv --output out/tw.csv   # TradeWith admin import page
```

| `--format` | किसके लिए | यह क्या है |
|---|---|---|
| `csv` (default) | buyers, sellers | निश्चित columns: scores, dimensions, status, company-level channels, Missing पंक्ति, `score_version` |
| `tradewith-json` | buyers | TradeWith के admin bulk-import endpoint का `{"buyers": [...]}` body |
| `tradewith-csv` | buyers | वे पाँच columns जिन्हें admin buyer-import page पढ़ता है (`sourceId, companyName, country, website, industry`)। इसमें provenance और matching fields छूट जाते हैं; `tradewith-json` को प्राथमिकता दें |

Default रूप से केवल qualified records export होते हैं; इसे बदलने के लिए `--include-unqualified` या `--min-score N` जोड़ें। बंद हो चुकी और पहुँच से बाहर companies तथा `excluded[]` कभी export नहीं होते।

**TradeWith में क्या होता है।** Rows **बिना review के, tier C के रूप में** आती हैं: export कोई quality tier set नहीं करता, और tier C default रूप से buyer matching से बाहर रहता है। कोई admin हर row की review करता है, उसे tier A या B में promote करता है और उसके tags जोड़ता है। तब तक row sellers को offer नहीं की जाती।

- `contactName`, `contactEmail` और `contactPhone` **कभी नहीं भरे जाते**, `sales@` जैसे company role mailbox से भी नहीं। भरा हुआ `contactEmail` row को verified contact चिह्नित कर देगा, और दोबारा import करने पर वह उस address को overwrite कर देगा जिसे किसी admin ने सुधारा था।
- `sourceId` `kbtm:<company domain>` है, इसलिए बाद के run को import करने पर नई row जुड़ने के बजाय वही row update होती है।
- `originalSource` package, `score_version`, `as_of`, record id और record stale है या नहीं, यह दर्ज करता है। `social` केवल एक LinkedIn company page होता है, किसी व्यक्ति का profile कभी नहीं।

**Generic CSV** केवल company-level channels रखता है। कोई email तभी बचता है जब वह company के अपने domain पर role mailbox (`info@`, `sales@` …) हो; LinkedIn member profile रोक लिया जाता है। हर export की गई value personal-data scan से गुज़रती है, और एक भी hit पूरे export को अस्वीकार कर देता है। जिन cells को कोई spreadsheet formula के रूप में चला देगा, उनके आगे एक apostrophe लगाया जाता है।

विवरण: [`data-contract.md`](kbeauty-trade-matchmaker/references/data-contract.md) §9.6।

## Scripts को MCP tools के रूप में उपयोग करें

`scripts/mcp_server.py` stdio पर चलने वाला एक वैकल्पिक, केवल standard library वाला MCP server है। यह उन runtimes के लिए, जो shell के बजाय tools call करते हैं, ग्यारह scripts को tools के रूप में उपलब्ध कराता है: `normalize_company`, `dedupe_companies`, `score_buyer`, `score_seller`, `score_match`, `validate_output`, `make_review_sheet`, `acceptance_report`, `diff_runs`, `stale_evidence` और `export_leads`। हर tool अपनी script को उसके flags के एक subset के साथ चलाता है, और command line जैसा ही नतीजा देता है। Internal-data adapter उपलब्ध नहीं कराया जाता।

- **`--root DIR` अनिवार्य है**: वह एकमात्र folder जिससे tools पढ़ सकते हैं और जिसमें लिख सकते हैं। Server `/`, आपकी home directory या उसके किसी parent को अस्वीकार कर देता है। `../` और symlinks इसके बाहर नहीं ले जा सकते।
- **हर call पर `as_of` अनिवार्य है।** Server कभी कोई तारीख़ ख़ुद नहीं देता।
- **कोई overwrite नहीं।** `output_path` root के भीतर एक नई `.json` या `.csv` file होनी चाहिए, skill package के बाहर और किसी hidden folder में नहीं। पूरा run आम तौर पर 32,768-byte की inline सीमा से बड़ा होता है, इसलिए वास्तविक runs के लिए `output_path` दें।
- कुछ भी send या fetch नहीं होता। Scripts `python3 -I` के साथ और `TRADEWITH_*` variables के बिना चलते हैं।

**Claude Code.** Plugin आपके लिए server शुरू करता है (देखें [Plugin के रूप में install करें](#plugin-के-रूप-में-install-करें))। `install.sh` के बाद इसे हाथ से जोड़ें:

```bash
claude mcp add --transport stdio kbtm -- python3 /abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py --root /abs/path/to/project
```

**Codex CLI.** `~/.codex/config.toml` में; `tool_timeout_sec` को server के `--tool-timeout` (default 120 seconds) से ऊपर रखें:

```toml
[mcp_servers.kbtm]
command = "python3"
args = ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "/abs/path/to/project"]
tool_timeout_sec = 180
```

**Cursor.** `.cursor/mcp.json` (project) या `~/.cursor/mcp.json` (global) में:

```json
{"mcpServers": {"kbtm": {"type": "stdio", "command": "python3",
  "args": ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "${workspaceFolder}"]}}}
```

Client configuration को 2026-09-19 को हर vendor के documentation से जाँचा गया। पूरे नियम, protocol versions और छोड़े गए flags: [`runtime-adapters.md`](kbeauty-trade-matchmaker/references/runtime-adapters.md) §5.6।

---

## Installation

### claude.ai, बिना terminal

1. Latest release से [`kbeauty-trade-matchmaker.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker.zip) download करें। इसे zip ही रहने दें।
2. claude.ai में Settings → Capabilities खोलें और सुनिश्चित करें कि code execution चालू है। फिर Customize → Skills खोलें, Upload skill चुनें, ZIP डालें और save करें।
3. एक नई chat में web search चालू करें और सामान्य भाषा में पूछें, जैसे "Find 5 K-Beauty distributors in the UAE that carry sunscreen." पाँच companies में लगभग 10 से 15 मिनट लगते हैं।

2026-09-14 को एक paid plan पर शुरू से अंत तक verify किया गया: skill का नाम लिए बिना ही उसे invoke किया गया, उसने skill के भीतर web search और page fetches चलाए, और sandbox में scoring scripts execute किए। 2026-09-19 को free plan पर भी एक छोटे request (3 companies) के साथ verify किया गया: scoring scripts समेत skill शुरू से अंत तक चला और usage limit नहीं आया। बड़े request पर free plan की limit आ सकती है। Step-by-step guide (यह guide केवल कोरियाई भाषा में है): [https://kbeauty.tradewith.kr/install-ko](https://kbeauty.tradewith.kr/install-ko)। कहीं अटक गए? [LinkedIn पर मुझे message करें](https://www.linkedin.com/in/hm-choi)।

### Plugin के रूप में install करें

हर runtime के लिए install करने का **एक** ही तरीका चुनें। एक ही runtime में plugin और `install.sh` copy दोनों होने पर दो skills load होते हैं जो एक ही requests पर fire होते हैं।

**Claude Code.** यह repository एक plugin marketplace है जिसमें एक plugin है, ख़ुद package folder:

```
/plugin marketplace add choihyeonmuk/kbeauty-trade-matchmaker
/plugin install kbeauty-trade-matchmaker@kbeauty-trade-matchmaker
```

Skill `/kbeauty-trade-matchmaker:kbeauty-trade-matchmaker` है, या implicitly fire होता है। Plugin bundled MCP server (`kbtm`) को भी आपके project folder को root बनाकर शुरू करता है; इसके लिए `PATH` पर `python3` ज़रूरी है। Claude Code को किसी project folder के भीतर शुरू करें: home directory से launch करने पर server शुरू होने से इनकार कर देता है और failed दिखता है। बाद में `/plugin marketplace update kbeauty-trade-matchmaker` से update करें।

**ChatGPT और Codex.** हर release में एक दूसरा asset होता है, [`kbeauty-trade-matchmaker-plugin.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker-plugin.zip)। यह **केवल skills** है, इसमें कोई MCP server नहीं: OpenAI ऐसे plugin को, जो MCP server declare करता है, desktop-only चिह्नित करता है, और यह ZIP ChatGPT के **web और mobile** तक पहुँचने के लिए ही है।

1. इसे `~/.codex/plugins/kbeauty-trade-matchmaker` में unzip करें।
2. यह entry `~/.agents/plugins/marketplace.json` के `plugins` array में जोड़ें (यदि file मौजूद है तो हाथ से merge करें; path `~` के सापेक्ष है):

```json
{"name": "kbeauty-trade-matchmaker",
 "source": {"source": "local", "path": "./.codex/plugins/kbeauty-trade-matchmaker"},
 "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
 "category": "Business & Operations"}
```

3. ChatGPT desktop app को restart करें और Plugins से इसे install करें, या Codex CLI में `/plugins` चलाएँ।
4. ChatGPT web और mobile के लिए, कोई workspace admin plugin को workspace में publish करता है। Public directory में listing, submission की OpenAI review पर निर्भर करती है।
5. Analyses **Work** mode में चलाएँ। OpenAI यह document नहीं करता कि सामान्य Chat mode bundled scripts चलाता है। जहाँ वे नहीं चल सकते, वहाँ skill यह बता देता है और बिना scores के evidence लौटाता है; यह कभी हाथ से score का अनुमान नहीं लगाता।

Codex IDE extension plugins को support नहीं करता; वहाँ `install.sh --runtime codex` का उपयोग करें। ZIP layout और marketplace entry, 2026-09-19 को जाँचे गए OpenAI documentation का पालन करते हैं; web और mobile पर Work mode में scripts चलते हैं या नहीं, यह अनुमान है, test नहीं किया गया। विवरण: [`runtime-adapters.md`](kbeauty-trade-matchmaker/references/runtime-adapters.md) §5.5।

### Claude Code और Codex installer

`install.sh` POSIX `sh` है, यह न network का उपयोग करता है न sudo का, और target directory के बाहर कुछ नहीं लिखता। **पहले `--dry-run` चलाएँ।**

```bash
git clone https://github.com/choihyeonmuk/kbeauty-trade-matchmaker.git
cd kbeauty-trade-matchmaker

sh kbeauty-trade-matchmaker/install.sh --dry-run          # Print the plan, change nothing
sh kbeauty-trade-matchmaker/install.sh --verify           # Claude Code: symlink into ~/.claude/skills/, then run the tests
sh kbeauty-trade-matchmaker/install.sh --runtime codex    # Codex: symlink into ~/.agents/skills/
```

| Option | अर्थ |
|---|---|
| *(default)* | `$HOME/.claude/skills/kbeauty-trade-matchmaker` पर **Symlink**; source में किए गए बदलाव तुरंत लागू होते हैं |
| `--runtime claude\|codex` | Runtime चुनें। `claude` (default) `.claude/skills/` का उपयोग करता है, `codex` `.agents/skills/` का। `--codex`, `--runtime codex` का संक्षिप्त रूप है |
| `--copy` | Symlink के बजाय एक स्वतंत्र copy install करें |
| `--project DIR` | `$HOME` के बजाय `DIR` के अंतर्गत install करें, ताकि skill उस repository के साथ रहे |
| `--force` | ऐसी मौजूदा directory को overwrite करें जिसे इस script ने नहीं बनाया (default रूप से अस्वीकार) |
| `--verify` | Install के बाद `tests/run_tests.py` चलाएँ; failure पर exit 1 |
| `--dry-run` | केवल plan print करें |

दोबारा चलाना सुरक्षित है। Exit codes: `0` सफलता, `1` installation अस्वीकार या verification fail, `2` usage error।

### Claude Code और claude.ai

```bash
sh kbeauty-trade-matchmaker/install.sh                                  # Personal: every project on this machine
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project  # Project scope
```

- **Folder का नाम `kbeauty-trade-matchmaker` ही होना चाहिए**, जो `SKILL.md` में दिए `name` से मेल खाता है।
- **`SKILL.md` पहले से पूरी तरह conformant है; frontmatter keys जोड़ना सुधार नहीं, बल्कि regression है।** [Agent Skills open standard](https://agentskills.io/specification) ठीक छह fields को मान्यता देता है: `name` और `description` (अनिवार्य), तथा वैकल्पिक `license`, `compatibility`, `metadata` और experimental `allowed-tools`। Claude Code के बाहर (claude.ai, Skills API) केवल यही छह स्वीकार किए जाते हैं, इसलिए Claude Code की एक भी अतिरिक्त key upload को रोक देती है। इस package में केवल दो अनिवार्य keys हैं। Versions `SKILL.md` के body में रहते हैं।
- Surfaces आपस में sync नहीं होते। Claude Code (filesystem), claude.ai (Customize → Skills में zip upload) और Skills API (`/v1/skills`), हर एक में folder को अलग से upload करना पड़ता है।

### OpenAI Codex और ChatGPT

Folder बिना बदलाव के काम करता है, पर Codex `.claude/skills` नहीं पढ़ता। इसके skill roots `.agents/skills` हैं।

```bash
sh kbeauty-trade-matchmaker/install.sh --runtime codex                                # User scope: ~/.agents/skills/
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo   # Repository scope
```

- Codex working directory से लेकर repository root तक हर directory में `.agents/skills` को scan करता है। `~/.codex/skills` deprecated है पर अभी भी supported है; नए installs के लिए `~/.agents/skills` का उपयोग करें।
- CLI और IDE extension में `$kbeauty-trade-matchmaker` या `/skills` से, या ChatGPT में `@` से स्पष्ट रूप से invoke करें। Implicit invocation का निर्णय `description` से होता है।
- एक standalone skill folder केवल **ChatGPT desktop app, Codex CLI और IDE extension** में दिखाई देता है। ChatGPT **web और mobile** के लिए plugin ZIP ज़रूरी है; देखें [Plugin के रूप में install करें](#plugin-के-रूप-में-install-करें)।
- `AGENTS.md` skills install करने का तरीका नहीं है। यह हमेशा लागू रहने वाले repository instructions के लिए Codex का एक अलग feature है।

> **2026-09-13 को आधिकारिक documentation से जाँचा गया।** यदि यहाँ लिखी कोई बात मौजूदा docs से मेल नहीं खाती, तो docs सही हैं।
> [Agent Skills specification](https://agentskills.io/specification) · [Anthropic Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [Claude Code skills](https://code.claude.com/docs/en/skills) · [OpenAI build skills](https://learn.chatgpt.com/docs/build-skills)
>
> ChatGPT workspace में upload करने की प्रक्रिया, और desktop के अलावा ChatGPT के अन्य surfaces bundled `python3` scripts execute कर सकते हैं या नहीं, इसे verify **नहीं** किया जा सका; संबंधित OpenAI help pages ने automated requests पर HTTP 403 लौटाया। यदि scripts नहीं चल सकते, तो skill बिना scores के केवल evidence देने पर लौट आता है (देखें `references/runtime-adapters.md` §4)।

### Installation की जाँच करें

दो चीज़ें एक-दूसरे से स्वतंत्र रूप से टूट सकती हैं। ये तीन lines **केवल code** की जाँच करती हैं:

```bash
python3 kbeauty-trade-matchmaker/scripts/validate_output.py --version
python3 kbeauty-trade-matchmaker/adapters/tradewith_adapter.py --version
python3 kbeauty-trade-matchmaker/tests/run_tests.py
```

यह जाँचने के लिए कि **runtime को skill दिख रहा है**, Claude Code की skill list में, या Codex की `/skills` list में `kbeauty-trade-matchmaker` देखें। यदि यह नहीं दिखता, तो जाँचें कि folder सीधे skills root के नीचे है, उसका नाम `kbeauty-trade-matchmaker` है, `SKILL.md` की पहली line ठीक `---` है, और किसी उच्च-प्राथमिकता वाले scope में इसी नाम का कोई skill नहीं है। बदलाव के बाद Codex को restart करें।

---

## Tests चलाना

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py       # Exit 0 when everything passes, 1 otherwise
python3 tests/run_tests.py -v    # One line per case, not just failures
```

Runner केवल standard library का उपयोग करता है, fixtures को अपनी location के सापेक्ष ढूँढ़ता है, हर script को `--as-of 2026-09-12` देता है, और output की तुलना expected fixtures से **byte for byte** करता है। Fixture cases से पहले यह schemas की ख़ुद जाँच करता है: कि वे parse होते हैं, हर `$ref` resolve होता है, कोई unsupported keyword उपयोग नहीं हुआ है, shared `$defs` सभी files में एक जैसे हैं, और embedded versions `scoring.config.json` से मेल खाते हैं।

`plugins` phase repository-स्तर के manifests और builder को पढ़ता है, इसलिए 536 की पूरी गिनती repository checkout पर लागू होती है; installed copy दो SKIPs report करती है (`plugins` phase और एक MCP case)। यह phase release builder को एक अस्थायी git repository में चलाता है, इसलिए uncommitted काम नतीजे को प्रभावित नहीं करता।

Scripts को सीधे भी चलाया जा सकता है। JSON `stdout` पर जाता है और हर diagnostic `stderr` पर, इसलिए pipes सुरक्षित हैं।

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py --input tests/fixtures/match-134.input.json --as-of 2026-09-12 --pretty
```

---

## Adapter quickstart: केवल files, कोई backend नहीं

पूरा workflow आज **बिना किसी internal API के** चलता है। Adapter का एक interface और दो backends हैं, `file` और `http`, और `file` default है। इसे किसी credentials, network या service की ज़रूरत नहीं है।

```bash
cd kbeauty-trade-matchmaker
export TRADEWITH_DATA_DIR=./tradewith-data     # Created on first write

python3 adapters/tradewith_adapter.py get-rfq --id 134 --pretty
python3 adapters/tradewith_adapter.py list-sellers --country KR --limit 20
python3 adapters/tradewith_adapter.py save-leads --input out/buyers.scored.json
python3 adapters/tradewith_adapter.py save-matches --input out/match-134.json
python3 adapters/tradewith_adapter.py save-outreach-drafts --input out/drafts.json   # Queues for review; does not send
python3 adapters/tradewith_adapter.py update-lead-status --id BUY-gulfbeauty-example-com --status VERIFIED
```

Data directory एक साधारण JSON file tree है जिसमें **file का नाम ही id है**, इसलिए एक ही input के साथ दोबारा चलाने पर वही file overwrite होती है और नतीजे byte-stable रहते हैं। इसे package के बाहर रखें, और यदि इसमें वास्तविक company data है तो version control के भी बाहर; `.gitignore` पहले से `tradewith-data/` को exclude करता है।

जब कोई API उपलब्ध हो, तो केवल एक flag बदलता है। Scores, schemas और output नहीं बदलते।

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # Environment only; a command-line token lands in shell history
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

Environment variables import के समय नहीं, call के समय पढ़े जाते हैं। जब तक `--backend http` निर्दिष्ट न किया जाए, कुछ भी network को नहीं छूता, API से files पर कोई चुपचाप fallback नहीं होता, और token कभी errors, warnings या stored documents में नहीं दिखता। विवरण `adapters/tradewith_adapter.md` में है।

---

## खुले निर्णय

छह प्रश्न अभी खुले हैं। इस implementation में हर एक का एक default है, और हर default को एक ही जगह बदला जा सकता है।

| # | प्रश्न | यहाँ default | कहाँ बदलें |
|---|---|---|---|
| 1 | क्या कोई internal seller/RFQ API मौजूद है, या files से शुरुआत करें? | **दोनों।** Default रूप से file backend; API होने पर `--backend http` | `TRADEWITH_BACKEND` / `--backend` |
| 2 | Leads को सीधे service database में save करें, या staging में? | **Staging.** Skill केवल `POST /research/leads` (या `<data-dir>/leads/`) में लिखता है; promotion application-layer पर मिलने वाली मंज़ूरी है | Adapter का write target |
| 3 | 70 का निश्चित threshold, या हर category के लिए percentile? | **निश्चित 70.** Percentile mode implement किया गया है; उपयोग किया गया mode हमेशा `summary` में दर्ज होता है | `scoring.config.json` में `thresholds`, `--threshold`, `--threshold-mode` |
| 4 | Role addresses (`info@`, `sales@`) और नामित contacts को किस हद तक store करें? | **केवल company-level channels.** Code में personal contact का अनुमान लगाने या उसे इकट्ठा करने की कोई व्यवस्था नहीं है | `evidence-policy.md`, `compliance-notes.md` |
| 5 | Directories के लिए crawling का दायरा और terms-of-service की जाँच? | **Robots और ToS का सम्मान करें, कुछ भी bypass न करें।** Blocked access `"unknown"` रहता है | `compliance-notes.md`, discovery source lists |
| 6 | जब कोई RFQ न हो, तो seller outreach में क्या कहा जा सकता है? | **एक अलग no-RFQ template.** ऐसी demand का संकेत देना जो मौजूद नहीं है, प्रतिबंधित है | `templates/seller_outreach.md`, `outreach-guidelines.md` |

---

## Versions

| Version | Value | क्या दर्शाता है | कहाँ रहता है |
|---|---|---|---|
| `skill_version` | `0.3.0` | Package: prompts, references, scripts, templates, tests | `SKILL.md` body, `match-result.skill_version`, दोनों plugin manifests |
| `schema_version` | `0.1.0` | Shape contract: field names, enums, required lists | हर document, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | Rubric: weights, criteria, signals, penalties, thresholds, hard filters | `scoring.config.json`, हर scored document |

```bash
python3 scripts/validate_output.py --version
```

जब rubric बदलता है, तो stored scores परिभाषा के अनुसार पुराने हो जाते हैं। चूँकि raw records अपना evidence, query surface और `as_of` सुरक्षित रखते हैं, इसलिए scores को दोबारा crawl किए बिना फिर से गणना की जा सकती है। अलग-अलग `score_version` के नतीजों की एक ही list में तुलना करना या उन्हें rank करना वर्जित है, और `validate_output.py` इसे पकड़ लेता है।

---

## आवश्यकताएँ

- `python3` 3.9–3.14, केवल standard library।
- Installer के लिए एक POSIX shell।
- Discovery modes के लिए runtime की web search और page fetching ज़रूरी है। Scripts पूरी तरह offline हैं, इसलिए tests और re-scoring बिना network के चलते हैं।
- Internal-data integration वैकल्पिक है; adapter के `file` backend को किसी चीज़ की ज़रूरत नहीं है।

---

## Data handling

इस repository में कोई वास्तविक company data या व्यक्तिगत जानकारी नहीं है। Fixtures `.example` domains पर काल्पनिक companies का उपयोग करते हैं। जब आप operational data जोड़ना शुरू करें, तो `tradewith-data/`, `.env` और `out/` को ignored रखें, जैसा `.gitignore` पहले से करता है, और समय-समय पर जाँचें कि आप जो store करते हैं वह दावा, URL, देखे जाने का समय और छोटे quote तक ही सीमित रहे।

**यह कानूनी सलाह नहीं है।** हर jurisdiction में direct-marketing नियमों की जाँच करना किसी व्यक्ति का काम है; यह package बताता है कि वह जाँच कहाँ ज़रूरी है।
