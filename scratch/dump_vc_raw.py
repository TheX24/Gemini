import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class DumpBot(discord.Client):
    async def on_ready(self):
        for guild in self.guilds:
            for vc in guild.voice_channels:
                if vc.members:
                    print(f"VC: {vc.name} ({vc.id})")
                    # Print raw dictionary if available
                    if hasattr(vc, "_raw"):
                        print(f"  Raw: {vc._raw}")
                    # Print all attributes
                    for attr in dir(vc):
                        if not attr.startswith("_"):
                            try:
                                val = getattr(vc, attr)
                                if "time" in attr.lower() or "since" in attr.lower() or "start" in attr.lower():
                                    print(f"    {attr}: {val}")
                            except: pass
        await self.close()

bot = DumpBot()
bot.run(token)
