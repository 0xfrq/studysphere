#!/usr/bin/env python3
"""LoRA / QLoRA fine-tuning for the StudySphere planner or responder.

This script deliberately uses plain Transformers + PEFT instead of a notebook so
the run is reproducible.  It masks the system/user tokens and trains only on the
assistant JSON.

Quick start (CUDA, QLoRA for a 1-2B responder):

  pip install -r ai/train/requirements.txt
  python ai/train/train_lora.py \
    --base-model Qwen/Qwen3-1.7B \
    --train-file ai/data/responder/train.jsonl \
    --val-file ai/data/responder/val.jsonl \
    --output-dir ai/out/responder \
    --load-in-4bit --epochs 3

Planner (Gemma 3 270M fits in bf16 on most 8 GB GPUs):

  python ai/train/train_lora.py \
    --base-model google/gemma-3-270m-it \
    --train-file ai/data/planner/train.jsonl \
    --val-file ai/data/planner/val.jsonl \
    --output-dir ai/out/planner \
    --epochs 4 --batch-size 8 --grad-accum 2
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    Trainer,
    TrainingArguments,
)


class CausalCollator:
    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, features):
        max_len = max(len(feature["input_ids"]) for feature in features)
        input_ids = torch.full((len(features), max_len), self.pad_token_id, dtype=torch.long)
        attention_mask = torch.zeros((len(features), max_len), dtype=torch.long)
        labels = torch.full((len(features), max_len), -100, dtype=torch.long)
        for index, feature in enumerate(features):
            length = len(feature["input_ids"])
            input_ids[index, :length] = torch.tensor(feature["input_ids"], dtype=torch.long)
            attention_mask[index, :length] = 1
            labels[index, :length] = torch.tensor(feature["labels"], dtype=torch.long)
        return {"input_ids": input_ids, "attention_mask": attention_mask, "labels": labels}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--train-file", required=True)
    parser.add_argument("--val-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-seq-length", type=int, default=1280)
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--target-modules", default="all-linear")
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--gradient-checkpointing", action="store_true")
    parser.add_argument("--chat-template-kwargs", default="{}")
    parser.add_argument("--disable-thinking", action="store_true", help="Qwen3: pass enable_thinking=False to the chat template")
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--logging-steps", type=int, default=20)
    parser.add_argument("--save-steps", type=int, default=200, help="save a checkpoint every N optimizer steps")
    parser.add_argument("--save-total-limit", type=int, default=3)
    parser.add_argument(
        "--resume-from-checkpoint",
        default="none",
        help="'auto' to resume the latest valid checkpoint, 'none' to start fresh, or an explicit checkpoint path",
    )
    return parser.parse_args()


def latest_checkpoint(output_dir: Path) -> Path | None:
    """Return the newest checkpoint that has a complete trainer state."""
    candidates = []
    for path in output_dir.glob("checkpoint-*"):
        if not path.is_dir() or not (path / "trainer_state.json").is_file():
            continue
        try:
            step = int(path.name.rsplit("-", 1)[1])
        except (IndexError, ValueError):
            continue
        candidates.append((step, path))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def resolve_checkpoint(output_dir: Path, requested: str) -> Path | None:
    if requested == "none":
        return None
    if requested == "auto":
        checkpoint = latest_checkpoint(output_dir)
        if checkpoint is None:
            print(f"resume: no complete checkpoint in {output_dir}; starting fresh")
            return None
        print(f"resume: found checkpoint {checkpoint}")
        return checkpoint
    checkpoint = Path(requested)
    if not checkpoint.is_dir():
        raise SystemExit(f"checkpoint path does not exist: {checkpoint}")
    if not (checkpoint / "trainer_state.json").is_file():
        raise SystemExit(f"checkpoint is incomplete (no trainer_state.json): {checkpoint}")
    print(f"resume: using explicit checkpoint {checkpoint}")
    return checkpoint


def main():
    args = parse_args()
    if args.load_in_4bit and not torch.cuda.is_available():
        raise SystemExit("4-bit QLoRA needs a CUDA GPU. Use a smaller model in bf16 on CPU, or run on Colab.")
    if not torch.cuda.is_available():
        print("WARNING: no CUDA GPU detected. Training will be very slow; use a Colab T4/L4 or a local GPU.")

    torch.manual_seed(args.seed)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_checkpoint = resolve_checkpoint(output_dir, args.resume_from_checkpoint)

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    # T4 (Colab free) does not support bf16, so do not hard-code bf16 for the
    # 4-bit compute dtype.  Match the model dtype to what the GPU supports.
    compute_dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    quantization_config = None
    if args.load_in_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=compute_dtype,
            bnb_4bit_use_double_quant=True,
        )

    dtype = compute_dtype
    if not torch.cuda.is_available():
        dtype = torch.float32
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        trust_remote_code=True,
        quantization_config=quantization_config,
        torch_dtype=dtype,
        device_map="auto" if torch.cuda.is_available() else None,
        attn_implementation="sdpa",
    )
    model.config.use_cache = False
    if args.load_in_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=args.gradient_checkpointing)
    if args.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})

    lora_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=args.target_modules,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    chat_template_kwargs = json.loads(args.chat_template_kwargs)
    if args.disable_thinking:
        chat_template_kwargs["enable_thinking"] = False
    raw = load_dataset(
        "json",
        data_files={"train": args.train_file, "validation": args.val_file},
    )

    def tokenize(example):
        messages = example["messages"]
        prompt_messages = messages[:-1]
        prompt_text = tokenizer.apply_chat_template(
            prompt_messages,
            tokenize=False,
            add_generation_prompt=True,
            **chat_template_kwargs,
        )
        full_text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
            **chat_template_kwargs,
        )
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
        if full_ids[: len(prompt_ids)] != prompt_ids:
            # Some templates normalize whitespace.  Fall back to the longest
            # common prefix so we never train on system/user text.
            common = 0
            for a, b in zip(prompt_ids, full_ids):
                if a != b:
                    break
                common += 1
            prompt_ids = full_ids[:common]
        labels = [-100] * len(prompt_ids) + full_ids[len(prompt_ids) :]
        input_ids = full_ids[: args.max_seq_length]
        labels = labels[: args.max_seq_length]
        return {
            "input_ids": input_ids,
            "labels": labels,
            "attention_mask": [1] * len(input_ids),
        }

    tokenized = raw.map(tokenize, remove_columns=raw["train"].column_names, desc="tokenizing")
    tokenized = tokenized.filter(lambda row: sum(label != -100 for label in row["labels"]) > 0)
    print(f"train rows: {len(tokenized['train'])}, val rows: {len(tokenized['validation'])}")
    if len(tokenized["train"]) == 0:
        raise SystemExit("No trainable rows after masking. Check the chat template and the dataset format.")

    training_kwargs = dict(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_ratio=0.03,
        lr_scheduler_type="cosine",
        logging_steps=args.logging_steps,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=args.save_total_limit,
        report_to="none",
        remove_unused_columns=False,
        dataloader_num_workers=0,
        seed=args.seed,
        bf16=dtype == torch.bfloat16,
        fp16=dtype == torch.float16,
        optim="paged_adamw_8bit" if args.load_in_4bit else "adamw_torch",
        gradient_checkpointing=args.gradient_checkpointing,
    )
    signature = inspect.signature(TrainingArguments.__init__).parameters
    eval_key = "eval_strategy" if "eval_strategy" in signature else "evaluation_strategy"
    training_kwargs[eval_key] = "epoch"
    training_args = TrainingArguments(**training_kwargs)

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["validation"],
        data_collator=CausalCollator(tokenizer.pad_token_id),
    )
    trainer.train(resume_from_checkpoint=str(resume_checkpoint) if resume_checkpoint else None)
    trainer.save_model(str(output_dir / "adapter"))
    tokenizer.save_pretrained(str(output_dir / "adapter"))
    with (output_dir / "run.json").open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "base_model": args.base_model,
                "train_file": args.train_file,
                "val_file": args.val_file,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "grad_accum": args.grad_accum,
                "lr": args.lr,
                "lora_r": args.lora_r,
                "lora_alpha": args.lora_alpha,
                "load_in_4bit": args.load_in_4bit,
                "chat_template_kwargs": chat_template_kwargs,
                "save_steps": args.save_steps,
                "save_total_limit": args.save_total_limit,
                "resume_from_checkpoint": str(resume_checkpoint) if resume_checkpoint else None,
                "train_rows": len(tokenized["train"]),
                "val_rows": len(tokenized["validation"]),
            },
            handle,
            indent=2,
        )
    print(f"saved adapter to {output_dir / 'adapter'}")


if __name__ == "__main__":
    main()
