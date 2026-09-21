# TradeWith Adapter

Korean gloss: TradeWith 연동 어댑터 — 내부 RFQ/Seller 데이터를 읽고, 리서치 결과·매칭 결과·아웃리치 초안을 저장하는 유일한 경계. API가 아직 없어도 로컬 파일 백엔드로 오늘 바로 동작한다.

`adapters/tradewith_adapter.py` is the only place in this package that touches TradeWith's internal
data. Everything else — `scripts/*.py`, `references/*.md`, `SKILL.md` — is backend-agnostic by
construction: no script imports a network client, and no scoring path reads a credential.

The adapter exists because PRD Open Question 1 is still open: *"MVP가 접근할 TradeWith 내부
Seller/RFQ API는 이미 존재하는가, 아니면 파일/DB 직접 연결로 시작할 것인가?"* The answer this
package ships with is **both**. One interface, two backends:

| Backend | When you use it | Needs |
|---|---|---|
| `file` (default) | Today. Pilot runs, offline fixtures, the test harness, any operator with a folder of JSON. | Nothing. No credentials, no network, no service. |
| `http` | Once TradeWith's internal API exists. | `TRADEWITH_BASE_URL` + `TRADEWITH_TOKEN` in the environment. |

Swapping between them changes one flag. It never changes a score, a schema, or a rendered output
block — which is the whole point of putting the boundary here.

---

## 1. Quick start (file backend, zero setup)

```bash
cd kbeauty-trade-matchmaker

# 1. Point the adapter at a data directory. It is created on first write.
export TRADEWITH_DATA_DIR=./tradewith-data

# 2. Read a buying request.
python3 adapters/tradewith_adapter.py get-rfq --id 134 --pretty

# 3. List internal seller candidates.
python3 adapters/tradewith_adapter.py list-sellers --country KR --limit 20

# 4. Store discovery results (buyer or seller documents).
python3 adapters/tradewith_adapter.py save-leads --input out/buyers.scored.json

# 5. Store one match run.
python3 adapters/tradewith_adapter.py save-matches --input out/match-134.json

# 6. Queue outreach drafts for human review.
python3 adapters/tradewith_adapter.py save-outreach-drafts --input out/drafts.json

# 7. Move a lead inside the skill's own range of the state machine.
python3 adapters/tradewith_adapter.py update-lead-status \
    --id BUY-gulfbeauty-example-com --status VERIFIED
```

Every command writes **only** JSON to stdout and **only** diagnostics to stderr, so `|` and `>` are
safe. Exit codes match the scripts: `0` success, `1` validation/data error, `2` usage error.

Shared flags (`--backend`, `--data-dir`, `--as-of`, `--pretty`, `--quiet`, …) are accepted either
before or after the subcommand, so `--backend http get-rfq` and `get-rfq --backend http` both work.

---

## 2. Configuration

### 2.1 Environment variables

Read at **call time only** — never at import, never written to disk, never echoed (INV-25, INV-27).

| Variable | Alias also accepted | Backend | Default | Meaning |
|---|---|---|---|---|
| `TRADEWITH_BASE_URL` | `TRADEWITH_API_BASE` | `http` | *(none — required)* | API root, e.g. `https://api.tradewith.example/v1`. No trailing slash needed. |
| `TRADEWITH_TOKEN` | `TRADEWITH_API_TOKEN` | `http` | *(none — required)* | Bearer token. Set it in the environment; never on the command line, where it lands in shell history. |
| `TRADEWITH_BACKEND` | — | both | `file` | `file` or `http`. |
| `TRADEWITH_DATA_DIR` | — | `file` | `./tradewith-data` | Root of the local JSON tree. |
| `TRADEWITH_TIMEOUT_SECONDS` | — | `http` | `20` | Per-request timeout. |
| `TRADEWITH_MAX_RETRIES` | — | `http` | `2` | Retry budget, 0–5. Total attempts = retries + 1. |
| `TRADEWITH_APP_URL` | — | both | `https://app.tradewith.example` | Human-clickable app root used to build `source_url` on internal-record evidence (BUILD-CONTRACT.md 4.7). |

