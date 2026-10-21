#!/usr/bin/env python3
"""Evaluate a planner or responder model over an OpenAI-compatible endpoint.

Examples
--------
ollama serve
python ai/eval/evaluate.py --task planner --data ai/data/planner/test.jsonl \
  --base-url http://localhost:11434/v1 --model studysphere-planner

python ai/eval/evaluate.py --task responder --data ai/data/eval/responder_hard.jsonl \
  --base-url http://localhost:11434/v1 --model studysphere-responder
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "synth"))

from common import INFO, MOODS, TASKS, info_cost, read_jsonl  # noqa: E402

SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
MARKDOWN = re.compile(r"(\*\*|__|`|^#|^\s*[-*]\s|\]\([^)]+\))", re.MULTILINE)


def extract_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def call_model(base_url: str, model: str, messages, api_key: str | None, json_mode: bool, timeout: int):
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 320,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    latency = time.perf_counter() - started
    content = payload["choices"][0]["message"]["content"]
    return content, latency


def normalise_command(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return value
    return {key: value[key] for key in sorted(value)}


def set_metrics(predicted, gold):
    predicted, gold = set(predicted), set(gold)
    tp = len(predicted & gold)
    precision = tp / len(predicted) if predicted else (1.0 if not gold else 0.0)
    recall = tp / len(gold) if gold else (1.0 if not predicted else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1, len(predicted - gold), len(gold - predicted)


def evaluate_planner(args):
    rows = list(read_jsonl(Path(args.data)))
    if args.limit:
        rows = rows[: args.limit]
    counters = Counter()
    precisions, recalls, f1s = [], [], []
    costs_pred, costs_gold = [], []
    failures = []
    predictions = []

    for row in rows:
        messages = [m for m in row["messages"] if m["role"] != "assistant"]
        gold = json.loads(row["messages"][-1]["content"])
        gold_task = gold["task"]
        gold_info = gold["required_information"]
        counters["n"] += 1
        try:
            raw, latency = call_model(args.base_url, args.model, messages, args.api_key, args.json_mode, args.timeout)
            out = extract_json(raw)
            counters["json_ok"] += 1
            task = out.get("task")
            info = out.get("required_information", [])
            if not isinstance(info, list):
                raise ValueError("required_information is not a list")
            counters["task_ok"] += int(task == gold_task)
            if set(info) == set(gold_info):
                counters["exact_set"] += 1
            p, r, f1, extra, missing = set_metrics(info, gold_info)
            precisions.append(p)
            recalls.append(r)
            f1s.append(f1)
            predicted_set, gold_set = set(info), set(gold_info)
            counters["tp"] += len(predicted_set & gold_set)
            counters["fp"] += len(predicted_set - gold_set)
            counters["fn"] += len(gold_set - predicted_set)
            counters["extra_items"] += extra
            counters["missing_items"] += missing
            cost = info_cost([name for name in info if name in INFO])
            costs_pred.append(cost)
            costs_gold.append(info_cost(gold_info))
            if task != gold_task or set(info) != set(gold_info):
                failures.append(
                    {
                        "id": row.get("id"),
                        "request": messages[-1]["content"].partition("\nREQUEST: ")[2],
                        "gold": gold,
                        "pred": out,
                    }
                )
            predictions.append(
                {
                    "id": row.get("id"),
                    "raw": raw,
                    "pred": out,
                    "gold": gold,
                    "latency_s": latency,
                    "cost_pred": cost,
                    "cost_gold": info_cost(gold_info),
                }
            )
        except Exception as exc:  # noqa: BLE001
            counters["errors"] += 1
            failures.append({"id": row.get("id"), "error": str(exc)})
            predictions.append({"id": row.get("id"), "error": str(exc)})

    n = max(1, counters["n"])
    print(f"rows: {counters['n']}")
    print(f"JSON valid: {counters['json_ok']}/{n} ({counters['json_ok']/n:.1%})")
    print(f"task accuracy: {counters['task_ok']}/{n} ({counters['task_ok']/n:.1%})")
    print(f"exact information set: {counters['exact_set']}/{n} ({counters['exact_set']/n:.1%})")
    if f1s:
        tp, fp, fn = counters["tp"], counters["fp"], counters["fn"]
        micro_precision = tp / (tp + fp) if tp + fp else (1.0 if not fn else 0.0)
        micro_recall = tp / (tp + fn) if tp + fn else (1.0 if not fp else 0.0)
        micro_f1 = (
            2 * micro_precision * micro_recall / (micro_precision + micro_recall)
            if micro_precision + micro_recall
            else 0.0
        )
        print(f"micro precision: {micro_precision:.3f}")
        print(f"micro recall:    {micro_recall:.3f}")
        print(f"micro F1:        {micro_f1:.3f}")
        print(f"macro precision: {statistics.mean(precisions):.3f}")
        print(f"macro recall:    {statistics.mean(recalls):.3f}")
        print(f"macro F1:        {statistics.mean(f1s):.3f}")
    print(f"extra information items: {counters['extra_items']}")
    print(f"missing information items: {counters['missing_items']}")
    if costs_pred:
        print(f"sensor cost (predicted): mean {statistics.mean(costs_pred):.2f}, total {sum(costs_pred)}")
        print(f"sensor cost (gold):      mean {statistics.mean(costs_gold):.2f}, total {sum(costs_gold)}")
    if args.report:
        write_predictions(args.report, predictions)
    print_failures(failures)


def evaluate_responder(args):
    rows = list(read_jsonl(Path(args.data)))
    if args.limit:
        rows = rows[: args.limit]
    counters = Counter()
    latencies = []
    failures = []
    predictions = []
    intents = defaultdict(lambda: Counter())

    for row in rows:
        messages = [m for m in row["messages"] if m["role"] != "assistant"]
        expect = row.get("meta", {}).get("expect")
        if expect is None:
            # Synthetic test rows carry a gold assistant message.
            gold = json.loads(row["messages"][-1]["content"])
            expect = {
                "mood": gold["mood"],
                "command": gold["command"],
            }
        counters["n"] += 1
        intent = row.get("meta", {}).get("intent", "?")
        intents[intent]["n"] += 1
        try:
            raw, latency = call_model(args.base_url, args.model, messages, args.api_key, args.json_mode, args.timeout)
            latencies.append(latency)
            out = extract_json(raw)
            counters["json_ok"] += 1
            say = out.get("say", "")
            mood = out.get("mood")
            command = normalise_command(out.get("command"))
            expected_command = normalise_command(expect.get("command"))
            if mood == expect.get("mood"):
                counters["mood_ok"] += 1
                intents[intent]["mood_ok"] += 1
            if command == expected_command:
                counters["command_ok"] += 1
                intents[intent]["command_ok"] += 1
            if isinstance(say, str) and len(SENTENCE_END.findall(say)) <= 2:
                counters["brevity_ok"] += 1
            if isinstance(say, str) and not EMOJI.search(say) and not MARKDOWN.search(say):
                counters["format_ok"] += 1
            must = [s.lower() for s in expect.get("must_include", [])]
            must_not = [s.lower() for s in expect.get("must_not_include", [])]
            lowered = say.lower() if isinstance(say, str) else ""
            if all(s in lowered for s in must):
                counters["keyword_ok"] += 1
            if not any(s in lowered for s in must_not):
                counters["keyword_clean"] += 1
            if mood != expect.get("mood") or command != expected_command:
                failures.append(
                    {
                        "id": row.get("id"),
                        "user": messages[-1]["content"],
                        "expect": expect,
                        "pred": out,
                    }
                )
            predictions.append(
                {
                    "id": row.get("id"),
                    "raw": raw,
                    "pred": out,
                    "expect": expect,
                    "latency_s": latency,
                }
            )
        except Exception as exc:  # noqa: BLE001
            counters["errors"] += 1
            failures.append({"id": row.get("id"), "error": str(exc)})
            predictions.append({"id": row.get("id"), "error": str(exc)})

    n = max(1, counters["n"])
    print(f"rows: {counters['n']}")
    print(f"JSON valid: {counters['json_ok']}/{n} ({counters['json_ok']/n:.1%})")
    print(f"mood accuracy: {counters['mood_ok']}/{n} ({counters['mood_ok']/n:.1%})")
    print(f"command accuracy: {counters['command_ok']}/{n} ({counters['command_ok']/n:.1%})")
    print(f"brevity (<=2 sentences): {counters['brevity_ok']}/{n} ({counters['brevity_ok']/n:.1%})")
    print(f"no emoji/markdown: {counters['format_ok']}/{n} ({counters['format_ok']/n:.1%})")
    if any("must_include" in (row.get("meta", {}).get("expect") or {}) for row in rows):
        print(f"keyword coverage: {counters['keyword_ok']}/{n}")
        print(f"keyword clean (no forbidden): {counters['keyword_clean']}/{n}")
    if latencies:
        print(f"latency: mean {statistics.mean(latencies):.2f}s, p95 {percentile(latencies, 0.95):.2f}s")
    if args.report:
        write_predictions(args.report, predictions)
    print_failures(failures)
    print("per-intent command accuracy:")
    for intent, stats in sorted(intents.items()):
        print(f"  {intent:20s} {stats['command_ok']}/{stats['n']}")


def percentile(values, q):
    values = sorted(values)
    index = min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))
    return values[index]


def write_predictions(path, predictions):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in predictions:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"wrote predictions to {path}")


def print_failures(failures, limit=8):
    if not failures:
        return
    print(f"\nfirst {min(len(failures), limit)} failure(s):")
    for failure in failures[:limit]:
        print(json.dumps(failure, ensure_ascii=False)[:1200])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("planner", "responder"), required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--base-url", default="http://localhost:11434/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--report", default=None, help="write per-example predictions to this JSONL file")
    args = parser.parse_args()
    if args.task == "planner":
        evaluate_planner(args)
    else:
        evaluate_responder(args)


if __name__ == "__main__":
    main()
