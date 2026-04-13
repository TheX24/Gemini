# @Gemini Discord Self-Bot

A powerful, stateful, and lightning-fast personal assistant for Discord. Powered by local **Ollama** (`qwen3:8b`) and built with a modular, tool-intelligent architecture.

> [!CAUTION]
> **Self-Bot Warning:** Automating user accounts is strictly against Discord's Terms of Service. Using this tool carries a risk of permanent account suspension. Use at your own discretion.

---

## ✨ Features

### 🧠 Agentic Loop
The assistant uses a **self-directed ReAct loop** — on each message it decides its own next step:
- **Respond directly** for simple conversational queries.
- **Engage Thinking Mode** (`[MODE: think]`) for deep reasoning, debugging, or complex analysis.
- **Call a tool** (`[ACTION: tool(args)]`) when it needs external data, and loop back with the result.

No separate routing model needed — intent is determined dynamically by the model itself.

### 🧰 Integrated Productivity Suite
- **⛅ Live Weather**: Instant reports for any city via `wttr.in`.
- **🔢 Safe Calculator**: Accurate math without LLM hallucination.
- **⏰ Smart Reminders**: Precise timers and alerts.
- **🔍 Deep Search**: Real-time web search via your local **SearXNG** instance.
- **🧠 Persistent Memory**: Private database that remembers facts about you across sessions.
- **📊 Stats Dashboard**: Token usage, searches, and tool executions.

### 📎 File Attachment Support
Attach and analyse plain-text files directly in Discord. Supported formats:

`.txt` `.log` `.md` `.rst` `.csv` `.tsv` `.json` `.yaml` `.yml` `.toml` `.ini` `.cfg` `.conf` `.env` `.xml` `.html` `.htm` `.css` `.diff` `.patch` `.ttml`

> [!NOTE]
> Executables, scripts, binaries, and media files are blocked for security. Up to **5 files × 200 KB** per message.

### 🖼️ Image Understanding
Send images and get intelligent analysis powered by a vision model or OCR fallback.

**Option A — Vision Model (recommended):** Uses a local Ollama vision model (e.g. `moondream`, `llava`, `qwen2-vl`) to fully describe images, transcribe text, and interpret screenshots.

**Option B — pytesseract OCR:** Text extraction only, no extra model needed. Good for screenshots with dense text.

Supports: `.png` `.jpg` `.jpeg` `.webp` `.gif` — up to **3 images × 10 MB** per message.

### 🕒 Real-Time Context
- Current day, date, and UTC time injected into every response.
- Channel history summarisation for long conversations.
- User profile awareness (bio, pronouns, roles, rich presence / RPC).

---

## 🎮 Rich Presence
Custom **Discord Rich Presence** with the official Google Gemini icon, showing live stats (messages answered, tokens used) while keeping the assistant running on your account.

---

## 🚀 Quick Setup

### 1. Pull the Models
```bash
ollama pull qwen3:8b

# Optional: vision model for image understanding
ollama pull moondream
```

### 2. Install Python Dependencies
```bash
pip install -r requirements.txt

# Optional: OCR fallback (if not using a vision model)
pip install pytesseract pillow
sudo pacman -S tesseract tesseract-data-eng   # Arch / CachyOS
# sudo apt install tesseract-ocr              # Debian / Ubuntu
```

### 3. Configure `.env`
```env
# --- Required ---
DISCORD_TOKEN=your_token_here
OLLAMA_MODEL=qwen3:8b
SEARXNG_BASE_URL=http://localhost:8888

# --- Context Window ---
# Larger = more memory, better recall. Tune to your VRAM.
# Approx VRAM cost on qwen3:8b (Q4): 8k≈7.8GB, 16k≈8.9GB, 32k≈11GB
OLLAMA_NUM_CTX=32768

# --- Image Support (optional) ---
# Set to a vision-capable model name, or leave empty to use pytesseract OCR fallback
OLLAMA_VISION_MODEL=moondream

# GPU layers for the vision model:
#   0  = fully on CPU/RAM — recommended, keeps VRAM free for main model
#  -1  = fully on GPU — fastest, but evicts main model from VRAM
VISION_NUM_GPU=0
```

### 4. Run
```bash
python main.py
```

---

## 🛠️ Manual Overrides
Prefix or suffix your message to force a specific path:

| Flag | Behaviour |
|---|---|
| `--think` | Activates extended reasoning mode |
| `--search` | Forces a web search |
| `--stats` | Shows the performance dashboard |

---

## 📐 VRAM Tuning Guide

| `OLLAMA_NUM_CTX` | VRAM (qwen3:8b Q4) |
|---|---|
| 8 192 | ~7.8 GB |
| 16 384 | ~8.9 GB |
| 32 768 | ~11.0 GB |

**Vision model co-existence:** With `VISION_NUM_GPU=0`, the vision model runs entirely in system RAM — no VRAM is consumed and the main model is never evicted. Any system with 8+ GB RAM can run `moondream` this way alongside a 32k-context qwen3:8b.

---

## 📄 License
This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
