"""
Check GitHub Service Token Expiration

Simple script to check manual expiry date and send email alerts.

Cron: 0 9 * * 1 cd /path/to/ceos-ard-server && pixi run python scripts/check_token_expiry.py

Required:
    GITHUB_SERVICE_TOKEN_EXPIRES_AT - Token expiry date (YYYY-MM-DD)

Optional (for email alerts):
    ALERT_EMAIL_TO    - Email address to send alerts to
    ALERT_EMAIL_FROM  - Email from address (default: ceos-ard-server@localhost)
    SMTP_HOST         - SMTP server hostname
    SMTP_PORT         - SMTP server port (default: 587)
    SMTP_USER         - SMTP username
    SMTP_PASSWORD     - SMTP password
    SMTP_MODE         - SMTP mode (ssl, tls, none)
"""

import argparse
import logging
import os
import smtplib
import sys
from datetime import UTC, datetime
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def check_expiry():
    """Check token expiry date and return days remaining."""
    expires_at = os.getenv("GITHUB_SERVICE_TOKEN_EXPIRES_AT")

    if not expires_at:
        logger.error("ERROR: GITHUB_SERVICE_TOKEN_EXPIRES_AT not set")
        logger.error("Add to .env: GITHUB_SERVICE_TOKEN_EXPIRES_AT=YYYY-MM-DD")
        sys.exit(1)

    try:
        expiry_date = datetime.strptime(expires_at.strip(), "%Y-%m-%d").replace(tzinfo=UTC)
        days_remaining = (expiry_date - datetime.now(UTC)).days
        return days_remaining, expires_at
    except ValueError:
        logger.error(f"ERROR: Invalid date format: {expires_at}")
        logger.error("Expected format: YYYY-MM-DD")
        sys.exit(1)


def send_email(subject, message):
    """Send email alert via SMTP."""
    email_to = os.getenv("ALERT_EMAIL_TO")
    if not email_to:
        logger.warning("No ALERT_EMAIL_TO configured - skipping email")
        return False

    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        logger.warning("No SMTP_HOST configured - skipping email")
        return False

    email_from = os.getenv("ALERT_EMAIL_FROM", "ceos-ard-server@localhost")
    smtp_port = os.getenv("SMTP_PORT")
    if smtp_port:
        smtp_port = int(smtp_port)
    port_str = f":{smtp_port}" if smtp_port else ""
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_mode = os.getenv("SMTP_MODE", "tls").lower()  # tls, ssl, none

    logger.info(f"Sending email to {email_to} via SMTP server {smtp_host}{port_str} (mode: {smtp_mode})")

    try:
        msg = MIMEText(message)
        msg["Subject"] = subject
        msg["From"] = email_from
        msg["To"] = email_to

        timeout = 10
        if smtp_mode == "ssl":
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout)
        if smtp_mode == "tls":
            server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)

        server.send_message(msg)
        server.quit()

        logger.info(f"✓ Email sent to {email_to}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Check GitHub token expiry")
    parser.add_argument("--no-email", action="store_true", help="Skip email alerts")
    parser.add_argument("--force-email", action="store_true", help="Send test email")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("GitHub Service Token Expiration Check")
    logger.info("=" * 60)

    days_remaining, expires_at = check_expiry()

    if days_remaining < 0:
        logger.error(f"⚠️  Token EXPIRED {abs(days_remaining)} days ago")
        logger.error(f"Expiry date: {expires_at}")

        if not args.no_email:
            send_email(
                "🚨 URGENT: GitHub Service Token Expired",
                f"""The GitHub service token has EXPIRED.

Expired: {abs(days_remaining)} days ago
Expiry date: {expires_at}

ACTION REQUIRED:
1. Generate new GitHub Personal Access Token
2. Update GITHUB_SERVICE_TOKEN in .env
3. Update GITHUB_SERVICE_TOKEN_EXPIRES_AT in .env

Server: {settings.SERVER_URL}
Time: {datetime.now(UTC).isoformat()}
""",
            )
        sys.exit(1)

    elif days_remaining <= 7:
        logger.warning(f"⚠️  Token expires in {days_remaining} days")
        logger.warning(f"Expiry date: {expires_at}")

        if not args.no_email:
            send_email(
                f"⚠️  GitHub Service Token Expires in {days_remaining} Days",
                f"""The GitHub service token will expire soon.

Days remaining: {days_remaining}
Expiry date: {expires_at}

ACTION REQUIRED SOON:
1. Generate new GitHub Personal Access Token
2. Update GITHUB_SERVICE_TOKEN in .env
3. Update GITHUB_SERVICE_TOKEN_EXPIRES_AT in .env

Server: {settings.SERVER_URL}
Time: {datetime.now(UTC).isoformat()}
""",
            )

    elif days_remaining <= 14:
        logger.warning(f"Token expires in {days_remaining} days")
        logger.warning(f"Expiry date: {expires_at}")

        if not args.no_email:
            send_email(
                f"📅 GitHub Service Token Expires in {days_remaining} Days",
                f"""The GitHub service token will expire in {days_remaining} days.

Expiry date: {expires_at}

REMINDER: Plan to renew the token soon.
1. Generate new GitHub Personal Access Token
2. Update GITHUB_SERVICE_TOKEN in .env
3. Update GITHUB_SERVICE_TOKEN_EXPIRES_AT in .env

Server: {settings.SERVER_URL}
Time: {datetime.now(UTC).isoformat()}
""",
            )

    elif args.force_email:
        logger.info(f"Token expires in {days_remaining} days")
        logger.info(f"Expiry date: {expires_at}")
        logger.info("Sending test email...")

        send_email(
            "✓ GitHub Service Token Status (Test)",
            f"""Test alert - token is valid.

Days remaining: {days_remaining}
Expiry date: {expires_at}

Server: {settings.SERVER_URL}
Time: {datetime.now(UTC).isoformat()}
""",
        )

    else:
        logger.info("✓ Token is healthy")
        logger.info(f"Expires in {days_remaining} days ({expires_at})")

    logger.info("=" * 60)
    logger.info("Check completed")


if __name__ == "__main__":
    main()
