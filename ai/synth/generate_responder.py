#!/usr/bin/env python3
"""Generate the StudySphere responder (owl) dataset.

Input : room facts + a user request (or a system alert).
Output: {"say": "...", "mood": "...", "command": {...} or null}

The handlers are a rule-based teacher.  They encode the same contract as
desktop/ui/index.html: two short sentences, a mood from the fixed set, and a
command limited to the keys/values the connected device supports.
"""

from __future__ import annotations

import argparse
import random
import re
from pathlib import Path

from common import (
    DATA,
    MOODS,
    add_typo,
    assign_template_splits,
    compact_json,
    make_row,
    normalise_text,
    write_jsonl,
)

MOOD_LIST = "happy, normal, sleepy, dizzy, wide, love, wink, thinking"
LCD_MAX = 32

ID_PREFIX = ["", "", "", "eh, ", "halo, ", "sebentar, ", "aku mau tanya: ", "coba cek, "]
EN_PREFIX = ["", "", "", "hey, ", "hi, ", "one moment, ", "quick question: ", "quick check: "]

STATES = [
    "comfortable",
    "comfortable",
    "hot",
    "hot",
    "cold",
    "dark",
    "bright",
    "stale",
    "stale",
    "empty",
    "long_session",
    "hot_stale",
    "moderate",
    "missing",
    "offline",
    "empty_lamp",
]


# ---------------------------------------------------------------------------
# Facts


def sample_state(device: str, rng: random.Random, kind: str | None = None):
    kind = kind or rng.choice(STATES)
    online = kind != "offline"
    age_s = 35 if not online else rng.choice([0, 0, 1, 2, 3, 4, 5, 7, 9])
    missing: dict[str, str] = {}

    if kind in ("hot", "hot_stale"):
        temperature = rng.choice([30, 31, 32, 33, 34, 35])
        humidity = rng.choice([55, 60, 65, 70, 75])
    elif kind == "cold":
        temperature = rng.choice([16, 17, 18, 19, 20])
        humidity = rng.choice([35, 40, 45, 50])
    elif kind in ("stale", "moderate"):
        temperature = rng.choice([24, 25, 26, 27, 28])
        humidity = rng.choice([50, 55, 60, 65, 70])
    else:
        temperature = rng.choice([22, 23, 24, 25, 26, 27])
        humidity = rng.choice([40, 45, 50, 55, 60])

    occupied = kind not in ("empty", "empty_lamp", "offline")
    if kind == "long_session":
        occupied = True
    session_duration = 0
    if occupied:
        if kind == "long_session":
            session_duration = rng.choice([3600, 4200, 4800, 5400, 6300, 7200, 8400])
        else:
            session_duration = rng.choice([0, 30, 120, 300, 600, 900, 1800, 2700, 3300])

    if device == "node":
        if kind == "dark":
            lighting = rng.choice([20, 40, 60, 80, 100, 120, 140])
        elif kind == "bright":
            lighting = rng.choice([700, 800, 900, 1100, 1300])
        elif kind in ("stale", "moderate", "hot_stale"):
            lighting = rng.choice([180, 250, 350, 450, 550])
        else:
            lighting = rng.choice([300, 380, 450, 520, 600, 680])

        if kind in ("stale", "hot_stale"):
            co2 = rng.choice([1100, 1250, 1400, 1600, 1800, 2100])
        elif kind == "moderate":
            co2 = rng.choice([850, 900, 950, 990])
        else:
            co2 = rng.choice([400, 450, 500, 550, 600, 650, 750])

        lamp = rng.choice(["on", "off"])
        if kind == "empty_lamp":
            lamp = "on"
        if kind == "long_session":
            lamp = "on"
        lamp_mode = rng.choice(["auto", "on", "off"])
        if kind == "empty_lamp":
            lamp_mode = rng.choice(["auto", "on"])
        facts = {
            "temperature": temperature,
            "humidity": humidity,
            "lighting": lighting,
            "co2": co2,
            "air_quality": "stale" if co2 >= 1000 else "good",
            "occupancy": occupied,
            "session_duration": session_duration,
            "lamp": lamp,
            "lamp_mode": lamp_mode,
        }
    else:
        bright = kind not in ("dark",)
        if kind == "bright":
            bright = True
        if kind == "dark":
            bright = False
        if kind in ("stale", "moderate", "hot_stale", "empty", "empty_lamp"):
            bright = rng.choice([True, False])

        if kind in ("stale", "hot_stale"):
            air_quality = rng.choice(["poor", "poor", "moderate"])
        elif kind == "moderate":
            air_quality = "moderate"
        elif kind == "missing":
            air_quality = rng.choice(["good", "moderate"])
        else:
            air_quality = "good"

        fan = rng.choice([True, False])
        vent = rng.choice([0, 180])
        if kind in ("hot", "hot_stale"):
            fan = rng.choice([True, True, False])
            vent = rng.choice([180, 180, 0])
        fan_mode = rng.choice(["auto", "on", "off"])
        facts = {
            "temperature": temperature,
            "humidity": humidity,
            "bright": bright,
            "air_quality": air_quality,
            "occupancy": occupied,
            "session_duration": session_duration,
            "distance_cm": rng.choice([35, 42, 50, 58]) if occupied else None,
            "fan": fan,
            "vent": vent,
            "fan_mode": fan_mode,
        }

    # A missing sensor is rare.  Keep the key absent and record why.
    if kind == "missing" or (online and rng.random() < 0.04):
        candidates = ["temperature", "humidity", "occupancy"]
        if device == "node":
            candidates += ["lighting", "co2"]
        else:
            candidates += ["lighting", "air_quality"]
        key = rng.choice(candidates)
        if key in facts:
            facts.pop(key, None)
        missing[key] = rng.choice(["sensor_error", "timeout", "budget"])
        if key == "co2":
            facts.pop("air_quality", None)
            missing["air_quality"] = "sensor_error"
        if key == "lighting" and device == "station":
            facts.pop("bright", None)
            missing["bright"] = missing.pop("lighting")

    meta = {"online": online, "age_s": age_s}
    return facts, meta, missing


# ---------------------------------------------------------------------------
# Phrasing helpers


def pick(rng, id_options, en_options, lang):
    if lang == "en":
        return rng.choice(en_options)
    return rng.choice(id_options)


def fmt_temp(value):
    return int(round(value))


