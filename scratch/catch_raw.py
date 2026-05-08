import discord
import asyncio
import os
import json
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class RawBot(discord.Client):
    async def on_socket_response(self, msg):
        if msg.get("t") == "GUILD_CREATE":
            data = msg.get("d")
            print(f"GUILD_CREATE for {data.get('name')}")
            for vs in data.get("voice_states", []):
                print(f"  User {vs.get('user_id')} in {vs.get('channel_id')}")
                print(f"  Full VS data: {vs}")
        elif msg.get("t") == "READY":
            print("READY received")

    async def on_ready(self):
        print("Ready!")
        await asyncio.sleep(5)
        await self.close()

bot = RawBot()
bot.run(token)
