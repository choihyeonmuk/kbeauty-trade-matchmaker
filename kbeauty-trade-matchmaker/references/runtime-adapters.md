# Runtime Adapters

Korean gloss: 런타임 어댑터 — 동일한 core workflow를 Claude Agent Skills와 OpenAI/Codex Skills 양쪽에서 그대로 돌리기 위한 이식성 계층. 벤더별 도구 이름은 오직 이 문서에만 존재한다.

This page implements PRD 9.2 (Runtime Adapter 원칙) and the PRD 14 *Portability* requirement:
**공통 core + adapter — Codex/Claude 양쪽 재사용.**

It is the **only** file in this package that names a vendor-specific tool. `SKILL.md`,
`references/*.md` (other than this one), `scripts/*.py` and `templates/*.md` describe
**capabilities** — "the runtime's web-search tool", "the runtime's internal-data connector" — and
point here for the binding. That separation is what lets the same folder run unchanged in two
runtimes, and it is checked by the build (INV-36).

---

## 1. The one rule

> **Core logic never names a vendor tool.**
>
> Korean gloss: core 로직은 벤더 도구 이름을 절대 쓰지 않는다.

Concretely:

- `SKILL.md` says *"use the runtime's web-search capability"*, never a product name. A grep for
  `WebSearch`, `WebFetch`, `web.run`, `browser.`, `mcp__`, `claude`, `anthropic`, `openai`, `codex`,
  `gpt-`, `chatgpt` must not match anywhere in `SKILL.md` except a pointer to this file.
- `scripts/*.py` are pure and offline: no network client, no runtime API, no tool name. They take
  JSON in and give JSON out, so they behave identically wherever they run.
- `adapters/tradewith_adapter.py` is the internal-data boundary, and it too is runtime-agnostic: it
  speaks plain HTTP or plain files, never a runtime-specific connector API.
- This file maps capability → tool, per runtime. When a runtime changes, **only this file changes.**

Why it matters: a skill that hard-codes `WebSearch` silently degrades to "no candidates found" in a
runtime that spells the capability differently, and an operator cannot tell that outcome apart from
"the market genuinely has no candidates". Those are very different answers.

---

## 2. The portability table (PRD 9.2)

| Layer | Claude (Agent Skills) | Codex / ChatGPT (Skills) | Generic runtime | Owned by |
|---|---|---|---|---|
| **Core workflow** | The same `SKILL.md` | The same `SKILL.md` | The same `SKILL.md` | `SKILL.md` — identical bytes in all three |
| **Web research** | The runtime's built-in web search and page fetch (in current builds: `WebSearch`, `WebFetch`); an MCP browser/search server where one is configured | The runtime's browsing tool | Any tool that can (a) run a query and return result URLs and (b) fetch a URL's readable text | This file |
| **Internal data** (TradeWith RFQ / Seller) | `adapters/tradewith_adapter.py`, or a TradeWith MCP server / connector where one exists | `adapters/tradewith_adapter.py`, or the TradeWith API / connector | `adapters/tradewith_adapter.py` (`file` backend needs nothing at all) | `adapters/tradewith_adapter.md` |
| **CRM write** | A separate MCP server or tool — never this skill's own code beyond the adapter's review-queue writes | A separate app or tool | The adapter's `POST /research/leads`, `POST /matches`, `POST /outreach-drafts` | `adapters/tradewith_adapter.md` |
| **Email** | **Draft only.** The skill ends at `READY_FOR_REVIEW`; a human approves and an external system does the sending | **Draft only**, same boundary | **Draft only**, same boundary | `references/outreach-guidelines.md`, `templates/*.md` |
| **Scoring / normalization / validation** | `python3 scripts/*.py`, or the same scripts as MCP tools (§5.6) | `python3 scripts/*.py`, or the MCP tools (§5.6) | `python3 scripts/*.py`, or the MCP tools (§5.6) | `scripts/` — stdlib only, no runtime dependency |

Korean gloss (PRD 9.2 표 그대로): Core workflow는 공통 SKILL.md, Web research는 런타임의
web/browser/search tool, Internal data는 TradeWith API/MCP/connector, CRM write는 별도 app/tool,
Email은 Draft only — Send는 외부 승인 단계.

**The Email row is not a limitation to be engineered around.** There is no message-transport code
anywhere in this package, in any runtime, by design (PRD 11.1, 11.2, INV-10). Drafting and sending
are separate capabilities. If a runtime offers a send tool, this skill still does not call it: the
skill's completion condition is a draft plus a reviewer checklist.

---

## 3. Capability contracts

What the core workflow assumes, stated as a contract so any runtime can satisfy it.

### 3.1 Web research

| Sub-capability | What the workflow needs | Used by |
|---|---|---|
| **Search** | Take a query string, return ranked results with resolvable URLs and enough title/snippet text to decide whether to fetch | `references/buyer-discovery.md`, `references/seller-discovery.md` query expansion |
| **Fetch** | Take a URL, return the readable text of that page, and report failure honestly when the page is unreachable | Evidence verification; the `stale` / `operational_status` determination |

Boundaries that hold in **every** runtime (INV-12, PRD 11.1):

- No CAPTCHA solving, no login automation, no paywall circumvention, no `robots.txt` override, no
  rate-limit evasion. If a page needs any of those, the fact stays `"unknown"` — that is the
  correct outcome, not a failure to work around.
- `evidence.retrieval_method` is restricted to `web_search`, `page_fetch`, `sitemap`,
  `internal_api`, `manual_entry`. If your runtime's tool does something outside that list, it is
  outside this skill's scope.
- No headless-browser automation ships in the package. A runtime may have one; the skill neither
  requires nor drives it beyond plain fetching.

### 3.2 Internal data

The workflow needs to load one RFQ by id and list internal seller candidates. Both go through
`adapters/tradewith_adapter.py`, whose `file` backend needs no service at all. A runtime that
exposes a TradeWith connector may use it instead — but the **shape** it returns must still be the
shipped schemas (`schemas/rfq.schema.json`, `schemas/seller.schema.json`), because every downstream
script validates against them.

