"""
Send alert emails via SendGrid (same provider used for magic links).
"""
import logging
import os
from html import escape

logger = logging.getLogger(__name__)


def _get_sendgrid_key():
    key = os.environ.get("SENDGRID_API_KEY", "")
    if not key:
        try:
            from profoundd.utils.models import SiteSetting
            key = SiteSetting.get("sendgrid_api_key", "")
        except Exception:
            pass
    return key


def send_alert_email(to_email, alert, articles, domain="profoundd.com"):
    """Send an alert email with the new articles matching a saved search.

    alert: SavedAlert instance (has query, category, unsub_token)
    articles: list of article dicts from search engine
    Returns True if sent successfully.
    """
    api_key = _get_sendgrid_key()
    if not api_key:
        logger.warning("SendGrid key not configured — cannot send alert")
        return False

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content
    except ImportError:
        logger.warning("sendgrid package not installed")
        return False

    try:
        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        from_email = Email(f"alerts@{domain}", "Profoundd Alerts")
        to = To(to_email)
        subject = f"{len(articles)} new result{'s' if len(articles) != 1 else ''} for \"{alert.query}\""

        # Build article list
        article_html = ""
        for a in articles:
            title = escape(a.get("title", "Untitled"))
            url = escape(a.get("url", "#"))
            source = escape(a.get("source_name", ""))
            summary = escape((a.get("summary") or "")[:200])
            pub = escape((a.get("published_at") or "")[:10])
            article_html += f"""
            <div style="margin-bottom:20px;padding-bottom:20px;border-bottom:1px solid #2a2a3e;">
                <h3 style="margin:0 0 6px 0;font-size:15px;">
                    <a href="{url}" style="color:#c7d2fe;text-decoration:none;">{title}</a>
                </h3>
                <div style="font-size:12px;color:#64748b;margin-bottom:8px;">
                    {source}{f" &middot; {pub}" if pub else ""}
                </div>
                <p style="margin:0;font-size:13px;color:#94a3b8;line-height:1.5;">{summary}{'&hellip;' if len(summary) == 200 else ''}</p>
            </div>
            """

        # Unsubscribe URL uses the alert's token (no login required)
        unsub_url = f"https://{domain}/alerts/unsubscribe/{alert.unsub_token}" if alert.unsub_token else f"https://{domain}/auth/settings"
        search_url = f"https://{domain}/search?q={escape(alert.query).replace(' ', '+')}"

        html = f"""
        <div style="font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:32px;background:#0f0f1e;color:#cbd5e1;">
            <h2 style="color:#e2e8f0;margin:0 0 8px 0;font-size:18px;">Profoundd Alert</h2>
            <p style="color:#94a3b8;margin:0 0 24px 0;font-size:14px;">
                New results for <strong style="color:#c7d2fe;">"{escape(alert.query)}"</strong>
                {f' in <em>{escape(alert.category)}</em>' if alert.category and alert.category != 'all' else ''}
            </p>
            <div style="background:#16162a;border-radius:12px;padding:24px;">
                {article_html}
                <div style="text-align:center;margin-top:12px;">
                    <a href="{search_url}" style="background:#6366f1;color:white;padding:10px 24px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;display:inline-block;">
                        See all results on Profoundd &rarr;
                    </a>
                </div>
            </div>
            <p style="font-size:12px;color:#64748b;margin-top:24px;text-align:center;">
                You're receiving this because you saved an alert on <a href="https://{domain}" style="color:#818cf8;">profoundd.com</a>.<br>
                <a href="{unsub_url}" style="color:#64748b;">Unsubscribe from this alert</a>
                &nbsp;&middot;&nbsp;
                <a href="https://{domain}/auth/settings" style="color:#64748b;">Manage all alerts</a>
            </p>
        </div>
        """

        mail = Mail(from_email, to, subject, Content("text/html", html))
        response = sg.client.mail.send.post(request_body=mail.get())
        ok = 200 <= response.status_code < 300
        if not ok:
            logger.warning("Alert email returned %s", response.status_code)
        return ok
    except Exception as e:
        logger.warning("Alert email send failed: %s", e)
        return False
