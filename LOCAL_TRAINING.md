# StudySphere — Local Training Guide (RTX 2050 4 GB)

Panduan ini untuk menjalankan **step training paling ringan** di laptop saat GPU
Colab sedang tidak tersedia.

Targetnya bukan menghasilkan model final. Targetnya memastikan pipeline benar-benar
jalan:

- Python dan CUDA terdeteksi.
- Hugging Face bisa mengunduh model.
- Chat template dan assistant-only masking bekerja.
- LoRA bisa training.
- Checkpoint dan resume bekerja.
- Adapter dan `run.json` terbentuk.

Untuk model final, tetap gunakan Colab T4/L4 atau GPU lain.

---

## 0. Batas realistis RTX 2050 4 GB

| Model | Status lokal | Catatan |
|---|---|---|
| Planner Gemma 3 270M | **Bisa** | LoRA biasa, batch 1, gradient checkpointing |
| Responder Qwen3 1.7B | **Sangat ketat** | Hanya smoke test 100 baris; QLoRA Windows sebaiknya lewat WSL2 |
| Planner 100k + responder 100k | **Tidak praktis** | Tetap butuh Colab/L4 atau GPU lain |

Step ringan yang dipakai di guide ini:

```text
Planner Gemma 3 270M
2.000 baris train
200 baris validasi
1 epoch
batch size 1
gradient accumulation 16
max sequence length 768
```

Perkiraan waktu: **10–30 menit** setelah model selesai diunduh. Model Gemma 270M
ukurannya sekitar 500 MB.

---

## 1. Cek kondisi awal

Dari root repo:

```powershell
cd D:\studysphere\fileconfig
nvidia-smi
python --version
```

Di mesin ini:

```text
GPU      : NVIDIA GeForce RTX 2050 4 GB
Python   : 3.15.0
PyTorch  : belum terinstall
```

Python 3.15 belum cocok dengan wheel PyTorch stabil. Jangan install PyTorch di
Python 3.15. Kita buat environment Python 3.12 khusus project.

---

## 2. Install Python 3.12

Gunakan Python Install Manager:

```powershell
py install 3.12
py -3.12 --version
```

Kalau `py install` tidak tersedia, install lewat winget:

```powershell
winget install -e --id Python.Python.3.12
```

Pastikan versinya 3.12.x.

---

## 3. Buat virtual environment

```powershell
cd D:\studysphere\fileconfig
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python --version
```

`python --version` harus menampilkan **3.12.x**, bukan 3.15.

Kalau activation diblokir PowerShell, jalankan Python langsung:

```powershell
.\.venv\Scripts\python.exe --version
```

---

## 4. Install PyTorch CUDA

RTX 2050 di mesin ini memakai driver NVIDIA yang mendukung CUDA 12.8. Install
wheel CUDA, bukan wheel CPU:

```powershell
python -m pip install --upgrade pip
python -m pip install "torch>=2.4,<2.9" --index-url https://download.pytorch.org/whl/cu128
```

Lalu dependency training:

```powershell
python -m pip install "transformers>=4.53,<5" "datasets>=3.6,<5" "accelerate>=1.8,<2" "peft>=0.16,<1" sentencepiece protobuf safetensors
```

Catatan:

- `bitsandbytes` tidak wajib untuk planner 270M.
- `bitsandbytes` baru dibutuhkan untuk QLoRA responder.
- Di Windows native, QLoRA/`bitsandbytes` sering bermasalah. Gunakan WSL2 kalau
  ingin mencoba responder.

---

## 5. Verifikasi CUDA

```powershell
python -c "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"
```

Yang diharapkan:

```text
torch 2.8...
cuda True
NVIDIA GeForce RTX 2050
```

Kalau `cuda False`:

1. Pastikan venv aktif.
2. Pastikan yang terinstall wheel `cu128`, bukan CPU.
3. Cek driver NVIDIA.
4. Coba install ulang PyTorch.

Jangan lanjut kalau CUDA masih `False`.

---

## 6. Login Hugging Face

Gemma 3 biasanya gated. Buka halaman model:

```text
https://huggingface.co/google/gemma-3-270m-it
```

Setujui license, lalu login:

```powershell
python -c "from huggingface_hub import login; login()"
```

Tempel token Hugging Face yang punya akses ke model tersebut.

---

## 7. Kosongkan VRAM

Tutup aplikasi GPU yang tidak perlu:

- game
- browser berat
- OBS
- aplikasi video
- aplikasi 3D

Cek:

```powershell
nvidia-smi
```

Target pemakaian awal di bawah sekitar 700 MB, supaya sisa VRAM mendekati 3,5 GB.

Set alokasi CUDA:

```powershell
$env:PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"
```

---

## 8. Pastikan dataset planner ada

Dataset sudah ada di:

```text
ai/data/planner/train.jsonl
ai/data/planner/val.jsonl
```

Kalau belum ada, generate:

```powershell
python ai/synth/generate_planner.py
python ai/synth/validate.py
```

---

## 9. Train planner ringan

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

Penjelasan setting:

