from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SignInRequest(BaseModel):
    provider: Literal["firebase", "guest"]
    identity_token: Optional[str] = Field(default=None, description="Required for firebase — the Firebase ID token")
    device_uuid: Optional[str] = Field(default=None, description="Required for guest")


class SignInData(BaseModel):
    # "restorable": this identity deleted its account less than 24h ago —
    # user_id/session_token are null, nothing was created or signed into.
    # The client shows a restore-or-start-fresh choice and calls
    # POST /auth/restore or /auth/restart with the same request instead of
    # treating this as a completed sign-in. See auth_service.sign_in.
    status: Literal["signed_in", "restorable"] = "signed_in"
    user_id: Optional[str] = None
    session_token: Optional[str] = None
    is_new_user: bool = False
    is_guest: bool = False
    restorable_until: Optional[datetime] = None


class LinkIdentityRequest(BaseModel):
    identity_token: str = Field(description="Firebase ID token, from either Google or Apple sign-in")


class LinkIdentityData(BaseModel):
    user_id: str
    is_guest: bool


class WebHandoffTokenData(BaseModel):
    # A short-lived Firebase custom token, single-use in practice — see
    # auth_service.create_web_handoff_token and firebase_auth.mint_custom_token.
    custom_token: str
