# Rencana alignment StudySphere: dari SFT ke jawaban yang lebih predictable, konsisten, dan efisien

Status: **rancangan**. Belum ada training DPO, ORPO, atau RLVR yang dijalankan.
Dokumen ini menjelaskan apa yang masuk akal dicoba setelah SFT baseline selesai,
bukan klaim bahwa metode tersebut pasti meningkatkan hasil.

---

## 1. Definisi yang harus diukur

"Predictable", "konsisten", dan "efisien" mudah jadi klaim kosong. Di proyek ini
definisinya harus operasional:

| Tujuan | Definisi operasional | Metrik |
|---|---|---|
| Predictable | Output punya skema dan keputusan yang stabil pada input yang sama | JSON valid rate, schema match, agreement@N |
| Konsisten | Parafrase dengan maksud sama menghasilkan task/info yang sama; fakta sama menghasilkan command yang sama | task agreement, info-set Jaccard, command agreement |
| Tidak kontradiktif | `say` tidak bertentangan dengan fakta atau command | contradiction rate, grounding rate |
| Efisien | Tidak membaca sensor/token lebih banyak dari yang perlu | sensor cost, token count, latency, bytes |
| Tetap benar | Konsistensi tidak boleh dicapai dengan cara konsisten salah | task accuracy, exact-set match, command accuracy |

Aturan penting:

> Konsistensi tanpa akurasi bukan keberhasilan. Model yang selalu menjawab
> `{"task":"general_chat","required_information":[]}` sangat konsisten, tetapi
> salah untuk hampir semua request.

---

## 2. Posisi proyek sekarang

Yang sudah ada:

- Planner SFT: `google/gemma-3-270m-it` + LoRA.
- Responder SFT: `Qwen/Qwen3-1.7B` + QLoRA.
- Dataset SFT: 100.000 train / 10.000 val / 10.000 test per peran.
- Hard eval: 42 planner + 28 responder.
- Belum ada CPT, data preference, reward model, atau RL.

Yang **tidak** dilakukan:

- CPT pada `draft.pdf`, README, atau log firmware. Corpus-nya kecil, tidak
  berlabel instruction, dan berisiko membuat model lupa format.
- RLHF penuh dengan anotator manusia. Biayanya tinggi dan tidak proporsional
  untuk model 270M/1.7B pada skripsi ini.

---

## 3. Hierarki intervensi

Jangan langsung ke RL. Urutannya dari yang paling murah dan paling pasti:

```text
0. Deterministic inference + schema validation + fallback
1. SFT yang konsisten
2. DPO / ORPO
3. RLVR / GRPO
4. RLHF penuh
```

| Lapisan | Masalah yang diselesaikan | Biaya | Risiko |
|---|---|---|---|
| Deterministic decoding + schema | Format berubah-ubah, JSON rusak | Sangat rendah | Tidak memperbaiki reasoning |
| SFT | Belum mengikuti kebijakan/label | Sedang | Data salah → model salah |
| DPO/ORPO | Command safety, gaya, pilihan jawaban | Sedang | Pair terlalu mudah, overfit |
| RLVR/GRPO | Cost/accuracy planner | Tinggi | Reward hacking, tidak stabil |
| RLHF | Preferensi manusia yang kompleks | Sangat tinggi | Subjektif, mahal, tidak stabil |

Untuk sebagian besar masalah "tidak predictable", lapisan 0 dan 1 sudah cukup.
DPO/RLVR hanya layak kalau evaluasi SFT menunjukkan error yang spesifik.

---

## 4. Kondisi eksperimen

Jangan mencampur semua metode dalam satu angka. Bandingkan kondisi berikut:

| Kondisi | Model | Training tambahan | Yang dijawab |
|---|---|---|---|
| E0 | Planner + responder | SFT saja | Baseline |
| E1 | Responder | SFT + DPO/ORPO | Apakah preference membantu command/safety? |
| E2 | Planner | SFT + RLVR/GRPO | Apakah reward biaya membantu cost tanpa mengorbankan akurasi? |
| E3 | Planner + responder | SFT + DPO + RLVR | Apakah efeknya saling melengkapi? |
| E4 | Qwen3 satu adapter | SFT gabungan | Fallback satu model; laporkan terpisah |

E1 dan E2 adalah eksperimen utama. E3 hanya kalau E1/E2 sudah menunjukkan sinyal
positif. E4 jangan dicampur ke tabel utama B0–B6.

---

## 5. DPO / ORPO

### 5.1 Kapan dipakai

