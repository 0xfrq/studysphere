# StudySphere AI: dari requirement ke model lokal

Panduan ini menjelaskan cara membuat model AI StudySphere **dari nol sampai jalan di laptop**, memakai model open-source yang sudah ada dan di-fine-tune dengan LoRA. Dataset-nya sudah dibuat dan ada di `ai/data/`.

> **Status:** dataset, generator, skrip training/evaluasi/export, dan panduan sudah ada. Model **belum dilatih** di lingkungan ini karena tidak ada GPU/model runtime; jalankan perintah di §5 di Colab/WSL2/laptop GPU.

Ringkasnya:

```text
ucapan/teks siswa
      │
      ▼
[Planner 270M]  "informasi minimum apa yang cukup?"
      │  {"task":"...","required_information":["temperature","lighting"]}
      ▼
ESP32 / stasiun  ── hanya membaca sensor yang diminta (cache + budget)
      │  {"data":{...},"missing":{...}}
      ▼
[Responder 1–2B]  "jawab dan, kalau diminta, kirim command"
      │  {"say":"...","mood":"...","command":{...}}
      ▼
suara + balon kata + MQTT command
```

Dua model kecil lebih mudah dilatih dan lebih akurat daripada satu model yang dipaksa melakukan dua pekerjaan yang berbeda. Ini juga membuat kontribusi penelitian "cost-aware semantic sensing" terlihat jelas: planner-nya bisa dibandingkan dengan baseline yang mengirim semua data.

> **Penting:** angka dan klaim di panduan ini adalah target, bukan hasil yang sudah diukur. Setelah training, jalankan evaluasi dan ganti angka di laporan dengan hasil nyata.

---

## 1. Peta requirement → dataset

| Requirement di project | Wujud di dataset |
|---|---|
| DHT11/DHT22 (suhu, kelembapan) | `temperature`, `humidity` |
| LDR / BH1750 (cahaya) | `lighting` (node: lux, station: bright/dark) |
| MQ-135 (kualitas udara) | `air_quality` (level), `co2` (perkiraan ppm, node saja) |
| PIR / HC-SR04 (kehadiran) | `occupancy`, `session_duration` |
| Lampu belajar | `lamp`, `lamp_mode`, command `{"lamp":"on"}` |
| Kipas & servo ventilasi | `fan`, `vent`, command `{"fan":"on"}`, `{"vent":"open"}` |
| LCD 16×2 | command `{"lcd":"..."}` maksimal 32 karakter |
| MQTT `request`/`response` | output planner `{"task","required_information"}` |
| Prompt burung hantu di desktop | output responder `{"say","mood","command"}` |
| Alarm udara pengap / panas / node diam | intent `alert_air`, `alert_hot`, `offline` |
| Baseline raw vs semantic vs cost-aware | `ai/docs/experiment-plan.md` |
| Gemma 3 270M + LoRA | config planner di bawah |

Nama informasi, satuan, source cost, dan batas usia cache ada di `ai/spec/taxonomy.json`. File itu adalah "kontrak" antara dataset, firmware, dan desktop.

---

## 2. Struktur folder

```text
ai/
  README.md                     panduan ini
  spec/taxonomy.json            registry informasi, task, command, threshold
  synth/
    common.py                   helper + split + cost
    generate_planner.py         generator dataset planner
    generate_responder.py       generator dataset responder
    generate_hard.py            eval set tulisan tangan
    validate.py                 cek kontrak dataset
  data/
    planner/{train,val,test}.jsonl
    responder/{train,val,test}.jsonl
    eval/{planner_hard,responder_hard}.jsonl
  train/
    requirements.txt
    train_lora.py               LoRA/QLoRA + masking assistant
    merge_adapter.py            merge adapter untuk GGUF
  eval/evaluate.py              evaluasi lewat endpoint OpenAI-compatible
  serve/ollama/                 Modelfile planner & responder
  docs/
    dataset-card.md             label policy + statistik + limitasi
    experiment-plan.md          baseline, metrik, protokol eksperimen
    alignment-plan.md           opsi DPO/RLVR setelah SFT + metrik konsistensi
    integration.md              cara menyambung ke desktop & MQTT
```

