# AI Emergency Savior

> An AI-powered system that transcribes and analyzes 911 emergency calls in real time, combining a fine-tuned speech recognition model with a full-stack web interface.

---

## Overview

**AI Emergency Savior** is a monorepo that integrates three components:

- **Frontend** — Next.js web interface for operators to view and interact with call transcriptions
- **Backend** — FastAPI service exposing the transcription and analysis API
- **ASR Fine-tuning** — Whisper model fine-tuned on real 911 recordings via LoRA (Low-Rank Adaptation)

The system uses pseudo-labeling to generate training data from unlabeled audio, then fine-tunes OpenAI's Whisper on emergency call vocabulary using parameter-efficient LoRA adapters — training only ~0.3% of the model's parameters.

---

## Monorepo Structure

```
AI-Emergency-Savior/
├── frontend/              # Next.js web app
├── backend/               # FastAPI REST API
├── asr-finetuning/        # Whisper + LoRA fine-tuning pipeline
│   ├── prep_pseudo.py     # Step 1: generate pseudo-labels from raw audio
│   ├── fine_tune.py       # Step 2: fine-tune Whisper with LoRA
│   └── pseudo_911.json    # Generated pseudo-labeled dataset
└── docs/                  # Diagrams, screenshots, demo assets
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js, React |
| Backend | FastAPI, Python |
| Speech Recognition | OpenAI Whisper (local) |
| Fine-tuning | HuggingFace Transformers, PEFT / LoRA |
| Audio Processing | librosa |
| LLM Inference | Groq API |
| Evaluation | WER (Word Error Rate) |
| Hardware | CUDA GPU (recommended, 4 GB+ VRAM) |

---

## ASR Fine-tuning Pipeline

### Step 1 — Generate Pseudo-Labels

`prep_pseudo.py` runs base Whisper on the raw 911 audio files and writes transcriptions to `pseudo_911.json`.

```bash
cd asr-finetuning
python prep_pseudo.py
```

**Output:** `pseudo_911.json` — a JSON array of `{ "audio": "<path>", "text": "<transcript>" }` objects.

### Step 2 — Fine-tune with LoRA

`fine_tune.py` loads the pseudo-labeled dataset, applies LoRA adapters to Whisper's attention layers, and trains for 10 epochs.

```bash
python fine_tune.py
```

LoRA configuration:
- Rank `r = 8`, alpha `= 32`
- Target modules: `q_proj`, `v_proj`
- Trainable parameters: ~0.3% of total
- Output: `whisper_911_finetuned/best_model/` (~2 MB adapter weights)

The best checkpoint (lowest WER on the validation split) is saved automatically.

---

## Environment Variables

Copy `.env.example` and fill in your values:

```bash
cp .env.example .env.local
```

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend API base URL (default: `http://localhost:8000`) |
| `HF_TOKEN` | HuggingFace access token |
| `GROQ_API_KEY` | Groq API key for LLM inference |

---

## Getting Started

### Prerequisites

- Node.js 18+
- Python 3.10+
- CUDA-capable GPU (recommended)

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### ASR Fine-tuning

```bash
cd asr-finetuning
pip install torch transformers peft datasets librosa evaluate
python prep_pseudo.py   # generate pseudo-labels
python fine_tune.py     # fine-tune Whisper
```

---

## Team

| Name | Role |
|---|---|
| Youssef Hadj Kacem | ASR / ML |
| Mohamed Karim Moalla | Backend / Infrastructure |

---

## License

This project is for academic and research purposes. See [LICENSE](LICENSE) for details.
