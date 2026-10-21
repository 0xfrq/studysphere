# Integrasi StudySphere AI ke desktop dan MQTT

Dokumen ini menunjukkan cara menyambungkan model hasil fine-tuning ke aplikasi desktop yang sudah ada, tanpa mengubah kontrak MQTT dan tanpa memindahkan logika keselamatan ke model.

Ada dua tahap:

```text
user berbicara / mengetik
        │
        ▼
[Planner]  required_information + task
        │
        ├── kosong? ──► langsung ke responder
        │
        ▼
[Retriever]  cache → request MQTT → response
        │
        ▼
[Responder]  {"say","mood","command"}
        │
        ├── validasi command (whitelist)
        ▼
MQTT command + suara + balon kata
```

Prinsip yang tidak boleh dilanggar:

1. **Model tidak pernah memilih topik MQTT.** Topik ditentukan oleh kode.
2. **Model tidak pernah mengirim command mentah.** Command divalidasi dan diterjemahkan dulu.
3. **Prompt saat inference harus sama dengan prompt saat training.** Kalau berbeda, model SFT akan turun.
4. **Jangan mengarang data.** Kalau field tidak ada, masukkan ke `MISSING`, bukan diisi angka default.
5. **Alert tidak boleh mengirim command.**

---

## 1. Topik MQTT yang harus ditangani

Desktop sudah berlangganan `+/+/+` dan mengenali akhiran topik. Dua device punya nama topik yang berbeda:

| Device | Push | Request | Response | Command | State |
|---|---|---|---|---|---|
| Node (`firmware/`) | `studysphere/room-01/telemetry` tiap 5 s | `.../request` | `.../response` | `.../command` | — |
| Station (`alatnya cu/`) | `.../stream` jika `stream_ms` aktif | `.../req` | `.../resp` | `.../cmd` | `.../state` retained |

Contoh:

```text
studysphere/room-01/telemetry
studysphere/room-01/request
studysphere/room-01/response
studysphere/room-01/command

studysphere/station01/state
studysphere/station01/req
studysphere/station01/resp
studysphere/station01/cmd
studysphere/station01/stream
```

Perbedaan penting:

- Node membalas `response` dengan `{task,data,unknown}` dan **tidak** mengembalikan `id`.
- Station membalas `resp` dengan `{id,task,data,missing,meta}` dan **mengembalikan `id`**.
- Station bisa `mode:"raw" | "semantic" | "cost_aware"`, `budget`, dan `max_age_ms`. Node tidak; node hanya mengembalikan informasi yang diminta dari state terakhirnya.
- Station mengirim `state` retained berisi `{fan,vent,arduino_alive,stream_ms}`. Node mengirim `telemetry` lengkap.

Karena node tidak punya `id`, hanya boleh ada **satu request node yang sedang berjalan**. Kode di bawah memakai satu `pending` global, bukan map banyak request.

---

## 2. Kontrak data

### 2.1 Planner

Input planner:

```json
{
  "device_type": "station",
  "device_id": "station01",
  "available_information": [
    "temperature", "humidity", "lighting", "air_quality",
    "occupancy", "session_duration", "mh_sensor", "fan", "vent"
  ],
  "known_state": {"fan": false, "vent": 0},
  "previous": null
}
```

User message:

```text
CONTEXT: {"device_type":"station",...}
REQUEST: "Udara di sini pengap, sebaiknya buka jendela?"
```

Output:

```json
{"task":"ventilation_decision","required_information":["air_quality"]}
```

Aturan penting:

- `available_information` memakai daftar **lengkap** registry device, bukan hanya sensor yang sedang terbaca. Kalau sensor hilang, tetap ada di daftar; nanti masuk `missing`.
- `known_state` node: `{"lamp":"on","lamp_mode":"auto"}`.
- `known_state` station: `{"fan":false,"vent":0}`.
- `co2` hanya ada di node. Station memakai `air_quality` (level).
- `previous` dipakai untuk progressive retrieval: `{"task":"...","data":{...},"missing":{...}}`.

Daftar yang dipakai:

```js
const DEVICE_INFO = {
  node: [
    'temperature', 'humidity', 'lighting', 'air_quality', 'co2',
    'occupancy', 'session_duration', 'lamp', 'lamp_mode',
  ],
  station: [
    'temperature', 'humidity', 'lighting', 'air_quality',
    'occupancy', 'session_duration', 'mh_sensor', 'fan', 'vent',
  ],
};
```

### 2.2 Responder

Facts node:

```json
{
  "temperature": 27,
  "humidity": 55,
  "lighting": 420,
  "co2": 780,
  "air_quality": "good",
  "occupancy": true,
  "session_duration": 1800,
  "lamp": "on",
  "lamp_mode": "auto"
}
```

Facts station:

```json
{
  "temperature": 30,
  "humidity": 62,
  "bright": false,
  "air_quality": "moderate",
  "occupancy": true,
  "session_duration": 900,
  "distance_cm": 52,
  "fan": false,
  "vent": 0,
  "fan_mode": "auto"
}
```

Jangan memberi station field `co2`, `lamp`, atau `lamp_mode`. Jangan memberi node field `fan`, `vent`, atau `bright`. Model dilatih untuk menolak capability yang tidak ada; kalau faktanya salah, penolakannya juga salah.

`MISSING` berisi alasan:

```json
{"temperature": "sensor_error", "air_quality": "budget"}
```

