"""Input validation shared by the JSON APIs and the HTML views."""
import re

BRANCH_CODE_RE = re.compile(r"[A-Z0-9_-]{2,10}")

# Codes that would collide with top-level URL segments (the short display
# URL is /<branch_code>).
RESERVED_BRANCH_CODES = frozenset({
    "ADMIN", "API", "AUTH", "DISPLAY", "MANAGER", "OFFICER", "REGISTER",
    "APPOINTMENTS", "STATIC", "HEALTHZ", "LOGIN", "LOGOUT", "SOCKET_IO",
})

# Loose pre-check before normalisation: digits, spaces, dashes, brackets, +.
MOBILE_INPUT_RE = re.compile(r"\+?[0-9][0-9 \-()]{7,20}")


def validate_branch_code(code: str) -> str | None:
    """Return an error message, or None if the code is acceptable."""
    if not BRANCH_CODE_RE.fullmatch(code or ""):
        return "Location code must be 2-10 letters, digits, '-' or '_'"
    if code in RESERVED_BRANCH_CODES:
        return f"'{code}' is reserved; choose another location code"
    return None


def validate_password(password) -> str | None:
    """Return an error message if the password is too weak, else None."""
    if not isinstance(password, str) or len(password) < 8:
        return "Password must be at least 8 characters"
    if len(password) > 128:
        return "Password must be 128 characters or fewer"
    return None


def normalise_mobile(mobile: str) -> str:
    """Return E.164 (+94XXXXXXXXX for Sri Lanka) or raise ValueError.

    Accepted inputs: 0771234567, 771234567, 94771234567, +94771234567,
    0094771234567, with optional spaces, dashes or brackets. Any other
    international number must be given with + or 00.
    """
    m = re.sub(r"[\s\-()]", "", mobile or "")
    if m.startswith("00"):
        m = "+" + m[2:]
    if m.startswith("+"):
        digits = m[1:]
    elif m.startswith("94") and len(m) == 11:
        digits = m
    elif m.startswith("0") and len(m) == 10:
        digits = "94" + m[1:]
    elif len(m) == 9 and m[0] == "7":          # local mobile without trunk 0
        digits = "94" + m
    else:
        raise ValueError("Unrecognised phone number")
    if not re.fullmatch(r"[1-9][0-9]{8,14}", digits):
        raise ValueError("Unrecognised phone number")
    if digits.startswith("94") and len(digits) != 11:
        raise ValueError("Sri Lankan numbers have 9 digits after +94")
    return "+" + digits


def mask_mobile(mobile) -> str:
    """For logs: keep the country code and the last two digits."""
    if not mobile:
        return str(mobile)
    mobile = str(mobile)
    return f"{mobile[:3]}•••{mobile[-2:]}"