def fmt_hum(value):
    return int(round(value))


def fmt_lux(value):
    return int(round(value))


def fmt_duration(seconds, lang):
    seconds = int(seconds)
    if seconds < 60:
        return "baru beberapa detik" if lang == "id" else "only a few seconds"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} menit" if lang == "id" else f"{minutes} minutes"
    hours, rem = divmod(minutes, 60)
    if lang == "id":
        return f"{hours} jam" if rem == 0 else f"{hours} jam {rem} menit"
    return f"{hours} hour" if rem == 0 else f"{hours} hours {rem} minutes"


def air_level(facts, device):
    if device == "node":
        return "stale" if facts.get("air_quality") == "stale" else "good"
    return facts.get("air_quality")


def is_dark(facts, device):
    if device == "node":
        value = facts.get("lighting")
        return value is not None and value < 150
    return facts.get("bright") is False


def is_hot(facts):
    value = facts.get("temperature")
    return value is not None and value >= 32


def is_warm(facts):
    value = facts.get("temperature")
    return value is not None and value >= 30


def is_empty(facts):
    return facts.get("occupancy") is False


def has(facts, key):
    return key in facts and facts[key] is not None


def mood_for(facts, device, missing, meta):
    if not meta["online"]:
        return "wide"
    if missing:
        return "thinking"
    aq = air_level(facts, device)
    if aq in ("poor", "stale"):
        return "dizzy"
    if is_hot(facts):
        return "wide"
    if is_warm(facts):
        return "dizzy"
    if is_empty(facts):
        return "sleepy"
    if has(facts, "session_duration") and facts["session_duration"] >= 5400:
        return "sleepy"
    if is_dark(facts, device) and facts.get("occupancy"):
        return "normal"
    return "happy"


def mood_temp(t):
    if t >= 33:
        return "wide"
    if t >= 30:
        return "dizzy"
    if t <= 18:
        return "normal"
    return "happy"


def mood_humidity(h):
    if h >= 70 or h <= 35:
        return "normal"
    return "happy"


def mood_air(aq):
    if aq in ("poor", "stale"):
        return "dizzy"
    if aq in ("moderate", "warmup"):
        return "normal"
    return "happy"


def mood_light(dark, present):
    if dark:
        return "normal" if present else "sleepy"
    return "happy"


def mood_occupancy(present):
    return "happy" if present else "sleepy"


def mood_session(seconds):
    if seconds >= 5400:
        return "sleepy"
    if seconds >= 2700:
        return "normal"
    return "happy"


def mood_break(seconds, aq, hot):
    if seconds >= 2700:
        return "sleepy"
    if aq in ("poor", "stale"):
        return "dizzy"
    if hot:
        return "dizzy"
    return "happy"


def join(sentences):
    return " ".join(s.strip() for s in sentences if s and s.strip())


def missing_say(key, lang):
    names = {
        "temperature": ("suhu", "temperature"),
        "humidity": ("kelembapan", "humidity"),
        "lighting": ("cahaya", "light"),
        "bright": ("cahaya", "light"),
        "air_quality": ("kualitas udara", "air quality"),
        "co2": ("CO2", "CO2"),
        "occupancy": ("kehadiran", "occupancy"),
        "session_duration": ("durasi belajar", "study duration"),
        "fan": ("status kipas", "fan status"),
        "vent": ("status ventilasi", "vent status"),
        "lamp": ("status lampu", "lamp status"),
    }
    id_name, en_name = names.get(key, (key, key))
    if lang == "id":
        return f"Sensor {id_name} belum mengirim bacaan.", "Coba periksa koneksinya sebentar."
    return f"The {en_name} sensor has not reported yet.", "Please check its connection."


# ---------------------------------------------------------------------------
# Handlers.  Every handler returns (say, mood, command).


def handle_offline(facts, device, lang, rng, missing, meta, slot=None):
    device_id = "stasiun" if device == "station" else "node ruangan"
    device_en = "station" if device == "station" else "room node"
    if lang == "id":
        say = join([
            f"Aku belum dengar kabar dari {device_id}, jadi data terakhirnya sudah tidak akurat.",
            "Periksa koneksi atau nyalakan ulang perangkatnya.",
        ])
    else:
        say = join([
            f"I have not heard from the {device_en}, so the last readings are out of date.",
            "Please check the connection or power-cycle the device.",
        ])
    return say, "wide", None


def handle_status(facts, device, lang, rng, missing, meta, slot=None):
    if missing:
        key = next(iter(missing))
        first, second = missing_say(key, lang)
        return join([first, second]), "thinking", None

    sentences = []
    if has(facts, "temperature"):
        t = fmt_temp(facts["temperature"])
        sentences.append(
            f"Ruangan sekarang {t} derajat Celsius." if lang == "id" else f"The room is {t} degrees Celsius."
        )
    if len(sentences) < 2 and has(facts, "air_quality"):
        aq = air_level(facts, device)
        if aq in ("poor", "stale"):
            sentences.append(
                "Udaranya pengap dan perlu ventilasi." if lang == "id" else "The air is stale and needs ventilation."
            )
        elif aq == "moderate":
            sentences.append(
                "Udaranya agak pengap." if lang == "id" else "The air is a little stuffy."
            )
        else:
            sentences.append(
                "Udaranya masih segar." if lang == "id" else "The air is still fresh."
            )
    if len(sentences) < 2 and has(facts, "occupancy"):
        if facts["occupancy"]:
            sentences.append(
                "Ada orang yang sedang belajar." if lang == "id" else "Someone is studying here."
            )
        else:
            sentences.append(
                "Tidak ada orang di ruangan." if lang == "id" else "No one is in the room."
            )
    if len(sentences) < 2 and is_dark(facts, device) and facts.get("occupancy"):
        sentences.append(
            "Cahayanya masih gelap untuk belajar." if lang == "id" else "It is still too dark for studying."
        )
    if not sentences:
        return handle_missing_generic(lang), "thinking", None
    return join(sentences[:2]), mood_for(facts, device, missing, meta), None


def handle_missing_generic(lang):
    if lang == "id":
        return "Aku belum punya bacaan sensor yang bisa dipakai."
    return "I do not have any usable sensor reading yet."