Nilai yang dikenal: `sensor_error`, `timeout`, `budget`, `arduino_offline`.

### 2.3 Response device

Node:

```json
{"task":"thermal_comfort_check","data":{"temperature":31},"unknown":[]}
```

Station:

```json
{
  "id":"ai-7",
  "task":"ventilation_decision",
  "mode":"cost_aware",
  "data":{
    "air_quality":{"raw":310,"level":"poor","alarm":true}
  },
  "missing":{},
  "meta":{"sensor_reads":1,"sources":["arduino_uart"],"uart_bytes":18,"read_ms":41,"latency_ms":43,"payload_bytes":180}
}
```

Station membungkus `lighting` dan `air_quality` sebagai objek, dan `occupancy` sebagai objek. Node mengembalikan nilai datar. Jangan memakai parser yang sama untuk keduanya.

---

## 3. Konfigurasi

Tambahkan tiga setting di `desktop/src-tauri/src/main.rs`:

```rust
fn ai_settings() -> Value {
    let env = |k: &str| std::env::var(k).ok().filter(|v| !v.is_empty());
    // ... groqKeys yang sudah ada ...
    json!({
        "groqKeys": keys,
        "llmUrl": env("STUDYSPHERE_LLM_URL")
            .unwrap_or_else(|| "https://api.groq.com/openai/v1".into()),
        "plannerModel": env("STUDYSPHERE_PLANNER_MODEL")
            .unwrap_or_else(|| "studysphere-planner".into()),
        "responderModel": env("STUDYSPHERE_RESPONDER_MODEL")
            .unwrap_or_else(|| "studysphere-responder".into()),
        "aiMode": env("STUDYSPHERE_AI_MODE")
            .unwrap_or_else(|| "two_stage".into()),
        "llmKey": env("STUDYSPHERE_LLM_KEY"),
    })
}
```

Build ulang Tauri setelah mengubah Rust. Lalu jalankan:

```powershell
$env:STUDYSPHERE_LLM_URL="http://localhost:11434/v1"
$env:STUDYSPHERE_PLANNER_MODEL="studysphere-planner"
$env:STUDYSPHERE_RESPONDER_MODEL="studysphere-responder"
$env:STUDYSPHERE_AI_MODE="two_stage"
$env:OLLAMA_ORIGINS="*"
npm run tauri dev
```

Di Linux/macOS:

```sh
STUDYSPHERE_LLM_URL=http://localhost:11434/v1 \
STUDYSPHERE_PLANNER_MODEL=studysphere-planner \
STUDYSPHERE_RESPONDER_MODEL=studysphere-responder \
STUDYSPHERE_AI_MODE=two_stage \
npm run tauri dev
```

`STUDYSPHERE_AI_MODE`:

- `two_stage` — planner + responder (default).
- `responder_only` — satu model, semua fakta cache dikirim; untuk demo cepat.
- `off` — kembali ke perilaku lama/Groq.

---

## 4. Prompt yang harus sama dengan training

### 4.1 Planner

Salin dari `ai/synth/generate_planner.py`:

```js
const PLANNER_SYSTEM = `You are the sensing planner of StudySphere, a cost-aware semantic IoT study room.
Given the room context and the user's request, choose the minimum sensor information needed to answer or act.
Return exactly one JSON object, no markdown and no explanation:
{"task":"<task>","required_information":["<information>", ...]}

Tasks: room_status, study_environment_assessment, thermal_comfort_check, air_quality_check, lighting_check, occupancy_check, study_break_advice, ventilation_decision, energy_saving_check, lamp_control, fan_control, vent_control, lcd_message, general_chat.

Rules:
1. Use only names that appear in context.available_information.
2. Include an item only if its value can change the answer or the action. Never add data "just in case".
3. For a pure command (lamp, fan, vent, LCD) or chit-chat, required_information is [].
4. air_quality is a freshness level. co2 is raw estimated ppm and exists only on the room node. If co2 is unavailable, use air_quality.
5. For a lighting decision, include lighting plus occupancy and lamp_mode when the answer depends on whether the lamp should be on.
6. For ventilation, include air_quality; add temperature only when heat is mentioned.
7. For thermal comfort, include humidity only when the user mentions stickiness, sweat, mold, dryness or general comfort; a simple hot/cold question needs temperature only.
8. If context.previous exists, return only information that is still missing or newly requested, keep previous.task, and never repeat keys already present in previous.data.
9. Never invent sensor names. Output valid JSON only.`;
```

Fungsi planner:

```js
async function planRequest(text, deviceType, knownState, previous = null) {
  const context = {
    device_type: deviceType,
    device_id: deviceType === 'node' ? 'room-01' : 'station01',
    available_information: DEVICE_INFO[deviceType],
    known_state: knownState,
    previous,
  };
  const user = `CONTEXT: ${JSON.stringify(context)}\nREQUEST: ${JSON.stringify(text)}`;
  const out = await callJson(
    info.ai.llmUrl,
    info.ai.plannerModel || 'studysphere-planner',
    [{ role: 'system', content: PLANNER_SYSTEM }, { role: 'user', content: user }],
    8000,
  );

  const task = TASKS.has(out.task) ? out.task : 'general_chat';
  const allowed = new Set(DEVICE_INFO[deviceType]);
  const required = Array.isArray(out.required_information)
    ? [...new Set(out.required_information.filter((name) => allowed.has(name)))]
    : [];
  return { task, required_information: required };
}
```

