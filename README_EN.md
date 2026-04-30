# EasyCDP

A Python browser automation library based on Chrome DevTools Protocol (CDP). No CDP fingerprint leakage, supports penetrating `ShadowRoot(closed)`, bypasses Cloudflare 5s shield, hCaptcha, reCAPTCHA, and mainstream bot detection.

---

## Anti-Detection Capabilities

#### Bot Detection Bypass

| Feature | Description |
|---------|-------------|
| Custom CDP Framework | Implements CDP protocol directly, no Playwright/Puppeteer dependency, no framework fingerprint |
| No CDP Domain Leakage | Does not inject `console.enable`, `runtime.enable` or other page-detectable CDP domains |
| isTrust / click Detection | Mouse events carry real `isTrusted=true` with human-like trajectory and random delays |
| webdriver Detection | Removes `navigator.webdriver`, `window.cdc_*`, `__webdriver_*` automation markers |
| Full Container CDP Control | Create, delete, set fingerprint, set proxy — all via CDP commands |

### Fingerprint Anti-Detection (Tab-level Isolation)

- **Container Isolation**: Each container (Tab) has independent proxy, fingerprint, Cookie, LocalStorage, IndexedDB — multiple accounts fully isolated
- **Create Container**: Create isolated fingerprint container via CDP command
- **Delete Container**: Destroy container and all its data via CDP command
- **List Containers**: List all containers with their fingerprint and proxy config

> **ℹ️ About EasyBrowser**
>
> EasyBrowser is a **tab-level isolated** fingerprint browser designed for automation. One browser instance runs multiple fully isolated containers (Tabs), solving the three pain points of traditional multi-instance approaches: resource consumption, traffic consumption, and bot detection.
>
> **Container Isolation**: Each Tab has independent fingerprint (CPU, memory, language, timezone, WebRTC, WebGL, Canvas, Audio, Worker), Cookie / LocalStorage / IndexedDB, and proxy — multiple accounts never interfere.
>
> **Resource Savings**: 30%+ memory reduction vs multi-instance; shared static file cache across containers + proxy bypass rules for static assets reduce traffic significantly.
>
> **Fingerprint Anti-Detection**: Passes Browserscan, CreepJS, Pixelscan and other major detection sites; JS / Intl / HTTP / Worker multi-endpoint consistency; Canvas rendering layer handles blank detection, noise detection, multi-API consistency; Audio kernel and CSS API detection.

#### Mainstream Shield & Detection Platform Support

| Platform | Status |
|----------|--------|
| Cloudflare | ✅ |
| hCaptcha | ✅ |
| reCAPTCHA | ✅ |
| Kasada | ✅ |
| Akamai | ✅ |
| Shape / F5 | ✅ |
| Bet365 | ✅ |
| Datadome | ✅ |
| Brotector (with CDP-Patches) | ✅ |
| Fingerprint.com | ✅ |
| CreepJS | ✅ |
| Sannysoft | ✅ |
| Incolumitas | ✅ |
| IPHey | ✅ |
| Browserscan | ✅ |
| Pixelscan | ✅ |

---

## Examples

### Bypass Cloudflare 5s Shield + Turnstile

Penetrates three layers of closed shadow + cross-origin iframe, auto-clicks checkbox:

```python
await page.goto("https://nopecha.com/captcha/turnstile")

el = await page.get_shadow_iframe_element(
    "*challenges.cloudflare.com*", "input[type=checkbox]", timeout=30
)
await el.click()
```

Full example: [examples/example_turnstile.py](examples/example_turnstile.py)

> More verified scenarios: ChatGPT registration, Outlook, Gmail

---

## Quick Start

```python
import asyncio
from EasyCDP import EasyBrowserCDP

async def main():
    # Launch browser, connect directly if port is already in use
    browser = await EasyBrowserCDP.launch_and_connect(
        port=9992,
        executable=r'path\to\fp_chrome.exe',
        user_data_dir=r'path\to\user_data',
    )

    # Create isolated container (fingerprint and proxy are optional)
    container = await browser.new_container(name="my-container")

    # Open a page inside the container
    page = await container.new_page("https://example.com")
    await page.goto("https://example.com")
    print(await page.title())

    await page.close()
    await container.remove()

asyncio.run(main())
```

---

## Requirements

- Python 3.8+
- `websockets`, `requests`
- Custom Chromium build (supports `Container.*` CDP commands)
- `fingerprint` package (fingerprint builder, install separately)