`TRADEWITH_BASE_URL` / `TRADEWITH_TOKEN` are the spellings fixed by BUILD-CONTRACT.md 13.3 and win
when both spellings are present. The `_API_` aliases exist so an operator who already exported them
under that name does not have to re-learn anything.

### 2.2 Safety behaviour you should expect

- **The default backend is `file`.** No call reaches the network unless someone asked for `http` by
  name, via `--backend http` or `TRADEWITH_BACKEND=http`. There is no "try the API and fall back".
- **Missing credentials fail loudly**, with a message naming the variable to set — never a silent
  degrade to an empty result.
- **Plain `http://` is refused** for any host other than `localhost` / `127.0.0.1` / `::1`, so a
  bearer token is never put on the wire in the clear.
- **The token is never printed.** Every error message, retry warning and stored document passes
  through a redaction step that removes the token and any `Authorization:` header value.
- **Identifiers are path-checked.** `--id ../../etc/passwd` is a usage error, not a file read.

---

## 3. The six capabilities (PRD 13.1)

One function per capability, exactly as fixed by BUILD-CONTRACT.md 13.3. The CLI exposes each under
a plain-English name and under its endpoint-shaped alias; both spellings are the same code path.

| PRD 13.1 endpoint | Python function | CLI subcommand (and alias) | Returns |
|---|---|---|---|
| `GET /rfqs/:id` | `get_rfq(rfq_id)` | `get-rfq --id 134` | one `rfq` document |
| `GET /sellers?filters=` | `list_sellers(filters)` | `list-sellers` | list of `seller` documents |
| `POST /research/leads` | `post_research_leads(records, overwrite=False)` | `save-leads` (`post-research-leads`) `[--overwrite]` | `[{"id", "status", "result"}]` |
| `POST /matches` | `post_matches(match_result)` | `save-matches` (`post-matches`) | `{"id"}` |
| `POST /outreach-drafts` | `post_outreach_drafts(drafts)` | `save-outreach-drafts` (`post-outreach-drafts`) | `[{"id", "status"}]` |
| `PATCH /leads/:id/status` | `patch_lead_status(lead_id, status)` | `update-lead-status` (`patch-lead-status`) | `{"id", "status"}` |

Calling from Python instead of the CLI:

```python
import sys
sys.path.insert(0, "kbeauty-trade-matchmaker/adapters")
import tradewith_adapter as tw

adapter = tw.get_adapter(backend="file", data_dir="./tradewith-data", as_of="2026-09-12")
rfq = adapter.get_rfq("134")
sellers = adapter.list_sellers({"country": "KR", "product_categories": ["sunscreen"]})
tw.post_matches(match_result, adapter=adapter)
```

`get_adapter(fixtures_dir=...)` is the BUILD-CONTRACT.md 13.3 rule (d) spelling of the same thing:
it points the file backend at a fixture tree so the whole package is exercisable with no network and
no credentials.

### 3.1 `list_sellers` filters

Filters are **retrieval hints, not hard filters.** A seller whose field is `"unknown"` or absent is
**kept**, always. Hard filtering — with its unknown handling, its `rules_skipped_unknown` list and
its unknown penalties — belongs to `scripts/score_match.py` (BUILD-CONTRACT.md 6.2, INV-07).
Filtering on unknowns here would silently delete exactly the candidates the scoring stage is
required to surface and penalise.

| Filter key | Type | Semantics |
|---|---|---|
| `seller_ids` | array of id strings | Exact ids only. |
| `country` | alpha-2 or array | Excludes only a seller whose **known** country is outside the list. |
| `company_type` / `company_types` | string or array | Same rule against `seller.company_type`. |
| `product_categories` | array | Keeps a seller whose known list overlaps. A **verified-empty** list (`[]`) is a real negative and is excluded; an absent list is unknown and is kept. |
| `product_forms` | array | Same rule. |
| `oem_odm`, `private_label`, `brand_export`, `english_site` | boolean | Compared only when the seller's tri-state value is `true`/`false`; `"unknown"` is kept. |
| `certifications` | array | Keeps a seller whose known list contains **all** of them. |
| `max_moq` (+ `moq_unit`) | number | Compares on `seller.moq.min` (BUILD-CONTRACT.md 3.3, `HF-03` side rule). A **unit mismatch keeps the seller** — it never silently excludes. |
| `max_lead_time_days` | number | Compares on `lead_time_days.max` (worst case). |
| `operational_status` | string or array | Compared only against a known status. |
| `limit` | integer | Ceiling on rows returned, applied after a stable sort by `seller_id`. |