---

## 3. Quick start 30 menit (tanpa training)

Untuk training di Colab, buka notebook `studysphere_colab.ipynb` di root repo.
Notebook itu sudah berisi setup, checkpoint/resume ke Drive, training, merge,
dan urutan evaluasi.

Kalau kamu cuma mau memastikan datasetnya sehat:

```sh
python ai/synth/generate_planner.py
python ai/synth/generate_responder.py
python ai/synth/generate_hard.py
python ai/synth/validate.py
```

Output default saat ini:

```text
planner/train.jsonl: 100000 rows
planner/val.jsonl: 10000 rows
planner/test.jsonl: 10000 rows
responder/train.jsonl: 100000 rows
responder/val.jsonl: 10000 rows
responder/test.jsonl: 10000 rows
...
no problems
```

Total **240.000 baris** (120.000 per peran). Ini 24× versi awal 5.000 baris.
Angka ini adalah baris training, bukan 240.000 kalimat unik: variasi utamanya
ada di konteks/device/state. Lihat `ai/docs/dataset-card.md` §7 untuk jumlah teks
user unik. File training tidak di-commit karena ukurannya ~525 MB; file tetap ada di disk
dan bisa dibuat ulang deterministik dari generator. Hard eval set tetap bisa
di-commit karena kecil.

### 3.1 Mengatur ukuran dataset

Default sudah besar. Untuk eksperimen cepat di laptop, kecilkan target dan
jumlah varian per template:

```sh
python ai/synth/generate_planner.py --train 10000 --val 1000 --test 1000 --variants 120
python ai/synth/generate_responder.py --train 10000 --val 1000 --test 1000 --variants 150
```

Untuk kembali ke default 120.000 baris per peran, jalankan tanpa flag. Jangan
menaikkan target tanpa menaikkan `--variants`: jumlah baris unik dibatasi oleh
kombinasi template × device × state × prefix. Generator akan berhenti di jumlah
unik yang tersedia, lalu `validate.py` akan melaporkan jumlah sebenarnya.

Lihat satu contoh:

```sh
python -c "import json; r=json.loads(open('ai/data/planner/train.jsonl',encoding='utf-8').readline()); print(r['messages'][1]['content']); print(r['messages'][2]['content'])"
```

---

## 4. Pilih model dasar

Jangan mulai dari model raksasa. Untuk dua peran ini:

| Model | Ukuran | Cocok untuk | Catatan |
|---|---:|---|---|
| `google/gemma-3-270m-it` | 270M | **Planner** | Paling ringan, CPU-friendly, cocok untuk structured prediction. Ini model di draft. |
| `Qwen/Qwen3-1.7B` | 1.7B | **Responder** | Multilingual bagus, JSON kuat, lisensi Apache 2.0. Q4 ~1.1 GB. |
| `google/gemma-3-1b-it` | 1B | Responder alternatif | Satu keluarga dengan planner, lebih sederhana. Bahasa Indonesia cukup, tapi Qwen3 biasanya lebih kuat. |
| `meta-llama/Llama-3.2-1B-Instruct` | 1B | Responder alternatif | Lisensi komunitas; cek syarat distribusi. |
| `HuggingFaceTB/SmolLM3-3B` | 3B | Responder kalau laptop kuat | Kualitas lebih baik, tapi Q4 ~1.9 GB dan lebih lambat. |

**Rekomendasi skripsi/demo:**

- Planner: **Gemma 3 270M** (sesuai draft, klaim "model 270M cukup untuk task understanding" jadi kuat).
- Responder: **Qwen3 1.7B** (kualitas bahasa Indonesia dan JSON lebih baik).
- Kalau ingin satu model saja: **Qwen3 1.7B** dengan dua prompt, tapi penelitiannya jadi kurang tajam karena tidak ada perbandingan planner kecil vs model besar.

**Jalur paling sesuai draft:** satu **Gemma 3 270M** untuk keduanya. Ini boleh dicoba, tapi siapkan ekspektasi: respons Indonesia-nya akan lebih kaku dan command-nya lebih sering salah. Dataset yang sama bisa dipakai; tinggal latih dua adapter atau satu adapter multi-task.

