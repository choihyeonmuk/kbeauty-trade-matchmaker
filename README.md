# K-Beauty Trade Matchmaker

**해외 K-Beauty Buyer와 한국 Seller를 공개 정보 기반으로 발굴·검증·점수화·매칭하고, 사람이 검토할 아웃리치 초안까지만 만드는 재사용형 Agent Skill.**

Claude Agent Skills와 OpenAI/Codex Skills 양쪽에서 **같은 폴더가 수정 없이** 동작한다.
Python은 **표준 라이브러리만** 사용하며(3.9–3.14), 설치할 의존성이 없다.

---

## ⚠️ 안전 경계 — 이 스킬은 아무것도 보내지 않는다

가장 먼저 알아야 할 사실이다. 이 패키지 어디에도 **메시지를 전송하는 코드가 없다.**

- **Draft only.** 아웃리치 작업은 항상 `READY_FOR_REVIEW` 상태에서 끝난다. 초안에는 `auto_send: false`, `manual_approval_required: true`가 붙는다.
- **승인은 사람이, 발송은 외부 시스템이.** 스킬이 쓸 수 있는 상태는 `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW`까지다. `APPROVED_FOR_OUTREACH` 이후는 TradeWith/CRM과 사람의 결정이며, 어댑터도 그 상태로의 전이를 **거부**한다.
- **SMTP·Gmail·SES·webhook 전송 코드 없음**, 개인 이메일/전화번호 추론·대량 수집 없음(회사 단위 채널만), CAPTCHA·로그인·robots/ToS 우회 없음.
- **없는 사실은 만들지 않는다.** MOQ, 인증, 수출 가능 국가, 독점권, 생산능력은 근거가 있을 때만 기재하고 그 외에는 `"unknown"`으로 남는다. "바이어가 기다리고 있다" 같은 근거 없는 긴박감 문구는 템플릿 수준에서 금지된다.
- **법률 판단을 하지 않는다.** `references/compliance-notes.md`는 "확인이 필요하다"를 표시할 뿐, "보내도 된다"를 결론짓지 않는다.

이건 기능 부족이 아니라 설계다. *초안 작성*과 *발송*은 서로 다른 권한이고, 이 패키지에는 앞의 것만 들어 있다.

---

## 1. 무엇을 해주는가

TradeWith 운영자가 하루 종일 Google·LinkedIn·박람회 디렉터리를 뒤지던 작업을, **근거 URL이 달린 검증 가능한 후보 리스트와 매칭 제안**으로 바꾼다.

1. **발굴** — 국가/카테고리/OEM·MOQ·인증 조건으로 Buyer·Seller 후보를 공개 웹에서 찾는다.
2. **검증** — 회사 공식 페이지 등 신뢰 출처에서 각 주장(claim)을 확인하고 출처 URL·관찰 시각과 함께 저장한다.
3. **정규화** — 도메인·회사명을 정규화해 중복을 병합하고, Buyer 요구조건과 Seller 역량을 하나의 스키마에 올린다.
4. **점수화** — 결정론적 Python 스크립트로 Buyer/Seller 적격 점수를 계산한다. 같은 입력 + 같은 `--as-of` = 같은 출력.
5. **매칭** — RFQ를 기준으로 하드 필터 → 가중 점수 → 의미 기반 재정렬 → unknown 처리 순으로 Top N과 **제외 사유**를 함께 낸다.
6. **초안** — 검증된 사실만 써서 개인화 아웃리치 초안을 만들고 거기서 멈춘다.

---

## 2. 4가지 모드

PRD §5의 대표 시나리오 그대로다. 아래 호출 형태는 운영자가 에이전트에게 말하는 방식이며, 런타임이 슬래시 명령을 지원하지 않으면 같은 내용을 자연어로 말해도 된다.

### 2.1 Buyer Discovery — 해외 바이어 발굴

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

UAE에서 K-Beauty를 취급하는 Distributor/Importer/Wholesaler를 찾아 회사 유형·취급 브랜드·Wholesale 여부·파트너 모집 신호·공식 연락 채널·근거 URL을 정규화해 출력한다.

### 2.2 Seller Discovery — 한국 셀러 발굴

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

한국 Seller/Manufacturer 후보를 찾아 OEM/ODM 여부, MOQ, 인증, 주요 제품, 해외 수출·파트너십 신호를 검증한다.

### 2.3 RFQ Matching — 구매요청 ↔ 셀러 매칭

```
/kbeauty-match rfq="#134" top=10
```

RFQ #134의 제품·MOQ·목적국·인증·private-label 요구를 기준으로 셀러를 필터링하고 정량/정성 점수를 결합해 Top 10과 **제외 이유**를 반환한다. 적격 셀러가 없으면 "없음"을 근거와 함께 답하는 것도 정상 출력이다.

### 2.4 Outreach Draft — 아웃리치 초안

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

검증된 사실만으로 제목/본문/개인화 포인트를 작성한다. 근거가 약하면 일반화하거나 "확인 필요"로 표시한다. **발송하지 않는다.**

