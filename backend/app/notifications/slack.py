"""Deliver a short report summary to a Slack Incoming Webhook."""

import json
import re
import urllib.error
import urllib.request

from .base import DeliveryError


SLACK_WEBHOOK = re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9]{1,64}/[A-Za-z0-9]{1,64}/[A-Za-z0-9_-]{1,128}")
SLACK_CHANNEL_RECIPIENT = "workspace-channel"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def send_slack_summary(webhook: str, message: str, *, timeout: float) -> None:
    if not SLACK_WEBHOOK.fullmatch(webhook):
        raise DeliveryError("SLACK_NOT_CONFIGURED", "Настройте URL Incoming Webhook для Slack.")
    payload = json.dumps({"text": message, "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    request = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status, body, retry_after = response.status, response.read(16), ""
    except urllib.error.HTTPError as error:
        status, body, retry_after = error.code, b"", error.headers.get("Retry-After", "")
        error.close()
    except OSError:
        raise DeliveryError("SLACK_OUTCOME_UNKNOWN", "Slack не подтвердил отправку.", outcome="unknown") from None
    if status == 200 and body.strip() == b"ok":
        return
    if status == 429:
        delay = min(int(retry_after), 3600) if re.fullmatch(r"[0-9]{1,5}", retry_after) else None
        raise DeliveryError("SLACK_RATE_LIMIT", "Slack ограничил частоту отправки.", outcome="retry", retry_after=delay)
    if status >= 500 or 200 <= status < 300:
        raise DeliveryError("SLACK_OUTCOME_UNKNOWN", "Slack не подтвердил отправку.", outcome="unknown")
    raise DeliveryError("SLACK_REJECTED", "Slack отклонил сообщение. Проверьте webhook и доступ к каналу.")
