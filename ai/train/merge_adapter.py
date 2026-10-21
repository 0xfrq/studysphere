#!/usr/bin/env python3
"""Merge a LoRA adapter into its base model, ready for GGUF conversion."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dtype", choices=("bf16", "fp16", "fp32"), default="bf16")
    args = parser.parse_args()

    dtype = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": torch.float32}[args.dtype]
    print(f"loading {args.base_model} ...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=dtype,
        device_map="cpu",
        trust_remote_code=True,
    )
    print(f"loading adapter {args.adapter} ...")
    model = PeftModel.from_pretrained(model, args.adapter)
    model = model.merge_and_unload()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output, safe_serialization=True)
    AutoTokenizer.from_pretrained(args.adapter, trust_remote_code=True).save_pretrained(output)
    print(f"merged model written to {output}")


if __name__ == "__main__":
    main()
