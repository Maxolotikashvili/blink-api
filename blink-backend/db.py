from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient("mongodb+srv://maxo:maxoo1234@cluster0.9f61g7q.mongodb.net/?retryWrites=true&w=majority")
db = client["blink"]
users_collection = db["users"]