### 3.3 Scoring and validation

`python3` with the standard library, versions 3.9–3.14. No `pip` install, no `jsonschema`, no
network. If a runtime cannot execute a bundled script, the agent must say so explicitly and stop —
see §4. It must **not** compute scores in its head: the numbers are the deterministic half of the
product (PRD 14 *Determinism*), and an eyeballed score is not reproducible and not auditable.

---

## 4. Degradation — what to do when a capability is missing

> **Say so and stop. Never invent the missing half.**
>
> Korean gloss: 도구가 없으면 없다고 말하고 멈춘다. 후보를 지어내지 않는다.

This is the single most important rule on this page. The failure mode it prevents — an agent with
no web access producing a confident list of plausible-sounding distributor names — is worse than
returning nothing, because it looks exactly like a successful run and a salesperson will act on it.

| Missing capability | Correct behaviour | Never |
|---|---|---|
| **No web search / fetch** | State plainly that no web research tool is available in this runtime, so buyer/seller discovery cannot run. Offer the paths that still work: score a list of candidates the operator supplies, run RFQ matching against internal sellers from the adapter, or draft outreach from already-evidenced records. Return **zero** discovered candidates. | Produce company names, domains, MOQs, certifications or contact URLs from memory. Cite a URL that was not fetched in this run. |
| **Fetch works, search does not** | Ask the operator for seed URLs or a domain list, then verify those. Say that coverage is seed-limited. | Guess additional domains by pattern. |
| **Search works, fetch does not** | Report candidates as **leads only**, with every material claim `"unknown"` and a note that no page could be read. Do not expect low scores: with no evidenced material claim the scorers apply the DISC-06 evidence bar and move each record to `excluded[]` as `no evidenced material claim (no source could be opened)` — or `(evidence covers no material claim)` when snippet-only items were recorded — so the run ranks zero candidates. That is the honest result. | Treat a search snippet as evidence for MOQ, certifications, export markets or buyer intent. |
| **No internal-data access** (no adapter data dir, no API) | Say that TradeWith internal data is unavailable; run discovery and qualification only. RFQ matching requires an RFQ. | Fabricate an RFQ, or assume a buyer's requirements. |
| **Cannot execute `scripts/*.py`** | Say that deterministic scoring is unavailable in this runtime. Produce evidence-backed records **without** scores, and mark the output clearly as unscored. | Estimate a `qualification_score` or a `match_score` by hand. |
| **A host refuses this retrieval method** (HTTP 403 / 429 / a bot filter / a TLS handshake or certificate-name failure), while `robots.txt` permits the path | The host answered — this is a **transport refusal, not an absent page and not stale content**. Retrying the *same* URL with a different non-bypassing method (a plain page fetch instead of the reader tool, or `http://` when the certificate does not cover the host) is permitted and is **not** an access-control bypass. Record which method finally read the page in `evidence.retrieval_method` and name the failure in `notes[]`. If no permitted method works, the facts stay `"unknown"` and the candidate carries the reason. | Set `stale: true` or `operational_status: "unreachable"` for a transport refusal — `stale` is a claim about CONTENT age and `unreachable` means DNS itself failed. Solve a CAPTCHA, sign in, spoof an identity to defeat a bot filter, or ignore `robots.txt`. |
| **Some pages unreachable mid-run** | Continue. Mark those records `partial`, append the reason to `notes[]`, keep the affected fields `"unknown"`, and still return the run (INV-35). | Drop the candidate silently, or fill the gap with an assumption. |

The general principle, in one line: **a smaller honest answer always beats a larger invented one**
(PRD DISC-06 — evidence quality outranks result count; `references/evidence-policy.md`).

---

## 5. Installation

The package is a plain folder — `SKILL.md` plus `references/`, `schemas/`, `scripts/`, `templates/`,
`adapters/`, `tests/`. Every runtime below loads a skill from a folder like this; only the **location**
and the invocation syntax differ. No file in the package changes between runtimes.

> Both runtimes' details change faster than this document does. Every path, limit and field name in
> §5 and §6 was re-verified against the official documentation on **2026-09-13**; the URL that fixes
> each one is in §6. **Re-verify before you install**, and treat anything below that disagrees with
> current docs as out of date.

### 5.0 What the portable frontmatter is

The Agent Skills format is an **open standard** (§6), and its normative frontmatter is six fields:
`name` and `description` (both required), plus the optional `license`, `compatibility`, `metadata`
and the explicitly *experimental* `allowed-tools`. Claude Code additionally accepts host-only keys of
its own (no count is published); its documentation states that **outside Claude Code — claude.ai and
the Skills API — only the six spec fields are allowed**, so a single Claude-Code-only key silently
makes the folder unuploadable elsewhere.

This package therefore ships the **maximally portable shape: exactly `name` and `description`, and
nothing else.**

- There is **no `version` key**, in this package or in the standard. `skill_version`,
  `schema_version` and `score_version` are published in the **body** of `SKILL.md` (line 13), not in
  the frontmatter. The standard's only sanctioned frontmatter home for a version string is
  `metadata.version`; adding a top-level `version` would break upload to the hosted surfaces and
  would fail this package's own `tests/run_tests.py` frontmatter check.
- Adding **any** optional key is a coordinated three-file change (`SKILL.md`, BUILD-CONTRACT §2.2
  row 1, `tests/run_tests.py`), never a drop-in edit. The current two-key shape has no conformance
  defect, so the cheapest correct action is to add nothing.
- Hard limits the standard states: `name` 1–64 chars, lowercase `[a-z0-9]` and hyphens only, no
  leading/trailing hyphen, no `--`, and it **must match the parent directory name**;
  `description` 1–1024 characters, non-empty, third person, saying both *what* and *when*;
  `compatibility` ≤ 500 chars when present. Anthropic's product docs add two rules the open spec does
  not state but which gate upload to the hosted surfaces: `name` may not contain XML tags and may not
  contain the reserved words "anthropic" or "claude"; `description` may not contain XML tags either.