---

## 5. Mulai training

### 5.1 Kebutuhan

- Python 3.10–3.12 (jangan 3.13/3.14; wheel CUDA sering belum ada).
- GPU NVIDIA minimal 8 GB untuk QLoRA responder. Planner 270M bisa di GPU 8 GB dengan bf16, bahkan CPU (lambat).
- Alternatif gratis: Google Colab T4/L4, Kaggle GPU, atau RunPod.
- Windows: QLoRA/bitsandbytes paling aman lewat **WSL2** atau Colab. LoRA bf16 biasa bisa jalan native Windows.
- Disk: ~10 GB untuk model + cache + output, ditambah ~0,6 GB untuk dataset 240.000 baris.

Install:

```sh
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate

pip install -r ai/train/requirements.txt
```

Cek GPU:

```sh
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')"
```

### 5.2 Latih planner (Gemma 3 270M)

```sh
python ai/train/train_lora.py ^
  --base-model google/gemma-3-270m-it ^
  --train-file ai/data/planner/train.jsonl ^
  --val-file ai/data/planner/val.jsonl ^
  --output-dir ai/out/planner ^
  --epochs 2 ^
  --batch-size 8 ^
  --grad-accum 2 ^
  --lr 1e-4 ^
  --max-seq-length 1024
```

Di Linux/Colab ganti `^` dengan `\`.

Yang dilakukan script:

1. Membaca `messages` dan menerapkan chat template milik tokenizer.
2. Memberi label `-100` pada token system+user, jadi model hanya belajar dari JSON assistant.
3. Memasang LoRA di semua linear layer (`all-linear`).
4. Menyimpan adapter + tokenizer + `run.json` di `ai/out/planner/adapter`.

VRAM: ~4–7 GB. Waktu sangat tergantung GPU. Estimasi lama 10–25 menit hanya
untuk dataset 5.000 baris; dataset 120.000 baris jauh lebih lama. Mulai dengan
2 epoch, pantau val loss, dan tambah epoch hanya kalau val loss masih turun.

### 5.3 Latih responder (Qwen3 1.7B, QLoRA)

```sh
python ai/train/train_lora.py ^
  --base-model Qwen/Qwen3-1.7B ^
  --train-file ai/data/responder/train.jsonl ^
  --val-file ai/data/responder/val.jsonl ^
  --output-dir ai/out/responder ^
  --load-in-4bit ^
  --disable-thinking ^
  --epochs 2 ^
  --batch-size 4 ^
  --grad-accum 4 ^
  --lr 1e-4 ^
  --max-seq-length 1280
```

VRAM: ~6–8 GB. Dataset 120.000 baris membuat waktu training jauh lebih lama
dari estimasi lama 40–90 menit (yang untuk 5.000 baris). Mulai 2 epoch dan ukur
di GPU masing-masing; turunkan `--max-seq-length` atau target data kalau perlu.
Di T4, script otomatis memakai fp16 untuk QLoRA karena T4 tidak mendukung bf16
native; di L4/A100 script memakai bf16.

Kalau memakai **Gemma 3 1B**:

```sh
python ai/train/train_lora.py ^
  --base-model google/gemma-3-1b-it ^
  --train-file ai/data/responder/train.jsonl ^
  --val-file ai/data/responder/val.jsonl ^
  --output-dir ai/out/responder-gemma ^
  --epochs 2 --batch-size 4 --grad-accum 4 --lr 1e-4 --max-seq-length 1280