def handle_temp(facts, device, lang, rng, missing, meta, slot=None):
    if not has(facts, "temperature"):
        first, second = missing_say("temperature", lang)
        return join([first, second]), "thinking", None
    t = fmt_temp(facts["temperature"])
    if lang == "id":
        sentences = [f"Suhu sekarang {t} derajat Celsius."]
        if t >= 32:
            sentences.append("Ini panas, sebaiknya buka jendela atau nyalakan kipas.")
        elif t >= 30:
            sentences.append("Agak panas, coba beri ventilasi.")
        elif t <= 18:
            sentences.append("Ini dingin, pakai jaket atau tutup jendela.")
    else:
        sentences = [f"The temperature is {t} degrees Celsius."]
        if t >= 32:
            sentences.append("That is hot, so open a window or turn on the fan.")
        elif t >= 30:
            sentences.append("It is a little warm, so ventilate the room.")
        elif t <= 18:
            sentences.append("It is cold, so wear a jacket or close the window.")
    return join(sentences), mood_temp(t), None


def handle_humidity(facts, device, lang, rng, missing, meta, slot=None):
    if not has(facts, "humidity"):
        first, second = missing_say("humidity", lang)
        return join([first, second]), "thinking", None
    h = fmt_hum(facts["humidity"])
    if lang == "id":
        sentences = [f"Kelembapannya {h} persen."]
        if h >= 70:
            sentences.append("Ini cukup lembap, beri ventilasi supaya tidak pengap.")
        elif h <= 35:
            sentences.append("Ini kering, minum air dan pertimbangkan humidifier.")
    else:
        sentences = [f"The humidity is {h} percent."]
        if h >= 70:
            sentences.append("That is quite humid, so ventilate the room.")
        elif h <= 35:
            sentences.append("That is dry, so drink water and consider a humidifier.")
    return join(sentences), mood_humidity(h), None


def handle_air(facts, device, lang, rng, missing, meta, slot=None):
    aq = air_level(facts, device)
    if aq is None:
        first, second = missing_say("air_quality", lang)
        return join([first, second]), "thinking", None
    if lang == "id":
        if aq in ("poor", "stale"):
            sentences = ["Udaranya pengap dan perlu udara segar.", "Buka jendela atau ventilasi sebentar."]
        elif aq == "moderate":
            sentences = ["Udaranya agak pengap.", "Sebaiknya beri ventilasi sebelum belajar lagi."]
        elif aq == "warmup":
            sentences = ["Sensor udara masih pemanasan.", "Tunggu sekitar dua menit lagi ya."]
        else:
            sentences = ["Udara di ruangan ini masih segar."]
            if has(facts, "co2"):
                sentences[0] = f"Udara masih segar, CO2 sekitar {fmt_lux(facts['co2'])} ppm."
    else:
        if aq in ("poor", "stale"):
            sentences = ["The air is stale and needs fresh air.", "Open a window or the vent for a moment."]
        elif aq == "moderate":
            sentences = ["The air is a little stuffy.", "Ventilate the room before studying again."]
        elif aq == "warmup":
            sentences = ["The air sensor is still warming up.", "Give it about two more minutes."]
        else:
            sentences = ["The air in this room is still fresh."]
            if has(facts, "co2"):
                sentences[0] = f"The air is fresh, with CO2 around {fmt_lux(facts['co2'])} ppm."
    return join(sentences), mood_air(aq), None


def handle_light(facts, device, lang, rng, missing, meta, slot=None):
    if device == "node":
        if not has(facts, "lighting"):
            first, second = missing_say("lighting", lang)
            return join([first, second]), "thinking", None
        lux = fmt_lux(facts["lighting"])
        dark = lux < 150
        if lang == "id":
            sentences = [f"Cahayanya sekitar {lux} lux."]
            if dark:
                sentences.append("Ini masih gelap untuk belajar, nyalakan lampu.")
            else:
                sentences.append("Cahayanya sudah cukup untuk belajar.")
        else:
            sentences = [f"The light is about {lux} lux."]
            if dark:
                sentences.append("It is still too dark, so turn on the lamp.")
            else:
                sentences.append("There is enough light for studying.")
    else:
        if "bright" not in facts:
            first, second = missing_say("bright", lang)
            return join([first, second]), "thinking", None
        bright = bool(facts["bright"])
        if lang == "id":
            sentences = [
                "Sensor cahaya bilang ruangan ini terang; stasiun ini tidak mengukur angka lux."
                if bright
                else "Sensor cahaya bilang ruangan ini gelap; stasiun ini tidak mengukur angka lux."
            ]
            if not bright:
                sentences.append("Nyalakan lampu belajar kalau kamu mau mulai.")
        else:
            sentences = [
                "The light sensor says the room is bright; this station does not measure lux."
                if bright
                else "The light sensor says the room is dark; this station does not measure lux."
            ]
            if not bright:
                sentences.append("Turn on the study lamp if you want to start.")
        dark = not bright
        return join(sentences), mood_light(dark, facts.get("occupancy", False)), None
    return join(sentences), mood_light(dark, facts.get("occupancy", False)), None


def handle_occupancy(facts, device, lang, rng, missing, meta, slot=None):
    if not has(facts, "occupancy"):
        first, second = missing_say("occupancy", lang)
        return join([first, second]), "thinking", None
    present = bool(facts["occupancy"])
    if lang == "id":
        sentences = ["Ada orang di ruangan." if present else "Tidak ada orang di ruangan."]
        if present and has(facts, "session_duration"):
            sentences.append(f"Sesi belajarnya sudah {fmt_duration(facts['session_duration'], 'id')}.")
    else:
        sentences = ["Someone is in the room." if present else "No one is in the room."]
        if present and has(facts, "session_duration"):
            sentences.append(f"The study session has run for {fmt_duration(facts['session_duration'], 'en')}.")
    return join(sentences), mood_occupancy(present), None


def handle_session(facts, device, lang, rng, missing, meta, slot=None):
    if not has(facts, "session_duration"):
        first, second = missing_say("session_duration", lang)
        return join([first, second]), "thinking", None
    duration = fmt_duration(facts["session_duration"], lang)
    minutes = int(facts["session_duration"]) // 60
    if lang == "id":
        sentences = [f"Kamu sudah belajar {duration}."]
        if minutes >= 60:
            sentences.append("Sebaiknya ambil jeda 5 menit dan minum air.")
        elif minutes >= 45:
            sentences.append("Sudah cukup lama, pertimbangkan istirahat sebentar.")
    else:
        sentences = [f"You have been studying for {duration}."]
        if minutes >= 60:
            sentences.append("Take a five-minute break and drink some water.")
        elif minutes >= 45:
            sentences.append("That is a while, so consider a short break.")
    return join(sentences), mood_session(int(facts["session_duration"])), None


