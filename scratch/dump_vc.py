import discord
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

class DumpBot(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}")
        for guild in self.guilds:
            for vc in guild.voice_channels:
                if vc.members:
                    print(f"VC: {vc.name} ({vc.id}) in {guild.name}")
                    for member in vc.members:
                        vs = member.voice
                        print(f"  Member: {member.name}")
                        print(f"  Raw Voice State: {vs}")
                        # Try to see if there are any non-standard attributes
                        for attr in dir(vs):
                            if not attr.startswith("_"):
                                try:
                                    val = getattr(vs, attr)
                                    if "time" in attr.lower() or "since" in attr.lower():
                                        print(f"    {attr}: {val}")
                                except:
                                    pass
        await self.close()

bot = DumpBot()
bot.run(token)
