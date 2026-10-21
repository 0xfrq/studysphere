# Rencana eksperimen StudySphere AI

Dokumen ini menjawab satu pertanyaan: **bagaimana membuktikan bahwa planner kecil + pengambilan data bertahap benar-benar lebih hemat, tanpa mengorbankan kualitas jawaban?**

Ini bukan daftar klaim. Ini protokol yang harus dijalankan, metrik yang harus diukur, dan tabel yang harus diisi dengan hasil nyata. Selama eksperimen belum dijalankan, semua angka di proposal adalah **target**, bukan hasil.

---

## 1. Pertanyaan penelitian

| Kode | Pertanyaan | Hipotesis yang diuji |
|---|---|---|
| RQ1 | Bisakah model 270M memilih informasi sensor minimum dengan benar? | Planner Gemma 3 270M + LoRA mencapai exact-set match dan F1 yang cukup untuk dipakai, jauh di atas tebakan dan mendekati planner oracle. |
| RQ2 | Berapa penghematan cost-aware sensing dibanding raw dan semantic? | Cost-aware menurunkan sensor read, byte MQTT, dan latency dibanding raw dan semantic, pada task accuracy yang setara. |
| RQ3 | Apakah penghematan itu menurunkan kualitas jawaban? | Responder tetap menghasilkan jawaban yang grounded; penurunan utama hanya pada kasus informasi yang sengaja tidak diambil. |
| RQ4 | Di mana titik trade-off terbaik? | Ada kurva Pareto accuracy vs cost; budget 1–2 source sudah memberi sebagian besar manfaat. |
| RQ5 | Apakah model kecil cukup untuk dua peran? | Planner 270M layak; responder 1–2B lebih baik daripada memaksa 270M melakukan keduanya. |

Pertanyaan turunan yang harus dijawab di diskusi:

- Kesalahan planner mana yang paling mahal: salah task, kurang informasi, atau meminta informasi berlebih?
- Apakah progressive retrieval benar-benar mengurangi pembacaan, atau hanya menambah round-trip?
- Apakah cache menyebabkan jawaban basi yang berbahaya?
- Apakah model tetap aman saat sensor hilang, node offline, atau user meminta command yang tidak didukung device?

---

## 2. Kondisi eksperimen

Semua kondisi memakai **responder yang sama** dan **workload yang sama**. Yang berubah hanya cara informasi dikumpulkan dan dikirim ke model. Ini penting: kalau responder ikut diganti, penurunan kualitas tidak bisa diatribusikan ke cost-aware sensing.

| Kode | Nama | Planner | Cara mengambil data | Cache | Budget | Progressive | Tujuan |
|---|---|---|---|---|---|---|---|
| B0 | Raw streaming | Tidak ada; semua field dikirim | Node: telemetry tiap 5 s. Station: `stream_ms=5000` mode `raw` | Tidak relevan | Tidak ada | Tidak | Baseline IoT konvensional |
| B1 | Semantic, always fresh | Oracle (gold label) | Hanya `required_information`, selalu baca fresh | Tidak | Tidak ada | Tidak | Isolasi manfaat pemilihan semantik |
| B2 | **Cost-aware (proposed)** | SFT planner | Hanya prediksi planner, cache dipakai, budget diterapkan | Ya | 2 source | Ya, maks. 2 ronde | Sistem yang diusulkan |
| B3 | Cost-aware + oracle planner | Oracle | Seperti B2, tapi label planner benar | Ya | 2 source | Ya | Batas atas retrieval |
| B4 | Cost-aware tanpa cache | SFT planner | Selalu fresh, `max_age_ms=0` | Tidak | 2 source | Ya | Isolasi efek cache |
| B5 | Cost-aware tanpa budget | SFT planner | Cache dipakai, budget tak terbatas | Ya | ∞ | Ya | Isolasi efek budget |
| B6 | Cost-aware tanpa progressive | SFT planner | Satu ronde saja | Ya | 2 source | Tidak | Isolasi efek progressive |
| B7 | Single-model fallback | Qwen3 1.7B dengan prompt planner | Sama seperti B2 | Ya | 2 source | Ya | Uji jalur satu model |

Catatan implementasi:

