"""
queue_manager.py
================
Thread-safe components:

  PendingMessage    – data class for one queued conversation turn
  MessageQueue      – producer/consumer queue between Flask and HumanLoop
  SessionStore      – per-user conversation history + metadata
  MessageLogger     – appends every event to chatbot.log
"""

from __future__ import annotations

import csv
import logging
import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PendingMessage:
    """Represents one inbound message waiting for human review."""
    msg_id:        str                    # unique ID (timestamp + sender)
    sender_id:     str                    # WhatsApp number e.g. whatsapp:+919876543210
    sender_name:   str                    # Profile name from Twilio (may be empty)
    user_text:     str                    # What the user sent
    ai_draft:      str                    # AI-suggested reply
    received_at:   datetime = field(default_factory=datetime.utcnow)
    # Filled in by HumanLoop after review:
    final_reply:   Optional[str] = None
    approved:      bool = False
    rejected:      bool = False
    edited:        bool = False


@dataclass
class ConversationTurn:
    role:      str   # "user" or "assistant"
    content:   str
    timestamp: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# MessageQueue
# ---------------------------------------------------------------------------

class MessageQueue:
    """
    Thread-safe wrapper around queue.Queue.

    Flask threads push PendingMessage objects.
    The HumanLoop worker pops them for review.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[PendingMessage] = queue.Queue()
        self._lock = threading.Lock()
        self._pending: Dict[str, PendingMessage] = {}   # msg_id → item

    # ── Producer side ────────────────────────────────────────────────────────

    def enqueue(self, msg: PendingMessage) -> None:
        with self._lock:
            self._pending[msg.msg_id] = msg
        self._q.put(msg)
        logger.debug("Enqueued msg_id=%s from %s", msg.msg_id, msg.sender_id)

    # ── Consumer side ────────────────────────────────────────────────────────

    def dequeue(self, timeout: float = 1.0) -> Optional[PendingMessage]:
        """Block up to *timeout* seconds; returns None on timeout."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def mark_done(self, msg_id: str) -> None:
        """Call after the HumanLoop finishes processing an item."""
        self._q.task_done()
        with self._lock:
            self._pending.pop(msg_id, None)

    # ── Introspection ────────────────────────────────────────────────────────

    def depth(self) -> int:
        return self._q.qsize()

    def snapshot(self) -> List[PendingMessage]:
        with self._lock:
            return list(self._pending.values())


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

class SessionStore:
    """
    Maintains per-user conversation history and metadata.
    All methods are thread-safe.
    """

    def __init__(self, max_history: int = 20) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, List[ConversationTurn]] = {}
        self._metadata: Dict[str, Dict] = {}
        self._max_history = max_history

    def add_turn(self, sender_id: str, role: str, content: str) -> None:
        with self._lock:
            if sender_id not in self._sessions:
                self._sessions[sender_id] = []
                self._metadata[sender_id] = {
                    "first_seen": datetime.utcnow().isoformat(),
                    "message_count": 0,
                }
            self._sessions[sender_id].append(
                ConversationTurn(role=role, content=content)
            )
            # Trim to max_history turns
            if len(self._sessions[sender_id]) > self._max_history:
                self._sessions[sender_id] = \
                    self._sessions[sender_id][-self._max_history:]
            self._metadata[sender_id]["message_count"] += 1
            self._metadata[sender_id]["last_seen"] = datetime.utcnow().isoformat()

    def get_history(self, sender_id: str) -> List[Dict[str, str]]:
        """Return history in OpenAI message format."""
        with self._lock:
            turns = self._sessions.get(sender_id, [])
            return [{"role": t.role, "content": t.content} for t in turns]

    def active_users(self) -> List[str]:
        with self._lock:
            return list(self._sessions.keys())

    def stats(self, sender_id: str) -> Dict:
        with self._lock:
            return dict(self._metadata.get(sender_id, {}))

    def all_stats(self) -> Dict[str, Dict]:
        with self._lock:
            return {uid: dict(meta) for uid, meta in self._metadata.items()}


# ---------------------------------------------------------------------------
# MessageLogger
# ---------------------------------------------------------------------------

class MessageLogger:
    """
    Appends every message event to a CSV log file.
    Thread-safe via an internal lock.
    """

    _FIELDS = ["timestamp", "event", "sender_id", "sender_name",
               "user_text", "ai_draft", "final_reply", "outcome"]

    def __init__(self, log_path: str = "chatbot.log.csv") -> None:
        self._path = log_path
        self._lock = threading.Lock()
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not os.path.exists(self._path):
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self._FIELDS).writeheader()

    def log_received(self, msg: PendingMessage) -> None:
        self._write({
            "event":       "RECEIVED",
            "sender_id":   msg.sender_id,
            "sender_name": msg.sender_name,
            "user_text":   msg.user_text,
            "ai_draft":    msg.ai_draft,
            "final_reply": "",
            "outcome":     "pending",
        })

    def log_outcome(self, msg: PendingMessage) -> None:
        outcome = "rejected" if msg.rejected else ("edited" if msg.edited else "approved")
        self._write({
            "event":       "OUTCOME",
            "sender_id":   msg.sender_id,
            "sender_name": msg.sender_name,
            "user_text":   msg.user_text,
            "ai_draft":    msg.ai_draft,
            "final_reply": msg.final_reply or "",
            "outcome":     outcome,
        })

    def _write(self, row: Dict) -> None:
        row["timestamp"] = datetime.utcnow().isoformat(timespec="seconds")
        with self._lock:
            with open(self._path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self._FIELDS)
                writer.writerow(row)