CLI convenience flags `--country`, `--product-category` (repeatable), `--company-type`, `--max-moq`
and `--limit` are merged into whatever `--filters` / `--filters-file` supplied; the explicit flags
win.

### 3.2 Validation: nothing invalid is ever persisted

Every write path validates its payload before any file is opened or any request is made, and
refuses on failure (BUILD-CONTRACT.md 13.3 rule a). The chain is:

1. `scripts/validate_output.py` — schema **and** invariant checks. Used whenever it is present.
2. `scripts/_common.validate` — schema only. Used when the script above is unavailable; a note on
   stderr says the invariant pass was skipped.
3. **Refusal.** If neither validator can be used, the adapter refuses to read or write rather than
   persist something unchecked.

Validation is all-or-nothing: `save-leads` validates every record before writing the first one, so a
schema or invariant failure cannot leave the data directory half-updated. A record whose `status` is
`QUALIFIED`, `MATCH_CANDIDATE` or `READY_FOR_REVIEW` must be scored with `qualified: true` (§3.4).

**Re-running `save-leads` never silently downgrades a stored lead** (file backend). Each record is
compared with `leads/<id>.json` and reported per id in `result`:

| `result` | When | Written? |
|---|---|---|
| `created` | no stored lead | yes |
| `unchanged` | identical to the stored lead | no |
| `updated` | differs, and is not a downgrade (or `--overwrite` was passed) | yes |
| `refused` | would replace a scored lead with an `unscored` one, or move `status` backwards in the 9.1 order — without `--overwrite`; or the stored lead is in an application-layer state (`--overwrite` does not apply) | no; `reason` says why |

The other records in the batch are still written. The CLI prints every row and exits `1` when any
row is `refused`.

Reads are validated too, but warn by default rather than fail, so one malformed stored record does
not take a whole run down. `--strict` promotes those warnings to errors.

### 3.3 Outreach drafts are a review queue, never a dispatch

`post_outreach_drafts` moves data **into** TradeWith. It does not move a message **toward** a
recipient. This package contains no message-transport capability of any kind, by design (PRD 11.1,
11.2, INV-10) — drafting and sending are deliberately separate capabilities and only drafting lives
here.

Enforced per draft:

- `status` is `READY_FOR_REVIEW`, `auto_send` is `false`, `manual_approval_required` is `true`.
  Absent flags are filled in with those values; a flag **present with any other value is refused**,
  never silently corrected.
- At most `scoring.config.json output.max_outreach_drafts_per_run` drafts per call (currently 20) —
  the PRD 11.1 bulk guardrail.
- Required fields: `entity_id`, `side` (`Buyer`/`Seller`), `channel_type`, `channel_value`,
  `language`, `subject`, `body`.
- `channel_type` must be one of the **company-level** channels of BUILD-CONTRACT.md 3.6
  (`partnership_form`, `wholesale_form`, `form`, `contact_page`, `corporate_email`, `phone`,
  `linkedin`, `messenger`, `other`). Personal addresses and direct lines are out of scope
  everywhere (INV-11, INV-31).
- Every `personalization_facts[]` entry needs a `fact` and an absolute `source_url` a reviewer can
  click (PRD OUT-02).
- `draft_markdown`, the rendered 10.4 draft, is required: line 2 must be exactly
  `Status: READY_FOR_REVIEW` and line 3 exactly `Auto-send: false` (BUILD-CONTRACT.md R10.4.1).
