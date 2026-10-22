"""脱敏的单元测试。"""

import unittest

from tests.helpers import ROOT  # noqa: F401

from engine import masking


class MaskTest(unittest.TestCase):
    def test_pwd_and_pass(self):
        masked = masking.mask({"user": "u", "pwd": "right123", "passwd": "x", "password": "y"})
        self.assertEqual(masked["pwd"], "***")
        self.assertEqual(masked["passwd"], "***")
        self.assertEqual(masked["password"], "***")
        self.assertEqual(masked["user"], "u")

    def test_token_in_params_is_masked(self):
        # v0.9 的 bug：参数里只认 pwd/pass，token 会明文进报告
        masked = masking.mask({"token": "TOK-abc-123"})
        self.assertEqual(masked["token"], masking.MASK)

    def test_auth_key_secret(self):
        masked = masking.mask({"auth": "a", "X-Api-Key": "k", "secret": "s",
                               "Cookie": "c", "session": "sid"})
        self.assertTrue(all(value == masking.MASK for value in masked.values()))

    def test_header_case_insensitive(self):
        masked = masking.mask({"X-Token": "abc", "AUTHORIZATION": "Bearer x"})
        self.assertEqual(masked["X-Token"], masking.MASK)
        self.assertEqual(masked["AUTHORIZATION"], masking.MASK)

    def test_plain_values_kept(self):
        masked = masking.mask({"user": "test001", "page": 2})
        self.assertEqual(masked, {"user": "test001", "page": 2})

    def test_nested_dict(self):
        masked = masking.mask({"payload": {"token": "x", "user": "u"}})
        self.assertEqual(masked["payload"]["token"], masking.MASK)
        self.assertEqual(masked["payload"]["user"], "u")

    def test_sensitive_key_masks_whole_nested_dict(self):
        masked = masking.mask({"auth": {"token": "x", "user": "u"}})
        self.assertEqual(masked["auth"], masking.MASK)

    def test_extra_sensitive_keys(self):
        masked = masking.mask({"手机号": "13800000000"}, extra=("手机号",))
        self.assertEqual(masked["手机号"], masking.MASK)

    def test_empty_and_none(self):
        self.assertEqual(masking.mask(None), {})
        self.assertEqual(masking.mask({}), {})

    def test_input_not_mutated(self):
        source = {"pwd": "x"}
        masking.mask(source)
        self.assertEqual(source, {"pwd": "x"})


if __name__ == "__main__":
    unittest.main()