DPO/ORPO masuk akal kalau SFT sudah benar secara isi, tetapi:

- command masih salah device,
- jawaban terlalu panjang,
- gaya bahasa tidak konsisten,
- model kadang mengarang angka,
- model memilih jawaban aman vs tidak aman secara tidak stabil.

DPO **bukan** pengganti data SFT yang salah label.

### 5.2 Membuat preference pairs

Jangan memakai gold vs jawaban yang sengaja dirusak untuk seluruh dataset. Itu
terlalu mudah dan model hanya belajar membedakan benar/salah yang jelas.

Pipeline yang disarankan:

1. Ambil 10.000–20.000 prompt dari train (bukan test).
2. Jalankan model SFT 4–6 kali dengan temperature 0,7.
3. Skor setiap output dengan verifier aturan:
   - schema valid,
   - task benar,
   - information set F1,
   - sensor cost,
   - command valid/aman,
   - grounding,
   - brevity.
4. Pilih output terbaik sebagai `chosen`, terburuk sebagai `rejected`.
5. Buang pair kalau:
   - skornya sama,
   - keduanya invalid,
   - `chosen` masih mengandung pelanggaran capability,
   - `rejected` terlalu dekat dengan `chosen`.
6. Seimbangkan task/intent/device.
7. Simpan prompt test tetap tertutup.

Contoh format:

```json
{
  "prompt": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "chosen": "{\"say\":\"...\",\"mood\":\"normal\",\"command\":null}",
  "rejected": "{\"say\":\"...\",\"mood\":\"happy\",\"command\":{\"fan\":\"on\"}}",
  "meta": {"task": "air_quality_check", "reason": "wrong_device"}
}
```

Untuk responder, `chosen` harus lolos hard rules: command hanya untuk device yang
terhubung, tidak ada angka yang tidak ada di fakta, dan maksimal dua kalimat.
Untuk planner, `chosen` harus memakai informasi minimum, bukan semua sensor.

### 5.3 Konfigurasi awal

| Parameter | Nilai awal | Catatan |
|---|---|---|
| Metode | DPO atau ORPO | ORPO tidak butuh reference model |
| LoRA | r=16, alpha=32, dropout=0,05 | Sama seperti SFT |
| Epoch | 1 | Dataset preference kecil; overfit cepat |
| LR | 5e-5 | Lebih kecil dari SFT |
| Beta | 0,1 | Mulai di sini, bukan nilai sakral |
| Batch | 1–2 + grad accum | DPO menyimpan reference/policy |
| Max length | 1280 | Sama dengan SFT |

Jalankan pilot 5.000 pair dulu. Kalau tidak ada kenaikan command accuracy atau
consistency, jangan scale ke 20.000 pair.

---

## 6. RLVR / GRPO untuk planner

### 6.1 Kenapa planner dulu

Planner punya jawaban yang bisa diverifikasi program:

- task harus ada di taxonomy,
- information harus ada di registry,
- information harus tersedia di device,
- cost bisa dihitung,
- command task tidak boleh meminta sensor.

Responder lebih sulit karena kualitas `say` tidak bisa dinilai hanya dengan
aturan. Untuk responder, DPO/ORPO lebih masuk akal.

### 6.2 Reward awal

Contoh reward planner (bukan angka final; tuning di val):

```text
R = 2.0 * valid_schema
  + 1.5 * task_correct
  + 1.0 * info_f1
  + 1.0 * exact_info_set
  - 0.15 * sensor_cost
  - 2.0 * unsupported_information
  - 2.0 * invalid_information_name
  - 2.0 * command_task_with_sensor_request
```

Yang harus dijaga:

- Jangan hanya menghukum cost; model bisa memilih tidak membaca apa pun.
- `info_f1` dan `exact_info_set` harus cukup kuat.
- `sensor_cost` dihitung per source unik, bukan per item.
- `unsupported_information` mengecek device, bukan hanya nama sensor.
- Command task (`lamp_control`, `fan_control`, `vent_control`, `lcd_message`)
  harus mendapat reward tinggi hanya kalau `required_information` kosong.

### 6.3 GRPO

- Reuse prompt planner dari train; gold tetap dipakai untuk reward.
- 4–8 rollout per prompt, temperature 0,8.
- Bandingkan dengan reference model (SFT) untuk mencegah drift.
- Mulai 5.000 prompt; ukur cost dan accuracy, bukan hanya reward.
- Evaluasi di test dan hard set yang sama seperti SFT.

RLVR tidak boleh memakai test untuk memilih checkpoint atau reward scale.

### 6.4 Uji reward hacking

