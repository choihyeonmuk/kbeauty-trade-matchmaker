# K-Beauty Trade Matchmaker

**Kamuya açık web üzerinde yurt dışındaki K-Beauty alıcılarını ve Koreli satıcıları bulan, her önemli iddiayı tıklanabilir bir kaynağa karşı doğrulayan, iki tarafı da deterministik script'lerle puanlayan, satın alma taleplerini satıcılarla eşleştiren ve bir insanın incelemesi için hazırlanan iletişim taslağında duran bir Agent Skill.**

Aynı klasör **Claude Code** ve **OpenAI Codex** üzerinde hiçbir değişiklik yapılmadan çalışır. Python yalnızca **standart kütüphaneyi** kullanır (3.9–3.14); `pip install` ile kurulacak hiçbir şey yoktur.

[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [Proje sayfası](https://kbeauty.tradewith.kr/) · [LinkedIn](https://www.linkedin.com/in/hm-choi)

> **Durum: v0.1.1.** Pipeline, kurgusal fixture'lar üzerinde 179 vakaya karşı test edilmiş ve canlı web üzerinde bir kez denenmiştir. Puanlama rubriği **henüz gerçek sonuçlara karşı doğrulanmamıştır**: puanlar tekrarlanabilir ve izlenebilirdir, ancak öngörü gücü henüz bilinmemektedir. RFQ Matching ve Outreach Draft modları canlı veri üzerinde çalıştırılmamıştır. Bir puana güvenmeden önce [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) dosyasını okuyun.

---

## Güvenlik sınırı: bu skill hiçbir şey göndermez

Bu paketle ilgili en önemli gerçek şudur: **paketteki hiçbir kod mesaj gönderemez.**

- **Yalnızca taslak.** İletişim çalışması her zaman `READY_FOR_REVIEW` durumunda sona erer; taslakta `auto_send: false` ve `manual_approval_required: true` bulunur.
- **İnsanlar onaylar, harici sistemler gönderir.** Skill bir lead'i `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` aşamalarından geçirebilir, daha ileri götüremez. `APPROVED_FOR_OUTREACH` ve sonrası bir CRM'e ve bir kişiye aittir; adapter bu geçişleri **reddeder**.
- **SMTP, Gmail, SES veya webhook ile gönderim kodu yoktur.** Kişisel e-posta adresleri veya telefon numaraları tahmin edilmez ya da toplu olarak toplanmaz (yalnızca şirket düzeyindeki kanallar kullanılır). CAPTCHA, oturum açma, ödeme duvarı, robots.txt veya kullanım koşulları aşılmaz.
- **Hiçbir şey uydurulmaz.** MOQ, sertifikalar, ihracat pazarları, münhasırlık ve kapasite yalnızca bir kaynak bunları belirttiğinde kaydedilir; aksi takdirde `"unknown"` olarak kalır. "Bir alıcı sizi bekliyor" gibi dayanaksız aciliyet ifadeleri şablon düzeyinde yasaklanmıştır.
- **Hukuki değerlendirme yapılmaz.** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) nerede kontrol gerektiğini işaretler; gönderimin serbest olduğu sonucuna asla varmaz.

Bu eksik bir özellik değil, bilinçli bir tasarım kararıdır. *Taslak hazırlamak* ve *göndermek* farklı yetkilerdir ve bu paket yalnızca ilkine sahiptir.

---

## Ne yapar

Google, LinkedIn ve fuar rehberlerinde geçen bir günlük aramayı, **her iddianın kaynağına bağlandığı doğrulanabilir bir kısa listeye** dönüştürür.

1. **Keşfet:** Kamuya açık web üzerinde alıcı ve satıcı adaylarını ülke, kategori, OEM/ODM, MOQ ve sertifikaya göre bulur.
2. **Doğrula:** Her iddiayı güvenilir kaynaklara, genellikle şirketin kendi sitesine karşı doğrular ve URL'si ile gözlemlendiği zamanla birlikte saklar.
3. **Normalleştir:** Alan adlarını ve şirket adlarını normalleştirir, mükerrer kayıtları birleştirir, alıcı gereksinimlerini ve satıcı yeteneklerini tek bir şemada toplar.
4. **Puanla:** Alıcıları ve satıcıları deterministik Python script'leriyle puanlar. Aynı girdi ve aynı `--as-of` aynı çıktıyı verir.
5. **Eşleştir:** Bir satın alma talebini (RFQ) satıcılarla eşleştirir: önce kesin filtreler, ardından ağırlıklı puan, ardından sınırlandırılmış semantik yeniden sıralama ve son olarak bilinmeyen değerlerin ele alınması. İlk N sonucu **ve reddedilen her satıcının neden reddedildiğini** döndürür.
6. **Taslak hazırla:** Yalnızca doğrulanmış olgulardan kişiselleştirilmiş iletişim taslağı yazar ve orada durur.

---

## Dört mod

Agent'a sade bir dille sorun. Aşağıdaki biçimler, her modun aldığı parametrelerin kısa gösterimidir.

### Buyer Discovery

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

BAE'de K-Beauty ürünleri taşıyan distribütörleri, ithalatçıları ve toptancıları bulur; şirket türünü, taşınan markaları, toptan satış imkânını, iş ortaklığı sinyallerini, resmi iletişim kanallarını ve kanıt URL'lerini normalleştirir.

### Seller Discovery

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

Koreli üreticileri ve markaları bulur; OEM/ODM kabiliyetini, MOQ'yu, sertifikaları, ana ürünleri ve ihracat ya da iş ortaklığı sinyallerini doğrular.

### RFQ Matching

```
/kbeauty-match rfq="#134" top=10
```

Satıcıları RFQ'nun ürün, MOQ, varış pazarı, sertifika ve private label gereksinimlerine göre filtreler, nicel ve nitel puanları birleştirir ve ilk 10 sonucu hariç tutma gerekçeleriyle birlikte döndürür. Gerekçeleriyle birlikte "uygun eşleşme yok" sonucu da olağan bir çıktıdır.

### Outreach Draft

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

Yalnızca doğrulanmış olgulardan bir konu satırı, ileti gövdesi ve kişiselleştirme noktaları yazar. Zayıf kanıtlar genelleştirilir veya "doğrulama gerekiyor" olarak işaretlenir. **Gönderim yapmaz.**

---

## Örnek çıktı

Paketle birlikte gelen test fixture'ı üzerinde çalıştırılan RFQ Matching'den bir alıntı. Satıcılar kurgusaldır.

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

9 numaralı satıcıya dikkat edin: MOQ'su yayımlanmadığı için MOQ kesin filtresi **başarısız sayılmadı, atlandı**. Satıcı sessizce reddedilmek ya da sessizce geçirilmek yerine, daha düşük bir operasyonel puan ve açık bir "Missing" satırıyla listede kalır.

---

## Paket yapısı

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
│   └── scoring.config.json       # Every weight, threshold and penalty lives in this one file
├── scripts/                      # Standard library only. No network, no credentials
│   ├── _common.py                # Config, rounding, normalisation, tri-state helpers, schema validator
│   ├── normalize_company.py
│   ├── dedupe_companies.py
│   ├── score_buyer.py
│   ├── score_seller.py
│   ├── score_match.py
│   └── validate_output.py        # Schema plus contract invariants
├── templates/
│   ├── buyer_outreach.md
│   ├── seller_outreach.md        # With and without an RFQ
│   └── legal_notices.md          # Per-jurisdiction, per-channel notice blocks
└── tests/
    ├── cases.md                  # Test case specification, including negative and edge cases
    ├── run_tests.py
    └── fixtures/                 # Golden inputs and expected outputs; all companies fictional
```

Yalnızca `kbeauty-trade-matchmaker/` klasörü kurulur. Runtime'ın ihtiyaç duyduğu sözleşme içeriği `references/data-contract.md` ve `references/output-format.md` dosyalarında paketle birlikte gelir.

---

## Puanlama nasıl çalışır

Puanlar bir modelin izleniminden değil, **deterministik script'lerden** gelir.

Alıcılar altı boyutta puanlanır: `kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10. Satıcılar da altı boyutta puanlanır: `product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10. Her boyut, kriter düzeyindeki puanları toplar ve uygulanabilir puanlar üzerinden 0–100 aralığına normalleştirir.

Eşleştirme dört aşamayı sırayla uygular:

1. **Kesin filtreler** `HF-01..HF-08` ve buna ek olarak görüntüleme kapısı `HF-00`. Başarısızlıklar süreci erken kesmez; **tüm** hariç tutma gerekçeleri toplanır.
2. **Ağırlıklı puan:** `product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10. Bunlar satıcının kendi altı boyutunun RFQ'ya göre yeniden puanlanmış hâlidir; böylece bir satıcı asla iki farklı rubrikle değerlendirilmez.
3. **Sınırlandırılmış semantik yeniden sıralama.** Modelin ürettiği tek adım budur. Etkisi sınırlandırılmıştır, kanıta dayalı gerekçeler gerektirir ve kesin filtreden kalmış bir adayı asla geri getiremez.
4. **Bilinmeyen değerlerin ele alınması.**

En önemli üç kural şunlardır:

- **Bilinmeyen, sıfır demek değildir.** Bilinmeyen bir değer, ilgili kriterin azami puanının %30'unu alır (`neutral_base` 50 × `penalty_factor` 0.6). Bir adayı yalnızca bir değer bilinmediği için reddetmek yasaktır; cezadan kaçınmak için değer tahmin etmek de yasaktır.
- **Sistem saati okunmaz.** Tüm zaman hesaplamaları `--as-of` kullanır; böylece aynı girdi, ne zaman çalıştırılırsa çalıştırılsın aynı sayıları verir.
- **Güven, kalite değildir.** Güven düzeyi "bu kayda ne ölçüde güvenilebilir" sorusunu yanıtlar, `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW` olarak gösterilir ve nitelendirme puanını asla okumaz.

Hiçbir K-Beauty kanıtı bulunmayan bir alıcı yine de puanlanır ve sıralanır, ancak diğer boyutları ne kadar güçlü olursa olsun asla nitelikli (qualified) olarak işaretlenemez. Varsayılan nitelendirme eşiği sabit 70'tir; yüzdelik dilim modu da uygulanmıştır. Ayarlanabilir tüm sayılar `schemas/scoring.config.json` dosyasındadır.

## Kanıtlar nasıl çalışır

Çıktının birimi "bir şirket" değil, **kanıtıyla birlikte bir iddiadır**. Bir kanıt öğesi tek bir `claim`, bu iddianın kaynak URL'si, kaynak kademesi, içerik tarihi (`source_date`), gözlemlendiği zaman (`observed_at`), olgu mu çıkarım mı olduğu ve kısa bir alıntı içerir.

| Kademe | Kaynak | Puan |
|---|---|---|
| 1 | Şirketin resmi sitesi | 100 |
| 2 | Resmi fuar, dernek, kamu kurumu veya ticaret ajansı rehberleri; dahili kayıtlar | 82 |
| 3 | Resmi LinkedIn ve sosyal medya profilleri | 64 |
| 4 | Saygın üçüncü taraf rehberleri ve basın bültenleri | 46 |
| 5 | Topluluk gönderileri ve bloglar (yalnızca destekleyici sinyal) | 20 |

Puanlar içeriğin yaşıyla çarpılır: 90 gün içinde 1.00, bir yıl içinde 0.92, iki yıl içinde 0.80, daha eski 0.60, tarihsiz 0.85. Yaş her zaman `observed_at` değil `source_date` üzerinden ölçülür; çünkü bir sayfayı ne zaman okuduğumuz, içeriğinin ne kadar eski olduğu hakkında hiçbir şey söylemez.

İki durum bilinçli olarak birbirinden ayrılmıştır:

- **`unverified`**: Kaydın kanıtı vardır, ancak hiçbir önemli iddianın resmi (1. kademe) bir kaynağı yoktur. Kayıt puanlanır, sıralanır ve `— unverified` işaretiyle gösterilir. **Hariç tutulmaz.**
- Hiçbir önemli iddiada **hiç kanıt bulunmaması**: Kayıt puanlamadan önce gerekçesi belirtilerek ("site açılamadı" veya "okundu, ancak önemli bir iddia yok") `excluded[]` listesine taşınır. Sıralı bir listeyi kanıtsız şirketlerle şişirmek, tam olarak bu kuralın önlediği hatadır.

Depolama asgari düzeydedir: iddia, URL, gözlem zamanı ve kısa bir alıntı; asla sayfaların tamamı veya gereksiz kişisel profiller saklanmaz. Çelişen kaynaklar bir insanın görmesi için `conflicts[]` içinde kaydedilir, asla sessizce çözülmez ve çelişkiler güven düzeyini düşürür.

---

## Kurulum

### claude.ai, terminal gerektirmez

1. En son sürümden [`kbeauty-trade-matchmaker.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker.zip) dosyasını indirin. ZIP dosyasını açmayın.
2. claude.ai'da Settings → Capabilities bölümünü açın ve kod çalıştırmanın (code execution) etkin olduğundan emin olun. Ardından Customize → Skills bölümünü açın, Upload skill seçeneğini seçin, ZIP dosyasını bırakın ve kaydedin.
3. Yeni bir sohbette web aramasını açın ve sade bir dille sorun; örneğin "Find 5 K-Beauty distributors in the UAE that carry sunscreen." (BAE'de güneş kremi taşıyan 5 K-Beauty distribütörü bul.) Beş şirket yaklaşık 10 ila 15 dakika sürer.

2026-09-14 tarihinde ücretli bir planda uçtan uca doğrulanmıştır: skill adı anılmadan çağrıldı, skill içinde web araması ve sayfa getirme işlemlerini yürüttü ve puanlama script'lerini sandbox içinde çalıştırdı. Ücretsiz bir planın tam bir çalıştırma için yeterli kullanım hakkı sunup sunmadığı kontrol edilmemiştir. Adım adım kurulum kılavuzu (yalnızca Korece): [https://kbeauty.tradewith.kr/install-ko](https://kbeauty.tradewith.kr/install-ko). Takıldınız mı? [Bana LinkedIn üzerinden mesaj gönderin](https://www.linkedin.com/in/hm-choi).

### Claude Code ve Codex kurulum aracı

`install.sh` POSIX `sh` script'idir; ağ bağlantısı veya sudo kullanmaz ve hedef dizin dışında hiçbir şey yazmaz. **Önce `--dry-run` çalıştırın.**

```bash
git clone https://github.com/choihyeonmuk/kbeauty-trade-matchmaker.git
cd kbeauty-trade-matchmaker

sh kbeauty-trade-matchmaker/install.sh --dry-run          # Print the plan, change nothing
sh kbeauty-trade-matchmaker/install.sh --verify           # Claude Code: symlink into ~/.claude/skills/, then run the tests
sh kbeauty-trade-matchmaker/install.sh --runtime codex    # Codex: symlink into ~/.agents/skills/
```

| Seçenek | Anlamı |
|---|---|
| *(varsayılan)* | `$HOME/.claude/skills/kbeauty-trade-matchmaker` konumuna **symlink** oluşturur; kaynakta yapılan değişiklikler hemen geçerli olur |
| `--runtime claude\|codex` | Runtime'ı seçer. `claude` (varsayılan) `.claude/skills/`, `codex` ise `.agents/skills/` kullanır. `--codex`, `--runtime codex` için kısaltmadır |
| `--copy` | Symlink yerine bağımsız bir kopya kurar |
| `--project DIR` | `$HOME` yerine `DIR` altına kurar; böylece skill o repository ile birlikte taşınır |
| `--force` | Bu script'in oluşturmadığı mevcut bir dizinin üzerine yazar (varsayılan olarak reddedilir) |
| `--verify` | Kurulumdan sonra `tests/run_tests.py` dosyasını çalıştırır; başarısızlıkta 1 koduyla çıkar |
| `--dry-run` | Yalnızca planı yazdırır |

Yeniden çalıştırmak güvenlidir. Çıkış kodları: `0` başarılı, `1` kurulum reddedildi veya doğrulama başarısız oldu, `2` kullanım hatası.

### Claude Code ve claude.ai

```bash
sh kbeauty-trade-matchmaker/install.sh                                  # Personal: every project on this machine
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project  # Project scope
```

- **Klasörün adı `kbeauty-trade-matchmaker` olmalıdır**; bu ad `SKILL.md` içindeki `name` ile eşleşir.
- **`SKILL.md` zaten tamamen uyumludur; frontmatter'a anahtar eklemek bir iyileştirme değil, gerilemedir.** [Agent Skills açık standardı](https://agentskills.io/specification) tam olarak altı alanı tanır: `name` ve `description` (zorunlu) ile isteğe bağlı `license`, `compatibility`, `metadata` ve deneysel `allowed-tools`. Claude Code dışında (claude.ai, Skills API) yalnızca bu altı alan kabul edilir; dolayısıyla yalnızca Claude Code'a özgü tek bir anahtar bile yüklemeyi engeller. Bu paket yalnızca iki zorunlu anahtarı içerir. Sürüm bilgileri `SKILL.md` gövdesinde yer alır.
- Platformlar birbiriyle senkronize olmaz. Claude Code (dosya sistemi), claude.ai (Customize → Skills bölümünde ZIP yükleme) ve Skills API (`/v1/skills`) için klasörün her birine ayrı ayrı yüklenmesi gerekir.

### OpenAI Codex ve ChatGPT

Klasör değiştirilmeden taşınır, ancak Codex `.claude/skills` dizinini okumaz. Codex'in skill kök dizinleri `.agents/skills` şeklindedir.

```bash
sh kbeauty-trade-matchmaker/install.sh --runtime codex                                # User scope: ~/.agents/skills/
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo   # Repository scope
```

- Codex, çalışma dizininden repository köküne kadar her dizindeki `.agents/skills` klasörünü tarar. `~/.codex/skills` kullanımdan kaldırılmıştır (deprecated) ancak hâlâ desteklenmektedir; yeni kurulumlar için `~/.agents/skills` kullanın.
- Açıkça çağırmak için CLI ve IDE eklentisinde `$kbeauty-trade-matchmaker` veya `/skills`, ChatGPT'de ise `@` kullanın. Örtük çağırma `description` alanına göre belirlenir.
- Bağımsız bir skill klasörü yalnızca **ChatGPT masaüstü uygulaması, Codex CLI ve IDE eklentisinde** görünür. ChatGPT **web ve mobil** sürümleri, skill'in bir plugin olarak paketlenmesini gerektirir; v0.1.1 bunu içermez.
- `AGENTS.md` bir skill kurulum yöntemi değildir. Repository için sürekli geçerli talimatlar sağlayan ayrı bir Codex özelliğidir.

> **2026-09-13 tarihinde resmi dokümantasyonla karşılaştırılarak kontrol edilmiştir.** Buradaki herhangi bir bilgi güncel dokümantasyonla çelişirse, dokümantasyon esas alınmalıdır.
> [Agent Skills spesifikasyonu](https://agentskills.io/specification) · [Anthropic Agent Skills genel bakış](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [Claude Code skill'leri](https://code.claude.com/docs/en/skills) · [OpenAI ile skill oluşturma](https://learn.chatgpt.com/docs/build-skills)
>
> ChatGPT workspace yükleme prosedürü ve masaüstü dışındaki ChatGPT platformlarının paketteki `python3` script'lerini çalıştırıp çalıştıramayacağı **doğrulanamamıştır**; ilgili OpenAI yardım sayfaları otomatik isteklere HTTP 403 döndürmüştür. Script'ler çalıştırılamazsa skill, puan içermeyen kanıt çıktısına geri döner (bkz. `references/runtime-adapters.md` §4).

### Kurulumu kontrol edin

Birbirinden bağımsız olarak bozulabilecek iki şey vardır. Aşağıdaki üç satır **yalnızca kodu** kontrol eder:

```bash
python3 kbeauty-trade-matchmaker/scripts/validate_output.py --version
python3 kbeauty-trade-matchmaker/adapters/tradewith_adapter.py --version
python3 kbeauty-trade-matchmaker/tests/run_tests.py
```

**Runtime'ın skill'i gördüğünü** kontrol etmek için Claude Code'un skill listesinde veya Codex'in `/skills` listesinde `kbeauty-trade-matchmaker` adını arayın. Görünmüyorsa klasörün doğrudan skill kök dizininin altında bulunduğunu, adının `kbeauty-trade-matchmaker` olduğunu, `SKILL.md` dosyasının ilk satırının tam olarak `---` olduğunu ve daha yüksek öncelikli bir kapsamda aynı ada sahip bir skill bulunmadığını kontrol edin. Değişikliklerden sonra Codex'i yeniden başlatın.

---

## Testleri çalıştırma

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py       # Exit 0 when everything passes, 1 otherwise
python3 tests/run_tests.py -v    # One line per case, not just failures
```

Test çalıştırıcısı yalnızca standart kütüphaneyi kullanır, fixture'ları kendi konumuna göre bulur, her script'e `--as-of 2026-09-12` parametresini iletir ve çıktıyı beklenen fixture'larla **bayt bayt** karşılaştırır. Fixture vakalarından önce şemaların kendisini kontrol eder: ayrıştırılabildiklerini, her `$ref` referansının çözümlendiğini, desteklenmeyen bir anahtar sözcük kullanılmadığını, paylaşılan `$defs` tanımlarının dosyalar arasında tutarlı olduğunu ve gömülü sürümlerin `scoring.config.json` ile eşleştiğini.

Script'ler doğrudan da çalıştırılabilir. JSON `stdout`'a, tüm tanılama mesajları ise `stderr`'e gider; bu nedenle pipe kullanımı güvenlidir.

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py --input tests/fixtures/match-134.input.json --as-of 2026-09-12 --pretty
```

---

## Adapter hızlı başlangıç: yalnızca dosyalar, backend gerekmez

Tüm iş akışı bugün **herhangi bir dahili API olmadan** çalışır. Adapter'ın tek bir arayüzü ve iki backend'i vardır: `file` ve `http`; varsayılan `file`'dır. Kimlik bilgisi, ağ bağlantısı veya servis gerektirmez.

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

Veri dizini, **dosya adının id olduğu** düz bir JSON dosya ağacıdır; bu nedenle aynı girdiyle yeniden çalıştırmak aynı dosyanın üzerine yazar ve sonuçlar bayt düzeyinde sabit kalır. Bu dizini paketin dışında tutun; gerçek şirket verileri içeriyorsa sürüm kontrolünün de dışında tutun. `.gitignore` zaten `tradewith-data/` dizinini hariç tutar.

Bir API mevcut olduğunda yalnızca bir parametre değişir. Puanlar, şemalar ve çıktı değişmez.

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # Environment only; a command-line token lands in shell history
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

Ortam değişkenleri import sırasında değil, çağrı sırasında okunur. `--backend http` belirtilmedikçe hiçbir şey ağa erişmez, API'den dosyalara sessiz bir geri dönüş yoktur ve token hiçbir zaman hata mesajlarında, uyarılarda veya saklanan belgelerde görünmez. Ayrıntılar `adapters/tradewith_adapter.md` dosyasındadır.

---

## Açık kararlar

Altı soru hâlâ açıktır. Her birinin bu uygulamada bir varsayılanı vardır ve her varsayılan tek bir yerden değiştirilebilir.

| # | Soru | Buradaki varsayılan | Nereden değiştirilir |
|---|---|---|---|
| 1 | Dahili bir satıcı/RFQ API'si var mı, yoksa dosyalarla mı başlanmalı? | **Her ikisi.** Varsayılan olarak file backend; bir API mevcut olduğunda `--backend http` | `TRADEWITH_BACKEND` / `--backend` |
| 2 | Lead'ler doğrudan servis veritabanına mı, yoksa bir hazırlık (staging) alanına mı kaydedilmeli? | **Staging.** Skill yalnızca `POST /research/leads` (veya `<data-dir>/leads/`) hedefine yazar; üst aşamaya aktarım, uygulama katmanında verilen bir onaydır | Adapter yazma hedefi |
| 3 | Sabit 70 eşiği mi, yoksa kategori bazında yüzdelik dilim mi? | **Sabit 70.** Yüzdelik dilim modu uygulanmıştır; kullanılan mod her zaman `summary` içinde kaydedilir | `scoring.config.json` içindeki `thresholds`, `--threshold`, `--threshold-mode` |
| 4 | Rol adresleri (`info@`, `sales@`) ve isimli kişiler ne ölçüde saklanmalı? | **Yalnızca şirket düzeyindeki kanallar.** Kodda kişisel iletişim bilgisi tahmini veya toplama işlevi yoktur | `evidence-policy.md`, `compliance-notes.md` |
| 5 | Rehberler için tarama kapsamı ve kullanım koşulları kontrolleri nasıl olmalı? | **robots ve kullanım koşullarına uyulur, hiçbir şey aşılmaz.** Engellenen erişim `"unknown"` olarak kalır | `compliance-notes.md`, keşif kaynak listeleri |
| 6 | RFQ bulunmadığında satıcıya yönelik iletişimde ne söylenebilir? | **RFQ'suz ayrı bir şablon.** Var olmayan bir talebi ima etmek yasaktır | `templates/seller_outreach.md`, `outreach-guidelines.md` |

---

## Sürümler

| Sürüm | Değer | Neyi tanımlar | Nerede bulunur |
|---|---|---|---|
| `skill_version` | `0.1.1` | Paket: prompt'lar, referanslar, script'ler, şablonlar, testler | `SKILL.md` gövdesi, `match-result.skill_version` |
| `schema_version` | `0.1.0` | Yapı sözleşmesi: alan adları, enum'lar, zorunlu alan listeleri | Her belge, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | Rubrik: ağırlıklar, kriterler, sinyaller, cezalar, eşikler, kesin filtreler | `scoring.config.json`, puanlanmış her belge |

```bash
python3 scripts/validate_output.py --version
```

Rubrik değiştiğinde, saklanan puanlar tanım gereği güncelliğini yitirir. Ham kayıtlar kanıtlarını, sorgu kapsamını ve `as_of` değerini koruduğu için puanlar yeniden tarama yapılmadan yeniden hesaplanabilir. Farklı `score_version` değerlerine sahip sonuçları tek bir listede karşılaştırmak veya sıralamak yasaktır ve `validate_output.py` bunu yakalar.

---

## Gereksinimler

- `python3` 3.9–3.14, yalnızca standart kütüphane.
- Kurulum aracı için bir POSIX shell.
- Keşif modları, runtime'ın web arama ve sayfa getirme özelliklerine ihtiyaç duyar. Script'ler tamamen çevrimdışı çalışır; bu nedenle testler ve yeniden puanlama ağ bağlantısı olmadan yapılabilir.
- Dahili veri entegrasyonu isteğe bağlıdır; adapter'ın `file` backend'i hiçbir şey gerektirmez.

---

## Veri işleme

Bu repository gerçek şirket verisi veya kişisel bilgi içermez. Fixture'lar `.example` alan adlarındaki kurgusal şirketleri kullanır. Operasyonel veri eklemeye başladığınızda `tradewith-data/`, `.env` ve `out/` dizinlerini `.gitignore` dosyasında zaten yapıldığı gibi hariç tutulmuş hâlde bırakın ve sakladığınız verilerin iddia, URL, gözlem zamanı ve kısa alıntı düzeyinde kaldığını düzenli olarak kontrol edin.

**Bu bir hukuki tavsiye değildir.** Her yargı bölgesindeki doğrudan pazarlama kurallarını kontrol etmek bir insanın işidir; bu paket bu kontrolün nerede gerektiğini işaretler.
