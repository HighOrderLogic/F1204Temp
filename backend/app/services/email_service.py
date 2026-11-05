from __future__ import annotations

import base64
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.constants import MEASURE_LABELS
from app.models.schemas import FilterState

TZ = ZoneInfo("Australia/Sydney")


def _parse_send_time(send_time: Any) -> time:
    if isinstance(send_time, time):
        return time(send_time.hour, send_time.minute, getattr(send_time, "second", 0) or 0)
    if isinstance(send_time, str):
        parts = send_time.strip().split(":")
        if len(parts) < 2:
            raise ValueError(f"Invalid send_time: {send_time!r}")
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(float(parts[2])) if len(parts) > 2 and parts[2] != "" else 0
        return time(hour, minute, second)
    if hasattr(send_time, "hour") and hasattr(send_time, "minute"):
        return time(int(send_time.hour), int(send_time.minute))
    raise ValueError(f"Invalid send_time: {send_time!r}")


def compute_next_send(schedule: dict, *, now: datetime | None = None) -> datetime:
    """Next fire time in Australia/Sydney for the schedule's send_time + frequency.

    Actual delivery still depends on how often `/api/email/process-due` runs.
    On Vercel Hobby the built-in cron is once daily (~10:00 Sydney / 23:00 UTC),
    so emails due earlier that morning are sent when the cron fires unless a
    more frequent external cron is configured.
    """
    now = now or datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    else:
        now = now.astimezone(TZ)

    send_time = _parse_send_time(schedule["send_time"])
    frequency = schedule["frequency"]

    if frequency == "daily":
        candidate = datetime.combine(now.date(), send_time, TZ)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if frequency == "weekly":
        dow = int(schedule.get("day_of_week") or 1)
        days_ahead = (dow - now.isoweekday()) % 7
        candidate = datetime.combine(now.date(), send_time, TZ) + timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    if frequency == "monthly":
        dom = int(schedule.get("day_of_month") or 1)
        day = min(max(dom, 1), 28)
        candidate = datetime.combine(date(now.year, now.month, day), send_time, TZ)
        if candidate <= now:
            if now.month == 12:
                candidate = datetime.combine(date(now.year + 1, 1, day), send_time, TZ)
            else:
                candidate = datetime.combine(date(now.year, now.month + 1, day), send_time, TZ)
        return candidate

    if frequency == "once":
        send_date = schedule.get("send_date")
        if isinstance(send_date, str):
            send_date = date.fromisoformat(send_date)
        if send_date is None:
            send_date = now.date()
        return datetime.combine(send_date, send_time, TZ)

    return now + timedelta(days=1)


def generate_filter_summary(filters: dict) -> dict[str, str]:
    summary = {}
    for key in ["year", "month", "day", "measure"]:
        if key in filters and filters[key]:
            summary[key] = str(filters[key])
    for key in ["sex", "treatment", "breed", "mob", "eid"]:
        if key in filters:
            summary[key] = ", ".join(filters[key]) if isinstance(filters[key], list) else str(filters[key])
    for col, vals in (filters.get("extra_filters") or {}).items():
        if vals:
            summary[col] = ", ".join(vals) if isinstance(vals, list) else str(vals)
    if "measures" in filters and isinstance(filters["measures"], list) and filters["measures"]:
        summary["measure"] = ", ".join(MEASURE_LABELS.get(m, m) for m in filters["measures"])
    elif "measure" in filters:
        summary["measure"] = MEASURE_LABELS.get(filters["measure"], filters["measure"])
    return summary


