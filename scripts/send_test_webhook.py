#!/usr/bin/env python
"""Sign a Clerk-shaped webhook with the local secret and POST it.

Lets you exercise POST /webhooks/clerk without a Clerk account or a tunnel.

    uv run python scripts/send_test_webhook.py user.created
    uv run python scripts/send_test_webhook.py user.updated --clerk-id user_test_1
    uv run python scripts/send_test_webhook.py user.deleted --clerk-id user_test_1
    uv run python scripts/send_test_webhook.py user.created --bad-signature
"""

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from svix.webhooks import Webhook

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402

DEFAULT_CLERK_ID = "user_test_1"


def build_payload(event_type: str, clerk_id: str, updated_at: datetime) -> dict[str, Any]:
    if event_type == "user.deleted":
        # Clerk's delete payload really is this thin: no email, no updated_at.
        return {
            "type": "user.deleted",
            "object": "event",
            "data": {"id": clerk_id, "deleted": True, "object": "user"},
        }

    millis = int(updated_at.timestamp() * 1000)
    return {
        "type": event_type,
        "object": "event",
        "data": {
            "id": clerk_id,
            "object": "user",
            "email_addresses": [
                {
                    "id": "idn_test_1",
                    "object": "email_address",
                    "email_address": f"{clerk_id}@example.com",
                    "verification": {"status": "verified", "strategy": "email_code"},
                }
            ],
            "primary_email_address_id": "idn_test_1",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "username": None,
            "image_url": "https://img.clerk.com/placeholder.png",
            "created_at": millis,
            "updated_at": millis,
            "last_sign_in_at": millis,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "event_type",
        nargs="?",
        default="user.created",
        choices=["user.created", "user.updated", "user.deleted"],
    )
    parser.add_argument("--clerk-id", default=DEFAULT_CLERK_ID)
    parser.add_argument("--url", default="http://localhost:8000/webhooks/clerk")
    parser.add_argument(
        "--svix-id",
        default=None,
        help="Reuse a previous value to test idempotency (replays must be no-ops).",
    )
    parser.add_argument(
        "--updated-minutes-ago",
        type=float,
        default=0.0,
        help="Backdate Clerk's updated_at to test the out-of-order guard.",
    )
    parser.add_argument(
        "--bad-signature",
        action="store_true",
        help="Tamper with the body after signing; the service must answer 400.",
    )
    args = parser.parse_args()

    secret = get_settings().clerk_webhook_secret
    if not secret:
        print("CLERK_WEBHOOK_SECRET is unset (check your .env)", file=sys.stderr)
        return 2

    updated_at = datetime.now(UTC) - timedelta(minutes=args.updated_minutes_ago)
    payload = build_payload(args.event_type, args.clerk_id, updated_at)
    body = json.dumps(payload)

    msg_id = args.svix_id or f"msg_{uuid.uuid4().hex[:16]}"
    # Signed timestamp is always "now": Svix rejects anything beyond ±5 min, which is
    # unrelated to the Clerk updated_at being backdated above.
    signed_at = datetime.now(UTC)
    signature = Webhook(secret).sign(msg_id, signed_at, body)

    if args.bad_signature:
        # Signature stays valid for the original bytes; the body no longer matches.
        payload["data"]["id"] = "user_tampered"
        body = json.dumps(payload)

    headers = {
        "content-type": "application/json",
        "svix-id": msg_id,
        "svix-timestamp": str(int(signed_at.timestamp())),
        "svix-signature": signature,
    }

    response = httpx.post(args.url, content=body, headers=headers, timeout=10.0)
    print(f"{args.event_type} svix-id={msg_id} -> HTTP {response.status_code}")
    if response.content:
        print(response.text)

    return 0 if response.status_code < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
