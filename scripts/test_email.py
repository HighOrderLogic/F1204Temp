#!/usr/bin/env python3
"""Send a test email to verify SMTP settings in backend/.env"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings
from app.services.email_service import EmailService


def main():
    settings = get_settings()
    recipient = sys.argv[1] if len(sys.argv) > 1 else settings.smtp_user

    if not recipient:
        print("Usage: python scripts/test_email.py recipient@example.com")
        sys.exit(1)

    print(f"Provider: {settings.email_provider}")
    print(f"From: {settings.email_from or settings.smtp_user}")
    print(f"To: {recipient}")
    print(f"Dry run: {settings.email_dry_run}")

    if settings.email_dry_run:
        print("\nEMAIL_DRY_RUN=true — no email will actually be sent.")
        print("Set EMAIL_DRY_RUN=false in backend/.env to send for real.")
        sys.exit(0)

    ok, status = EmailService.send_report_email(
        recipient=recipient,
        subject="Livestock Dashboard — Test Email",
        body_html="<p>If you received this, SMTP is configured correctly.</p>",
        attachment_bytes=b"%PDF-1.4 test",
        attachment_name="test.pdf",
    )
    if ok:
        print(f"\nEmail sent successfully ({status}). Check the inbox (and spam folder).")
    else:
        print(f"\nFailed to send: {status}")
        print("Check SMTP_USER, SMTP_PASSWORD, and EMAIL_FROM in backend/.env")
        sys.exit(1)


if __name__ == "__main__":
    main()
