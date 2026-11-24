import os
import re
import qrcode
from flask import current_app


def generate_branch_qr(branch) -> str:
    """Generate a QR code PNG for a branch registration URL."""
    # Defence in depth: the code becomes a filename, so never allow path chars.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,10}", branch.code or ""):
        raise ValueError("Unsafe branch code")
    payload  = branch.qr_payload()
    qr       = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#c80000", back_color="white")

    qr_dir = os.path.join(current_app.root_path, "..", "static", "qrcodes")
    os.makedirs(qr_dir, exist_ok=True)
    path = os.path.join(qr_dir, f"{branch.code}.png")
    img.save(path)
    return path
