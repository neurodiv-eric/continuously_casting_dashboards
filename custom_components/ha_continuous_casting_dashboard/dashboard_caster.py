import asyncio
import subprocess
import time
import logging
import logging.handlers

_LOGGER = logging.getLogger(__name__)

from datetime import datetime

class HaContinuousCastingDashboard:
    def __init__(self, hass, config):
        self.hass = hass
        self.config = config

        self.device_map = {}
        self.cast_delay = self.config['cast_delay']
        self.start_time = datetime.strptime(self.config['start_time'], '%H:%M').time()
        self.end_time = datetime.strptime(self.config['end_time'], '%H:%M').time()
        self.max_retries = 5
        self.retry_delay = 30

        for device_name, device_info in self.config['devices'].items():
            self.device_map[device_name] = {
                "dashboard_url": device_info["dashboard_url"],
                "dashboard_state_name": device_info.get("dashboard_state_name", "Dummy"),
            }

        log_level = config.get("logging_level", "info")
        numeric_log_level = getattr(logging, log_level.upper(), None)
        if not isinstance(numeric_log_level, int):
            raise ValueError(f"Invalid log level: {log_level}")
        _LOGGER.setLevel(numeric_log_level)


    async def check_status(self, device_name, state):
        try:
            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "status", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            status_output = stdout.decode()
            return status_output
        except subprocess.CalledProcessError as e:
            _LOGGER.error(f"Error checking {state} state for {device_name}: {e}\nOutput: {e.output.decode()}")
            return None
        except subprocess.TimeoutExpired as e:
            _LOGGER.error(f"Timeout checking {state} state for {device_name}: {e}")
            return None
        except ValueError as e:
            _LOGGER.error(f"Invalid file descriptor for {device_name}: {e}")
            return None


    async def check_dashboard_state(self, device_name):
        dashboard_state_name = self.device_map[device_name]["dashboard_state_name"]
        return await self.check_status(device_name, dashboard_state_name)


    async def check_media_state(self, device_name):
        return await self.check_status(device_name, "PLAYING")


    async def check_both_states(self, device_name):
        dashboard_state_name = self.device_map[device_name]["dashboard_state_name"]
        status_output = await self.check_status(device_name, dashboard_state_name)
        
        if status_output is None or not status_output:
            return False

        _LOGGER.debug(f"Status output for {device_name} when checking for dashboard state '{dashboard_state_name}': {status_output}")

        is_dashboard_state = dashboard_state_name in status_output
        is_media_state = "PLAYING" in status_output

        return is_dashboard_state or is_media_state


    async def cast_dashboard(self, device_name, dashboard_url):
        try:
            _LOGGER.info(f"Casting dashboard to {device_name}")

            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "stop")
            await asyncio.wait_for(process.wait(), timeout=10)

            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "volume", "0")
            await asyncio.wait_for(process.wait(), timeout=10)

            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "cast_site", dashboard_url)
            await asyncio.wait_for(process.wait(), timeout=10)

            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "volume", "50")
            await asyncio.wait_for(process.wait(), timeout=10)
        except subprocess.CalledProcessError as e:
            _LOGGER.error(f"Error casting dashboard to {device_name}: {e}")
            return None
        except ValueError as e:
            _LOGGER.error(f"Invalid file descriptor for {device_name}: {e}")
            return None
        except asyncio.TimeoutError as e:
            _LOGGER.error(f"Timeout casting dashboard to {device_name}: {e}")
            return None



    max_retries = 5
    retry_delay = 30
    retry_count = 0
    async def start(self):
        while True:
            now = datetime.now().time()
            if self.start_time <= now <= datetime.strptime('23:59', '%H:%M').time() or datetime.strptime('00:00', '%H:%M').time() <= now < self.end_time:        
                for device_name, device_info in self.device_map.items():
                    retry_count = 0
                    while retry_count < self.max_retries:
                        if (await self.check_both_states(device_name)) is None:
                            retry_count += 1
                            _LOGGER.warning(f"Retrying in {self.retry_delay} seconds for {retry_count} time(s) due to previous errors")
                            await asyncio.sleep(self.retry_delay)
                            continue
                        elif await self.check_both_states(device_name):
                            _LOGGER.info(f"HA Dashboard (or media) is playing on {device_name}...")
                        else:
                            _LOGGER.info(f"HA Dashboard (or media) is NOT playing on {device_name}!")
                            await self.cast_dashboard(device_name, device_info["dashboard_url"])
                        break
                    else:
                        _LOGGER.error(f"Max retries exceeded for {device_name}. Skipping...")
                        continue
                    await asyncio.sleep(self.cast_delay)
            else:
                _LOGGER.info("Local time is outside of allowed range for casting the screen. Checking for any active HA cast sessions...")
                ha_cast_active = False
                for device_name, dashboard_url in self.device_map.items():
                    if await self.check_dashboard_state(device_name):
                        _LOGGER.info(f"HA Dashboard is currently being cast on {device_name}. Stopping...")
                        try:
                            process = await asyncio.create_subprocess_exec("catt", "-d", device_name, "stop")
                            await process.wait()
                            ha_cast_active = True
                        except subprocess.CalledProcessError as e:
                            _LOGGER.error(f"Error stopping dashboard on {device_name}: {e}")
                            continue
                    else:
                        _LOGGER.info(f"HA Dashboard is NOT currently being cast on {device_name}. Skipping...")
                        continue
                    await asyncio.sleep(self.cast_delay)
                if not ha_cast_active:
                    _LOGGER.info("No active HA cast sessions found. Sleeping for 5 minutes...")
                    await asyncio.sleep(self.cast_delay)