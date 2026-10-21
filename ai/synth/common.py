#!/usr/bin/env python3
"""Shared helpers for the StudySphere synthetic dataset generators.

The generators are deliberately dependency-free: only the Python standard
library is needed, so the dataset is reproducible on any laptop or in CI.

Two datasets are produced:

  planner   : user request + room context -> {"task", "required_information"}
  responder : room facts + user request   -> {"say", "mood", "command"}

Both are stored as JSONL with a chat-style `messages` list.  Training code
applies the tokenizer's own chat template, so the same files work for Gemma,
Qwen, Llama and other instruction-tuned bases.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "spec" / "taxonomy.json"
DATA = ROOT / "data"

TAXONOMY = json.loads(SPEC.read_text(encoding="utf-8"))
INFO = TAXONOMY["information"]
INFO_NAMES = list(INFO)
CANONICAL = TAXONOMY["canonical_order"]
DEVICE_INFO = {
    device: [name for name in INFO_NAMES if device in INFO[name]["devices"]]
    for device in ("node", "station")
}
SOURCE_COST = {
    name: int(meta["source_cost"]) for name, meta in INFO.items()
}
MOODS = set(TAXONOMY["moods"])
TASKS = set(TAXONOMY["tasks"])
COMMAND_TASKS = set(TAXONOMY["command_tasks"])


def compact_json(value) -> str:
    """The exact JSON style the models are trained to emit."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def pretty_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def canonical_info(names) -> list[str]:
    """Stable order so the model learns one serialization, not many."""
    chosen = set(names)
    return [name for name in CANONICAL if name in chosen]


def info_cost(names) -> int:
    """Cost of the distinct sensor sources needed for an information set.

    This is the same accounting the station firmware uses: reading one source
    once can satisfy every information item that source provides.  State-only
    items cost zero.
    """
    # Distinct source costs, not the sum over items: the DHT gives temperature
    # and humidity in one read; the UNO gives lighting and air quality in one hop.
    by_source = {}
    for name in names:
        meta = INFO.get(name)
        if not meta or meta["source"] in ("state", "derived"):
            continue
        by_source[meta["source"]] = max(by_source.get(meta["source"], 0), int(meta["source_cost"]))
    return sum(by_source.values())


def split_for(key: str) -> str:
    """Deterministic 80/10/10 split by template family, not by example.

    Keeping every paraphrase of one template in a single split prevents the
    validation/test sets from being near-duplicates of the training set.
    """
    bucket = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "val"
    return "test"


def assign_template_splits(groups: dict[str, list[str]]) -> dict[str, str]:
    """Assign templates so every task/intent appears in train, val and test.

    The first three templates of each group are forced to train/val/test; the
    rest use the deterministic 80/10/10 hash.  This keeps evaluation coverage
    for small task groups without putting paraphrases of one template in two
    different splits.
    """
    mapping = {}
    for template_ids in groups.values():
        for index, template_id in enumerate(sorted(set(template_ids))):
            if index == 0:
                mapping[template_id] = "train"
            elif index == 1:
                mapping[template_id] = "val"
            elif index == 2:
                mapping[template_id] = "test"
            else:
                mapping[template_id] = split_for(template_id)
    return mapping


def write_jsonl(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(compact_json(row))
            handle.write("\n")
            count += 1
    return count


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {exc}") from exc


def normalise_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def add_typo(text: str, rng: random.Random) -> str:
    """A light, label-preserving typo for robustness.  Used on <5% of rows."""
    words = text.split()
    if len(words) < 3:
        return text
    index = rng.randrange(1, len(words))
    word = words[index]
    if len(word) < 5 or not word.isalpha():
        return text
    operation = rng.choice(("swap", "drop", "double"))
    pos = rng.randrange(1, len(word) - 1)
    if operation == "swap":
        chars = list(word)
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]
        word = "".join(chars)
    elif operation == "drop":
        word = word[:pos] + word[pos + 1 :]
    else:
        word = word[:pos] + word[pos] + word[pos:]
    words[index] = word
    return " ".join(words)


def indent_block(text: str, spaces: int = 2) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())


def make_row(
    row_id: str,
    task_type: str,
    messages: list[dict],
    meta: dict,
) -> dict:
    return {
        "id": row_id,
        "task_type": task_type,
        "messages": messages,
        "meta": meta,
    }


def assert_supported_device(device: str) -> None:
    if device not in ("node", "station"):
        raise ValueError(f"unknown device type: {device}")
