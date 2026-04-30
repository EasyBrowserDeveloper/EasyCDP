# EasyCDP

基于 Chrome CDP 的 Python 浏览器自动化库。无 CDP 特征泄露，可穿透 `ShadowRoot(closed)`，支持通过 Cloudflare 5s 盾、hCaptcha、reCAPTCHA 等主流机器人检测。

---

## 反检测能力

#### 机器人防检测

| 特性 | 说明 |
|------|------|
| 自研 CDP 自动化框架 | 直接实现 CDP 协议，不依赖 Playwright/Puppeteer，无框架特征 |
| 无 CDP 域泄露 | 不注入 `console.enable`、`runtime.enable` 等可被页面感知的 CDP 域，彻底消除泄露 |
| 通过 isTrust / click 检测 | 鼠标事件携带真实 `isTrusted=true`，模拟真人轨迹与随机延迟 |
| 通过 webdriver 检测 | 移除 `navigator.webdriver`、`window.cdc_*`、`__webdriver_*` 等自动化标记 |
| 容器 CDP 全控 | 新建容器、删除容器、设置指纹、设置代理，全部通过 CDP 命令完成 |

### 指纹放检测(tab级别隔离)

- **容器隔离**：每个容器(Tab)独立的代理、指纹、Cookie、LocalStorage、IndexedDB，多账号互不干扰
- **新建容器**：通过 CDP 命令创建独立指纹容器
- **删除容器**：通过 CDP 命令销毁容器及其所有数据
- **查看容器**：列出所有容器及其指纹、代理配置

> **ℹ️ 关于 EasyBrowser**
>
> EasyBrowser 是专为自动化场景设计的**页签级隔离**指纹浏览器，一个浏览器实例即可运行多个完全隔离的容器（Tab），直击传统多实例方案的三大痛点：资源消耗、流量消耗、机器人检测。
>
> **容器隔离能力**：每个 Tab 独立的指纹（CPU、内存、语言、时区、WebRTC、WebGL、Canvas、Audio、Worker）、Cookie / LocalStorage / IndexedDB、代理，多账号互不干扰。
>
> **节省资源**：相比多实例并发节省内存 30%+；多容器共享静态文件缓存 + 代理白名单静态资源不走代理，大幅降低流量消耗。
>
> **指纹防检测**：通过 Browserscan、CreepJS、Pixelscan 等主流检测；JS / Intl / HTTP / Worker 多端一致性处理；Canvas 渲染层处理空白检测、噪音检测、多 API 一致性检测；Audio 内核特征及 CSS API 检测。

#### 主流盾 & 检测平台支持

| 平台 | 状态 |
|------|------|
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

## 案例

### 通过 Cloudflare 5s 盾 + Turnstile

穿透三层 closed shadow + 跨域 iframe，自动点击 checkbox：

```python
await page.goto("https://nopecha.com/captcha/turnstile")

el = await page.get_shadow_iframe_element(
    "*challenges.cloudflare.com*", "input[type=checkbox]", timeout=30
)
await el.click()
```

完整示例见 [examples/example_turnstile.py](examples/example_turnstile.py)

> 更多已验证场景：ChatGPT 注册、Outlook、Gmail

---

## 快速开始

```python
import asyncio
from EasyCDP import EasyBrowserCDP

async def main():
    # 启动浏览器，端口已占用则直接连接
    browser = await EasyBrowserCDP.launch_and_connect(
        port=9992,
        executable=r'path\to\fp_chrome.exe',
        user_data_dir=r'path\to\user_data',
    )

    # 创建隔离容器（可选传入 fingerprint、proxy）
    container = await browser.new_container(name="my-container")

    # 在容器内打开页面
    page = await container.new_page("https://example.com")
    await page.goto("https://example.com")
    print(await page.title())

    await page.close()
    await container.remove()

asyncio.run(main())
```

---

## 依赖

- Python 3.8+
- `websockets`、`requests`
- 定制版 Chromium（支持 `Container.*` CDP 命令）
- `fingerprint` 包（指纹构建，需单独安装）
