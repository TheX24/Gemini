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
                    print(f"VC: {vc.name}")
                    for member in vc.members:
                        print(f"  Member: {member.name}")
                        print(f"  VS Vars: {vars(member.voice)}")
        await self.close()

bot = DumpBot()
bot.run(token)
