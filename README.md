# 🛍️ Stylist Bot
 
AI shopping stylist on **WhatsApp**, with an outbound **voice** follow-up channel. A single AI agent decides how to respond and which tool to use: search the clothing catalog, search the web, run a virtual try-on, or update the user's style profile.
 
## ✨ Features
 
- **WhatsApp chat** (text + photos) via [Kapso](https://kapso.ai)
- **8-step onboarding** that builds a style profile (gender, style, sizes, budget, colors, brands) in Supabase
- **Catalog search** — semantic search over an internal clothing catalog
- **Web product search** — Perplexity Sonar + Google Shopping (SerpAPI) for items outside the catalog
- **Virtual try-on** — via OpenVTO
- **Voice follow-up (MVP)** — outbound Twilio calls, Grok for conversation, Cartesia for text-to-speech
## 🧱 Stack
 
| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn |
| Agent | Strands Agents + AWS Bedrock AgentCore, running Claude Sonnet 4 |
| WhatsApp | Kapso |
| Voice | Twilio + Grok (xAI) + Cartesia Sonic TTS + Deepgram STT |
| Catalog search | AWS Bedrock Knowledge Base (hybrid search, Cohere rerank) |
| Web search | Perplexity Sonar, SerpAPI |
| Try-on | OpenVTO + Pillow |
| Database | Supabase (Postgres) |
| Storage | AWS S3 |
 
## 🏗️ Architecture
 
```
        WhatsApp (Kapso)                Voice call (Twilio)
              │                                │
              ▼                                ▼
      app.py (FastAPI)               voice_bot/main.py (FastAPI)
      POST /webhooks/whatsapp        /voice webhook
              │                                │
              ▼                                ▼
      AI Agent (Strands + Bedrock)    Voice pipeline
      Claude Sonnet 4                 STT: Deepgram/Twilio
      text + image input              LLM: Grok
              │                       TTS: Cartesia
              ▼
      Agent tools
      • Bedrock KB (catalog)
      • Perplexity + SerpAPI (web)
      • OpenVTO (try-on)
      • Supabase (profile)
      • S3 (images)
```
 
**WhatsApp flow:** Kapso webhook → `app.py` → `kapso/handler.py` extracts text/image → agent picks a tool and replies → any `s3://` image refs get signed and sent back as photos.
 
**Voice flow:** outbound call → Twilio transcribes each turn → Grok replies with an intent (`interested`/`objection`/`close`/`exit`) → Cartesia generates audio → Twilio plays it and gathers the next turn, or hangs up.
 
## 📁 Structure
 
```
app.py          # FastAPI entrypoint (WhatsApp webhook)
agentcore/      # Agent: model, tools, memory, system prompt
kapso/          # WhatsApp webhook handling + onboarding flow
catalog/        # Catalog upload endpoints
try_on/         # Virtual try-on tests
voice_bot/      # Twilio + Grok + Cartesia voice pipeline
db/             # Supabase SQL migrations
utils/          # Shared helpers
docs/           # Architecture notes & test report
```
 
## 🚀 Setup
 
**1. Install**
```bash
git clone https://github.com/JuanMontreuil08/stylist-bot.git
cd stylist-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
 
**2. `.env`** — you'll need keys/credentials for: AWS (Bedrock, S3), Kapso, Supabase, SerpAPI, xAI, Twilio, Cartesia, Deepgram. Full list of variable names is in `agentcore/`, `kapso/`, and `voice_bot/` (search for `os.getenv`).
 
**3. Database** — run `db/migration_user_profiles.sql` in the Supabase SQL editor.
 
**4. Run WhatsApp agent**
```bash
uvicorn app:app --reload
```
Point Kapso's webhook at `<your-url>/webhooks/whatsapp` (use ngrok locally).
 
**5. Run voice bot (optional)**
```bash
uvicorn voice_bot.main:app --reload --port 8001
```
Point Twilio's voice webhook at `<your-url>/voice`.
 
## 📌 Status
 
MVP. Onboarding and web/catalog search are tested (see `docs/mvp_test_report.md`). Voice works but isn't yet callable by the agent as a tool. Per-user agent sessions and voice call state are in-memory — move to persistent storage before scaling.
