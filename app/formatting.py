"""Display rules shared by the app. Kept separate from Streamlit so they can be unit-tested."""

from decimal import Decimal

UNKNOWN_LABEL = "Unknown (not reported)"
NOT_RATED_LABEL = "Not rated"
SHARE_NOT_AVAILABLE = "Not available"


def service_label(value: bool | None) -> str:
    """true/false/NULL -> Yes/No/Unknown. NULL is never shown as No."""
    if value is None:
        return UNKNOWN_LABEL
    return "Yes" if value else "No"


def share_label(share: Decimal | float | None) -> str:
    """Unrounded 0-1 share -> '32.0%'. NULL (denominator 0) -> 'Not available', never '0%'."""
    if share is None:
        return SHARE_NOT_AVAILABLE
    return f"{float(share) * 100:.1f}%"


def star_label(rating: Decimal | float | None) -> str:
    """Star rating -> '3 stars'. NULL -> 'Not rated', never 0."""
    if rating is None:
        return NOT_RATED_LABEL
    n = int(rating)
    return f"{n} star" if n == 1 else f"{n} stars"