`TASKS`:

```js
const TASKS = new Set([
  'room_status', 'study_environment_assessment', 'thermal_comfort_check',
  'air_quality_check', 'lighting_check', 'occupancy_check',
  'study_break_advice', 'ventilation_decision', 'energy_saving_check',
  'lamp_control', 'fan_control', 'vent_control', 'lcd_message', 'general_chat',
]);
```

Jangan menambahkan aturan baru di prompt inference kalau aturan itu tidak ada saat training. Kalau mau mengubah perilaku, ubah dataset dan latih ulang.

### 4.2 Responder

```js
const MOODS = new Set([
  'happy', 'normal', 'sleepy', 'dizzy', 'wide', 'love', 'wink', 'thinking',
]);

function responderSystem(deviceType, facts, { ageS, online, missing }) {
  const isNode = deviceType === 'node';
  const kind = isNode ? 'room node' : 'learning station';
  const commands = isNode
    ? `"lamp": "auto" | "on" | "off" (the study lamp), "lcd": "at most 32 characters"`
    : `"fan": "auto" | "on" | "off" (auto: on from 30 C, off at 29 C), "vent": "open" | "closed", "lcd": "at most 32 characters"`;
  const legend = isNode
    ? `temperature: degrees Celsius; humidity: percent RH; lighting: lux; co2: estimated ppm from an MQ-135 (not a true CO2 sensor); air_quality: good or stale; occupancy: a study session is running; session_duration: seconds; lamp: on/off; lamp_mode: auto/on/off.`
    : `temperature: degrees Celsius; humidity: percent RH; bright: the light sensor says bright (false = dark); air_quality: good/moderate/poor/warmup; occupancy: a study session is running; session_duration: seconds; distance_cm: distance to the person; fan: on/off; vent: 0-180 degrees (open is about 180); fan_mode: auto/on/off.`;
  const freshness = online
    ? `Readings from ${ageS} s ago.`
    : `Readings from ${ageS} s ago. The node has gone quiet: these are out of date and commands will not reach it.`;

  return `You are the owl who looks after a StudySphere smart ${kind}. You live in a small island at the top of the student's screen, keep an eye on the room and chat with the student. Be warm, brief and a little playful.
Answer with one JSON object and nothing else:
{"say": "...", "mood": "...", "command": {...} or null}
say: what you say out loud, at most two short sentences, in the language the student used. No emoji, lists or markdown. Round readings to whole numbers and write units as words in that language (28 derajat, 60 persen).
mood: one of happy, normal, sleepy, dizzy, wide, love, wink, thinking.
command: what to change in the room, or null. Only these keys and values: ${commands}. Only send a command when the student asks for a change, and say what you changed. If this ${kind} does not support a command, say so and use null.
A message that starts with [alert] comes from the room's sensors, not the student: pass the warning on, briefly and kindly, look dizzy or wide, and send no command.
Only use the facts below; never make up a reading. If something is not measured, say so.
${freshness}
FACTS: ${JSON.stringify(facts)}
Legend: ${legend}
MISSING: ${JSON.stringify(missing)}`;
}
```

`JSON.stringify` menghasilkan JSON rapat seperti dataset. Urutan key facts harus sama dengan urutan di dataset:

- Node: `temperature, humidity, lighting, co2, air_quality, occupancy, session_duration, lamp, lamp_mode`.
- Station: `temperature, humidity, bright, air_quality, occupancy, session_duration, distance_cm, fan, vent, fan_mode`.

Kalau urutannya berbeda, biasanya model tetap bekerja, tetapi konsistensi membantu.

---

## 5. Panggilan model

```js
async function callJson(baseUrl, model, messages, timeoutMs = 20000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(baseUrl.replace(/\/$/, '') + '/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        messages,
        temperature: 0,
        max_tokens: 320,
        stream: false,
      }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`${res.status} ${res.statusText} ${body}`.trim());
    }
    const payload = await res.json();
    return parseJson(payload.choices[0].message.content);
  } finally {
    clearTimeout(timer);
  }
}

function parseJson(raw) {
  try {
    return JSON.parse(raw);
  } catch {
    const start = raw.indexOf('{');
    const end = raw.lastIndexOf('}');
    if (start >= 0 && end > start) return JSON.parse(raw.slice(start, end + 1));
    throw new Error('model did not return JSON');
  }
}
```

Catatan:

- `response_format` tidak dipakai di contoh ini karena tidak semua versi Ollama menerimanya. Kalau endpoint-mu mendukung, tambahkan `response_format: {type:'json_object'}` dan uji dulu.
- `temperature: 0` untuk evaluasi dan demo yang bisa diulang. Untuk variasi bahasa, tetap 0; variasi datang dari input, bukan sampling.
- `max_tokens: 320` cukup untuk planner/responder. Jangan biarkan model menulis esai.
- Kalau memakai API key, tambahkan header `Authorization` seperti `call()` yang sudah ada di desktop.

---

## 6. Cache dan pengambilan data

### 6.0 State yang perlu disimpan

Tambahkan satu objek `room` supaya fakta, timestamp, dan request tidak tercampur dengan variabel UI:

