"""
simulate_message.py
===================
Sends a fake Twilio-style POST to your local /webhook endpoint.
Use this to test the full pipeline without a real WhatsApp number.

Usage:
    python simulate_message.py
    python simulate_message.py "What are your prices?" "+919876543210" "Rahul"
"""

import sys
import requests

BASE_URL = "http://localhost:5000"

def simulate(body: str = "Hello, I need help!",
             from_number: str = "+919876543210",
             profile_name: str = "Test User") -> None:

    payload = {
        "From":        f"whatsapp:{from_number}",
        "To":          "whatsapp:+14155238886",
        "Body":        body,
        "ProfileName": profile_name,
        "NumMedia":    "0",
        "MessageSid":  "SMtest000000000000000000000000000001",
    }

    print(f"→ POST {BASE_URL}/webhook")
    print(f"  From: {payload['From']} ({profile_name})")
    print(f"  Body: {body}\n")

    try:
        r = requests.post(f"{BASE_URL}/webhook", data=payload, timeout=10)
        print(f"← Status: {r.status_code}")
        print(f"← Body  : {r.text[:200]}")
    except requests.ConnectionError:
        print("✗ Could not connect – is app.py running?")


if __name__ == "__main__":
    args = sys.argv[1:]
    simulate(
        body         = args[0] if len(args) > 0 else "Hello, I need help!",
        from_number  = args[1] if len(args) > 1 else "+919876543210",
        profile_name = args[2] if len(args) > 2 else "Test User",
    )