- **Node** (`firmware/`) tidak punya mode `raw`/`semantic`/`cost_aware`, `budget`, atau cache. Baseline node diimplementasikan di desktop: berapa field yang diminta, kapan meminta, dan apakah memakai nilai telemetry yang masih segar. Jangan menulis di laporan bahwa firmware node melakukan cache kalau yang melakukan cache adalah desktop.
- **Station** (`alatnya cu/studysphere_esp32.ino`) sudah punya `mode`, `budget`, `max_age_ms`, cache, `missing`, dan `meta.sensor_reads`/`uart_bytes`/`read_ms`. Gunakan itu untuk B0–B6 pada station.
- B0 station memakai `mode:"raw"`; responsnya memuat semua informasi plus `device` (RSSI, uptime, fan, vent), jadi payload-nya lebih besar. B0 node memakai telemetry periodik yang sudah ada.
- Untuk perbandingan yang adil, **prompt responder harus identik** di semua kondisi. Yang berbeda hanya isi `FACTS`, `MISSING`, dan `age_s`.
- Untuk B1 dan B3, planner oracle adalah label `assistant` di dataset, bukan model. Ini bukan kondisi yang bisa dipakai di produksi; gunanya hanya mengukur batas atas.
- B7 adalah jalur alternatif satu model. Jangan mencampurnya di tabel perbandingan utama B0–B6; laporkan sebagai eksperimen terpisah karena responder-nya juga berbeda.

### 2.1 Mengapa B0 dan B1 perlu

Tanpa B0, klaim “hemat” tidak punya pembanding konvensional. Tanpa B1, keberhasilan B2 bisa berasal dari pemilihan semantik, bukan dari cache/budget. Tabel hasil harus memisahkan keduanya.

### 2.2 Yang tidak boleh dibandingkan

- Jangan membandingkan B2 yang memakai Qwen3 1.7B dengan B1 yang memakai Gemma 3 270M. Itu membandingkan dua hal sekaligus.
- Jangan mengukur B0 pada station dan B2 pada node. Device, sensor, dan payload-nya berbeda.
- Jangan memakai test set untuk memilih epoch, prompt, budget, atau suhu sampling.

---

## 3. Workload

### 3.1 Offline planner evaluation

- Data: `ai/data/planner/test.jsonl` (10.000 baris, 14 task, 2 device, 2 bahasa).
- Hard set: `ai/data/eval/planner_hard.jsonl` (42 kasus tulis tangan).
- Satu baris = satu panggilan planner. Tidak ada device dan tidak ada responder.
- Jalankan dengan `temperature=0`, `--json-mode` jika endpoint mendukung.
- Simpan prediksi mentah ke JSONL.

### 3.2 Offline responder evaluation

- Data: `ai/data/responder/test.jsonl` (10.000 baris, 27 intent).
- Hard set: `ai/data/eval/responder_hard.jsonl` (28 kasus).
- Metrik utama: command accuracy, mood accuracy, grounding, brevity, format.
- Untuk hard set, `meta.expect` sudah memuat `mood`, `command`, `must_include`, dan `must_not_include`.

### 3.3 End-to-end replay

Workload ini yang menghasilkan angka cost, byte, dan latency. Definisikan satu set skenario tetap, lalu jalankan semua kondisi pada skenario yang sama.

Usulan:

- 300 skenario, distratifikasi:
  - 14 task planner (semua muncul),
  - 2 device (node dan station),
  - 2 bahasa (Indonesia dan Inggris),
  - 6 keadaan ruangan: nyaman, panas, udara pengap, gelap, kosong, sensor hilang/offline.
- Setiap skenario = satu utterance + satu snapshot ruangan + satu device.
- Urutan kondisi diacak per skenario, dengan seed yang disimpan.
- Untuk station, jalankan device asli atau simulator yang memakai sketch yang sama. Untuk node, jalankan firmware asli atau simulator WireLab.
- Sebelum pengukuran, lakukan 5 request pemanasan; jangan masukkan ke statistik.
- Ulangi 3 kali per skenario untuk latency; untuk cost/byte, satu kali sudah deterministik kalau cache dimulai dari keadaan yang sama.

Yang harus dicatat per request:

```json
{
  "scenario_id": "s-0042",
  "condition": "B2",
  "device": "station",
  "task": "ventilation_decision",
  "utterance": "Udara di sini pengap, sebaiknya buka jendela?",
  "planner": {"task": "ventilation_decision", "required_information": ["air_quality"]},
  "rounds": 1,
  "requested": ["air_quality"],
  "fresh_read": ["air_quality"],
  "cached": [],
  "missing": {},
  "sensor_reads": 1,
  "sources": ["arduino_uart"],
  "uart_bytes": 18,
  "request_bytes": 96,
  "response_bytes": 142,
  "planner_ms": 84,
  "device_ms": 41,
  "responder_ms": 620,
  "total_ms": 745,
  "reply": {"say": "...", "mood": "dizzy", "command": null},
  "command_sent": null,
  "answer_adequate": true
}
```

`answer_adequate` tidak boleh diisi oleh model yang dievaluasi. Isi dengan rubrik manusia atau aturan yang sudah dibekukan sebelum melihat hasil.

### 3.4 Real utterances

Dataset ini sintetis. Tambahkan **100–300 ucapan nyata** dari teman/kos/kelas, dengan izin, tanpa data pribadi:

- 50% Indonesia, 50% Inggris atau campuran,
- minimal 30% bukan kalimat lengkap,
- minimal 20% mengandung typo atau slang,
- minimal 10% meminta hal yang tidak didukung device.

Labeli dengan kebijakan yang sama seperti `ai/docs/dataset-card.md`, lalu bekukan. Ini test set eksternal, bukan data training. Laporkan hasilnya terpisah; jangan mencampurnya dengan test sintetis.

---

## 4. Metrik

### 4.1 Planner

| Metrik | Definisi | Arah |
|---|---|---|
| JSON valid rate | Output bisa diparse dan punya skema yang benar | Tinggi |
| Task accuracy | `task` sama dengan gold | Tinggi |
| Exact information set | Himpunan `required_information` sama persis | Tinggi |
| Micro precision | TP / (TP + FP) item informasi | Tinggi |
| Micro recall | TP / (TP + FN) item informasi | Tinggi |
| Micro F1 | Harmonic mean precision/recall | Tinggi |
| Extra items | Jumlah informasi yang diminta tetapi tidak perlu | Rendah |
| Missing items | Jumlah informasi yang perlu tetapi tidak diminta | Rendah |
| Hallucinated names | Nama sensor yang tidak ada di registry | Rendah |
| Sensor cost | `info_cost()` atas himpunan prediksi | Rendah |
| Cost error | `cost_pred - cost_gold` | Mendekati 0 |

`ai/eval/evaluate.py` sudah mencetak sebagian besar metrik ini. Untuk analisis per task, baca `report` JSONL dan kelompokkan dengan `meta.task`.

### 4.2 Retrieval dan sensing

| Metrik | Definisi | Satuan |
|---|---|---|
| `sensor_reads` | Jumlah source yang benar-benar dibaca fresh | read/request |
| `weighted_source_cost` | Jumlah cost source unik yang dibaca | cost/request |
| `fresh_ratio` | Informasi yang diminta dan benar-benar fresh | 0–1 |
| `cache_hit_ratio` | Informasi yang dipenuhi dari cache | 0–1 |
| `budget_miss` | Informasi yang tidak diambil karena budget | item/request |
| `uart_bytes` | Byte yang lewat UART ke UNO | byte/request |
| `mqtt_request_bytes` | Ukuran payload request | byte/request |
| `mqtt_response_bytes` | Ukuran payload response | byte/request |
| `mqtt_messages` | Jumlah pesan MQTT per interaksi | pesan/request |
| `rounds` | Jumlah ronde retrieval | ronde/request |
| `stale_served` | Jawaban memakai nilai yang lewat TTL | kejadian |

Cost source yang dipakai konsisten dengan `ai/spec/taxonomy.json`:

```text
occupancy, session_duration     -> 1 (HC-SR04 / derived)
temperature, humidity            -> 2 (satu pembacaan DHT)
lighting, air_quality, co2       -> 3 (satu hop UART ke UNO / MQ-135)
lamp, lamp_mode, fan, vent       -> 0 (state, bukan sensing)
```

Biaya dihitung per **source unik**, bukan per item. `temperature + humidity` = 2, bukan 4. `lighting + air_quality` = 3, bukan 6.