def handle_break(facts, device, lang, rng, missing, meta, slot=None):
    if not has(facts, "session_duration"):
        first, second = missing_say("session_duration", lang)
        return join([first, second]), "thinking", None
    minutes = int(facts["session_duration"]) // 60
    aq = air_level(facts, device)
    hot = is_warm(facts)
    if lang == "id":
        sentences = [f"Kamu sudah belajar {fmt_duration(facts['session_duration'], 'id')}."]
        if minutes >= 60:
            sentences.append("Sebaiknya istirahat 5 menit, minum air, dan lihat jauh sebentar.")
        elif minutes >= 45 or aq in ("poor", "stale") or hot:
            sentences.append("Ambil jeda sebentar dan beri ruangan udara segar.")
        else:
            sentences.append("Belum terlalu lama, tapi tetap jaga postur dan minum air.")
    else:
        sentences = [f"You have been studying for {fmt_duration(facts['session_duration'], 'en')}."]
        if minutes >= 60:
            sentences.append("Take a five-minute break, drink water, and look into the distance.")
        elif minutes >= 45 or aq in ("poor", "stale") or hot:
            sentences.append("Pause for a moment and let some fresh air in.")
        else:
            sentences.append("Not too long yet, but keep your posture good and drink water.")
    return join(sentences), mood_break(int(facts["session_duration"]), aq, hot), None


def handle_lamp(facts, device, lang, rng, missing, meta, slot=None):
    mode = slot
    if device != "node":
        if lang == "id":
            return "Stasiun ini tidak mengontrol lampu, jadi aku tidak bisa mengubahnya.", "thinking", None
        return "This station does not control the lamp, so I cannot change it.", "thinking", None
    if not meta["online"]:
        return handle_offline(facts, device, lang, rng, missing, meta)
    if lang == "id":
        say = {
            "on": "Oke, lampu belajar kunyalakan.",
            "off": "Lampu belajar sudah kumatikan.",
            "auto": "Lampu kuset ke mode otomatis.",
        }[mode]
    else:
        say = {
            "on": "Okay, I turned the study lamp on.",
            "off": "I turned the study lamp off.",
            "auto": "I set the lamp to automatic mode.",
        }[mode]
    return say, "wink", {"lamp": mode}


def handle_fan(facts, device, lang, rng, missing, meta, slot=None):
    mode = slot
    if device != "station":
        if lang == "id":
            return "Node ruangan ini tidak punya kipas, jadi aku tidak bisa mengubahnya.", "thinking", None
        return "This room node has no fan, so I cannot change it.", "thinking", None
    if not meta["online"]:
        return handle_offline(facts, device, lang, rng, missing, meta)
    if lang == "id":
        say = {
            "on": "Kipas kunyalakan.",
            "off": "Kipas sudah kumatikan.",
            "auto": "Kipas kuset ke mode otomatis.",
        }[mode]
    else:
        say = {
            "on": "I turned the fan on.",
            "off": "I turned the fan off.",
            "auto": "I set the fan to automatic mode.",
        }[mode]
    return say, "wink", {"fan": mode}


def handle_vent(facts, device, lang, rng, missing, meta, slot=None):
    state = slot
    if device != "station":
        if lang == "id":
            return "Node ini tidak punya ventilasi otomatis. Buka jendela manual ya.", "thinking", None
        return "This node has no automatic vent. Please open the window by hand.", "thinking", None
    if not meta["online"]:
        return handle_offline(facts, device, lang, rng, missing, meta)
    if lang == "id":
        say = "Ventilasi kubuka." if state == "open" else "Ventilasi kututup."
    else:
        say = "I opened the vent." if state == "open" else "I closed the vent."
    return say, "wink", {"vent": state}


def handle_lcd(facts, device, lang, rng, missing, meta, slot=None):
    message = (slot or "StudySphere")[:LCD_MAX]
    if not meta["online"]:
        return handle_offline(facts, device, lang, rng, missing, meta)
    if lang == "id":
        say = f"Pesan \"{message}\" sudah kutampilkan di LCD."
    else:
        say = f"I put \"{message}\" on the LCD."
    return say, "wink", {"lcd": message}


def handle_alert_air(facts, device, lang, rng, missing, meta, slot=None):
    aq = air_level(facts, device)
    if lang == "id":
        if device == "node" and has(facts, "co2"):
            say = join([
                f"Udara di ruangan ini pengap, CO2 sekitar {fmt_lux(facts['co2'])} ppm.",
                "Buka jendela atau ventilasi sebentar, ya.",
            ])
        else:
            say = join([
                "Kualitas udara di ruangan ini buruk.",
                "Buka jendela atau ventilasi sebentar, ya.",
            ])
    else:
        if device == "node" and has(facts, "co2"):
            say = join([
                f"The air is stale, with CO2 around {fmt_lux(facts['co2'])} ppm.",
                "Please open a window or the vent for a moment.",
            ])
        else:
            say = join([
                "The air quality in this room is poor.",
                "Please open a window or the vent for a moment.",
            ])
    return say, "dizzy", None


def handle_alert_hot(facts, device, lang, rng, missing, meta, slot=None):
    t = fmt_temp(facts.get("temperature", 32))
    if lang == "id":
        if device == "station":
            advice = "Nyalakan kipas, buka ventilasi, dan minum air."
        else:
            advice = "Buka jendela, minum air, dan istirahat sebentar."
        say = join([f"Suhu ruangan sudah {t} derajat Celsius.", advice])
    else:
        if device == "station":
            advice = "Turn on the fan, open the vent, and drink water."
        else:
            advice = "Open a window, drink water, and take a short break."
        say = join([f"The room is already {t} degrees Celsius.", advice])
    return say, "wide", None


