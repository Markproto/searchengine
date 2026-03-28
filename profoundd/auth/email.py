"""SendGrid email integration for magic link login."""
import logging
import os

logger = logging.getLogger(__name__)


def send_magic_link(to_email, magic_url):
    """
    Send a magic login link via SendGrid.
    Returns True on success, False on failure.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        # Try DB setting
        try:
            from profoundd.utils.models import SiteSetting
            api_key = SiteSetting.get("sendgrid_api_key", "")
        except Exception:
            pass

    if not api_key:
        logger.warning("SendGrid API key not configured")
        return False

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail, Email, To, Content

        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        from_email = Email("noreply@profoundd.com", "Profoundd")
        to = To(to_email)
        subject = "Your Profoundd Login Link"
        html_content = Content("text/html", f"""
        <div style="font-family: -apple-system, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px;">
            <h2 style="color: #e2e8f0; background: #1a1a2e; padding: 24px; border-radius: 12px 12px 0 0; margin: 0; text-align: center;">
                Profoundd
            </h2>
            <div style="background: #16162a; padding: 24px; border-radius: 0 0 12px 12px; color: #cbd5e1;">
                <p>Click the button below to log in to your Profoundd account:</p>
                <p style="text-align: center; margin: 24px 0;">
                    <a href="{magic_url}"
                       style="background: #6366f1; color: white; padding: 12px 32px; border-radius: 8px;
                              text-decoration: none; font-weight: 600; display: inline-block;">
                        Log In to Profoundd
                    </a>
                </p>
                <p style="font-size: 13px; color: #64748b;">
                    This link expires in 15 minutes. If you didn't request this, you can safely ignore this email.
                </p>
            </div>
        </div>
        """)

        mail = Mail(from_email, to, subject, html_content)
        response = sg.client.mail.send.post(request_body=mail.get())
        logger.info("Magic link email sent to %s (status=%s)", to_email, response.status_code)
        return 200 <= response.status_code < 300

    except ImportError:
        logger.warning("sendgrid package not installed")
        return False
    except Exception as e:
        logger.warning("Failed to send magic link email: %s", e)
        return False
