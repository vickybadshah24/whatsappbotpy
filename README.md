# WhatsApp Chatbot — Python · Flask · Twilio · OpenAI
Hi, This is vicky, hope you are well.

A production-ready WhatsApp chatbot with **human-in-the-loop approval**,
thread-safe message queuing, session management, and full CSV logging.

---

## Architecture

```
WhatsApp User
     │  (sends message)
     ▼
Twilio Cloud ──POST /webhook──► Flask (app.py)
                                     │
                              SessionStore.add_turn("user")
                                     │
                              AIEngine.generate_response()
                                     │
                              MessageQueue.enqueue(PendingMessage)
                                     │
                        ┌────────────┘
                        ▼
              HumanLoopWorker (background thread)
                        │
              ┌─────────▼──────────┐
              │   Console Review   │
              │  [1] Approve       │
              │  [2] Edit          │
              │  [3] Reject        │
              └─────────┬──────────┘
                        │  approved
                        ▼
              Twilio REST API ──► WhatsApp User
                        │
              SessionStore.add_turn("assistant")
              MessageLogger.log_outcome(CSV)
```

### File Map

| File | Responsibility |
|---|---|
| `app.py` | Flask server, `/webhook`, `/status`, `/queue` endpoints |
| `ai_engine.py` | OpenAI ChatCompletion integration + keyword placeholder |
| `queue_manager.py` | `MessageQueue`, `SessionStore`, `MessageLogger` |
| `human_loop.py` | Background worker: console review UI + Twilio sender |
| `simulate_message.py` | Local test tool — POST fake WhatsApp messages |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |
| `Dockerfile` | Container image |
| `Procfile` | Heroku / Railway / Render deployment |

---

## Quick Start (Local)

### 1 — Prerequisites

- Python 3.10+
- [ngrok](https://ngrok.com/download) (free account, one command)
- Twilio account — [sign up free](https://www.twilio.com/try-twilio)

### 2 — Install dependencies

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 3 — Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886   # sandbox number
OPENAI_API_KEY=sk-...                        # optional
PORT=5000
```

> **No API keys?** The bot still runs in simulation mode:  
> AI replies use the built-in keyword engine, sent messages are printed  
> to the console instead of going to WhatsApp.

### 4 — Run the server

```bash
python app.py
```

You'll see:
```
╔══════════════════════════════════════════════════════════╗
║   WhatsApp Chatbot  –  Flask Server                     ║
╚══════════════════════════════════════════════════════════╝
  Listening on : http://0.0.0.0:5000
  Webhook URL  : http://0.0.0.0:5000/webhook
```

### 5 — Test locally (no WhatsApp needed)

In a second terminal:

```bash
# Default test message
python simulate_message.py

# Custom message
python simulate_message.py "What are your prices?" "+919876543210" "Rahul"
```

The console shows the AI draft and prompts for approval:

```
╔══════════════════════════════════════════════════════════╗
║           HUMAN-IN-THE-LOOP  ·  REVIEW REQUIRED         ║
╚══════════════════════════════════════════════════════════╝
  From      : Rahul (whatsapp:+919876543210)
  Received  : 14:32:07 UTC
  Message   : What are your prices?
  AI Draft  : Our pricing varies by package. Could you share more details?

  [1] Approve   [2] Edit   [3] Reject
  Your choice >
```

---

## Expose via ngrok (connect to Twilio)

### 1 — Start ngrok

```bash
ngrok http 5000
```

Copy the HTTPS forwarding URL, e.g.:
```
https://a1b2-103-21-45-67.ngrok-free.app
```

### 2 — Configure Twilio Sandbox

1. Go to [Twilio Console → Messaging → Try it out → Send a WhatsApp message](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Follow the sandbox join instructions (send `join <keyword>` from your phone)
3. Under **Sandbox Settings**, set:
   - **When a message comes in**: `https://a1b2-103-21-45-67.ngrok-free.app/webhook`
   - Method: `HTTP POST`
4. Click **Save**

### 3 — Send a real WhatsApp message

Send any message from your phone to the sandbox number. Watch the console for the approval prompt, then type `1` to send the reply back.

---

## Monitoring Endpoints

| Endpoint | Description |
|---|---|
| `GET /status` | Uptime, queue depth, active sessions |
| `GET /queue` | Live snapshot of messages awaiting approval |

```bash
curl http://localhost:5000/status | python -m json.tool
```

Example response:
```json
{
  "status": "ok",
  "uptime_seconds": 142,
  "queue_depth": 1,
  "active_sessions": 3,
  "simulation_mode": false
}
```

---

## Swap in a Real LLM

Open `ai_engine.py` and replace `_call_openai()` with any provider:

```python
# Anthropic Claude
from anthropic import Anthropic
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
msg = client.messages.create(
    model="claude-3-haiku-20240307",
    max_tokens=300,
    messages=messages,
)
return msg.content[0].text

# Ollama (local, free)
import requests
r = requests.post("http://localhost:11434/api/generate", json={
    "model": "llama3", "prompt": user_message, "stream": False
})
return r.json()["response"]
```

---

## Production Deployment

### Docker

```bash
docker build -t whatsapp-bot .
docker run -p 5000:5000 --env-file .env whatsapp-bot
```

### Heroku

```bash
heroku create my-whatsapp-bot
heroku config:set TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... OPENAI_API_KEY=...
git push heroku main
```

### Railway / Render

1. Connect your GitHub repo
2. Set environment variables in the dashboard
3. Deploy — both platforms auto-detect the `Procfile`

### Production considerations

- Replace the console `HumanLoopWorker` with a web dashboard  
  (e.g. Flask-SocketIO, React admin panel)
- Add Twilio request signature validation (`VALIDATE_TWILIO_SIGNATURE=1`)
- Replace in-memory `SessionStore` with Redis or PostgreSQL for multi-process deployments
- Use Gunicorn with `--workers 4` and a process manager (systemd, supervisord)

---

## Twilio WhatsApp Business API (vs Sandbox)

| | Sandbox | Business API |
|---|---|---|
| Approval needed | No | Yes (Meta review) |
| Custom number | No | Yes |
| Outbound messaging | Replies only | Full |
| Cost | Free | Pay-per-message |
| Setup time | 5 minutes | 1–7 days |

To upgrade: apply at [Meta Business Manager](https://business.facebook.com/), then update `TWILIO_WHATSAPP_FROM` to your approved number.

---

## Logs

Every message event is appended to `chatbot.log.csv`:

```
timestamp,event,sender_id,sender_name,user_text,ai_draft,final_reply,outcome
2024-01-15T14:32:07,RECEIVED,whatsapp:+919876543210,Rahul,What are your prices?,Our pricing varies..,,pending
2024-01-15T14:32:15,OUTCOME,whatsapp:+919876543210,Rahul,What are your prices?,Our pricing varies..,Our pricing varies..,approved
```
