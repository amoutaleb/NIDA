"""
NiDa — Firebase Cloud Messaging (FCM) Dispatcher

Sends push notifications to registered devices via the Firebase Admin
SDK. If Firebase credentials are not present (e.g. in CI, tests, or a
development sandbox), the dispatcher runs in DRY-RUN mode: alerts are
constructed and logged exactly as they would be sent, but no network
call is made. Dispatch state distinguishes sent(1) / failed(2) /
dry-run(3) so the evaluation section of the paper can report true
delivery statistics separately from simulated runs.
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from backend.config import settings

logger = logging.getLogger("nida.notifications.fcm")

_firebase_app = None
_dry_run = False


@dataclass
class DispatchResult:
    success: bool
    dry_run: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


def _init_firebase():
    """Lazy-initialize the Firebase Admin SDK once. Falls back to dry-run
    mode if credentials are missing or invalid."""
    global _firebase_app, _dry_run
    if _firebase_app is not None or _dry_run:
        return

    cred_path = settings.FIREBASE_CREDENTIALS_PATH
    if not cred_path or not os.path.exists(cred_path):
        logger.warning(
            f"Firebase credentials not found at '{cred_path}'. "
            f"FCM dispatcher running in DRY-RUN mode (alerts logged, not sent)."
        )
        _dry_run = True
        return

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(cred_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info(f"Firebase initialized for project {settings.FIREBASE_PROJECT_ID}")
    except Exception as exc:
        logger.error(f"Firebase initialization failed: {exc}. Falling back to DRY-RUN mode.")
        _dry_run = True


def send_push(fcm_token: str, title: str, body: str, data: Optional[dict] = None) -> DispatchResult:
    """
    Send a single push notification. Returns DispatchResult with dry_run
    flag set if Firebase credentials were unavailable.
    """
    _init_firebase()

    if _dry_run:
        logger.info(f"[DRY-RUN] Would send to {fcm_token[:20]}...: {title} | {body[:80]}")
        return DispatchResult(success=True, dry_run=True)

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=fcm_token,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    channel_id="nida_fire_alerts",
                    sound="default",
                ),
            ),
        )
        message_id = messaging.send(message)
        return DispatchResult(success=True, dry_run=False, message_id=message_id)
    except Exception as exc:
        logger.error(f"FCM send failed for token {fcm_token[:20]}...: {exc}")
        return DispatchResult(success=False, dry_run=False, error=str(exc))