- **The draft must pass `validate_outreach_draft()`** from `scripts/validate_output.py` (loaded by
  file path, so an installed copy always uses its own validator; if it cannot be loaded, nothing is
  queued). When the file backend holds the target — `leads/<entity_id>.json`, or a stored match run
  for the cited RFQ — and the cited RFQ, they are passed as the record, so `DRAFT-09` (scored,
  qualified, hard filter passed) and `DRAFT-05` (facts match evidence) apply too. Any
  error-severity issue — no personalization fact, a missing `(광고)`, a missing or altered legal
  notice, a leftover `{{token}}` or `[[ev:]]`, and the rest of output-format 10.4 — is a refusal
  that lists every issue. Warnings (`DRAFT-12` body length, the `INV-31` name-shape heuristics)
  print to stderr and do not block.
- Any dispatch- or credential-shaped key (`recipients`, `to`, `cc`, `bcc`, `reply_to`,
  `schedule_at`, `send_at`, `transport`, `smtp_host`, `webhook_url`, `token`, `bearer`,
  `password`, `api_key`, …) is a refusal. The scan is **recursive**: a key nested inside a
  sub-object or an array element (`delivery.transport`, `auth_block.token`) is the same
  instruction and gets the same refusal, and the error names the JSON path of every hit.
- A **live-demand claim** or an **RFQ citation** in the subject or body (the JSON fields and the
  rendered draft) — "currently looking", "a distributor asked about your brand", "only 3 slots",
  `해당 바이어가 관심이 있습니다`, `문의가 왔습니다`, `RFQ #134`, … or any `rfq_*` field — is a
  refusal unless every one of these holds (INV-34, R10.4.3, PRD 11.1, PRD test T05):
  - the run has an **explicit** as-of date: `--as-of` on the CLI, `as_of=` to `get_adapter()`
    (or to `_normalize_draft()` without an adapter). A draft that carries an `rfq_*` field or cites
    `RFQ #…` is refused without one, because an RFQ's age measured against
    `scoring.config.json as_of_default` (a frozen date) would let a stale RFQ through;
  - the draft carries `rfq_id` (matching every `RFQ #…` in the text), an `rfq_status` of
    `qualified` / `matching` / `proposal_open`, and an `rfq_as_of` date;
  - the text states that status and that date, and states no other, non-open status;
  - `rfq_as_of` is not after the run's `--as-of` and not more than `max_rfq_age_days` before it
    (`scoring.config.json output.max_rfq_age_days`, default 30): an old status is stale demand;
  - `get_rfq(rfq_id)` on the same backend finds the RFQ, in an open status equal to
    `rfq_status`, with `as_of` equal to `rfq_as_of` — the caller's fields are claims, the stored
    RFQ is the fact;
  - for every stored match run of that RFQ (file backend `matches/`), the target seller is not in
    `excluded[]` and its `hard_filter.passed` is `true`.

  The phrase list (`live_demand_matches`), the status/date readers and the open states are
  imported from `scripts/validate_output.py`, so the adapter and the validator hold one list. It
  targets a third party said to want the recipient, so the sender's own search ("We are currently
  looking for UAE distributors") is not refused, and a dated "you are actively looking for …"
  sentence is accepted without an RFQ when a Personalization fact backs its date (a stored lead's
  `sourcing_signals` item when the file backend holds the lead; see output-format 10.4.1 `INV-34`). It is a floor under
  the reviewer, never a licence for a demand claim it happens not to match.

`draft_id` defaults to `OD-<entity_id>-<channel_type>`, so re-running the same draft overwrites
rather than piling up duplicates.

### 3.4 `patch_lead_status` stops where the human starts

The skill owns `DISCOVERED → VERIFIED → QUALIFIED → MATCH_CANDIDATE → READY_FOR_REVIEW` (plus
`CLOSED`). `APPROVED_FOR_OUTREACH` and every later state belong to the TradeWith/CRM application
layer and require a human decision, so the adapter **refuses to write them** (BUILD-CONTRACT.md 9.2,
INV-09, INV-37) — before it makes any request, on either backend.

