"""
Verifies Firebase ID tokens using the Firebase Admin SDK. Firebase unifies
Google and Apple sign-in (and any other provider it's configured with)
behind one token type and one stable UID per user, which is what let this
replace two separate JWKS verification implementations with one.

Requires a real Firebase service account JSON (Project Settings > Service
Accounts > Generate new private key, in the Firebase console) to actually
verify anything — without it, this correctly raises ExternalProviderError
rather than pretending to succeed. Two ways to supply it:

1. FIREBASE_SERVICE_ACCOUNT_JSON_PATH (default) — a file path, for local
   dev where the file just sits in secrets/ on disk.
2. FIREBASE_SERVICE_ACCOUNT_JSON — the file's raw JSON *contents* as an
   env var, checked first when set. This is the one that matters for a
   real deploy: most hosting platforms (Render, Railway, Fly, plain
   Docker) have nowhere sane to put a secret *file* by default, but env
   vars are the standard way every one of them handles secrets — so
   deploying means pasting the whole service account JSON as one env
   var's value, not shipping the file itself (which is gitignored and
   should stay that way; it must never end up baked into a Docker image
   layer or committed to the repo).
"""
import json
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import auth as firebase_auth_sdk
from firebase_admin import credentials

from app.core.config import get_settings
from app.core.exceptions import ExternalProviderError, IdentityTokenInvalidError

settings = get_settings()

_firebase_app: Optional[firebase_admin.App] = None


def _get_firebase_app() -> firebase_admin.App:
    """Lazily initializes the Admin SDK exactly once per process. Raising
    ExternalProviderError here (not crashing at import time) means the rest
    of the app still boots and serves guest sign-in even before Firebase
    credentials are configured."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app
    try:
        if settings.firebase_service_account_json:
            cred = credentials.Certificate(json.loads(settings.firebase_service_account_json))
        else:
            cred = credentials.Certificate(settings.firebase_service_account_json_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        return _firebase_app
    except Exception as exc:
        raise ExternalProviderError(f"Could not initialize Firebase Admin SDK: {exc}") from exc


async def verify_firebase_id_token(id_token: str) -> Dict[str, Any]:
    """Returns {"provider_id": <firebase uid>, "email": <email or None>,
    "name": <display name or None>, "sign_in_provider": "google.com" |
    "apple.com" | ...}.

    "name" comes from the token's own "name" claim — Google always
    includes one; Apple only includes it on that identity's very first
    Sign in with Apple ever (and only if the person didn't choose to hide
    their name), so it's frequently None for Apple even on a real,
    named account. Callers should treat a missing name as "nothing to
    capture", not an error.

    firebase_admin.auth.verify_id_token is synchronous (it's a thin wrapper
    over Google's own token verification, not an I/O-bound network call on
    the hot path — the Admin SDK caches Google's public keys internally),
    so it's called directly rather than wrapped in a thread — awaiting it
    would be a no-op since there's nothing to await.
    """
    try:
        app = _get_firebase_app()
        decoded = firebase_auth_sdk.verify_id_token(id_token, app=app)
        return {
            "provider_id": decoded["uid"],
            "email": decoded.get("email"),
            "name": decoded.get("name"),
            "sign_in_provider": decoded.get("firebase", {}).get("sign_in_provider", "unknown"),
        }
    except ExternalProviderError:
        raise
    except firebase_auth_sdk.ExpiredIdTokenError as exc:
        raise IdentityTokenInvalidError("Firebase ID token has expired — sign in again") from exc
    except firebase_auth_sdk.RevokedIdTokenError as exc:
        raise IdentityTokenInvalidError("Firebase ID token has been revoked") from exc
    except firebase_auth_sdk.InvalidIdTokenError as exc:
        raise IdentityTokenInvalidError(f"Firebase ID token is invalid: {exc}") from exc
    except firebase_auth_sdk.CertificateFetchError as exc:
        raise ExternalProviderError(f"Could not fetch Firebase's public keys: {exc}") from exc
    except Exception as exc:
        raise IdentityTokenInvalidError(f"Firebase ID token could not be verified: {exc}") from exc
