# K-Beauty Trade Matchmaker

**हे एक Agent Skill आहे, जे सार्वजनिक वेबवर परदेशातील K-Beauty खरेदीदार (buyers) आणि कोरियन विक्रेते (sellers) शोधते, प्रत्येक महत्त्वाचा दावा तुम्ही क्लिक करून पाहू शकाल अशा स्रोताशी पडताळून पाहते, दोन्ही बाजूंना deterministic (प्रत्येक वेळी तोच निकाल देणाऱ्या) scripts द्वारे गुण देते, खरेदी मागण्या विक्रेत्यांशी जुळवते, आणि माणसाने तपासावा असा outreach मसुदा (draft) तयार करून तिथेच थांबते.**

हेच फोल्डर कोणताही बदल न करता **Claude Code** आणि **OpenAI Codex** मध्ये चालते. Python फक्त **standard library** वापरते (3.9–3.14); `pip install` करण्यासारखे काहीही नाही.

[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [प्रकल्प पृष्ठ](https://kbeauty.tradewith.kr/) · [LinkedIn](https://www.linkedin.com/in/hm-choi)

![योग्य K-Beauty व्यापार भागीदार शोधा](kbeauty-trade-partner-linkedin-cities.png)

> कार्यप्रवाहाचे दृश्य स्वरूप: शोधा, पडताळा, जुळवा आणि अंतिम निर्णय माणसाकडेच ठेवा.

> **स्थिती: v0.2.0.** हा pipeline काल्पनिक fixtures वरील 252 cases वर तपासला गेला आहे आणि live वेबवर एकदा चाचणी घेतली गेली आहे. गुणांकन पद्धत (scoring rubric) **प्रत्यक्ष व्यावसायिक परिणामांशी अद्याप पडताळलेली नाही**: गुण पुन्हा तसेच मिळवता येतात आणि त्यांचा मागोवा घेता येतो, पण ते भविष्याचा अचूक अंदाज देतात हे अजून सिद्ध झालेले नाही. v0.2.0 मध्ये ते मोजण्यासाठीची साधने जोडली आहेत ([गुणांची पडताळणी](#गुणांची-पडताळणी)), पण label केलेला नमुना (labelled sample) अजून अस्तित्वात नाही. RFQ Matching आणि Outreach Draft हे live डेटावर अजून चालवलेले नाहीत. कोणत्याही गुणांवर विश्वास ठेवण्यापूर्वी [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) वाचा.

---

## नवीन काय आहे

**v0.2.0 (2026-09-19).** विद्यमान कोणतेही गुण बदलत नाहीत.

- **गुण-पडताळणीची साधने.** Trade operator (व्यापाराचे प्रत्यक्ष कामकाज पाहणारी व्यक्ती) साठी एक blind review sheet, आणि त्यांच्या accept/reject (स्वीकार/नकार) निर्णयांची गुणांशी तुलना करणारा एक report. [गुणांची पडताळणी](#गुणांची-पडताळणी) पहा.
- **भारत, इंडोनेशिया आणि तुर्किये (Türkiye) साठी market packs.** स्थानिक भाषेत खरेदीदार शोध, matching मध्ये वापरले जाणारे बाजारपेठ-प्रवेशाचे नियम (CDSCO, BPOM, इंडोनेशियाचे अनिवार्य halal प्रमाणन, TİTCK), direct-marketing checklist च्या ओळी आणि notice blocks, आणि `Pvt Ltd`, `PT`, `A.Ş.` व तत्सम स्वरूपांसाठी कंपनीच्या नावांची हाताळणी. [Market packs](#market-packs-बाजारपेठनिहाय-संच) पहा.
- Tests: 179 → 252 cases.

प्रत्येक release च्या संपूर्ण नोंदी: [CHANGELOG.md](CHANGELOG.md) (फक्त इंग्रजीत उपलब्ध).

---

## सुरक्षिततेची मर्यादा: हे skill काहीही पाठवत नाही

या package बद्दलची सर्वात महत्त्वाची गोष्ट: **यातील कोणताही code संदेश पाठवू शकत नाही.**

- **फक्त मसुदा.** Outreach चे काम नेहमी `READY_FOR_REVIEW` या स्थितीत संपते, आणि मसुद्यावर `auto_send: false` व `manual_approval_required: true` असते.
- **मंजुरी माणूस देतो, पाठवण्याचे काम बाह्य प्रणाली करते.** हे skill एखाद्या lead ला `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` इथपर्यंतच पुढे नेऊ शकते, त्यापुढे नाही. `APPROVED_FOR_OUTREACH` आणि त्यापुढील टप्पे CRM आणि एखाद्या व्यक्तीच्या अखत्यारीत आहेत; adapter ते बदल **नाकारतो**.
- **SMTP, Gmail, SES किंवा webhook द्वारे पाठवणारा कोणताही code नाही.** वैयक्तिक ईमेल किंवा फोन नंबरचा अंदाज बांधणे किंवा ते मोठ्या प्रमाणात गोळा करणे नाही (फक्त कंपनी-स्तरीय संपर्क माध्यमे). CAPTCHA, login, paywall, robots.txt किंवा सेवा-अटी (terms of service) टाळून जाणे नाही.
- **काहीही मनाने रचले जात नाही.** MOQ, प्रमाणपत्रे (certifications), निर्यात बाजारपेठा, exclusivity आणि उत्पादन क्षमता फक्त एखाद्या स्रोतात नमूद असतील तरच नोंदवल्या जातात; अन्यथा त्या `"unknown"` राहतात. "एक खरेदीदार तुमची वाट पाहत आहे" अशी आधार नसलेली घाई template स्तरावरच प्रतिबंधित आहे.
- **कोणताही कायदेशीर निर्णय नाही.** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) कुठे तपासणी आवश्यक आहे ते दर्शवते; पाठवणे परवानगीयोग्य आहे असा निष्कर्ष ते कधीही काढत नाही.

हा डिझाइनचा जाणीवपूर्वक घेतलेला निर्णय आहे, राहून गेलेले वैशिष्ट्य नव्हे. *मसुदा तयार करणे* आणि *पाठवणे* या वेगवेगळ्या परवानग्या आहेत, आणि या package कडे फक्त पहिली परवानगी आहे.

---

## हे काय करते

Google, LinkedIn आणि trade-show directories मध्ये दिवसभर करावा लागणारा शोध हे **पडताळता येणाऱ्या shortlist मध्ये बदलते, जिथे प्रत्येक दावा त्याच्या स्रोताशी जोडलेला असतो**.

1. **शोधणे (Discover):** देश, उत्पादन श्रेणी, OEM/ODM, MOQ आणि प्रमाणपत्रांनुसार सार्वजनिक वेबवर खरेदीदार आणि विक्रेते उमेदवार शोधते.
2. **पडताळणे (Verify):** प्रत्येक दावा विश्वासार्ह स्रोतांशी, सहसा कंपनीच्या स्वतःच्या वेबसाइटशी, पडताळते आणि त्याचा URL व तो पाहिल्याची वेळ यांसह साठवते.
3. **प्रमाणित करणे (Normalize):** domains आणि कंपनींची नावे प्रमाणित करते, duplicate नोंदी एकत्र करते, आणि खरेदीदारांच्या गरजा व विक्रेत्यांच्या क्षमता एकाच schema वर आणते.
4. **गुण देणे (Score):** deterministic Python scripts द्वारे खरेदीदार आणि विक्रेत्यांना गुण देते. समान input आणि समान `--as-of` दिल्यास समान output मिळतो.
5. **जुळवणे (Match):** खरेदी मागणी (RFQ – Request for Quotation) विक्रेत्यांशी जुळवते: आधी hard filters, मग weighted score, मग मर्यादित semantic rerank, आणि शेवटी unknown मूल्यांची हाताळणी. हे top N निकाल देते **आणि नाकारलेला प्रत्येक विक्रेता का नाकारला गेला याचे कारणही देते**.
6. **मसुदा तयार करणे (Draft):** फक्त पडताळलेल्या तथ्यांवर आधारित वैयक्तिकृत outreach मसुदा लिहिते, आणि तिथेच थांबते.

---

## चार modes

Agent ला साध्या भाषेत विचारा. खालील स्वरूपे म्हणजे प्रत्येक mode कोणते parameters घेतो याचे संक्षिप्त रूप आहे.

### Buyer Discovery

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

UAE मध्ये K-Beauty उत्पादने विकणारे वितरक (distributors), आयातदार (importers) आणि घाऊक विक्रेते (wholesalers) शोधते, आणि कंपनीचा प्रकार, विकले जाणारे brands, घाऊक उपलब्धता, भागीदारीचे संकेत, अधिकृत संपर्क माध्यमे आणि पुराव्याचे URLs प्रमाणित स्वरूपात मांडते.

### Seller Discovery

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

कोरियन उत्पादक (manufacturers) आणि brands शोधते, आणि OEM/ODM क्षमता, MOQ, प्रमाणपत्रे, मुख्य उत्पादने आणि निर्यात किंवा भागीदारीचे संकेत पडताळते.

### RFQ Matching

```
/kbeauty-match rfq="#134" top=10
```

RFQ मधील उत्पादन, MOQ, गंतव्य देश, प्रमाणपत्रे आणि private-label गरज यांच्या आधारे विक्रेते गाळते, संख्यात्मक आणि गुणात्मक गुण एकत्र करते, आणि top 10 सोबत वगळण्याची कारणे देते. "कोणताही पात्र जुळणारा विक्रेता नाही" हा निकाल, त्याच्या कारणांसह, हा देखील एक सामान्य output आहे.

### Outreach Draft

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

फक्त पडताळलेल्या तथ्यांवरून विषय (subject), मुख्य मजकूर (body) आणि वैयक्तिकरणाचे मुद्दे लिहिते. कमकुवत पुरावा असल्यास तो सर्वसाधारण स्वरूपात मांडला जातो किंवा "पडताळणी आवश्यक" म्हणून चिन्हांकित केला जातो. **हे पाठवत नाही.**

---

## Output चे उदाहरण

सोबत दिलेल्या test fixture वर RFQ Matching चालवल्याचा एक अंश. हे विक्रेते काल्पनिक आहेत.

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

विक्रेता क्रमांक 9 कडे लक्ष द्या: त्याचा MOQ प्रकाशित केलेला नाही, म्हणून MOQ hard filter **वगळला गेला (skipped), तो अयशस्वी (failed) झाला नाही**. हा विक्रेता कमी operational गुणांसह आणि स्पष्ट "Missing" ओळीसह यादीत राहतो; त्याला गुपचूप नाकारले जात नाही किंवा गुपचूप पास केले जात नाही.

---

## Package ची रचना

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
│   └── acceptance_report.py      # Review sheets + scored runs -> Human Acceptance Rate report (v0.2.0)
├── templates/
│   ├── buyer_outreach.md
│   ├── seller_outreach.md        # With and without an RFQ
│   └── legal_notices.md          # Per-jurisdiction, per-channel notice blocks
└── tests/
    ├── cases.md                  # Test case specification, including negative and edge cases
    ├── run_tests.py
    └── fixtures/                 # Golden inputs and expected outputs; all companies fictional
```

फक्त `kbeauty-trade-matchmaker/` फोल्डर install केले जाते. Runtime ला आवश्यक असलेला contract मजकूर `references/data-contract.md` आणि `references/output-format.md` मध्ये समाविष्ट केलेला आहे.

---

## गुणांकन (scoring) कसे चालते

गुण **deterministic scripts** मधून येतात, एखाद्या model च्या अंदाजावरून नाही.

खरेदीदारांना सहा निकषांवर (dimensions) गुण दिले जातात: `kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10. विक्रेत्यांनाही सहा निकषांवर गुण दिले जातात: `product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10. प्रत्येक निकष त्यातील उप-निकषांचे (criterion-level) गुण एकत्र करतो आणि लागू होणाऱ्या गुणांच्या आधारे 0–100 या प्रमाणात रूपांतरित करतो.

Matching चे चार टप्पे क्रमाने चालतात:

1. **Hard filters** `HF-01..HF-08`, तसेच rendering gate `HF-00`. एखादा filter अयशस्वी झाला तरी प्रक्रिया मध्येच थांबत नाही; वगळण्याचे **प्रत्येक** कारण गोळा केले जाते.
2. **Weighted score:** `product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10. हे विक्रेत्याचेच सहा निकष आहेत, जे RFQ च्या संदर्भात पुन्हा गुणांकित केले जातात, त्यामुळे एखाद्या विक्रेत्याचे मूल्यमापन कधीही दोन वेगवेगळ्या rubrics ने होत नाही.
3. **मर्यादित semantic rerank.** हा एकमेव टप्पा आहे जो model ठरवतो. त्यावर मर्यादा (cap) आहे, त्यासाठी पुराव्याचा संदर्भ देणारी कारणे आवश्यक असतात, आणि hard filter मध्ये अयशस्वी झालेल्या विक्रेत्याला तो कधीही परत आणू शकत नाही.
4. **Unknown मूल्यांची हाताळणी.**

तीन नियम सर्वात महत्त्वाचे आहेत:

- **Unknown म्हणजे शून्य नव्हे.** Unknown मूल्याला त्या उप-निकषाच्या कमाल गुणांपैकी 30% गुण मिळतात (`neutral_base` 50 × `penalty_factor` 0.6). केवळ एखादे मूल्य unknown आहे म्हणून उमेदवाराला नाकारणे निषिद्ध आहे, आणि दंड टाळण्यासाठी मूल्याचा अंदाज बांधणेही निषिद्ध आहे.
- **घड्याळ वाचले जात नाही.** वेळेची सर्व गणिते `--as-of` वापरतात, त्यामुळे तुम्ही कधीही चालवले तरी समान input साठी समान आकडे मिळतात.
- **Confidence म्हणजे गुणवत्ता नव्हे.** Confidence हे "ही नोंद किती विश्वासार्ह आहे" या प्रश्नाचे उत्तर देते, ते `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW` असे दाखवले जाते, आणि ते पात्रता गुण (qualification score) कधीही वाचत नाही.

ज्या खरेदीदाराकडे K-Beauty चा कोणताही पुरावा नाही, त्याला तरीही गुण दिले जातात आणि क्रमवारीत स्थान मिळते, पण त्याचे इतर निकष कितीही मजबूत असले तरी त्याला कधीही पात्र (qualified) म्हणून चिन्हांकित केले जात नाही. डिफॉल्ट पात्रता मर्यादा (threshold) निश्चित 70 आहे; percentile mode देखील उपलब्ध आहे. बदलता येणारा प्रत्येक आकडा `schemas/scoring.config.json` मध्ये आहे.

## पुरावा (evidence) कसा चालतो

Output चे एकक "एक कंपनी" नसून **पुराव्यासह एक दावा** आहे. पुराव्याच्या एका नोंदीत एक `claim`, त्याचा स्रोत URL, स्रोताचा स्तर (tier), मजकुराची तारीख (`source_date`), तो कधी पाहिला गेला (`observed_at`), ते तथ्य आहे की अनुमान (inference), आणि एक छोटे अवतरण असते.

| स्तर | स्रोत | गुण |
|---|---|---|
| 1 | कंपनीची अधिकृत वेबसाइट | 100 |
| 2 | अधिकृत trade-show, संघटना, सरकारी किंवा व्यापार संस्थांच्या directories; अंतर्गत नोंदी | 82 |
| 3 | अधिकृत LinkedIn आणि social media profiles | 64 |
| 4 | प्रतिष्ठित third-party directories आणि press releases | 46 |
| 5 | Community posts आणि blogs (फक्त पूरक संकेत म्हणून) | 20 |

गुणांना मजकुराच्या वयाने गुणले जाते: 90 दिवसांच्या आत 1.00, एका वर्षाच्या आत 0.92, दोन वर्षांच्या आत 0.80, त्याहून जुने 0.60, तारीख नसलेले 0.85. वय नेहमी `source_date` पासून मोजले जाते, `observed_at` पासून कधीही नाही, कारण आपण एखादे पृष्ठ कधी वाचले यावरून त्यातील मजकूर किती जुना आहे हे समजत नाही.

दोन स्थिती जाणीवपूर्वक वेगळ्या ठेवल्या आहेत:

- **`unverified`**: नोंदीकडे पुरावा आहे, पण कोणत्याही महत्त्वाच्या दाव्याला अधिकृत (tier 1) स्रोत नाही. अशा नोंदीला गुण दिले जातात, क्रमवारीत स्थान मिळते आणि ती `— unverified` या चिन्हासह दाखवली जाते. ती **वगळली जात नाही**.
- कोणत्याही महत्त्वाच्या दाव्यासाठी **अजिबात पुरावा नसणे**: अशी नोंद गुणांकनापूर्वीच `excluded[]` मध्ये हलवली जाते आणि कारण नमूद केले जाते ("site उघडता आली नाही" किंवा "वाचली, पण कोणतेही महत्त्वाचे दावे नाहीत"). पुरावा नसलेल्या कंपन्या भरून क्रमवारीची यादी फुगवणे हीच चूक यामुळे टाळली जाते.

साठवण किमान ठेवली जाते: दावा, URL, पाहिल्याची वेळ आणि एक छोटे अवतरण; संपूर्ण पृष्ठे किंवा अनावश्यक वैयक्तिक profiles कधीही नाहीत. परस्परविरोधी स्रोत `conflicts[]` मध्ये माणसाने पाहण्यासाठी नोंदवले जातात, ते कधीही गुपचूप सोडवले जात नाहीत, आणि अशा विरोधांमुळे confidence कमी होतो.

---

## गुणांची पडताळणी

येथील गुण पुन्हा तसेच मिळवता येतात, पण ते एखाद्या व्यक्तीच्या निर्णयाशी अजून कोणीही ताडून पाहिलेले नाहीत. v0.2.0 मध्ये जोडलेल्या दोन स्वतंत्र scripts द्वारे तुम्ही ती तपासणी करू शकता. त्या कोणतेही गुण बदलत नाहीत.

```bash
cd kbeauty-trade-matchmaker

# 1. Make a blind sheet from a scored run. No score, rank or qualified flag; rows in a fixed shuffled order.
python3 scripts/make_review_sheet.py --input out/buyers.scored.json --include-excluded --output out/review.csv

# 2. A trade operator fills in verdict (accept / reject / unsure), a reason_code for each reject,
#    their role, and the date. Then:
python3 scripts/acceptance_report.py --scored out/buyers.scored.json --reviews out/review.csv --as-of 2026-09-19 --pretty
```

Report मध्ये, review केलेल्या कंपन्यांपैकी operator ने किती प्रमाणात स्वीकारल्या हे दिले जाते: एकूण, आणि `qualified` flag, score band, देश व reject चे कारण यांनुसार विभागून. 50 ते 90 पर्यंतच्या प्रत्येक threshold ने किती precision आणि recall दिले असते हे तो दाखवतो. एकूण गुण आणि सहा निकषांपैकी प्रत्येकासाठी, ते गुण स्वीकारलेल्या आणि नाकारलेल्या कंपन्यांना किती चांगल्या प्रकारे वेगळे करतात आणि त्या निकषाने किती वेगवेगळी मूल्ये दिली हे तो दाखवतो. Operator ने स्वीकारल्या असत्या अशा वगळलेल्या (excluded) कंपन्यांची यादीही तो देतो.

निर्णय दिलेले (accept किंवा reject) reviews 30 पेक्षा कमी असल्यास report स्वतःला `insufficient_sample` म्हणून चिन्हांकित करतो आणि weight किंवा threshold मधील बदलाचे समर्थन तो करू शकत नाही असे सांगतो. तो पुढील गोष्टी असलेल्या sheets नाकारतो: अज्ञात verdict, कारण नसलेले reject, परस्परविरोधी duplicates, वेगवेगळ्या `score_version` खाली गुणांकित झालेले runs, आणि ईमेल पत्ता किंवा फोन नंबर असलेल्या notes. Reviewers ची ओळख त्यांच्या भूमिकेने (role) होते, नावाने कधीही नाही.

[`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) §7 मध्ये नमुना कसा तयार करायचा (किमान दोन देश आणि दोन श्रेणी, review केलेल्या 100 ते 200 कंपन्या) आणि कोणता निकाल calibration चा प्रत्येक खुला प्रश्न निकाली काढेल हे सांगितले आहे. हे फक्त Human Acceptance Rate (माणसाने स्वीकारण्याचे प्रमाण) मोजते. संपर्क केलेला lead पुढे RFQ बनतो की नाही हे समजण्यासाठी application स्तरावरील outcome डेटा लागतो.

## Market packs (बाजारपेठनिहाय संच)

v0.2.0 मध्ये भारत, इंडोनेशिया आणि तुर्किये जोडले आहेत. गुणांकनाचा कोणताही नियम बदललेला नाही; एक नवीन test fixture (गंतव्य इंडोनेशिया, halal आवश्यक) दाखवतो की विद्यमान rubric त्यांना आधीच हाताळते.

| | भारत | इंडोनेशिया | तुर्किये |
|---|---|---|---|
| खरेदीदार शोधाची भाषा | आधी इंग्रजी, सोबत हिंदी | इंडोनेशियन | तुर्की |
| Matching मध्ये वापरला जाणारा बाजारपेठ-प्रवेशाचा नियम | CDSCO आयात नोंदणी (import registration) | BPOM notification; सौंदर्यप्रसाधनांसाठी अनिवार्य halal प्रमाणन (`--as-of` च्या तारखेवर अवलंबून) | ÜTS मार्फत TİTCK notification |
| कंपनीच्या नावांतून काढली जाणारी कायदेशीर स्वरूपे (legal forms) | `Pvt Ltd`, `Private Limited`, `LLP` | `PT`, `CV`, `Tbk` | `A.Ş.`, `Ltd. Şti.`, `San. ve Tic.` |
| Notice blocks | `IN.corporate_email`, `IN.partnership_form` | `ID.corporate_email`, `ID.partnership_form` | `TR.corporate_email`, `TR.partnership_form` |

- नोंदण्या (registrations) प्रत्येक बाजारपेठेनुसार `regulatory_registrations` मध्ये नोंदवल्या जातात आणि विद्यमान गंतव्य-बाजारपेठ उप-निकषाद्वारे गुणांकित होतात: नोंदणी झालेली (registered) ही प्रक्रियेत असलेल्यापेक्षा (in progress) वरचढ ठरते, आणि ती प्रकाशित न केलेल्यापेक्षा (not published) वरचढ ठरते.
- `Helal`, `Sertifikat Halal` आणि `हलाल` हे `HALAL` token बनतात, आणि प्रमाणित करणारी संस्था व तिची व्याप्ती (scope) notes मध्ये नोंदवली जाते. इंडोनेशियात मान्यता प्रत्येक संस्थेनुसार आणि प्रत्येक व्याप्तीनुसार दिली जाते, त्यामुळे अन्नपदार्थांसाठी मान्य असलेले प्रमाणपत्र सौंदर्यप्रसाधनांना लागू होत नाही.
- RFQ मध्ये नमूद नसलेले आवश्यक प्रमाणपत्र agent कधीही स्वतःहून जोडत नाही. तो ते एक जोखीम (risk) म्हणून मांडतो, ज्यावर निर्णय माणूस घेतो.
- Live run मध्ये अजून न वापरलेले स्रोत [`buyer-discovery.md`](kbeauty-trade-matchmaker/references/buyer-discovery.md) मध्ये "not yet field-tested" (अद्याप प्रत्यक्ष वापरात न तपासलेले) असे चिन्हांकित केले आहेत. Compliance च्या ओळी माणसाने काय तपासायचे ते सांगतात; ते कायदेशीर निष्कर्ष नाहीत.

---

## Installation (स्थापना)

### claude.ai, terminal शिवाय

1. नवीनतम release मधून [`kbeauty-trade-matchmaker.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker.zip) डाउनलोड करा. ते zip स्वरूपातच ठेवा.
2. claude.ai मध्ये Settings → Capabilities उघडा आणि code execution चालू असल्याची खात्री करा. नंतर Customize → Skills उघडा, Upload skill निवडा, ZIP टाका आणि save करा.
3. नवीन chat मध्ये web search चालू करा आणि साध्या भाषेत विचारा, उदाहरणार्थ "Find 5 K-Beauty distributors in the UAE that carry sunscreen." पाच कंपन्यांसाठी साधारण 10 ते 15 मिनिटे लागतात.

2026-09-14 रोजी सशुल्क (paid) plan वर संपूर्ण प्रक्रिया सुरुवातीपासून शेवटपर्यंत पडताळली गेली: skill चे नाव न घेता ते आपोआप सुरू झाले, skill मध्येच web search आणि page fetch चालले, आणि sandbox मध्ये scoring scripts चालल्या. मोफत (free) plan वर संपूर्ण run साठी पुरेसा वापर मिळतो की नाही हे तपासलेले नाही. कोरियन भाषेतील टप्प्याटप्प्याने मार्गदर्शक: [https://kbeauty.tradewith.kr/install-ko](https://kbeauty.tradewith.kr/install-ko) (टीप: हे मार्गदर्शक फक्त कोरियन भाषेत उपलब्ध आहे). अडचण येत आहे? [मला LinkedIn वर संदेश पाठवा](https://www.linkedin.com/in/hm-choi).

### Claude Code आणि Codex installer

`install.sh` हे POSIX `sh` आहे, ते network किंवा sudo वापरत नाही, आणि target directory च्या बाहेर काहीही लिहीत नाही. **आधी `--dry-run` चालवा.**

```bash
git clone https://github.com/choihyeonmuk/kbeauty-trade-matchmaker.git
cd kbeauty-trade-matchmaker

sh kbeauty-trade-matchmaker/install.sh --dry-run          # Print the plan, change nothing
sh kbeauty-trade-matchmaker/install.sh --verify           # Claude Code: symlink into ~/.claude/skills/, then run the tests
sh kbeauty-trade-matchmaker/install.sh --runtime codex    # Codex: symlink into ~/.agents/skills/
```

| पर्याय | अर्थ |
|---|---|
| *(डिफॉल्ट)* | `$HOME/.claude/skills/kbeauty-trade-matchmaker` येथे **Symlink**; source मधील बदल लगेच लागू होतात |
| `--runtime claude\|codex` | Runtime निवडा. `claude` (डिफॉल्ट) `.claude/skills/` वापरतो, `codex` `.agents/skills/` वापरतो. `--codex` हे `--runtime codex` चे संक्षिप्त रूप आहे |
| `--copy` | Symlink ऐवजी स्वतंत्र प्रत (copy) install करा |
| `--project DIR` | `$HOME` ऐवजी `DIR` खाली install करा, म्हणजे skill त्या repository सोबत राहते |
| `--force` | या script ने तयार न केलेली विद्यमान directory overwrite करा (डिफॉल्टनुसार नाकारले जाते) |
| `--verify` | Install केल्यानंतर `tests/run_tests.py` चालवा; अयशस्वी झाल्यास exit 1 |
| `--dry-run` | फक्त योजना दाखवा |

पुन्हा चालवणे सुरक्षित आहे. Exit codes: `0` यशस्वी, `1` installation नाकारले किंवा पडताळणी अयशस्वी, `2` वापरातील त्रुटी.

### Claude Code आणि claude.ai

```bash
sh kbeauty-trade-matchmaker/install.sh                                  # Personal: every project on this machine
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project  # Project scope
```

- **फोल्डरचे नाव `kbeauty-trade-matchmaker` असणे आवश्यक आहे**, जे `SKILL.md` मधील `name` शी जुळते.
- **`SKILL.md` आधीच पूर्णपणे मानकांनुसार (conformant) आहे; frontmatter keys वाढवणे ही सुधारणा नसून regression (मागे जाणे) आहे.** [Agent Skills open standard](https://agentskills.io/specification) नेमकी सहा fields ओळखते: `name` आणि `description` (आवश्यक), आणि पर्यायी `license`, `compatibility`, `metadata` व प्रायोगिक `allowed-tools`. Claude Code बाहेर (claude.ai, Skills API) फक्त ही सहाच स्वीकारली जातात, त्यामुळे फक्त Claude Code साठीची एक जरी key असली तरी upload अडतो. या package मध्ये फक्त दोन आवश्यक keys आहेत. आवृत्ती (version) माहिती `SKILL.md` च्या मुख्य मजकुरात असते.
- वेगवेगळे platforms आपोआप sync होत नाहीत. Claude Code (filesystem), claude.ai (Customize → Skills मध्ये zip upload) आणि Skills API (`/v1/skills`) या प्रत्येकासाठी फोल्डर स्वतंत्रपणे upload करावे लागते.

### OpenAI Codex आणि ChatGPT

फोल्डर कोणताही बदल न करता वापरता येते, पण Codex `.claude/skills` वाचत नाही. त्याची skill roots `.agents/skills` आहेत.

```bash
sh kbeauty-trade-matchmaker/install.sh --runtime codex                                # User scope: ~/.agents/skills/
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo   # Repository scope
```

- Codex working directory पासून repository root पर्यंतच्या प्रत्येक directory मध्ये `.agents/skills` शोधतो. `~/.codex/skills` deprecated आहे पण अजूनही समर्थित आहे; नवीन installation साठी `~/.agents/skills` वापरा.
- CLI आणि IDE extension मध्ये `$kbeauty-trade-matchmaker` किंवा `/skills` वापरून, किंवा ChatGPT मध्ये `@` वापरून स्पष्टपणे सुरू करा. आपोआप सुरू होणे (implicit invocation) `description` वरून ठरते.
- स्वतंत्र skill फोल्डर फक्त **ChatGPT desktop app, Codex CLI आणि IDE extension** मध्ये दिसते. ChatGPT **web आणि mobile** साठी skill plugin स्वरूपात package करावे लागते, जे v0.2.0 मध्ये समाविष्ट नाही.
- `AGENTS.md` हा skills install करण्याचा मार्ग नाही. ते repository साठी नेहमी लागू असणाऱ्या सूचनांचे वेगळे Codex वैशिष्ट्य आहे.

> **2026-09-13 रोजी अधिकृत documentation शी तपासले.** येथील काही माहिती सध्याच्या docs शी जुळत नसेल, तर docs बरोबर आहेत.
> [Agent Skills specification](https://agentskills.io/specification) · [Anthropic Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [Claude Code skills](https://code.claude.com/docs/en/skills) · [OpenAI build skills](https://learn.chatgpt.com/docs/build-skills)
>
> ChatGPT workspace मध्ये upload करण्याची प्रक्रिया, आणि desktop व्यतिरिक्त इतर ChatGPT platforms सोबत दिलेल्या `python3` scripts चालवू शकतात की नाही, हे पडताळता **आले नाही**; संबंधित OpenAI help pages ने स्वयंचलित requests ना HTTP 403 परत केला. Scripts चालू शकत नसल्यास, skill गुणांशिवाय फक्त पुरावा देण्याच्या पद्धतीवर परत जाते (`references/runtime-adapters.md` §4 पहा).

### Installation तपासा

दोन गोष्टी एकमेकांपासून स्वतंत्रपणे बिघडू शकतात. खालील तीन ओळी **फक्त code** तपासतात:

```bash
python3 kbeauty-trade-matchmaker/scripts/validate_output.py --version
python3 kbeauty-trade-matchmaker/adapters/tradewith_adapter.py --version
python3 kbeauty-trade-matchmaker/tests/run_tests.py
```

**Runtime ला skill दिसते का** हे तपासण्यासाठी, Claude Code च्या skill यादीत किंवा Codex च्या `/skills` यादीत `kbeauty-trade-matchmaker` शोधा. ते दिसत नसल्यास, फोल्डर थेट skills root खाली आहे का, त्याचे नाव `kbeauty-trade-matchmaker` आहे का, `SKILL.md` ची पहिली ओळ नेमकी `---` आहे का, आणि अधिक प्राधान्य असलेल्या scope मध्ये याच नावाचे दुसरे skill नाही ना, हे तपासा. बदल केल्यानंतर Codex पुन्हा सुरू (restart) करा.

---

## Tests चालवणे

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py       # Exit 0 when everything passes, 1 otherwise
python3 tests/run_tests.py -v    # One line per case, not just failures
```

Runner फक्त standard library वापरतो, fixtures स्वतःच्या स्थानाच्या सापेक्ष शोधतो, प्रत्येक script ला `--as-of 2026-09-12` देतो, आणि output ची अपेक्षित fixtures शी **byte-by-byte** तुलना करतो. Fixture cases च्या आधी तो schemas स्वतः तपासतो: ते parse होतात का, प्रत्येक `$ref` resolve होतो का, कोणताही असमर्थित keyword वापरलेला नाही ना, सामायिक `$defs` सर्व files मध्ये एकसारख्या आहेत का, आणि आत नमूद केलेल्या आवृत्त्या `scoring.config.json` शी जुळतात का.

Scripts थेटही चालवता येतात. JSON `stdout` वर जातो आणि प्रत्येक diagnostic संदेश `stderr` वर, त्यामुळे pipes सुरक्षित आहेत.

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py --input tests/fixtures/match-134.input.json --as-of 2026-09-12 --pretty
```

---

## Adapter quickstart: फक्त files, backend शिवाय

संपूर्ण workflow आजच **कोणत्याही अंतर्गत API शिवाय** चालतो. Adapter ला एक interface आणि दोन backends आहेत, `file` आणि `http`, आणि `file` हा डिफॉल्ट आहे. त्याला credentials, network किंवा कोणत्याही सेवेची गरज नाही.

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

Data directory ही साध्या JSON files ची रचना आहे, जिथे **file चे नाव हाच id असतो**, त्यामुळे समान input सह पुन्हा चालवल्यास तीच file overwrite होते आणि निकाल byte-स्तरावर स्थिर राहतात. ती package च्या बाहेर ठेवा, आणि त्यात प्रत्यक्ष कंपन्यांचा डेटा असल्यास version control च्या बाहेरही ठेवा; `.gitignore` आधीच `tradewith-data/` वगळते.

API उपलब्ध झाल्यावर फक्त एक flag बदलतो. गुण, schemas आणि output बदलत नाहीत.

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # Environment only; a command-line token lands in shell history
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

Environment variables import च्या वेळी नव्हे तर call च्या वेळी वाचले जातात. `--backend http` नमूद केल्याशिवाय काहीही network ला स्पर्श करत नाही, API वरून files कडे गुपचूप परत जाणे (silent fallback) होत नाही, आणि token कधीही errors, warnings किंवा साठवलेल्या documents मध्ये दिसत नाही. तपशील `adapters/tradewith_adapter.md` मध्ये आहेत.

---

## प्रलंबित निर्णय

सहा प्रश्न अजून खुले आहेत. प्रत्येकासाठी या implementation मध्ये एक डिफॉल्ट आहे, आणि प्रत्येक डिफॉल्ट एकाच ठिकाणी बदलता येतो.

| # | प्रश्न | येथील डिफॉल्ट | कुठे बदलावे |
|---|---|---|---|
| 1 | अंतर्गत seller/RFQ API अस्तित्वात आहे का, की files पासून सुरुवात करायची? | **दोन्ही.** डिफॉल्टनुसार file backend; API उपलब्ध असल्यास `--backend http` | `TRADEWITH_BACKEND` / `--backend` |
| 2 | Leads थेट सेवेच्या database मध्ये साठवायचे की staging मध्ये? | **Staging.** Skill फक्त `POST /research/leads` (किंवा `<data-dir>/leads/`) मध्ये लिहिते; पुढे नेणे (promotion) हे application-स्तरावरील मंजुरीचे काम आहे | Adapter चे write target |
| 3 | निश्चित 70 ची मर्यादा, की श्रेणीनिहाय percentile? | **निश्चित 70.** Percentile mode उपलब्ध आहे; वापरलेला mode नेहमी `summary` मध्ये नोंदवला जातो | `scoring.config.json` मधील `thresholds`, `--threshold`, `--threshold-mode` |
| 4 | Role addresses (`info@`, `sales@`) आणि नावाने ओळखले जाणारे संपर्क कितपत साठवायचे? | **फक्त कंपनी-स्तरीय संपर्क माध्यमे.** वैयक्तिक संपर्काचा अंदाज बांधणारा किंवा तो गोळा करणारा कोणताही code अस्तित्वात नाही | `evidence-policy.md`, `compliance-notes.md` |
| 5 | Directories साठी crawling ची व्याप्ती आणि सेवा-अटींची तपासणी? | **robots आणि ToS चा आदर करा, काहीही टाळून जाऊ नका.** प्रवेश रोखला गेल्यास मूल्य `"unknown"` राहते | `compliance-notes.md`, discovery स्रोतांच्या याद्या |
| 6 | RFQ नसताना seller outreach मध्ये काय सांगता येईल? | **RFQ नसलेल्या स्थितीसाठी स्वतंत्र template.** अस्तित्वात नसलेली मागणी सूचित करणे निषिद्ध आहे | `templates/seller_outreach.md`, `outreach-guidelines.md` |

---

## आवृत्त्या (Versions)

| आवृत्ती | मूल्य | काय दर्शवते | कुठे असते |
|---|---|---|---|
| `skill_version` | `0.2.0` | Package: prompts, references, scripts, templates, tests | `SKILL.md` चा मुख्य मजकूर, `match-result.skill_version` |
| `schema_version` | `0.1.0` | रचनेचा contract: field names, enums, आवश्यक fields च्या याद्या | प्रत्येक document, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | Rubric: weights, निकष, संकेत, दंड, thresholds, hard filters | `scoring.config.json`, प्रत्येक गुणांकित document |

```bash
python3 scripts/validate_output.py --version
```

Rubric बदलल्यास, साठवलेले गुण व्याख्येनुसारच कालबाह्य ठरतात. मूळ नोंदी त्यांचा पुरावा, query surface आणि `as_of` जपून ठेवत असल्यामुळे, पुन्हा crawling न करता गुण पुन्हा मोजता येतात. वेगवेगळ्या `score_version` मधील निकालांची एकाच यादीत तुलना करणे किंवा क्रमवारी लावणे निषिद्ध आहे, आणि `validate_output.py` हे पकडतो.

---

## आवश्यकता

- `python3` 3.9–3.14, फक्त standard library.
- Installer साठी POSIX shell.
- Discovery modes साठी runtime चे web search आणि page fetching आवश्यक आहे. Scripts पूर्णपणे offline चालतात, त्यामुळे tests आणि पुनर्गुणांकन network शिवाय चालतात.
- अंतर्गत डेटाशी जोडणी (integration) ऐच्छिक आहे; adapter च्या `file` backend ला कशाचीही गरज नाही.

---

## डेटा हाताळणी

या repository मध्ये कोणत्याही प्रत्यक्ष कंपनीचा डेटा किंवा वैयक्तिक माहिती नाही. Fixtures मध्ये `.example` domains वरील काल्पनिक कंपन्या वापरल्या आहेत. तुम्ही प्रत्यक्ष कामकाजाचा डेटा जोडायला सुरुवात केल्यावर, `.gitignore` आधीच करते त्याप्रमाणे `tradewith-data/`, `.env` आणि `out/` वगळलेले ठेवा, आणि तुम्ही साठवत असलेली माहिती दावा, URL, पाहिल्याची वेळ आणि छोटे अवतरण इतपतच मर्यादित राहते आहे ना, हे वेळोवेळी तपासा.

**हा कायदेशीर सल्ला नाही.** प्रत्येक अधिकारक्षेत्रातील (jurisdiction) direct-marketing नियम तपासणे हे माणसाचे काम आहे; हे package फक्त ती तपासणी कुठे आवश्यक आहे ते दर्शवते.
