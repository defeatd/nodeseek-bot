from __future__ import annotations


class FetchError(Exception):
    def __init__(self, error_type: str, detail: str = ""):
        super().__init__(f"{error_type}: {detail}")
        self.error_type = error_type
        self.detail = detail


ERROR_LOGIN_REQUIRED = "LOGIN_REQUIRED"
ERROR_ANTIBOT = "ANTIBOT"
ERROR_PARSE_FAIL = "PARSE_FAIL"
ERROR_TIMEOUT = "TIMEOUT"
ERROR_HTTP = "HTTP_ERROR"
ERROR_UNKNOWN = "UNKNOWN"
