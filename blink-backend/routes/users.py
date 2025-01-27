from fastapi import APIRouter, HTTPException, Depends
from models import UserRegister, UserLogin
from db import users_collection
import bcrypt
import uuid
from datetime import datetime, timedelta
import jwt
from auth import ALGORITHM, SECRET_KEY, get_current_user
from pydantic import BaseModel

router = APIRouter()

@router.get("/user_info")
async def get_user_info(current_user: dict = Depends(get_current_user)):
    return {
        "user": {
            "userId": current_user["user_id"],
            "username": current_user["username"],
            "bio": current_user["bio"],
            "email": current_user["email"],
            "avatar": current_user["avatar"],
            "theme": current_user.get("theme", "synthwave"),
            "notifications": current_user.get("notifications", []),
            "friendsList": current_user.get("friendsList", []),
            "groupChatsList": current_user.get("groupChatsList", [])
        }
    }

@router.post("/register")
async def register_user(user: UserRegister):
    existing_user = await users_collection.find_one(
        {"username": {"$regex": f"^{user.username}$", "$options": "i"}}
    )
    existing_email = await users_collection.find_one(
        {"email": {"$regex": f"^{user.email}$", "$options": "i"}}
    )

    if existing_user:
        raise HTTPException(status_code=400, detail="Username already taken")

    if existing_email:
        raise HTTPException(status_code=400, detail="Email already taken")

    hashed_password = bcrypt.hashpw(user.password.encode("utf-8"), bcrypt.gensalt())

    new_user = {
        "user_id": str(uuid.uuid4()),
        "username": user.username,
        "bio": user.bio,
        "email": user.email.lower(),
        "password": hashed_password,
        "avatar": user.avatar,
        "theme": "synthwave",
        "notifications": [],
        "friendsList": [],
    }

    await users_collection.insert_one(new_user)

    return {"message": "User registered successfully!"}


@router.post("/login")
async def login_user(user: UserLogin):
    normalized_email = user.email.strip().lower()
    existing_user = await users_collection.find_one({"email": normalized_email})

    if not existing_user:
        raise HTTPException(status_code=400, detail="Invalid email")

    if not bcrypt.checkpw(user.password.encode("utf-8"), existing_user["password"]):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    token_data = {
        "sub": existing_user["email"],
        "exp": datetime.utcnow() + timedelta(hours=12),
        "username": existing_user["username"],
    }
    token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)

    return {"message": "Login successful", "access_token": token}


@router.delete("/delete_notification")
async def delete_notification(
    id: str,
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    notification_exists = await users_collection.find_one({"user_id": user_id, "notifications.id": id})
    if not notification_exists:
        raise HTTPException(status_code=404, detail="Notification not found or already deleted")

    result = await users_collection.update_one(
        {"user_id": user_id},
        {"$pull": {"notifications": {"id": id}}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found or already deleted")

    return {"message": "Notification deleted successfully"}


@router.put("/mark_all_notifications_seen")
async def mark_all_notifications_seen(
    current_user: dict = Depends(get_current_user)
):
    user_id = current_user["user_id"]
    
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "notifications" not in user or not isinstance(user["notifications"], list):
        raise HTTPException(status_code=404, detail="No notifications array found")

    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"notifications.$[].isSeenByUser": True}}
    )

    return {"message": "All notifications marked as seen"}


class MuteFriendRequest(BaseModel):
    friend_id: str
    is_muted: bool