```js
const room = {
  device: null,       // 'node' | 'station'
  base: null,         // 'studysphere/room-01/'
  lastSeen: 0,        // kapan data terakhir masuk
  statusOnline: null, // dari topik status (retained LWT)
  facts: {},
  fieldSeen: {},      // nama informasi -> timestamp
  missing: {},
  fan: null,
  vent: null,
  fanMode: 'auto',
  sessionStart: 0,
  lastPresent: 0,
};
```

Saat topik pertama masuk:

```js
function adoptDevice(device, topic) {
  const kind = topic.slice(topic.lastIndexOf('/') + 1);
  room.device = device;
  room.base = topic.slice(0, -kind.length);
}
```

Di `onMessage` yang sudah ada:

```js
if (kind === 'state' && !room.device) adoptDevice('station', topic);
if (kind === 'telemetry' && !room.device) adoptDevice('node', topic);
```

Aplikasi ini mengasumsikan **satu device aktif**. Kalau node dan station hidup bersamaan, pilih salah satu lewat setting, jangan menebak dari topik terakhir.

Topik `status` berisi string `online`/`offline` (retained LWT), bukan JSON:

```js
if (kind === 'status') {
  room.statusOnline = json === 'online';
  return;
}
```

`knownState()`:

```js
function knownState() {
  if (room.device === 'node') {
    return {
      lamp: room.facts.lamp ?? 'off',
      lamp_mode: room.facts.lamp_mode ?? 'auto',
    };
  }
  return {
    fan: room.fan ?? false,
    vent: room.vent ?? 0,
  };
}
```

### 6.1 TTL

| Informasi | TTL | Alasan |
|---|---:|---|
| `temperature`, `humidity` | 60 s | berubah lambat |
| `lighting`, `bright`, `mh_sensor` | 10 s | cahaya bisa berubah cepat |
| `air_quality`, `co2` | 30 s | butuh waktu untuk berubah |
| `occupancy`, `session_duration` | 3 s | orang bisa datang/pergi |
| `lamp`, `lamp_mode`, `fan`, `vent`, `fan_mode` | 0 | state, selalu dianggap fresh |

TTL ini sama dengan `ai/spec/taxonomy.json` dan firmware station. Kalau mengubah TTL, ubah di satu tempat dan catat di laporan.

### 6.2 Menentukan yang perlu diambil

```js
const TTL_MS = {
  temperature: 60000,
  humidity: 60000,
  lighting: 10000,
  bright: 10000,
  mh_sensor: 10000,
  air_quality: 30000,
  co2: 30000,
  occupancy: 3000,
  session_duration: 3000,
  lamp: 0,
  lamp_mode: 0,
  fan: 0,
  vent: 0,
  fan_mode: 0,
};

function isFresh(name) {
  if (TTL_MS[name] === 0) return true;
  if (name === 'session_duration') return isFresh('occupancy');
  const seen = room.fieldSeen[name];
  return seen != null && Date.now() - seen <= TTL_MS[name];
}
```

Untuk node, telemetry setiap 5 s memperbarui semua field sekaligus. Jadi biasanya tidak perlu request; request hanya untuk memastikan nilai yang diminta masih ada saat cache lokal sudah tua.

Untuk station, `state` hanya berisi fan/vent. Sensor harus diminta lewat `req`; `resp` memperbarui field yang diminta.

### 6.3 Request

```js
let pending = null; // hanya satu request per device

function requestDevice(required, { budget = 2, mode = 'cost_aware' } = {}) {
  return new Promise((resolve) => {
    if (!required.length) return resolve({ data: {}, missing: {} });
    if (pending) return resolve({ data: {}, missing: Object.fromEntries(required.map((k) => [k, 'busy'])) });

    const id = `ai-${Date.now()}`;
    const payload = {
      id,
      task: 'desktop_ai',
      required_information: required,
    };
    if (room.device === 'station') {
      payload.mode = mode;
      payload.budget = budget;
    }

    const timer = setTimeout(() => {
      pending = null;
      resolve({ data: {}, missing: Object.fromEntries(required.map((k) => [k, 'timeout'])) });
    }, 2500);

    pending = { id, required, timer, resolve };
    invoke('publish', {
      topic: room.base + (room.device === 'station' ? 'req' : 'request'),
      payload: JSON.stringify(payload),
    }).catch((err) => {
      clearTimeout(timer);
      pending = null;
      resolve({ data: {}, missing: Object.fromEntries(required.map((k) => [k, String(err)])) });
    });
  });
}
```

Catatan:

- Node tidak mendukung `budget`; jangan kirim field yang tidak dikenal. Budget node ditegakkan di desktop dengan hanya meminta subset.
- Station `budget` menghitung **source**, bukan field. `temperature + humidity` tetap satu source.
- Timeout 2.5 s cukup untuk station yang membaca UNO; ukur ulang di hardware.
- Kalau `pending` sudah ada, lebih baik antrekan daripada mengembalikan `busy`. Contoh ini memilih gagal cepat supaya tidak ada request bersarang.

### 6.4 Menerima response

Tambahkan penanganan `response` (node) dan `resp` (station) di `onMessage`:

```js
function resolvePending(msg) {
  if (!pending) return false;
  const matches = room.device === 'station'
    ? msg.id === pending.id
    : msg.task === 'desktop_ai';
  if (!matches) return false;

  clearTimeout(pending.timer);
  const request = pending;
  pending = null;

  if (room.device === 'station') {
    mergeStationResponse(msg);
  } else {
    mergeNodeResponse(msg);
  }

  // Node memakai array "unknown", station memakai objek "missing".
  const missing = msg.missing
    || Object.fromEntries((msg.unknown || []).map((name) => [name, 'unknown']));
  request.resolve({ data: msg.data || {}, missing });
  return true;
}
```