class EmailService:
    @staticmethod
    def send_report_email(
        recipient: str,
        subject: str,
        body_html: str,
        attachment_bytes: bytes,
        attachment_name: str = "report.pdf",
    ) -> tuple[bool, str]:
        """Send email. Returns (ok, message)."""
        settings = get_settings()
        recipient = (recipient or "").strip()
        if not recipient or "@" not in recipient:
            return False, "Recipient email is required"

        if settings.email_dry_run:
            return True, "dry_run"

        if settings.email_provider.lower() == "smtp":
            return EmailService._send_via_smtp(
                recipient, subject, body_html, attachment_bytes, attachment_name
            )
        return EmailService._send_via_resend(
            recipient, subject, body_html, attachment_bytes, attachment_name
        )

    @staticmethod
    def _send_via_smtp(
        recipient: str,
        subject: str,
        body_html: str,
        attachment_bytes: bytes,
        attachment_name: str,
    ) -> tuple[bool, str]:
        import smtplib
        from email import encoders
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        settings = get_settings()
        if not settings.smtp_user or not settings.smtp_password:
            return False, "SMTP_USER / SMTP_PASSWORD are not configured"

        msg = MIMEMultipart()
        msg["From"] = settings.email_from or settings.smtp_user
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html or "", "html"))

        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
        msg.attach(part)

        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=45) as server:
                if settings.smtp_use_tls:
                    server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(msg["From"], [recipient], msg.as_string())
            return True, "sent"
        except Exception as exc:
            return False, f"SMTP error: {exc}"

    @staticmethod
    def _send_via_resend(
        recipient: str,
        subject: str,
        body_html: str,
        attachment_bytes: bytes,
        attachment_name: str,
    ) -> tuple[bool, str]:
        settings = get_settings()
        if not settings.resend_api_key:
            return False, "RESEND_API_KEY is not configured"
        try:
            import resend

            resend.api_key = settings.resend_api_key
            resend.Emails.send(
                {
                    "from": settings.email_from,
                    "to": recipient,
                    "subject": subject,
                    "html": body_html or "",
                    "attachments": [
                        {
                            "filename": attachment_name,
                            "content": base64.b64encode(attachment_bytes).decode(),
                        }
                    ],
                }
            )
            return True, "sent"
        except Exception as exc:
            return False, f"Resend error: {exc}"

    @staticmethod
    def build_email_body(
        filters: dict,
        custom_body: str,
        farm_name: str,
        stats_summary: list[dict] | None = None,
    ) -> str:
        summary = generate_filter_summary(filters)
        items = "".join(f"<li><b>{k.replace('_', ' ').title()}:</b> {v}</li>" for k, v in summary.items())
        stats_table_html = ""
        if stats_summary:
            rows_html = "".join(
                f"<tr style='border-bottom: 1px solid #CBD5E1;'>"
                f"<td style='padding: 6px 8px; font-weight: bold;'>{s['display_name']}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['mean']:.2f}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['median']:.2f}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['std_dev']:.2f}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['min']:.2f}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['max']:.2f}</td>"
                f"<td style='padding: 6px 8px; text-align: center;'>{s['total_records']:,}</td>"
                f"</tr>"
                for s in stats_summary
            )
            stats_table_html = f"""
            <div style="margin: 16px 0;">
              <h3 style="color: #1B4332; margin-bottom: 8px; font-size: 14px;">Executive Key Performance Indicators</h3>
              <table style="width: 100%; max-width: 650px; border-collapse: collapse; font-size: 12px; font-family: sans-serif; border: 1px solid #CBD5E1;">
                <thead>
                  <tr style="background-color: #1B4332; color: #FFFFFF;">
                    <th style="padding: 8px; text-align: left;">Metric</th>
                    <th style="padding: 8px; text-align: center;">Mean</th>
                    <th style="padding: 8px; text-align: center;">Median</th>
                    <th style="padding: 8px; text-align: center;">Std Dev</th>
                    <th style="padding: 8px; text-align: center;">Min</th>
                    <th style="padding: 8px; text-align: center;">Max</th>
                    <th style="padding: 8px; text-align: center;">Count</th>
                  </tr>
                </thead>
                <tbody>
                  {rows_html}
                </tbody>
              </table>
            </div>
            """

        custom_p = f"<p>{custom_body}</p>" if custom_body else ""
        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #0F172A; line-height: 1.5;">
          <p>Hello,</p>
          <p>Please find your automated livestock performance report for <strong>{farm_name}</strong> attached.</p>
          {custom_p}
          {stats_table_html}
          <div style="margin-top: 14px;">
            <p style="margin-bottom: 6px; font-weight: bold; color: #1B4332;">Filters Applied:</p>
            <ul style="padding-left: 20px; font-size: 13px; color: #334155;">{items}</ul>
          </div>
          <p style="font-size: 11px; color: #64748B; margin-top: 20px; border-top: 1px solid #E2E8F0; padding-top: 8px;">
            Livestock Analytics System • Automated Report
          </p>
        </div>
        """

    @staticmethod
    def process_due_emails() -> dict[str, int]:
        from app.db import execute, fetch_all
        from app.services.report_generator import ReportGenerator
        from app.services.data_service import DataService

        now = datetime.now(TZ)
        schedules = fetch_all(
            """
            SELECT es.*, f.farm_name
            FROM email_schedules es
            JOIN farms f ON f.farm_id = es.farm_id
            WHERE es.is_active = TRUE AND es.next_send_at <= %s
            """,
            (now,),
        )
        sent = 0
        failed = 0
        for sched in schedules:
            try:
                filters_data = dict(sched.get("report_filters") or {})
                filters_data["farm_id"] = sched["farm_id"]
                if not filters_data.get("year"):
                    # Seed KF data is year 2023; fall back to that when filters omit year
                    filters_data["year"] = 2023
                if "day" in filters_data and filters_data["day"] is None:
                    filters_data["day"] = "All"
                filters = FilterState(**filters_data)
                filtered, grouped, _, _ = DataService.get_filtered_data(filters, is_admin=True)
                pdf_bytes = ReportGenerator.generate_pdf(
                    filters,
                    grouped,
                    sched.get("report_charts") or [],
                    sched["farm_name"],
                    filtered_df=filtered,
                )
                subject = sched.get("email_subject") or f"Automated Livestock Report - {sched['farm_name']}"
                measures = list(dict.fromkeys(filters.measures or [filters.measure]))
                stats_summary = ReportGenerator.compute_executive_summary_stats(filtered, measures)
                body = EmailService.build_email_body(
                    filters_data, sched.get("email_body") or "", sched["farm_name"], stats_summary=stats_summary
                )
                ok, _msg = EmailService.send_report_email(
                    sched["recipient_email"], subject, body, pdf_bytes, "report.pdf"
                )
                if ok:
                    sent += 1
                    next_at = compute_next_send(sched)
                    if sched["frequency"] == "once":
                        execute(
                            "UPDATE email_schedules SET last_sent = %s, is_active = FALSE, next_send_at = NULL WHERE id = %s",
                            (now, sched["id"]),
                        )
                    else:
                        execute(
                            "UPDATE email_schedules SET last_sent = %s, next_send_at = %s WHERE id = %s",
                            (now, next_at, sched["id"]),
                        )
                else:
                    failed += 1
            except Exception:
                failed += 1
        return {"sent": sent, "failed": failed}
