# Local training di RTX 2050 4 GB

Tujuan halaman ini: menjalankan **step ringan** untuk memastikan pipeline training
benar-benar jalan di laptop, bukan untuk menghasilkan model final.

## Batas realistis

| Model | Di RTX 2050 4 GB | Catatan |
|---|---|---|
| Planner Gemma 3 270M | **Bisa** | LoRA biasa, batch 1, gradient checkpointing |
| Responder Qwen3 1.7B | **Sangat ketat** | Hanya smoke test 100 baris; QLoRA di Windows sebaiknya lewat WSL2 |
| Planner 100k + responder 100k | **Tidak praktis** | Tetap pakai Colab/L4 atau GPU lain |

Untuk step ringan, pakai **planner 270M + 2.000 baris train + 200 baris val**.
Dataset tetap diambil dari dataset penuh, jadi semua task tetap terwakili.

---

## 1. Siapkan Python 3.12

Python 3.15 yang ada sekarang belum cocok dengan wheel PyTorch yang stabil.
Install Python 3.12 lewat Python Install Manager:

```powershell
cd D:\studysphere\fileconfig
py install 3.12
py -3.12 --version
```

Buat virtual environment khusus project:

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python --version
```

`python --version` harus menampilkan **3.12.x**, bukan 3.15.

---

## 2. Install PyTorch CUDA dan dependency

Install PyTorch CUDA 12.8:

```powershell
python -m pip install --upgrade pip
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
```

Lalu dependency training. Untuk planner, `bitsandbytes` tidak wajib:

```powershell
python -m pip install "transformers>=4.53,<5" "datasets>=3.6,<5" "accelerate>=1.8,<2" "peft>=0.16,<1" sentencepiece protobuf safetensors
```

Verifikasi GPU:

```powershell
python -c "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

Yang diharapkan:

```text
torch 2.8.0...
cuda True
NVIDIA GeForce RTX 2050
```

Kalau `cuda False`, jangan lanjut. Biasanya salah install wheel CPU, venv tidak
aktif, atau driver NVIDIA perlu update.

---

## 3. Login Hugging Face

Gemma 3 biasanya gated. Buka halaman model di Hugging Face, setujui license,
lalu login:

```powershell
python -c "from huggingface_hub import login; login()"
```

Tempel token yang punya akses ke `google/gemma-3-270m-it`.

---

## 4. Pastikan VRAM cukup

Tutup game, browser berat, OBS, dan aplikasi GPU lain. Cek:

```powershell
nvidia-smi
```

Target: pemakaian awal di bawah ~700 MB, supaya sisa VRAM mendekati 3,5 GB.

Set juga alokasi CUDA yang lebih toleran:

```powershell
$env:PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
```

---

## 5. Train planner ringan

Jalankan dari root repo:

```powershell
python ai/train/train_lora.py `
  --base-model google/gemma-3-270m-it `
  --train-file ai/data/planner/train.jsonl `
  --val-file ai/data/planner/val.jsonl `
  --output-dir ai/out/local-planner `
  --limit-train 2000 `
  --limit-val 200 `
  --epochs 1 `
  --batch-size 1 `
  --grad-accum 16 `
  --lr 1e-4 `
  --max-seq-length 768 `
  --gradient-checkpointing `
  --save-steps 50 `
  --save-total-limit 2 `
  --resume-from-checkpoint auto `
  --logging-steps 5
```

Arti setting penting:

- `--limit-train 2000`: hanya 2.000 baris pertama. Dataset sudah diseimbangkan,
  jadi semua task utama tetap muncul.
- `--batch-size 1 --grad-accum 16`: effective batch 16, tetapi VRAM tetap kecil.
- `--max-seq-length 768`: cukup untuk prompt planner (maksimum sekitar 2.100
  karakter), tanpa memakan VRAM sebesar 1024/1280.
- `--gradient-checkpointing`: wajib untuk 4 GB.
- `--save-steps 50`: checkpoint lokal tiap 50 step, bisa resume kalau crash.

