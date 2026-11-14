"""QR rendering. The payload is always the ticket token and nothing else."""

from io import BytesIO

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def render_qr_png(payload: str, *, box_size: int = 10, border: int = 4) -> bytes:
    """Return a PNG of ``payload``. Kept in memory; never written to disk."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    image = qr.make_image(fill_color="#161320", back_color="#ffffff")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
