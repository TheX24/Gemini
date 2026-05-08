import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class DumpBot(discord.Client):
    async def on_ready(self):
        for guild in self.guilds:
            print(f"Guild: {guild.name}")
            for user_id, vs in guild._voice_states.items():
                print(f"  User {user_id}: {vs}")
                print(f"    Raw: {getattr(vs, '_raw', 'N/A')}")
                for attr in dir(vs):
                    if not attr.startswith("_"):
                        try:
                            val = getattr(vs, attr)
                            if "time" in attr.lower() or "since" in attr.lower():
                                print(f"      {attr}: {val}")
                        except: pass
        await self.close()

bot = DumpBot()
bot.run(token)
