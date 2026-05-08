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
            if hasattr(guild, "stage_instances"):
                for si in guild.stage_instances:
                    print(f"  Stage Instance: {si.topic} on {si.channel.name}")
                    print(f"    Created At: {si.created_at}")
            
            # Also check for 'Voice Channel Status' data
            # Some libraries store it in _raw or custom attributes
            for vc in guild.voice_channels:
                if vc.members:
                    print(f"  VC: {vc.name}")
                    if hasattr(vc, "status"):
                        print(f"    Status: {vc.status}")
        await self.close()

bot = DumpBot()
bot.run(token)
