"""Exceptions raised by the ITC PowerCommerce client."""


class PortalError(Exception):
    """Base class for all portal-related errors."""


class AuthError(PortalError):
    """Login failed: wrong credentials, 2FA required, or re-login exhausted."""


class SessionExpired(PortalError):
    """A request landed on the login page instead of the expected view.

    Raised internally to trigger a single automatic re-login. If it surfaces
    to the caller, the session could not be recovered.
    """


class ParseError(PortalError):
    """The portal returned a page whose structure we could not parse.

    This is the early-warning signal that the portal markup has changed.
    """