- The number **1,536** that circulates for `description` is not the spec limit: it is Claude Code's
  display cap on `description` + `when_to_use` *combined, in skill listings*. Portable skills hold to
  1,024. This package's description is 484 characters.
- Body size: keep `SKILL.md` under **500 lines** and under roughly **5,000 tokens**. This package is
  205 lines; at ~17 KB of dense CJK-mixed tables its token count is near the soft ceiling, so
  anything added to `SKILL.md` should displace something rather than accumulate.

### 5.1 Claude Code / claude.ai (Agent Skills)

Copy — or symlink — the whole folder, keeping its name:

```bash
# Available in every project (personal skill)
mkdir -p ~/.claude/skills
cp -R kbeauty-trade-matchmaker ~/.claude/skills/

# Or scoped to one project, checked in with the repo
mkdir -p <project>/.claude/skills
cp -R kbeauty-trade-matchmaker <project>/.claude/skills/

# Either scope, via the bundled installer (symlink by default)
sh install.sh                        # ~/.claude/skills/
sh install.sh --project <project>    # <project>/.claude/skills/
```

`install.sh` supports the two locations above. **The runtime searches more than two**, in this
precedence order:

| # | Scope | Location | Notes |
|---|---|---|---|
| 1 | Enterprise | `.claude/skills/<name>/SKILL.md` inside the managed-settings directory | Org-wide, highest precedence — an enterprise skill of the same name silently wins over yours |
| 2 | Personal | `~/.claude/skills/<name>/SKILL.md` | This machine, all projects. Not cloud/Cowork sessions |
| 3 | Project | `<project>/.claude/skills/<name>/SKILL.md` | Sessions in that repository; travels with the repo |
| 4 | Nested | `<subdir>/.claude/skills/<name>/SKILL.md` | Invoked as `/subdir:skill-name`; loads for work at or below `<subdir>` |
| 5 | Additional directory | `.claude/skills/<name>/SKILL.md` inside an `--add-dir` directory | That session only |
| 6 | Plugin | `<plugin>/skills/<name>/SKILL.md` | Invoked as `/plugin-name:skill-name` |
| 7 | Account sync | Skills enabled in claude.ai settings | Synced into cloud/Cowork sessions at session start |

- **Skill folders may be symlinks.** The runtime reads through to the target, and loads the skill once
  even when several locations point at the same target. That is what makes `install.sh`'s default
  symlink mode safe: edit the source tree, and every install sees the change.
- The folder name must equal the frontmatter `name`: `kbeauty-trade-matchmaker`. The folder name
  `synced` (any case) is reserved by the runtime and must not be used.
- A project-scoped copy takes precedence for work inside that project and travels with the repo,
  which is usually what a team wants; a personal copy is right for an individual operator.
- Progressive disclosure is the reason this package is split the way it is: `SKILL.md` stays small
  and the agent opens `references/*.md`, `schemas/*.json` or `adapters/tradewith_adapter.md` only
  when the task needs them.
- Nothing needs to be allow-listed inside `SKILL.md` — it carries exactly `name` and `description`
  and no runtime-specific keys, precisely so the same file parses in every runtime (§5.0).
- **Skills do not sync across surfaces.** Claude Code (filesystem), claude.ai (zip upload under
  Customize → Skills) and the Skills API (`/v1/skills`, referenced by `skill_id`, requires the code
  execution tool) are three separate uploads of the same folder.
- Runtime environment differs by surface: Claude Code has full network access and local package
  installs; the API surface has **no** network access and no runtime package installation; on
  claude.ai network access varies by user/admin setting. This package needs no network for scoring
  (`scripts/` are stdlib-only and offline) and needs search/fetch only for the discovery modes, so on
  a no-network surface §4's "No web search / fetch" row is the governing path.

### 5.2 Codex / ChatGPT (Skills)

Codex reads the **same unmodified folder**, but it does not look in `.claude/skills`. Its skill roots,
all named `.agents/skills`:

| Scope | Location | Install command |
|---|---|---|
| User | `~/.agents/skills/<name>/` | `mkdir -p ~/.agents/skills && cp -R kbeauty-trade-matchmaker ~/.agents/skills/` |
| Repository | `<repo>/.agents/skills/<name>/` | `mkdir -p <repo>/.agents/skills && cp -R kbeauty-trade-matchmaker <repo>/.agents/skills/` |
| Admin | `/etc/codex/skills/<name>/` | machine-wide; needs the usual privileges for `/etc` |
| System | bundled with the runtime (`CODEX_HOME/skills/.system`) | not a user install location |

Codex scans `.agents/skills` in **every directory from the working directory up to the repository
root**, so `$CWD/.agents/skills`, `$CWD/../.agents/skills` and `$REPO_ROOT/.agents/skills` all work.
The bundled installer targets these paths with `--runtime codex`:

```bash
sh install.sh --runtime codex                     # ~/.agents/skills/
sh install.sh --runtime codex --project <repo>    # <repo>/.agents/skills/
```

- **Symlinked skill folders are supported and followed**, so `install.sh`'s default symlink mode is
  valid here too.
- `$CODEX_HOME/skills` (i.e. `~/.codex/skills`) is **deprecated but still supported**: Codex's own
  skill-root resolution code keeps it with the comment *"Deprecated … kept for backward
  compatibility"* (`codex-rs/ext/skills/src/host_roots.rs`), and it appears in no current public
  document. An existing install there keeps working; no removal date is published. Third-party
  guides that tell you to unzip into `~/.codex/skills` are stale — use `~/.agents/skills` for
  anything new.
- **`AGENTS.md` is not the skills mechanism.** It is a separate, complementary Codex feature:
  always-on custom instructions for a repository, chained by directory and capped by
  `project_doc_max_bytes` (32 KiB by default). A skill is loaded **on demand**, matched from its
  `name`/`description`; an `AGENTS.md` is read **every session**. Installing this package neither
  requires an `AGENTS.md` entry nor is replaced by one, and nothing in this package writes to one.
- Two skills sharing a `name` are **not** merged; both appear in the selector.
- The parser Codex actually runs reads `name`, `description` and `metadata.short-description`, is
  more lenient than the standard (it does not enforce the name regex or the directory-name match, and
  it applies no `description` maximum), and silently **ignores** unrecognised frontmatter keys. Author
  to the standard (§5.0), not to the lenient implementation.