| Setting | Fungsi |
|---|---|
| `--limit-train 2000` | Hanya melatih 2.000 baris pertama |
| `--limit-val 200` | Validasi cepat, tidak menunggu 10.000 baris |
| `--batch-size 1` | Menekan VRAM |
| `--grad-accum 16` | Effective batch tetap 16 |
| `--max-seq-length 768` | Cukup untuk prompt planner, lebih hemat dari 1024/1280 |
| `--gradient-checkpointing` | Wajib untuk 4 GB |
| `--save-steps 50` | Checkpoint tiap 50 step |
| `--resume-from-checkpoint auto` | Melanjutkan checkpoint kalau training terhenti |

2.000 baris pertama dataset sudah seimbang dan mencakup semua 14 task planner.

---

## 10. Tanda training berhasil

Di awal, harus muncul baris seperti:

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

Isi `adapter/` biasanya:

```text
adapter_config.json
adapter_model.safetensors
tokenizer...
```

`run.json` menyimpan base model, jumlah baris, hyperparameter, dan checkpoint
terakhir.

Kalau dua output itu ada, pipeline training sudah sehat.

---

## 11. Kalau CUDA out of memory

Coba berurutan:

### 11.1 Tutup aplikasi GPU

```powershell
nvidia-smi
```

Pastikan tidak ada game/browser berat yang memakai VRAM.

### 11.2 Turunkan sequence length

Ubah:

```text
--max-seq-length 768
```

menjadi:

```text
--max-seq-length 640
```

Kalau masih OOM:

```text
--max-seq-length 512
```

### 11.3 Naikkan gradient accumulation

```text
--grad-accum 32
```

Batch tetap `1`.

### 11.4 Pastikan gradient checkpointing aktif

Flag ini harus ada:

```text
--gradient-checkpointing
```

### 11.5 Jangan pakai 4-bit untuk planner

Jangan tambahkan:

```text
--load-in-4bit
```

untuk Gemma 270M. Planner cukup LoRA biasa; 4-bit tidak membantu di kasus ini.

### 11.6 Kalau masih OOM

Hentikan local training. Planner 270M adalah model terkecil yang masuk akal untuk
pipeline ini. Responder 1.7B di 4 GB jauh lebih berat.

---

## 12. Resume kalau training terhenti

Jalankan command yang sama lagi. Karena ada:

```text
--resume-from-checkpoint auto
```

training akan mencari checkpoint terbaru yang punya `trainer_state.json` valid.

Untuk memulai dari nol:

```powershell
--resume-from-checkpoint none
```

atau hapus output:

```powershell
Remove-Item -Recurse -Force ai/out/local-planner
```

Jangan mengubah dataset, prompt, atau hyperparameter di tengah resume.

---

## 13. Responder Qwen3 1.7B di laptop ini

Jangan jadikan responder full sebagai target local.

Kalau hanya ingin smoke test pipeline QLoRA, gunakan WSL2 dan 100 baris:

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

Responder full 100k tetap butuh Colab/L4 atau GPU dengan VRAM lebih besar.

---

## 14. Setelah smoke test lokal berhasil

Lanjutkan dengan urutan ini:

1. Simpan hasil local sebagai bukti pipeline.
2. Tunggu GPU Colab/Kaggle tersedia.
3. Jalankan `studysphere_colab.ipynb` untuk full planner.
4. Full responder memakai subset 25k atau full 100k dengan checkpoint.
5. Merge adapter.
6. Convert ke GGUF.
7. Buat model Ollama.
8. Evaluasi 200 baris, lalu full test.
9. Integrasi desktop dan MQTT.
10. Jalankan baseline.

Jangan memakai adapter 2.000 baris sebagai model produksi.

---

## 15. Checklist

- [ ] Python 3.12 terinstall.
- [ ] Venv aktif dan `python --version` menunjukkan 3.12.
- [ ] PyTorch CUDA terinstall.
- [ ] `torch.cuda.is_available()` bernilai `True`.
- [ ] Hugging Face login dan Gemma license disetujui.
- [ ] VRAM awal di bawah ~700 MB.
- [ ] Dataset planner ada.
- [ ] Training planner 2.000 baris selesai.
- [ ] `adapter/` dan `run.json` terbentuk.
- [ ] Resume diuji sekali (hentikan lalu jalankan ulang).
- [ ] Responder hanya dicoba kalau memakai WSL2 dan 100 baris.

---

## 16. Troubleshooting cepat

| Gejala | Kemungkinan | Solusi |
|---|---|---|
| `No module named torch` | Venv tidak aktif | Aktifkan `.venv` |
| `cuda False` | Wheel CPU | Install wheel `cu128` |
| `CUDA out of memory` | Sequence/batch terlalu besar | `768 -> 640 -> 512`, batch 1 |
| Model 401/403 | Belum login atau license | `huggingface_hub.login()` dan accept license |
| `bitsandbytes` error | Windows native | Pakai WSL2, atau skip responder |
| Training berhenti | Runtime/power | Jalankan command yang sama, `auto` resume |
| Push GitHub ditolak | Branch salah | Pastikan commit ada di branch yang di-push |
| `Everything up-to-date` | Commit ada di branch lain | Cek `git branch -vv`, merge/push branch yang benar |

---

## 17. Perintah ringkas

Kalau environment sudah siap, command intinya hanya:

```powershell
cd D:\studysphere\fileconfig
.\.venv\Scripts\Activate.ps1
$env:PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True"

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