### 4.3 System dan kualitas jawaban

| Metrik | Definisi |
|---|---|
| End-to-end latency | Dari user selesai bicara sampai `say` siap |
| Planner latency | Waktu panggilan planner |
| Device latency | Dari publish request sampai response diterima |
| Responder latency | Waktu panggilan responder |
| Tokens | Prompt + completion token per panggilan |
| Command accuracy | Command sama dengan gold, termasuk `null` |
| Mood accuracy | Mood sama dengan gold |
| Grounding | Angka di `say` ada di `FACTS` atau merupakan turunan yang benar |
| Brevity | Maksimal dua kalimat |
| Format | Tanpa emoji, markdown, atau list |
| Answer adequacy | 1–5, dinilai manusia dengan rubrik |
| Unsafe action rate | Command berbahaya atau tidak didukung yang tetap dikirim |

Rubrik `answer_adequate` yang disarankan:

| Skor | Arti |
|---:|---|
| 5 | Menjawab penuh, memakai data yang tepat, tidak mengarang, command benar |
| 4 | Menjawab benar, ada detail kecil yang kurang |
| 3 | Arah jawaban benar, tetapi ada informasi penting yang tidak dipakai |
| 2 | Sebagian benar, ada angka/command yang tidak grounded |
| 1 | Salah, mengarang, atau command tidak aman |

Dua penilai independen untuk minimal 20% sampel. Hitung Cohen's kappa. Jika kappa < 0,6, perbaiki rubrik sebelum melanjutkan.

### 4.4 Energy proxy

Jangan menulis “hemat energi” hanya dari jumlah request. Ada tiga tingkat bukti:

1. **Primary (disarankan):** ukur daya dengan USB power meter/INA219 pada node dan station. Catat arus dan tegangan saat idle, saat membaca tiap sensor, dan saat mengirim MQTT. Hitung energi per interaksi.
2. **Secondary:** `sensor_reads`, `uart_bytes`, `mqtt_bytes`, dan `read_ms` dari firmware. Ini proksi yang jujur.
3. **Tertiary:** `E_proxy = Σ (I_source × V × t_read) + (I_radio × V × t_tx)`. Kalau memakai angka datasheet, tulis asumsinya di tabel dan jangan menyebutnya pengukuran. Hitung hanya komponen **marginal** per request; heater MQ-135 atau UNO yang selalu menyala bukan biaya per request, kecuali eksperimen memang mengukur seluruh node.

Untuk skripsi, minimal laporkan `sensor_reads`, `weighted_source_cost`, `uart_bytes`, dan `mqtt_bytes`. Sebut energy sebagai **proxy** kecuali benar-benar diukur.

---

## 5. Protokol

### Fase 0 — Bekukan artefak

Sebelum melihat hasil test:

- commit hash repository,
- hash dataset (`sha256` tiap file),
- versi prompt (salin teks system prompt ke lampiran),
- seed training dan evaluasi,
- versi model dasar dan tokenizer,
- versi Ollama/llama.cpp,
- spesifikasi laptop/GPU,
- daftar package (`pip freeze`).

Tulis di `ai/out/EXPERIMENT_LOCK.md`. Setelah test dibuka, jangan mengubah label, prompt, atau split.

### Fase 1 — Training

- Planner: `google/gemma-3-270m-it`, 3 seed (`20261021`, `20261022`, `20261023`), hyperparameter sama.
- Responder: `Qwen/Qwen3-1.7B`, 3 seed, QLoRA 4-bit.
- Fallback: Qwen3 1.7B untuk planner + responder, atau Gemma 3 270M multi-task; jalankan sebagai B7.
- Pilih checkpoint berdasarkan **val loss** dan exact-set match di val, bukan test.
- Simpan `run.json`, adapter, dan log training.

Contoh:

```sh
python ai/train/train_lora.py \
  --base-model google/gemma-3-270m-it \
  --train-file ai/data/planner/train.jsonl \
  --val-file ai/data/planner/val.jsonl \
  --output-dir ai/out/planner-seed-20261021 \
  --epochs 4 --batch-size 8 --grad-accum 2 --lr 1e-4 --seed 20261021
```

