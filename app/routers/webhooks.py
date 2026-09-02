import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import get_settings
from app.db import get_session
from app.schemas import ClerkEvent
from app.services.clerk_sync import claim_event, process_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/clerk", status_code=status.HTTP_204_NO_CONTENT)
async def clerk_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    if not settings.clerk_webhook_secret:
        logger.error("CLERK_WEBHOOK_SECRET is unset; refusing to accept webhooks")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="webhook secret not configured",
        )

    # Raw bytes, before anything parses them. This route deliberately declares no
    # Pydantic body parameter: FastAPI would parse and re-serialise the payload, and
    # the changed bytes would no longer match Svix's HMAC.
    raw = await request.body()

    try:
        # Raises on a bad signature or a timestamp outside Svix's ±5 min tolerance.
        # It returns None in svix>=2, so the payload is parsed from `raw` below.
        Webhook(settings.clerk_webhook_secret).verify(raw, dict(request.headers))
    except WebhookVerificationError:
        logger.warning("rejected clerk webhook with invalid signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid webhook signature",
        ) from None

    # Present and non-empty, or verify() above would have raised.
    svix_id = request.headers["svix-id"]

    try:
        event = ClerkEvent.model_validate_json(raw)
    except ValidationError:
        # Authentic but unparseable. Retrying cannot fix it, so acknowledge and move on.
        logger.exception("could not parse authentic clerk payload svix_id=%s", svix_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    clerk_user_id = event.data.get("id") if isinstance(event.data, dict) else None

    if not await claim_event(session, svix_id, event.type, clerk_user_id):
        logger.info("duplicate clerk webhook svix_id=%s ignored", svix_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    outcome = await process_event(session, event)
    logger.info(
        "processed clerk webhook svix_id=%s type=%s user=%s outcome=%s",
        svix_id,
        event.type,
        clerk_user_id,
        outcome,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