Sebelum training, uji reward pada output adversarial:

| Output | Reward yang diharapkan |
|---|---|
| JSON valid, task benar, info tepat | Tinggi |
| Task benar, info kosong | Rendah |
| Info semua sensor | Sedang/rendah karena cost |
| Nama sensor karangan | Sangat rendah |
| `co2` di station | Sangat rendah |
| Command task + minta 4 sensor | Sangat rendah |
| JSON dibungkus markdown | Rendah |

Kalau output "info kosong" masih mendapat reward tinggi, fungsi reward belum benar.

---

## 7. Evaluasi predictability, consistency, dan efficiency

### 7.1 Data

| Data | Fungsi |
|---|---|
| `ai/data/planner/test.jsonl` | Akurasi planner pada test sintetis |
| `ai/data/responder/test.jsonl` | Akurasi responder pada test sintetis |
| `ai/data/eval/planner_hard.jsonl` | Error analysis planner |
| `ai/data/eval/responder_hard.jsonl` | Command safety/grounding |
| Consistency set baru | Parafrase dan pengulangan |
| 100–300 ucapan nyata | Test eksternal beku |

Consistency set bisa dibuat dari test set:

- **Paraphrase group**: beberapa kalimat dengan maksud sama, context sama.
- **Repeat group**: input sama, dijalankan 5 kali pada temperature 0,7.
- **Fact group**: fakta sama, request berbeda yang seharusnya menghasilkan command sama.
- **Device group**: request sama di node dan station; capability harus berbeda.

### 7.2 Protokol

- Produksi: temperature 0, top_p 1, prompt tetap, schema/grammar validation.
- Stress test: temperature 0,7, 5 run, untuk mengukur variance.
- Jangan mengubah prompt antar-kondisi.
- Jangan memilih checkpoint dari test.
- Simpan prediksi mentah dan setting decoding.

### 7.3 Metrik

| Metrik | Cara hitung |
|---|---|
| JSON valid | Bisa diparse dan schema benar |
| Task agreement | Persentase task sama dalam satu paraphrase group |
| Info Jaccard | Rata-rata Jaccard `required_information` dalam group |
| Command agreement | Persentase command sama untuk fakta yang sama |
| Repeat agreement | Persentase output identik pada 5 run |
| Contradiction rate | `say` bertentangan dengan fakta/command |
| Sensor cost | `info_cost()` prediksi |
| Token count | Prompt + completion |
| Latency | p50/p95 di laptop target |
| Accuracy | Task/exact-set/command accuracy |

### 7.4 Statistik

- McNemar untuk perbandingan akurasi berpasangan.
- Paired bootstrap per template/profil, bukan per baris, karena baris test tidak
  independen.
- Laporkan effect size dan 95% CI.
- Jangan klaim signifikan kalau selisihnya di dalam noise.

---

## 8. Gate sebelum lanjut

| Gate | Lulus jika |
|---|---|
| SFT | JSON valid tinggi, task/command accuracy tercatat, error utama bisa dijelaskan |
| DPO | Command accuracy atau consistency naik, akurasi tidak turun secara bermakna |
| RLVR | Cost turun tanpa penurunan akurasi/exact-set yang tidak dapat diterima |
| Deploy | Hard case tidak menghasilkan command salah device/berbahaya |
| Laporan | Semua run, seed, prompt, dan prediksi tersimpan |

Kalau gate tidak lulus, **jangan** menambah metode. Perbaiki data, prompt, atau
definisi label.

---

## 9. Urutan eksekusi

1. Selesaikan SFT planner dan responder.
2. Evaluasi test + hard set; simpan `run.json` dan prediksi.
3. Buat verifier/reward dan consistency evaluator.
4. Buat 5.000 preference pairs dari output SFT.
5. Jalankan DPO/ORPO pilot; evaluasi.
6. Buat 5.000 prompt RLVR planner; jalankan GRPO pilot; evaluasi.
7. Scale hanya kalau ada kenaikan di luar noise.
8. Integrasikan ke desktop setelah gate deploy.
9. Jalankan baseline B0–B6 dan test nyata.

---

## 10. Yang tidak boleh diklaim

- "Predictable" bukan berarti benar.
- DPO/RLVR tidak otomatis lebih baik dari SFT.
- RLHF penuh belum tentu layak untuk model sekecil ini.
- Efisiensi sensor bukan efisiensi energi; energi butuh pengukuran.
- Dataset tetap sintetis; test nyata tetap diperlukan.
- MQ-135 tetap bukan sensor CO₂ terkalibrasi.
