from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db import users_collection
from models import ThemeEnum

router = APIRouter()

class ThemeRequest(BaseModel):
    theme: ThemeEnum

@router.post("/save_theme/{user_id}")
async def save_user_theme(user_id: str, theme_request: ThemeRequest):
    user = await users_collection.find_one({"user_id": user_id})
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    update_result = await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"theme": theme_request.theme}}
    )

    if update_result.modified_count == 1:
        return {"message": "Theme updated successfully"}
    else:
        raise HTTPException(status_code=400, detail="Failed to save updated theme")