def handle_save_energy(facts, device, lang, rng, missing, meta, slot=None):
    if device == "node":
        empty = is_empty(facts)
        lamp_on = facts.get("lamp") == "on"
        if empty and lamp_on:
            if lang == "id":
                say = "Tidak ada orang, tapi lampu masih menyala. Aku matikan supaya tidak boros."
            else:
                say = "No one is here, but the lamp is still on. I turned it off to save power."
            return say, "wink", {"lamp": "off"}
        if lang == "id":
            say = "Lampu dan kehadiran sudah sesuai, belum ada yang perlu dimatikan."
        else:
            say = "The lamp and occupancy look fine, nothing needs to be turned off."
        return say, mood_for(facts, device, missing, meta), None

    empty = is_empty(facts)
    fan_on = facts.get("fan") is True
    if empty and fan_on:
        if lang == "id":
            say = "Ruangan kosong tapi kipas masih menyala. Aku matikan supaya tidak boros."
        else:
            say = "The room is empty but the fan is still on. I turned it off to save power."
        return say, "wink", {"fan": "off"}
    if lang == "id":
        say = "Kipas dan ventilasi sudah sesuai, belum ada yang perlu dimatikan."
    else:
        say = "The fan and vent look fine, nothing needs to be turned off."
    return say, mood_for(facts, device, missing, meta), None


def handle_open_window(facts, device, lang, rng, missing, meta, slot=None):
    aq = air_level(facts, device)
    if aq in ("poor", "stale"):
        if device == "station":
            if lang == "id":
                return "Udaranya pengap, jadi ventilasi kubuka.", "wink", {"vent": "open"}
            return "The air is stale, so I opened the vent.", "wink", {"vent": "open"}
        if lang == "id":
            return "Udaranya pengap. Buka jendela sebentar ya, node ini tidak punya ventilasi otomatis.", "dizzy", None
        return "The air is stale. Open a window for a moment; this node has no automatic vent.", "dizzy", None
    if lang == "id":
        return "Udaranya masih segar, belum perlu buka jendela.", "happy", None
    return "The air is still fresh, so there is no need to open a window.", "happy", None


def handle_fan_off_hot(facts, device, lang, rng, missing, meta, slot=None):
    if device != "station":
        if lang == "id":
            return "Node ini tidak punya kipas, jadi tidak ada yang bisa kumatikan.", "thinking", None
        return "This node has no fan, so there is nothing to turn off.", "thinking", None
    if lang == "id":
        say = join([
            "Kipasnya kumatikan karena kamu minta.",
            "Tapi ruangan ini panas, jadi sebaiknya buka ventilasi atau nyalakan lagi nanti.",
        ])
    else:
        say = join([
            "I turned the fan off because you asked.",
            "The room is hot, though, so open the vent or turn it back on soon.",
        ])
    return say, "wide", {"fan": "off"}


def handle_multi(facts, device, lang, rng, missing, meta, slot=None):
    if device != "node":
        return handle_lamp(facts, device, lang, rng, missing, meta, "on")
    aq = air_level(facts, device)
    if lang == "id":
        first = "Lampu belajar kunyalakan."
        second = "Udaranya masih segar." if aq == "good" else "Tapi udaranya pengap, sebaiknya buka jendela."
    else:
        first = "I turned the study lamp on."
        second = "The air is still fresh." if aq == "good" else "But the air is stale, so open a window."
    return join([first, second]), "wink", {"lamp": "on"}


def handle_greeting(facts, device, lang, rng, missing, meta, slot=None):
    if lang == "id":
        say = pick(rng, ["Halo! Aku siap menjaga ruang belajarmu.", "Hai! Mau cek ruangan atau mulai belajar?"], [], lang)
    else:
        say = pick(rng, [], ["Hello! I am here to look after your study room.", "Hi! Want to check the room or start studying?"], lang)
    return say, "happy", None


def handle_thanks(facts, device, lang, rng, missing, meta, slot=None):
    if lang == "id":
        say = pick(rng, ["Sama-sama, semangat belajarnya!", "Dengan senang hati. Istirahat kalau capek, ya."], [], lang)
    else:
        say = pick(rng, [], ["You are welcome, good luck studying!", "Happy to help. Take a break if you get tired."], lang)
    return say, "love", None


def handle_unknown(facts, device, lang, rng, missing, meta, slot=None):
    if lang == "id":
        say = "Aku belum punya sensor untuk itu, jadi belum bisa mengukurnya."
    else:
        say = "I do not have a sensor for that, so I cannot measure it."
    return say, "thinking", None


def handle_co2_station(facts, device, lang, rng, missing, meta, slot=None):
    if lang == "id":
        say = "Stasiun ini melaporkan level kualitas udara, bukan angka ppm CO2."
    else:
        say = "This station reports an air-quality level, not a CO2 ppm number."
    return say, "thinking", None


# ---------------------------------------------------------------------------
# Intents


