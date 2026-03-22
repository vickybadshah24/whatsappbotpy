"""
app.py
======
Production-ready Flask server for the WhatsApp chatbot.

Endpoints
---------
POST /webhook          Twilio forwards every inbound WhatsApp message here.
GET  /status           JSON health-check & live stats.
GET  /queue            JSON snapshot of messages waiting for human review.

Architecture
------------
Flask (main thread + Gunicorn workers)
     │  ← POST /webhook
     ▼
SessionStore.add_turn("user", …)
     │
AIEngine.generate_response(…)
     │
MessageQueue.enqueue(PendingMessage)
     │
HumanLoopWorker (background thread)  ← console review UI
     │  approve / edit / reject
     ▼
Twilio REST API  →  WhatsApp
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime

from flask import Flask, Response, jsonify, request
from twilio.twiml.messaging_response import MessagingResponse

from ai_engine import generate_response
from human_loop import HumanLoopWorker
from queue_manager import MessageLogger, MessageQueue, PendingMessage, SessionStore

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("chatbot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared application state
# ─────────────────────────────────────────────────────────────────────────────
message_queue = MessageQueue()
session_store = SessionStore(max_history=20)
msg_logger    = MessageLogger("chatbot.log.csv")

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Start the background human-review worker
human_worker = HumanLoopWorker(
    message_queue=message_queue,
    session_store=session_store,
    msg_logger=msg_logger,
    twilio_from=TWILIO_FROM,
)
human_worker.start()

# ─────────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
SERVER_START = datetime.utcnow()


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook() -> Response:
    """
    Twilio sends a POST here for every inbound WhatsApp message.

    Important: Twilio expects a TwiML response within ~15 s.
    We return an *empty* TwiML immediately and process the message
    asynchronously via the queue + HumanLoopWorker so we never time out.
    """
    # ── Parse Twilio POST fields ──────────────────────────────────────────────
    sender_id   = request.form.get("From", "")        # whatsapp:+91XXXXXXXXXX
    sender_name = request.form.get("ProfileName", "")
    user_text   = request.form.get("Body", "").strip()
    num_media   = int(request.form.get("NumMedia", 0))

    if not sender_id or not user_text:
        logger.warning("Received empty/invalid webhook payload")
        return _empty_twiml()

    logger.info("INBOUND from %s (%s): %s", sender_id, sender_name, user_text)

    # ── Handle media (images, docs) – extend here as needed ──────────────────
    if num_media > 0:
        media_note = f"[{num_media} attachment(s)] "
        user_text  = media_note + user_text if user_text else media_note.strip()

    # ── Record user turn in session history ───────────────────────────────────
    session_store.add_turn(sender_id, "user", user_text)

    # ── Generate AI draft ─────────────────────────────────────────────────────
    history   = session_store.get_history(sender_id)
    ai_draft  = generate_response(user_text, history[:-1])  # exclude current turn

    # ── Build PendingMessage and push to queue ────────────────────────────────
    msg = PendingMessage(
        msg_id      = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}",
        sender_id   = sender_id,
        sender_name = sender_name,
        user_text   = user_text,
        ai_draft    = ai_draft,
    )
    msg_logger.log_received(msg)
    message_queue.enqueue(msg)

    # ── Return empty TwiML (actual reply sent by HumanLoopWorker) ────────────
    return _empty_twiml()


# ── Health & monitoring endpoints ────────────────────────────────────────────

@app.route("/status", methods=["GET"])
def status() -> Response:
    """Live health-check with session and queue stats."""
    uptime_s = int((datetime.utcnow() - SERVER_START).total_seconds())
    return jsonify({
        "status":          "ok",
        "uptime_seconds":  uptime_s,
        "queue_depth":     message_queue.depth(),
        "active_sessions": len(session_store.active_users()),
        "sessions":        session_store.all_stats(),
        "twilio_from":     TWILIO_FROM,
        "simulation_mode": os.getenv("TWILIO_ACCOUNT_SID", "") == "",
    })


@app.route("/queue", methods=["GET"])
def queue_snapshot() -> Response:
    """JSON view of messages currently waiting for human approval."""
    items = [
        {
            "msg_id":      m.msg_id,
            "sender_id":   m.sender_id,
            "sender_name": m.sender_name,
            "user_text":   m.user_text,
            "ai_draft":    m.ai_draft,
            "received_at": m.received_at.isoformat(),
        }
        for m in message_queue.snapshot()
    ]
    return jsonify({"pending": items, "count": len(items)})


@app.route("/", methods=["GET"])
def index() -> Response:
    return jsonify({
        "service": "WhatsApp Chatbot",
        "endpoints": {
            "POST /webhook": "Twilio inbound webhook",
            "GET  /status":  "Health check & stats",
            "GET  /queue":   "Pending approval queue",
        },
    })


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _empty_twiml() -> Response:
    """Return an empty TwiML response – actual reply sent asynchronously."""
    resp = MessagingResponse()
    return Response(str(resp), mimetype="application/xml")


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"

    print(
        "\n\033[32m\033[1m"
        "╔══════════════════════════════════════════════════════════╗\n"
        "║   WhatsApp Chatbot  –  Flask Server                     ║\n"
        "║   Human-in-the-loop · Twilio · OpenAI                   ║\n"
        "╚══════════════════════════════════════════════════════════╝"
        "\033[0m"
    )
    print(f"  Listening on : http://0.0.0.0:{port}")
    print(f"  Webhook URL  : http://0.0.0.0:{port}/webhook")
    print(f"  Status URL   : http://0.0.0.0:{port}/status")
    print(f"  Simulation   : {'YES (no Twilio keys)' if not os.getenv('TWILIO_ACCOUNT_SID') else 'NO (Twilio active)'}")
    print()

    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)
