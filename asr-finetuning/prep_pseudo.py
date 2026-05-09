# -*- coding: utf-8 -*-
"""
prep_pseudo.py - Generate Pseudo-Labels (GPU Accelerated)
"""
import glob, json, librosa, os, torch
from transformers import WhisperProcessor, WhisperForConditionalGeneration
import warnings
warnings.filterwarnings('ignore')

# ✅ FORCE GPU USAGE
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"🔥 Using device: {device.upper()}")

if device == "cpu":
    print("⚠️  WARNING: No GPU detected! Falling back to CPU (will be slow)")
    print("   Check: nvidia-smi")
else:
    print(f"   GPU: {torch.cuda.get_device_name(0)}")
    print(f"   VRAM: {torch.cuda.get_device_properties(0).total_memory / 1024**3:.1f} GB")

print("\nLoading Whisper model...")
processor = WhisperProcessor.from_pretrained("./whisper_model", local_files_only=True)
model = WhisperForConditionalGeneration.from_pretrained("./whisper_model", local_files_only=True)

# ✅ MOVE MODEL TO GPU
model.to(device)
model.eval()

folder = "/home/youssef/vscode/PCD/1st part/AI-Savior-/911_recordings"
dataset = []

# Get all MP3 files
mp3_files = glob.glob(f"{folder}/*.mp3")

# ✂️ USE ONLY HALF THE FILES
mp3_files = mp3_files[:len(mp3_files)//2]

print(f"\n📊 Processing HALF dataset: {len(mp3_files)} files (~1.5GB)\n")

for i, mp3 in enumerate(mp3_files):
    print(f"[{i+1}/{len(mp3_files)}] {os.path.basename(mp3)}", end=" ", flush=True)
    try:
        # Load first 30 seconds of audio
        audio, _ = librosa.load(mp3, sr=16000, duration=30)
        
        # Transcribe with Whisper
        inputs = processor(audio, sampling_rate=16000, return_tensors="pt")
        
        # ✅ MOVE INPUT TO GPU
        input_features = inputs.input_features.to(device)
        
        with torch.no_grad():
            ids = model.generate(input_features)
        
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        
        # Save ALL transcripts
        words = len(text.split())
        dataset.append({"audio": mp3, "text": text})
        print(f"✅ {words}w")
            
    except Exception as e:
        print(f"❌ {str(e)[:50]}")

# Save to JSON
json.dump(dataset, open("pseudo_911.json", "w"), indent=2)
print(f"\n✅ SAVED: {len(dataset)} samples → pseudo_911.json")
print(f"📁 Size: {os.path.getsize('pseudo_911.json')/1024/1024:.1f} MB")