INTENTS = [
    {
        "id": "status",
        "devices": ["node", "station"],
        "handler": handle_status,
        "id_texts": ["gimana kondisi ruangan sekarang?", "kasih ringkasan ruangan dong.", "ruangan aman nggak?", "cek keadaan ruangan sebentar."],
        "en": ["how is the room right now?", "give me a room summary.", "is the room okay?", "check the room state."],
    },
    {
        "id": "temp",
        "devices": ["node", "station"],
        "handler": handle_temp,
        "id_texts": ["suhu sekarang berapa?", "cek suhu dong.", "panas nggak ruangan ini?", "berapa derajat sekarang?"],
        "en": ["what is the temperature?", "check the temperature.", "is it hot in here?", "how many degrees is it?"],
    },
    {
        "id": "humidity",
        "devices": ["node", "station"],
        "handler": handle_humidity,
        "id_texts": ["kelembapannya berapa?", "cek kelembapan ruangan.", "apakah ruangan ini lembap?", "berapa persen humidity-nya?"],
        "en": ["what is the humidity?", "check the room humidity.", "is it humid in here?", "what is the humidity percentage?"],
    },
    {
        "id": "air",
        "devices": ["node", "station"],
        "handler": handle_air,
        "id_texts": ["udara di sini pengap nggak?", "cek kualitas udara dong.", "apakah udaranya masih segar?", "aku pusing, udaranya buruk ya?"],
        "en": ["is the air stuffy?", "check the air quality.", "is the air still fresh?", "i have a headache, is the air bad?"],
    },
    {
        "id": "light",
        "devices": ["node", "station"],
        "handler": handle_light,
        "id_texts": ["cahayanya cukup nggak?", "cek lampu dan cahaya dong.", "di sini gelap nggak?", "berapa lux sekarang?"],
        "en": ["is there enough light?", "check the light level.", "is it dark in here?", "how many lux is it?"],
    },
    {
        "id": "occupancy",
        "devices": ["node", "station"],
        "handler": handle_occupancy,
        "id_texts": ["ada orang di ruangan nggak?", "aku sendirian?", "cek kehadiran dong.", "ruangan ini kosong nggak?"],
        "en": ["is anyone in the room?", "am i alone?", "check occupancy.", "is the room empty?"],
    },
    {
        "id": "session",
        "devices": ["node", "station"],
        "handler": handle_session,
        "id_texts": ["aku udah belajar berapa lama?", "cek durasi belajar dong.", "sesi belajarku sudah berapa menit?", "sudah berapa lama aku di meja?"],
        "en": ["how long have i been studying?", "check the study duration.", "how many minutes is my session?", "how long have i been at this desk?"],
    },
    {
        "id": "break",
        "devices": ["node", "station"],
        "handler": handle_break,
        "id_texts": ["aku capek, perlu istirahat nggak?", "udah lama belajar, mending break?", "apakah aku harus istirahat sekarang?", "kasih saran istirahat dong."],
        "en": ["i am tired, should i take a break?", "i have studied for a while, should i stop?", "do i need a break now?", "advise me on a break."],
    },
    {
        "id": "lamp_on",
        "devices": ["node", "station"],
        "handler": handle_lamp,
        "slot": "on",
        "id_texts": ["nyalakan lampu belajar.", "lampu nyala dong.", "tolong hidupkan lampu.", "lampu belajarnya dinyalakan ya."],
        "en": ["turn on the study lamp.", "lamp on please.", "switch the lamp on.", "please turn the study light on."],
    },
    {
        "id": "lamp_off",
        "devices": ["node", "station"],
        "handler": handle_lamp,
        "slot": "off",
        "id_texts": ["matikan lampunya.", "lampu off dong.", "tolong matikan lampu belajar.", "lampu belajarnya dimatikan ya."],
        "en": ["turn off the lamp.", "lamp off please.", "switch the study lamp off.", "please turn the light off."],
    },
    {
        "id": "lamp_auto",
        "devices": ["node", "station"],
        "handler": handle_lamp,
        "slot": "auto",
        "id_texts": ["set lampu ke mode otomatis.", "lampu auto aja.", "pakai mode auto untuk lampu.", "kembalikan lampu ke otomatis."],
        "en": ["set the lamp to automatic mode.", "lamp auto please.", "use auto mode for the lamp.", "return the lamp to automatic."],
    },
    {
        "id": "fan_on",
        "devices": ["node", "station"],
        "handler": handle_fan,
        "slot": "on",
        "id_texts": ["nyalakan kipas.", "kipas on dong.", "tolong hidupkan fan.", "kipasnya dinyalakan ya."],
        "en": ["turn on the fan.", "fan on please.", "switch the fan on.", "please start the fan."],
    },
    {
        "id": "fan_off",
        "devices": ["node", "station"],
        "handler": handle_fan,
        "slot": "off",
        "id_texts": ["matikan kipas.", "kipas off dong.", "tolong matikan fan.", "kipasnya dimatikan ya."],
        "en": ["turn off the fan.", "fan off please.", "switch the fan off.", "please stop the fan."],
    },
    {
        "id": "fan_auto",
        "devices": ["node", "station"],
        "handler": handle_fan,
        "slot": "auto",
        "id_texts": ["kipas auto aja.", "set fan ke otomatis.", "pakai mode auto untuk kipas.", "kembalikan kipas ke otomatis."],
        "en": ["set the fan to auto.", "fan auto please.", "use automatic mode for the fan.", "return the fan to automatic."],
    },
    {
        "id": "vent_open",
        "devices": ["node", "station"],
        "handler": handle_vent,
        "slot": "open",
        "id_texts": ["buka ventilasinya.", "buka vent dong.", "tolong buka jendela otomatis.", "ventilasi dibuka ya."],
        "en": ["open the vent.", "open the ventilation please.", "please open the automatic window.", "open it up."],
    },
    {
        "id": "vent_close",
        "devices": ["node", "station"],
        "handler": handle_vent,
        "slot": "closed",
        "id_texts": ["tutup ventilasinya.", "tutup vent dong.", "tolong tutup jendela otomatis.", "ventilasi ditutup ya."],
        "en": ["close the vent.", "close the ventilation please.", "please close the automatic window.", "shut it."],
    },
    {
        "id": "lcd",
        "devices": ["node", "station"],
        "handler": handle_lcd,
        "slots": ["Istirahat 5 menit", "Fokus 25 menit", "Minum air", "Jadwal belajar", "Rest 5 minutes", "Focus 25 minutes", "Drink water", "Study schedule"],
        "id_texts": ["tampilkan pesan \"{slot}\" di lcd.", "tulis \"{slot}\" di layar lcd.", "kirim pesan \"{slot}\" ke lcd."],
        "en": ["show the message \"{slot}\" on the lcd.", "write \"{slot}\" on the lcd screen.", "send \"{slot}\" to the lcd."],
    },
    {
        "id": "alert_air",
        "devices": ["node", "station"],
        "handler": handle_alert_air,
        "states": ["stale", "hot_stale"],
        "id_texts": [
            "[alert] {facts}",
            "Peringatan ruangan: {facts}",
            "Sistem mendeteksi: {facts}",
            "Tolong beri tahu: {facts}",
        ],
        "en": [
            "[alert] {facts}",
            "Room warning: {facts}",
            "Sensor alert: {facts}",
            "Please pass this on: {facts}",
        ],
    },
    {
        "id": "alert_hot",
        "devices": ["node", "station"],
        "handler": handle_alert_hot,
        "states": ["hot", "hot_stale"],
        "id_texts": [
            "[alert] {facts}",
            "Peringatan suhu: {facts}",
            "Sistem mendeteksi: {facts}",
            "Tolong beri tahu: {facts}",
        ],
        "en": [
            "[alert] {facts}",
            "Temperature warning: {facts}",
            "Sensor alert: {facts}",
            "Please pass this on: {facts}",
        ],
    },
    {
        "id": "save_energy",
        "devices": ["node", "station"],
        "handler": handle_save_energy,
        "states": ["empty_lamp", "empty"],
        "id_texts": ["aku mau pergi, cek boros energi nggak.", "matikan yang masih nyala kalau ruangan kosong.", "apakah ada alat yang lupa dimatikan?"],
        "en": ["i am leaving, check for wasted energy.", "turn off anything still running if the room is empty.", "is anything left on by mistake?"],
    },
    {
        "id": "open_window",
        "devices": ["node", "station"],
        "handler": handle_open_window,
        "states": ["stale", "hot_stale", "comfortable"],
        "id_texts": ["perlu buka jendela nggak?", "buka ventilasi kalau udaranya buruk.", "apakah ruangan perlu udara segar?"],
        "en": ["should i open a window?", "open the vent if the air is bad.", "does the room need fresh air?"],
    },
    {
        "id": "fan_off_hot",
        "devices": ["station"],
        "handler": handle_fan_off_hot,
        "states": ["hot", "hot_stale"],
        "id_texts": ["matikan kipasnya, aku kedinginan.", "kipas off aja walaupun panas.", "matikan fan sekarang."],
        "en": ["turn the fan off, i feel cold.", "fan off even though it is hot.", "stop the fan now."],
    },
    {
        "id": "multi",
        "devices": ["node"],
        "handler": handle_multi,
        "id_texts": ["nyalakan lampu dan cek udaranya.", "lampu on, terus udaranya gimana?", "hidupkan lampu belajar sambil cek kualitas udara."],
        "en": ["turn on the lamp and check the air.", "lamp on, and how is the air?", "switch the lamp on while checking air quality."],
    },
    {
        "id": "greeting",
        "devices": ["node", "station"],
        "handler": handle_greeting,
        "id_texts": ["halo, apa kabar?", "selamat pagi.", "hai burung hantu.", "kamu di sana?"],
        "en": ["hello, how are you?", "good morning.", "hi owl.", "are you there?"],
    },
    {
        "id": "thanks",
        "devices": ["node", "station"],
        "handler": handle_thanks,
        "id_texts": ["makasih ya.", "terima kasih bantuannya.", "oke, thanks.", "good job."],
        "en": ["thanks.", "thank you for the help.", "okay, thanks.", "good job."],
    },
    {
        "id": "unknown_noise",
        "devices": ["node", "station"],
        "handler": handle_unknown,
        "id_texts": ["noise di sini berapa desibel?", "cek tingkat kebisingan ruangan.", "apakah ruangan ini terlalu berisik?"],
        "en": ["what is the noise level in decibels?", "check the room noise.", "is this room too noisy?"],
    },
    {
        "id": "co2_station",
        "devices": ["station"],
        "handler": handle_co2_station,
        "id_texts": ["co2 di ruangan ini berapa ppm?", "cek kadar co2 dong.", "berapa ppm co2 sekarang?"],
        "en": ["what is the co2 level in ppm?", "check the co2 reading.", "how many ppm of co2 is there?"],
    },
]


