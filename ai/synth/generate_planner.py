#!/usr/bin/env python3
"""Generate the StudySphere semantic-planner dataset.

Input : a natural-language request plus a compact room context.
Output: {"task": "...", "required_information": [...]}

The label policy is rule-based and deliberately explicit.  Every template
belongs to one policy profile; a template is never reused across train/val/test
because the split is keyed by template id.  See ai/docs/dataset-card.md.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from common import (
    DATA,
    DEVICE_INFO,
    add_typo,
    assign_template_splits,
    canonical_info,
    compact_json,
    info_cost,
    make_row,
    normalise_text,
    write_jsonl,
)

PLANNER_SYSTEM = """You are the sensing planner of StudySphere, a cost-aware semantic IoT study room.
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
9. Never invent sensor names. Output valid JSON only."""


def context_for(device: str, rng: random.Random, previous=None, device_id: str | None = None) -> dict:
    if device == "node":
        known = {
            "lamp": rng.choice(["on", "off"]),
            "lamp_mode": rng.choice(["auto", "on", "off"]),
        }
        device_id = device_id or rng.choice(NODE_IDS)
    else:
        known = {
            "fan": rng.choice([True, False]),
            "vent": rng.choice([0, 180]),
        }
        device_id = device_id or rng.choice(STATION_IDS)
    return {
        "device_type": device,
        "device_id": device_id,
        "available_information": DEVICE_INFO[device],
        "known_state": known,
        "previous": previous,
    }


def user_message(context: dict, utterance: str) -> str:
    return (
        "CONTEXT: "
        + compact_json(context)
        + "\nREQUEST: "
        + compact_json(utterance)
    )


# Device IDs are deliberately varied so the planner does not memorise one room
# name.  The desktop follows any DEVICE_ID, so room-02/station02 are valid.
NODE_IDS = ["room-01", "room-02", "room-03", "room-04", "room-05", "room-06"]
STATION_IDS = ["station01", "station02", "station03", "station04", "station05", "station06"]

# Label-preserving discourse markers.  They add lexical diversity without
# changing the task, the required information, or the device capability.
PREFIX_ID = [
    "", "", "", "eh, ", "halo, ", "sebentar, ", "aku mau tanya: ", "cek dong, ",
    "menurutmu, ", "kalau boleh tahu, ", "tolong cek: ", "sekarang, ", "saat ini, ",
    "di ruangan ini, ", "maaf, ", "jadi, ", "begini, ", "coba cek: ",
    "halo, mau tanya: ", "quick check: ", "hi, ", "hey, ", "aku penasaran, ",
    "bisakah kamu cek: ", "tolong lihat: ", "sebentar ya, ", "oke, ", "nah, ",
    "terus, ", "mau tanya: ",
]
PREFIX_EN = [
    "", "", "", "hey, ", "hi, ", "one moment, ", "quick question: ", "quick check: ",
    "so, ", "just checking, ", "right now, ", "at the moment, ", "in this room, ",
    "sorry, ", "hello, ", "tell me, ", "please tell me, ", "could you check: ",
    "i was wondering, ", "do you know, ", "okay, ", "well, ", "quick one: ",
    "hey, quick question: ", "hi, quick check: ", "just to confirm, ", "real quick, ",
    "for this room, ", "by the way, ", "let me ask, ",
]

PROFILES = [
    {
        "id": "temp_hot",
        "task": "thermal_comfort_check",
        "required": ["temperature"],
        "devices": ["node", "station"],
        "id_texts": [
            "panas banget ya di sini?",
            "suhu ruangan sekarang berapa?",
            "cek suhu dong, kayaknya panas.",
            "aku kepanasan, ini berapa derajat?",
            "ruangan ini gerah nggak?",
            "berapa suhu sekarang? aku mau buka jendela.",
        ],
        "en": [
            "is it too hot in here?",
            "what is the room temperature?",
            "check the temperature, it feels warm.",
            "i am overheating, how many degrees is it?",
            "is this room stuffy because of the heat?",
        ],
    },
    {
        "id": "temp_cold",
        "task": "thermal_comfort_check",
        "required": ["temperature"],
        "devices": ["node", "station"],
        "id_texts": [
            "dingin banget, suhunya berapa?",
            "cek suhu, aku kedinginan.",
            "apakah ruangan ini terlalu dingin?",
            "berapa derajat sekarang? aku pakai jaket.",
        ],
        "en": [
            "it is cold, what is the temperature?",
            "check the temperature, i feel chilly.",
            "is the room too cold?",
            "how many degrees is it right now?",
        ],
    },
    {
        "id": "thermal_comfort",
        "task": "thermal_comfort_check",
        "required": ["temperature", "humidity"],
        "devices": ["node", "station"],
        "id_texts": [
            "aku gerah dan lengket, suhu sama kelembapannya gimana?",
            "ruangan ini nyaman nggak? aku merasa lembap.",
            "cek comfort ruangan dong, suhu dan kelembapan.",
            "apakah kelembapan di sini bikin gerah?",
            "suhu dan kelembapan sekarang berapa? aku mau cek kenyamanan.",
        ],
        "en": [
            "i feel sticky, what are the temperature and humidity?",
            "is this room comfortable? it feels humid.",
            "check thermal comfort, temperature and humidity.",
            "is the humidity making it feel hotter?",
            "what are the temperature and humidity right now?",
        ],
    },
    {
        "id": "humidity_only",
        "task": "room_status",
        "required": ["humidity"],
        "devices": ["node", "station"],
        "id_texts": [
            "kelembapannya berapa?",
            "cek kelembapan ruangan dong.",
            "apakah udaranya lembap? aku mau jemur buku.",
            "berapa persen kelembapan di sini?",
        ],
        "en": [
            "what is the humidity?",
            "check the room humidity.",
            "is it humid in here? i want to dry some books.",
            "what is the humidity percentage?",
        ],
    },
    {
        "id": "air_stuffy",
        "task": "air_quality_check",
        "required": ["air_quality"],
        "devices": ["node", "station"],
        "id_texts": [
            "udara di sini pengap nggak?",
            "cek kualitas udara dong.",
            "kayaknya ruangan bau, udara aman?",
            "apakah udaranya masih segar?",
            "aku pusing, udaranya buruk ya?",
            "perlu ventilasi nggak? udaranya sesak.",
        ],
        "en": [
            "is the air stuffy in here?",
            "check the air quality.",
            "the room smells, is the air safe?",
            "is the air still fresh?",
            "i have a headache, is the air bad?",
            "does this room need ventilation?",
        ],
    },
    {
        "id": "air_hot",
        "task": "air_quality_check",
        "required": ["air_quality", "temperature"],
        "devices": ["node", "station"],
        "id_texts": [
            "panas dan pengap banget, kenapa ya?",
            "ruangan gerah plus udara sesak.",
            "cek suhu dan kualitas udara, aku nggak nyaman.",
            "apakah panas dan udara buruk ini berbahaya?",
        ],
        "en": [
            "it is hot and stuffy, what is going on?",
            "the room is warm and the air feels heavy.",
            "check temperature and air quality, i am uncomfortable.",
            "are the heat and bad air dangerous?",
        ],
    },
    {
        "id": "co2",
        "task": "air_quality_check",
        "required": ["co2"],
        "required_by_device": {"node": ["co2"], "station": ["air_quality"]},
        "devices": ["node", "station"],
        "id_texts": [
            "co2 di ruangan ini berapa ppm?",
            "cek kadar co2 dong.",
            "berapa ppm co2 sekarang?",
            "apakah co2 sudah terlalu tinggi?",
        ],
        "en": [
            "what is the co2 level in ppm?",
            "check the co2 reading.",
            "how many ppm of co2 is there?",
            "is the co2 level too high?",
        ],
    },
    {
        "id": "light_dark",
        "task": "lighting_check",
        "required": ["lighting"],
        "devices": ["node", "station"],
        "id_texts": [
            "di sini gelap nggak?",
            "cahayanya cukup buat belajar nggak?",
            "cek lux dong.",
            "apakah meja belajarku cukup terang?",
            "berapa lux cahaya di ruangan ini?",
            "lampu perlu dinyalakan nggak?",
        ],
        "en": [
            "is it dark in here?",
            "is there enough light to study?",
            "check the lux level.",
            "is my desk bright enough?",
            "how many lux is the room?",
            "should i turn on the lamp?",
        ],
    },
    {
        "id": "light_auto",
        "task": "lighting_check",
        "required": ["lighting", "occupancy", "lamp_mode"],
        "required_by_device": {
            "node": ["lighting", "occupancy", "lamp_mode"],
            "station": ["lighting", "occupancy"],
        },
        "devices": ["node", "station"],
        "id_texts": [
            "lampu otomatisnya bekerja nggak?",
            "apakah lampu seharusnya menyala sekarang?",
            "cek mode lampu dan kondisi cahaya.",
            "lampu auto-nya nyala nggak kalau aku di sini?",
            "apakah ruangan cukup gelap dan ada orang?",
        ],
        "en": [
            "is the automatic lamp working?",
            "should the lamp be on right now?",
            "check the lamp mode and the light level.",
            "does the auto lamp turn on when i am here?",
            "is the room dark enough and is someone present?",
        ],
    },
    {
        "id": "lamp_status",
        "task": "room_status",
        "required": ["lamp"],
        "devices": ["node"],
        "id_texts": [
            "lampu belajarku nyala nggak?",
            "status lampu sekarang apa?",
            "apakah lampu masih menyala?",
            "cek lampu dong, nyala atau mati?",
        ],
        "en": [
            "is my study lamp on?",
            "what is the lamp status?",
            "is the lamp still on?",
            "check the lamp, on or off?",
        ],
    },
    {
        "id": "occupancy_only",
        "task": "occupancy_check",
        "required": ["occupancy"],
        "devices": ["node", "station"],
        "id_texts": [
            "ada orang di ruangan nggak?",
            "aku sendirian di sini?",
            "cek kehadiran dong.",
            "apakah ada yang sedang belajar?",
            "ruangan ini kosong nggak?",
        ],
        "en": [
            "is anyone in the room?",
            "am i alone here?",
            "check occupancy.",
            "is someone studying here?",
            "is the room empty?",
        ],
    },
    {
        "id": "session",
        "task": "occupancy_check",
        "required": ["occupancy", "session_duration"],
        "devices": ["node", "station"],
        "id_texts": [
            "aku udah belajar berapa lama?",
            "sesi belajarku sudah berapa menit?",
            "sudah berapa lama aku di meja ini?",
            "cek durasi belajar dong.",
        ],
        "en": [
            "how long have i been studying?",
            "how many minutes is my study session?",
            "how long have i been at this desk?",
            "check the study duration.",
        ],
    },
    {
        "id": "break",
        "task": "study_break_advice",
        "required": ["session_duration", "temperature", "air_quality"],
        "devices": ["node", "station"],
        "id_texts": [
            "aku capek, perlu istirahat nggak?",
            "udah lama belajar, mending break?",
            "apakah aku harus istirahat sekarang?",
            "cek dong, aku masih kuat belajar atau perlu jeda?",
            "kasih saran istirahat berdasarkan sesi dan kondisi ruangan.",
        ],
        "en": [
            "i am tired, should i take a break?",
            "i have studied for a while, should i stop?",
            "do i need a break now?",
            "am i okay to keep studying or should i pause?",
            "advise me on a break based on my session and the room.",
        ],
    },
    {
        "id": "break_eyes",
        "task": "study_break_advice",
        "required": ["session_duration", "temperature", "air_quality", "lighting"],
        "devices": ["node", "station"],
        "id_texts": [
            "mataku pegel, perlu istirahat? cahayanya gimana?",
            "belajar lama bikin mata lelah, cek cahaya dan saran break.",
            "apakah cahaya ruangan dan durasi belajarku sudah tidak sehat?",
        ],
        "en": [
            "my eyes hurt, should i rest? how is the light?",
            "long study sessions strain my eyes, check the light and advise a break.",
            "is the lighting and my study duration becoming unhealthy?",
        ],
    },
    {
        "id": "vent",
        "task": "ventilation_decision",
        "required": ["air_quality"],
        "devices": ["node", "station"],
        "id_texts": [
            "perlu buka jendela nggak?",
            "ventilasinya perlu dibuka?",
            "apakah ruangan perlu udara segar?",
            "mending buka ventilasi atau nggak?",
        ],
        "en": [
            "should i open a window?",
            "does the vent need to be open?",
            "does this room need fresh air?",
            "should i ventilate the room?",
        ],
    },
    {
        "id": "vent_hot",
        "task": "ventilation_decision",
        "required": ["air_quality", "temperature"],
        "devices": ["node", "station"],
        "id_texts": [
            "pengap dan panas, sebaiknya buka ventilasi?",
            "udara sesak dan suhu tinggi, perlu buka jendela?",
            "cek udara dan suhu, lalu saran ventilasi.",
        ],
        "en": [
            "it is stuffy and hot, should i open the vent?",
            "the air is heavy and the temperature is high, should i open a window?",
            "check the air and temperature, then advise on ventilation.",
        ],
    },
    {
        "id": "energy_node",
        "task": "energy_saving_check",
        "required": ["occupancy", "lamp", "lamp_mode", "lighting"],
        "devices": ["node"],
        "id_texts": [
            "aku mau pergi, lampunya masih nyala nggak? boros nggak?",
            "cek apakah lampu belajar masih menyala padahal aku keluar.",
            "apakah lampu boros energi sekarang?",
            "lampu nyala terus, apakah ada orang di ruangan?",
        ],
        "en": [
            "i am leaving, is the lamp still on? is it wasting power?",
            "check whether the study lamp is on while i am away.",
            "is the lamp wasting energy right now?",
            "the lamp stays on, is anyone in the room?",
        ],
    },
    {
        "id": "energy_station",
        "task": "energy_saving_check",
        "required": ["occupancy", "fan", "vent", "temperature"],
        "devices": ["station"],
        "id_texts": [
            "aku mau pergi, kipas dan ventilasinya masih jalan?",
            "cek apakah kipas boros padahal ruangan kosong.",
            "apakah fan dan vent perlu dimatikan?",
            "kipas nyala terus, ada orang nggak?",
        ],
        "en": [
            "i am leaving, are the fan and vent still running?",
            "check whether the fan is wasting power while the room is empty.",
            "should the fan and vent be turned off?",
            "the fan stays on, is anyone here?",
        ],
    },
    {
        "id": "assessment",
        "task": "study_environment_assessment",
        "required": ["temperature", "air_quality", "lighting", "occupancy"],
        "devices": ["node", "station"],
        "id_texts": [
            "ruangan ini cocok buat belajar nggak?",
            "gimana kondisi ruangan buat fokus?",
            "apakah lingkungan belajarku sehat sekarang?",
            "cek ruangan dong, nyaman buat belajar atau nggak?",
            "aku mau mulai belajar, kondisi ruangannya aman?",
        ],
        "en": [
            "is this room suitable for studying?",
            "how is the room for focus?",
            "is my study environment healthy right now?",
            "check the room, is it comfortable for studying?",
            "i want to start studying, is the room okay?",
        ],
    },
    {
        "id": "assessment_comfort",
        "task": "study_environment_assessment",
        "required": ["temperature", "humidity", "air_quality", "lighting", "occupancy"],
        "devices": ["node", "station"],
        "id_texts": [
            "ruangan ini nyaman buat belajar? aku pengin tahu suhu, kelembapan, cahaya, udara, dan ada orang atau nggak.",
            "cek semua aspek kenyamanan belajar: suhu, kelembapan, cahaya, udara, kehadiran.",
            "apakah ruangan ini sehat dan nyaman secara keseluruhan?",
        ],
        "en": [
            "is this room comfortable for studying? check temperature, humidity, light, air and occupancy.",
            "check every comfort aspect: temperature, humidity, lighting, air quality and presence.",
            "is the room healthy and comfortable overall?",
        ],
    },
    {
        "id": "lamp_on",
        "task": "lamp_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "nyalakan lampu belajar.",
            "lampu nyala dong.",
            "tolong hidupkan lampu.",
            "lampu belajarnya dinyalakan ya.",
        ],
        "en": [
            "turn on the study lamp.",
            "lamp on please.",
            "switch the lamp on.",
            "please turn the study light on.",
        ],
    },
    {
        "id": "lamp_off",
        "task": "lamp_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "matikan lampunya.",
            "lampu off dong.",
            "tolong matikan lampu belajar.",
            "lampu belajarnya dimatikan ya.",
        ],
        "en": [
            "turn off the lamp.",
            "lamp off please.",
            "switch the study lamp off.",
            "please turn the light off.",
        ],
    },
    {
        "id": "lamp_auto",
        "task": "lamp_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "set lampu ke mode otomatis.",
            "lampu auto aja.",
            "pakai mode auto untuk lampu belajar.",
            "kembalikan lampu ke otomatis.",
        ],
        "en": [
            "set the lamp to automatic mode.",
            "lamp auto please.",
            "use auto mode for the study lamp.",
            "return the lamp to automatic.",
        ],
    },
    {
        "id": "fan_on",
        "task": "fan_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "nyalakan kipas.",
            "kipas on dong.",
            "tolong hidupkan fan.",
            "kipasnya dinyalakan ya.",
        ],
        "en": [
            "turn on the fan.",
            "fan on please.",
            "switch the fan on.",
            "please start the fan.",
        ],
    },
    {
        "id": "fan_off",
        "task": "fan_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "matikan kipas.",
            "kipas off dong.",
            "tolong matikan fan.",
            "kipasnya dimatikan ya.",
        ],
        "en": [
            "turn off the fan.",
            "fan off please.",
            "switch the fan off.",
            "please stop the fan.",
        ],
    },
    {
        "id": "fan_auto",
        "task": "fan_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "kipas auto aja.",
            "set fan ke otomatis.",
            "pakai mode auto untuk kipas.",
            "kembalikan kipas ke otomatis.",
        ],
        "en": [
            "set the fan to auto.",
            "fan auto please.",
            "use automatic mode for the fan.",
            "return the fan to automatic.",
        ],
    },
    {
        "id": "vent_open",
        "task": "vent_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "buka ventilasinya.",
            "buka vent dong.",
            "tolong buka jendela otomatis.",
            "ventilasi dibuka ya.",
        ],
        "en": [
            "open the vent.",
            "open the ventilation please.",
            "please open the automatic window.",
            "open it up.",
        ],
    },
    {
        "id": "vent_close",
        "task": "vent_control",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "tutup ventilasinya.",
            "tutup vent dong.",
            "tolong tutup jendela otomatis.",
            "ventilasi ditutup ya.",
        ],
        "en": [
            "close the vent.",
            "close the ventilation please.",
            "please close the automatic window.",
            "shut it.",
        ],
    },
    {
        "id": "lcd",
        "task": "lcd_message",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "tampilkan pesan istirahat 5 menit di lcd.",
            "tulis fokus 25 menit di layar lcd.",
            "kirim pesan minum air ke lcd.",
            "lcd-nya tampilkan jadwal belajar.",
        ],
        "en": [
            "show the message rest 5 minutes on the lcd.",
            "write focus 25 minutes on the lcd.",
            "send a drink water message to the lcd.",
            "display the study schedule on the lcd.",
        ],
    },
    {
        "id": "greeting",
        "task": "general_chat",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "halo, apa kabar?",
            "selamat pagi.",
            "hai burung hantu.",
            "kamu di sana?",
        ],
        "en": [
            "hello, how are you?",
            "good morning.",
            "hi owl.",
            "are you there?",
        ],
    },
    {
        "id": "thanks",
        "task": "general_chat",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "makasih ya.",
            "terima kasih bantuannya.",
            "oke, thanks.",
            "good job.",
        ],
        "en": [
            "thanks.",
            "thank you for the help.",
            "okay, thanks.",
            "good job.",
        ],
    },
    {
        "id": "status_short",
        "task": "room_status",
        "required": ["temperature", "air_quality", "occupancy"],
        "devices": ["node", "station"],
        "id_texts": [
            "gimana kondisi ruangan sekarang?",
            "kasih ringkasan ruangan dong.",
            "ruangan aman nggak?",
            "cek keadaan ruangan sebentar.",
        ],
        "en": [
            "how is the room right now?",
            "give me a room summary.",
            "is the room okay?",
            "check the room state.",
        ],
    },
    {
        "id": "status_all",
        "task": "room_status",
        "required": ["temperature", "humidity", "lighting", "air_quality", "occupancy"],
        "required_by_device": {
            "node": ["temperature", "humidity", "lighting", "co2", "air_quality", "occupancy"],
            "station": ["temperature", "humidity", "lighting", "air_quality", "occupancy"],
        },
        "devices": ["node", "station"],
        "id_texts": [
            "kasih semua data sensor dong.",
            "tampilkan semua bacaan ruangan.",
            "aku mau lihat suhu, kelembapan, cahaya, udara, dan kehadiran.",
            "baca semua sensor sekarang.",
        ],
        "en": [
            "give me all sensor data.",
            "show every room reading.",
            "i want temperature, humidity, light, air and occupancy.",
            "read all sensors now.",
        ],
    },
    {
        "id": "status_temp_humidity",
        "task": "room_status",
        "required": ["temperature", "humidity"],
        "devices": ["node", "station"],
        "id_texts": [
            "suhu dan kelembapan sekarang?",
            "berapa suhu dan rh ruangan?",
            "cek temperature dan humidity.",
            "kasih angka suhu sama kelembapan.",
        ],
        "en": [
            "temperature and humidity right now?",
            "what are the temperature and rh?",
            "check temperature and humidity.",
            "give me the temperature and humidity numbers.",
        ],
    },
    {
        "id": "unsupported_noise",
        "task": "room_status",
        "required": [],
        "devices": ["node", "station"],
        "id_texts": [
            "noise di sini berapa desibel?",
            "cek tingkat kebisingan ruangan.",
            "apakah ruangan ini terlalu berisik?",
        ],
        "en": [
            "what is the noise level in decibels?",
            "check the room noise.",
            "is this room too noisy?",
        ],
    },
]

# Progressive retrieval: context.previous already holds some data; the user
# asks for one more piece.  The model must return only the new information.
PROGRESSIVE = [
    {
        "id": "prev_assessment_light",
        "task": "study_environment_assessment",
        "required": ["lighting"],
        "previous": {
            "task": "study_environment_assessment",
            "data": {"temperature": 31, "air_quality": "stale"},
            "missing": {"lighting": "budget"},
        },
        "id_texts": [
            "cahayanya gimana?",
            "kalau lighting-nya?",
            "terus cahayanya cukup nggak?",
            "yang tadi belum kebaca, cahayanya.",
        ],
        "en_texts": [
            "what about the light?",
            "and the lighting?",
            "is the light enough?",
            "you missed the light reading.",
        ],
    },
    {
        "id": "prev_assessment_air",
        "task": "study_environment_assessment",
        "required": ["air_quality"],
        "previous": {
            "task": "study_environment_assessment",
            "data": {"temperature": 24, "lighting": 420, "occupancy": True},
            "missing": {"air_quality": "sensor_error"},
        },
        "id_texts": [
            "udaranya gimana?",
            "kalau kualitas udara?",
            "yang belum kebaca udaranya, cek dong.",
        ],
        "en_texts": [
            "what about the air?",
            "and the air quality?",
            "you missed the air reading, check it.",
        ],
    },
    {
        "id": "prev_thermal_humidity",
        "task": "thermal_comfort_check",
        "required": ["humidity"],
        "previous": {
            "task": "thermal_comfort_check",
            "data": {"temperature": 31},
            "missing": {"humidity": "budget"},
        },
        "id_texts": [
            "kalau kelembapannya?",
            "terus humidity-nya berapa?",
            "aku masih penasaran sama kelembapan.",
        ],
        "en_texts": [
            "what about the humidity?",
            "and the humidity?",
            "i still want the humidity reading.",
        ],
    },
    {
        "id": "prev_break_air",
        "task": "study_break_advice",
        "required": ["air_quality"],
        "previous": {
            "task": "study_break_advice",
            "data": {"session_duration": 4200, "temperature": 31},
            "missing": {"air_quality": "budget"},
        },
        "id_texts": [
            "udaranya masih bagus nggak?",
            "kalau kualitas udara?",
            "cek udara juga, buat saran istirahat.",
        ],
        "en_texts": [
            "is the air still okay?",
            "what about the air quality?",
            "check the air too, for the break advice.",
        ],
    },
    {
        "id": "prev_light_occupancy",
        "task": "lighting_check",
        "required": ["occupancy"],
        "previous": {
            "task": "lighting_check",
            "data": {"lighting": 80, "lamp_mode": "auto"},
            "missing": {"occupancy": "sensor_error"},
        },
        "id_texts": [
            "ada orang nggak?",
            "kalau kehadirannya?",
            "cek juga ada orang atau nggak.",
        ],
        "en_texts": [
            "is anyone there?",
            "what about occupancy?",
            "check whether someone is present too.",
        ],
    },
    {
        "id": "prev_vent_temp",
        "task": "ventilation_decision",
        "required": ["temperature"],
        "previous": {
            "task": "ventilation_decision",
            "data": {"air_quality": "stale"},
            "missing": {"temperature": "budget"},
        },
        "id_texts": [
            "kalau suhunya?",
            "terus temperature-nya berapa?",
            "panas nggak? aku butuh suhunya.",
        ],
        "en_texts": [
            "what about the temperature?",
            "and the temperature?",
            "is it hot? i need the temperature.",
        ],
    },
]


def build_rows(rng: random.Random, variants: int):
    rows = []
    used = set()

    groups = {}
    for profile in PROFILES:
        for lang, texts in (("id", profile["id_texts"]), ("en", profile["en"])):
            for template_index in range(len(texts)):
                template_id = f"{profile['id']}-{lang}-{template_index}"
                groups.setdefault(profile["task"], []).append(template_id)
    for profile in PROGRESSIVE:
        for lang, texts in (("id", profile["id_texts"]), ("en", profile["en_texts"])):
            for template_index in range(len(texts)):
                template_id = f"{profile['id']}-{lang}-{template_index}"
                groups.setdefault(profile["task"], []).append(template_id)
    split_map = assign_template_splits(groups)

    for profile in PROFILES:
        required_by_device = profile.get("required_by_device", {})
        for lang, texts in (("id", profile["id_texts"]), ("en", profile["en"])):
            for template_index, template in enumerate(texts):
                template_id = f"{profile['id']}-{lang}-{template_index}"
                split = split_map[template_id]
                for variant in range(variants):
                    device = rng.choice(profile["devices"])
                    required = required_by_device.get(device, profile["required"])
                    context = context_for(device, rng)
                    prefix = rng.choice(PREFIX_ID if lang == "id" else PREFIX_EN)
                    body = template
                    if prefix:
                        body = prefix + body
                    body = body[0].upper() + body[1:] if body else body
                    utterance = body
                    if rng.random() < 0.045:
                        utterance = add_typo(utterance, rng)
                    key = (split, device, normalise_text(utterance), compact_json(context))
                    if key in used:
                        continue
                    used.add(key)
                    output = {
                        "task": profile["task"],
                        "required_information": canonical_info(required),
                    }
                    row = make_row(
                        f"planner-{template_id}-{variant:02d}-{device}",
                        "planner",
                        [
                            {"role": "system", "content": PLANNER_SYSTEM},
                            {"role": "user", "content": user_message(context, utterance)},
                            {"role": "assistant", "content": compact_json(output)},
                        ],
                        {
                            "profile": profile["id"],
                            "task": profile["task"],
                            "template": template_id,
                            "language": lang,
                            "device": device,
                            "split": split,
                            "gold_cost": info_cost(required),
                        },
                    )
                    rows.append(row)

    # Progressive rows.  The task is inherited from previous.task.
    for profile in PROGRESSIVE:
        for lang, texts in (("id", profile["id_texts"]), ("en", profile["en_texts"])):
            for template_index, template in enumerate(texts):
                template_id = f"{profile['id']}-{lang}-{template_index}"
                split = split_map[template_id]
                for variant in range(max(3, variants // 3)):
                    device = rng.choice(["node", "station"])
                    context = context_for(device, rng, previous=profile["previous"])
                    prefix = rng.choice(PREFIX_ID if lang == "id" else PREFIX_EN)
                    body = template
                    if prefix:
                        body = prefix + body
                    body = body[0].upper() + body[1:] if body else body
                    utterance = body
                    if rng.random() < 0.04:
                        utterance = add_typo(utterance, rng)
                    key = (split, device, normalise_text(utterance), compact_json(context))
                    if key in used:
                        continue
                    used.add(key)
                    output = {
                        "task": profile["task"],
                        "required_information": canonical_info(profile["required"]),
                    }
                    row = make_row(
                        f"planner-{template_id}-{variant:02d}-{device}",
                        "planner",
                        [
                            {"role": "system", "content": PLANNER_SYSTEM},
                            {"role": "user", "content": user_message(context, utterance)},
                            {"role": "assistant", "content": compact_json(output)},
                        ],
                        {
                            "profile": profile["id"],
                            "task": profile["task"],
                            "template": template_id,
                            "language": lang,
                            "device": device,
                            "split": split,
                            "progressive": True,
                            "gold_cost": info_cost(profile["required"]),
                        },
                    )
                    rows.append(row)

    return rows


def balance(rows, target_train: int, target_val: int, target_test: int, rng: random.Random):
    by_split = {"train": [], "val": [], "test": []}
    for row in rows:
        by_split[row["meta"]["split"]].append(row)

    target = {"train": target_train, "val": target_val, "test": target_test}
    out = {}
    for split, items in by_split.items():
        rng.shuffle(items)
        # Keep every profile represented: round-robin by profile, then fill.
        selected = []
        seen_tasks = set()
        remainder = []
        for item in items:
            task = item["meta"]["task"]
            if task not in seen_tasks:
                selected.append(item)
                seen_tasks.add(task)
            else:
                remainder.append(item)
        rng.shuffle(remainder)
        selected.extend(remainder)
        out[split] = selected[: target[split]]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", type=int, default=600, help="variants per template")
    parser.add_argument("--train", type=int, default=100000)
    parser.add_argument("--val", type=int, default=10000)
    parser.add_argument("--test", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--out", type=Path, default=DATA / "planner")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows = build_rows(rng, args.variants)
    splits = balance(rows, args.train, args.val, args.test, rng)

    total = 0
    for split, items in splits.items():
        count = write_jsonl(args.out / f"{split}.jsonl", items)
        total += count
        print(f"planner/{split}.jsonl: {count} rows")
    print(f"planner total: {total} rows")


if __name__ == "__main__":
    main()



