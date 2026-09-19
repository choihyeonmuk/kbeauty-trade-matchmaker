# K-Beauty Trade Matchmaker

**Sebuah Agent Skill yang menemukan pembeli K-Beauty di luar negeri dan penjual asal Korea di web publik, memverifikasi setiap klaim material terhadap sumber yang dapat Anda klik, menilai kedua pihak dengan skrip deterministik, mencocokkan permintaan pembelian dengan penjual, dan berhenti pada draf outreach untuk ditinjau oleh manusia.**

Folder yang sama berjalan tanpa modifikasi di **Claude Code** dan **OpenAI Codex**. Python hanya menggunakan **standard library** (3.9–3.14); tidak ada yang perlu di-`pip install`.

[English](README.md) · [한국어](README.ko.md) · [मराठी](README.mr.md) · [हिन्दी](README.hi.md) · [Bahasa Indonesia](README.id.md) · [Türkçe](README.tr.md) · [Halaman proyek](https://kbeauty.tradewith.kr/) · [LinkedIn](https://www.linkedin.com/in/hm-choi)

![Temukan mitra dagang K-Beauty yang tepat](kbeauty-trade-partner-linkedin-cities.png)

> Gambaran alur kerja: temukan, verifikasi, cocokkan, dan tetap libatkan manusia dalam peninjauan.

> **Status: v0.3.0.** Pipeline ini telah diuji terhadap 536 kasus pada fixture fiktif dan telah diujicobakan satu kali terhadap web langsung. Rubrik penilaian **belum divalidasi terhadap hasil nyata**: skor dapat direproduksi dan ditelusuri, tetapi belum diketahui daya prediksinya. v0.2.0 telah menambahkan perangkat untuk mengukur hal tersebut ([Memvalidasi skor](#memvalidasi-skor)), tetapi sampel berlabel belum ada. RFQ Matching dan Outreach Draft belum dijalankan pada data langsung. Bacalah [`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) sebelum memercayai sebuah skor.

---

## Yang baru

**v0.3.0 (2026-09-19).** Tidak ada skor yang sudah ada yang berubah. Semua yang baru berada di luar pipeline penilaian.

- **Diff proses.** `scripts/diff_runs.py` membandingkan dua proses bernilai dari pencarian atau RFQ yang sama, lalu mendaftar perusahaan yang baru dan yang hilang, pengecualian yang bergeser, serta perubahan skor, peringkat, penanda qualified, confidence, dan baris Missing. Skrip ini menolak proses yang dinilai dengan versi rubrik yang berbeda dan tidak menyalin detail kontak apa pun. Lihat [Membandingkan dua proses](#membandingkan-dua-proses).
- **Antrean pemeriksaan ulang.** `scripts/stale_evidence.py` mendaftar record tersimpan dan halaman bukti mana yang perlu dibaca ulang, dimulai dari yang paling mendesak. Skrip ini tidak mengambil apa pun dan tidak mengubah record maupun skor. Lihat [Antrean pemeriksaan ulang](#antrean-pemeriksaan-ulang).
- **Ekspor lead.** `scripts/export_leads.py` menulis sebuah proses bernilai sebagai CSV untuk spreadsheet/CRM atau sebagai file bulk-import admin TradeWith. Skrip ini hanya menulis file. Baris TradeWith tidak memuat field kontak dan masuk sebagai tier C untuk ditinjau oleh admin. Lihat [Ekspor ke spreadsheet, CRM, atau TradeWith](#ekspor-ke-spreadsheet-crm-atau-tradewith).
- **Skrip sebagai tool MCP.** Server tool lokal yang opsional (MCP, stdio) memungkinkan agent memanggil sebelas skrip sebagai tool. Server ini hanya membaca dan menulis di dalam satu folder proyek, tidak pernah menimpa file, dan tidak mengirim apa pun. Lihat [Menggunakan skrip sebagai tool MCP](#menggunakan-skrip-sebagai-tool-mcp).
- **Plugin.** Claude Code dapat menginstal skill ini sebagai plugin dari repositori ini. ChatGPT dan Codex mendapatkan ZIP plugin yang hanya berisi skill pada setiap rilis; dengan cara inilah skill menjangkau ChatGPT versi web dan mobile. Lihat [Menginstal sebagai plugin](#menginstal-sebagai-plugin).
- Pengujian: 252 → 536 kasus.

v0.2.0 telah menambahkan [perangkat validasi skor](#memvalidasi-skor) dan [paket pasar](#paket-pasar) untuk India, Indonesia, dan Türkiye. Catatan lengkap untuk setiap rilis: [CHANGELOG.md](CHANGELOG.md) (changelog hanya tersedia dalam bahasa Inggris).

---

## Batas keamanan: skill ini tidak mengirim apa pun

Fakta terpenting tentang paket ini: **tidak ada kode di dalamnya yang dapat mengirim pesan.**

- **Hanya draf.** Pekerjaan outreach selalu berakhir pada state `READY_FOR_REVIEW`, dengan `auto_send: false` dan `manual_approval_required: true` pada draf.
- **Manusia menyetujui, sistem eksternal yang mengirim.** Skill ini dapat memindahkan lead melalui `DISCOVERED` → `VERIFIED` → `QUALIFIED` → `MATCH_CANDIDATE` → `READY_FOR_REVIEW` dan tidak lebih jauh. `APPROVED_FOR_OUTREACH` dan tahap selanjutnya menjadi wewenang CRM dan seorang manusia; adapter **menolak** transisi tersebut.
- **Tidak ada kode pengiriman SMTP, Gmail, SES, atau webhook.** Tidak ada penebakan atau pengumpulan massal email maupun nomor telepon pribadi (hanya kanal tingkat perusahaan). Tidak ada upaya melewati CAPTCHA, login, paywall, robots.txt, atau ketentuan layanan.
- **Tidak ada yang dikarang.** MOQ, sertifikasi, pasar ekspor, eksklusivitas, dan kapasitas hanya dicatat apabila dinyatakan oleh sebuah sumber; jika tidak, nilainya tetap `"unknown"`. Kesan mendesak yang tidak berdasar, seperti "seorang pembeli sedang menunggu Anda", dilarang di tingkat template.
- **Tidak ada penilaian hukum.** [`compliance-notes.md`](kbeauty-trade-matchmaker/references/compliance-notes.md) menandai titik-titik yang memerlukan pemeriksaan; dokumen ini tidak pernah menyimpulkan bahwa pengiriman diperbolehkan.

Ini adalah keputusan desain, bukan fitur yang belum ada. *Menyusun draf* dan *mengirim* adalah dua izin yang berbeda, dan paket ini hanya memegang izin yang pertama.

---

## Apa yang dilakukannya

Skill ini mengubah pencarian seharian di Google, LinkedIn, dan direktori pameran dagang menjadi **daftar pendek yang dapat diverifikasi, dengan setiap klaim tertaut ke sumbernya**.

1. **Menemukan (Discover)** kandidat pembeli dan penjual di web publik berdasarkan negara, kategori, OEM/ODM, MOQ, dan sertifikasi.
2. **Memverifikasi (Verify)** setiap klaim terhadap sumber tepercaya, biasanya situs resmi perusahaan itu sendiri, lalu menyimpannya beserta URL dan waktu pengamatannya.
3. **Menormalkan (Normalize)** domain dan nama perusahaan, menggabungkan duplikat, serta menempatkan kebutuhan pembeli dan kapabilitas penjual dalam satu skema.
4. **Menilai (Score)** pembeli dan penjual dengan skrip Python deterministik. Input yang sama dan `--as-of` yang sama menghasilkan output yang sama.
5. **Mencocokkan (Match)** permintaan pembelian (RFQ) dengan penjual: hard filter, kemudian skor berbobot, kemudian semantic rerank yang dibatasi, kemudian penanganan nilai unknown. Hasilnya adalah N teratas **beserta alasan setiap penjual yang ditolak**.
6. **Menyusun draf (Draft)** outreach yang dipersonalisasi hanya dari fakta yang telah diverifikasi, dan berhenti di situ.

---

## Empat mode

Ajukan permintaan kepada agent dalam bahasa sehari-hari. Bentuk di bawah ini adalah singkatan untuk parameter yang diterima setiap mode.

### Buyer Discovery

```
/kbeauty-buyers country="UAE" category="sunscreen" count=30
```

Menemukan distributor, importir, dan pedagang grosir yang menjual K-Beauty di UEA, serta menormalkan jenis perusahaan, merek yang dijual, ketersediaan grosir, sinyal kemitraan, kanal kontak resmi, dan URL bukti.

### Seller Discovery

```
/kbeauty-sellers product="sunscreen" oem_odm=true max_moq=3000 certifications="ISO22716" count=30
```

Menemukan produsen dan merek Korea, serta memverifikasi kapabilitas OEM/ODM, MOQ, sertifikasi, produk utama, dan sinyal ekspor atau kemitraan.

### RFQ Matching

```
/kbeauty-match rfq="#134" top=10
```

Menyaring penjual berdasarkan produk, MOQ, tujuan, sertifikasi, dan kebutuhan private label dalam RFQ, menggabungkan skor kuantitatif dan kualitatif, lalu mengembalikan 10 teratas beserta alasan pengecualian. "Tidak ada kecocokan yang memenuhi syarat", beserta alasannya, merupakan output yang normal.

### Outreach Draft

```
/kbeauty-outreach buyer="ABC Beauty UAE" seller="Seller A" mode="draft"
```

Menulis subjek, isi pesan, dan poin personalisasi hanya dari fakta yang telah diverifikasi. Bukti yang lemah digeneralisasi atau ditandai "perlu verifikasi". **Skill ini tidak mengirim.**

---

## Contoh output

Cuplikan RFQ Matching pada fixture uji yang disertakan. Para penjual di sini fiktif.

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

Perhatikan penjual nomor 9: MOQ-nya tidak dipublikasikan, sehingga hard filter MOQ **dilewati, bukan gagal**. Penjual ini tetap ada dalam daftar dengan skor operasional yang lebih rendah dan baris "Missing" yang eksplisit, alih-alih ditolak diam-diam atau diloloskan diam-diam.

---

## Struktur paket

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

Hanya folder `kbeauty-trade-matchmaker/` yang diinstal. Konten kontrak yang dibutuhkan runtime sudah disertakan dalam `references/data-contract.md` dan `references/output-format.md`.

Tingkat repositori, di luar paket (tidak ikut dikirim):

```
.claude-plugin/marketplace.json   # Claude Code plugin marketplace: one plugin, the package folder
packaging/openai/plugin.json      # Manifest of the ChatGPT/Codex plugin ZIP
tools/build_release.py            # Builds both release ZIPs from the HEAD commit, deterministically
```

---

## Cara kerja penilaian

Skor berasal dari **skrip deterministik**, bukan dari kesan sebuah model.

Pembeli dinilai pada enam dimensi: `kbeauty_korea_fit` 20, `b2b_commercial_role` 20, `sourcing_intent` 25, `market_relevance` 15, `reachability` 10, `evidence_quality` 10. Penjual dinilai pada enam dimensi: `product_fit` 25, `commercial_model` 20, `operational_fit` 20, `compliance_readiness` 15, `export_readiness` 10, `evidence_quality` 10. Setiap dimensi menjumlahkan poin per kriteria dan menormalkannya ke skala 0–100 berdasarkan poin yang berlaku.

Pencocokan menjalankan empat tahap secara berurutan:

1. **Hard filter** `HF-01..HF-08`, ditambah rendering gate `HF-00`. Kegagalan tidak menghentikan proses lebih awal; **setiap** alasan pengecualian dikumpulkan.
2. **Skor berbobot:** `product_fit` 0.30 + `model_fit` 0.20 + `operation_fit` 0.15 + `compliance_fit` 0.15 + `market_fit` 0.10 + `evidence_quality` 0.10. Ini adalah enam dimensi milik penjual itu sendiri yang dinilai ulang terhadap RFQ, sehingga seorang penjual tidak pernah dinilai dengan dua rubrik yang berbeda.
3. **Semantic rerank yang dibatasi.** Satu-satunya langkah yang disusun oleh model. Langkah ini dibatasi, memerlukan alasan yang mengutip bukti, dan tidak pernah dapat menghidupkan kembali kandidat yang gagal hard filter.
4. **Penanganan nilai unknown.**

Tiga aturan paling penting:

- **Unknown bukan nol.** Nilai yang tidak diketahui memperoleh 30% dari nilai maksimum kriteria tersebut (`neutral_base` 50 × `penalty_factor` 0.6). Menolak kandidat semata-mata karena suatu nilai tidak diketahui adalah terlarang, demikian pula menebak nilai untuk menghindari penalti.
- **Tidak membaca jam sistem.** Semua perhitungan waktu menggunakan `--as-of`, sehingga input yang sama menghasilkan angka yang sama kapan pun Anda menjalankannya.
- **Confidence bukan kualitas.** Confidence menjawab pertanyaan "seberapa jauh record ini dapat dipercaya", ditampilkan sebagai `HIGH ≥ 0.75 / MEDIUM ≥ 0.5 / LOW`, dan tidak pernah membaca skor kualifikasi.

Pembeli yang sama sekali tidak memiliki bukti K-Beauty tetap dinilai dan diperingkat, tetapi tidak pernah dapat ditandai sebagai qualified, sekuat apa pun dimensi lainnya. Ambang kualifikasi default adalah angka tetap 70; mode persentil juga telah diimplementasikan. Setiap angka yang dapat disetel berada di `schemas/scoring.config.json`.

## Cara kerja bukti

Satuan output bukanlah "sebuah perusahaan", melainkan **sebuah klaim beserta buktinya**. Satu item bukti memuat satu `claim`, URL sumbernya, tier sumber, tanggal konten (`source_date`), waktu pengamatan (`observed_at`), apakah klaim itu fakta atau inferensi, serta kutipan singkat.

| Tier | Sumber | Poin |
|---|---|---|
| 1 | Situs resmi perusahaan | 100 |
| 2 | Direktori resmi pameran dagang, asosiasi, pemerintah, atau lembaga perdagangan; catatan internal | 82 |
| 3 | Profil LinkedIn dan media sosial resmi | 64 |
| 4 | Direktori pihak ketiga dan siaran pers yang bereputasi | 46 |
| 5 | Unggahan komunitas dan blog (hanya sebagai sinyal pendukung) | 20 |

Poin dikalikan dengan usia konten: dalam 90 hari 1.00, dalam satu tahun 0.92, dalam dua tahun 0.80, lebih lama 0.60, tanpa tanggal 0.85. Usia selalu diukur dari `source_date`, bukan dari `observed_at`, karena waktu kita membaca sebuah halaman tidak mengatakan apa pun tentang usia kontennya.

Dua kondisi sengaja dibedakan:

- **`unverified`**: record memiliki bukti, tetapi tidak ada klaim material yang bersumber resmi (tier 1). Record ini tetap dinilai, diperingkat, dan ditampilkan dengan penanda `— unverified`. Record ini **tidak** dikecualikan.
- **Tidak ada bukti sama sekali** untuk klaim material mana pun: record dipindahkan ke `excluded[]` sebelum penilaian, dengan alasan yang dinyatakan ("situs tidak dapat dibuka" atau "sudah dibaca, tetapi tidak ada klaim material"). Mengisi daftar peringkat dengan perusahaan tanpa bukti adalah kegagalan yang justru dicegah oleh aturan ini.

Penyimpanan dibuat minimal: klaim, URL, waktu pengamatan, dan kutipan singkat; tidak pernah halaman utuh atau profil pribadi yang tidak diperlukan. Sumber yang saling bertentangan dicatat dalam `conflicts[]` agar dapat dilihat oleh manusia, tidak pernah diselesaikan secara diam-diam, dan konflik menurunkan confidence.

---

## Memvalidasi skor

Skor di sini dapat direproduksi, tetapi belum ada yang memeriksanya terhadap penilaian seorang manusia. Dua skrip mandiri, yang ditambahkan di v0.2.0, memungkinkan Anda menjalankan pemeriksaan itu. Keduanya tidak mengubah skor apa pun.

```bash
cd kbeauty-trade-matchmaker

# 1. Make a blind sheet from a scored run. No score, rank or qualified flag; rows in a fixed shuffled order.
python3 scripts/make_review_sheet.py --input out/buyers.scored.json --include-excluded --output out/review.csv

# 2. A trade operator fills in verdict (accept / reject / unsure), a reason_code for each reject,
#    their role, and the date. Then:
python3 scripts/acceptance_report.py --scored out/buyers.scored.json --reviews out/review.csv --as-of 2026-09-19 --pretty
```

Laporan ini menyajikan proporsi perusahaan yang ditinjau dan diterima oleh operator, secara keseluruhan maupun dipilah berdasarkan penanda `qualified`, rentang skor, negara, dan alasan penolakan. Laporan ini menunjukkan precision dan recall yang akan dihasilkan oleh setiap ambang dari 50 hingga 90. Untuk skor total dan masing-masing dari enam dimensi, laporan ini menunjukkan seberapa baik skor memisahkan perusahaan yang diterima dari yang ditolak, serta berapa banyak nilai berbeda yang dihasilkan dimensi tersebut. Laporan ini juga mendaftar perusahaan yang dikecualikan tetapi sebenarnya akan diterima oleh operator.

Di bawah 30 tinjauan yang sudah diputuskan, laporan menandai dirinya sendiri `insufficient_sample` dan menyatakan bahwa ia tidak dapat membenarkan perubahan bobot atau ambang. Laporan menolak lembar dengan verdict yang tidak dikenal, penolakan tanpa alasan, duplikat yang saling bertentangan, proses yang dinilai dengan `score_version` yang berbeda, serta catatan yang memuat alamat email atau nomor telepon. Peninjau diidentifikasi berdasarkan peran, tidak pernah berdasarkan nama.

[`calibration-notes.md`](kbeauty-trade-matchmaker/references/calibration-notes.md) §7 menjelaskan cara menyusun sampel (sedikitnya dua negara dan dua kategori, 100 hingga 200 perusahaan yang ditinjau) dan hasil apa yang akan menjawab setiap pertanyaan kalibrasi yang masih terbuka. Ini hanya mengukur Human Acceptance Rate (tingkat penerimaan oleh manusia). Apakah lead yang dihubungi berubah menjadi RFQ memerlukan data hasil dari lapisan aplikasi.

## Paket pasar

v0.2.0 menambahkan India, Indonesia, dan Türkiye. Tidak ada aturan penilaian yang berubah; sebuah fixture uji baru (tujuan Indonesia, halal diwajibkan) menunjukkan bahwa rubrik yang ada sudah menanganinya.

| | India | Indonesia | Türkiye |
|---|---|---|---|
| Bahasa pencarian pembeli | Bahasa Inggris terlebih dahulu, ditambah bahasa Hindi | Bahasa Indonesia | Bahasa Turki |
| Aturan masuk pasar yang digunakan dalam pencocokan | Registrasi impor CDSCO | Notifikasi BPOM; sertifikasi halal wajib untuk kosmetik (bergantung pada tanggal `--as-of`) | Notifikasi TİTCK melalui ÜTS |
| Bentuk badan hukum yang dihapus dari nama perusahaan | `Pvt Ltd`, `Private Limited`, `LLP` | `PT`, `CV`, `Tbk` | `A.Ş.`, `Ltd. Şti.`, `San. ve Tic.` |
| Blok pemberitahuan | `IN.corporate_email`, `IN.partnership_form` | `ID.corporate_email`, `ID.partnership_form` | `TR.corporate_email`, `TR.partnership_form` |

- Registrasi dicatat per pasar di `regulatory_registrations` dan dinilai dengan kriteria pasar tujuan yang sudah ada: terdaftar lebih tinggi daripada sedang diproses, yang lebih tinggi daripada tidak dipublikasikan.
- `Helal`, `Sertifikat Halal`, dan `हलाल` menjadi token `HALAL`, dengan lembaga pemberi sertifikat dan cakupannya dicantumkan dalam catatan. Pengakuan di Indonesia berlaku per lembaga dan per cakupan, sehingga sertifikat yang diakui untuk pangan tidak mencakup kosmetik.
- Agent tidak pernah menambahkan sertifikasi wajib yang tidak dinyatakan dalam RFQ. Agent mengangkatnya sebagai risiko untuk diputuskan oleh manusia.
- Sumber yang belum digunakan dalam proses langsung ditandai "not yet field-tested" (belum diuji di lapangan) di [`buyer-discovery.md`](kbeauty-trade-matchmaker/references/buyer-discovery.md). Baris-baris kepatuhan mendaftar apa yang harus diperiksa oleh manusia; baris-baris itu bukan kesimpulan hukum.

---

## Membandingkan dua proses

Jalankan pencarian atau RFQ yang sama lagi sebulan kemudian, dan `diff_runs.py` memberi tahu Anda apa yang bergeser. Skrip ini membaca dua proses yang sudah selesai dan tidak mengubah skor apa pun.

```bash
cd kbeauty-trade-matchmaker
python3 scripts/diff_runs.py --before out/buyers.2026-09-12.json --after out/buyers.2026-10-12.json --pretty --output out/diff.json
```

Diff ini mendaftar perusahaan yang baru dan yang hilang, perusahaan yang menjadi dikecualikan atau kembali masuk, pengecualian yang aturannya berubah, dan untuk setiap perusahaan, perubahan pada skor, peringkat (proses pencocokan), dimensi, penanda qualified, confidence, dan baris Missing. Diff ini juga menandai perubahan pada `as_of`, ambang, query, atau bobot.

- Record dipasangkan berdasarkan id, lalu melalui `merged_from`. Perusahaan yang digabungkan ke perusahaan lain oleh dedupe terbaca sebagai `merged_into`, bukan sebagai lead yang hilang. Tidak ada hal lain yang ditebak, sehingga perubahan nama tanpa `merged_from` tampil sebagai hilang + baru.
- Pada proses pencocokan, "hilang" berarti "tidak tercantum". Seorang penjual dapat keluar dari daftar karena ambang atau batas `--top`; diff menyatakan hal itu.
- Skrip ini menolak, alih-alih memperkirakan, dua proses yang dinilai dengan `score_version` yang berbeda, proses pembeli terhadap proses penjual, proses discovery terhadap proses pencocokan, serta proses pencocokan untuk RFQ yang berbeda.
- Skrip ini tidak menyalin kanal kontak, bukti, maupun situs web. Hanya nama perusahaan, domain, dan id aturan yang ikut terbawa.

Detail: [`data-contract.md`](kbeauty-trade-matchmaker/references/data-contract.md) §9.4.

## Antrean pemeriksaan ulang

Bukti menua. `stale_evidence.py` membaca record tersimpan dan mendaftar apa yang perlu dibaca ulang, dimulai dari yang paling mendesak. Skrip ini tidak mengambil apa pun dan tidak mengubah record maupun skor.

```bash
cd kbeauty-trade-matchmaker
python3 scripts/stale_evidence.py --input out/buyers.scored.json --as-of 2026-09-19 --top 20 --pretty
```

- `--as-of` wajib: usia diukur hingga tanggal pemeriksaan ulang, dan jam sistem tidak pernah dibaca. `--top N` mendaftar N record pertama; ringkasan tetap menghitung semuanya.
- Alasan, dari yang paling mendesak: situs tidak dapat dibuka, record ditandai usang, bukti melewati ambang usang (730 hari), sumber ditandai usang, konflik yang belum terselesaikan, menua (lebih dari satu tahun), tanpa tanggal. Klaim material tanpa bukti terkini dilaporkan tersendiri.
- Batas usia diambil dari `scoring.config.json`, sehingga antrean dan skor sepakat tentang arti "lama". Halaman lama tidak dimasukkan ke antrean jika klaim yang sama sudah memiliki sumber terkini. Halaman tanpa tanggal dianggap terkini selama pembacaan terakhirnya masih terkini.
- Skrip ini menerima proses bernilai, golden bundle, output dedupe, input pencocokan, daftar record, atau satu record. Match-result ditolak; berikan input pencocokannya sebagai gantinya.

Cara mengerjakan antrean: [`evidence-policy.md`](kbeauty-trade-matchmaker/references/evidence-policy.md) §5.6.

## Ekspor ke spreadsheet, CRM, atau TradeWith

`export_leads.py` menulis lead dari satu proses discovery bernilai sebagai file yang diimpor oleh seseorang. **Skrip ini hanya menulis file.** Skrip ini tidak membuka koneksi apa pun dan tidak pernah melakukan posting, upload, atau pengiriman.

```bash
cd kbeauty-trade-matchmaker
python3 scripts/export_leads.py --input out/buyers.scored.json --output out/leads.csv                     # Spreadsheet / CRM
python3 scripts/export_leads.py --input out/buyers.scored.json --format tradewith-json --output out/tw.json # TradeWith bulk-import body
python3 scripts/export_leads.py --input out/buyers.scored.json --format tradewith-csv --output out/tw.csv   # TradeWith admin import page
```

| `--format` | Untuk | Isinya |
|---|---|---|
| `csv` (default) | pembeli, penjual | Kolom tetap: skor, dimensi, status, kanal tingkat perusahaan, baris Missing, `score_version` |
| `tradewith-json` | pembeli | Body `{"buyers": [...]}` untuk endpoint bulk-import admin TradeWith |
| `tradewith-csv` | pembeli | Lima kolom yang dibaca halaman impor pembeli di admin (`sourceId, companyName, country, website, industry`). Format ini membuang field asal-usul dan pencocokan; utamakan `tradewith-json` |

Secara default hanya record yang qualified yang diekspor; tambahkan `--include-unqualified` atau `--min-score N` untuk mengubahnya. Perusahaan yang sudah tutup atau tidak dapat dijangkau, serta `excluded[]`, tidak pernah diekspor.

**Apa yang terjadi di TradeWith.** Baris masuk **belum ditinjau, sebagai tier C**: ekspor tidak menetapkan quality tier, dan tier C secara default tidak diikutkan dalam pencocokan pembeli. Seorang admin meninjau setiap baris, menaikkannya ke tier A atau B, dan menambahkan tag-nya. Sampai saat itu, baris tersebut tidak ditawarkan kepada penjual.

- `contactName`, `contactEmail`, dan `contactPhone` **tidak pernah diisi**, bahkan dengan mailbox peran perusahaan seperti `sales@`. `contactEmail` yang terisi akan menandai baris sebagai kontak terverifikasi, dan impor ulang akan menimpa alamat yang telah dikoreksi oleh admin.
- `sourceId` adalah `kbtm:<company domain>`, sehingga mengimpor proses berikutnya akan memperbarui baris yang sama alih-alih menambahkan baris baru.
- `originalSource` mencatat paket, `score_version`, `as_of`, id record, dan apakah record tersebut usang. `social` hanya berisi halaman perusahaan di LinkedIn, tidak pernah profil perorangan.

**CSV generik** hanya menyimpan kanal tingkat perusahaan. Email hanya dipertahankan sebagai mailbox peran (`info@`, `sales@` …) pada domain milik perusahaan itu sendiri; profil anggota LinkedIn ditahan. Setiap nilai yang diekspor melewati pemindaian data pribadi, dan satu temuan saja akan menolak seluruh ekspor. Sel yang akan dijalankan spreadsheet sebagai formula diberi apostrof di depannya.

Detail: [`data-contract.md`](kbeauty-trade-matchmaker/references/data-contract.md) §9.6.

## Menggunakan skrip sebagai tool MCP

`scripts/mcp_server.py` adalah server MCP opsional melalui stdio yang hanya menggunakan standard library. Server ini menyediakan sebelas skrip sebagai tool, untuk runtime yang memanggil tool alih-alih shell: `normalize_company`, `dedupe_companies`, `score_buyer`, `score_seller`, `score_match`, `validate_output`, `make_review_sheet`, `acceptance_report`, `diff_runs`, `stale_evidence`, dan `export_leads`. Setiap tool menjalankan skripnya dengan sebagian flag-nya, dan memberikan hasil yang sama dengan command line. Adapter data internal tidak disediakan.

- **`--root DIR` wajib**: satu-satunya folder tempat tool boleh membaca dan menulis. Server menolak `/`, direktori home Anda, atau induknya. `../` dan symlink tidak dapat membawa keluar.
- **`as_of` wajib pada setiap pemanggilan.** Server tidak pernah menyediakan tanggal.
- **Tidak ada penimpaan.** `output_path` harus berupa file `.json` atau `.csv` baru di dalam root, di luar paket skill, dan tidak berada di folder tersembunyi. Satu proses lengkap biasanya lebih besar daripada batas inline 32,768 byte, jadi berikan `output_path` untuk proses nyata.
- Tidak ada yang mengirim atau mengambil apa pun. Skrip dijalankan dengan `python3 -I` dan tanpa variabel `TRADEWITH_*`.

**Claude Code.** Plugin menjalankan server untuk Anda (lihat [Menginstal sebagai plugin](#menginstal-sebagai-plugin)). Setelah `install.sh`, tambahkan secara manual:

```bash
claude mcp add --transport stdio kbtm -- python3 /abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py --root /abs/path/to/project
```

**Codex CLI.** Di `~/.codex/config.toml`; pastikan `tool_timeout_sec` lebih besar daripada `--tool-timeout` server (default 120 detik):

```toml
[mcp_servers.kbtm]
command = "python3"
args = ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "/abs/path/to/project"]
tool_timeout_sec = 180
```

**Cursor.** Di `.cursor/mcp.json` (proyek) atau `~/.cursor/mcp.json` (global):

```json
{"mcpServers": {"kbtm": {"type": "stdio", "command": "python3",
  "args": ["/abs/path/kbeauty-trade-matchmaker/scripts/mcp_server.py", "--root", "${workspaceFolder}"]}}}
```

Konfigurasi klien telah diperiksa terhadap dokumentasi masing-masing vendor pada 2026-09-19. Aturan lengkap, versi protokol, dan flag yang tidak disertakan: [`runtime-adapters.md`](kbeauty-trade-matchmaker/references/runtime-adapters.md) §5.6.

---

## Instalasi

### claude.ai, tanpa terminal

1. Unduh [`kbeauty-trade-matchmaker.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker.zip) dari rilis terbaru. Biarkan tetap dalam bentuk ZIP.
2. Di claude.ai, buka Settings → Capabilities dan pastikan code execution aktif. Kemudian buka Customize → Skills, pilih Upload skill, masukkan file ZIP, lalu simpan.
3. Aktifkan web search di chat baru dan ajukan permintaan dalam bahasa sehari-hari, misalnya "Find 5 K-Beauty distributors in the UAE that carry sunscreen." Lima perusahaan membutuhkan waktu sekitar 10 hingga 15 menit.

Telah diverifikasi secara menyeluruh pada paket berbayar pada 2026-09-14: skill dipanggil tanpa disebutkan namanya, menjalankan web search dan pengambilan halaman di dalam skill, serta mengeksekusi skrip penilaian di sandbox. Juga diverifikasi pada paket gratis pada 2026-09-19 dengan permintaan kecil (3 perusahaan): skill berjalan menyeluruh, termasuk skrip penilaian, tanpa terkena batas penggunaan. Permintaan yang lebih besar masih bisa terkena batas paket gratis. Panduan langkah demi langkah: [https://kbeauty.tradewith.kr/install-ko](https://kbeauty.tradewith.kr/install-ko) (catatan: panduan ini hanya tersedia dalam bahasa Korea). Mengalami kendala? [Kirim pesan kepada saya di LinkedIn](https://www.linkedin.com/in/hm-choi).

### Menginstal sebagai plugin

Pilih **satu** cara instalasi per runtime. Plugin ditambah salinan `install.sh` di runtime yang sama akan memuat dua skill yang sama-sama terpicu oleh permintaan yang sama.

**Claude Code.** Repositori ini adalah marketplace plugin dengan satu plugin, yaitu folder paket itu sendiri:

```
/plugin marketplace add choihyeonmuk/kbeauty-trade-matchmaker
/plugin install kbeauty-trade-matchmaker@kbeauty-trade-matchmaker
```

Skill-nya adalah `/kbeauty-trade-matchmaker:kbeauty-trade-matchmaker`, atau terpicu secara implisit. Plugin juga menjalankan server MCP yang disertakan (`kbtm`) dengan folder proyek Anda sebagai root-nya; server ini memerlukan `python3` di `PATH`. Jalankan Claude Code di dalam folder proyek: jika dijalankan dari direktori home Anda, server menolak untuk mulai dan tampil sebagai gagal. Perbarui nanti dengan `/plugin marketplace update kbeauty-trade-matchmaker`.

**ChatGPT dan Codex.** Setiap rilis menyertakan aset kedua, [`kbeauty-trade-matchmaker-plugin.zip`](https://github.com/choihyeonmuk/kbeauty-trade-matchmaker/releases/latest/download/kbeauty-trade-matchmaker-plugin.zip). Aset ini **hanya berisi skill**, tanpa server MCP: OpenAI menandai plugin yang mendeklarasikan server MCP sebagai khusus desktop, dan ZIP ini ada untuk menjangkau ChatGPT **versi web dan mobile**.

1. Ekstrak ke `~/.codex/plugins/kbeauty-trade-matchmaker`.
2. Tambahkan entri ini ke array `plugins` di `~/.agents/plugins/marketplace.json` (gabungkan secara manual jika file sudah ada; path-nya relatif terhadap `~`):

```json
{"name": "kbeauty-trade-matchmaker",
 "source": {"source": "local", "path": "./.codex/plugins/kbeauty-trade-matchmaker"},
 "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
 "category": "Business & Operations"}
```

3. Mulai ulang aplikasi desktop ChatGPT dan instal dari Plugins, atau jalankan `/plugins` di Codex CLI.
4. Untuk ChatGPT versi web dan mobile, admin workspace memublikasikan plugin ke workspace. Pencantuman di direktori publik bergantung pada tinjauan OpenAI atas pengajuan.
5. Jalankan analisis dalam mode **Work**. OpenAI tidak mendokumentasikan bahwa mode Chat biasa menjalankan skrip yang disertakan. Di tempat skrip tidak dapat dijalankan, skill menyatakannya dan mengembalikan bukti tanpa skor; skill tidak pernah memperkirakan skor secara manual.

Ekstensi IDE Codex tidak mendukung plugin; gunakan `install.sh --runtime codex` di sana. Tata letak ZIP dan entri marketplace mengikuti dokumentasi OpenAI sebagaimana diperiksa pada 2026-09-19; apakah skrip berjalan dalam mode Work di web dan mobile merupakan inferensi, bukan hasil pengujian. Detail: [`runtime-adapters.md`](kbeauty-trade-matchmaker/references/runtime-adapters.md) §5.5.

### Installer untuk Claude Code dan Codex

`install.sh` adalah POSIX `sh`, tidak menggunakan jaringan maupun sudo, dan tidak menulis apa pun di luar direktori target. **Jalankan `--dry-run` terlebih dahulu.**

```bash
git clone https://github.com/choihyeonmuk/kbeauty-trade-matchmaker.git
cd kbeauty-trade-matchmaker

sh kbeauty-trade-matchmaker/install.sh --dry-run          # Print the plan, change nothing
sh kbeauty-trade-matchmaker/install.sh --verify           # Claude Code: symlink into ~/.claude/skills/, then run the tests
sh kbeauty-trade-matchmaker/install.sh --runtime codex    # Codex: symlink into ~/.agents/skills/
```

| Opsi | Arti |
|---|---|
| *(default)* | **Symlink** ke `$HOME/.claude/skills/kbeauty-trade-matchmaker`; perubahan pada sumber langsung berlaku |
| `--runtime claude\|codex` | Memilih runtime. `claude` (default) menggunakan `.claude/skills/`, `codex` menggunakan `.agents/skills/`. `--codex` adalah singkatan dari `--runtime codex` |
| `--copy` | Menginstal salinan independen alih-alih symlink |
| `--project DIR` | Menginstal di bawah `DIR` alih-alih `$HOME`, sehingga skill ikut bersama repositori tersebut |
| `--force` | Menimpa direktori yang sudah ada yang tidak dibuat oleh skrip ini (ditolak secara default) |
| `--verify` | Menjalankan `tests/run_tests.py` setelah instalasi; keluar dengan kode 1 jika gagal |
| `--dry-run` | Hanya menampilkan rencana |

Menjalankan ulang aman dilakukan. Kode keluar: `0` berhasil, `1` instalasi ditolak atau verifikasi gagal, `2` kesalahan penggunaan.

### Claude Code dan claude.ai

```bash
sh kbeauty-trade-matchmaker/install.sh                                  # Personal: every project on this machine
sh kbeauty-trade-matchmaker/install.sh --project /path/to/your-project  # Project scope
```

- **Folder harus bernama `kbeauty-trade-matchmaker`**, sesuai dengan `name` di `SKILL.md`.
- **`SKILL.md` sudah sepenuhnya sesuai standar; menambahkan key frontmatter adalah kemunduran, bukan perbaikan.** [Standar terbuka Agent Skills](https://agentskills.io/specification) hanya mengenali tepat enam field: `name` dan `description` (wajib), serta `license`, `compatibility`, `metadata` yang opsional dan `allowed-tools` yang masih eksperimental. Di luar Claude Code (claude.ai, Skills API), hanya keenam field tersebut yang diterima, sehingga satu key khusus Claude Code saja akan menggagalkan upload. Paket ini hanya memuat dua key yang wajib. Informasi versi berada di badan `SKILL.md`.
- Antarplatform tidak tersinkronisasi. Claude Code (filesystem), claude.ai (upload zip di Customize → Skills), dan Skills API (`/v1/skills`) masing-masing memerlukan upload folder secara terpisah.

### OpenAI Codex dan ChatGPT

Folder dapat dipindahkan tanpa perubahan, tetapi Codex tidak membaca `.claude/skills`. Root skill-nya adalah `.agents/skills`.

```bash
sh kbeauty-trade-matchmaker/install.sh --runtime codex                                # User scope: ~/.agents/skills/
sh kbeauty-trade-matchmaker/install.sh --runtime codex --project /path/to/your-repo   # Repository scope
```

- Codex memindai `.agents/skills` di setiap direktori mulai dari direktori kerja hingga root repositori. `~/.codex/skills` sudah deprecated tetapi masih didukung; gunakan `~/.agents/skills` untuk instalasi baru.
- Panggil secara eksplisit dengan `$kbeauty-trade-matchmaker` atau `/skills` di CLI dan ekstensi IDE, atau `@` di ChatGPT. Pemanggilan implisit ditentukan oleh `description`.
- Folder skill mandiri hanya terlihat di **aplikasi desktop ChatGPT, Codex CLI, dan ekstensi IDE**. ChatGPT **versi web dan mobile** memerlukan ZIP plugin; lihat [Menginstal sebagai plugin](#menginstal-sebagai-plugin).
- `AGENTS.md` bukan cara untuk menginstal skill. Itu adalah fitur Codex terpisah untuk instruksi repositori yang selalu aktif.

> **Diperiksa terhadap dokumentasi resmi pada 2026-09-13.** Jika ada hal di sini yang tidak sesuai dengan dokumentasi terkini, dokumentasilah yang benar.
> [Spesifikasi Agent Skills](https://agentskills.io/specification) · [Ikhtisar Anthropic Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) · [Skill Claude Code](https://code.claude.com/docs/en/skills) · [OpenAI: membangun skill](https://learn.chatgpt.com/docs/build-skills)
>
> Prosedur upload workspace ChatGPT, serta apakah platform ChatGPT selain desktop dapat mengeksekusi skrip `python3` yang disertakan, **tidak** dapat diverifikasi; halaman bantuan OpenAI yang relevan mengembalikan HTTP 403 untuk permintaan otomatis. Jika skrip tidak dapat dijalankan, skill akan beralih ke bukti tanpa skor (lihat `references/runtime-adapters.md` §4).

### Memeriksa instalasi

Ada dua hal yang dapat rusak secara terpisah. Tiga baris berikut hanya memeriksa **kodenya saja**:

```bash
python3 kbeauty-trade-matchmaker/scripts/validate_output.py --version
python3 kbeauty-trade-matchmaker/adapters/tradewith_adapter.py --version
python3 kbeauty-trade-matchmaker/tests/run_tests.py
```

Untuk memeriksa bahwa **runtime mengenali skill**, cari `kbeauty-trade-matchmaker` di daftar skill Claude Code, atau di daftar `/skills` milik Codex. Jika tidak ada, pastikan folder berada langsung di bawah root skills, bernama `kbeauty-trade-matchmaker`, baris pertama `SKILL.md` persis `---`, dan tidak ada scope dengan prioritas lebih tinggi yang memiliki skill dengan nama yang sama. Mulai ulang Codex setelah melakukan perubahan.

---

## Menjalankan pengujian

```bash
cd kbeauty-trade-matchmaker
python3 tests/run_tests.py       # Exit 0 when everything passes, 1 otherwise
python3 tests/run_tests.py -v    # One line per case, not just failures
```

Runner hanya menggunakan standard library, menemukan fixture secara relatif terhadap lokasinya sendiri, meneruskan `--as-of 2026-09-12` ke setiap skrip, dan membandingkan output dengan fixture yang diharapkan **byte demi byte**. Sebelum kasus-kasus fixture, runner memeriksa skema itu sendiri: bahwa skema dapat di-parse, bahwa setiap `$ref` dapat di-resolve, bahwa tidak ada keyword yang tidak didukung, bahwa `$defs` bersama konsisten di seluruh file, dan bahwa versi yang tertanam sesuai dengan `scoring.config.json`.

Fase `plugins` membaca manifest dan builder di tingkat repositori, sehingga jumlah penuh 536 berlaku untuk checkout repositori; salinan yang terinstal melaporkan dua SKIP (fase `plugins` dan satu kasus MCP). Fase ini menjalankan builder rilis di repositori git sekali pakai, sehingga pekerjaan yang belum di-commit tidak memengaruhi hasilnya.

Skrip juga dapat dijalankan secara langsung. JSON dikirim ke `stdout` dan setiap diagnostik ke `stderr`, sehingga penggunaan pipe aman.

```bash
python3 scripts/score_buyer.py \
    --input tests/fixtures/buyers.golden.json \
    --query tests/fixtures/query-buyer-uae-kbeauty.json \
    --as-of 2026-09-12 --pretty

python3 scripts/score_match.py --input tests/fixtures/match-134.input.json --as-of 2026-09-12 --pretty
```

---

## Panduan cepat adapter: hanya file, tanpa backend

Seluruh alur kerja sudah dapat berjalan saat ini **tanpa API internal apa pun**. Adapter memiliki satu antarmuka dan dua backend, `file` dan `http`, dengan `file` sebagai default. Backend ini tidak memerlukan kredensial, jaringan, maupun layanan.

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

Direktori data adalah pohon file JSON biasa di mana **nama file adalah id**, sehingga menjalankan ulang dengan input yang sama akan menimpa file yang sama dan hasilnya tetap stabil byte demi byte. Simpan direktori ini di luar paket, dan di luar version control jika berisi data perusahaan nyata; `.gitignore` sudah mengecualikan `tradewith-data/`.

Ketika API tersedia, cukup satu flag yang berubah. Skor, skema, dan output tidak berubah.

```bash
export TRADEWITH_BASE_URL='https://api.tradewith.example/v1'
export TRADEWITH_TOKEN='...'       # Environment only; a command-line token lands in shell history
python3 adapters/tradewith_adapter.py --backend http get-rfq --id 134
```

Environment variable dibaca pada saat pemanggilan, bukan pada saat import. Tidak ada yang mengakses jaringan kecuali `--backend http` disebutkan secara eksplisit, tidak ada fallback diam-diam dari API ke file, dan token tidak pernah muncul dalam pesan error, peringatan, atau dokumen yang disimpan. Detailnya ada di `adapters/tradewith_adapter.md`.

---

## Keputusan yang masih terbuka

Enam pertanyaan masih terbuka. Masing-masing memiliki nilai default dalam implementasi ini, dan setiap default dapat diubah di satu tempat.

| # | Pertanyaan | Default di sini | Tempat mengubahnya |
|---|---|---|---|
| 1 | Apakah ada API internal untuk seller/RFQ, atau mulai dengan file? | **Keduanya.** Backend file secara default; `--backend http` ketika API tersedia | `TRADEWITH_BACKEND` / `--backend` |
| 2 | Menyimpan lead langsung ke database layanan, atau ke staging? | **Staging.** Skill hanya menulis ke `POST /research/leads` (atau `<data-dir>/leads/`); promosi merupakan persetujuan di lapisan aplikasi | Target penulisan adapter |
| 3 | Ambang tetap 70, atau persentil per kategori? | **Tetap 70.** Mode persentil sudah diimplementasikan; mode yang digunakan selalu dicatat di `summary` | `thresholds` di `scoring.config.json`, `--threshold`, `--threshold-mode` |
| 4 | Sejauh mana menyimpan alamat berbasis peran (`info@`, `sales@`) dan kontak bernama? | **Hanya kanal tingkat perusahaan.** Tidak ada penebakan atau pengumpulan kontak pribadi di dalam kode | `evidence-policy.md`, `compliance-notes.md` |
| 5 | Cakupan crawling dan pemeriksaan ketentuan layanan untuk direktori? | **Menghormati robots dan ToS, tidak melewati apa pun.** Akses yang diblokir tetap `"unknown"` | `compliance-notes.md`, daftar sumber discovery |
| 6 | Apa yang boleh disampaikan dalam outreach ke penjual ketika tidak ada RFQ? | **Template terpisah tanpa RFQ.** Menyiratkan permintaan yang tidak ada adalah terlarang | `templates/seller_outreach.md`, `outreach-guidelines.md` |

---

## Versi

| Versi | Nilai | Menjelaskan | Lokasi |
|---|---|---|---|
| `skill_version` | `0.3.0` | Paket: prompt, referensi, skrip, template, pengujian | Badan `SKILL.md`, `match-result.skill_version`, kedua manifest plugin |
| `schema_version` | `0.1.0` | Kontrak bentuk: nama field, enum, daftar field wajib | Setiap dokumen, `schemas/*.json` |
| `score_version` | `kbtm-score-0.1.0` | Rubrik: bobot, kriteria, sinyal, penalti, ambang, hard filter | `scoring.config.json`, setiap dokumen yang dinilai |

```bash
python3 scripts/validate_output.py --version
```

Ketika rubrik berubah, skor yang tersimpan menurut definisinya sudah usang. Karena record mentah menyimpan bukti, query surface, dan `as_of`-nya, skor dapat dihitung ulang tanpa crawling ulang. Membandingkan atau memeringkat hasil dari `score_version` yang berbeda dalam satu daftar adalah terlarang, dan `validate_output.py` akan mendeteksinya.

---

## Persyaratan

- `python3` 3.9–3.14, hanya standard library.
- Shell POSIX untuk installer.
- Mode discovery memerlukan kemampuan web search dan pengambilan halaman dari runtime. Skrip sepenuhnya offline, sehingga pengujian dan penilaian ulang berjalan tanpa jaringan.
- Integrasi data internal bersifat opsional; backend `file` milik adapter tidak memerlukan apa pun.

---

## Penanganan data

Repositori ini tidak berisi data perusahaan nyata maupun informasi pribadi. Fixture menggunakan perusahaan fiktif pada domain `.example`. Ketika Anda mulai menambahkan data operasional, pastikan `tradewith-data/`, `.env`, dan `out/` tetap diabaikan seperti yang sudah diatur oleh `.gitignore`, dan periksa secara berkala bahwa data yang Anda simpan tetap terbatas pada klaim, URL, waktu pengamatan, dan kutipan singkat.

**Ini bukan nasihat hukum.** Memeriksa aturan pemasaran langsung di setiap yurisdiksi adalah tugas manusia; paket ini menandai titik-titik yang memerlukan pemeriksaan tersebut.
