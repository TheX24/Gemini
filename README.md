# @Gemini Discord Self-Bot

A powerful, stateful, and lightning-fast personal assistant for Discord. Powered by local **Ollama** (`qwen3:8b`) and built with a modular, tool-intelligent architecture.

> [!CAUTION]  
> **Self-Bot Warning:** Automating user accounts is strictly against Discord's Terms of Service. Using this tool carries a risk of permanent account suspension. Use at your own discretion.

---

## ✨ Features

### 🧠 Dual-Model Routing
The assistant uses a lightweight **Router Model** (`qwen2.5:1.5b`) to instantly classify your requests into three high-performance paths:
- **Fast Chat**: Direct, low-latency conversational responses.
- **Thinking Mode**: Deep reasoning for debugging, logic, or complex analysis.
- **Action Mode**: Intelligent tool execution for real-world tasks.

### 🧰 Integrated Productivity Suite
- **⛅ Live Weather**: Instant reports for any city using `wttr.in`.
- **🔢 Safe Calculator**: Perform complex math and expressions without the LLM hallucinating.
- **🌍 Professional Translator**: High-fidelity translation to any language with auto-detection.
- **⏰ Smart Reminders**: Set precise timers and alerts across all your devices.
- **🔍 Deep Search**: Real-time web-browsing capabilities powered by your local **SearXNG** instance.
- **🧠 Persistent Memory**: A private "brain" database that remembers facts about you for future context.

### 🕒 Real-Time Context
The assistant now has a **dedicated time-perception layer**. It always knows:
- Current Day and Date.
- Absolute UTC Time.
- Your personal "user notes" for every response.

---

## 🎮 Rich Presence
The bot features a custom **Discord Rich Presence** (RPC) with the official **Google Gemini** icon, showing your status to friends while keeping the assistant running invisibly on your account.

---

## 📊 Monitoring & Stats
Track your usage in real-time with the built-in dashboard:
- Use `@Gemini --stats` to see total messages, tokens consumed, and tools executed.

---

## 🚀 Quick Setup

1. **Pull the Models**:
   ```bash
   ollama pull qwen3:8b
   ollama pull qwen2.5:1.5b
   ```
2. **Environment Configuration** (`.env`):
   ```env
   DISCORD_TOKEN=your_token
   OLLAMA_MODEL=qwen3:8b
   OLLAMA_ROUTER_MODEL=qwen2.5:1.5b
   SEARXNG_BASE_URL=http://localhost:8888
   ```
3. **Run the Bot**:
   ```bash
   python main.py
   ```

## 🛠️ Manual Overrides
Force a specific logic path by prefixing or suffixing your message:
- `--fast`: Conversations.
- `--think`: Reasoning.
- `--search`: Web browsing.
- `--stats`: Performance dashboard.

---

## 📄 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