On the file backend the adapter also knows the lead's current state, so it enforces the
BUILD-CONTRACT.md 9.1 transition table: forward skips such as `VERIFIED → MATCH_CANDIDATE` are
refused, the listed backward edges are allowed, re-asserting the current state is a no-op, and a
record already in an application-layer state is left alone. `QUALIFIED`, `MATCH_CANDIDATE` and
`READY_FOR_REVIEW` are refused for a lead that is `unscored` or does not carry `qualified: true`
(INV-37, SKILL.md Mode 4): a raw or below-threshold record stops at `VERIFIED`.

The `http` backend cannot read a lead, so it applies only the target-state check; the transition
table and the scored-and-qualified rule are the server's to enforce (§5.6).

### 3.5 Adapter responses are evidence

Facts read out of TradeWith's own database are still evidence, and carry the BUILD-CONTRACT.md 4.7
shape: `source_type "internal_record"`, `source_tier 2`, `is_official false`,
`retrieval_method "internal_api"`, a resolvable internal `source_url` (never a bare id, never a
query string), and an `observed_at` derived from `--as-of` rather than the wall clock (INV-14,
INV-24).

```bash
python3 adapters/tradewith_adapter.py get-rfq --id 134 --with-evidence --as-of 2026-09-12
```

appends, idempotently:

```json
{
  "schema_version": "0.1.0",
  "evidence_id": "EV-004",
  "claim": "product_category",
  "value": "sunscreen",
  "source_url": "https://app.tradewith.example/rfqs/134",
  "source_type": "internal_record",
  "source_tier": 2,
  "is_official": false,
  "observed_at": "2026-09-12T00:00:00Z",
  "source_date": "2026-09-12",
  "confidence": 0.9,
  "quote_or_summary": "TradeWith internal record 134 read via the internal API on 2026-09-12.",
  "retrieval_method": "internal_api"
}
```

An internal record never substitutes for public evidence when the claim is about a third-party
company's **public** behaviour (BUILD-CONTRACT.md 4.7).

---

## 4. File backend layout

```
<data-dir>/                      # --data-dir, TRADEWITH_DATA_DIR, or ./tradewith-data
├── rfqs/
│   └── 134.json                 # one rfq document, read by get-rfq
├── sellers/
│   ├── SEL-hankosun-example-com.json
│   └── SEL-bigmoq-example-com.json     # one seller document each, read by list-sellers
├── leads/
│   └── BUY-gulfbeauty-example-com.json # written by save-leads, patched by update-lead-status
├── matches/
│   └── MR-134-2026-09-12-01.json       # written by save-matches
└── outreach-drafts/
    └── OD-SEL-hankosun-example-com-partnership_form.json
```

- **Filenames are ids.** `rfqs/<rfq_id>.json`, `sellers/<seller_id>.json`,
  `leads/<buyer_id|seller_id>.json`, `matches/<match_run_id>.json`,
  `outreach-drafts/<draft_id>.json` (BUILD-CONTRACT.md 3.5).
- **Writes are last-write-wins on the id**, so re-running the same inputs is byte-stable (INV-13).
  The exception is `leads/`: `save-leads` refuses a downgrade unless `--overwrite` is passed (§3.2).
- Files are UTF-8, `indent=2`, `ensure_ascii=False`, one trailing newline — readable in a diff and
  reviewable in a pull request.
- Directories are created on first write. Reading a section that does not exist yet returns an
  empty list (`list-sellers`) or a clear "not found" error (`get-rfq`, `update-lead-status`).
- **Put this directory outside the skill package**, and outside version control if it holds real
  company data. Nothing in the package reads it by default.

Seeding it is deliberately boring: drop a schema-valid RFQ into `rfqs/` and schema-valid sellers
into `sellers/`, and the whole four-mode workflow runs against real data with no service at all.
That is the intended pilot path while the API is being built.

---

## 5. What TradeWith must build (backend team spec)

The HTTP backend is written against the six PRD 13.1 endpoints. This section is the request/response
contract; the field-level shapes are the shipped schemas, not prose, so implement against those
files directly.

