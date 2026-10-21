# StudySphere AI dataset card

Version 2.0 · generated 2026-10-21 · language: Indonesian and English · license: follow the base model and the StudySphere repository

## 1. What this dataset is for

The dataset trains two small, local models that split the AI work instead of asking one model to do everything:

| Role | Input | Output | Why a separate role |
|---|---|---|---|
| **Planner** | user request + room context | `{"task": "...", "required_information": [...]}` | A tiny model can learn this structured decision very well. It is the "cost-aware semantic sensing" part of the thesis. |
| **Responder** | room facts + user request or `[alert]` | `{"say": "...", "mood": "...", "command": {...} or null}` | This needs natural language, grounding and command safety. A 1–2B model is a better fit than 270M. |

The planner is deliberately **not** a chatbot. The responder is deliberately **not** allowed to invent sensor readings. This separation is the main design decision in the dataset.

## 2. Source requirements

The schema is derived from the actual project, not invented:

- `firmware/firmware.ino` and `firmware/config.h`: telemetry names, lamp modes, LCD, semantic request/response.
- `alatnya cu/studysphere_esp32.ino`: station information registry, source costs, cache ages, `missing`, fan, vent, progressive retrieval.
- `desktop/ui/index.html`: the owl prompt, moods, command values, alert behaviour, 32-character LCD limit.
- `README.md`: thresholds (`150 lux`, `1000 ppm`, `60 s` idle, `32 °C` hot).
- `draft.pdf`: Gemma 3 270M + LoRA, task understanding, local reasoning, cost-aware semantic sensing, baseline comparison.

## 3. Files

```text
ai/data/
  planner/
    train.jsonl  100000 rows
    val.jsonl     10000 rows
    test.jsonl    10000 rows
  responder/
    train.jsonl  100000 rows
    val.jsonl     10000 rows
    test.jsonl    10000 rows
  eval/
    planner_hard.jsonl     42 hand-written cases
    responder_hard.jsonl   28 hand-written cases
```

Each row is one JSON object:

```json
{
  "id": "planner-temp_hot-id-0-03-node",
  "task_type": "planner",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "{\"task\":\"thermal_comfort_check\",\"required_information\":[\"temperature\"]}"}
  ],
  "meta": {
    "profile": "temp_hot",
    "task": "thermal_comfort_check",
    "template": "temp_hot-id-0",
    "language": "id",
    "device": "node",
    "split": "train",
    "gold_cost": 2
  }
}
```

`messages` is the only field the trainer reads. `meta` is for analysis, balancing and error inspection.

## 4. Planner: label policy

The planner must return the **minimum set of information whose value can change the answer or the action**. "More data" is not better.

### Information registry

| Name | Unit | Source | Source cost | Available on | Notes |
|---|---|---|---|---|---|
| `occupancy` | boolean | HC-SR04 / PIR | 1 | node, station | Cheapest source. |
| `session_duration` | seconds | derived | 1 | node, station | 0 when nobody is present. |
| `temperature` | °C | DHT11 / DHT22 | 2 | node, station | |
| `humidity` | % RH | DHT11 / DHT22 | 2 | node, station | Same read as temperature. |
| `lighting` | lux (node) / bright-dark (station) | LDR | 3 | node, station | Station does not report lux. |
| `air_quality` | level | MQ-135 | 3 | node, station | MQ-135 is not a true CO₂ sensor. |
| `co2` | ppm (estimated) | MQ-135 | 3 | node only | Never request on the station. |
| `lamp` | on/off | state | 0 | node | Already in telemetry. |
| `lamp_mode` | auto/on/off | state | 0 | node | |
| `fan` | on/off | state | 0 | station | |
| `vent` | 0–180° | state | 0 | station | |

### Task policy

| Task | Required information | Minimality rule |
|---|---|---|
| `thermal_comfort_check` | `temperature` | Add `humidity` only for stickiness, sweat, mold, dryness or explicit comfort. |
| `air_quality_check` | `air_quality` | Add `temperature` only when heat is part of the complaint. Use `co2` only on the node and only for a ppm question. |
| `lighting_check` | `lighting` | Add `occupancy` and `lamp_mode` when deciding whether the lamp should be on. |
| `occupancy_check` | `occupancy` | Add `session_duration` only for "how long". |
| `study_break_advice` | `session_duration`, `temperature`, `air_quality` | Add `lighting` for eye strain. |
| `ventilation_decision` | `air_quality` | Add `temperature` when heat is mentioned. |
| `energy_saving_check` | node: `occupancy`, `lamp`, `lamp_mode`, `lighting`; station: `occupancy`, `fan`, `vent`, `temperature` | Only these keys can reveal wasted energy. |
| `study_environment_assessment` | `temperature`, `air_quality`, `lighting`, `occupancy` | Add `humidity` for an explicit full-comfort request. |
| `room_status` | whatever the user explicitly asks for | "All sensors" means all available readings; a specific question means one or a few. |
| `lamp_control`, `fan_control`, `vent_control`, `lcd_message` | `[]` | A command needs no sensing. The device confirms with telemetry/state. |
| `general_chat` | `[]` | Greeting or thanks needs no reading. |

### Progressive retrieval

When `context.previous` exists, the planner returns only the new or missing information and keeps `previous.task`. It must not repeat keys already in `previous.data`. This is the supervised form of the progressive retrieval described in `draft.pdf`.

### Device awareness

- `co2` is only in `available_information` on the node.
- `lamp` and `lamp_mode` are node-only.
- `fan` and `vent` are station-only.
- If the user asks for `co2` on the station, the correct answer is `air_quality`, not an invented ppm.

## 5. Responder: label policy

