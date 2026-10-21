# StudySphere: Smart Study Room (UTS/UAS IoT)

Node IoT berbasis ESP32 untuk ruang belajar pintar. Node membaca suhu, kelembapan, cahaya, kualitas udara dan kehadiran orang, menyalakan lampu belajar secara otomatis, menampilkan status di LCD, dan berkomunikasi dengan aplikasi desktop lewat **MQTT**. Aplikasi desktop (Tauri, Windows dan Linux) ada di `desktop/` dan sekaligus menjadi broker MQTT di laptop, jadi sistemnya berdiri sendiri tanpa internet. LLM lokal dikerjakan terpisah dan memakai kontrak MQTT di bawah.

## Komponen dan wiring

| Komponen | Pin modul | ESP32 | Daya |
|---|---|---|---|
| DHT11 (suhu & kelembapan) | DATA | GPIO4 | 3V3 |
| Modul LDR (LM393) | AO | GPIO34 | 5V |
| MQ-135 (kualitas udara / CO2) | AO | GPIO35 | 5V |
| HC-SR501 PIR (kehadiran) | OUT | GPIO27 | 5V |
| LCD 16x2 I2C (alamat 0x27) | SDA, SCL | GPIO21, GPIO22 | 5V |
| LED lampu belajar + resistor 220 Ω | anoda lewat resistor | GPIO13 | |

Semua GND disambung ke GND ESP32. Rangkaiannya ada di `diagram.json` dan bisa dibuka di WireLab atau Wokwi.

