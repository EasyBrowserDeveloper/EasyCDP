import subprocess
import requests
from .session import CDPSession
from .container import Container
import json


class EasyBrowserCDP:
    def __init__(self, session: CDPSession):
        self._s = session

    @staticmethod
    async def launch_and_connect(port: int, executable: str, user_data_dir: str = None, timeout: int = 60) -> "EasyBrowserCDP":
        """启动浏览器并连接，若端口已有实例则直接连接。

        Args:
            port: 远程调试端口，如 9992
            executable: fp_chrome.exe 路径
            user_data_dir: 用户数据目录，None 则使用默认
            timeout: 等待浏览器就绪的超时秒数
        """
        import asyncio

        try:
            r = requests.get(f"http://localhost:{port}/json/version", timeout=1)
            ws_url = r.json()["webSocketDebuggerUrl"]
            print(f"[ContainerBrowser] 端口 {port} 已存在，直接连接")
        except Exception:
            print(f"[ContainerBrowser] 端口 {port} 未响应，启动浏览器")
            args = [
                executable,
                f"--remote-debugging-port={port}",
                "--no-first-run",
                "--no-default-browser-check",
                '--proxy-bypass-list=*.js;*.css;*.png;*.jpg;*.jpeg;*.gif;*.webp;*.svg;*.ico;*.woff;*.woff2;*.ttf;*.eot;*.otf'
                # f"--proxy-pac-url=file:///E:/MyBusProject/MyCDP/proxy.pac"
            ]
            if user_data_dir:
                args.append(f"--user-data-dir={user_data_dir}")
            proc = subprocess.Popen(args)

            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                try:
                    r = requests.get(f"http://localhost:{port}/json/version", timeout=1)
                    ws_url = r.json()["webSocketDebuggerUrl"]
                    break
                except Exception:
                    if asyncio.get_event_loop().time() > deadline:
                        proc.terminate()
                        raise TimeoutError(f"浏览器在 {timeout}s 内未就绪")
                    await asyncio.sleep(0.5)

        session = CDPSession(ws_url)
        await session.connect()
        return EasyBrowserCDP(session)

    @staticmethod
    async def launch_only(port: int, executable: str, user_data_dir: str = None, timeout: int = 60):
        """启动浏览器进程并等待就绪，不建立 WebSocket 连接。"""
        import asyncio
        try:
            requests.get(f"http://localhost:{port}/json/version", timeout=1)
            print(f"[ContainerBrowser] 端口 {port} 已存在，跳过启动")
            return
        except Exception:
            pass
        args = [
            executable,
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            '--proxy-bypass-list=*.js;*.css;*.png;*.jpg;*.jpeg;*.gif;*.webp;*.svg;*.ico;*.woff;*.woff2;*.ttf;*.eot;*.otf'
        ]
        if user_data_dir:
            args.append(f"--user-data-dir={user_data_dir}")
        proc = subprocess.Popen(args)
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            try:
                requests.get(f"http://localhost:{port}/json/version", timeout=1)
                print(f"[ContainerBrowser] 浏览器已就绪")
                return
            except Exception:
                if asyncio.get_event_loop().time() > deadline:
                    proc.terminate()
                    raise TimeoutError(f"浏览器在 {timeout}s 内未就绪")
                await asyncio.sleep(0.5)

    @staticmethod
    async def connect(port: int = 9222) -> "EasyBrowserCDP":
        r = requests.get(f"http://localhost:{port}/json/version")
        ws_url = r.json()["webSocketDebuggerUrl"]
        session = CDPSession(ws_url)
        await session.connect()
        return EasyBrowserCDP(session)

    async def new_container(self, name: str, fingerprint: dict = None, proxy: str = None) -> Container:
        """创建隔离容器，同名容器已存在则直接返回。

        Args:
            name: 容器名称
            fingerprint: 加密指纹，由 build_fingerprint_encrypted() 生成
            proxy: 代理地址，格式 http://user:pass@host:port
        """
        for c in await self.list_containers():
            if c.name == name:
                return c
        params = {"name": name}
        if fingerprint:
            params["fingerprintConfig"] =fingerprint# fingerprint_encrypt(json.dumps(fingerprint, separators=(",", ":"))) if isinstance(fingerprint, dict) else fingerprint
        if proxy:
            from urllib.parse import urlparse
            p = urlparse(proxy)
            proxy_user = p.username or ""
            proxy_pass = p.password or ""
            if p.hostname:
                port = f":{p.port}" if p.port else ""
                params["proxyIp"] = f"{p.scheme}://{p.hostname}{port}"
            if proxy_user:
                params["proxyUsername"] = proxy_user
            if proxy_pass:
                params["proxyPassword"] = proxy_pass
        r = await self._s.send("Container.create", params)
        return Container(self._s, r["containerId"], name, fingerprint,
                         proxy_user if proxy else "", proxy_pass if proxy else "")

    async def list_containers(self) -> list[Container]:
        """返回所有容器列表。"""
        r = await self._s.send("Container.list")
        result = []
        for c in r.get("containers", []):
            raw = c.get("fingerprintConfig")
            fp = {}
            if raw:
                try:
                    fp = json.loads(raw)
                except Exception:
                    try:
                        fp = json.loads(raw) if isinstance(raw, str) else raw
                    except Exception:
                        fp = {}
            result.append(Container(self._s, c["containerId"], c["name"], fp))
        return result

    async def get_container(self, container_id: str) -> Container:
        """按 ID 获取容器，不存在则抛出异常。"""
        containers = await self.list_containers()
        for c in containers:
            if c.id == container_id:
                return c
        raise Exception(f"Container not found: {container_id}")

    async def remove_container(self, container_id: str):
        await self._s.send("Container.remove", {"containerId": container_id})

    async def close(self):
        await self._s.disconnect()
