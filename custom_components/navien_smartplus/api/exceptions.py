"""Exceptions raised by the Navien Smart Plus client."""


class NavienError(Exception):
    """Base error."""


class NavienAuthError(NavienError):
    """Credentials rejected or session expired beyond refresh."""


class NavienConnectionError(NavienError):
    """Network or MQTT connection problem."""


class NavienApiError(NavienError):
    """The cloud returned an error response."""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Keep the cloud's error code, when there is one."""
        super().__init__(message)
        self.code = code


class NavienTimeoutError(NavienConnectionError):
    """No MQTT response arrived in time."""
