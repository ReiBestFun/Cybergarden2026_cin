"""Local SMTP sink for development: accepts every message and saves it as an .eml file.

Nothing is relayed anywhere. Point the backend at it with:

    SMTP_HOST=127.0.0.1  SMTP_PORT=1025  SMTP_SECURITY=none  SMTP_FROM=cyberflow@localhost

Run: python -m backend.app.devtools.mail_sink [--port 1025] [--dir .mail]
Addresses at the domain given by --reject-domain are refused, to try partial delivery.
"""

import argparse
import socketserver
import threading
from collections.abc import Callable
from datetime import datetime
from email import message_from_bytes, policy
from pathlib import Path

Received = Callable[[str, list[str], bytes], None]


class _Handler(socketserver.StreamRequestHandler):
    server: "SmtpSink"

    def _reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        sender, recipients = "", []
        self._reply("220 cyberflow-mail-sink ready")
        while line := self.rfile.readline(4096):
            command = line.decode("utf-8", "replace").strip()
            verb = command[:4].upper()
            if verb in {"EHLO", "HELO"}:
                self._reply("250-cyberflow-mail-sink" if verb == "EHLO" else "250 cyberflow-mail-sink")
                if verb == "EHLO":
                    self._reply("250 8BITMIME")
            elif verb == "MAIL":
                sender, recipients = command.partition(":")[2].strip().strip("<>"), []
                self._reply("250 OK")
            elif verb == "RCPT":
                address = command.partition(":")[2].strip().strip("<>")
                if self.server.reject_domain and address.lower().endswith("@" + self.server.reject_domain):
                    self._reply("550 Mailbox unavailable")
                else:
                    recipients.append(address)
                    self._reply("250 OK")
            elif verb == "DATA":
                if not recipients:
                    self._reply("554 No valid recipients")
                    continue
                self._reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while (chunk := self.rfile.readline()) not in {b".\r\n", b".\n", b""}:
                    lines.append(chunk[1:] if chunk.startswith(b"..") else chunk)
                self.server.received(sender, recipients, b"".join(lines))
                self._reply("250 OK: queued")
            elif verb in {"RSET", "NOOP"}:
                sender, recipients = ("", []) if verb == "RSET" else (sender, recipients)
                self._reply("250 OK")
            elif verb == "QUIT":
                self._reply("221 Bye")
                return
            else:
                self._reply("502 Command not implemented")


class SmtpSink(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, received: Received, *, reject_domain: str | None = None, host: str = "127.0.0.1"):
        self.received = received
        self.reject_domain = reject_domain.lower() if reject_domain else None
        super().__init__((host, port), _Handler)

    def start(self) -> "SmtpSink":
        threading.Thread(target=self.serve_forever, daemon=True).start()
        return self


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный SMTP-приёмник: сохраняет письма в файлы, никуда не отправляет.")
    parser.add_argument("--port", type=int, default=1025)
    parser.add_argument("--dir", type=Path, default=Path(".mail"))
    parser.add_argument("--reject-domain", default="invalid.test", help="Отклонять адреса этого домена (для проверки ошибок).")
    args = parser.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)

    def save(sender: str, recipients: list[str], data: bytes) -> None:
        name = args.dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}.eml"
        name.write_bytes(data)
        message = message_from_bytes(data, policy=policy.default)
        files = [part.get_filename() for part in message.iter_attachments()]
        print(f"[{datetime.now():%H:%M:%S}] {sender} -> {', '.join(recipients)} | {message['Subject']} | вложения: {files} | {name}", flush=True)

    with SmtpSink(args.port, save, reject_domain=args.reject_domain) as server:
        print(f"SMTP-приёмник слушает 127.0.0.1:{args.port}, письма сохраняются в {args.dir.resolve()}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
