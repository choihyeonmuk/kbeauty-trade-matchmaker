# K-Beauty Trade Matchmaker


**An Agent Skill that finds overseas K-Beauty buyers and Korean sellers on the public web, verifies every material claim against a source you can click, scores both sides with deterministic scripts, matches buying requests to sellers, and stops at an outreach draft for a human to review.**


The same folder runs unmodified in **Claude Code** and **OpenAI Codex**. Python is **standard library only** (3.9–3.14); there is nothing to `pip install`.


[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [Project page](https://kbeauty.tradewith.kr/) · [Contact on LinkedIn](https://www.linkedin.com/in/hm-choi)


![Find the right K-Beauty trade partner](kbeauty-trade-partner-linkedin-cities.png)

> A visual overview of the workflow: discover, verify, match, and keep a human in the loop.


> **Status: v0.1.1.** The pipeline is tested against 179 cases on fictional fixtures and was trialled once against the live web. The scoring rubric is **not yet validated against real outcomes**: scores are reproducible and traceable, not yet known to be predictive. RFQ Matching and Outreach Draft have not been run on live data. Read [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) before trusting a score.


---


## Safety boundary: this skill sends nothing


The most important fact about this package: **no code in it can send a message.**


- **Draft only.** Outreach work always ends in state `READY_FOR_REVIEW`, with `auto_send: false` and `manual_approval_required: true` on the draft.
- **Humans approve, external systems send.** The skill may move a lead through `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` and no further. `APPROVED_FOR_OUTREACH` and later belong to a CRM and a person; the adapter **refuses** those transitions.
- **No SMTP, Gmail, SES or webhook sending code.** No inferring or bulk-collecting personal emails or phone numbers (company-level channels only). No bypassing CAPTCHA, logins, paywalls, robots.txt or terms of service.
- **Nothing is invented.** MOQ, certifications, export markets, exclusivity and capacity are recorded only when a source states them; otherwise they stay `"unknown"`. Unbacked urgency such as "a buyer is waiting for you" is banned at the template level.
- **No legal judgements.** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) marks where a check is needed; it never concludes that sending is allowed.


This is a design decision, not a missing feature. *Drafting* and *sending* are different permissions, and this package only holds the first.


---


## What it does


It turns a day of searching Google, LinkedIn and trade-show directories into **a verifiable shortlist where every claim links to its source**.


1. **Discover** buyer and seller candidates on the public web by country, category, OEM/ODM, MOQ and certification.
2. **Verify** each claim against trustworthy sources, usually the company's own site, and store it with its URL and the time it was observed.
3. **Normalize** domains and company names, merge duplicates, and put buyer requirements and seller capabilities on one schema.
4. **Score** buyers and sellers with deterministic Python scripts. Same input and same `--as-of` give the same output.
5. **Match** a buying request (RFQ) to sellers: hard filters, then weighted score, then a bounded semantic rerank, then unknown handling. It returns the top N **and the reason each rejected seller was rejected**.
6. **Draft** personalised outreach from verified facts only, and stop there.


---


## Four modes


Ask the agent in plain language. The forms below are shorthand for the parameters each mode takes.


### Buyer Discovery


```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```


Finds distributors, importers and wholesalers that carry K-Beauty in the UAE, and normalises company type, brands carried, wholesale availability, partnership signals, official contact channels and evidence URLs.


### Seller Discovery


```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```


Finds Korean manufacturers and brands, and verifies OEM/ODM capability, MOQ, certifications, main products and export or partnership signals.


### RFQ Matching


```
/kbeauty-match rfq="#134" top=10
```


Filters sellers against the RFQ's product, MOQ, destination, certifications and private-label requirement, combines quantitative and qualitative scores, and returns the top 10 plus the exclusion reasons. "No qualified match", with its reasons, is a normal output.


### Outreach Draft


```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```


Writes a subject, body and personalisation points from verified facts only. Weak evidence is generalised or marked "needs verification". **It does not send.**


---


## Example output


An excerpt of RFQ Matching on the bundled test fixture. The sellers are fictional.


```
RFQ #134
Destination: United Arab Emirates
Product: Sunscreen
MOQ: <= 3,000
