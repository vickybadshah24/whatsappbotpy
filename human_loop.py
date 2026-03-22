"""
human_loop.py
=============
Background worker thread that pops PendingMessages from the queue,
presents them on the console for human review, then sends the
approved reply via Twilio.

The approval UI is console-based by default.  To build a web dashboard,
replace _review_in_console() with an async web call (e.g. via Flask-SocketIO
or a simple REST poll endpoint) – the rest of the pipeline stays identical.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Optional

from twilio.rest import Client as TwilioClient

from queue_manager import MessageLogger, MessageQueue, PendingMessage, SessionStore

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# ANSI colour helpers (Windows 10+ / most terminals support these)
# ─────────────────────────────────────────────────────────────────────────────
RST  = "\033[0m"
BOLD = "\033[1m"
GRN  = "\033[32m"
YEL  = "\033[33m"
CYN  = "\033[36m"
RED  = "\033[31m"
MAG  = "\033[35m"


class HumanLoopWorker:
    """
    Spawns a daemon thread that continuously:
      1. Pops a PendingMessage from the queue
      2. Displays it on the console for human review
      3. Sends the approved/edited message via Twilio
      4. Logs the outcome
    """

    def __init__(
        self,
        message_queue: MessageQueue,
        session_store: SessionStore,
        msg_logger: MessageLogger,
        twilio_from: str,          # e.g. "whatsapp:+14155238886"
    ) -> None:
        self._queue    = message_queue
        self._sessions = session_store
        self._logger   = msg_logger
        self._from     = twilio_from
        self._stop     = threading.Event()

        # Twilio client (None = simulation mode)
        self._twilio: Optional[TwilioClient] = self._init_twilio()

        self._thread = threading.Thread(
            target=self._run, name="HumanLoopWorker", daemon=True
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._thread.start()
        logger.info("HumanLoopWorker started (thread=%s)", self._thread.name)

    def stop(self) -> None:
        self._stop.set()
        logger.info("HumanLoopWorker stopping…")

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            msg = self._queue.dequeue(timeout=1.0)
            if msg is None:
                continue
            try:
                self._process(msg)
            except Exception as exc:
                logger.exception("Error processing msg_id=%s: %s", msg.msg_id, exc)
            finally:
                self._queue.mark_done(msg.msg_id)

    def _process(self, msg: PendingMessage) -> None:
        """Full pipeline for one message: review → send → log → session update."""
        self._review_in_console(msg)

        if msg.rejected:
            logger.info("Message from %s REJECTED by human", msg.sender_id)
            self._logger.log_outcome(msg)
            return

        if msg.approved and msg.final_reply:
            sent = self._send_reply(msg)
            if sent:
                # Record bot turn in session history
                self._sessions.add_turn(msg.sender_id, "assistant", msg.final_reply)
            self._logger.log_outcome(msg)

    # ── Console review UI ─────────────────────────────────────────────────────

    def _review_in_console(self, msg: PendingMessage) -> None:
        """
        Block until the human makes a decision.
        Thread-safe: only one message is presented at a time.
        """
        print(f"\n{CYN}{BOLD}"
              f"╔══════════════════════════════════════════════════════════╗\n"
              f"║           HUMAN-IN-THE-LOOP  ·  REVIEW REQUIRED         ║\n"
              f"╚══════════════════════════════════════════════════════════╝"
              f"{RST}")
        print(f"  {YEL}From      :{RST} {msg.sender_name or 'Unknown'} "
              f"({msg.sender_id})")
        print(f"  {YEL}Received  :{RST} {msg.received_at.strftime('%H:%M:%S UTC')}")
        print(f"  {YEL}Message   :{RST} {msg.user_text}")
        print(f"  {YEL}AI Draft  :{RST} {msg.ai_draft}")
        print()
        print(f"  {GRN}[1] Approve{RST}   "
              f"{CYN}[2] Edit{RST}   "
              f"{RED}[3] Reject{RST}")

        while True:
            try:
                raw = input("  Your choice > ").strip()
            except EOFError:
                # Non-interactive mode (piped input / CI) – auto-approve
                raw = "1"

            if raw == "1":
                msg.approved    = True
                msg.final_reply = msg.ai_draft
                print(f"  {GRN}✓ Approved.{RST}")
                break

            elif raw == "2":
                try:
                    edited = input(f"  Enter edited reply: ").strip()
                except EOFError:
                    edited = msg.ai_draft

                if edited:
                    msg.approved    = True
                    msg.edited      = True
                    msg.final_reply = edited
                    print(f"  {GRN}✓ Edited & approved.{RST}")
                    break
                else:
                    print(f"  {RED}Empty input – please try again.{RST}")

            elif raw == "3":
                msg.rejected = True
                print(f"  {RED}✗ Rejected – message will not be sent.{RST}")
                break

            else:
                print(f"  {RED}Invalid choice. Enter 1, 2 or 3.{RST}")

    # ── Twilio sender ─────────────────────────────────────────────────────────

    def _send_reply(self, msg: PendingMessage) -> bool:
        """Send msg.final_reply to msg.sender_id via Twilio.  Returns True on success."""
        if self._twilio is None:
            # Simulation mode
            print(f"\n  {MAG}{BOLD}[SIMULATED SEND]{RST} "
                  f"To {msg.sender_id}: {msg.final_reply}\n")
            logger.info("SIMULATED SEND to %s: %s", msg.sender_id, msg.final_reply)
            return True

        try:
            twilio_msg = self._twilio.messages.create(
                from_=self._from,
                to=msg.sender_id,
                body=msg.final_reply,
            )
            logger.info("SENT (SID=%s) to %s", twilio_msg.sid, msg.sender_id)
            print(f"\n  {GRN}✓ Sent via Twilio (SID: {twilio_msg.sid}){RST}\n")
            return True

        except Exception as exc:
            logger.error("Twilio send failed for %s: %s", msg.sender_id, exc)
            print(f"\n  {RED}✗ Twilio send failed: {exc}{RST}\n")
            return False

    # ── Twilio client factory ─────────────────────────────────────────────────

    @staticmethod
    def _init_twilio() -> Optional[TwilioClient]:
        sid   = os.getenv("TWILIO_ACCOUNT_SID", "")
        token = os.getenv("TWILIO_AUTH_TOKEN", "")
        if sid and token:
            logger.info("Twilio client initialised (SID=%s…)", sid[:8])
            return TwilioClient(sid, token)
        logger.warning(
            "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not set – "
            "running in SIMULATION mode (messages printed, not sent)"
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Quick standalone test  (python human_loop.py)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    from queue_manager import MessageQueue, SessionStore, MessageLogger, PendingMessage
    import datetime

    mq   = MessageQueue()
    ss   = SessionStore()
    ml   = MessageLogger("test_chatbot.log.csv")
    from_ = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

    worker = HumanLoopWorker(mq, ss, ml, from_)
    worker.start()

    # Inject a synthetic message
    test_msg = PendingMessage(
        msg_id="test-001",
        sender_id="whatsapp:+919876543210",
        sender_name="Test User",
        user_text="Hi, what are your prices?",
        ai_draft="Our pricing varies by package. Could you share more details?",
    )
    ml.log_received(test_msg)
    mq.enqueue(test_msg)

    # Wait for the worker to finish processing, then exit
    time.sleep(30)
