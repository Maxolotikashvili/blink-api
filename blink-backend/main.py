from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from routes.users import router as users_router
from routes.themes import router as themes_router
from fastapi.middleware.cors import CORSMiddleware
from auth import verify_jwt_token
from db import users_collection
import json
import datetime
import uuid

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router, prefix="/users", tags=["Users"])
app.include_router(themes_router, prefix="/themes", tags=["Themes"])

active_connections = {}
    
@app.websocket("/connect")
async def connectUser(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    user_email = current_user["sub"].lower()
    await websocket.accept()
    print(f"WebSocket: Connect established for {current_user['username']}.")
    active_connections[user_email] = websocket

    current_user_data = await users_collection.find_one({"email": user_email})
    if not current_user_data:
        await websocket.send_json({"message": "User not found"})
        await websocket.close()
        return

    friends = current_user_data.get("friendsList", [])
    for friend in friends:
        friend_email = friend["email"]
        if friend_email in active_connections:
            await users_collection.update_one(
                {"email": user_email, "friendsList.email": friend_email},
                {"$set": {"friendsList.$.isOnline": True}},
            )
            await active_connections[friend_email].send_json(
                {
                    "type": "connection",
                    "friendName": current_user['username'],
                    "isOnline": True
                }
            )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if user_email in active_connections:
            del active_connections[user_email]

        for friend in friends:
            friend_email = friend["email"]
            if friend_email in active_connections:
                await users_collection.update_one(
                    {"email": friend_email, "friendsList.email": user_email},
                    {"$set": {"friendsList.$.isOnline": False}},
                )
                await active_connections[friend_email].send_json(
                    {
                        "type": "connection",
                        "friendName": current_user['username'],
                        "isOnline": False
                    }
                )


@app.websocket("/add_friend")
async def websocket_endpoint(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    user_email = current_user["sub"].lower()
    await websocket.accept()
    print(f"WebSocket connection established for {current_user['username']}.")
    
    active_connections[user_email] = websocket

    try:
        while True:
            target_username_or_email = await websocket.receive_text()
            target_username_or_email = target_username_or_email.strip().lower()

            sender_data = await users_collection.find_one({"email": user_email})
            if not sender_data:
                await websocket.send_json({"message": "Sender not found"})
                continue

            target_user = await users_collection.find_one(
                {
                    "$or": [
                        {"email": {"$regex": f"^{target_username_or_email}$", "$options": "i"}},
                        {"username": {"$regex": f"^{target_username_or_email}$", "$options": "i"}},
                    ]
                }
            )
            if not target_user:
                await websocket.send_json({"message": "User not found"})
                continue

            if sender_data["email"] == target_user["email"]:
                await websocket.send_json(
                    {"message": "You cannot add yourself as a friend"}
                )
                continue

            if any(
                friend["userId"] == target_user["user_id"]
                for friend in sender_data.get("friendsList", [])
            ):
                await websocket.send_json(
                    {
                        "message": f"{target_user['username']} is already in your friends list"
                    }
                )
                continue

            if any(
                notif["type"] == "friend-request"
                and notif["sender"]["userId"] == target_user["user_id"]
                and notif["status"] == "pending"
                for notif in sender_data.get("notifications", [])
            ):
                await websocket.send_json(
                    {
                        "message": f"{target_user['username']} already sent you a friend request"
                    }
                )
                continue

            if any(
                req["receiver"]["userId"] == target_user["user_id"]
                and req["status"] == "pending"
                for req in sender_data.get("notifications", [])
            ):
                await websocket.send_json({"message": "Friend request already sent"})
                continue

            if any(
                req["sender"]["userId"] == sender_data["user_id"]
                and req["status"] == "pending"
                for req in target_user.get("notifications", [])
            ):
                await websocket.send_json(
                    {
                        "message": "Can't process request right now, try again later"
                    }
                )
                continue

            notification_id = str(uuid.uuid4())

            outgoing_request = {
                "id": str(uuid.uuid4()),
                "notificationId": notification_id,
                "isSeenByUser": True,
                "isIncoming": False,
                "type": "friend-request",
                "sender": {
                    "userId": sender_data["user_id"],
                    "username": sender_data["username"],
                    "email": sender_data["email"],
                },
                "receiver": {
                    "userId": target_user["user_id"],
                    "username": target_user["username"],
                    "email": target_user["email"],
                },
                "status": "pending",
                "message": f"Friend request sent to {target_user['username']}",
                "displayMessage": f"Friend request sent to {target_user['username']}",
                "timeStamp": str(datetime.datetime.now()),
            }

            incoming_notification = {
                "id": str(uuid.uuid4()),
                "notificationId": notification_id,
                "isSeenByUser": False,
                "isIncoming": True,
                "type": "friend-request",
                "sender": {
                    "userId": sender_data["user_id"],
                    "username": sender_data["username"],
                    "email": sender_data["email"],
                    "avatar": sender_data.get("avatar", ""),
                },
                "receiver": {
                    "userId": target_user["user_id"],
                    "username": target_user["username"],
                    "email": target_user["email"],
                },
                "status": "pending",
                "message": f"{sender_data["username"]} wants to be your friend",
                "displayMessage": f"{sender_data['username']} wants to be your friend",
                "timeStamp": str(datetime.datetime.now()),
            }

            await users_collection.update_one(
                {"email": target_user["email"]},
                {"$push": {"notifications": incoming_notification}},
            )

            await users_collection.update_one(
                {"email": sender_data["email"]},
                {"$push": {"notifications": outgoing_request}},
            )
            
            if user_email in active_connections:
                await active_connections[user_email].send_json(outgoing_request)

            if target_user["email"] in active_connections:
                await active_connections[target_user["email"]].send_json(incoming_notification)

    except WebSocketDisconnect:
        print(f"{current_user['username']} disconnected.")
        if user_email in active_connections:
            del active_connections[user_email]

@app.websocket("/accept_friend_request")
async def accept_friend_request(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    user_email = current_user["sub"].lower()
    await websocket.accept()
    print(f"WebSocket connection established for {current_user['username']}.")

    active_connections[user_email] = websocket

    try:
        while True:
            data = await websocket.receive_text()
            parsed_data = json.loads(data)
            sender_email = parsed_data.get("senderEmail")
            is_accepted = parsed_data.get("isAccepted")

            if not sender_email or is_accepted is None:
                await websocket.send_json({"message": "Invalid data received"})
                continue

            recipient_data = await users_collection.find_one({"email": user_email})
            if not recipient_data:
                await websocket.send_json({"message": "Current user not found"})
                continue

            sender_data = await users_collection.find_one({"email": sender_email})
            if not sender_data:
                await websocket.send_json({"message": "Sender not found"})
                continue

            friend_request_notification = next(
                (
                    notif
                    for notif in recipient_data.get("notifications", [])
                    if notif.get("type") == "friend-request"
                    and notif.get("sender", {}).get("email") == sender_email
                    and notif.get("status") == "pending"
                ),
                None,
            )

            if not friend_request_notification:
                await websocket.send_json({"message": "Friend request not found"})
                continue

            notification_id = friend_request_notification.get("notificationId")

            notification_object = {
                "id": str(uuid.uuid4()),
                "notificationId": notification_id,
                "type": "friend-request",
                "sender": {
                    "userId": recipient_data["user_id"],
                    "username": recipient_data["username"],
                    "email": recipient_data["email"],
                },
                "receiver": {
                    "userId": sender_data["user_id"],
                    "username": sender_data["username"],
                    "email": sender_data["email"],
                },
                "status": "complete",
                "timeStamp": str(datetime.datetime.now()),
            }

            if is_accepted: 
                await users_collection.update_one(
                    {"email": user_email},
                    {
                        "$pull": {
                            "notifications": {
                                "type": "friend-request",
                                "sender.email": sender_email,
                            }
                        }
                    },
                )

                recipient_friend_object = {
                    "userId": sender_data["user_id"],
                    "username": sender_data["username"],
                    "bio": sender_data["bio"],
                    "email": sender_data["email"],
                    "avatar": sender_data.get("avatar", ""),
                    "messages": [],
                }

                sender_friend_object = {
                    "userId": recipient_data["user_id"],
                    "username": recipient_data["username"],
                    "bio": recipient_data["bio"],
                    "email": recipient_data["email"],
                    "avatar": recipient_data.get("avatar", ""),
                    "messages": [],
                }

                await users_collection.update_one(
                    {"email": user_email},
                    {"$push": {"friendsList": recipient_friend_object}},
                )

                await users_collection.update_one(
                    {"email": sender_email},
                    {"$push": {"friendsList": sender_friend_object}},
                )

                notification_for_sender = {
                    **notification_object,
                    "isSeenByUser": False,
                    "isIncoming": True,
                    "message": f"{recipient_data['username']} accepted your friend request",
                    "displayMessage": f"{recipient_data['username']} accepted your friend request",
                    "newFriend": sender_friend_object,
                }

                notification_for_recipient = {
                    **notification_object,
                    "isSeenByUser": True,
                    "isIncoming": False,
                    "message": f"You and {sender_data['username']} are now friends",
                    "displayMessage": f"You accepted {sender_data['username']}'s friend request",
                    "newFriend": recipient_friend_object,
                }

                if sender_email in active_connections:
                    await active_connections[sender_email].send_json(notification_for_sender)

                if user_email in active_connections:
                    await active_connections[user_email].send_json(notification_for_recipient)

                await users_collection.update_one(
                    {"email": sender_email},
                    {"$push": {"notifications": notification_for_sender}},
                )

                await users_collection.update_one(
                    {"email": user_email},
                    {"$push": {"notifications": notification_for_recipient}}
                )

            else:  
                await users_collection.update_one(
                    {"email": sender_email},
                    {
                        "$pull": {
                            "notifications": {
                                "receiver.userId": recipient_data["user_id"]
                            }
                        }
                    },
                )

                await users_collection.update_one(
                    {"email": user_email},
                    {
                        "$pull": {
                            "notifications": {
                                "type": "friend-request",
                                "sender.email": sender_email,
                            }
                        }
                    }
                )

                notification_for_rejection = {
                    **notification_object,
                    "status": "rejected",
                    "message": f"You rejected friend request from {sender_data['username']}",
                }

                if sender_email in active_connections:
                    await active_connections[sender_email].send_json(
                        {**notification_object, "status": "rejected", "message": ""}
                    )

                if user_email in active_connections:
                    await active_connections[user_email].send_json(notification_for_rejection)

    except WebSocketDisconnect:
        print(f"{current_user['username']} disconnected.")
        if user_email in active_connections:
            del active_connections[user_email]



@app.websocket("/chat")
async def chat(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    user_email = current_user["sub"].lower()
    await websocket.accept()
    print(f"WebSocket connection established for {current_user['username']}.")

    active_connections[user_email] = websocket

    try:
        while True:
            data = await websocket.receive_text()
            parsed_data = json.loads(data)

            message_type = parsed_data.get("type")

            # Groupchat messages
            if message_type == "groupChatText":
                chat_id = parsed_data.get("chatId")
                text = parsed_data.get("text")

                if not chat_id or not text:
                    await websocket.send_json({"message": "Invalid data received for group chat"})
                    continue

                user_data = await users_collection.find_one({"email": user_email})
                if not user_data:
                    await websocket.send_json({"message": "User not found"})
                    continue

                group_chat = next(
                    (chat for chat in user_data.get("groupChatsList", []) if chat["chatId"] == chat_id),
                    None,
                )
                if not group_chat:
                    await websocket.send_json({"message": "Group chat not found"})
                    continue

                timestamp = str(datetime.datetime.now())

                base_message = {
                    "id": str(uuid.uuid4()),
                    "timeStamp": timestamp,
                    "text": text,
                    "isSeenBy": []
                }
                
                for member in group_chat["users"]:
                    member_email = member["email"]
                    is_sender = member_email == user_email  

                    user_specific_message = {
                        **base_message,
                        "isIncoming": not is_sender,
                        "sender": "user" if is_sender else current_user["username"],
                        "senderAvatar": user_data["avatar"]
                    }


                    await users_collection.update_one(
                        {"email": member_email, "groupChatsList.chatId": chat_id},
                        {"$push": {"groupChatsList.$.messages": user_specific_message}},
                    )

                    if member_email in active_connections:
                        await active_connections[member_email].send_json(
                            {
                                "type": "groupMessage",
                                "chatId": chat_id,
                                "message": user_specific_message,
                            }
                        )

                continue


            # Friend messages
            elif message_type == "friendText":
                friend_name = parsed_data.get("friendName")
                text = parsed_data.get("text")

                if not friend_name or not text:
                    await websocket.send_json({"message": "Invalid data received for friend"})
                    continue

                user_data = await users_collection.find_one({"email": user_email})
                if not user_data:
                    await websocket.send_json({"message": "User not found"})
                    continue

                friend_data = next(
                    (
                        friend
                        for friend in user_data.get("friendsList", [])
                        if friend["username"] == friend_name
                    ),
                    None,
                )
                if not friend_data:
                    await websocket.send_json({"message": "Friend not found"})
                    continue

                message = {
                    "id": str(uuid.uuid4()),
                    "isSeen": False,
                    "lastSeen": False,
                    "sender": "user",
                    "timeStamp": str(datetime.datetime.now()),
                    "text": text,
                }

                message_for_user = {**message, "isIncoming": False}
                message_for_friend = {**message, "sender": current_user["username"], "isIncoming": True}

                await users_collection.update_one(
                    {"email": user_email, "friendsList.username": friend_name},
                    {"$push": {"friendsList.$.messages": message_for_user}},
                )

                await users_collection.update_one(
                    {
                        "email": friend_data["email"],
                        "friendsList.username": current_user["username"],
                    },
                    {"$push": {"friendsList.$.messages": message_for_friend}},
                )

                if user_email in active_connections:
                    await active_connections[user_email].send_json(
                        {
                            "type": "message",
                            "message": message_for_user,
                            "friendName": friend_data["username"],
                        }
                    )

                if friend_data["email"] in active_connections:
                    await active_connections[friend_data["email"]].send_json(
                        {
                            "type": "message",
                            "message": message_for_friend,
                            "friendName": current_user["username"],
                        }
                    )

    except WebSocketDisconnect:
        print(f"{current_user['username']} disconnected.")
        if user_email in active_connections:
            del active_connections[user_email]
            

@app.websocket("/has_seen")
async def has_seen(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    user_email = current_user["sub"]

    try:
        while True:
            data = await websocket.receive_text()
            parsed_data = json.loads(data)

            chat_id = parsed_data.get("chat_id")
            friend_id = parsed_data.get("friend_id")

            if chat_id and friend_id:
                await websocket.send_json({"error": "Provide either chat_id or friend_id, not both"})
                continue

            if chat_id:
                user_data = await users_collection.find_one({"email": user_email})
                if not user_data:
                    await websocket.send_json({"error": "User not found"})
                    continue
                
                group_chat = next(
                    (
                        chat
                        for chat in user_data.get("groupChatsList", [])
                        if chat["chatId"] == chat_id
                    ),
                    None,
                )
                if not group_chat:
                    await websocket.send_json({"error": "Group chat not found"})
                    continue
                last_message = group_chat["messages"][-1] if group_chat["messages"] else None

                if not last_message or not last_message["isIncoming"]:
                    continue

                for member in group_chat["users"]:
                    member_data = await users_collection.find_one({"email": member["email"]})
                    if not member_data:
                        continue

                    for group_chat in member_data.get("groupChatsList", []):
                        if group_chat["chatId"] == chat_id:
                            for message in group_chat.get("messages", []):
                                message["isSeenBy"] = [
                                    seen for seen in message["isSeenBy"]
                                    if seen['email'] != current_user['sub']
                                ]
                                
                            last_message = group_chat["messages"][-1] if group_chat["messages"] else None
                            if last_message:
                                if not any(seen['email'] == current_user['sub'] for seen in last_message['isSeenBy']):
                                    new_seen_entry = {
                                        "email": current_user['sub'],
                                        "username": "user" if member["username"] == "user" else current_user["username"],
                                        "avatar": user_data['avatar']
                                    }
                                    last_message["isSeenBy"].append(new_seen_entry)

                            await users_collection.update_one(
                                {"email": member["email"], "groupChatsList.chatId": chat_id},
                                {"$set": {"groupChatsList.$.messages": group_chat["messages"]}},
                            )

                            updated_member_data = await users_collection.find_one({"email": member["email"]})
                            updated_group_chat = next(
                                (gc for gc in updated_member_data.get("groupChatsList", []) if gc["chatId"] == chat_id),
                                None
                            )

                            if updated_group_chat and member["email"] in active_connections:
                                await active_connections[member["email"]].send_json({
                                    **updated_group_chat,
                                    "type": "groupSeen"
                                })


            elif friend_id:
                user_data = await users_collection.find_one({"email": user_email})
                if not user_data:
                    await websocket.send_json({"error": "User not found"})
                    continue

                friend = next(
                    (
                        friend
                        for friend in user_data.get("friendsList", [])
                        if friend["userId"] == friend_id
                    ),
                    None,
                )
                if not friend:
                    await websocket.send_json({"error": "Friend not found"})
                    continue

                messages = friend.get("messages", [])
                if not messages:
                    await websocket.send_json(
                        {
                            "type": "hasSeen",
                            "error": "No messages available",
                            "friendName": friend["username"],
                        }
                    )
                    continue

                # Update user's messages: lastSeen and isSeen logic
                last_outgoing_index = next(
                    (i for i, msg in reversed(list(enumerate(messages))) if not msg.get("isIncoming")),
                    None,
                )
                for i, message in enumerate(messages):
                    if not message.get("isIncoming"):  # Only touch messages with !isIncoming
                        message["lastSeen"] = i == last_outgoing_index
                    # Set isSeen to true for all messages
                    message["isSeen"] = True

                await users_collection.update_one(
                    {"email": user_email, "friendsList.userId": friend_id},
                    {"$set": {"friendsList.$.messages": messages}},
                )

                # Handle the friend's data
                friend_data = await users_collection.find_one({"user_id": friend_id})
                if not friend_data:
                    await websocket.send_json({"error": "Friend's data not found"})
                    continue

                friend_friends_list = friend_data.get("friendsList", [])
                for user_friend in friend_friends_list:
                    if user_friend["email"] == user_email:
                        friend_messages = user_friend.get("messages", [])

                        # Update friend's messages: lastSeen and isSeen logic
                        last_outgoing_index = next(
                            (i for i, msg in reversed(list(enumerate(friend_messages))) if not msg.get("isIncoming")),
                            None,
                        )
                        for i, message in enumerate(friend_messages):
                            if not message.get("isIncoming"):  # Only touch messages with !isIncoming
                                message["lastSeen"] = i == last_outgoing_index
                            # Set isSeen to true for all messages
                            message["isSeen"] = True

                        await users_collection.update_one(
                            {
                                "email": friend_data["email"],
                                "friendsList.email": user_email,
                            },
                            {"$set": {"friendsList.$.messages": friend_messages}},
                        )

                        friend_ws_connection = active_connections.get(friend_data["email"])
                        if friend_ws_connection:
                            await friend_ws_connection.send_json(
                                {
                                    "type": "hasSeen",
                                    "friendName": current_user["username"],
                                    "lastSeen": True,
                                }
                            )

                # Notify the current user
                await websocket.send_json(
                    {"type": "hasSeen", "friendName": friend["username"], "lastSeen": True}
                )

    except WebSocketDisconnect:
        await websocket.close()


@app.websocket("/create-group-chat")
async def create_group_chat(websocket: WebSocket):
    query_params = websocket.query_params
    token = query_params.get("token")

    if not token:
        await websocket.close(code=1008)
        return

    current_user = verify_jwt_token(token)
    if not current_user:
        await websocket.close(code=1008)
        return

    user_email = current_user["sub"].lower()
    await websocket.accept()
    print(f"WebSocket connection established for {current_user['username']}.")

    active_connections[user_email] = websocket

    try:
        while True:
            data = await websocket.receive_json()

            if not isinstance(data, list):
                await websocket.send_json({"message": "Invalid data format, expected an array of usernames."})
                continue

            usernames = data

            if not usernames:
                await websocket.send_json({"message": "Usernames list cannot be empty."})
                continue

            sender_data = await users_collection.find_one({"email": user_email})
            if not sender_data or "user_id" not in sender_data:
                await websocket.send_json({"message": "Sender not found or missing userId."})
                continue

            target_users = await users_collection.find({"username": {"$in": usernames}}).to_list(length=None)

            if len(target_users) != len(usernames):
                missing_usernames = set(usernames) - {user["username"] for user in target_users}
                await websocket.send_json({"message": f"Some users not found: {', '.join(missing_usernames)}"})
                continue

            chat_id = str(uuid.uuid4())

            group_chat_users = [
                {
                    "userId": target_user["user_id"],
                    "username": target_user["username"],
                    "avatar": target_user.get("avatar", ""),
                    "email": target_user["email"],
                }
                for target_user in target_users
            ]

            group_chat_users.append({
                "userId": sender_data["user_id"],
                "username": sender_data["username"],
                "avatar": sender_data.get("avatar", ""),
                "email": sender_data["email"],
            })

            group_chat_users_sorted = sorted(group_chat_users, key=lambda x: x["userId"])

            existing_group_chat = await users_collection.aggregate([
                {"$match": {"email": user_email}},  
                {"$unwind": "$groupChatsList"}, 
                {"$project": {"groupChatsList.users": 1}},
                {"$match": {
                    "groupChatsList.users": {"$size": len(group_chat_users_sorted)},  
                    "$expr": {"$eq": [
                        {"$sortArray": {"input": "$groupChatsList.users", "sortBy": {"userId": 1}}},
                        group_chat_users_sorted
                    ]}  
                }},
            ]).to_list(length=None)

            if existing_group_chat:
                await websocket.send_json({"message": "You already have a group chat with these users."})
                continue

            for user in group_chat_users:
                user_specific_group_chat_users = [
                    {
                        "userId": u["userId"],
                        "username": "user" if u["email"] == user["email"] else u["username"],
                        "avatar": u["avatar"],
                        "email": u["email"],
                    }
                    for u in group_chat_users
                ]

                user_specific_group_chat = {
                    "chatId": chat_id,
                    "users": user_specific_group_chat_users,
                    "messages": [],
                    "isMuted": False
                }

                await users_collection.update_one(
                    {"email": user["email"]},
                    {"$push": {"groupChatsList": user_specific_group_chat}}
                )

            response_group_chat_users = [
                {
                    "userId": u["userId"],
                    "username": "user" if u["email"] == user_email else u["username"],
                    "avatar": u["avatar"],
                    "email": u["email"],
                }
                for u in group_chat_users
            ]

            group_chat_response = {
                "chatId": chat_id,
                "users": response_group_chat_users,
                "messages": [],
                "isMuted": False,
                "type": "group-chat-create"
            }

            await websocket.send_json(group_chat_response)

            for user in group_chat_users:
                if user["email"] != user_email:
                    recipient_email = user["email"]
                    
                    if recipient_email in active_connections:
                        recipient_websocket = active_connections[recipient_email]
                        
                        await recipient_websocket.send_json({**group_chat_response, "message": f"{current_user['username']} added you in a group chat"})

    except WebSocketDisconnect:
        print(f"{current_user['username']} disconnected.")
        if user_email in active_connections:
            del active_connections[user_email]

    except Exception as e:
        print(f"Error: {e}")
        await websocket.close(code=1011)