---

## 3. 패키지 구조

```
kbeauty-trade-matchmaker/
├── SKILL.md                      # 런타임 진입점: frontmatter(name/description) + 4 모드 + workflow
├── install.sh                    # 두 런타임의 skills 디렉터리로 설치 (symlink 기본, --copy 가능)
├── adapters/
│   ├── tradewith_adapter.py      # TradeWith 내부 데이터 경계. file/http 두 백엔드, 자격증명 없이 import 가능
│   └── tradewith_adapter.md      # 어댑터 설정·호출법과 "하지 않는 일"
├── references/                   # progressive disclosure — 필요할 때만 열리는 참조 문서
│   ├── buyer-discovery.md        # 바이어 쿼리 확장, 국가별 검색 패턴, 중단 조건
│   ├── seller-discovery.md       # 한국 셀러 쿼리 확장, OEM/ODM·MOQ 페이지 패턴, 협회/박람회 디렉터리
│   ├── qualification-rubric.md   # 점수 차원의 서술형 설명 (숫자는 SCORING-CONTRACT가 진실)
│   ├── matching-rules.md         # 하드 필터 → 가중 점수 → 재정렬 → unknown 처리, HF-00..HF-08 표
│   ├── calibration-notes.md      # 두 번의 실측 트라이얼이 무엇을 재고 무엇을 바꿨는가, 무엇이 미검증인가
│   ├── evidence-policy.md        # fact vs inference vs unknown, 출처 tier, observed_at 규율, 충돌 처리
│   ├── outreach-guidelines.md    # draft-only 규칙, 근거 기반 개인화, CTA 정책, 금지 표현, 리뷰 체크리스트
│   ├── compliance-notes.md       # 관할별 다이렉트 마케팅 주의, 데이터 최소화, robots/ToS 경계
│   ├── data-contract.md          # 스키마·정규화 계약의 동봉 요약본 (docs/ 없이도 동작)
│   ├── output-format.md          # 출력 렌더링 계약 10.1–10.5 + 한국어 라벨 맵 (동봉본)
│   └── runtime-adapters.md       # 이식성 계층 — 벤더 도구 이름이 등장하는 유일한 파일
├── schemas/                      # 데이터 계약 (JSON Schema 부분집합, 자체 validator로 검증)
│   ├── buyer.schema.json         #   raw / scored 두 프로필
│   ├── seller.schema.json
│   ├── rfq.schema.json
│   ├── evidence.schema.json      #   출처 하나 + 주장 하나의 최소 단위
│   ├── match-result.schema.json  #   한 번의 매칭 실행 전체
│   ├── discovery-result.schema.json  # 한 번의 발굴 실행 전체 (summary/records/excluded/partial/notes)
│   └── scoring.config.json       #   모든 가중치·임계값·페널티가 사는 단일 파일
├── scripts/                      # 표준 라이브러리만. 네트워크 없음, 자격증명 없음
│   ├── _common.py                #   설정 로딩, 반올림, 정규화, tri-state 헬퍼, 의존성 없는 스키마 검증기
│   ├── normalize_company.py      #   canonical_domain / normalized_name / alias 도메인 산출
│   ├── dedupe_companies.py       #   중복 회사 병합, merged_from / alias_domains / conflicts 생성
│   ├── score_buyer.py            #   바이어 차원 점수 · 적격 점수 · confidence · missing
│   ├── score_seller.py           #   셀러 동일 (쿼리 표면 기준)
│   ├── score_match.py            #   RFQ → 셀러 파이프라인, match-result 문서 생성
│   └── validate_output.py        #   스키마 + 계약 불변식 검증 (검증자의 진입점)
├── templates/
│   ├── buyer_outreach.md         # 바이어용 초안 템플릿 ({{token}} 자리표시자)
│   ├── seller_outreach.md        # 셀러용 초안 템플릿 (RFQ 있는 경우 / 없는 경우)
│   └── legal_notices.md          # 관할×채널별 고지 블록 ({{country_alpha2}}.{{channel_type}})
└── tests/
    ├── cases.md                  # PRD T01–T10 + negative/edge 케이스 명세
    ├── run_tests.py              # 표준 라이브러리 러너. 인자 없이 실행, exit 0/1
    └── fixtures/                 # golden 입력과 expected/ 출력 (실제 개인정보 없음)
```

저장소 루트에는 이 README와 함께 `docs/`가 있다. 공개되는 것은 구현 계약 두 건 — `docs/BUILD-CONTRACT.md`와 `docs/SCORING-CONTRACT.md` — 이며, 두 계약이 인용하는 제품 정의(PRD)와 실측 트라이얼 원자료는 내부 문서라 이 저장소에 포함되지 않는다. 계약이 PRD 조항 번호를 인용하는 곳은 그 조항의 요구사항을 계약 본문이 이미 다시 적어 두었으므로, PRD 없이도 읽을 수 있다. **`docs/`는 배포 대상이 아니다** — 설치되는 것은 `kbeauty-trade-matchmaker/` 폴더뿐이고, 런타임이 필요로 하는 계약 내용은 `references/data-contract.md`(스키마·정규화)와 `references/output-format.md`(출력 렌더링)에 동봉되어 있다. 그래서 패키지 안의 어떤 파일도 런타임 에이전트에게 `docs/…` 경로를 열라고 지시하지 않는다.

