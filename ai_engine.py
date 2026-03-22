"""
ai_engine.py
============
Handles AI response generation via OpenAI ChatCompletion API.
Falls back to a keyword-based placeholder when OPENAI_API_KEY is not set.

Swap the _call_openai() body for any other LLM (Anthropic, Gemini, Ollama…)
without touching the rest of the codebase.
"""

from __future__ import annotations

import logging
import os
import re
from typing import List, Dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt – customise for your use-case
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a helpful WhatsApp customer-support assistant.
Be concise (≤ 3 sentences), friendly, and professional.
If you don't know something, say so honestly rather than guessing."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_response(user_message: str, history: List[Dict[str, str]]) -> str:
    """
    Return an AI-generated reply string.

    Parameters
    ----------
    user_message : str
        The latest message from the user.
    history : list[dict]
        Recent conversation turns: [{"role": "user"|"assistant", "content": "…"}, …]
    """
    api_key = os.getenv("OPENAI_API_KEY", "")

    if api_key:
        try:
            return _call_openai(user_message, history, api_key)
        except Exception as exc:
            logger.error("OpenAI call failed, using placeholder: %s", exc)

    return _placeholder_response(user_message)


# ---------------------------------------------------------------------------
# OpenAI integration
# ---------------------------------------------------------------------------

def _call_openai(user_message: str,
                 history: List[Dict[str, str]],
                 api_key: str) -> str:
    """Call OpenAI ChatCompletion and return the reply text."""
    # Lazy import so the app runs without openai installed (placeholder mode)
    try:
        from openai import OpenAI  # openai >= 1.0
    except ImportError:
        raise RuntimeError("openai package not installed – run: pip install openai")

    client = OpenAI(api_key=api_key)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history[-10:])          # keep last 10 turns for context
    messages.append({"role": "user", "content": user_message})

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-3.5-turbo"),
        messages=messages,
        max_tokens=300,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Keyword-based placeholder (no API key required)
# ---------------------------------------------------------------------------

_RULES: List[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(hi|hello|hey|howdy)\b", re.I),
     "Hello! 👋 How can I help you today?"),

    (re.compile(r"\bpric(e|es|ing)\b|\bcost\b|\bquote\b|how much", re.I),
     "Our pricing varies by package. Could you share more details so I can "
     "give you an accurate quote?"),

    (re.compile(r"\b(order|track|shipping|delivery|shipment)\b", re.I),
     "I can help with your order! Please share your order ID and I'll look "
     "it up right away."),

    (re.compile(r"\b(problem|issue|error|broken|bug|not working)\b", re.I),
     "I'm sorry to hear that! 😔 Please describe the issue in detail and "
     "I'll do my best to resolve it."),

    (re.compile(r"\b(thank|thanks|bye|goodbye|cheers)\b", re.I),
     "Thanks for reaching out! Have a wonderful day. 🌟 Feel free to "
     "message anytime."),

    (re.compile(r"\b(hours|open|available|when)\b", re.I),
     "We're available Monday–Friday 9 AM–6 PM. Outside these hours, "
     "leave a message and we'll reply ASAP."),

    (re.compile(r"\b(refund|return|cancel|cancellation)\b", re.I),
     "I understand you'd like to discuss a refund/cancellation. "
     "Please share your order ID and reason so we can assist you."),
]

_FALLBACK = (
    "Thank you for your message! A member of our team will review it and "
    "get back to you shortly. Is there anything else I can help with?"
)


def _placeholder_response(message: str) -> str:
    """Simple keyword matcher – no API key needed."""
    for pattern, reply in _RULES:
        if pattern.search(message):
            return reply
    return _FALLBACK