- `SKILL.md`'s `description` is what decides implicit invocation, so keep its trigger words intact
  (K-Beauty buyer discovery, seller sourcing, RFQ matching, outreach preparation, and the Korean
  phrasings).
- The bundled `scripts/` are ordinary Python invoked as `python3 scripts/<name>.py`; they need no
  runtime-specific wrapper, and they run through the runtime's normal shell tool, so they are subject
  to its approval and sandbox settings.
- Web research uses the runtime's own browsing tool, under exactly the boundaries of §3.1.

**Invoking and refreshing on Codex.** Explicit invocation is `$kbeauty-trade-matchmaker`, or the
`/skills` command in the CLI and the IDE extension; in ChatGPT it is `@`. Implicit invocation fires
off the `description`. Codex detects skill changes automatically — restart it if an edit does not
appear. To disable the skill without deleting it, add to `~/.codex/config.toml` and restart:

```toml
[[skills.config]]
path = "/path/to/kbeauty-trade-matchmaker/SKILL.md"
enabled = false
```

**Which ChatGPT surfaces see a bare folder.** A standalone skill folder is available in the ChatGPT
**desktop app**, **Codex CLI** and the **IDE extension**. To reach Chat and Work on ChatGPT **web and
mobile**, the skill must be packaged as a **plugin** that bundles it. Each release ships that plugin
as the ZIP asset described in §5.5.

> **Uncertain — a human should confirm (checked 2026-09-13).** The ChatGPT *workspace* upload and
> enable procedure could not be verified: <https://help.openai.com/en/articles/20001066> ("Skills in
> ChatGPT") and <https://openai.com/academy/skills/> both return **HTTP 403** to automated fetch, and
> the first is known to exist only from an outbound link on
> <https://learn.chatgpt.com/docs/enterprise/skills>. Also unverified: whether ChatGPT (as opposed to
> Codex CLI) can execute this package's bundled `python3` scripts at all — if it cannot, §4's
> "Cannot execute `scripts/*.py`" row governs; the runtime's default sandbox/approval posture for
> running them and for writing the intermediate `tmp.*.json` files; and whether any file-count or
> package-size limit applies (none is documented for a local filesystem skill; this package is
> ~55 files / ~2.0 MB). Re-checked 2026-09-19: ordinary Chat mode is still not documented to run
> bundled scripts; Work mode runs the Codex harness with shell execution (§5.5 b, e).

### 5.3 Any other runtime

The package needs only: a filesystem, `python3` (3.9–3.14, stdlib only), and — for discovery — some
way to search and fetch pages. Bind those to §3's capability contracts, record the binding in this
file, and everything else works unchanged. The open standard (§6) lists some forty further clients.

### 5.4 Verifying an install

Two different things can be broken, and the first three commands only test one of them.

**a. The bundled code runs.**

```bash
python3 scripts/validate_output.py --version          # scripts are runnable
python3 adapters/tradewith_adapter.py --version       # adapter is runnable, no credentials needed
python3 tests/run_tests.py                            # golden fixtures pass
```

If the first two print a version line and the third exits `0`, the code is sound in that runtime.

**b. The runtime can actually see the skill.** All three commands above pass in a runtime that never
loaded `SKILL.md` at all — the silent failure §1 exists to prevent. Check it explicitly:

| Runtime | Check | If it is missing |
|---|---|---|
| Claude Code | The skill is listed among available skills, and `/kbeauty-trade-matchmaker` resolves | Confirm the folder sits directly under one of §5.1's `.claude/skills` roots, that the directory is named `kbeauty-trade-matchmaker` (it must equal frontmatter `name`), and that `SKILL.md`'s **first line is exactly `---`** — if the opening `---` is not line 1, the whole file is treated as body text and the skill does not load. Check no higher-precedence scope holds a skill of the same name |
| Codex CLI / IDE | `/skills` lists `kbeauty-trade-matchmaker`, or typing `$kbeauty` completes it | Same three checks against §5.2's `.agents/skills` roots, then restart Codex. With many skills installed Codex may shorten descriptions or omit skills from the startup list with a warning: that list is capped at 2% of the context window, or 8,000 characters when the window is unknown |

### 5.5 Plugin packaging

Korean gloss: 같은 패키지 폴더를 복제 없이 세 가지 경로로 배포한다 — Claude Code 플러그인(저장소 마켓플레이스), ChatGPT/Codex 플러그인 ZIP(스킬 전용), claude.ai 스킬 ZIP. 한 런타임에는 한 가지 방식만 설치한다.

Every path below ships **this same folder**, byte for byte. None of them adds a file to the package,
and none keeps a second copy in git. Checked against the official documentation on **2026-09-19**
(sources in §6).

**a. The three ship paths.**

| Path | Built from | Who uses it |
|---|---|---|
| **Claude Code plugin** | `.claude-plugin/marketplace.json` at the repository root. Its one entry has `"source": "./kbeauty-trade-matchmaker"` (this folder), `"strict": false` (the entry is the whole definition, so the package needs no `plugin.json`) and `"skills": ["./"]` (the package root `SKILL.md` is the skill) | `/plugin marketplace add choihyeonmuk/kbeauty-trade-matchmaker`, then `/plugin install kbeauty-trade-matchmaker@kbeauty-trade-matchmaker`. The skill is `/kbeauty-trade-matchmaker:kbeauty-trade-matchmaker`, or fires implicitly |
| **ChatGPT / Codex plugin** | Release asset `kbeauty-trade-matchmaker-plugin.zip`: the portable layout, `plugin.json` (from `packaging/openai/plugin.json`) plus `skills/kbeauty-trade-matchmaker/` (this folder). Skills only | ChatGPT desktop and Codex CLI through a personal marketplace; ChatGPT web and mobile once a workspace admin publishes it |
| **claude.ai skill ZIP** | Release asset `kbeauty-trade-matchmaker.zip`: one top folder `kbeauty-trade-matchmaker/`, the same layout as the v0.2.0 asset | Upload as in §5.1 (zip upload under Settings → Features) |

