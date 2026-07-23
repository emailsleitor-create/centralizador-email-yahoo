import os
import unittest
from email import policy
from email.parser import BytesParser

os.environ.setdefault("STATE_PATH", "/tmp/coletor-yahoo-test-state.json")

from collector import YahooAccount, build_forward, extract_body, safe_filename


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


if __name__ == "__main__":
    unittest.main()