**Conventions for all six.** JSON in, JSON out, UTF-8. `Authorization: Bearer <token>`.
`Content-Type: application/json; charset=utf-8` on bodies. `404` for a missing resource, `401` for a
bad token, `422` for a body that fails validation with a machine-readable error list. `408`, `429`,
`500`, `502`, `503` and `504` are treated as retryable by the client (bounded, 2 retries by default,
1s then 2s backoff); everything else fails fast. Every other status is returned to the operator with
its body, truncated and token-redacted.

Responses may be bare or wrapped in a single-key envelope (`{"rfq": …}`, `{"sellers": […]}`,
`{"results": […]}`); the client unwraps either, so pick whichever fits your API conventions.

### 5.1 `GET /rfqs/:id`

Load one buying request.

- **Path param**: `id`, the bare TradeWith RFQ id (`"134"` — no `#`; BUILD-CONTRACT.md 3.5).
- **200 body**: one document conforming to `schemas/rfq.schema.json` — required keys
  `schema_version`, `score_version`, `rfq_id`, `buyer_id`, `destination_country`,
  `product_category`, `commercial_model`, `status`, `qualification_score`, `confidence`,
  `evidence`, `notes`.
- `score_version` is `"unscored"` for an RFQ TradeWith stores itself; the skill computes readiness
  and returns a scored copy.
- `status` is the **lowercase** RFQ lifecycle (`draft | qualified | matching | proposal_open |
  matched | closed`) and is a different namespace from the uppercase entity states
  (BUILD-CONTRACT.md 3.4). Do not mix them.
- Quantities (`quantity`, `max_moq`) follow the BUILD-CONTRACT.md 3.3 normal form: a number, or
  `{"min": m, "max": M, "unit": u?}` with **both** bounds, or the literal `"unknown"`. An open
  lower bound ("MOQ from 1,000") must be sent as `"unknown"` plus a note — never as `{"min": 1000}`
  and never as a ceiling.

### 5.2 `GET /sellers?filters=<url-encoded JSON>`

Internal seller candidates.

- **Query**: one parameter, `filters`, holding the URL-encoded JSON filter object of §3.1. Sending
  the whole object in one parameter keeps the wire format identical to the Python call and matches
  PRD 13.1's `GET /sellers?filters=...`.
- **200 body**: an array of documents conforming to `schemas/seller.schema.json`, or
  `{"sellers": [...]}`.
- **Unknown is a value, not an omission.** Send `"unknown"` (or omit the field) for a fact you have
  not verified; send `false` or `[]` only when you have *checked* and the answer is genuinely
  negative. Those are different states and they score differently (BUILD-CONTRACT.md 3.2). Filling
  an unverified field with `false`, `0`, `""` or `"N/A"` corrupts every downstream score.
- `certifications_verified` must be `true` only for an **exhaustive, officially sourced**
  certification list — it is what allows the required-certification hard filter to reject a seller
  (`HF-04`, BUILD-CONTRACT.md R6.2.4).
- Pagination is your choice; if you add it, keep the unpaginated shape working, because the client
  treats the response as a complete candidate list.

### 5.3 `POST /research/leads`

Store external discovery results.

- **Request**: `{"as_of": "YYYY-MM-DD", "schema_version": "0.1.0", "overwrite": false, "records": [ … ]}`
  where each record is a full `buyer` or `seller` document. The client has already validated every
  record against the schema and the invariants; validate again server-side anyway.
- **201 body**: `{"results": [{"id": "<buyer_id|seller_id>", "status": "<entity_status>"}, …]}`,
  one row per submitted record, in the submitted order.