### Fase 2 — Offline evaluation

Untuk setiap seed dan setiap model:

```sh
python ai/eval/evaluate.py \
  --task planner \
  --data ai/data/planner/test.jsonl \
  --base-url http://localhost:11434/v1 \
  --model studysphere-planner \
  --report ai/out/planner-test-predictions.jsonl
```

```sh
python ai/eval/evaluate.py \
  --task responder \
  --data ai/data/responder/test.jsonl \
  --base-url http://localhost:11434/v1 \
  --model studysphere-responder \
  --report ai/out/responder-test-predictions.jsonl
```

Ulangi untuk hard set. Jangan memilih model berdasarkan hard set; hard set adalah laporan error, bukan tuning set.

### Fase 3 — Replay end-to-end

- Jalankan 300 skenario pada B0–B6.
- Untuk station, aktifkan `mode` yang sesuai di request; untuk B0 gunakan `stream_ms`.
- Untuk node, atur strategi retrieval di desktop dan catat field yang diminta.
- Acak urutan kondisi per skenario; simpan seed.
- Rekam `ai/out/replay.jsonl` dengan skema di §3.3.
- Ulangi 3 kali untuk latency; median dan p95 dihitung dari semua run yang valid.

### Fase 4 — Real utterances

- Kumpulkan 100–300 ucapan, dengan izin.
- Labeli dengan kebijakan yang sama; dua orang untuk 20% sampel.
- Bekukan sebelum evaluasi.
- Jalankan planner dan responder; laporkan terpisah dari test sintetis.

### Fase 5 — Robustness dan safety

Uji minimal kasus berikut:

| Kasus | Yang harus terjadi |
|---|---|
| Node offline | Responder mengatakan data tidak tersedia; tidak ada command |
| Sensor `temperature` hilang | Tidak mengarang suhu; `missing` disebut |
| Station diminta `co2` ppm | Menjelaskan station hanya punya level `air_quality` |
| Node diminta `fan` | Menjelaskan node tidak punya fan |
| Station diminta `lamp` | Menjelaskan station tidak punya lamp |
| `[alert]` | Tidak ada command |
| Fan dimatikan saat suhu 35 °C | Dataset mengajarkan “patuhi + peringatkan”; catat apakah model melakukan itu |
| LCD > 32 karakter | Dipotong, tidak error |
| Bahasa campur | Tetap menjawab in-language; tidak beralih tanpa alasan |
| Typo | Tidak crash; minimal tetap menjawab |

### Fase 6 — Analisis dan laporan

- Hitung metrik dari JSONL, bukan dari hafalan.
- Buat tabel dan grafik di §7.
- Tulis error taxonomy: 10 kesalahan terburuk per model.
- Tulis threats to validity di §8.
- Simpan semua artefak agar bisa direproduksi.

---

## 6. Ablasi

| Ablasi | Variabel | Yang dijawab |
|---|---|---|
| Ukuran planner | Gemma 270M vs Gemma 1B vs Qwen3 1.7B | Apakah 270M cukup? |
| Ukuran data | 10.000 / 25.000 / 50.000 / 100.000 baris train | Apakah 100.000 baris benar-benar perlu, atau 25.000 sudah jenuh? |
| Budget | 0 / 1 / 2 / 3 / ∞ source | Titik jenuh benefit |
| Cache | TTL taxonomy vs TTL 0 vs TTL 2× | Trade-off fresh vs hemat |
| Progressive | 1 / 2 / 3 ronde | Apakah ronde tambahan sepadan? |
| Prompt | Dengan aturan vs tanpa aturan | Seberapa besar aturan membantu? |
| Kuantisasi | bf16 vs Q4_K_M | Penurunan akurasi dan latency |
| Single vs two model | Qwen3 planner+responder vs dua adapter | Apakah pemisahan peran perlu? |
| Label policy | `temperature` saja vs `temperature+humidity` untuk “gerah” | Sensitivitas terhadap kebijakan |

Ablasi budget dan cache adalah yang paling penting untuk RQ2/RQ4. Jalankan pada station, karena station yang punya implementasi asli. Untuk node, jelaskan bahwa budget diterapkan di desktop.

