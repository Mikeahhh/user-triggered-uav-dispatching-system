import os
import re
from urllib.parse import urlsplit


def load_firebase_runtime_config(environ=None):
    source = os.environ if environ is None else environ
    raw = source.get("GS_FIREBASE_DATABASE_URL")
    errors = []
    target = None
    if not isinstance(raw, str) or not raw.strip():
        errors.append("GS_FIREBASE_DATABASE_URL is required")
    else:
        try:
            if any(ord(c) < 32 or ord(c) == 127 for c in raw):
                raise ValueError("control character in RTDB target")
            url = urlsplit(raw.strip())
            host = url.hostname or ""
            if (url.scheme != "https" or url.username is not None or url.password is not None
                    or url.port not in (None, 443) or url.path not in ("", "/")
                    or url.query or url.fragment or not re.fullmatch(
                        r"(?:[a-z0-9-]+\.firebaseio\.com|[a-z0-9-]+\.[a-z0-9-]+\.firebasedatabase\.app)", host)):
                raise ValueError("invalid RTDB target")
            target = f"https://{host.lower()}"
        except (ValueError, TypeError):
            errors.append("GS_FIREBASE_DATABASE_URL must be an HTTPS RTDB origin without credentials, path, query or fragment")
    return {"database_url": target, "errors": errors, "ready": not errors}
