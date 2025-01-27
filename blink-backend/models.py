from pydantic import BaseModel
from enum import Enum

class UserRegister(BaseModel):
    username: str
    bio: str
    email: str
    password: str
    avatar: str

class UserLogin(BaseModel):
    email: str
    password: str

class AddFriendRequest(BaseModel):
    friend_username_or_email: str

class ThemeEnum(str, Enum):
    chronoflux = "chronoflux"
    timberly = "timberly"
    auraline = "auraline"
    darkbloom = "darkbloom"
    albescent = "albescent"

class ThemeRequest(BaseModel):
    user_id: str
    theme: ThemeEnum