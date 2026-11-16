import unittest

from app.http import _CLIENT_KWARGS, _tls_verify


class HTTPClientTests(unittest.TestCase):
    def test_tls_verification_is_enabled(self):
        self.assertIs(_tls_verify({}), True)
        self.assertIs(_CLIENT_KWARGS["verify"], True)

    def test_custom_ca_bundle_can_be_configured(self):
        self.assertEqual(_tls_verify({"KPFU_CA_BUNDLE": "/tmp/kpfu-ca.pem"}), "/tmp/kpfu-ca.pem")


if __name__ == "__main__":
    unittest.main()