`tools/build_release.py` (repository root, not shipped) builds both ZIPs from the **HEAD commit**:
it lists files with `git ls-tree` and reads their bytes with `git cat-file`, never from the working
tree, so gitignored local state such as a filled review sheet, and any edit not yet committed, can
never reach a public asset. It refuses (exit 1) when the package or either manifest has an
uncommitted change or an untracked, unignored file, when the package holds a symlink or a plugin
manifest or component directory, or when the two manifests' `version` disagrees with
`skill_version`; a manifest missing from HEAD or not valid JSON is exit 2. Both ZIPs are built and
both targets checked before either file is replaced. Entry order, timestamps (1980-01-01, or
`--as-of`) and permissions (`install.sh` 0755, everything else 0644) are fixed, so one commit builds
identical bytes on one toolchain. Deflate output can differ between zlib builds, so compare hashes
on the same machine.

**b. What runs where.**

| Surface | Install | What runs |
|---|---|---|
| Claude Code | Plugin (recommended) or `install.sh` | Skill and scripts; the bundled MCP server (declared by the plugin, see d; by hand after `install.sh`, see §5.6) |
| claude.ai web / Desktop chat | Skill ZIP upload | Scripts when code execution is on (checked 2026-09-14); no MCP server. Official pages disagree on whether web chat installs plugins, so this page does not document that path |
| Claude Cowork | Not tested with this repository | Not advertised |
| Codex CLI | `install.sh --runtime codex`, or the plugin ZIP through a personal marketplace | Scripts run; the MCP server needs a manual `codex mcp add` (§5.6 b) |
| Codex IDE extension | `install.sh` only (the extension does not support plugins) | Scripts run |
| ChatGPT desktop | Standalone folder (§5.2) or the plugin ZIP | Work mode is inferred to run the scripts |
| ChatGPT web / mobile | Only a published plugin (workspace, or the public directory after OpenAI review) | Work mode runs the Codex harness with shell execution, so the scripts probably run (inferred; the Python version is unpublished). Ordinary Chat mode is **not documented** to run bundled scripts: apply (e) |

**c. One install per runtime.** Installing the plugin **and** an `install.sh` copy in the same
runtime loads two skills (`kbeauty-trade-matchmaker` and
`kbeauty-trade-matchmaker:kbeauty-trade-matchmaker`) that both fire on the same requests. Pick one.

