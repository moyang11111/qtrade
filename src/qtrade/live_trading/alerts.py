"""
Alert system for notifications.

Provides:
- Email alerts (SMTP)
- Webhook alerts (Slack, Discord, custom)
- Console alerts
- Alert throttling and deduplication
"""

from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import json
from loguru import logger


class AlertLevel(Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Alert data class."""
    timestamp: datetime
    level: AlertLevel
    title: str
    message: str
    details: Dict = None
    sent: bool = False

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class AlertSystem:
    """Multi-channel alert system."""

    def __init__(self, throttle_minutes: int = 5):
        """
        Args:
            throttle_minutes: Minimum minutes between duplicate alerts
        """
        self.throttle_minutes = throttle_minutes
        self.channels: List['AlertChannel'] = []
        self.alert_history: List[Alert] = []
        self.last_alert_times: Dict[str, datetime] = {}

        logger.info(f"AlertSystem initialized (throttle={throttle_minutes}min)")

    def add_channel(self, channel: 'AlertChannel'):
        """Add alert channel."""
        self.channels.append(channel)
        logger.info(f"Alert channel added: {channel.__class__.__name__}")

    def send_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        details: Optional[Dict] = None,
        force: bool = False,
    ):
        """
        Send alert through all channels.

        Args:
            level: Alert severity level
            title: Alert title
            message: Alert message
            details: Additional details
            force: Bypass throttling
        """
        # Check throttling
        if not force and self._is_throttled(title):
            logger.debug(f"Alert throttled: {title}")
            return

        # Create alert
        alert = Alert(
            timestamp=datetime.now(),
            level=level,
            title=title,
            message=message,
            details=details or {},
        )

        # Send through all channels
        for channel in self.channels:
            try:
                channel.send(alert)
                alert.sent = True
            except Exception as e:
                logger.error(f"Failed to send alert via {channel.__class__.__name__}: {e}")

        # Record alert
        self.alert_history.append(alert)
        self.last_alert_times[title] = datetime.now()

        # Log alert
        if level == AlertLevel.CRITICAL:
            logger.critical(f"🚨 ALERT: {title} - {message}")
        elif level == AlertLevel.ERROR:
            logger.error(f"❌ ALERT: {title} - {message}")
        elif level == AlertLevel.WARNING:
            logger.warning(f"⚠️ ALERT: {title} - {message}")
        else:
            logger.info(f"ℹ️ ALERT: {title} - {message}")

    def _is_throttled(self, title: str) -> bool:
        """Check if alert is throttled."""
        if title not in self.last_alert_times:
            return False

        last_time = self.last_alert_times[title]
        elapsed = datetime.now() - last_time

        return elapsed.total_seconds() < self.throttle_minutes * 60

    def get_alert_history(self, limit: int = 100) -> List[Alert]:
        """Get recent alert history."""
        return self.alert_history[-limit:]

    def clear_history(self):
        """Clear alert history."""
        self.alert_history.clear()
        self.last_alert_times.clear()


class AlertChannel:
    """Base class for alert channels."""

    def send(self, alert: Alert):
        """Send alert through channel."""
        raise NotImplementedError


class ConsoleAlert(AlertChannel):
    """Console/log alert channel."""

    def send(self, alert: Alert):
        """Log alert to console."""
        emoji_map = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }

        emoji = emoji_map.get(alert.level, "")
        timestamp = alert.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        print(f"\n{emoji} [{timestamp}] {alert.level.value.upper()}: {alert.title}")
        print(f"   {alert.message}")

        if alert.details:
            print(f"   Details: {json.dumps(alert.details, indent=2)}")


class EmailAlert(AlertChannel):
    """Email alert channel via SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        username: str,
        password: str,
        from_email: str,
        to_emails: List[str],
        use_tls: bool = True,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.to_emails = to_emails
        self.use_tls = use_tls

        logger.info(f"EmailAlert configured: {from_email} -> {to_emails}")

    def send(self, alert: Alert):
        """Send email alert."""
        # Create message
        msg = MIMEMultipart()
        msg['From'] = self.from_email
        msg['To'] = ', '.join(self.to_emails)
        msg['Subject'] = f"[{alert.level.value.upper()}] {alert.title}"

        # Create body
        body = f"""
Trading System Alert

Level: {alert.level.value.upper()}
Time: {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
Title: {alert.title}

Message:
{alert.message}
"""

        if alert.details:
            body += f"\nDetails:\n{json.dumps(alert.details, indent=2)}"

        msg.attach(MIMEText(body, 'plain'))

        # Send email
        try:
            if self.use_tls:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)

            server.login(self.username, self.password)
            server.send_message(msg)
            server.quit()

            logger.debug(f"Email alert sent: {alert.title}")

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            raise


