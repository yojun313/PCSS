import requests
import traceback
import aiohttp
import asyncio
from user_agent import generate_navigator


TIMEOUT = 10
TRYNUM = 10

def Requester(self, url, headers={}, params={}, cookies={}):
    try:
        if headers == {}:
            headers = random_heador()
        return requests.get(url, headers=headers, params=params, verify=False)

    except Exception as e:
            return ("ERROR", traceback.format_exc())

async def asyncRequester(self, url, headers={}, params={}, cookies={}, session=None):
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    trynum = 0
    while True:
        try:
            headers = random_heador()
            async with session.get(url, headers=headers, params=params, cookies=cookies,
                                    ssl=False, timeout=TIMEOUT) as response:
                return await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError, Exception) as e:
            if trynum >= TRYNUM:
                self.write_log(traceback.format_exc())
            trynum += 1
            
def random_heador():
    navigator = generate_navigator()
    navigator = navigator['user_agent']
    return {"User-Agent": navigator}
