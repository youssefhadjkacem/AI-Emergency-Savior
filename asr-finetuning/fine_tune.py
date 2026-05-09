import json
import torch
import librosa
from dataclasses import dataclass
from typing import Any, Dict, List, Union
from datasets import Dataset
from transformers import (
    WhisperProcessor,
    WhisperForConditionalGeneration,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer
)
from peft import LoraConfig, get_peft_model
import evaluate


# ============ CONFIGURATION ============
LOCAL_MODEL = "./whisper_model"
PSEUDO_LABELS = "pseudo_911.json"
OUTPUT_DIR = "./whisper_911_finetuned"
EPOCHS = 10
BATCH_SIZE = 1  # Reduced for 4GB GPU
LEARNING_RATE = 1e-4  # Slightly higher for LoRA
WARMUP_STEPS = 500
USE_HALF_DATA = True


device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🔥 Using device: {device.upper()}")

# Clear GPU cache
if device == "cuda":
    torch.cuda.empty_cache()


# ============ LOAD DATA ============
print("Loading dataset...")
with open(PSEUDO_LABELS) as f:
    data = json.load(f)


if not data:
    print("❌ ERROR: pseudo_911.json is EMPTY!")
    exit(1)


if USE_HALF_DATA:
    data = data[:len(data)//2]


print(f"📊 Using {len(data)} samples")


split_idx = int(len(data) * 0.9)
train_data = data[:split_idx]
val_data = data[split_idx:]


dataset = {
    "train": Dataset.from_dict({
        "audio": [item["audio"] for item in train_data],
        "text": [item["text"] for item in train_data]
    }),
    "validation": Dataset.from_dict({
        "audio": [item["audio"] for item in val_data],
        "text": [item["text"] for item in val_data]
    })
}


print(f"✅ Train: {len(dataset['train'])} | Val: {len(dataset['validation'])}")


# ============ LOAD LOCAL MODEL ============
print(f"Loading LOCAL model from {LOCAL_MODEL}...")
processor = WhisperProcessor.from_pretrained(LOCAL_MODEL, local_files_only=True)
model = WhisperForConditionalGeneration.from_pretrained(LOCAL_MODEL, local_files_only=True)
model.to(device)

# ============ APPLY LoRA ============
print("🔧 Applying LoRA (trains only 0.3% of parameters)...")
lora_config = LoraConfig(
    r=8,
    lora_alpha=32,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.1,
    bias="none"
)
model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Config
model.config.use_cache = False
model.config.forced_decoder_ids = None
model.config.suppress_tokens = []


# ============ PREPROCESSING ============
def prepare_dataset(batch):
    audio_path = batch["audio"]
    audio_array, _ = librosa.load(audio_path, sr=16000, duration=30)
    
    input_features = processor.feature_extractor(
        audio_array, sampling_rate=16000
    ).input_features[0]
    
    labels = processor.tokenizer(batch["text"]).input_ids
    
    return {"input_features": input_features, "labels": labels}


print("Preprocessing...")
dataset["train"] = dataset["train"].map(prepare_dataset, remove_columns=["audio", "text"])
dataset["validation"] = dataset["validation"].map(prepare_dataset, remove_columns=["audio", "text"])


# ============ DATA COLLATOR ============
@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: Any
    decoder_start_token_id: int


    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        input_features = [{"input_features": feature["input_features"]} for feature in features]
        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")
        
        label_features = [{"input_ids": feature["labels"]} for feature in features]
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")
        
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)
        
        if (labels[:, 0] == self.decoder_start_token_id).all().cpu().item():
            labels = labels[:, 1:]
        
        batch["labels"] = labels
        return batch


data_collator = DataCollatorSpeechSeq2SeqWithPadding(
    processor=processor,
    decoder_start_token_id=model.config.decoder_start_token_id
)


# ============ METRICS ============
metric = evaluate.load("wer")


def compute_metrics(pred):
    pred_ids = pred.predictions
    label_ids = pred.label_ids
    label_ids[label_ids == -100] = processor.tokenizer.pad_token_id
    
    pred_str = processor.batch_decode(pred_ids, skip_special_tokens=True)
    label_str = processor.batch_decode(label_ids, skip_special_tokens=True)
    
    wer = 100 * metric.compute(predictions=pred_str, references=label_str)
    return {"wer": wer}


# ============ TRAINING ARGS ============
training_args = Seq2SeqTrainingArguments(
    output_dir=OUTPUT_DIR,
    per_device_train_batch_size=BATCH_SIZE,
    per_device_eval_batch_size=BATCH_SIZE,
    gradient_accumulation_steps=16,
    gradient_checkpointing=False,  # Don't use with LoRA
    learning_rate=LEARNING_RATE,
    warmup_steps=WARMUP_STEPS,
    num_train_epochs=EPOCHS,
    fp16=False,
    eval_strategy="epoch",
    save_strategy="epoch",
    logging_steps=25,
    report_to=["tensorboard"],
    load_best_model_at_end=True,
    metric_for_best_model="wer",
    greater_is_better=False,
    predict_with_generate=True,
    generation_max_length=225,
    save_total_limit=2,
    push_to_hub=False
)


# ============ TRAINER ============
trainer = Seq2SeqTrainer(
    args=training_args,
    model=model,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
    data_collator=data_collator,
    compute_metrics=compute_metrics,
    tokenizer=processor.feature_extractor
)


# ============ TRAIN ============
print("\n🚀 Fine-tuning with LoRA on GPU...")
trainer.train()


# ============ SAVE ============
print(f"\n✅ Saving LoRA adapters to {OUTPUT_DIR}/best_model")
model.save_pretrained(f"{OUTPUT_DIR}/best_model")
processor.save_pretrained(f"{OUTPUT_DIR}/best_model")


print("\n🎉 DONE! LoRA adapters saved (only ~2MB)!")
print(f"   Model path: {OUTPUT_DIR}/best_model")
