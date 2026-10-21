#!/usr/bin/env python3
"""Validate the generated StudySphere datasets before training.

Checks are intentionally strict about the contract the desktop and firmware
expect: one JSON object, allowed keys, allowed command values, at most two
sentences, and no invented sensor names.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from common import (
    COMMAND_TASKS,
    DATA,
    INFO,
    MOODS,
    TASKS,
    compact_json,
    info_cost,
    normalise_text,
    read_jsonl,
)

SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")
EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]"
)
MARKDOWN = re.compile(r"(\*\*|__|`|^#|^\s*[-*]\s|\]\([^)]+\))", re.MULTILINE)


class Problems:
    def __init__(self):
        self.items = []

    def add(self, where, message):
        self.items.append((where, message))

    def report(self, limit=40):
        if not self.items:
            print("  no problems")
            return 0
        print(f"  {len(self.items)} problem(s):")
        for where, message in self.items[:limit]:
            print(f"    - {where}: {message}")
        if len(self.items) > limit:
            print(f"    ... {len(self.items) - limit} more")
        return len(self.items)


def parse_assistant(row):
    messages = row.get("messages", [])
    if len(messages) != 3:
        raise ValueError("expected exactly system/user/assistant messages")
    if [m.get("role") for m in messages] != ["system", "user", "assistant"]:
        raise ValueError("message roles must be system, user, assistant")
    return json.loads(messages[2]["content"])


def check_planner(row, problems, where):
    try:
        out = parse_assistant(row)
    except Exception as exc:
        problems.add(where, f"invalid assistant JSON: {exc}")
        return None
    if set(out) != {"task", "required_information"}:
        problems.add(where, f"output keys must be task/required_information, got {sorted(out)}")
    task = out.get("task")
    required = out.get("required_information")
    if task not in TASKS:
        problems.add(where, f"unknown task {task!r}")
        return None
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        problems.add(where, "required_information must be a list of strings")
        return None
    if len(required) != len(set(required)):
        problems.add(where, "required_information contains duplicates")

    user = row["messages"][1]["content"]
    if not user.startswith("CONTEXT: "):
        problems.add(where, "user message must start with CONTEXT:")
        return out
    first_line, _, rest = user.partition("\nREQUEST: ")
    context = json.loads(first_line[len("CONTEXT: ") :])
    if not rest:
        problems.add(where, "user message has no REQUEST line")
    available = set(context.get("available_information", []))
    device = context.get("device_type")
    unknown = set(required) - available
    if unknown:
        problems.add(where, f"requested unavailable information {sorted(unknown)}")
    for name in required:
        if name not in INFO:
            problems.add(where, f"unknown information name {name!r}")

    if task in COMMAND_TASKS and required:
        problems.add(where, f"command task {task} must request no sensor data, got {required}")
    if "co2" in required and device != "node":
        problems.add(where, "co2 is only available on the room node")
    if ("fan" in required or "vent" in required) and device != "station":
        problems.add(where, "fan/vent are only available on the station")
    if ("lamp" in required or "lamp_mode" in required) and device != "node":
        problems.add(where, "lamp/lamp_mode are only available on the room node")

    allowed = {
        "thermal_comfort_check": {"temperature", "humidity"},
        "air_quality_check": {"air_quality", "co2", "temperature"},
        "lighting_check": {"lighting", "occupancy", "lamp_mode"},
        "occupancy_check": {"occupancy", "session_duration"},
        "study_break_advice": {"session_duration", "temperature", "air_quality", "lighting"},
        "ventilation_decision": {"air_quality", "temperature"},
        "study_environment_assessment": {
            "temperature", "humidity", "air_quality", "lighting", "occupancy", "co2"
        },
    }
    if task in allowed and not set(required) <= allowed[task]:
        problems.add(where, f"{task} includes unrelated information {sorted(set(required) - allowed[task])}")
    if task == "energy_saving_check":
        if device == "node" and not set(required) <= {"occupancy", "lamp", "lamp_mode", "lighting"}:
            problems.add(where, "node energy check must use occupancy/lamp/lamp_mode/lighting")
        if device == "station" and not set(required) <= {"occupancy", "fan", "vent", "temperature"}:
            problems.add(where, "station energy check must use occupancy/fan/vent/temperature")

    previous = context.get("previous")
    if previous:
        if task != previous.get("task"):
            problems.add(where, f"progressive task changed from {previous.get('task')} to {task}")
        already = set(previous.get("data", {}))
        repeated = set(required) & already
        if repeated:
            problems.add(where, f"progressive request repeats data {sorted(repeated)}")

    # The profile label and the serialized label must agree.
    expected = out
    if row.get("meta", {}).get("gold_cost") != info_cost(required):
        problems.add(where, "gold_cost does not match required_information")
    return expected


def sentence_count(text):
    return len(SENTENCE_END.findall(text))


def check_responder(row, problems, where):
    try:
        out = parse_assistant(row)
    except Exception as exc:
        problems.add(where, f"invalid assistant JSON: {exc}")
        return None
    if set(out) != {"say", "mood", "command"}:
        problems.add(where, f"output keys must be say/mood/command, got {sorted(out)}")
    say = out.get("say")
    mood = out.get("mood")
    command = out.get("command")
    if not isinstance(say, str) or not say.strip():
        problems.add(where, "say must be a non-empty string")
        return None
    if sentence_count(say) > 2:
        problems.add(where, f"say has {sentence_count(say)} sentences: {say!r}")
    if EMOJI.search(say):
        problems.add(where, "say contains an emoji")
    if MARKDOWN.search(say):
        problems.add(where, "say contains markdown")
    if mood not in MOODS:
        problems.add(where, f"invalid mood {mood!r}")

    system = row["messages"][0]["content"]
    if "smart learning station" in system:
        device = "station"
    elif "smart room node" in system:
        device = "node"
    else:
        problems.add(where, "cannot infer device from system prompt")
        return out
    user = row["messages"][1]["content"]
    if user.startswith("[alert]") and command is not None:
        problems.add(where, "alert turns must not send a command")
    if "has gone quiet" in system and command is not None:
        problems.add(where, "offline turns must not send a command")

    if command is not None:
        if not isinstance(command, dict) or not command:
            problems.add(where, "command must be null or a non-empty object")
        else:
            allowed = {"lcd"}
            if device == "node":
                allowed |= {"lamp"}
            else:
                allowed |= {"fan", "vent"}
            extra = set(command) - allowed
            if extra:
                problems.add(where, f"command keys not supported by {device}: {sorted(extra)}")
            if "lamp" in command and command["lamp"] not in ("auto", "on", "off"):
                problems.add(where, f"bad lamp value {command['lamp']!r}")
            if "fan" in command and command["fan"] not in ("auto", "on", "off"):
                problems.add(where, f"bad fan value {command['fan']!r}")
            if "vent" in command and command["vent"] not in ("open", "closed"):
                problems.add(where, f"bad vent value {command['vent']!r}")
            if "lcd" in command:
                if not isinstance(command["lcd"], str) or not command["lcd"].strip():
                    problems.add(where, "lcd must be a non-empty string")
                elif len(command["lcd"]) > 32:
                    problems.add(where, f"lcd is {len(command['lcd'])} characters (max 32)")
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args()

    exit_code = 0
    for task_type in ("planner", "responder"):
        print(f"\n=== {task_type} ===")
        all_users = defaultdict(set)
        stats = Counter()
        languages = Counter()
        devices = Counter()
        profiles = Counter()
        for split in ("train", "val", "test"):
            path = args.data / task_type / f"{split}.jsonl"
            if not path.exists():
                print(f"  missing {path}")
                exit_code = 1
                continue
            problems = Problems()
            count = 0
            for row in read_jsonl(path):
                count += 1
                where = f"{split}:{row.get('id', count)}"
                stats[split] += 1
                meta = row.get("meta", {})
                languages[meta.get("language", "?")] += 1
                devices[meta.get("device", "?")] += 1
                if task_type == "planner":
                    check_planner(row, problems, where)
                    profiles[meta.get("profile", "?")] += 1
                else:
                    check_responder(row, problems, where)
                    profiles[meta.get("intent", "?")] += 1
                user_text = row["messages"][1]["content"]
                if task_type == "planner":
                    user_text = user_text.partition("\nREQUEST: ")[2]
                # Alerts are generated by the system, not typed by the student;
                # their exact wording is allowed to repeat across splits.
                if not user_text.startswith("[alert]"):
                    all_users[split].add(normalise_text(user_text))
            print(f"  {split}: {count} rows")
            exit_code |= 1 if problems.report() else 0

        overlap = (all_users["train"] & all_users["test"])
        overlap_val = (all_users["train"] & all_users["val"])
        print(f"  unique user texts: train={len(all_users['train'])}, val={len(all_users['val'])}, test={len(all_users['test'])}")
        print(f"  exact train/test text overlap: {len(overlap)}; train/val: {len(overlap_val)}")
        print(f"  languages: {dict(languages)}")
        print(f"  devices: {dict(devices)}")
        print(f"  profiles: {len(profiles)} distinct")
        if len(overlap) > max(5, len(all_users["test"]) * 0.05):
            print("  WARNING: more than 5% exact text overlap between train and test")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
