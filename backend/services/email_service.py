"""Transactional email delivery via Resend (https://resend.com), a plain
HTTPS API call — no SDK needed, reuses the httpx dependency already in the
project (FastAPI/Groq pull it in).

Dev fallback: if RESEND_API_KEY isn't set, emails are printed to the
console instead of sent. This is what makes the whole forgot-password flow
testable end to end on a laptop with no paid email account — exactly the
same "console backend" pattern real frameworks (Django, Rails) ship for
local development. Swap in a real RESEND_API_KEY (and RESEND_FROM_EMAIL, a
domain verified on your Resend account) to send real email with zero code
changes.
"""

import os

import httpx
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Voya AI <onboarding@resend.dev>")
RESEND_API_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, html_body: str) -> None:
    if not RESEND_API_KEY:
        print(f"\n[email_service] DEV MODE - no RESEND_API_KEY set, printing instead of sending:")
        print(f"  To:      {to}")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{html_body}\n")
        return

    try:
        response = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "html": html_body,
            },
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        # Don't let a flaky email provider 500 the whole forgot-password
        # request — the reset token is already stored in Redis and the user
        # can simply request another link. Log and move on rather than
        # raising.
        print(f"[email_service] send_email error: {e}")


def send_password_reset_email(to: str, name: str, reset_url: str) -> None:
    send_email(
        to=to,
        subject="Reset your Voya AI password",
        html_body=(
            f"<p>Hi {name},</p>"
            f"<p>We received a request to reset your Voya AI password. Click the link "
            f"below to choose a new one:</p>"
            f"<p><a href='{reset_url}'>{reset_url}</a></p>"
            f"<p>This link expires in 30 minutes and can only be used once. "
            f"If you didn't request this, you can safely ignore this email.</p>"
        ),
    )