---

## 4. 점수는 어떻게 매겨지는가

점수는 LLM의 인상이 아니라 **결정론적 스크립트**가 낸다. Buyer는 6개 차원(`kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10 — 합 100), Seller도 6개 차원(`product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10)으로 채점되고, 각 차원은 criterion 단위 가점을 합산한 뒤 적용 가능한 만점으로 0–100 정규화한다. 매칭은 네 단계를 순서대로 밟는다 — 하드 필터 `HF-01..HF-08`과 렌더링 게이트 `HF-00`(실패해도 단락하지 않고 **모든** 제외 사유를 수집한다) → 가중 점수(`product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10) → 경계가 정해진 의미 기반 재정렬 → unknown 처리. 여섯 개의 매칭 성분은 **셀러의 여섯 차원 바로 그것**이며, RFQ를 같은 쿼리 표면에 투영해 다시 채점한 값이다. 한 셀러가 두 개의 다른 루브릭으로 평가되는 일은 없다. 핵심 규칙 세 가지: **unknown은 0이 아니다** — 모르는 값은 해당 criterion 만점의 30%를 받고(`neutral_base` 50 × `penalty_factor` 0.6), unknown이라는 이유만으로 후보를 탈락시키는 것은 금지이며, 페널티를 피하려고 값을 추정하는 것도 금지다. **시계를 읽지 않는다** — 모든 시간 계산은 `--as-of`(기본 `2026-09-12`)를 쓰므로 같은 입력은 언제 돌려도 같은 숫자를 낸다. **confidence는 품질 점수가 아니다** — `evidence_quality / 100 × coverage_factor × stale × conflict`로 계산되는 "이 레코드를 얼마나 믿을 수 있나"이며, `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW`로 표시되고 적격 점수를 절대 읽지 않는다. 기본 합격선은 70점 고정이고(`thresholds.mode = "fixed"`), percentile 모드도 구현되어 있다(§9 항목 3). 모든 튜닝 숫자는 `schemas/scoring.config.json` 한 곳에만 있다.

## 5. 근거(evidence)는 어떻게 다뤄지는가

이 스킬의 출력 단위는 "회사"가 아니라 **근거가 붙은 주장**이다. evidence 항목 하나는 주장 하나(`claim`)와 그 출처 URL, 출처 tier, 콘텐츠 날짜(`source_date`), 우리가 본 시각(`observed_at`), 사실인지 추론인지(fact/inference), 짧은 인용으로 이루어진다. 출처는 5단계로 등급이 매겨진다 — tier 1 회사 공식 사이트(100점), tier 2 박람회·협회·정부/무역기관 공식 디렉터리와 TradeWith 내부 레코드(82), tier 3 공식 LinkedIn·소셜(64), tier 4 평판 있는 제3자 디렉터리·보도자료(46), tier 5 커뮤니티·블로그(20, 보조 신호 전용). 여기에 콘텐츠 나이에 따른 배수가 곱해진다(90일 이내 1.00, 1년 이내 0.92, 2년 이내 0.80, 그 이상 0.60, 날짜 불명 0.85). 배수의 기준은 언제나 `source_date`이지 `observed_at`이 아니다 — 후자는 우리가 읽은 시각이지 내용의 나이가 아니기 때문이다. 차원 점수로 들어가는 `evidence_quality`는 `0.50 × 출처 강도 + 0.35 × 주요 주장 커버리지 + 0.15 × 교차검증`에 공식 출처 보너스·다중 출처 보너스·staleness/충돌/추론 페널티를 더한 뒤 0–100으로 clamp한 값이다. 여기서 서로 다른 두 상태를 구분해야 한다. **`unverified`** 는 근거가 있으나 주요 주장에 **공식(tier 1, `is_official`) 출처가 하나도 없는** 경우다 — 점수가 매겨지고 순위에 올라가며 출력 헤더에 ` — unverified` 표시가 붙는다. **탈락하지 않는다**(PRD 15.1). 반면 **주요 주장에 근거 항목이 아예 하나도 없는** 레코드는 `evidence_quality = 0`이고 DISC-06 근거 기준선에 따라 점수 산정 전에 `excluded[]`로 빠진다 — 이때 사유에는 "사이트를 열 수 없었다"인지 "읽었으나 주요 주장이 없었다"인지가 명시된다. 근거가 전혀 없는 회사를 순위 목록에 채워 넣는 것이야말로 DISC-06이 막으려는 실패다. 저장은 최소화 원칙을 따른다: 주장·URL·관찰 시각·짧은 인용만 남기고 페이지 전체나 불필요한 개인 프로필은 남기지 않는다. 상충하는 출처가 나오면 조용히 하나를 고르지 않고 `conflicts[]`에 기록해 사람이 보게 하며, 그 대가로 confidence가 내려간다.

---

## 6. 설치

`install.sh`는 POSIX `sh` 스크립트이며 네트워크도 sudo도 쓰지 않고, 선택한 대상 디렉터리 바깥에는 아무것도 쓰지 않는다. **먼저 `--dry-run`으로 계획을 확인하는 것을 권한다.**

```bash
cd kbeauty-trade-matchmaker

sh install.sh --dry-run              # 무엇을 할지만 출력하고 아무것도 바꾸지 않는다
sh install.sh                        # 기본: ~/.claude/skills/ 로 symlink
sh install.sh --runtime codex        # Codex: ~/.agents/skills/ 로 symlink
sh install.sh --verify               # 설치 후 번들 테스트 스위트까지 실행
```

| 옵션 | 뜻 |
|---|---|
| *(기본)* | `$HOME/.claude/skills/kbeauty-trade-matchmaker` 로 **symlink**. 원본을 고치면 즉시 반영된다 |
| `--runtime claude\|codex` | 대상 런타임의 skills 디렉터리를 고른다. `claude`(기본) → `.claude/skills/`, `codex` → `.agents/skills/`. `--codex`는 `--runtime codex`의 축약 |
| `--copy` | symlink 대신 독립 사본 설치 (`tradewith-data/`와 `__pycache__`는 사본에서 제외) |
| `--project DIR` | `$HOME` 대신 `DIR` 아래 해당 런타임의 skills 디렉터리에 설치 — 저장소와 함께 커밋되는 프로젝트 범위 |
| `--force` | 이 스크립트가 만들지 않은 기존 디렉터리를 덮어쓴다 (기본은 **거부**) |
| `--verify` | 설치 후 `tests/run_tests.py` 실행. 실패하면 exit 1 |
| `--dry-run` | 계획만 출력. 파일 시스템을 건드리지 않는다 |

재실행은 안전하다. 이미 같은 곳을 가리키는 symlink면 "Nothing to do"로 끝나고, 이 스크립트가 만든 사본이면 `--force` 없이 갱신된다. 반대로 **내가 만들지 않은 디렉터리는 `--force` 없이는 절대 지우지 않는다.** exit 코드는 `0` 성공, `1` 설치 거부 또는 검증 실패, `2` 사용법 오류다.

두 런타임 모두 **symlink된 스킬 폴더를 따라간다**(target까지 읽는다). 그래서 symlink가 기본값이다.

### 6.1 Claude Code / claude.ai (Agent Skills)

```bash
# 전역(개인) 스킬 — 이 머신의 모든 프로젝트에서 사용
sh kbeauty-trade-matchmaker/install.sh

# 또는 프로젝트 범위 — 저장소와 함께 이동
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project
```

스크립트 없이 손으로 해도 같다:

```bash
# 전역
mkdir -p ~/.claude/skills
ln -s "$(pwd)/kbeauty-trade-matchmaker" ~/.claude/skills/kbeauty-trade-matchmaker
# 또는 사본으로
cp -R kbeauty-trade-matchmaker ~/.claude/skills/

# 프로젝트 범위
mkdir -p <project>/.claude/skills
cp -R kbeauty-trade-matchmaker <project>/.claude/skills/
```

`install.sh`가 쓰는 위치는 위 두 개지만 **런타임이 스캔하는 위치는 더 많다**(우선순위 순): ① 엔터프라이즈 관리 설정 디렉터리의 `.claude/skills/`, ② 개인 `~/.claude/skills/`, ③ 프로젝트 `.claude/skills/`, ④ 중첩 `<subdir>/.claude/skills/`(`/subdir:skill-name`으로 호출), ⑤ `--add-dir` 디렉터리의 `.claude/skills/`, ⑥ 플러그인 `<plugin>/skills/`(`/plugin-name:skill-name`), ⑦ claude.ai 계정 동기화(클라우드 세션). **같은 이름의 엔터프라이즈 스킬이 있으면 내 설치보다 우선한다** — 스킬이 안 보이면 여기부터 확인한다. 전체 표는 `kbeauty-trade-matchmaker/references/runtime-adapters.md` §5.1에 있다.

- **폴더 이름은 반드시 `kbeauty-trade-matchmaker`** — `SKILL.md` frontmatter의 `name`과 일치해야 한다(open standard의 규칙). 폴더 이름 `synced`는 런타임 예약어라 쓸 수 없다.
- **`SKILL.md`는 이미 규격을 완전히 만족한다 — 키를 더하는 것은 개선이 아니라 회귀다.** Agent Skills 오픈 표준이 인정하는 frontmatter 필드는 정확히 **6개**다: `name`, `description`(둘 다 필수)과 선택 필드 `license`, `compatibility`, `metadata`, 그리고 실험적 `allowed-tools`(2026-09-13 확인 — https://agentskills.io/specification). Claude Code는 호스트 전용 키를 더 받아주지만, **Claude Code 밖(claude.ai·Skills API)에서는 이 6개만 허용된다**(2026-09-13 확인 — https://code.claude.com/docs/en/skills). 즉 Claude Code 전용 키가 **하나라도** 있으면 그 폴더는 업로드 자체가 막힌다. 이 패키지는 필수 2개(`name`, `description`)만 싣는 **최대 이식 형태**다.
- 표준이 못박은 한계값(2026-09-13 확인 — https://agentskills.io/specification): `name` 1–64자, 소문자 `[a-z0-9]`와 하이픈만, 앞뒤 하이픈 금지, `--` 금지, **부모 디렉터리 이름과 일치**. `description` 1–1024자. `compatibility`는 쓸 경우 500자 이하. 본문은 **500줄 미만·약 5,000토큰 미만**을 유지한다. Anthropic 제품 문서는 여기에 두 가지를 더한다 — `name`·`description`에 XML 태그 금지, `name`에 예약어 "anthropic"/"claude" 금지(2026-09-13 확인 — https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview).
- 버전 3종(`skill_version` / `schema_version` / `score_version`)은 frontmatter가 아니라 `SKILL.md` **본문**에 적혀 있다 — 표준에 `version` frontmatter 키는 **존재하지 않으며**(버전 문자열의 유일한 정규 자리는 `metadata.version`이다), 최상위 `version`을 추가하면 claude.ai / Skills API 업로드가 막히고 이 패키지의 테스트도 실패한다.
- 표면(surface)끼리 **동기화되지 않는다.** Claude Code(파일 시스템), claude.ai(설정 → Features에서 zip 업로드), Skills API(`/v1/skills`)는 같은 폴더를 각각 따로 올려야 한다.

### 6.2 OpenAI Codex / ChatGPT Skills

같은 폴더를 **한 글자도 고치지 않고** 옮기면 되지만, Codex는 `.claude/skills`를 보지 않는다. Codex의 스킬 루트는 모두 `.agents/skills`다.

```bash
# 사용자 범위
sh kbeauty-trade-matchmaker/install.sh --runtime codex
# 또는 손으로
mkdir -p ~/.agents/skills && cp -R kbeauty-trade-matchmaker ~/.agents/skills/

# 저장소 범위
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo
# 또는 손으로
mkdir -p <repo>/.agents/skills && cp -R kbeauty-trade-matchmaker <repo>/.agents/skills/

# 머신 전체(관리자)
cp -R kbeauty-trade-matchmaker /etc/codex/skills/
```

Codex는 작업 디렉터리에서 저장소 루트까지 **모든 디렉터리의 `.agents/skills`를 스캔**한다. `~/.codex/skills`(`$CODEX_HOME/skills`)는 **deprecated이지만 여전히 지원된다** — Codex의 스킬 루트 해석 코드가 *"Deprecated … kept for backward compatibility"* 주석과 함께 남겨 두고 있다(`codex-rs/ext/skills/src/host_roots.rs`, https://github.com/openai/codex/tree/main/codex-rs/skills). 이미 거기 설치한 것은 계속 동작하고 제거 일정도 공표된 바 없지만, 공개 문서에는 등장하지 않으므로 **새로 설치할 때는 `~/.agents/skills`를 쓴다.**

호출과 갱신:

- 명시 호출은 `$kbeauty-trade-matchmaker` 또는 CLI/IDE 확장의 `/skills`, ChatGPT에서는 `@`. 암묵 호출은 `description`으로 결정된다.
- 스킬 변경은 자동 감지되지만, 반영되지 않으면 Codex를 재시작한다.
- 지우지 않고 끄려면 `~/.codex/config.toml`에 다음을 넣고 재시작한다:

```toml
[[skills.config]]
path = "/path/to/kbeauty-trade-matchmaker/SKILL.md"
enabled = false
```

**ChatGPT 표면 주의.** 단독 스킬 폴더는 **ChatGPT 데스크톱 앱, Codex CLI, IDE 확장**에서만 보인다. ChatGPT **웹·모바일**의 Chat/Work에서 쓰려면 스킬을 **플러그인으로 패키징**해야 한다. 이 패키지는 v0.1.0에서 단독 폴더로만 배포하며 플러그인 패키징은 범위 밖이다.

> **2026-09-13 기준 공식 문서.** 설치 전에 재확인하고, 아래와 어긋나면 **공식 문서가 맞다.**
> - Agent Skills 오픈 표준(규범): https://agentskills.io/specification
> - Anthropic — Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
> - Claude Code — Skills: https://code.claude.com/docs/en/skills
> - OpenAI — Build skills(정본; `developers.openai.com/codex/skills`는 여기로 308 리다이렉트): https://learn.chatgpt.com/docs/build-skills
>
> **2026-09-13에 확인했으나 열리지 않은 문서:** `https://openai.com/academy/skills/`와 `https://help.openai.com/en/articles/20001066`("Skills in ChatGPT")은 자동 요청에 **HTTP 403**을 반환한다. 그래서 **ChatGPT 워크스페이스 업로드·활성화 절차는 이 저장소에서 검증하지 못했고, 사람이 확인해야 한다.** 같은 이유로 **ChatGPT(데스크톱 외 표면)가 번들 `python3` 스크립트를 실제로 실행할 수 있는지도 미확인**이다 — 실행할 수 없다면 `references/runtime-adapters.md` §4의 "Cannot execute `scripts/*.py`" 행(점수 없이 근거만 제시)이 적용된다.
>
> 런타임별 경로 매핑은 `references/runtime-adapters.md` §5에 정리되어 있고, 런타임이 바뀌면 **그 파일 하나만** 고치면 된다.

- 런타임은 `SKILL.md`의 `name` / `description`을 보고 언제 이 스킬을 쓸지 판단한다. description의 트리거 단어(K뷰티 바이어 발굴, OEM/ODM 셀러 소싱, RFQ 매칭, 아웃리치 초안, 그리고 영어 표현들)를 지우지 말 것.
- Codex가 실제로 읽는 frontmatter 키는 `name`, `description`, `metadata.short-description`뿐이고 나머지는 조용히 무시한다. 그래도 **표준에 맞춰** 작성해야 다른 런타임에서 깨지지 않는다.
- `scripts/`는 평범한 Python이며 `python3 scripts/<name>.py`로 실행된다. 런타임 전용 래퍼가 필요 없지만, 런타임의 승인/샌드박스 설정을 따른다.
- **`AGENTS.md`는 스킬 설치 수단이 아니다.** Codex의 별개 기능으로, 저장소에 상시 적용되는 커스텀 지시문이며 디렉터리 단위로 이어 붙고 `project_doc_max_bytes`(기본 32 KiB)로 제한된다(2026-09-13 확인 — https://learn.chatgpt.com/docs/agent-configuration/agents-md). 스킬은 `name`/`description`을 보고 **필요할 때** 로드되고, `AGENTS.md`는 **매 세션** 읽힌다. 이 패키지를 설치하는 데 `AGENTS.md` 항목이 필요하지도 않고, `AGENTS.md`가 스킬 설치를 대신하지도 않는다.

### 6.3 설치 확인

두 가지가 따로 깨질 수 있다. 아래 세 줄은 그중 **코드만** 검사한다.

```bash
python3 scripts/validate_output.py --version      # 스크립트가 실행된다
python3 adapters/tradewith_adapter.py --version   # 어댑터가 자격증명 없이 실행된다
python3 tests/run_tests.py                        # golden fixture가 통과한다
```

앞의 두 개가 버전 줄을 출력하고 세 번째가 exit `0`이면 그 런타임에서 **코드는** 건전하다.

**런타임이 스킬을 실제로 보는지**는 따로 확인해야 한다 — 위 세 줄은 `SKILL.md`를 한 번도 읽지 않은 런타임에서도 전부 통과하기 때문이다.

- Claude Code: 스킬 목록에 `kbeauty-trade-matchmaker`가 보이는지 확인한다.
- Codex CLI/IDE: `/skills` 목록에 나오는지, 또는 `$kbeauty`가 자동완성되는지 확인한다.

안 보이면 ① 폴더가 해당 런타임의 skills 루트 **바로 아래**에 있는지, ② 디렉터리 이름이 `kbeauty-trade-matchmaker`인지(frontmatter `name`과 같아야 한다), ③ `SKILL.md`의 **첫 줄이 정확히 `---`**인지(아니면 파일 전체가 본문으로 취급된다), ④ 더 높은 우선순위 범위에 같은 이름의 스킬이 있는지를 본다. Codex는 재시작한다.

---

## 7. 테스트 실행

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py            # 인자 없음. exit 0 = 전부 통과, 1 = 하나라도 실패
python3 tests/run_tests.py -v         # 실패만이 아니라 모든 케이스를 한 줄씩 출력
```

러너는 표준 라이브러리만 쓰고, fixture를 `__file__` 기준으로 스스로 찾으며, 모든 스크립트에 `--as-of 2026-09-12`를 넘겨 재현 가능하게 실행한 뒤 expected fixture와 **바이트 단위로** 비교한다. 마지막에 `PASS n / FAIL m` 요약을 낸다.

fixture 케이스보다 먼저 도는 것이 **스키마 자체 검사**다: `schemas/*.json`이 파싱되는지, `$ref`가 자기 파일 안에서 풀리는지, 검증기가 지원하지 않는 키워드를 쓴 곳이 없는지, 공유 `$defs`가 파일 간에 구조적으로 일치하는지, 그리고 스키마에 박힌 `schema_version` / `score_version`이 `scoring.config.json`과 어긋나지 않는지를 본다. 그 다음 각 문서 종류의 golden fixture 하나씩을 `validate_output.py --strict --invariants`로 통과시킨다. 이 두 검사가 PRD 완료 조건 "스키마가 validator를 통과한다"의 기계 검증 형태다.

개별 스크립트를 직접 돌려볼 수도 있다. `stdout`에는 JSON만, 진단은 전부 `stderr`로 나가므로 파이프가 안전하다.

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py \
    --input tests/fixtures/match-134.input.json \
    --as-of 2026-09-12 --pretty

python3 scripts/validate_output.py --input out/match-134.json --strict --invariants
```

---

## 8. TradeWith 어댑터 quickstart — 백엔드 없이 파일만으로

TradeWith 내부 API가 **아직 없어도** 전체 워크플로가 오늘 돈다. 어댑터는 인터페이스 하나에 백엔드 두 개(`file`, `http`)를 두고, 기본값이 `file`이다. 자격증명도, 네트워크도, 서비스도 필요 없다.

```bash
cd kbeauty-trade-matchmaker

# 1. 데이터 디렉터리를 정한다. 첫 쓰기에서 자동 생성된다.
export TRADEWITH_DATA_DIR=./tradewith-data

# 2. 구매요청 읽기
python3 adapters/tradewith_adapter.py get-rfq --id 134 --pretty

# 3. 내부 셀러 후보 조회
python3 adapters/tradewith_adapter.py list-sellers --country KR --limit 20

# 4. 발굴 결과 저장 (buyer 또는 seller 문서)
python3 adapters/tradewith_adapter.py save-leads --input out/buyers.scored.json

# 5. 매칭 실행 결과 저장
python3 adapters/tradewith_adapter.py save-matches --input out/match-134.json

# 6. 아웃리치 초안을 사람 검토 큐에 넣기 (발송이 아니다)
python3 adapters/tradewith_adapter.py save-outreach-drafts --input out/drafts.json

# 7. 스킬이 쓸 수 있는 범위 안에서 lead 상태 이동
python3 adapters/tradewith_adapter.py update-lead-status \
    --id BUY-gulfbeauty-example-com --status VERIFIED
```

데이터 디렉터리 구조는 그냥 JSON 파일 트리다. **파일 이름이 곧 id**이고, 같은 입력을 다시 넣으면 같은 파일을 덮어써서 결과가 바이트 단위로 안정적이다.

```
tradewith-data/
├── rfqs/134.json
├── sellers/SEL-hankosun-example-com.json
├── leads/BUY-gulfbeauty-example-com.json
├── matches/MR-134-2026-09-12-01.json
└── outreach-drafts/OD-SEL-hankosun-example-com-partnership_form.json
```

시드하는 방법도 지루할 만큼 단순하다 — 스키마에 맞는 RFQ 하나를 `rfqs/`에, 셀러들을 `sellers/`에 넣으면 끝이다. **이 디렉터리는 패키지 바깥에, 그리고 실제 회사 데이터가 들어간다면 버전 관리 바깥에 두라.** 저장소의 `.gitignore`에 `tradewith-data/`가 이미 들어 있다.

나중에 API가 생기면 플래그 하나만 바뀐다. 점수도, 스키마도, 출력 블록도 바뀌지 않는다.

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # 환경변수로만. 커맨드라인에 쓰면 셸 히스토리에 남는다
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

환경변수는 **호출 시점에만** 읽는다(import 시점이 아니다). `--backend http`를 이름으로 지정하지 않는 한 어떤 호출도 네트워크에 나가지 않으며, "API를 시도하고 실패하면 파일로 폴백" 같은 동작은 존재하지 않는다. 토큰은 어떤 에러 메시지·경고·저장 문서에도 출력되지 않는다. 자세한 내용은 `adapters/tradewith_adapter.md`에 있다.

---

## 9. 결정 필요 (PRD §21 Open Questions)

PRD가 열어 둔 여섯 가지다. 각 항목은 **아직 사람이 결정할 문제**이고, 결정될 때까지 이 구현이 고른 기본값을 함께 적는다. 기본값은 전부 바꿀 수 있게 한 곳에 모여 있다.

| # | 결정 필요 | 이 구현의 기본값 | 어디서 바꾸나 |
|---|---|---|---|
| 1 | TradeWith 내부 Seller/RFQ **API가 이미 있는가, 파일/DB 직접 연결로 시작하는가?** | **둘 다.** 인터페이스 하나에 백엔드 두 개. `file`이 기본이라 API 없이 오늘 동작하고, API가 생기면 `--backend http`로 전환한다. 스코어·스키마·출력은 그대로다. | `TRADEWITH_BACKEND` 환경변수 / `--backend` 플래그 |
| 2 | 발굴한 lead를 **서비스 DB에 바로 저장할지, 별도 research staging을 둘지?** | **staging 우선.** 스킬은 `POST /research/leads`(파일 백엔드에서는 `<data-dir>/leads/`)에만 쓴다. 서비스 DB로의 승격은 애플리케이션 레이어의 승인 단계이며, 스킬의 상태 기계는 `READY_FOR_REVIEW`에서 끝난다. | `adapters/tradewith_adapter.py`의 write 대상 |
| 3 | 적격 임계값을 **70점 고정으로 할지, 카테고리별 percentile로 할지?** | **70점 고정** (`thresholds.mode = "fixed"`, `fixed = 70`). PRD 12.1의 "Qualified (>=70)"과 일치한다. percentile 모드도 완전히 구현되어 있다(nearest-rank 75, 하한 50, 모집단 8 미만이면 fixed로 폴백). 어느 쪽을 썼든 `summary.threshold_used` / `summary.threshold_mode`에 항상 기록된다. | `schemas/scoring.config.json`의 `thresholds` / `--threshold`, `--threshold-mode` |
| 4 | `info@`·`sales@` 같은 대표 주소와 named business contact를 **어디까지 저장할지?** | **회사 단위 채널만.** 공개된 대표 연락 채널(대표 메일, 문의 폼, 회사 전화)만 저장한다. 개인 이메일·전화번호의 추론·생성·대량 수집은 코드 수준에서 존재하지 않는다. 이름이 붙은 담당자 정보는 이 스킬이 만들지 않으며, 필요하면 사람이 CRM에 직접 넣는다. | `references/evidence-policy.md`, `references/compliance-notes.md` (정책 변경 시 스키마의 contact 필드도 함께) |
| 5 | 박람회·협회 디렉터리별 **crawling 허용 범위와 ToS 확인 프로세스**를 어떻게 둘지? | **robots/ToS 준수, 우회 없음.** CAPTCHA·로그인·페이월·rate-limit 우회 코드가 없다. 접근이 막히면 그 사실은 추정되지 않고 `"unknown"`으로 남는다. 저장은 주장·URL·관찰 시각·짧은 인용까지만(데이터 최소화). 관할별·사이트별 확인은 `compliance-notes.md`가 "확인 필요"로 **표시**할 뿐 합법 여부를 결론짓지 않는다. | `references/compliance-notes.md`, `references/buyer-discovery.md` / `seller-discovery.md`의 소스 목록 |
| 6 | **RFQ가 없을 때** Seller outreach를 어떤 value proposition으로 제한할지? | **RFQ 없는 변형 템플릿을 따로 쓴다.** 존재하지 않는 수요나 대기 중인 바이어를 암시하는 문장은 금지(PRD 테스트 T05)이며, 검증된 사실에 근거한 카테고리 수준의 소개와 중립적 CTA만 허용한다. 초안은 그래도 `READY_FOR_REVIEW`에서 멈춘다. | `templates/seller_outreach.md`의 RFQ-absent 변형, `references/outreach-guidelines.md` |

---

## 10. 버전

| 버전 | 현재 값 | 무엇을 설명하나 | 어디에 사는가 |
|---|---|---|---|
| `skill_version` | `0.1.0` | 패키지 자체 — 프롬프트, references, scripts, templates, tests | `SKILL.md` 본문, 이 README, `match-result.skill_version` |
| `schema_version` | `0.1.0` | **모양** 계약 — 필드 이름, enum, required 목록 | 모든 문서, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | **루브릭** — 가중치, criterion, 신호, 페널티, 임계값, 하드 필터, evidence 함수 | `schemas/scoring.config.json`, 점수가 매겨진 모든 문서 |

세 값을 확인하는 가장 빠른 방법:

```bash
python3 scripts/validate_output.py --version
# validate_output.py skill_version=0.1.0 schema_version=0.1.0 score_version=kbtm-score-0.1.0
```

루브릭이 바뀌면 저장된 점수는 **정의상 낡은 것**이 된다. 원본 레코드를 그대로 보관하기 때문에(evidence·쿼리 표면·`as_of`를 함께 저장한다) 웹을 다시 긁지 않고 재계산할 수 있다. 과거 결과를 **재현**하려면 원래의 `--as-of`를, 최신 상태로 **갱신**하려면 새 `--as-of`를 넘긴다. 서로 다른 `score_version`의 결과를 한 목록에서 비교하거나 순위를 매기는 것은 금지이며, `validate_output.py`가 이를 잡아낸다.

---

## 11. 요구사항

- `python3` 3.9–3.14. **표준 라이브러리만** 사용한다. `pip install`이 필요한 것은 아무것도 없다.
- POSIX 셸 (설치 스크립트용).
- 발굴 모드에는 런타임의 웹 검색·페이지 조회 능력이 필요하다. 스크립트 자체는 완전히 오프라인이므로 테스트와 점수 재계산은 네트워크 없이 돈다.
- TradeWith 연동은 선택이다. 어댑터의 `file` 백엔드는 아무것도 요구하지 않는다.

---

## 12. 라이선스 / 데이터 취급

이 저장소에는 실제 회사 데이터나 개인정보가 들어 있지 않다. fixture는 `example.com` 계열의 가공 도메인을 쓴다. 운영 데이터를 넣기 시작하면 `.gitignore`가 이미 막고 있는 `tradewith-data/`, `.env`, `out/`을 그대로 두고, 저장하는 것이 주장·URL·관찰 시각·짧은 인용에 머무는지 주기적으로 확인하라.

**법률 자문이 아니다.** 관할별 다이렉트 마케팅 규정 확인은 사람의 몫이며, 이 패키지는 그 확인이 필요한 지점을 표시할 뿐이다.