Di `onMessage(topic, json)` yang sudah ada:

```js
if (kind === 'response' || kind === 'resp') {
  let msg;
  try { msg = JSON.parse(json); } catch { return; }
  if (resolvePending(msg)) return;
}
```

### 6.5 Normalisasi station

```js
function mergeStationResponse(msg) {
  const d = msg.data || {};
  const now = Date.now();

  if (d.temperature != null) {
    room.facts.temperature = d.temperature;
    room.fieldSeen.temperature = now;
    delete room.missing.temperature;
  }
  if (d.humidity != null) {
    room.facts.humidity = d.humidity;
    room.fieldSeen.humidity = now;
    delete room.missing.humidity;
  }
  if (d.lighting?.level) {
    room.facts.bright = d.lighting.level === 'bright';
    room.fieldSeen.bright = now;
    room.fieldSeen.lighting = now;
    delete room.missing.lighting;
    delete room.missing.bright;
  }
  if (d.air_quality?.level) {
    room.facts.air_quality = d.air_quality.level;
    room.fieldSeen.air_quality = now;
    delete room.missing.air_quality;
  }
  if (d.mh_sensor) {
    room.facts.mh_sensor = !!d.mh_sensor.triggered;
    room.fieldSeen.mh_sensor = now;
    delete room.missing.mh_sensor;
  }
  if (d.occupancy) {
    const present = !!d.occupancy.present;
    if (present && !room.sessionStart) room.sessionStart = now;
    if (present) room.lastPresent = now;
    if (!present) room.sessionStart = 0;
    room.facts.distance_cm = d.occupancy.distance_cm ?? null;
    room.fieldSeen.occupancy = now;
    delete room.missing.occupancy;
  }
  if (msg.missing) room.missing = { ...room.missing, ...msg.missing };
  room.lastSeen = now;
  updateDerivedFacts();
}
```

`updateDerivedFacts()`:

```js
function updateDerivedFacts() {
  if (room.device !== 'station') return;
  const now = Date.now();
  if (room.fieldSeen.occupancy) {
    room.facts.occupancy = now - room.lastPresent < 30000; // sesi, bukan presence sesaat
    room.facts.session_duration = room.facts.occupancy && room.sessionStart
      ? Math.floor((now - room.sessionStart) / 1000)
      : 0;
  }
  if (room.fan != null) room.facts.fan = room.fan;
  if (room.vent != null) room.facts.vent = room.vent;
  room.facts.fan_mode = room.fanMode;
}
```

`distance_cm` adalah presence sesaat; `occupancy` adalah sesi. Ini sama dengan perilaku desktop yang sudah ada (`AWAY_MS = 30000`).

State station (retained) memperbarui aktuator, bukan sensor:

```js
function mergeStationState(msg) {
  if (typeof msg.fan === 'boolean') room.fan = msg.fan;
  if (Number.isFinite(msg.vent)) room.vent = msg.vent;
  room.lastSeen = Date.now();
  updateDerivedFacts();
}
```

Di `onMessage`:

```js
if (kind === 'state' && room.device === 'station') {
  mergeStationState(JSON.parse(json));
  // ... render yang sudah ada ...
  return;
}
```

### 6.6 Normalisasi node

```js
function mergeNodeResponse(msg) {
  const d = msg.data || {};
  const now = Date.now();
  for (const key of ['temperature', 'humidity', 'lighting', 'co2',
                     'air_quality', 'occupancy', 'session_duration',
                     'lamp', 'lamp_mode']) {
    if (d[key] !== undefined && d[key] !== null) {
      room.facts[key] = d[key];
      room.fieldSeen[key] = now;
      delete room.missing[key];
    }
  }
  if (msg.unknown) {
    for (const key of msg.unknown) room.missing[key] = 'unknown';
  }
  room.lastSeen = now;
}
```

Telemetry node juga harus masuk ke fungsi yang sama:

```js
if (kind === 'telemetry') {
  mergeNodeResponse({ data: JSON.parse(json) });
  // ... render yang sudah ada ...
}
```

---

## 7. Menyusun facts

```js
function snapshotFacts() {
  if (room.device === 'node') {
    return pick(room.facts, [
      'temperature', 'humidity', 'lighting', 'co2',
      'air_quality', 'occupancy', 'session_duration',
      'lamp', 'lamp_mode',
    ]);
  }
  return pick(room.facts, [
    'temperature', 'humidity', 'bright', 'air_quality',
    'occupancy', 'session_duration', 'distance_cm',
    'fan', 'vent', 'fan_mode',
  ]);
}

function pick(object, keys) {
  const out = {};
  for (const key of keys) {
    if (object[key] !== undefined) out[key] = object[key];
  }
  return out;
}
```

Jangan mengirim `missing` di dalam `FACTS`. `MISSING` adalah objek terpisah di prompt.

`online` dan umur data:

```js
const ONLINE_MS = 15000;

function deviceOnline() {
  // LWT "offline" selalu menang.
  if (room.statusOnline === false) return false;
  // Station bisa idle tanpa request; status retained adalah bukti koneksi.
  if (room.device === 'station' && room.statusOnline === true) return true;
  // Node mengirim telemetry tiap 5 s; diam 15 s berarti bermasalah.
  return room.lastSeen > 0 && Date.now() - room.lastSeen <= ONLINE_MS;
}

function ageSeconds() {
  return room.lastSeen ? Math.round((Date.now() - room.lastSeen) / 1000) : 0;
}
```

Kalau `lastSeen === 0`, jangan panggil model. Balas dengan pesan statis “Aku belum mendengar node” dan jangan kirim command.

---

## 8. Alur dua tahap

```js
async function handleUtterance(text) {
  const started = performance.now();
  const isAlert = text.startsWith('[alert]');
  const log = {
    request: text,
    device: room.device,
    planner: null,
    rounds: 0,
    requested: [],
    missing: {},
    planner_ms: 0,
    responder_ms: 0,
  };

  if (!room.device) return staticReply('Aku belum terhubung ke ruangan.');

  let plan = { task: 'general_chat', required_information: [] };
  if (!isAlert && info.ai.aiMode !== 'responder_only') {
    const t0 = performance.now();
    try {
      plan = await planRequest(text, room.device, knownState());
    } catch (err) {
      // Degraded: jangan menebak. Pakai fakta cache dan beri tahu di log.
      console.warn('planner failed', err);
      plan = { task: 'study_environment_assessment', required_information: [] };
    }
    log.planner = plan;
    log.planner_ms = Math.round(performance.now() - t0);
  }

  if (!isAlert && plan.required_information.length) {
    let previous = null;
    for (let round = 0; round < 2; round++) {
      const needed = plan.required_information.filter((name) => !isFresh(name));
      if (!needed.length) break;

      const result = await requestDevice(needed, {
        budget: 2,
        mode: 'cost_aware',
      });
      log.rounds += 1;
      log.requested.push(...needed);
      log.missing = { ...log.missing, ...(result.missing || {}) };
      room.missing = { ...room.missing, ...(result.missing || {}) };

      if (!Object.keys(result.missing || {}).length) break;

      const retry = Object.entries(result.missing)
        .filter(([, reason]) => reason === 'budget')
        .map(([name]) => name);
      if (!retry.length) break;

      previous = {
        task: plan.task,
        data: snapshotFacts(),
        missing: result.missing,
      };
      try {
        plan = await planRequest(text, room.device, knownState(), previous);
      } catch {
        plan = { ...plan, required_information: retry };
      }
    }
  }

  const t1 = performance.now();
  const reply = await respond(text, {
    isAlert,
    online: deviceOnline(),
    ageS: ageSeconds(),
    missing: room.missing,
  });
  log.responder_ms = Math.round(performance.now() - t1);

  if (!isAlert && reply.command && deviceOnline()) {
    executeCommand(reply.command);
  }

  log.reply = reply;
  log.total_ms = Math.round(performance.now() - started);
  console.debug('[ai]', log);
  return reply;
}
```

Urutan yang penting:

1. Alert tidak memanggil planner.
2. Command murni (`required_information: []`) tidak memanggil device.
3. Kalau informasi sudah fresh, tidak ada request.
4. Kalau `missing` karena `budget`, boleh satu ronde planner lagi dengan `previous`.
5. Command hanya dieksekusi kalau online dan bukan alert.

### 8.1 Responder

```js
async function respond(text, { isAlert, online, ageS, missing }) {
  const facts = snapshotFacts();
  if (!Object.keys(facts).length) {
    return staticReply('Aku belum punya data ruangan.');
  }

  const system = responderSystem(room.device, facts, { ageS, online, missing });
  let out;
  try {
    out = await callJson(
      info.ai.llmUrl,
      info.ai.responderModel || 'studysphere-responder',
      [{ role: 'system', content: system }, { role: 'user', content: text }],
      20000,
    );
  } catch (err) {
    console.warn('responder failed', err);
    return staticReply(online
      ? 'Maaf, aku belum bisa menjawab sekarang.'
      : 'Aku belum mendengar node ruangan.');
  }
  if (!out || typeof out !== 'object') {
    return staticReply(online
      ? 'Maaf, aku belum bisa menjawab sekarang.'
      : 'Aku belum mendengar node ruangan.');
  }

  const say = typeof out.say === 'string' ? out.say.trim() : '';
  const mood = MOODS.has(out.mood) ? out.mood : 'normal';
  const command = sanitizeCommand(out.command, {
    device: room.device,
    isAlert,
    online,
  });
  return {
    say: say || 'Hmm?',
    mood,
    command,
  };
}
```

### 8.2 Validasi command

```js
function sanitizeCommand(command, { device, isAlert, online }) {
  if (!command || isAlert || !online || typeof command !== 'object') return null;

  const out = {};
  if (device === 'node') {
    if (['auto', 'on', 'off'].includes(command.lamp)) out.lamp = command.lamp;
    if (typeof command.lcd === 'string' && command.lcd.trim()) {
      out.lcd = command.lcd.trim().slice(0, 32);
    }
  } else {
    if (['auto', 'on', 'off'].includes(command.fan)) out.fan = command.fan;
    if (command.vent === 'open' || command.vent === 'closed') out.vent = command.vent;
    if (typeof command.lcd === 'string' && command.lcd.trim()) {
      out.lcd = command.lcd.trim().slice(0, 32);
    }
  }
  return Object.keys(out).length ? out : null;
}
```

Yang ditolak:

- `lamp` di station, `fan` di node.
- `vent` selain `open`/`closed`.
- `lcd` bukan string atau kosong.
- Command saat offline.
- Command saat `[alert]`.
- Command dengan key lain (`stream_ms`, `reboot`, dan sebagainya).

### 8.3 Eksekusi command

Gunakan fungsi yang sudah ada di desktop, jangan membuat publisher baru:

```js
function executeCommand(command) {
  if (room.device === 'node') {
    if (command.lamp) setLamp(command.lamp).catch(() => {});
    if (command.lcd) sendLcd(command.lcd).catch(() => {});
    return;
  }

  // Station: fan string -> mode lokal; driveFan() yang mengirim bool ke firmware.
  if (command.fan) setFan(command.fan);
  if (command.vent) {
    setVent(command.vent === 'open' ? 180 : 0).catch(() => {});
  }
  if (command.lcd) sendLcd(command.lcd).catch(() => {});
}
```

Kenapa tidak mengirim `{"fan":"on"}` langsung ke station? Karena firmware station menerima `fan` sebagai boolean/angka, bukan string mode. `setFan()` mengatur `fanMode` dan `driveFan()` menerjemahkannya menjadi `{"fan":true|false}`. Ini juga menjaga hysteresis 30/29 °C.

Kenapa `vent` menjadi angka? Karena servo menerima sudut 0–180, bukan `"open"`. `setVent(180)` membuka, `setVent(0)` menutup.

`sendLcd()` yang sudah ada menangani node (`string`) dan station (`[baris1, baris2]` + `lcd_ms`). Jangan mengirim array ke node.

---

## 9. Alert

Alur alert yang sudah ada di `warn()` memanggil model dengan `[alert] ...`. Pertahankan itu, tetapi:

1. Jangan panggil planner.
2. Paksa `command: null`.
3. Tetap kirim facts dan `MISSING`.
4. Jangan mengulang alert lebih dari `REMIND_MS`.

```js
async function warn(factsText) {
  attention();
  const text = `[alert] ${factsText} Say it in Bahasa Indonesia.`;
  const reply = await respond(text, {
    isAlert: true,
    online: deviceOnline(),
    ageS: ageSeconds(),
    missing: room.missing,
  });
  sayReply(reply.say, reply.mood);
  // Tidak ada executeCommand di sini.
}
```

Kalau memakai bahasa terakhir user, ganti `Bahasa Indonesia` dengan bahasa tersebut; dataset mendukung keduanya.

---

## 10. Mode fallback

### 10.1 Responder saja

`STUDYSPHERE_AI_MODE=responder_only`.

- Tetap pakai `responderSystem()` dari §4.2, bukan prompt lama.
- Kirim semua facts yang ada; tidak ada planner dan tidak ada request tambahan.
- Ini bukan cost-aware; hanya untuk demo cepat.
- Kalau model SFT dilatih dengan prompt training, prompt lama yang berbeda bisa menurunkan kualitas.

### 10.2 Satu model untuk dua peran

Pilihan:

1. **Qwen3 1.7B, dua prompt, tanpa adapter planner.** Paling cepat dicoba; planner accuracy lebih rendah daripada Gemma 270M yang dilatih khusus.
2. **Qwen3 1.7B, satu adapter multi-task.** Gabungkan data planner dan responder, latih satu adapter, pakai dua prompt. Hemat memori, tetapi ada interferensi task.
3. **Gemma 3 270M multi-task.** Paling sesuai draft, tetapi kualitas bahasa Indonesia dan command lebih rendah. Siapkan ekspektasi dan evaluasi terpisah.

Menggabungkan data untuk opsi 2:

```powershell
New-Item -ItemType Directory -Force ai/data/combined | Out-Null
Get-Content ai/data/planner/train.jsonl, ai/data/responder/train.jsonl |
  Set-Content ai/data/combined/train.jsonl
Get-Content ai/data/planner/val.jsonl, ai/data/responder/val.jsonl |
  Set-Content ai/data/combined/val.jsonl
```

Linux/macOS:

```sh
mkdir -p ai/data/combined
cat ai/data/planner/train.jsonl ai/data/responder/train.jsonl > ai/data/combined/train.jsonl
cat ai/data/planner/val.jsonl ai/data/responder/val.jsonl > ai/data/combined/val.jsonl
```

Evaluasi tetap terpisah: planner di `ai/data/planner/test.jsonl`, responder di `ai/data/responder/test.jsonl`. Jangan menilai model gabungan hanya dari satu metrik.

### 10.3 Planner gagal

Jangan menebak `required_information`. Kalau planner error:

- pakai fakta cache yang ada,
- jangan request sensor baru,
- panggil responder dengan `MISSING` yang jujur,
- catat `degraded: true` di log.

Kalau ingin fallback yang lebih baik, buat aturan keyword sederhana yang **tidak** menggantikan model, misalnya “jika mengandung ‘panas’ tambahkan temperature”. Tetapi jangan mencampurnya dengan hasil evaluasi model; laporkan sebagai mode terpisah.

### 10.4 Responder gagal

Balas dengan template statis berbasis `mood()` yang sudah ada. Jangan mengirim command. Contoh:

```js
function staticReply(text) {
  return { say: text, mood: 'normal', command: null };
}
```

---

## 11. Tes integrasi

### 11.1 Unit test tanpa MQTT

Uji `sanitizeCommand()`:

| Input | Device | Harapan |
|---|---|---|
| `{lamp:"on"}` | node | lolos |
| `{lamp:"on"}` | station | `null` |
| `{fan:"auto"}` | station | lolos |
| `{fan:"auto"}` | node | `null` |
| `{vent:"open"}` | station | lolos |
| `{vent:"half"}` | station | `null` |
| `{lcd:"123456789012345678901234567890123"}` | node | 32 karakter |
| `{lamp:"on"}` + `isAlert` | node | `null` |
| `{lamp:"on"}` + offline | node | `null` |
| `{reboot:true}` | node | `null` |

Uji prompt:

- `PLANNER_SYSTEM` harus sama dengan string di `generate_planner.py`.
- `responderSystem('node', ...)` harus memuat `"lamp"` dan tidak memuat `"fan"`.
- `responderSystem('station', ...)` harus memuat `"fan"` dan tidak memuat `"lamp"`.
- `FACTS:` harus JSON valid.
- `MISSING:` harus JSON valid.

### 11.2 Tes MQTT manual

Node:

```sh
mosquitto_pub -h localhost -t studysphere/room-01/request \
  -m '{"task":"thermal_comfort_check","required_information":["temperature"]}'
```

Station:

```sh
mosquitto_pub -h localhost -t studysphere/station01/req \
  -m '{"id":"ai-1","task":"ventilation_decision","required_information":["air_quality"],"mode":"cost_aware","budget":2}'
```

Command node:

```sh
mosquitto_pub -h localhost -t studysphere/room-01/command -m '{"lamp":"on"}'
```

Command station:

```sh
mosquitto_pub -h localhost -t studysphere/station01/cmd -m '{"fan":true}'
mosquitto_pub -h localhost -t studysphere/station01/cmd -m '{"vent":180}'
```

### 11.3 Tes end-to-end

1. Nyalakan node/station dan desktop.
2. Pastikan `telemetry`/`state` masuk.
3. Tanya “berapa suhu ruangan?”.
4. Cek log: planner `thermal_comfort_check`, `required_information:["temperature"]`, tidak ada request kalau telemetry masih fresh.
5. Tanya “matikan lampu” di node: planner `lamp_control`, `required_information:[]`, tidak ada request sensor, command `{"lamp":"off"}`.
6. Tanya “nyalakan kipas” di node: responder harus menolak, command `null`.
7. Tanya “buka ventilasi” di station: command `{"vent":"open"}` → `setVent(180)`.
8. Matikan node, tunggu 15 s, tanya suhu: jawaban menyebut data tidak tersedia; tidak ada command.
9. Kirim `[alert]` buatan: tidak ada command.
10. Cek latency dan payload di log.

### 11.4 Kriteria penerimaan awal

Ini target, bukan hasil:

- JSON valid ≥ 99% pada test sintetis.
- Exact-set planner ≥ 80% pada test sintetis; ≥ 60% pada hard set.
- Tidak ada command tidak aman pada 28 hard responder cases.
- Tidak ada angka halusinasi pada kasus `missing`/offline.
- p95 end-to-end < 3 s di laptop target (setelah model dimuat).
- Tidak ada command yang dikirim saat `[alert]`.

Kalau target tidak tercapai, jangan menurunkan target diam-diam. Perbaiki data/prompt, atau laporkan hasil apa adanya.

---

## 12. Checklist integrasi

- [ ] `STUDYSPHERE_LLM_URL` menunjuk Ollama.
- [ ] `STUDYSPHERE_PLANNER_MODEL` dan `STUDYSPHERE_RESPONDER_MODEL` benar.
- [ ] `OLLAMA_ORIGINS=*` di Windows.
- [ ] Prompt inference identik dengan dataset.
- [ ] `available_information` lengkap per device.
- [ ] `co2` tidak pernah dikirim ke station.
- [ ] `fan`/`vent` tidak pernah dikirim ke node.
- [ ] `response` node dan `resp` station ditangani terpisah.
- [ ] Hanya satu request node yang berjalan.
- [ ] `MISSING` diisi alasan, bukan angka default.
- [ ] `[alert]` tidak memanggil planner dan tidak mengirim command.
- [ ] Command divalidasi dengan whitelist.
- [ ] `fan` station dikirim sebagai bool lewat `driveFan()`, bukan string.
- [ ] `vent` station dikirim sebagai sudut 0/180.
- [ ] LCD dipotong 32 karakter; station dipecah 2×16.
- [ ] Node offline → tidak ada command.
- [ ] Log latensi, request, dan response disimpan untuk eksperimen.

---

## 13. Yang belum ada di desktop

Dokumen ini adalah sketsa integrasi, bukan patch yang sudah diuji di sesi ini. Yang masih perlu dikerjakan:

- Menambahkan `plannerModel`, `responderModel`, dan `aiMode` ke `ai_settings()`.
- Mengganti `prompt()`/`think()` dengan `responderSystem()`/`callJson()`.
- Menambahkan `response` ke `onMessage` dan `pending` request.
- Menambahkan normalisasi station/node.
- Menambahkan unit test untuk `sanitizeCommand()` dan prompt.
- Menjalankan ulang `evaluate.py` setelah prompt integration benar-benar sama.

Sampai itu selesai, jangan mengklaim hasil end-to-end. Yang sudah bisa diklaim hanya dataset, training script, dan evaluasi offline.