def build_system_prompt(device: str, facts: dict, meta: dict, missing: dict | None = None) -> str:
    missing = missing or {}
    if device == "node":
        commands = '"lamp": "auto" | "on" | "off" (the study lamp), "lcd": "at most 32 characters"'
        legend = (
            "temperature: degrees Celsius; humidity: percent RH; lighting: lux; "
            "co2: estimated ppm from an MQ-135 (not a true CO2 sensor); "
            "air_quality: good or stale; occupancy: a study session is running; "
            "session_duration: seconds; lamp: on/off; lamp_mode: auto/on/off."
        )
        kind = "room node"
    else:
        commands = '"fan": "auto" | "on" | "off" (auto: on from 30 C, off at 29 C), "vent": "open" | "closed", "lcd": "at most 32 characters"'
        legend = (
            "temperature: degrees Celsius; humidity: percent RH; bright: the light sensor says bright "
            "(false = dark); air_quality: good/moderate/poor/warmup; occupancy: a study session is running; "
            "session_duration: seconds; distance_cm: distance to the person; fan: on/off; "
            "vent: 0-180 degrees (open is about 180); fan_mode: auto/on/off."
        )
        kind = "learning station"

    age = meta["age_s"]
    if meta["online"]:
        freshness = f"Readings from {age} s ago."
    else:
        freshness = (
            f"Readings from {age} s ago. The node has gone quiet: these are out of date and commands will not reach it."
        )

    return f"""You are the owl who looks after a StudySphere smart {kind}. You live in a small island at the top of the student's screen, keep an eye on the room and chat with the student. Be warm, brief and a little playful.
Answer with one JSON object and nothing else:
{{"say": "...", "mood": "...", "command": {{...}} or null}}
say: what you say out loud, at most two short sentences, in the language the student used. No emoji, lists or markdown. Round readings to whole numbers and write units as words in that language (28 derajat, 60 persen).
mood: one of {MOOD_LIST}.
command: what to change in the room, or null. Only these keys and values: {commands}. Only send a command when the student asks for a change, and say what you changed. If this {kind} does not support a command, say so and use null.
A message that starts with [alert] comes from the room's sensors, not the student: pass the warning on, briefly and kindly, look dizzy or wide, and send no command.
Only use the facts below; never make up a reading. If something is not measured, say so.
{freshness}
FACTS: {compact_json(facts)}
Legend: {legend}
MISSING: {compact_json(missing)}"""