Rencana alignment setelah SFT (DPO/ORPO dan RLVR/GRPO) ada di
`ai/docs/alignment-plan.md`. Perlakukan sebagai eksperimen terpisah: jangan
mencampur hasil SFT murni dengan model yang sudah di-align.

Analisis Pareto:

- Sumbu X: `weighted_source_cost` per request (atau `mqtt_bytes`).
- Sumbu Y: exact-set match atau answer adequacy.
- Plot setiap kondisi/ablasi sebagai titik.
- Tandai titik yang **didominasi** (lebih mahal dan tidak lebih akurat).
- Jangan memilih titik terbaik hanya dari satu metrik.

---

## 7. Tabel dan gambar untuk laporan

### Tabel 1 — Dataset

| Dataset | Train | Val | Test | Task/intent | Catatan |
|---|---:|---:|---:|---:|---|
| Planner | 100.000 | 10.000 | 10.000 | 14 | 41 profile, 2 device, 2 bahasa |
| Responder | 100.000 | 10.000 | 10.000 | 27 | 2 alert, 2 offline/missing, 4 capability error |
| Planner hard | — | — | 42 | — | tulis tangan |
| Responder hard | — | — | 28 | — | tulis tangan |

Catatan: jumlah baris besar karena variasi konteks/device/state, bukan karena
120.000 kalimat unik. Lihat `ai/docs/dataset-card.md` §7 untuk jumlah teks user
unik; laporkan keduanya kalau mengklaim ukuran dataset.

### Tabel 2 — Hasil utama

| Kondisi | Task acc | Exact set | Info F1 | Cost/request | MQTT bytes | p50 latency | p95 latency | Adequacy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B0 Raw | | | | | | | | |
| B1 Semantic fresh | | | | | | | | |
| B2 Cost-aware | | | | | | | | |
| B3 Oracle planner | | | | | | | | |
| B4 No cache | | | | | | | | |
| B5 No budget | | | | | | | | |
| B6 No progressive | | | | | | | | |

Tulis mean ± SD across 3 seeds untuk model-based conditions. Untuk latency, tulis median dan p95, bukan mean saja.

### Tabel 3 — Per task

| Task | n | Exact set | F1 | Cost gold | Cost pred | Selisih cost |
|---|---:|---:|---:|---:|---:|---:|
| `thermal_comfort_check` | | | | | | |
| `air_quality_check` | | | | | | |
| `lighting_check` | | | | | | |
| `occupancy_check` | | | | | | |
| `study_break_advice` | | | | | | |
| `ventilation_decision` | | | | | | |
| `energy_saving_check` | | | | | | |
| `study_environment_assessment` | | | | | | |
| `room_status` | | | | | | |
| `lamp_control` | | | | | | |
| `fan_control` | | | | | | |
| `vent_control` | | | | | | |
| `lcd_message` | | | | | | |
| `general_chat` | | | | | | |

### Tabel 4 — Error taxonomy

| Kategori | Contoh | Jumlah | Dampak |
|---|---|---:|---|
| Task salah | “berapa suhu?” → `room_status` | | |
| Informasi kurang | Panas → hanya `temperature`, padahal gerah butuh `humidity` | | |
| Informasi berlebih | Command lampu → minta 4 sensor | | |
| Nama sensor halusinasi | `noise`, `brightness`, `pm25` | | |
| Device salah | Station → `co2` | | |
| Command salah | Node → `fan` | | |
| Command berbahaya | Fan off saat 35 °C | | |
| Tidak grounded | Angka tidak ada di FACTS | | |
| Format | JSON dibungkus markdown | | |

### Gambar

1. Pareto cost vs exact-set / adequacy.
2. Breakdown cost per source untuk B0–B2.
3. CDF latency per kondisi.
4. Confusion matrix task planner.
5. Contoh 3 jawaban baik dan 3 jawaban buruk per device.

---

## 8. Analisis statistik

- **Accuracy:** McNemar test untuk pasangan kondisi pada skenario yang sama. Laporkan selisih proporsi dan 95% CI.
- **Cost, byte, latency:** paired bootstrap 10.000 resample; laporkan median selisih dan 95% CI. Latency juga p95.
- **Per task:** macro average dan micro average; jangan hanya micro, karena task minor bisa tenggelam.
- **Human adequacy:** mean, SD, Cohen's kappa; jika ordinal, gunakan Wilcoxon signed-rank.
- **Multiple comparisons:** Holm–Bonferroni untuk keluarga uji utama.
- **Effect size:** selisih persentase poin dan odds ratio untuk McNemar; jangan hanya p-value.

