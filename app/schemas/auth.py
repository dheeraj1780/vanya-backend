from typing import Literal, Optional

from pydantic import BaseModel, Field


class SignInRequest(BaseModel):
    provider: Literal["firebase", "guest"]
    identity_token: Optional[str] = Field(default=None, description="Required for firebase — the Firebase ID token")
    device_uuid: Optional[str] = Field(default=None, description="Required for guest")


class SignInData(BaseModel):
    user_id: str
    session_token: str
    is_new_user: bool
    is_guest: bool


class LinkIdentityRequest(BaseModel):
    identity_token: str = Field(description="Firebase ID token, from either Google or Apple sign-in")


class LinkIdentityData(BaseModel):
    user_id: str
    is_guest: bool
