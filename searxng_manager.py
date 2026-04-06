import os
import subprocess
import logging
import asyncio
import httpx
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

class SearxngManager:
    def __init__(self, base_url: str):
        self.base_url = base_url
        parsed = urlparse(base_url)
        self.port = parsed.port or 8080
        self.container_name = "searxng"
        self.managed_by_us = False

    async def is_reachable(self) -> bool:
        """Check if SearXNG is reachable at the configured URL."""
        try:
            async with httpx.AsyncClient() as client:
                # We do a basic request to see if it responds
                resp = await client.get(self.base_url, timeout=2.0)
                return resp.status_code < 500
        except Exception:
            return False

    async def start(self) -> bool:
        """Start SearXNG via Docker if it's not already running."""
        if await self.is_reachable():
            logger.info(f"SearXNG is already reachable at {self.base_url}. Skipping Docker launch.")
            return True

        logger.info("SearXNG not reachable. Attempting to start via Docker...")
        
        try:
            # Check if container already exists
            exists_cmd = ["docker", "ps", "-a", "-q", "-f", f"name=^{self.container_name}$"]
            exists_output = subprocess.check_output(exists_cmd, text=True).strip()
            
            if exists_output:
                logger.info(f"Container '{self.container_name}' exists. Starting it...")
                subprocess.run(["docker", "start", self.container_name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                logger.info(f"Creating new container '{self.container_name}'...")
                
                # Create local settings.yml to explicitly allow 'json' format
                config_dir = os.path.abspath("searxng_config")
                os.makedirs(config_dir, exist_ok=True)
                settings_path = os.path.join(config_dir, "settings.yml")
                if not os.path.exists(settings_path):
                    with open(settings_path, "w") as f:
                        f.write(f"use_default_settings: true\n")
                        f.write(f"server:\n")
                        f.write(f"  base_url: http://localhost:{self.port}/\n")
                        f.write(f"  secret_key: 'searxng_gemini_secret'\n")
                        f.write(f"search:\n")
                        f.write(f"  formats:\n")
                        f.write(f"    - html\n")
                        f.write(f"    - json\n")
                        
                cmd = [
                    "docker", "run", "-d", "--rm", 
                    "--name", self.container_name, 
                    "-p", f"{self.port}:8080", 
                    "-v", f"{config_dir}:/etc/searxng",
                    "docker.io/searxng/searxng:latest"
                ]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            self.managed_by_us = True
            
            # Poll until ready
            logger.info("Waiting for SearXNG to become ready...")
            for i in range(15):
                await asyncio.sleep(2)
                if await self.is_reachable():
                    logger.info("SearXNG is up and running!")
                    return True
                    
            logger.warning("SearXNG failed to become reachable after 30 seconds.")
            return False
            
        except subprocess.SubprocessError as e:
            logger.warning(f"Failed to manage SearXNG container: {e}. Search feature may be unavailable.")
            return False
        except FileNotFoundError:
            logger.warning("Docker is not installed or not in PATH. SearXNG will not start.")
            return False

    def stop(self):
        """Stop the SearXNG container if we started it."""
        if self.managed_by_us:
            logger.info(f"Stopping managed SearXNG container '{self.container_name}'...")
            try:
                subprocess.run(["docker", "stop", self.container_name], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                logger.warning(f"Failed to stop SearXNG container: {e}")