The responder has the same contract as the current desktop owl:

```json
{"say":"...", "mood":"...", "command":{...} or null}
```

- `say`: at most two short sentences, in the student's language, no emoji, lists or markdown.
- `mood`: one of `happy`, `normal`, `sleepy`, `dizzy`, `wide`, `love`, `wink`, `thinking`.
- `command`: only what the connected device supports:
  - node: `lamp` = `auto|on|off`, `lcd` = up to 32 characters.
  - station: `fan` = `auto|on|off`, `vent` = `open|closed`, `lcd` = up to 32 characters.
- A command is sent only when the student asks for a change.
- `[alert]` turns never send a command.
- If a sensor is missing or the node is offline, say so; never invent a value.
- If a capability does not exist on the device (lamp on station, fan on node, lux on station, ppm CO₂ on station), say so and return `null`.

The teacher is rule-based, so the same facts always lead to a grounded answer. The phrasing varies through multiple templates and sentence forms.

## 6. How the data was generated

`ai/synth/generate_planner.py` and `ai/synth/generate_responder.py` are deterministic, dependency-free Python scripts.

1. A **policy profile** (planner) or **intent** (responder) defines the allowed devices, required information, commands and phrasing templates.
2. Each template gets a fixed split. The first three templates of every task/intent are forced into train/val/test; the rest use a deterministic 80/10/10 hash. This guarantees that every task appears in all three splits without putting paraphrases of the same template in two splits.
3. Variants change the device, room ID, room state, prefix, and occasionally add a small typo (under 5%).
4. Exact duplicates are removed by `(split, device, normalised utterance, context/facts, answer)`.
5. `ai/synth/validate.py` checks JSON shape, task names, information availability, command values, sentence length, emoji/markdown, offline rules and train/test overlap.

The split is **template-disjoint**, not row-random. A random row split would put near-identical paraphrases in train and test and inflate the score.

The default generators emit 100,000/10,000/10,000 rows per role. To regenerate a smaller or larger version, pass `--train`, `--val`, `--test`, and `--variants`; the actual count is capped by the number of unique template × device × state × prefix combinations. Always rerun `validate.py` after changing the scale.

## 7. Statistics

Planner:

- 120,000 rows: 100,000/10,000/10,000.
- 14 tasks, 41 policy profiles, 2 languages, 2 device types.
- 100,000 train rows and 10,000 test rows contain all 14 tasks.
- Sensor cost is computed over distinct sources, not over information items: temperature + humidity cost 2, not 4; lighting + air quality cost 3, not 6.
- The rows are not 120,000 unique sentence templates. The latest validation found 8,423 unique user texts in train, 1,067 in val, and 1,590 in test; the rest of the rows vary the room context and device state. This is intentional: the planner must learn to condition on context, not memorise sentences.

Responder:

- 120,000 rows: 100,000/10,000/10,000.
- 27 intents, including 2 alert intents, 2 offline/missing cases, 4 capability-error intents, and 2 progressive/command mixes.
- 2 languages, 2 device types.
- All 27 intents appear in train, val and test.
- The latest validation found 1,782 unique user texts in train, 273 in val, and 336 in test. Most rows repeat an utterance with different facts, which teaches grounding but limits lexical diversity. Treat the unique-text count as the honest measure of language coverage.

Hard evaluation:

- 42 planner cases and 28 responder cases written by hand.
- Covers device capability errors, progressive retrieval, missing sensors, offline, stale air, heat, long sessions, and command safety.

## 8. Known limitations

1. **Synthetic language is still synthetic.** The templates are broad, but real students will phrase requests differently. Collect 100–300 real utterances, label them with the policy table, and keep them as a frozen test set.
2. **The teacher encodes one policy.** If you disagree with a label (for example, whether "is it hot?" needs humidity), change the policy first, then regenerate. Do not patch individual rows.
3. **MQ-135 is not a CO₂ sensor.** The dataset says "estimated ppm" for the node. The station only exposes a level. Do not present it as a calibrated CO₂ measurement in the thesis.
4. **Command safety is simplified.** The dataset teaches "comply and warn" when a student asks to turn off the fan in a hot room. A production system may prefer to refuse. Change the handler and regenerate if so.
5. **No audio data.** Speech-to-text and text-to-speech remain separate. The dataset starts from text, which is the right boundary for this model.
6. **No personal data.** Planner room IDs vary over `room-01`–`room-06` and `station01`–`station06`; there are no real names, locations or recordings. The real deployment only uses `room-01`/`station01`, so the extra IDs are augmentation, not a claim that six rooms exist.

## 9. How to extend safely

- Add a new task: add a policy row, an information whitelist, templates, and a check in `validate.py`.
- Add a new sensor: add it to `spec/taxonomy.json`, give it a source cost and device list, then add it to the relevant tasks.
- To improve **lexical** diversity, add new paraphrase templates or label-preserving prefixes. Increasing `--variants` alone mostly repeats existing sentences with new contexts and will not raise the unique-text count much.
- Add a new command: add it to `taxonomy.commands`, add a handler, add capability checks, and add a hard case.
- Add a new language: add `id_texts`/`en`-style lists. Keep the system prompt in English; the model learns the output language from the user message.
- After any change, run:

```sh
python ai/synth/generate_planner.py
python ai/synth/generate_responder.py
python ai/synth/generate_hard.py
python ai/synth/validate.py
```

## 10. Citation / attribution

If you use this dataset in a report, describe it as:

> A synthetic, policy-grounded dataset for cost-aware semantic sensing in the StudySphere AIoT study room. Planner labels follow a minimum-sufficient-information policy over the firmware's information registry; responder labels follow the desktop owl's JSON contract and device command capabilities.