class WebhookAlert(AlertChannel):
    """Webhook alert channel (Slack, Discord, custom)."""

    def __init__(self, webhook_url: str, platform: str = "generic"):
        """
        Args:
            webhook_url: Webhook URL
            platform: Platform type ('slack', 'discord', 'generic')
        """
        self.webhook_url = webhook_url
        self.platform = platform

        logger.info(f"WebhookAlert configured: {platform}")

    def send(self, alert: Alert):
        """Send webhook alert."""
        if self.platform == "slack":
            payload = self._format_slack(alert)
        elif self.platform == "discord":
            payload = self._format_discord(alert)
        else:
            payload = self._format_generic(alert)

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            logger.debug(f"Webhook alert sent: {alert.title}")

        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")
            raise

    def _format_slack(self, alert: Alert) -> Dict:
        """Format alert for Slack."""
        color_map = {
            AlertLevel.INFO: "#36a64f",
            AlertLevel.WARNING: "#ff9500",
            AlertLevel.ERROR: "#ff0000",
            AlertLevel.CRITICAL: "#8b0000",
        }

        return {
            "attachments": [
                {
                    "color": color_map.get(alert.level, "#36a64f"),
                    "title": alert.title,
                    "text": alert.message,
                    "fields": [
                        {"title": "Level", "value": alert.level.value.upper(), "short": True},
                        {"title": "Time", "value": alert.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "short": True},
                    ],
                    "footer": "QTrade Alert System",
                }
            ]
        }

    def _format_discord(self, alert: Alert) -> Dict:
        """Format alert for Discord."""
        color_map = {
            AlertLevel.INFO: 0x36a64f,
            AlertLevel.WARNING: 0xff9500,
            AlertLevel.ERROR: 0xff0000,
            AlertLevel.CRITICAL: 0x8b0000,
        }

        emoji_map = {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚨",
        }

        emoji = emoji_map.get(alert.level, "")

        return {
            "embeds": [
                {
                    "title": f"{emoji} {alert.title}",
                    "description": alert.message,
                    "color": color_map.get(alert.level, 0x36a64f),
                    "fields": [
                        {"name": "Level", "value": alert.level.value.upper(), "inline": True},
                        {"name": "Time", "value": alert.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "inline": True},
                    ],
                    "footer": {"text": "QTrade Alert System"},
                }
            ]
        }

    def _format_generic(self, alert: Alert) -> Dict:
        """Format alert for generic webhook."""
        return {
            "level": alert.level.value,
            "title": alert.title,
            "message": alert.message,
            "timestamp": alert.timestamp.isoformat(),
            "details": alert.details,
        }


class FileAlert(AlertChannel):
    """File-based alert logging."""

    def __init__(self, log_file: str = "alerts.log"):
        self.log_file = log_file

        logger.info(f"FileAlert configured: {log_file}")

    def send(self, alert: Alert):
        """Write alert to file."""
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{alert.timestamp.isoformat()}] ")
            f.write(f"[{alert.level.value.upper()}] ")
            f.write(f"{alert.title}: {alert.message}\n")

            if alert.details:
                f.write(f"  Details: {json.dumps(alert.details)}\n")