Dengan 10.000 baris test, interval kepercayaan 95% baris-per-baris untuk akurasi di sekitar 50% adalah kira-kira ±1,0 poin persentase. Namun baris test tidak independen: banyak baris berbagi template, prefix, atau profil yang sama, sehingga CI baris-per-baris terlalu optimistis. Gunakan paired bootstrap yang mengelompokkan per template/profil, atau laporkan CI berbasis family. Hard set 42/28 hanya untuk analisis error, bukan untuk klaim signifikansi.

Jangan:

- memilih seed yang hasilnya paling bagus,
- membuang run yang gagal tanpa melaporkannya,
- mengklaim signifikan dari test set yang dipakai untuk tuning,
- membandingkan angka dari laptop yang berbeda tanpa mencatat spesifikasinya.

---

## 9. Threats to validity

| Ancaman | Dampak | Mitigasi |
|---|---|---|
| Bahasa sintetis | Akurasi bisa terlalu tinggi | Uji 100–300 ucapan nyata; laporkan terpisah |
| Template leakage | Test terlalu mudah | Split template-disjoint; cek overlap di `validate.py` |
| Satu kebijakan label | “Benar” menurut satu definisi | Tulis kebijakan; uji sensitivitas label |
| MQ-135 bukan CO₂ | Klaim lingkungan salah | Sebut “estimasi”; station hanya level |
| Simulator vs hardware | Latency/energy berbeda | Ukur di hardware untuk klaim akhir |
| LLM nondeterminisme | Hasil berubah | Temperature 0 untuk evaluasi; 3 seed training |
| Cost model internal | Tidak sama dengan energi nyata | Laporkan sebagai proxy; ukur daya jika bisa |
| Sampel kecil | CI lebar | Laporkan CI; jangan overclaim |
| Command safety | Model bisa salah | Whitelist di desktop; uji hard cases |
| Prompt mismatch | Model SFT turun | Prompt integration harus identik dengan dataset |

---

## 10. Checklist eksperimen

- [ ] Dataset di-hash dan dibekukan.
- [ ] Prompt planner/responder disalin ke lampiran.
- [ ] 3 seed training planner selesai.
- [ ] 3 seed training responder selesai.
- [ ] Test planner dan hard planner dievaluasi.
- [ ] Test responder dan hard responder dievaluasi.
- [ ] B0–B6 dijalankan pada skenario yang sama.
- [ ] `ai/out/replay.jsonl` lengkap.
- [ ] 100–300 ucapan nyata dilabeli dan dibekukan.
- [ ] Dua penilai untuk 20% sampel; kappa dihitung.
- [ ] Analisis statistik dijalankan.
- [ ] Tabel dan gambar diisi dari JSONL.
- [ ] Error taxonomy ditulis.
- [ ] Threats to validity ditulis.
- [ ] Tidak ada klaim “hemat energi” tanpa pengukuran/proxy yang jelas.

---

## 11. Yang harus ada di laporan

Minimal:

1. Diagram sistem dua tahap dan kontrak MQTT.
2. Tabel dataset dan kebijakan label.
3. Tabel hasil B0–B6.
4. Grafik Pareto.
5. Per-task breakdown.
6. Error taxonomy dengan contoh nyata.
7. Analisis statistik dengan CI.
8. Threats to validity.
9. Lampiran prompt, hyperparameter, seed, dan hash.

Kalimat yang aman untuk abstrak:

> Kami membandingkan pengambilan data raw, semantic, dan cost-aware pada workload yang sama. Model planner 270M memprediksi informasi minimum, sementara responder 1,7B menghasilkan jawaban dan command. Pada eksperimen ini, cost-aware menurunkan [isi dengan hasil nyata] dibanding raw, dengan penurunan akurasi [isi dengan hasil nyata].

Jangan menulis “menghemat energi” sebelum ada pengukuran daya. Tulis “menurunkan jumlah pembacaan sumber dan byte MQTT” jika yang diukur hanya itu.
