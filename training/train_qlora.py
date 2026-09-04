from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an 8B-class QLoRA news classifier")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rank", type=int, choices=(16, 32), default=16)
    parser.add_argument("--epochs", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig
        from transformers import BitsAndBytesConfig
        from trl import SFTConfig, SFTTrainer
    except ImportError as exc:
        raise RuntimeError("install the training dependency group on the RTX 3090 host") from exc
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    peft = LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM"
    )
    config = SFTConfig(
        output_dir=args.output,
        learning_rate=1e-4,
        num_train_epochs=args.epochs,
        max_length=2048,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=16,
        assistant_only_loss=True,
        bf16=True,
        model_init_kwargs={"device_map": "auto", "quantization_config": quantization},
    )
    dataset = load_dataset(
        "json",
        data_files={
            "train": f"{args.dataset}/train.jsonl",
            "validation": f"{args.dataset}/validation.jsonl",
        },
    )
    trainer = SFTTrainer(
        model=args.model,
        args=config,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        peft_config=peft,
    )
    trainer.train()
    trainer.save_model(args.output)


if __name__ == "__main__":
    main()
