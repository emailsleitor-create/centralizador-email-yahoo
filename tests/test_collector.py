import os
import unittest
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser

os.environ.setdefault("STATE_PATH", "/tmp/coletor-yahoo-test-state.json")

from collector import (
    MessageUnavailableError,
    BackfillConfig,
    YahooAccount,
    build_forward,
    extract_body,
    fetch_message,
    safe_filename,
    search_unread_history,
)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.account = YahooAccount(
            email="origem@yahoo.com",
            app_password="senha-de-teste",
            label="Projetos",
        )

    def parse(self, value: bytes):
        return BytesParser(policy=policy.default).parsebytes(value)

    def test_build_forward_preserves_metadata_and_body(self):
        original = self.parse(
            b"From: Pessoa <pessoa@example.com>\r\n"
            b"To: origem@yahoo.com\r\n"
            b"Subject: Prazo importante\r\n"
            b"Date: Wed, 22 Jul 2026 10:00:00 -0300\r\n"
            b"Message-ID: <abc@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n"
            b"\r\n"
            b"Providenciar documentos ate sexta-feira.\r\n"
        )
        forwarded = build_forward(
            original,
            self.account,
            "central@gmail.com",
        )
        self.assertEqual(forwarded["To"], "central@gmail.com")
        self.assertEqual(forwarded["From"], "central@gmail.com")
        self.assertEqual(forwarded["Reply-To"], "Pessoa <pessoa@example.com>")
        self.assertIn("[Yahoo: Projetos]", forwarded["Subject"])
        self.assertIn("Providenciar documentos", forwarded.get_content())
        self.assertIn("origem@yahoo.com", forwarded.get_content())

    def test_html_body_is_readable(self):
        original = self.parse(
            b"From: aviso@example.com\r\n"
            b"Content-Type: text/html; charset=utf-8\r\n"
            b"\r\n"
            b"<p>Resultado <strong>publicado</strong>.</p>\r\n"
        )
        self.assertIn("Resultado publicado.", extract_body(original))

    def test_attachments_are_copied(self):
        original = self.parse(
            b"From: edital@example.com\r\n"
            b"To: origem@yahoo.com\r\n"
            b"Subject: Edital\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=x\r\n"
            b"\r\n"
            b"--x\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\nSegue anexo.\r\n"
            b"--x\r\n"
            b"Content-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=edital.pdf\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"JVBERi0xLjQ=\r\n"
            b"--x--\r\n"
        )
        forwarded = build_forward(original, self.account, "central@gmail.com")
        attachments = list(forwarded.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "edital.pdf")

    def test_safe_filename_removes_path_characters(self):
        self.assertEqual(safe_filename("../../arquivo.pdf", 1), ".._.._arquivo.pdf")

    def test_missing_yahoo_message_has_specific_error(self):
        class EmptyImap:
            def uid(self, *args):
                return "OK", [b"67 (FLAGS (\\Seen))", b")"]

        with self.assertRaises(MessageUnavailableError):
            fetch_message(EmptyImap(), 67)

    def test_history_search_only_requests_unread_in_fixed_period(self):
        class SearchImap:
            arguments = None

            def uid(self, *args):
                self.arguments = args
                return "OK", [b"10 20 30"]

        imap = SearchImap()
        config = BackfillConfig(
            since=datetime(2026, 6, 23, tzinfo=timezone.utc),
            before=datetime(2026, 7, 21, tzinfo=timezone.utc),
            max_per_run=25,
            dry_run=True,
        )
        self.assertEqual(search_unread_history(imap, config), [10, 20, 30])
        self.assertEqual(
            imap.arguments,
            (
                "search",
                None,
                "UNSEEN",
                "SINCE",
                "23-Jun-2026",
                "BEFORE",
                "21-Jul-2026",
            ),
        )


if __name__ == "__main__":
    unittest.main()