def make_alert_text(intent_id, device, facts, lang, rng):
    if intent_id == "alert_air":
        if lang == "id":
            if device == "node" and has(facts, "co2"):
                ppm = fmt_lux(facts["co2"])
                return rng.choice([
                    f"[alert] Udara pengap (CO2 {ppm} ppm): sebaiknya buka jendela.",
                    f"Peringatan ruangan: CO2 sudah {ppm} ppm, buka jendela sebentar.",
                    f"Sistem mendeteksi udara pengap dengan CO2 {ppm} ppm; beri ventilasi.",
                ])
            return rng.choice([
                "[alert] Kualitas udara buruk: sebaiknya buka jendela atau ventilasi.",
                "Peringatan ruangan: kualitas udara buruk, buka ventilasi sebentar.",
                "Sistem mendeteksi udara pengap; sebaiknya beri udara segar.",
            ])
        if device == "node" and has(facts, "co2"):
            ppm = fmt_lux(facts["co2"])
            return rng.choice([
                f"[alert] The air is stale (CO2 {ppm} ppm): the student should open a window.",
                f"Room warning: CO2 has reached {ppm} ppm; open a window for a moment.",
                f"Sensor alert: stale air at {ppm} ppm CO2; ventilate the room.",
            ])
        return rng.choice([
            "[alert] The air quality is poor: the student should open a window or the vent.",
            "Room warning: the air quality is poor; open the vent for a moment.",
            "Sensor alert: the air is stale; let some fresh air in.",
        ])
    t = fmt_temp(facts.get("temperature", 32))
    if lang == "id":
        if device == "station":
            return rng.choice([
                f"[alert] Suhu {t} derajat Celsius: sebaiknya nyalakan kipas dan buka ventilasi.",
                f"Peringatan suhu: ruangan sudah {t} derajat Celsius, nyalakan kipas.",
                f"Sistem mendeteksi panas {t} derajat Celsius; buka ventilasi.",
            ])
        return rng.choice([
            f"[alert] Suhu {t} derajat Celsius: sebaiknya buka jendela dan istirahat sebentar.",
            f"Peringatan suhu: ruangan sudah {t} derajat Celsius, buka jendela.",
            f"Sistem mendeteksi panas {t} derajat Celsius; minum air dan istirahat.",
        ])
    if device == "station":
        return rng.choice([
            f"[alert] It is {t} C: the fan should be on and the vent open.",
            f"Temperature warning: the room is {t} C; turn on the fan and open the vent.",
            f"Sensor alert: {t} C in the room; ventilate and cool it down.",
        ])
    return rng.choice([
        f"[alert] It is {t} C: the student should open a window and take a short break.",
        f"Temperature warning: the room is {t} C; open a window and rest.",
        f"Sensor alert: {t} C in the room; drink water and take a short break.",
    ])


def build_rows(rng: random.Random, variants: int):
    rows = []
    used = set()

    groups = {}
    for intent in INTENTS:
        for lang in ("id", "en"):
            texts = intent["id_texts"] if lang == "id" else intent["en"]
            for template_index in range(len(texts)):
                template_id = f"{intent['id']}-{lang}-{template_index}"
                groups.setdefault(intent["id"], []).append(template_id)
    split_map = assign_template_splits(groups)

    for intent in INTENTS:
        handler = intent["handler"]
        for lang in ("id", "en"):
            texts = intent["id_texts"] if lang == "id" else intent["en"]
            for template_index, template in enumerate(texts):
                template_id = f"{intent['id']}-{lang}-{template_index}"
                split = split_map[template_id]
                for variant in range(variants):
                    device = rng.choice(intent["devices"])
                    states = intent.get("states")
                    kind = rng.choice(states) if states else rng.choice(STATES)
                    facts, meta, missing = sample_state(device, rng, kind)
                    facts = dict(facts)
                    if missing:
                        facts["_missing"] = missing

                    slot = None
                    if "slot" in intent:
                        slot = intent["slot"]
                    elif "slots" in intent:
                        slot = rng.choice(intent["slots"])

                    utterance = template
                    if "{slot}" in utterance:
                        if slot is None:
                            slot = rng.choice(intent.get("slots", ["StudySphere"]))
                        utterance = utterance.replace("{slot}", slot)
                    if "{facts}" in utterance:
                        # Alerts are generated from the room state, as the desktop does.
                        utterance = make_alert_text(intent["id"], device, facts, lang, rng)
                        if not utterance.startswith("[alert]"):
                            utterance = "[alert] " + utterance

                    prefix = rng.choice(ID_PREFIX if lang == "id" else EN_PREFIX)
                    if prefix and not utterance.startswith("[alert]"):
                        utterance = prefix + utterance
                    if utterance and not utterance.startswith("[alert]"):
                        utterance = utterance[0].upper() + utterance[1:]
                    if rng.random() < 0.035 and not utterance.startswith("[alert]"):
                        utterance = add_typo(utterance, rng)

                    say, mood, command = handler(facts, device, lang, rng, missing, meta, slot)
                    if mood not in MOODS:
                        raise SystemExit(f"bad mood {mood} from {intent['id']}")
                    if command is not None:
                        if "lcd" in command and len(command["lcd"]) > LCD_MAX:
                            command["lcd"] = command["lcd"][:LCD_MAX]
                    if not meta["online"] and command is not None:
                        # The desktop refuses commands when the node is silent.
                        command = None
                    output = {"say": say, "mood": mood, "command": command}
                    clean_facts = {k: v for k, v in facts.items() if k != "_missing"}
                    system_prompt = build_system_prompt(device, clean_facts, meta, missing)

                    key = (split, device, normalise_text(utterance), compact_json(facts), say)
                    if key in used:
                        continue
                    used.add(key)
                    rows.append(
                        make_row(
                            f"responder-{template_id}-{variant:02d}-{device}",
                            "responder",
                            [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": utterance},
                                {"role": "assistant", "content": compact_json(output)},
                            ],
                            {
                                "intent": intent["id"],
                                "template": template_id,
                                "language": lang,
                                "device": device,
                                "split": split,
                                "state": kind,
                                "missing": missing,
                                "online": meta["online"],
                            },
                        )
                    )
    return rows


def balance(rows, target_train: int, target_val: int, target_test: int, rng: random.Random):
    by_split = {"train": [], "val": [], "test": []}
    for row in rows:
        by_split[row["meta"]["split"]].append(row)
    target = {"train": target_train, "val": target_val, "test": target_test}
    out = {}
    for split, items in by_split.items():
        rng.shuffle(items)
        selected = []
        seen = set()
        remainder = []
        for item in items:
            intent = item["meta"]["intent"]
            if intent not in seen:
                selected.append(item)
                seen.add(intent)
            else:
                remainder.append(item)
        rng.shuffle(remainder)
        selected.extend(remainder)
        out[split] = selected[: target[split]]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", type=int, default=900, help="variants per template")
    parser.add_argument("--train", type=int, default=100000)
    parser.add_argument("--val", type=int, default=10000)
    parser.add_argument("--test", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--out", type=Path, default=DATA / "responder")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    rows = build_rows(rng, args.variants)
    splits = balance(rows, args.train, args.val, args.test, rng)
    total = 0
    for split, items in splits.items():
        count = write_jsonl(args.out / f"{split}.jsonl", items)
        total += count
        print(f"responder/{split}.jsonl: {count} rows")
    print(f"responder total: {total} rows")


if __name__ == "__main__":
    main()