Untuk perangkat asli, pakai PCB di `hardware/` (lihat [PCB dan daftar komponen](#pcb-dan-daftar-komponen)). Bedanya dengan rangkaian simulator:

- Modul LDR diberi daya 3V3, jadi output AO-nya tidak pernah melewati batas ADC ESP32 (sekitar 3,1 V).
- AO MQ-135 bisa sampai 5 V, jadi masuk ke GPIO35 lewat pembagi 10 kΩ/15 kΩ dan maksimal 3,0 V.
- LCD tetap 5 V, tapi SDA dan SCL lewat level shifter BSS138, jadi pin ESP32 hanya melihat 3,3 V.
- Aktifkan `#define REAL_BOARD` di `firmware/config.h`. Firmware lalu membaca tegangan asli dengan `analogReadMilliVolts()` dan menghitung LDR 3,3 V serta pembagi MQ-135. Tanpa itu, firmware memakai skala simulator: ADC ESP32 membaca 0 sampai 5 V sebagai 0 sampai 4095.
- Konstanta kalibrasi LDR dan MQ-135 (`LDR_RL10`, `MQ_RZERO` dan seterusnya) ada di atas `firmware.ino`. `MQ_RZERO` sebaiknya dikalibrasi di udara bersih.
- Modul DHT11 tiga pin sudah punya resistor pull-up. PCB juga punya pull-up 10 kΩ (R1), jadi DHT11 empat pin tanpa modul juga bisa disambung lewat kabel jumper.

## PCB dan daftar komponen

Proyek KiCad 10 ada di `hardware/`; buka `hardware/studysphere.kicad_pro`. ERC dan DRC bersih, dan PCB sama dengan skematik (schematic parity).

| File | Isi |
|---|---|
| `hardware/fab/skematik.pdf` | Skematik untuk laporan atau dicetak |
| `hardware/fab/render-top.png`, `render-bottom.png` | Tampilan 3D PCB |
| `hardware/fab/pcb-top.pdf`, `pcb-bottom.pdf` | Jalur tembaga dan silkscreen tiap sisi |
| `hardware/fab/gerber-jlcpcb.zip` | Gerber dan file drill, siap diunggah ke JLCPCB atau PCBWay |
| `hardware/fab/bom.csv` | Daftar komponen dari KiCad |

PCB 2 layer, 102 × 62 mm, tebal 1,6 mm, dengan lubang M3 di keempat sudut. ESP32-DevKitC V4 ditancap ke dua female header 1x19 (jarak baris 25,4 mm): antena di tepi atas, USB di tepi bawah. Di bawah antena tidak ada tembaga. Kedua sisi PCB diisi GND. Jalur daya 0,6 mm, jalur sinyal 0,3 mm.

**Komponen yang disolder ke PCB**

| Jumlah | Komponen | Ref | Fungsi |
|---|---|---|---|
| 6 | Resistor 10 kΩ 1/4 W | R1, R2, R4, R5, R6, R7 | Pull-up DHT11, pembagi MQ-135 (atas), pull-up I2C |
| 1 | Resistor 15 kΩ 1/4 W | R3 | Pembagi MQ-135 (bawah) |
| 1 | Resistor 220 Ω 1/4 W | R8 | Arus LED, sekitar 6 mA |
| 2 | Kapasitor keramik 100 nF (kode 104) | C2, C3 | Filter ADC LDR dan MQ-135 |
| 1 | Kapasitor elektrolit 100 µF 16 V | C1 | Menahan lonjakan arus heater MQ-135 |
| 2 | MOSFET N-channel BSS138 (SOT-23) | Q1, Q2 | Level shifter I2C 3,3 V ke 5 V |
| 1 | LED kuning 5 mm | D1 | Lampu belajar |
| 4 | Pin header male 1x3 | J1 sampai J4 | DHT11, PIR, LDR, MQ-135 |
| 1 | Pin header male 1x4 | J5 | LCD I2C (urutan GND, VCC, SDA, SCL seperti backpack-nya) |
| 2 | Female header 1x19 | U1 | Soket ESP32-DevKitC V4 |
| 4 | Baut M3 dan spacer | H1 sampai H4 | Kaki PCB |

**Modul dan lainnya:** ESP32-DevKitC V4 (38 pin), modul DHT11, HC-SR501, modul LDR, modul MQ-135, LCD 16x2 dengan backpack I2C PCF8574, 16 kabel jumper female-female, dan kabel micro-USB data.

- R2 dan R3 sebaiknya metal film 1% supaya faktor pembagi MQ-135 tepat. Resistor lain boleh 5%.
- Sebelum memesan, ukur jarak dua baris pin DevKit kamu: harus 25,4 mm dengan urutan pin seperti DevKitC V4 resmi (3V3, EN, VP sampai 5V di satu sisi). Beberapa klon 38 pin punya jarak atau urutan lain.
- Urutan solder: Q1 dan Q2 dulu (SMD paling kecil), lalu resistor, kapasitor, LED, pin header, dan female header terakhir. Kaki + C1 masuk ke pad bertanda +, kaki pendek LED (katoda) ke pad kotak.
- Di JLCPCB, unggah `gerber-jlcpcb.zip`, pilih 2 layer dan tebal 1,6 mm, pengaturan lain biarkan default. Kalau PCB diubah di KiCad, buat ulang Gerber lewat **File > Fabrication Outputs**.
- Arus total sekitar 450 mA (ESP32 dengan WiFi, heater MQ-135 sekitar 150 mA, backlight LCD), jadi pakai port USB dan kabel yang bagus.

## Cara kerja

- Sensor dibaca setiap 2 detik. Telemetry dikirim setiap 5 detik, dan langsung saat kehadiran atau lampu berubah.
- **Kehadiran:** PIR mendeteksi gerakan. Sesi belajar dimulai saat ada gerakan dan berakhir setelah 60 detik tanpa gerakan (`IDLE_MS`).
- **Lampu belajar:** mode `auto` menyalakan lampu saat ada orang dan cahaya di bawah 150 lux (`DARK_LUX`). Mode `on` atau `off` bisa dipilih dari aplikasi desktop.
- **Kualitas udara:** CO2 di atas 1000 ppm (`CO2_STALE`) dianggap `stale`, artinya ruangan perlu dibuka atau diberi ventilasi.
- **LCD:** baris 1 berisi suhu, kelembapan dan cahaya (`25.0C 50%  500lx`). Baris 2 berisi CO2 dan status koneksi (`CO2  400ppm MQTT`; `WIFI` berarti WiFi tersambung tapi broker belum, `----` berarti offline). Pesan dari aplikasi desktop tampil di LCD selama 30 detik, maksimal 32 karakter untuk dua baris.

## MQTT

Broker: aplikasi desktop di laptop, port 1883 (lihat [Aplikasi desktop](#aplikasi-desktop)). Semua topik diawali `studysphere/room-01/` (`TOPIC_ROOT` dan `DEVICE_ID` di `firmware/config.h`).

| Topik | Arah | Isi |
|---|---|---|
| `studysphere/room-01/telemetry` | node ke desktop | Semua data, setiap 5 detik dan saat ada perubahan |
| `studysphere/room-01/request` | desktop ke node | Semantic selector: data apa yang diminta |
| `studysphere/room-01/response` | node ke desktop | Jawaban untuk request |
| `studysphere/room-01/command` | desktop ke node | Mengatur lampu dan LCD |
| `studysphere/room-01/status` | node ke desktop | `online` atau `offline` (retained; `offline` dikirim broker sebagai last will saat node putus) |

**Telemetry**

```json
{"temperature":25,"humidity":50,"lighting":500,"co2":400,"air_quality":"good","occupancy":false,"session_duration":0,"lamp":"off","lamp_mode":"auto"}
```

**Request dan response.** Formatnya sama dengan proyek semanticiot, jadi LLM yang sudah dilatih dengan format itu bisa langsung dipakai:

```json
{"task":"comfort_check","required_information":["temperature","humidity","noise"]}
```

```json
{"task":"comfort_check","data":{"temperature":25,"humidity":50},"unknown":["noise"]}
```

**Command:**

```json
{"lamp":"on"}
{"lamp":"auto"}
{"lcd":"Waktunya istirahat 5 menit"}
```

Setelah command lampu, node langsung mengirim telemetry baru sebagai konfirmasi.

**Nama informasi yang dikenal node:**

| Nama | Satuan / nilai |
|---|---|
| `temperature` | °C |
| `humidity` | % RH |
| `lighting` | lux |
| `co2` | ppm (perkiraan dari MQ-135) |
| `air_quality` | `good` atau `stale` |
| `occupancy` | `true` / `false` |
| `session_duration` | detik sejak sesi belajar dimulai |
| `lamp` | `on` / `off` |
| `lamp_mode` | `auto`, `on` atau `off` |

Nama lain dikembalikan di `unknown`. Request dan command yang sama juga bisa dikirim lewat Serial, satu baris JSON per perintah.

## Konfigurasi

Semua pengaturan jaringan ada di `firmware/config.h`: SSID dan password WiFi, host dan port broker, user dan password MQTT, `TOPIC_ROOT` dan `DEVICE_ID`. `MQTT_HOST` otomatis `127.0.0.1` di simulator lokal WireLab (makro `WIRELAB_SIM`). Untuk board asli, isi dengan IP laptop yang ditampilkan aplikasi desktop.

## Aplikasi desktop

Aplikasi di `desktop/` adalah *island*: pulau hitam kecil yang menggantung di tengah atas layar, dengan maskot burung hantu yang menjaga ruang belajar. Wajah dan kalimat burung hantu mengikuti keadaan ruangan: tidur saat ruangan kosong, senang saat ada yang belajar, pusing saat udara pengap, dan kaget saat node tidak mengirim data.

| Yang dilakukan | Yang terjadi |
|---|---|
| Arahkan mouse ke garis kecil di tengah atas layar | Island muncul satu baris: suhu, kelembapan, CO2 dan lampu |
| Klik island | Island terbuka: semua data, mode lampu (auto, on, off) dan pesan untuk LCD |
| `Esc`, atau mouse keluar | Island menutup lagi (8 detik setelah terbuka, 20 detik setelah satu baris) |
| Udara pengap atau node putus | Island terbuka sendiri |
| Tombol **Quit** di island yang terbuka | Menutup aplikasi |
| Shortcut keyboard, tombol mic, atau ketik di kolom obrolan | Ngobrol dengan burung hantu: tanya keadaan ruangan atau minta ia mengubah sesuatu (lihat di bawah) |
| Udara pengap atau ruangan terlalu panas | Burung hantu langsung bicara memberi tahu, lalu mengingatkan lagi tiap 10 menit selama keadaannya belum membaik |

Saat dibuka, aplikasi menjalankan broker MQTT sendiri di port 1883 pada semua interface laptop, jadi tidak perlu Mosquitto atau internet. Kalau port 1883 sudah dipakai broker lain (misalnya Mosquitto), aplikasi memakai broker itu. Aplikasi mengikuti `DEVICE_ID` apa pun yang mengirim telemetry.

### Ngobrol dengan burung hantu

Tekan shortcut (atau tombol mic) lalu bicara: island terbuka, burung hantu berbunyi "hoo-hoo" dan mendengarkan. Rekaman berhenti sendiri saat kamu diam, atau tekan shortcut/mic lagi untuk langsung mengirim; `Esc` membatalkan, dan menekan lagi saat burung hantu bicara membuatnya diam. Mic hanya terbuka selama ia mendengarkan. Batas suara mengikuti noise ruangan dan bisa disetel lewat `VOICE_RATIO` dan `VOICE_MIN` di `desktop/ui/index.html` (`node desktop/ears.test.js` mengecek kapan rekaman berhenti). Bisa juga mengetik di kolom obrolan.

| Sistem | Shortcut |
|---|---|
| Windows, macOS, Linux X11 | `Ctrl+Alt+Space`, langsung dari aplikasi (kalau sudah dipakai aplikasi lain, hint di island tidak menyebutnya) |
| Linux Wayland (Hyprland, Sway, KDE, GNOME) | Aplikasi tidak bisa memasang shortcut global sendiri di Wayland. Buat shortcut di desktop yang menjalankan `studysphere --talk`; perintah itu diteruskan ke aplikasi yang sedang jalan |

Contoh Hyprland (config Lua), `SUPER + H`:

```lua
hl.bind("SUPER + H", hl.dsp.exec_cmd("studysphere --talk")) -- StudySphere: talk to the owl
```

Saat development (`npm run tauri dev`), ganti `studysphere` dengan path lengkap `desktop/src-tauri/target/debug/studysphere`. Aplikasi hanya jalan satu instance: membukanya lagi tidak membuat island kedua.

Burung hantu juga bicara sendiri kalau ruangan sudah parah: udara pengap (CO2 di atas 1000 ppm, atau MQ-135 stasiun `poor`/alarm) atau suhu 32 °C ke atas (`HOT_C`). Ia memberi tahu apa yang harus dilakukan, dalam Bahasa Indonesia (atau bahasa yang terakhir kamu pakai dengannya), dan mengulang tiap 10 menit (`REMIND_MS`) selama keadaannya belum membaik. Burung hantu menjawab lewat suara dan balon kata, dalam bahasa yang kamu pakai, dan bisa menjalankan perintah ke node: lampu (auto, on, off) dan LCD untuk `firmware/`, atau kipas, ventilasi dan LCD untuk stasiun di `alatnya cu/`. Perintah dari LLM hanya diteruskan kalau nilainya sama dengan yang bisa dikirim tombol.

| Bagian | Sekarang (sementara) | Diatur lewat |
|---|---|---|
| Speech to text | Groq `whisper-large-v3-turbo` | |
| Otak (chat) | Groq `openai/gpt-oss-120b`, endpoint OpenAI-compatible | `STUDYSPHERE_LLM_URL`, `STUDYSPHERE_LLM_MODEL`, `STUDYSPHERE_LLM_KEY` |
| Suara | Groq `canopylabs/orpheus-v1-english` (suara bahasa Inggris, tapi kalimat Indonesia tetap jelas) | |

API key Groq dibaca dari environment shell yang menjalankan aplikasi: `GROQ_API_KEY`, atau `GROQ_API_KEY1` sampai `GROQ_API_KEY9`. Aplikasi mencoba key satu per satu sampai ada yang berhasil, jadi key yang kena rate limit dilewati. Model TTS Orpheus harus di-*accept terms*-nya dulu oleh admin org di [console Groq](https://console.groq.com/playground?model=canopylabs%2Forpheus-v1-english); tanpa itu burung hantu tetap menjawab, hanya tanpa suara. Kalau aplikasi dibuka dari menu desktop (bukan dari terminal), variabel ini harus ada di environment sesi login.

Untuk pindah ke model SFT lokal di Ollama, jalankan aplikasi dengan:

```sh
STUDYSPHERE_LLM_URL=http://localhost:11434/v1 STUDYSPHERE_LLM_MODEL=nama-model-sft npm run tauri dev
```

Model menerima system prompt berisi data ruangan terbaru dan daftar perintah yang boleh dipakai, lalu harus menjawab satu objek JSON: `{"say": "...", "mood": "happy", "command": {"lamp": "on"}}` (`command` boleh `null`). Data latih SFT sebaiknya memakai format yang sama. Di Windows, Ollama perlu `OLLAMA_ORIGINS=*` supaya bisa dipanggil dari aplikasi. Whisper dan suara tetap memakai Groq sampai ada pengganti lokal.

Panduan lengkap untuk membuat dan memasang model lokal sendiri — dataset sintetis, LoRA Gemma 3 270M + Qwen3 1.7B, evaluasi, sampai integrasi dua tahap — ada di [`ai/README.md`](ai/README.md). Untuk training di Colab, gunakan notebook [`studysphere_colab.ipynb`](studysphere_colab.ipynb); checkpoint otomatis disimpan ke Google Drive dan bisa dilanjutkan setelah runtime habis. Kalau GPU Colab sedang tidak tersedia, step ringan di laptop 4 GB ada di [`ai/docs/local-training.md`](ai/docs/local-training.md).

Screenshot island (tersembunyi, satu baris, terbuka) ada di `desktop/screenshots/`.

Di Linux, island memakai layer-shell supaya tetap di atas layar (KDE, Hyprland, Sway, COSMIC). Pasang `gtk-layer-shell` (Arch) atau `libgtk-layer-shell0` (Debian/Ubuntu). Tanpa itu, atau di GNOME, island menjadi jendela biasa yang selalu di atas, dan posisinya ditentukan oleh desktop.

Syarat untuk build: [Rust](https://rustup.rs), Node.js, dan prasyarat Tauri untuk sistemnya ([daftar lengkap](https://v2.tauri.app/start/prerequisites/)): di Linux `webkit2gtk-4.1`, di Windows Microsoft C++ Build Tools (WebView2 sudah ada di Windows 10 dan 11).

```sh
cd desktop
npm install
npm run tauri dev     # jalankan
npm run tauri build   # installer: .msi dan .exe di Windows, .deb, .rpm dan .AppImage di Linux
```

Installer Windows harus di-build di Windows (Tauri tidak bisa cross-compile dari Linux). Hasilnya ada di `desktop/src-tauri/target/release/bundle/`.

Supaya ESP32 asli bisa tersambung ke laptop:

- ESP32 dan laptop harus di WiFi yang sama. WiFi kampus sering memblokir koneksi antar perangkat; kalau begitu, pakai hotspot HP atau hotspot laptop.
- Windows menanyakan izin firewall saat aplikasi pertama kali dibuka. Izinkan untuk jaringan **Private**, lalu pastikan WiFi-nya diset sebagai Private. Di Linux dengan ufw: `sudo ufw allow 1883/tcp`.
- Broker tidak memakai password, jadi siapa pun di WiFi yang sama bisa membaca dan mengirim command. Untuk presentasi di jaringan bersama, pakai hotspot sendiri.

## Menjalankan

### Di simulator WireLab (lokal, tanpa kuota)

Buka aplikasi desktop, lalu buka folder ini di WireLab dan tekan **Run**. Proyek ini memakai **simulator lokal** WireLab (`"engine": "local"` di `wirelab.json`):

- Firmware di-compile untuk PC dan berjalan di PC, jadi tidak memakai server atau kuota Wokwi.
- WiFi memakai jaringan PC, dan node tersambung ke broker aplikasi desktop di `127.0.0.1:1883`.
- Sensor diatur dengan mengklik part saat simulasi berjalan: slider cahaya (LDR), CO2 (MQ-135), suhu dan kelembapan (DHT11), serta tombol gerakan (PIR). LED dan LCD ikut menampilkan hasilnya.
- Syaratnya: compiler C++ (`g++` atau `clang++`) dan arduino-cli.

Simulator lokal mensimulasikan perilaku, bukan chip-nya: timing sinyal DHT11 dan I2C LCD tidak disimulasikan (library-nya diganti versi tiruan yang memakai nilai dari WireLab). Untuk menguji di mesin Wokwi, ganti simulator di **Settings** (memakai kuota CI: akun Free mendapat 50 menit per 30 hari), atau jalankan perintah di bawah. Mesin Wokwi berjalan di server Wokwi, jadi tidak bisa menjangkau broker di laptop; tes ini hanya memakai Serial.

```sh
arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir firmware/build firmware
wokwi-cli --scenario test.scenario.yaml --timeout 60000 .
```

### Stasiun ESP32 + UNO (`alatnya cu/`) di WireLab

`alatnya cu/` juga proyek WireLab sendiri: buka folder itu di WireLab (**Open…**), lalu tekan **Run**. Rangkaiannya (`alatnya cu/diagram.json`) sama dengan alat fisiknya. ESP32 memakai DHT11, HC-SR04, servo SG90 dan LCD; UNO memakai MQ-135, modul LDR, LCD dan kipas di D13+D12; keduanya tersambung lewat UART D1→GPIO27 dan GPIO26→D0. Kedua sketch dijalankan apa adanya, tanpa diubah.

- `wirelab.json` menentukan sketch untuk tiap board (`"boards"`) dan mengalihkan `MQTT_HOST` di sketch ESP32 (`192.168.1.10`) ke broker aplikasi desktop di laptop ini (`"hosts"`).
- Saat simulasi berjalan, klik part untuk mengatur jarak HC-SR04, cahaya, MQ-135, serta suhu dan kelembapan. Kipas berputar saat menyala, servo ikut bergerak, dan kedua LCD tampil.
- Simulasi memakai `DEVICE_ID` yang sama dengan alat fisik (`station01`). Matikan alat fisiknya dulu supaya perintah dari desktop tidak sampai ke dua-duanya.
- MQ-135 tetap butuh pemanasan 2 menit (`MQ_WARMUP_MS` di sketch UNO), sama seperti di alat fisik.

### Di ESP32 asli

1. Pasang library (sekali saja):
   ```sh
   arduino-cli lib install "DHT sensor library" "Adafruit Unified Sensor" "LiquidCrystal I2C" PubSubClient ArduinoJson
   ```
2. Isi WiFi kamu dan `MQTT_HOST` (IP laptop, ditampilkan aplikasi desktop) di `firmware/config.h`, dan aktifkan `#define REAL_BOARD` di file yang sama (rangkaian PCB di `hardware/`).
3. Compile, upload, lalu lihat serial monitor (ganti `/dev/ttyUSB0` sesuai port ESP32):
   ```sh
   arduino-cli compile --fqbn esp32:esp32:esp32 firmware
   arduino-cli upload -p /dev/ttyUSB0 --fqbn esp32:esp32:esp32 firmware
   arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200
   ```
4. Buka aplikasi desktop. Serial monitor menampilkan `MQTT connected` setelah node tersambung.

## Mengetes MQTT tanpa aplikasi desktop

Selama aplikasi desktop terbuka, broker-nya bisa dipakai alat lain. Pakai [MQTT Explorer](https://mqtt-explorer.com/) (GUI), atau `mosquitto_sub` / `mosquitto_pub` dari paket mosquitto:

```sh
# lihat semua pesan dari node
mosquitto_sub -h localhost -t 'studysphere/room-01/#' -v

# minta data seperti yang akan dilakukan LLM
mosquitto_pub -h localhost -t studysphere/room-01/request \
  -m '{"task":"comfort_check","required_information":["temperature","humidity","co2"]}'

# atur lampu dan LCD
mosquitto_pub -h localhost -t studysphere/room-01/command -m '{"lamp":"on"}'
mosquitto_pub -h localhost -t studysphere/room-01/command -m '{"lcd":"Waktunya istirahat"}'
```

## Struktur folder

```text
firmware/firmware.ino   program ESP32
firmware/config.h       WiFi, broker MQTT, topik
desktop/                aplikasi desktop dan broker MQTT (Tauri)
desktop/screenshots/    screenshot UI desktop
alatnya cu/             stasiun ESP32 + UNO, dengan proyek WireLab-nya
hardware/               skematik, PCB dan library KiCad 10
hardware/fab/           PDF skematik, BOM, Gerber, render PCB
diagram.json            rangkaian (WireLab / Wokwi)
wokwi.toml              lokasi firmware dan custom chip untuk simulator
chips/                  DHT11, MQ-135 dan HC-SR501 untuk simulator
test.scenario.yaml      tes otomatis lewat Serial di simulator
libraries.txt           daftar library Arduino
```