```

### 5.4 Kalau VRAM kurang

Urutan yang paling aman:

1. Turunkan `--max-seq-length` ke 768. Dataset ini pendek; 768 cukup.
2. Turunkan `--batch-size` ke 1 dan naikkan `--grad-accum` ke 16.
3. Pakai `--load-in-4bit` (responder).
4. Planner 270M: jangan 4-bit; bf16 sudah kecil.
5. Pindah ke Colab/Kaggle.

### 5.5 Cara membaca loss

- Loss turun lalu mendatar: normal.
- Val loss naik sementara train loss turun: overfit. Kurangi epoch, naikkan dropout, atau tambah data.
- Loss tetap tinggi: cek chat template, cek masking, cek apakah assistant JSON valid.
- Model mengulang prompt: cek `labels`; mungkin prompt tidak ter-mask.

### 5.6 Checkpoint dan resume setelah Colab mati

`train_lora.py` sekarang menyimpan checkpoint setiap `--save-steps` optimizer step
dan menyimpan maksimal `--save-total-limit` checkpoint. Ini penting karena Colab
gratis bisa memutus runtime sebelum training selesai.

Simpan output langsung ke Google Drive, bukan hanya `/content`:

```sh
python ai/train/train_lora.py ^
  --base-model Qwen/Qwen3-1.7B ^
  --train-file ai/data/responder/train.jsonl ^
  --val-file ai/data/responder/val.jsonl ^
  --output-dir /content/drive/MyDrive/studysphere_runs/responder ^
  --load-in-4bit --disable-thinking ^
  --epochs 2 --batch-size 4 --grad-accum 4 ^
  --max-seq-length 1280 ^
  --save-steps 200 --save-total-limit 3 ^
  --resume-from-checkpoint auto
```

Kalau runtime mati:

1. Buka Colab baru dan mount Drive.
2. Generate ulang dataset (deterministik, sekitar 1 menit).
3. Jalankan command yang sama.
4. `--resume-from-checkpoint auto` mencari `checkpoint-*` terbaru yang punya
   `trainer_state.json`, lalu melanjutkan dari step terakhir.

Kalau runtime mati sebelum training selesai, `run.json` mungkin belum terbentuk;
itu normal. Yang penting folder `checkpoint-*` sudah lengkap. Adapter final baru
ditulis setelah `trainer.train()` selesai.

Untuk memulai dari nol, ganti menjadi `--resume-from-checkpoint none`, atau
hapus folder output. Jangan mengubah dataset/prompt/hyperparameter di tengah
resume; hasilnya tidak lagi sebanding.

---

## 6. Evaluasi sebelum deploy

Evaluasi bisa lewat Ollama, vLLM, LM Studio, atau endpoint OpenAI-compatible apa pun.

### 6.1 Planner

```sh
python ai/eval/evaluate.py ^
  --task planner ^
  --data ai/data/planner/test.jsonl ^
  --base-url http://localhost:11434/v1 ^
  --model studysphere-planner ^
  --report ai/out/planner_predictions.jsonl
```

Metrik yang dicetak:

- JSON valid rate.
- Task accuracy.
- Exact information-set match.
- Micro precision/recall/F1 untuk himpunan informasi.
- Jumlah informasi ekstra dan informasi yang kurang.
- Sensor cost prediksi vs gold.

Yang paling penting untuk penelitian bukan cuma F1, tapi **cost**. Model yang menjawab benar dengan 4 informasi lebih buruk daripada model yang menjawab benar dengan 2 informasi.

### 6.2 Responder

```sh
python ai/eval/evaluate.py ^
  --task responder ^
  --data ai/data/responder/test.jsonl ^
  --base-url http://localhost:11434/v1 ^
  --model studysphere-responder ^
  --report ai/out/responder_predictions.jsonl
```

Lalu uji hard cases:

```sh
python ai/eval/evaluate.py ^
  --task responder ^
  --data ai/data/eval/responder_hard.jsonl ^
  --base-url http://localhost:11434/v1 ^
  --model studysphere-responder
```

Metrik: JSON valid, mood accuracy, command accuracy, brevity, format, keyword grounding. Hard cases lebih jujur daripada test sintetis.

### 6.3 Yang harus dikumpulkan untuk laporan

Simpan:

- `run.json` tiap training.
- File prediksi JSONL.
- Confusion matrix task (planner).
- Contoh 10 kesalahan terburuk.
- Latency rata-rata dan p95 di laptop yang sama.
- Perbandingan baseline (lihat `ai/docs/experiment-plan.md`).

---

## 7. Export ke GGUF + Ollama

### 7.1 Merge adapter

```sh
python ai/train/merge_adapter.py ^
  --base-model google/gemma-3-270m-it ^
  --adapter ai/out/planner/adapter ^
  --output ai/out/planner/merged
