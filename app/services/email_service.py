"""
Email Notification Service.

Handles all outbound email notifications for the FinanceGatekeeper application.
Sends synchronously via SMTP with structured audit logging for every attempt.

Architectural notes:
    - Configuration sourced from AppConfig singleton (no Flask dependency).
    - User lookups delegated to injected UserRepository (offline-first).
    - All print() diagnostics replaced with structured logger calls.
    - Email config validated lazily on first send (instance-level flag).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Optional, Union

from app.config import AppConfig, get_config
from app.models.service_models import ServiceResult
from app.models.transaction import Transaction
from app.repositories.user_repository import UserRepository
from app.services.base_service import BaseService
from app.utils.audit import log_audit_event


class EmailService(BaseService):
    """Service for composing and sending email notifications."""

    def __init__(
        self,
        user_repo: UserRepository,
        logger: logging.Logger,
    ) -> None:
        super().__init__(logger)
        self._user_repo = user_repo
        self._validated: bool = False

    # ------------------------------------------------------------------
    # Core send method
    # ------------------------------------------------------------------

    def send_email(
        self,
        to_addresses: Union[str, list[str]],
        subject: str,
        body_text: str,
    ) -> ServiceResult:
        """
        Compose and send an email synchronously via SMTP.

        Validates email configuration on first invocation (lazy, one-time).
        Returns a ServiceResult indicating success or failure so callers
        can react without catching exceptions.
        """
        config: AppConfig = get_config()

        # Lazy validation: check email config once per service lifetime
        if not self._validated:
            try:
                AppConfig.validate_email_config()
                self._validated = True
            except ValueError as exc:
                self._logger.error("Email configuration error: %s", exc)
                return ServiceResult(
                    success=False,
                    error=f"Email configuration error: {exc}",
                    status_code=500,
                )

        # Build the message
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.MAIL_USERNAME

        if isinstance(to_addresses, list):
            recipients_str = ", ".join(to_addresses)
        else:
            recipients_str = to_addresses
        msg["To"] = recipients_str

        msg.set_content(body_text)

        # Audit: log the send attempt
        log_audit_event(
            logger=self._logger,
            action="EMAIL_SEND_ATTEMPT",
            entity_type="Email",
            entity_id=subject,
            user_id="system",
            details={
                "to": recipients_str,
                "subject": subject,
            },
        )

        # Send via SMTP
        return self._dispatch_smtp(config, msg)

    # ------------------------------------------------------------------
    # Domain-specific notification helpers
    # ------------------------------------------------------------------

    def send_new_transaction_email(
        self,
        salesman_name: str,
        client_name: str,
        salesman_email: str,
    ) -> ServiceResult:
        """
        Notify the finance inbox and the submitting salesman that a new
        template request has been received.
        """
        config: AppConfig = get_config()
        default_recipient: str = config.MAIL_DEFAULT_RECIPIENT

        if not default_recipient or not config.MAIL_USERNAME:
            self._logger.warning(
                "MAIL_DEFAULT_RECIPIENT or MAIL_USERNAME not set. "
                "Skipping new-transaction email."
            )
            return ServiceResult(
                success=False,
                error="Email not configured: missing MAIL_DEFAULT_RECIPIENT or MAIL_USERNAME",
                status_code=500,
            )

        recipients: list[str] = [default_recipient, salesman_email]
        subject = f"Nueva Solicitud de Plantilla: {client_name}"
        body = (
            f"Se ha recibido una solicitud de plantilla de {salesman_name}, "
            f"para el cliente {client_name}."
        )

        return self.send_email(recipients, subject, body)

    def send_status_update_email(
        self,
        transaction: Transaction,
        new_status: str,
    ) -> ServiceResult:
        """
        Notify the submitting salesman that their transaction has been
        approved or rejected.
        """
        config: AppConfig = get_config()

        if not config.MAIL_USERNAME:
            self._logger.warning(
                "MAIL_USERNAME not set. Skipping status-update email."
            )
            return ServiceResult(
                success=False,
                error="Email not configured: missing MAIL_USERNAME",
                status_code=500,
            )

        # Look up the salesman's email via the repository
        sales_user = self._user_repo.get_by_username(transaction.salesman)

        if sales_user is None or not sales_user.email:
            self._logger.warning(
                "Could not find email for salesman '%s'. "
                "Skipping status-update email.",
                transaction.salesman,
            )
            return ServiceResult(
                success=False,
                error=f"No email on file for salesman '{transaction.salesman}'",
                status_code=404,
            )

        recipient_email: str = sales_user.email

        status_text = "confirmado" if new_status == "APPROVED" else "rechazado"
        subject = f"Actualización de Solicitud: {transaction.client_name}"
        body = (
            f"Se ha {status_text} la solicitud para el cliente "
            f"{transaction.client_name} (ID: {transaction.id})."
        )

        if new_status == "REJECTED" and transaction.rejection_note:
            body += f"\n\nMotivo del rechazo:\n{transaction.rejection_note}"

        return self.send_email(recipient_email, subject, body)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _dispatch_smtp(
        self,
        config: AppConfig,
        msg: EmailMessage,
    ) -> ServiceResult:
        """
        Open an SMTP connection, authenticate, send, and close.

        Returns ServiceResult so callers never need to handle raw SMTP
        exceptions.
        """
        smtp: Optional[smtplib.SMTP] = None
        try:
            smtp = smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT)
            smtp.starttls()
            smtp.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
            smtp.send_message(msg)

            self._logger.info(
                "Email sent successfully to %s", msg["To"]
            )

            log_audit_event(
                logger=self._logger,
                action="EMAIL_SENT",
                entity_type="Email",
                entity_id=msg["Subject"] or "",
                user_id="system",
                details={
                    "to": msg["To"],
                    "subject": msg["Subject"],
                },
            )

            return ServiceResult(success=True)

        except smtplib.SMTPAuthenticationError as exc:
            self._logger.error(
                "SMTP authentication failed for '%s': %s",
                config.MAIL_USERNAME,
                exc,
            )
            return ServiceResult(
                success=False,
                error=f"SMTP authentication failed: {exc}",
                status_code=500,
            )

        except smtplib.SMTPException as exc:
            self._logger.error(
                "SMTP error sending to %s: %s", msg["To"], exc
            )
            return ServiceResult(
                success=False,
                error=f"SMTP error: {exc}",
                status_code=500,
            )

        except OSError as exc:
            self._logger.error(
                "Network error connecting to %s:%d: %s",
                config.MAIL_SERVER,
                config.MAIL_PORT,
                exc,
            )
            return ServiceResult(
                success=False,
                error=f"Network error: {exc}",
                status_code=500,
            )

        finally:
            if smtp is not None:
                try:
                    smtp.quit()
                except Exception:
                    pass
