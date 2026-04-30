import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from EasyCDP import EasyBrowserCDP

CHROME_PATH   = r'E:\MyBusProject\MyCDP\fingerprint\fp_chrome\fp_chrome.exe'
USER_DATA_DIR = r'D:\MyCDP\fp_chrome\user_data333'
PORT          = 9992
PROXY_URL     = None


async def main():
    fp_encrypted = None
    browser = await EasyBrowserCDP.launch_and_connect(
        port=PORT,
        executable=CHROME_PATH,
        user_data_dir=USER_DATA_DIR,
    )
    container = await browser.new_container(
        name="turnstile-test",
        fingerprint=fp_encrypted,
        proxy=PROXY_URL,
    )
    imported = await browser.get_container(container.id)
    print("new page")
    page = await imported.new_page()
    print("goto")
    await page.goto("https://nopecha.com/captcha/turnstile")
    print("click")
    el = await page.get_shadow_iframe_element(
        "*challenges.cloudflare.com*", "input[type=checkbox]", timeout=30
    )
    await el.click()
    print("验证完成")

    await asyncio.sleep(3)

    await page.close()
    await imported.remove()


asyncio.run(main())