Perkiraan waktu: **10–30 menit** untuk download model, tokenisasi, dan 125
optimizer step. Kalau laptop sibuk atau model belum pernah diunduh, bisa lebih
lama.

Yang diharapkan:

```text
raw train rows: 2000, raw val rows: 200
train rows: ...
trainable params: ...
```

Setelah selesai:

```text
ai/out/local-planner/adapter/
ai/out/local-planner/run.json
```

Ini **bukan model final**. Ini hanya bukti bahwa chat template, masking, LoRA,
checkpoint, dan evaluasi berjalan.

---

## 6. Kalau CUDA out of memory

Coba berurutan:

1. Tutup semua aplikasi GPU, lalu cek `nvidia-smi`.
2. Turunkan `--max-seq-length` dari `768` ke `640`, lalu `512`.
3. Naikkan `--grad-accum` dari `16` ke `32`; batch tetap `1`.
4. Pastikan `--gradient-checkpointing` ada.
5. Jangan memakai `--load-in-4bit` untuk planner 270M; itu tidak membantu di sini.
6. Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` lagi di PowerShell baru.

Kalau `--max-seq-length 512` masih OOM, hentikan. Jangan paksa responder 1.7B
di laptop ini.

---

## 7. Resume kalau training terhenti

Jalankan command yang sama lagi. Karena ada:

```text
--resume-from-checkpoint auto
```

training akan mencari `checkpoint-*` terakhir yang punya `trainer_state.json`
valid dan melanjutkan dari situ.

Untuk mulai dari nol:

```powershell
--resume-from-checkpoint none
```

atau hapus:

```powershell
Remove-Item -Recurse -Force ai/out/local-planner
```

---

## 8. Responder Qwen3 1.7B: opsional, jangan berharap banyak

Di Windows native, QLoRA 4-bit sering bermasalah karena `bitsandbytes`. Kalau
tetap ingin smoke test, gunakan **WSL2** dan hanya 100 baris:

```powershell
python ai/train/train_lora.py `
  --base-model Qwen/Qwen3-1.7B `
  --train-file ai/data/responder/train.jsonl `
  --val-file ai/data/responder/val.jsonl `
  --output-dir ai/out/local-responder-smoke `
  --limit-train 100 `
  --limit-val 20 `
  --load-in-4bit `
  --disable-thinking `
  --epochs 1 `
  --batch-size 1 `
  --grad-accum 8 `
  --lr 1e-4 `
  --max-seq-length 512 `
  --gradient-checkpointing `
  --save-steps 20 `
  --save-total-limit 2 `
  --resume-from-checkpoint auto
```

Kalau OOM, turunkan `--max-seq-length` ke `384`. Kalau tetap OOM, berhenti.
Responder full tetap butuh Colab/L4 atau GPU dengan VRAM lebih besar.

---

## 9. Yang jangan dilakukan di 4 GB

- Jangan train planner full 100k sampai selesai; waktunya tidak praktis.
- Jangan train responder full 100k di laptop.
- Jangan menganggap adapter dari 2.000 baris sebagai model produksi.
- Jangan mengubah dataset/prompt di tengah resume.
- Jangan pakai prompt desktop lama untuk mengevaluasi model SFT; prompt harus
  sama dengan prompt training di `ai/docs/integration.md`.

---

## 10. Setelah smoke test lokal berhasil

Lanjutkan ke salah satu:

1. **Colab T4/L4** untuk planner full dan responder full, memakai
   `studysphere_colab.ipynb`.
2. **Laptop** hanya untuk eksperimen planner kecil, evaluasi, dan integrasi.
3. **WSL2** kalau ingin mencoba QLoRA responder dalam skala sangat kecil.

Kalau local smoke test sudah menghasilkan `adapter/` dan `run.json`, berarti
kode training-nya sehat; kegagalan berikutnya biasanya soal VRAM/waktu, bukan
soal pipeline.
