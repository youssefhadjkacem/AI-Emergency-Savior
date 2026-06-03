<div align="center">

<img src="docs/images/logo.png" alt="AI Emergency Savior Logo" width="120" />

# 🚨 AI Emergency Savior

**Real-time AI-powered transcription and analysis of 911 emergency calls**

[![License: Academic](https://img.shields.io/badge/License-Academic%20%2F%20Research-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Next.js](https://img.shields.io/badge/Next.js-14-black?logo=next.js)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Whisper](https://img.shields.io/badge/OpenAI-Whisper-412991?logo=openai&logoColor=white)](https://openai.com/research/whisper)
[![HuggingFace](https://img.shields.io/badge/🤗-HuggingFace-FFD21E)](https://huggingface.co/)

[Overview](#-overview) · [Demo](#-platform-screenshots) · [Architecture](#-system-architecture) · [Getting Started](#-getting-started) · [Fine-tuning](#-asr-fine-tuning-pipeline) · [API](#-hugging-face-spaces--apis)

</div>

---

## 📌 Overview

**AI Emergency Savior** is a full-stack, production-grade system designed to assist emergency dispatch operators by transcribing and analyzing 911 calls in real time. It combines a fine-tuned speech recognition model with an intelligent web interface to reduce response time and improve decision-making in high-pressure situations.

### ✨ Key Features

- 🎙️ **Real-time transcription** of 911 audio using a fine-tuned OpenAI Whisper model
- 🧠 **AI-powered call analysis** via LLM inference (Groq API)
- 🪪 **ID card extraction** from caller-submitted documents
- 👁️ **Computer vision analysis** for scene understanding
- ⚡ **Serverless inference** via Hugging Face Spaces — no GPU required on your server
- 🔧 **Parameter-efficient fine-tuning** with LoRA — trains only ~0.3% of model parameters

---

## 🖥️ Platform Screenshots

<table>
  <tr>
    <td align="center">
      <img src="docs/images/screenshot-dashboard.png" alt="Operator Dashboard" width="100%" /><br/>
      <sub><b>Operator Dashboard</b> — Live call monitoring and transcription feed</sub>
    </td>
    <td align="center">
      <img src="docs/images/screenshot-transcription.png" alt="Transcription View" width="100%" /><br/>
      <sub><b>Transcription View</b> — Real-time speech-to-text with highlighted keywords</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="docs/images/screenshot-analysis.png" alt="Call Analysis" width="100%" /><br/>
      <sub><b>Call Analysis Panel</b> — AI-generated incident summary and priority scoring</sub>
    </td>
    <td align="center">
      <img src="docs/images/screenshot-id-extraction.png" alt="ID Extraction" width="100%" /><br/>
      <sub><b>ID Extraction Module</b> — Automated caller identity verification</sub>
    </td>
  </tr>
</table>

---

## 🏗️ System Architecture

<div align="center">
  <img src="docs/images/architecture.png" alt="System Architecture Diagram" width="85%" />
  <br/>
  <sub><i>End-to-end architecture — from audio ingestion to operator interface</i></sub>
</div>

---

## 📁 Monorepo Structure

```
AI-Emergency-Savior/
├── frontend/                  # Next.js operator web app
├── backend/                   # FastAPI REST API
├── asr-finetuning/            # Whisper + LoRA fine-tuning pipeline
│   ├── prep_pseudo.py         # Step 1: generate pseudo-labels from raw audio
│   ├── fine_tune.py           # Step 2: fine-tune Whisper with LoRA
│   └── pseudo_911.json        # Generated pseudo-labeled dataset
└── docs/
    └── images/                # Screenshots, diagrams, and demo assets
```

---

## 🤗 Hugging Face Spaces & APIs

The backend delegates specialized inference to serverless Hugging Face Spaces via Gradio API endpoints, keeping the core server lightweight and scalable.

| Variable | Space | Purpose |
|---|---|---|
| `HF_AUDIO_URL` | [emergency-savior-speech](https://youssef0081-emergency-savior-speech.hf.space) | 🎙️ Speech-to-text transcription |
| `HF_CV_URL` | [model-cv-pcd](https://azizgharbi1-model-cv-pcd.hf.space) | 👁️ Computer vision scene analysis |
| `HF_OPT_URL` | [emergency-savior-output](https://youssef0081-emergency-savior-output.hf.space) | 📝 Structured output generation |
| `HF_ID_URL` | [id-card-extractor](https://azizgharbi1-id-card-extractor.hf.space) | 🪪 ID card data extraction |

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | Next.js 14, React |
| **Backend** | FastAPI, Python 3.10+ |
| **Speech Recognition** | OpenAI Whisper (fine-tuned) |
| **Fine-tuning** | HuggingFace Transformers, PEFT / LoRA |
| **Audio Processing** | librosa |
| **LLM Inference** | Groq API |
| **Evaluation** | WER (Word Error Rate) |
| **Hardware** | CUDA GPU (recommended, 4 GB+ VRAM) |

---

## 🚀 Getting Started

### Prerequisites

- Node.js 18+
- Python 3.10+
- CUDA-capable GPU *(recommended for local inference)*

### 1 — Environment Setup

```bash
cp .env.example .env.local
```

| Variable | Description |
|---|---|
| `NEXT_PUBLIC_API_URL` | Backend base URL (default: `http://localhost:8000`) |
| `HF_TOKEN` | HuggingFace access token |
| `GROQ_API_KEY` | Groq API key for LLM inference |

### 2 — Frontend

```bash
cd frontend
npm install
npm run dev
```

> Runs on [http://localhost:3000](http://localhost:3000)

### 3 — Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

> API docs available at [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧬 ASR Fine-tuning Pipeline

The fine-tuning pipeline adapts OpenAI Whisper to emergency call vocabulary using **pseudo-labeling** and **LoRA** (Low-Rank Adaptation), making training accessible even without large annotated datasets.

### Step 1 — Generate Pseudo-Labels

Run base Whisper on raw 911 audio to bootstrap a training dataset automatically:

```bash
cd asr-finetuning
python prep_pseudo.py
```

**Output:** `pseudo_911.json` — an array of `{ "audio": "<path>", "text": "<transcript>" }` records.

### Step 2 — Fine-tune with LoRA

Apply LoRA adapters to Whisper's attention layers and train for 10 epochs:

```bash
python fine_tune.py
```

| LoRA Hyperparameter | Value |
|---|---|
| Rank `r` | 8 |
| Alpha | 32 |
| Target modules | `q_proj`, `v_proj` |
| Trainable parameters | ~0.3% of total |
| Output | `whisper_911_finetuned/best_model/` (~2 MB) |

The best checkpoint (lowest WER on the validation split) is saved automatically.

### Install Fine-tuning Dependencies

```bash
pip install torch transformers peft datasets librosa evaluate
```

---

## 📄 License

This project is intended for **academic and research purposes only**.  
Please review the [LICENSE](LICENSE) file before using or distributing any part of this codebase.

---

<div align="center">
  <sub>Built with ❤️ for faster, smarter emergency response</sub>
</div>