- **Refuse downgrades unless `overwrite` is true**: replacing a scored lead with an `unscored` one,
  or moving `status` backwards. Never replace a lead in `APPROVED_FOR_OUTREACH` or later. The client
  cannot read your stored leads, so this rule is yours to enforce (the file backend's version is §3.2).
- **Dedupe on `canonical_domain`**, per PRD 13.2. The client sends `canonical_domain`,
  `normalized_name`, `alias_domains[]` and `merged_from[]` already computed; merging on your side
  should keep the surviving id and append absorbed ids to `merged_from`.
- **Store `score_version` with the score** (PRD 13.2), so a rubric change can be recomputed rather
  than guessed at. Keep the deterministic components (`dimension_scores`, `dimension_details`,
  `unknown_penalty_applied`) separate from the AI narrative (`rationale`, `risks`) — that separation
  is a PRD 13.2 storage requirement, not a nicety.
- Records arrive in the `DISCOVERED`–`READY_FOR_REVIEW` range only. A record carrying
  `APPROVED_FOR_OUTREACH` or later is a client bug; reject it.

### 5.4 `POST /matches`

Store one match run.

- **Request**: `{"match_result": { … }}`, one document conforming to
  `schemas/match-result.schema.json`.
- **201 body**: `{"id": "<match_run_id>"}`. Echoing the submitted `match_run_id`
  (`MR-<rfq_id>-<as_of>-<NN>`) is fine and is what the client falls back to.
- Store the run whole: `summary`, `results[]`, `excluded[]` **and** `no_match`. The exclusion list
  is the operator-facing half of the product (PRD 12.2 "Excluded — Seller X: MOQ minimum 10,000")
  and dropping it destroys the auditability the whole skill exists to provide.
- `threshold` and `summary.threshold_used` / `threshold_mode` record which cut-off was applied, so a
  reader never has to guess. Keep them.

### 5.5 `POST /outreach-drafts`

Queue drafts for human review.

- **Request**: `{"as_of": "…", "auto_send": false, "manual_approval_required": true,
  "drafts": [ … ]}`. Each draft carries `draft_id`, `entity_id`, optional `rfq_id`, `side`,
  `channel_type`, `channel_value`, `language`, `subject`, `body`, `personalization_facts[]`,
  `draft_markdown`, optional `rfq_status` / `rfq_as_of`, and the three fixed flags of §3.3.
- Before posting, the client reads each cited RFQ through `GET /rfqs/:id` (§3.3), so that endpoint
  must be reachable whenever a draft cites an RFQ.
- **201 body**: `{"results": [{"id": "<draft_id>", "status": "READY_FOR_REVIEW"}, …]}`.
- **This endpoint must not dispatch anything.** It is a review-queue write. If your platform later
  gains a send capability, it must be a **separate endpoint behind a separate human approval**, and
  the skill will still not call it (PRD 11.2, INV-10).
- Returning any status other than `READY_FOR_REVIEW` makes the client fail the call rather than
  accept an escalation it did not ask for.
- Reject a draft whose `channel_type` is not one of the nine company-level channels, and never store
  a personal email address, direct line or personal social handle (PRD 11.3, INV-11).

### 5.6 `PATCH /leads/:id/status`

Move a lead through the operational state machine.

- **Path param**: `id`, the lead's `buyer_id` or `seller_id`.
- **Request**: `{"status": "<entity_status>"}`.
- **200 body**: `{"id": "<lead_id>", "status": "<entity_status>"}`.
- The skill only ever sends `DISCOVERED`, `VERIFIED`, `QUALIFIED`, `MATCH_CANDIDATE`,
  `READY_FOR_REVIEW` or `CLOSED`. Enforce the PRD 8 transition table server-side — and refuse
  `QUALIFIED` / `MATCH_CANDIDATE` / `READY_FOR_REVIEW` for a lead that is unscored or not
  `qualified: true`, because this client cannot read the lead to check (§3.4) — and own
  `APPROVED_FOR_OUTREACH` and everything after it yourself — that edge is the human approval gate
  and must never be reachable from an automated client.

### 5.7 Things the backend should *not* ask the skill for

- **A send endpoint.** Not implemented here, and not planned here.
- **Personal contact enumeration.** Company-level channels only.
- **A "fill in the blanks" mode.** A field the skill could not evidence arrives as `"unknown"` on
  purpose. Do not ask for it to be guessed, and do not default it to `false` on ingest.
- **Credentials in a file.** The adapter reads the token from the environment at call time and
  nowhere else.

---

## 6. What this adapter deliberately does not do

| Not done | Why | Where it belongs |
|---|---|---|
| Send an email, DM, SMS or webhook message | PRD 11.1/11.2, INV-10. Drafting and sending are separate capabilities; only drafting lives in this package. | An external system, behind a human approval step |
| Set `APPROVED_FOR_OUTREACH` or any later state | INV-09, INV-37 — approval is a human decision | TradeWith / CRM application layer |
| Score, rank, or hard-filter | The adapter is a data boundary; mixing retrieval and scoring would make scores depend on which backend answered | `scripts/score_*.py` |
| Expand a region word (`"GCC"`, `"EU"`) into countries | Scripts never expand regions; the agent does it before the query surface is built (BUILD-CONTRACT.md 8.7) | The agent's discovery step |
| Crawl the public web | No scraping, no login automation, no CAPTCHA handling, no robots.txt override (INV-12) | The runtime's own web tool, per `references/runtime-adapters.md` |
| Cache responses to disk | Would make a re-run depend on invisible state and break byte-stability | Nothing — re-read instead |
| Store credentials, cookies or personal data | INV-25, INV-11 | The operator's environment |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR: the http backend needs a base URL` | `--backend http` with no `TRADEWITH_BASE_URL` | Export it, or drop back to the file backend |
| `ERROR: refusing to send a bearer token over plain http` | `http://` to a non-localhost host | Use `https://` |
| `ERROR: … failed buyer validation and was not written` | The payload violates `schemas/buyer.schema.json` or an invariant | Run `python3 scripts/validate_output.py --input <file>` to see every problem at once |
| `ERROR: no validator is available` | `scripts/` is missing from the installed package | Reinstall the whole skill folder; the adapter will not write unvalidated data |
| `warning: seller … failed seller validation; returning it anyway` | A stored record is malformed | Fix the record, or re-run with `--strict` to make it fatal |
| `ERROR: transition VERIFIED -> MATCH_CANDIDATE is not a skill-performed edge` | A forward skip in the state machine | Go through `QUALIFIED` (BUILD-CONTRACT.md 9.1) |
| `ERROR: refusing to write status APPROVED_FOR_OUTREACH` | Working as designed | A human approves in TradeWith; the skill's job ended at `READY_FOR_REVIEW` |
| `ERROR: refusing N outreach drafts in one call` | Above the bulk guardrail | Split the run; the cap is a PRD 11.1 safety limit, not a performance limit |
| `ERROR: refusing status QUALIFIED for lead …: it is unscored` / `qualified is False` | The lead was never scored, or scored below threshold | Score it (`score_buyer.py` / `score_seller.py`) and save the scored record; a non-qualified lead stays at `VERIFIED` |
| `ERROR: N record(s) refused and left as stored` from `save-leads` | The run would downgrade a stored lead (see each row's `reason`) | Save the scored output instead, or pass `--overwrite` if replacing it is intended |
| `ERROR: draft[i] (…) failed outreach-draft validation and was not queued` | `validate_outreach_draft` found an error (listed below the line) | Fix the draft; `python3 scripts/validate_output.py --input draft.md --schema outreach-draft --record <record>` shows the same issues |
| `ERROR: draft[i] … is stale demand` / `… but RFQ 134 is 'closed'` | The cited RFQ is older than `max_rfq_age_days`, or its stored status/date differs from the draft | Re-read the RFQ with `get-rfq` and redraft with its current status and `as_of`, or drop the demand claim |
| `ERROR: rfq_id '…' is not a valid identifier` | Path separators or `..` in an id | Pass the bare id (`134`, `BUY-acme-com`) |

---

## 8. Cross-references

| Topic | File |
|---|---|
| Which runtime tool plays which role | `references/runtime-adapters.md` |
| Unknown vs verified-negative, ranges, ids, evidence | `references/data-contract.md`, `references/evidence-policy.md` |
| Hard filters and unknown handling | `references/matching-rules.md` |
| Draft-only rules and the outreach envelope | `references/outreach-guidelines.md`, `templates/*.md` |
| Field-level truth | `schemas/*.json` |