```

Untuk responder:

```sh
python ai/train/merge_adapter.py ^
  --base-model Qwen/Qwen3-1.7B ^
  --adapter ai/out/responder/adapter ^
  --output ai/out/responder/merged
```

### 7.2 Konversi ke GGUF

Install `llama.cpp` (ikut instruksinya), lalu:

```sh
python llama.cpp/convert_hf_to_gguf.py ai/out/planner/merged --outfile ai/out/planner/studysphere-planner-f16.gguf --outtype f16
python llama.cpp/llama-quantize ai/out/planner/studysphere-planner-f16.gguf ai/out/planner/studysphere-planner-q4_k_m.gguf Q4_K_M
```

Responder:

```sh
python llama.cpp/convert_hf_to_gguf.py ai/out/responder/merged --outfile ai/out/responder/studysphere-responder-f16.gguf --outtype f16
python llama.cpp/llama-quantize ai/out/responder/studysphere-responder-f16.gguf ai/out/responder/studysphere-responder-q4_k_m.gguf Q4_K_M
```

### 7.3 Daftarkan ke Ollama

Copy GGUF ke folder `ai/serve/ollama/`, lalu:

```sh
cd ai/serve/ollama
ollama create studysphere-planner -f Modelfile.planner
ollama create studysphere-responder -f Modelfile.responder
```

Tes:

```sh
ollama run studysphere-planner "CONTEXT: {\"device_type\":\"node\",\"available_information\":[\"temperature\",\"humidity\",\"lighting\",\"occupancy\"],\"previous\":null} REQUEST: \"Ruangan panas, berapa suhunya?\""
```

Di Windows, kalau desktop memanggil Ollama:

```powershell
$env:OLLAMA_ORIGINS="*"
ollama serve
```

---

## 8. Menyambung ke desktop

Ada dua tahap integrasi. Mulai dari yang paling kecil risikonya.

### Tahap A — responder saja (paling cepat)

Desktop sekarang sudah memanggil satu model dengan prompt burung hantu. Ganti endpoint ke Ollama:

```sh
STUDYSPHERE_LLM_URL=http://localhost:11434/v1
STUDYSPHERE_LLM_MODEL=studysphere-responder
npm run tauri dev
```

Di PowerShell:

```powershell
$env:STUDYSPHERE_LLM_URL="http://localhost:11434/v1"
$env:STUDYSPHERE_LLM_MODEL="studysphere-responder"
npm run tauri dev
```

Ini belum cost-aware; desktop masih mengirim semua telemetry ke prompt. Tapi ini cara tercepat melihat model SFT-mu hidup.

### Tahap B — planner + responder (cost-aware)

Alurnya:

1. Ambil pesan user dan fakta ruangan yang sudah ada di `desktop/ui/index.html`.
2. Panggil planner dengan `CONTEXT` + `REQUEST`.
3. Kalau informasi yang diminta belum ada/fresh, publish MQTT request ke node/stasiun.
4. Tunggu `response`/`resp`, gabungkan data.
5. Panggil responder dengan fakta yang sudah terkumpul.
6. Parse `{"say","mood","command"}` dan jalankan command yang valid.

Contoh potongan kode ada di `ai/docs/integration.md`. Topik MQTT dan format request/response tetap sama seperti `README.md`:

```json
{"task":"study_environment_assessment","required_information":["temperature","lighting","air_quality"]}
```

```json
{"task":"study_environment_assessment","data":{"temperature":31,"lighting":80},"missing":{"air_quality":"budget"}}
```

Kalau `required_information` kosong (command murni), **jangan** kirim request sensor; langsung kirim command dan tunggu konfirmasi telemetry/state.

---

## 9. Eksperimen untuk laporan

Ringkasan ada di `ai/docs/experiment-plan.md`. Rencana lanjutan setelah SFT
(DPO/ORPO, RLVR/GRPO, dan metrik predictability/consistency) ada di
`ai/docs/alignment-plan.md`. Jangan mulai eksperimen alignment sebelum SFT
baseline selesai dievaluasi.

Intinya:

| Kondisi | Yang dikirim ke LLM | Yang diukur |
|---|---|---|
| Baseline 1: raw | semua sensor, tiap saat | bandwidth, energi, latency, akurasi |
| Baseline 2: semantic | semua informasi yang relevan, selalu fresh | bandwidth, jumlah query, akurasi |
| Proposed: cost-aware | informasi minimum + cache + budget | bandwidth, jumlah source read, energi, latency, akurasi |

Metrik:

- **Task accuracy**: apakah task dan informasi yang diminta benar.
- **Information-set F1**: precision/recall himpunan informasi.
- **Sensor cost**: jumlah source read berbobot (`occupancy=1`, DHT=2, UNO=3).
- **Bytes**: ukuran payload MQTT request+response.
- **Energy proxy**: arus × waktu baca per source, atau jumlah source read sebagai proksi.
- **Latency**: waktu dari user message sampai `say` siap.
- **Response adequacy**: apakah jawaban memakai data yang cukup dan tidak mengarang.

Jangan klaim "hemat energi" tanpa pengukuran. Kalau tidak punya power meter, sebut sebagai **energy proxy** dan jelaskan asumsinya.

---

## 10. Troubleshooting

| Gejala | Penyebab umum | Solusi |
|---|---|---|
| `Chat template not found` | Base model bukan instruct/chat | Pakai `-it`/`-Instruct`, atau set template manual. |
| Model output bukan JSON | Belum cukup training / prompt beda | Cek dataset assistant, tambah epoch, pakai `--json-mode` saat eval. |
| Loss tidak turun | Label masking salah | Cek `sum(label != -100)` > 0; lihat `run.json`. |
| CUDA OOM | Batch/seq terlalu besar | Turunkan batch, seq, pakai 4-bit, grad accum. |
| QLoRA error di Windows | bitsandbytes/Windows | Pakai WSL2 atau Colab. |
| Ollama balas template aneh | GGUF tidak membawa chat template | Uncomment `TEMPLATE` di Modelfile. |
| Planner sering minta semua data | Dataset terlalu didominasi assessment | Tambah contoh minimal, cek label policy, evaluasi cost. |
| Responder mengarang angka | Data fakta tidak ada di prompt | Pastikan `FACTS` diisi; tambah hard case missing/offline. |
| Command salah device | Model belum belajar capability | Cek hard case `lamp-station`, `fan-node`, `vent-node`, `co2-station`. |

---

## 11. Checklist

- [ ] `python ai/synth/validate.py` bersih.
- [ ] Split train/val/test tidak bocor (template-disjoint).
- [ ] Hard eval sudah dijalankan.
- [ ] Baseline raw dan semantic sudah diukur.
- [ ] `run.json` disimpan.
- [ ] Model GGUF bisa dijalankan offline.
- [ ] Desktop bisa memanggil model lewat `STUDYSPHERE_LLM_URL`.
- [ ] Command yang dihasilkan model divalidasi lagi di desktop (whitelist nilai).
- [ ] Laporan menyebut MQ-135 bukan sensor CO₂ sejati.
- [ ] Laporan menyebut dataset sintetis dan keterbatasannya.

---

## 12. Batasan yang harus jujur ditulis

- Dataset ini **sintetis**. Uji dengan 100–300 ucapan nyata sebelum mengklaim akurasi.
- Label planner mengikuti satu kebijakan. Kalau kebijakan berubah, regenerate; jangan edit baris satuan.
- MQ-135 menghasilkan **perkiraan** kualitas udara, bukan ppm CO₂ terkalibrasi.
- Model 270M tidak akan sebaik 1.7B untuk bahasa alami. Itu sebabnya peran dipecah.
- Command safety di dataset ini "patuhi + beri peringatan". Produksi bisa memilih menolak.
- Speech-to-text dan text-to-speech masih terpisah (Groq atau model lokal lain). Dataset ini mulai dari teks.