**d. The MCP server.** Only the Claude Code plugin declares it, inline in the marketplace entry,
and only while the package ships `scripts/mcp_server.py` (the `plugins` test phase enforces the
pairing both ways, so the plugin never starts a server that is not there):
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/mcp_server.py --root ${CLAUDE_PROJECT_DIR}` (both
placeholders are substituted in a stdio server's `args`). It starts when the plugin is enabled and
needs `python3` on `PATH`. The server refuses a project directory that is `/`, your home directory
or a parent of it, so start Claude Code inside a project folder; launched from `~`, the kbtm server
shows as failed. The OpenAI ZIP carries **no** `mcp.json`: a plugin that declares an MCP
server is marked *Desktop only* and disappears from web and mobile, which is the one thing the ZIP
exists to reach. Register the server by hand where a runtime needs it.

**e. When scripts cannot run.** In ChatGPT, run analyses in **Work** mode. If a `python3
scripts/…` call cannot execute (ordinary Chat mode, or any sandbox without Python), §4's row
"Cannot execute `scripts/*.py`" governs: return evidence-backed records marked **unscored** and say
why. Never estimate a score by hand.

**f. Codex marketplace limits.** Codex also reads a repository's `.claude-plugin/marketplace.json`,
but this repository's plugin has no `skills/` directory, so `codex plugin marketplace add
choihyeonmuk/kbeauty-trade-matchmaker` may list a broken or empty entry, and a ChatGPT workspace
admin who imports the repository from GitHub may pick up an inline MCP server and get a Desktop-only
plugin. Codex and ChatGPT users take the plugin ZIP instead. Unzip it into
`~/.codex/plugins/kbeauty-trade-matchmaker`, then add this entry to the `plugins` array of
`~/.agents/plugins/marketplace.json` (merge by hand if the file exists; `source.path` resolves
against the marketplace root, which is `~` for the personal file):

```json
{"name": "kbeauty-trade-matchmaker",
 "source": {"source": "local", "path": "./.codex/plugins/kbeauty-trade-matchmaker"},
 "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
 "category": "Business & Operations"}
```

Restart the ChatGPT desktop app and install from Plugins, or run `/plugins` in Codex CLI.

**g. Maintainer release checks.**

1. `python3 kbeauty-trade-matchmaker/tests/run_tests.py` exits 0 in the checkout. The `plugins`
   phase checks both manifests and the builder; an installed copy has neither and reports two
   SKIPs (the `plugins` phase and M-36).
2. `claude plugin validate .` passes at the repository root.
3. From a **fresh clone** (a working tree can carry untracked files into the plugin cache):
   `/plugin marketplace add ./`, install, and confirm the skill (and the MCP server, when declared)
   is listed (`claude plugin details kbeauty-trade-matchmaker@kbeauty-trade-matchmaker`).
4. From the same clone (`dist/` is gitignored, so a fresh clone has none and the builder creates
   no directory): `mkdir -p dist && python3 tools/build_release.py --out dist/ --as-of <release date>`.
5. Upload both ZIPs to the GitHub release.
6. Install the plugin ZIP in the ChatGPT desktop app following (f), and run one discovery.
7. Only if the owner decides to submit to the public directory: the portal's listing limits are
   checked by hand, not by the test suite, because they are vendor policy that changes without
   notice. On 2026-09-19 they were `interface.displayName` and `shortDescription` ≤ 30 characters,
   `longDescription` ≤ 4,000, `developerName` ≤ 80, `defaultPrompt` ≤ 3 × 128, one of 13 fixed
   categories, `plugin-name:skill-name` ≤ 64 characters, and an archive of ≤ 5,000 entries,
   ≤ 100 MB compressed and ≤ 512 MiB extracted. The repository has no LICENSE file, so neither
   manifest states a `license`.

### 5.6 Tool server (MCP)

Korean gloss: `scripts/mcp_server.py`는 같은 스크립트를 MCP(stdio) 도구로 노출한다. `--root` 필수, 루트 밖 경로와 덮어쓰기는 거부, 모든 호출에 `as_of` 필수, 아무것도 전송하거나 가져오지 않는다.

`scripts/mcp_server.py` is an optional, stdlib-only Model Context Protocol server over stdio. It
exposes the package's deterministic scripts as tools for a runtime that calls tools rather than a
shell. Each tool runs its script as a child process with a **subset** of that script's flags, and
for the flags it exposes a tool result is exactly what the CLI prints (the `mcp:` test phase checks
every tool against its CLI and the existing goldens, `tests/cases.md` §16). Nothing about scoring
changes. Left out on purpose, so use the CLI for them: `--config`, `--schema-dir`, `--no-validate`
(where the script has it) and the presentation flags `--pretty`, `--quiet` and `--version` on every
tool; `--min-score`, `--hard-filter` / `--no-hard-filter` and `--records-only` on
`score_buyer` / `score_seller`; `--match-run-id`, `--rerank-adjustments` and `--skill-version` on
`score_match`; `--schema-file` on `validate_output`; `--name` and `--url` on `normalize_company`.

| Tool | Script | Writes a file |
|---|---|---|
| `normalize_company` | `scripts/normalize_company.py` | only to `output_path` |
| `dedupe_companies` | `scripts/dedupe_companies.py` | only to `output_path` |
| `score_buyer` | `scripts/score_buyer.py` | only to `output_path` |
| `score_seller` | `scripts/score_seller.py` | only to `output_path` |
| `score_match` | `scripts/score_match.py` | only to `output_path` |
| `validate_output` | `scripts/validate_output.py` | never (read-only) |
| `make_review_sheet` | `scripts/make_review_sheet.py` | only to `output_path` |
| `acceptance_report` | `scripts/acceptance_report.py` | only to `output_path` |
| `diff_runs` | `scripts/diff_runs.py` | only to `output_path` |
| `stale_evidence` | `scripts/stale_evidence.py` | only to `output_path` |
| `export_leads` | `scripts/export_leads.py` | only to `output_path` |

**Rules the server enforces.**

- **`--root DIR` is required.** The server refuses to start without it, or when it is a filesystem
  root, the home directory or a parent of the home directory (exit 2). Every input and output path
  must resolve inside that one directory after `realpath`, so a `../` or a symlink cannot lead out.
  Relative paths resolve against the root.
- **`as_of` is required on every tool** and must be a real `YYYY-MM-DD` date. The server never
  supplies a date and never reads the clock.
- **The main document goes in `input` (inline JSON object) or `input_path` (a file), never both.**
  An array of records is wrapped as `{"records": [...]}`. Only one document per call can be inline;
  `diff_runs` takes `before`/`before_path` and `after`/`after_path` with at most one inline.
  `score_match` needs `input`/`input_path`, or `rfq_path` and `sellers_path` together; anything
  less is refused before the script runs. The
  other inputs (`query_path`, `rfq_path`, `sellers_path`, `rerank_input_path`,
  `rationale_input_path`, `scored_paths`, `reviews_paths`) are files, except that `score_buyer` and
  `score_seller` also take an inline `query` of at most 65,536 bytes (it travels on the script's
  command line; above that, save it and pass `query_path`). Inline JSON is re-serialized for the
  script: `NaN`/`Infinity` are refused, duplicate keys keep the last value, and a string holding a
  lone UTF-16 surrogate is refused as not valid UTF-8, as is such a path.
- **No overwrite.** `output_path` must not exist yet (a dangling symlink counts as existing), its
  directory must exist (the server creates none), it must end in `.json` or `.csv`, no part of it
  below the root may start with a dot (no `.git/`, `.claude/`, `.env*`), and it must be outside the
  skill package, which is recognised by file identity (`st_dev`, `st_ino`) so a case-changed or
  differently normalised spelling of its path is refused too. The
  server itself writes nothing; the script writes the file. A script that exits 1 after writing
  (R7.3.2) leaves the file, so a retry needs a new name. That is why every tool can honestly carry
  `destructiveHint: false`; only `validate_output` is `readOnlyHint: true`; `openWorldHint` is
  false everywhere.
- **Size cap.** A result larger than `--max-inline-bytes` (default 32,768 bytes, range
  1,024–16,777,216) is not returned; the call is refused with a pointer to `output_path`, which
  returns `{path, bytes, sha256}` instead. A full discovery or match run is usually larger than the
  default, so pass `output_path` for real runs. Claude Code caps a tool result at
  `MAX_MCP_OUTPUT_TOKENS` (25,000 tokens by default), and the result is sent both as
  `structuredContent` and as a text block, so do not raise the cap far.
- **Results.** `structuredContent` is `{tool, exit_code, document | csv | output, diagnostics,
  error?}`. A script's exit 1 or 2, a refused argument and a timeout (`--tool-timeout`, default 120
  seconds) are `isError: true`; `validate_output` exit 1 is a normal result whose report says
  `valid: false`. A protocol error (unknown tool, malformed request) is a JSON-RPC error.
- **Nothing else runs.** No tool sends, posts, fetches or opens a network connection, and the
  server runs only the scripts in the table. The internal-data adapter is not exposed. `TRADEWITH_*`
  variables are removed from a script's environment, the script runs with `python3 -I` (no
  `PYTHONPATH`, no user site-packages) and never inherits the server's stdin.

**Protocol.** Newline-delimited JSON-RPC 2.0 on stdin/stdout; diagnostics on stderr only (one line
per tool call unless `--quiet`: `kbtm-mcp: <tool> exit=<n>` for a script run, `kbtm-mcp: <tool>
refused` for an argument refusal, `kbtm-mcp: <tool> timeout` or `… could not start: <reason>`
otherwise; best effort, so a closed
stderr is harmless); end of input exits 0. No single line can end the process: nesting deeper than the
interpreter allows is `-32700`, a reply that would echo a lone surrogate is written with `\u`
escapes, and a response sent to the server (any id, even `null`) is never answered. `initialize`
echoes a requested `2025-11-25`, `2025-06-18`, `2025-03-26` or `2024-11-05` and answers any other
version with `2025-11-25`; `ping` answers `{}`; notifications are never answered. A request whose
`params._meta` carries `io.modelcontextprotocol/protocolVersion` is served on the stateless
`2026-07-28` path: `server/discover`, `resultType`, `ttlMs` and `cacheScope` are added,
`-32022` answers an unsupported version and `-32602` a `_meta` without client capabilities. Claude
Code talks to stdio servers the legacy way unless `MCP_PROTOCOL_NEGOTIATION=auto`; which revision
Codex and Cursor send over stdio is not documented (inferred legacy), so both paths are kept.

**a. Claude Code.** The plugin declares the server (§5.5 d); it fails to start when Claude Code
was launched in `/`, the home directory or a parent of it, so open a project folder. By hand,
point at the installed package folder and pass the project as the root:

```bash
claude mcp add --transport stdio kbtm -- python3 /abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py --root /abs/path/to/project
```

or in a project `.mcp.json` (approved on first use in interactive sessions):

```json
{"mcpServers": {"kbtm": {"command": "python3",
  "args": ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "/abs/path/to/project"]}}}
```

**b. Codex CLI.** In `~/.codex/config.toml` (or a trusted project's `.codex/config.toml`); keep
`tool_timeout_sec` above the server's `--tool-timeout`:

```toml
[mcp_servers.kbtm]
command = "python3"
args = ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "/abs/path/to/project"]
tool_timeout_sec = 180
```

or `codex mcp add kbtm -- python3 /abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py --root /abs/path/to/project`.

**c. Cursor.** In `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

```json
{"mcpServers": {"kbtm": {"type": "stdio", "command": "python3",
  "args": ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "${workspaceFolder}"]}}}
```

Client configuration was checked on 2026-09-19 against the sources in §6. A TOCTOU window remains:
a symlink swapped in after the check and before the script opens the file could still escape the
root. That is acceptable for a local, single-user stdio server and is why the root should be a
project directory the user alone controls.

---

## 6. Sources (PRD 23)

Every row was fetched on **2026-09-13**. §5's paths, limits and field names come from these; re-verify
against the current version of each before relying on a runtime detail.

| Source | What it fixes | Link |
|---|---|---|
| **Agent Skills — Specification** (the open standard; normative and the strictest of the three Anthropic-side documents) | The six frontmatter fields and every numeric limit (`name` 1–64 + `^[a-z0-9]+(-[a-z0-9]+)*$` + directory match, `description` 1–1024, `compatibility` ≤ 500, `metadata` string→string map, `allowed-tools` experimental); folder layout; progressive disclosure; the < 500-line / < 5,000-token body guidance | https://agentskills.io/specification |
| Agent Skills — writing scripts for agents | Script contract the bundled `scripts/` already meet: no interactive prompts, `--help`, structured stdout with diagnostics on stderr, documented exit codes, idempotency, `--dry-run`, predictable output size | https://agentskills.io/skill-creation/using-scripts |
| Anthropic — Agent Skills overview | Surfaces (claude.ai, Skills API `/v1/skills`, Claude Code and the cloud platforms), per-surface network/package-install differences, no cross-surface sync, and the two extra `name`/`description` rules (no XML tags; no reserved words "anthropic"/"claude") | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview |
| Anthropic — Agent Skills best practices | Third-person `description`, forward-slash paths only, one-level-deep references, a table of contents on reference files over 100 lines | https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices |
| Claude Code — Skills | The seven install locations of §5.1, symlink read-through, the reserved `synced` folder name, the "outside Claude Code only the six spec fields are allowed" boundary, the 1,536-character *listing* cap, and the frontmatter-must-start-on-line-1 rule | https://code.claude.com/docs/en/skills |
| OpenAI — Build skills (canonical; `https://developers.openai.com/codex/skills` 308-redirects here) | The `.agents/skills` roots of §5.2, `$skill` / `/skills` invocation, `[[skills.config]]`, the 2% / 8,000-character startup-list budget, `agents/openai.yaml`, and the desktop-vs-web surface split | https://learn.chatgpt.com/docs/build-skills |
| OpenAI — Skills in the enterprise workspace | The workspace-Skill vs filesystem-skill vs plugin distinction | https://learn.chatgpt.com/docs/enterprise/skills |
| OpenAI — `AGENTS.md` (agent configuration) | That `AGENTS.md` is always-on repository instructions, a **separate** feature from Skills, with its own `project_doc_max_bytes` 32 KiB chain limit | https://learn.chatgpt.com/docs/agent-configuration/agents-md |
| OpenAI Codex source — `codex-rs/skills/` and `codex-rs/ext/skills/` | What the runtime **actually** enforces: the lenient frontmatter parser (`parser.rs` — `name` ≤ 64 chars, non-empty `description`, no regex or directory-match check), the `.system` bundle location (`lib.rs`), and the *"Deprecated … kept for backward compatibility"* comment on `$CODEX_HOME/skills` (`host_roots.rs`) | https://github.com/openai/codex/tree/main/codex-rs/skills |
| Claude Code — Plugins reference (fetched 2026-09-19) | Plugin layout; a root `SKILL.md` as a single skill; `"skills": ["./"]` naming the plugin root; inline `mcpServers`; `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PROJECT_DIR}` substitution in a stdio server's `command`, `args`, `env`; the path-escape rule; the plugin cache | https://code.claude.com/docs/en/plugins-reference |
| Claude Code — Plugin marketplaces (fetched 2026-09-19) | `marketplace.json` fields, relative `source`, `strict` (false = the entry is the whole definition), keeping `version` in one place, reserved names, `claude plugin validate` | https://code.claude.com/docs/en/plugin-marketplaces |
| OpenAI — Build plugins (fetched 2026-09-19) | Portable root `plugin.json` with `extensions."com.openai".interface`, skills discovered from `skills/`, repo and personal marketplace files, `source.path` relative to the marketplace root, the `./.codex/plugins/<name>` personal pattern, `policy` and `category` required on each entry | https://developers.openai.com/plugins/build/plugins |
| OpenAI — Plugin submission errors (fetched 2026-09-19) | Archive limits, manifest and listing field limits, the 13 categories, "a skills-only ZIP must not include `mcp.json`" | https://developers.openai.com/plugins/deploy/submission-errors |
| Model Context Protocol — versioning, 2026-07-28 changelog, stdio transport, `server/discover`, tools (fetched 2026-09-19) | The current revision `2026-07-28`; the legacy `initialize` era (`2025-11-25` and earlier) and that a server MAY serve both; newline-delimited UTF-8 frames and stderr-only logging; `-32022` for an unsupported version; `resultType`, `ttlMs`, `cacheScope`; tool annotations and their pessimistic defaults | https://modelcontextprotocol.io/specification/versioning, https://modelcontextprotocol.io/specification/2026-07-28/changelog, https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio, https://modelcontextprotocol.io/specification/2026-07-28/server/tools |
| Model Context Protocol — 2025-11-25 lifecycle and ping (fetched 2026-09-19) | `initialize` version negotiation (echo a supported version, otherwise answer the newest supported), `notifications/initialized`, `ping` | https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle |
| Claude Code — MCP (fetched 2026-09-19) | `claude mcp add --transport stdio`, `.mcp.json`, `MAX_MCP_OUTPUT_TOKENS`, `MCP_PROTOCOL_NEGOTIATION` (stdio servers are spoken to the legacy way by default) | https://code.claude.com/docs/en/mcp |
| OpenAI Codex — MCP (fetched 2026-09-19) | `[mcp_servers.<name>]` with `command`, `args`, `env`, `cwd`, `tool_timeout_sec`; `codex mcp add` | https://learn.chatgpt.com/docs/extend/mcp?surface=cli |
| Cursor — MCP (fetched 2026-09-19) | `.cursor/mcp.json` / `~/.cursor/mcp.json`, `"type": "stdio"`, `${workspaceFolder}` interpolation in `args` | https://cursor.com/docs/context/mcp |
| OpenAI — Plugin management (fetched 2026-09-19) | "Any imported plugin that declares MCP servers … is marked Desktop only"; GitHub import and admin publishing | https://developers.openai.com/codex/enterprise/plugin-management |
| OpenAI — Plugins and Build skills (fetched 2026-09-19) | Plugins reach Chat and Work on web, desktop and mobile; standalone skills reach only desktop, Codex CLI and the IDE extension; the IDE extension does not support plugins | https://learn.chatgpt.com/docs/plugins, https://learn.chatgpt.com/docs/build-skills |
| Anthropic Engineering — "Equipping agents for the real world with Agent Skills" | Background on the design and on the 2025-12-18 release of the format as an open standard. **Not** the normative source; the constraints now live at agentskills.io | https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills |

**Stale and blocked citations (checked 2026-09-13), kept because PRD 23 names them.**

| Cited in PRD 23 | State on 2026-09-13 | Use instead |
|---|---|---|
| `https://docs.claude.com/en/docs/agents-and-tools/agent-skills` | HTTP 302 redirect | `https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview` |
| `https://docs.claude.com/en/docs/claude-code/skills` | HTTP 301 redirect | `https://code.claude.com/docs/en/skills` |
| `https://openai.com/academy/skills/` | **HTTP 403** to automated fetch — content unverified | `https://learn.chatgpt.com/docs/build-skills` |
| `https://help.openai.com/en/articles/20001066` ("Skills in ChatGPT") | **HTTP 403** to automated fetch — existence confirmed only via an outbound link from `learn.chatgpt.com/docs/enterprise/skills`; content unverified | `https://learn.chatgpt.com/docs/enterprise/skills` |

**Open questions a human should close (checked 2026-09-13, all unresolved):**

- **Unknown-key handling is unspecified.** The standard lists the six recognised fields but states no
  rule for what a runtime must do with an unrecognised key — ignore, warn or reject. Codex's parser
  demonstrably ignores them; Claude Code documents only that hosted surfaces accept the six. A
  reference validator exists (`skills-ref validate ./my-skill`, https://github.com/agentskills/agentskills)
  and was **not** run here. This is why §5.0 refuses to add optional keys speculatively.
- **The standard publishes no version number or changelog**, so the revision this package was built
  against cannot be named, and it is unknown whether `compatibility` was in the original release.
- **Character vs UTF-8-byte counting for `description`** is stated nowhere. This package's
  description is 484 characters / 543 bytes, far under 1,024 either way, but a CJK-heavy skill near
  the cap would need this answered.
- **Whether `name` must match the parent directory is enforced or merely specified.** The standard
  states it normatively; Claude Code makes `name` optional and defaults it to the directory name, and
  Codex enforces neither. This package satisfies the strict reading, which is the right one for a
  portable folder.
- **`SKILL.md`'s token count is an estimate, not a measurement.** The ~5,000-token figure in §5.0 is
  a bytes-per-token approximation over a CJK-mixed file, not a tokenizer run, so whether the
  standard's "< 5,000 tokens recommended" guidance is actually exceeded is unknown. The rule both
  documents state as a hard number — under 500 lines — is met at 205 lines, so do not act on the
  token estimate alone.
- **The ChatGPT workspace upload path and script execution**, per the box in §5.2.

---

## 7. Cross-references

| Topic | File |
|---|---|
| TradeWith setup, endpoint mapping, backend spec | `adapters/tradewith_adapter.md` |
| What counts as evidence, and unknown discipline | `references/evidence-policy.md` |
| Query expansion for each discovery mode | `references/buyer-discovery.md`, `references/seller-discovery.md` |
| Draft-only outreach rules | `references/outreach-guidelines.md` |
| Jurisdictional caveats before anything is sent | `references/compliance-notes.md` |
| Field-level truth | `schemas/*.json`, `references/data-contract.md` |
