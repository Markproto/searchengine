#!/usr/bin/env python3
"""Train a LoRA adapter on the Profoundd editorial corpus.

Runs on Tark1 (RTX 5070 Ti, 16GB VRAM, sm_120 Blackwell).

Default base model: meta-llama/Llama-3.2-3B-Instruct  (~6GB, fast first pass)
Production target:  NousResearch/Hermes-3-Llama-3.1-8B (~16GB, QLoRA)

Output:
  ~/profoundd-train/adapters/<run_name>/
    adapter_model.safetensors  (LoRA weights)
    adapter_config.json
    tokenizer.* + special tokens
    train_log.json

After training, convert to GGUF + register with Ollama via:
  scripts/lora_to_ollama.sh <run_name>  (separate script, run manually)

Usage:
  source ~/profoundd-train/bin/activate
  python3 scripts/train_profoundd_lora.py \
      --base meta-llama/Llama-3.2-3B-Instruct \
      --corpus /tmp/training_corpus.jsonl \
      --run-name profoundd-v1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
    TrainingArguments,
)
from trl import SFTTrainer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="meta-llama/Llama-3.2-3B-Instruct",
                   help="HF model id for the base. Use Hermes-3-Llama-3.1-8B for production.")
    p.add_argument("--corpus", default="/tmp/training_corpus.jsonl",
                   help="JSONL training corpus with {messages:[...]} entries.")
    p.add_argument("--run-name", default="profoundd-v1",
                   help="Output adapter dir name.")
    p.add_argument("--epochs", type=float, default=3.0)
    p.add_argument("--batch-size", type=int, default=1,
                   help="Per-device batch size. Keep at 1 for 8B QLoRA in 16GB.")
    p.add_argument("--grad-accum", type=int, default=8,
                   help="Gradient accumulation steps (effective batch = batch * accum).")
    p.add_argument("--lr", type=float, default=2e-4)
    p.add_argument("--lora-rank", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--max-seq-len", type=int, default=2048)
    p.add_argument("--qlora", action="store_true",
                   help="Enable 4-bit QLoRA. Required for 8B models in 16GB VRAM.")
    p.add_argument("--out-dir", default=str(Path.home() / "profoundd-train" / "adapters"))
    return p.parse_args()


def main():
    args = parse_args()
    out = Path(args.out_dir) / args.run_name
    out.mkdir(parents=True, exist_ok=True)
    log.info("Run output: %s", out)

    # --- 1. Tokenizer ---
    log.info("Loading tokenizer: %s", args.base)
    tokenizer = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # --- 2. Model (optionally 4-bit) ---
    log.info("Loading base model (qlora=%s)", args.qlora)
    if args.qlora:
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model = AutoModelForCausalLM.from_pretrained(
            args.base,
            quantization_config=bnb_cfg,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        model = prepare_model_for_kbit_training(model)
    else:
        model = AutoModelForCausalLM.from_pretrained(
            args.base,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )

    model.config.use_cache = False

    # --- 3. LoRA config ---
    lora_cfg = LoraConfig(
        r=args.lora_rank,
        lora_alpha=args.lora_alpha,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
    )
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    # --- 4. Dataset ---
    log.info("Loading corpus: %s", args.corpus)
    dataset = load_dataset("json", data_files=args.corpus, split="train")
    log.info("Corpus size: %d examples", len(dataset))

    # Show a sample example shape for sanity
    if len(dataset) > 0:
        ex = dataset[0]
        log.info("Example keys: %s", list(ex.keys()))
        if "messages" in ex:
            log.info("Roles in first example: %s",
                     [m.get("role") for m in ex["messages"]])

    # --- 5. Training args ---
    train_args = TrainingArguments(
        output_dir=str(out),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        bf16=True,
        optim="paged_adamw_8bit" if args.qlora else "adamw_torch",
        report_to="none",
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        dataloader_pin_memory=False,
        remove_unused_columns=False,
    )

    # --- 6. Trainer ---
    log.info("Building SFTTrainer")
    # SFTTrainer in newer trl uses processing_class instead of tokenizer
    trainer_kwargs = dict(
        model=model,
        train_dataset=dataset,
        args=train_args,
        peft_config=None,  # we already wrapped with get_peft_model
    )
    try:
        trainer = SFTTrainer(**trainer_kwargs, processing_class=tokenizer)
    except TypeError:
        trainer = SFTTrainer(**trainer_kwargs, tokenizer=tokenizer)

    # --- 7. Train ---
    log.info("Starting training...")
    result = trainer.train()
    log.info("Training complete. metrics=%s", result.metrics)

    # --- 8. Save adapter ---
    log.info("Saving adapter to %s", out)
    trainer.save_model(str(out))
    tokenizer.save_pretrained(str(out))

    # Save metadata
    meta = {
        "base_model": args.base,
        "run_name": args.run_name,
        "corpus": args.corpus,
        "corpus_size": len(dataset),
        "epochs": args.epochs,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "qlora": args.qlora,
        "train_metrics": dict(result.metrics),
    }
    with open(out / "train_log.json", "w") as f:
        json.dump(meta, f, indent=2, default=str)

    log.info("Done. Adapter saved to %s", out)


if __name__ == "__main__":
    main()
