"""Tencent Zhiying aPaaS request signing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import unittest
from urllib.parse import parse_qs, quote, urlparse

from app.providers.zhiying import sign_url

# Vector from the aPaaS signing docs: accesstoken=example_accesstoken,
# params appkey=example_appkey & timestamp=1717639699.
DOC_TOKEN = "example_accesstoken"
DOC_PARAMS = {"appkey": "example_appkey", "timestamp": "1717639699"}
DOC_SIGNATURE = "aCNWYzZdplxWVo+JsqzZc9+J9XrwWWITfX3eQpsLVno="


class SignUrlTest(unittest.TestCase):
    def test_doc_vector_matches(self):
        url = sign_url("https://gw-sg.tvs.qq.com", "/v2/demo", DOC_PARAMS, DOC_TOKEN)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["signature"], [DOC_SIGNATURE])

    def test_params_are_sorted_and_url_encoded(self):
        url = sign_url(
            "https://gw-sg.tvs.qq.com",
            "/v2/demo",
            {"timestamp": "1717639699", "appkey": "example_appkey"},
            DOC_TOKEN,
        )
        signing_content = urlparse(url).query.rsplit("&signature=", 1)[0]
        self.assertEqual(
            signing_content, "appkey=example_appkey&timestamp=1717639699"
        )

    def test_signature_is_url_encoded(self):
        url = sign_url("https://gw-sg.tvs.qq.com", "/v2/demo", DOC_PARAMS, DOC_TOKEN)
        raw_signature = base64.b64encode(
            hmac.new(
                DOC_TOKEN.encode(),
                "appkey=example_appkey&timestamp=1717639699".encode(),
                hashlib.sha256,
            ).digest()
        ).decode()
        self.assertIn("&signature=" + quote(raw_signature, safe=""), url)
        # "+" must not survive unencoded, otherwise servers decode it as a space.
        tail = url.rsplit("&signature=", 1)[1]
        self.assertNotIn("+", tail)

    def test_base_url_and_path_are_joined_verbatim(self):
        url = sign_url("https://example.com/base", "/v2/demo", DOC_PARAMS, DOC_TOKEN)
        self.assertTrue(url.startswith("https://example.com/base/v2/demo?"))


if __name__ == "__main__":
    unittest.main()