@router.patch("/mute_friend_chat")
async def mute_friend(request: MuteFriendRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    friend_found = False
    for friend in user.get("friendsList", []):
        if friend["userId"] == request.friend_id:
            friend_found = True
            break

    if not friend_found:
        raise HTTPException(status_code=404, detail="Friend not found")

    result = await users_collection.update_one(
        {"user_id": user_id, "friendsList.userId": request.friend_id},
        {"$set": {"friendsList.$.isMuted": request.is_muted}}
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to update friend status")

    return {"message": f"Chat is {'now muted' if request.is_muted else 'unmuted'}"}


class MuteGroupchatRequest(BaseModel):
    chat_id: str
    is_muted: bool

@router.patch("/mute_groupchat")
async def mute_groupchat(request: MuteGroupchatRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user['user_id']
  
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    groupchat_found = False
    for groupchat in user.get('groupChatsList', []):
        if groupchat['chatId'] == request.chat_id:
            groupchat_found = True
            break
        
    if not groupchat_found:
        raise HTTPException(status_code=404, detail="Groupchat not found")
        
    result = await users_collection.update_one(
        {"user_id": user_id, "groupChatsList.chatId": request.chat_id},
        {"$set": {"groupChatsList.$.isMuted": request.is_muted}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to update groupchat status")

    return {"message": f"Chat is {'now muted' if request.is_muted else 'unmuted'}"}

        
@router.delete("/leave_groupchat")
async def leave_groupchat(chat_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user['user_id']
    
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    group_chat = next((gc for gc in user.get("groupChatsList", []) if gc["chatId"] == chat_id), None)
    if not group_chat:
        raise HTTPException(status_code=404, detail="Group chat not found")
    
    result = await users_collection.update_one(
        {"user_id": user_id},
        {"$pull": {"groupChatsList": {"chatId": chat_id}}}
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to remove group chat from user's account")
    
    group_members = group_chat["users"]
    
    for member in group_members:
        if member["userId"] != user_id:
            await users_collection.update_one(
                {"user_id": member["userId"]},
                {"$pull": {"groupChatsList": {"chatId": chat_id}}}
            )
    
    if len(group_members) == 2:
        for member in group_members:
            await users_collection.update_one(
                {"user_id": member["userId"]},
                {"$pull": {"groupChatsList": {"chatId": chat_id}}}
            )
    
    return {"message": f"You have successfully left the group chat '{chat_id}'."}


@router.delete("/delete_friend")
async def delete_friend(friendId: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    friends_list = user.get("friendsList", [])
    updated_friends_list = [friend for friend in friends_list if friend["userId"] != friendId]

    if len(friends_list) == len(updated_friends_list):
        raise HTTPException(status_code=404, detail="Friend not found")

    update_result = await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"friendsList": updated_friends_list}}
    )

    if update_result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to update user's friends list")

    friend_user = await users_collection.find_one({"user_id": friendId})
    if friend_user:
        friend_friends_list = friend_user.get("friendsList", [])
        updated_friend_friends_list = [
            friend for friend in friend_friends_list if friend["userId"] != user_id
        ]

        await users_collection.update_one(
            {"user_id": friendId},
            {"$set": {"friendsList": updated_friend_friends_list}}
        )

    return {"message": f"{friend_user["username"]} removed from friends list"}


@router.delete("/delete_chat")
async def delete_chat(friendId: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]

    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    friends_list = user.get("friendsList", [])
    friend_found = False
    for friend in friends_list:
        if friend["userId"] == friendId:
            friend_found = True
            friend["messages"] = []
            break

    if not friend_found:
        raise HTTPException(status_code=404, detail="Friend not found")

    update_result = await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"friendsList": friends_list}}
    )

    if update_result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to clear messages")

    return {"message": "Chat messages cleared successfully"}


@router.delete("/delete-group-chat")
async def delete_group_chat(chatId: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user['user_id']  
    
    user = await users_collection.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")  
    
    groupChatsList = user.get('groupChatsList', [])
    groupChatsListFound = False
    
    for groupChat in groupChatsList:
        if groupChat["chatId"] == chatId:
            groupChatsListFound = True
            groupChat["messages"] = []
            break

    if not groupChatsListFound:
        raise HTTPException(status_code=404, detail="Group chat not found")

    update_result = await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"groupChatsList": groupChatsList}}
    )

    if update_result.modified_count == 0:
        raise HTTPException(status_code=500, detail="Failed to clear messages")

    return {"message": "Group chat messages cleared successfully"}


@router.put('change-avatar')
async def update_user_avatar(request: str, current_user: dict = Depends(get_current_user)):
    if not request.avatar:
        raise HTTPException(status_code=400, detail="Avatar URL cannot be empty.")
    
    current_user["avatar"] = request.avatar
    
    return {"message": "Avatar updated successfully"}