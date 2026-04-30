import json
from .session import CDPSession
from .page import Page


class Container:
    def __init__(self, browser_session: CDPSession, container_id: str, name: str, fingerprint: dict = None, proxy_username: str = "", proxy_password: str = ""):
        self._bs = browser_session
        self.id = container_id
        self.name = name
        self.fingerprint = fingerprint or {}
        self._proxy_username = proxy_username
        self._proxy_password = proxy_password

    async def new_page(self, url: str = "about:blank") -> Page:
        """在容器内创建新页面并返回 Page 对象。

        Args:
            url: 初始 URL，默认 about:blank
        """
        r = await self._bs.send("Container.newPage", {
            "containerId": self.id,
            "url": "about:blank",
        })
        target_id = r["targetId"]

        r2 = await self._bs.send("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })
        session_id = r2["sessionId"]

        # 创建子 session，共享父 ws
        page_session = CDPSession.__new__(CDPSession)
        page_session._ws_url = None
        page_session._ws = self._bs._ws
        page_session._call_id = 0
        page_session._pending = {}
        page_session._listeners = {}
        page_session._recv_task = None
        page_session._sessions = {}
        page_session._session_id = session_id
        page_session._parent = self._bs

        # 注册到父 session，由 _recv_loop 统一路由
        self._bs._sessions = getattr(self._bs, "_sessions", {})
        self._bs._sessions[session_id] = page_session

        # 覆盖 send 以带上 sessionId
        import asyncio
        async def send_with_session(method, params=None):
            page_session._call_id += 1
            cid = page_session._call_id
            msg = {"id": cid, "method": method, "params": params or {}, "sessionId": session_id}
            fut = asyncio.get_event_loop().create_future()
            page_session._pending[cid] = fut
            await self._bs._ws.send(json.dumps(msg))
            return await fut

        page_session.send = send_with_session

        # 自动处理代理认证
        if self._proxy_username and self._proxy_password:
            await send_with_session("Fetch.enable", {"handleAuthRequests": True})

            async def on_auth(params):
                if params.get("authChallenge", {}).get("source") == "Proxy":
                    await send_with_session("Fetch.continueWithAuth", {
                        "requestId": params["requestId"],
                        "authChallengeResponse": {
                            "response": "ProvideCredentials",
                            "username": self._proxy_username,
                            "password": self._proxy_password,
                        }
                    })
                else:
                    await send_with_session("Fetch.continueWithAuth", {
                        "requestId": params["requestId"],
                        "authChallengeResponse": {"response": "Default"},
                    })

            page_session.on("Fetch.authRequired", on_auth)

        page = Page(page_session, target_id)
        if url != "about:blank":
            await page.goto(url)
        return page

    async def get_pages(self) -> list:
        """返回容器内所有已打开的 Page 对象列表。"""
        import asyncio
        r = await self._bs.send("Target.getTargets")
        pages = []
        for t in r.get("targetInfos", []):
            if t.get("type") != "page":
                continue
            target_id = t["targetId"]
            r2 = await self._bs.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
            session_id = r2["sessionId"]

            page_session = CDPSession.__new__(CDPSession)
            page_session._ws_url = None
            page_session._ws = self._bs._ws
            page_session._call_id = 0
            page_session._pending = {}
            page_session._listeners = {}
            page_session._recv_task = None
            page_session._sessions = {}
            page_session._session_id = session_id
            page_session._parent = self._bs

            self._bs._sessions = getattr(self._bs, "_sessions", {})
            self._bs._sessions[session_id] = page_session

            async def _make_send(sid, ps):
                async def send_with_session(method, params=None):
                    ps._call_id += 1
                    cid = ps._call_id
                    msg = {"id": cid, "method": method, "params": params or {}, "sessionId": sid}
                    fut = asyncio.get_event_loop().create_future()
                    ps._pending[cid] = fut
                    await self._bs._ws.send(json.dumps(msg))
                    return await fut
                return send_with_session

            page_session.send = await _make_send(session_id, page_session)
            pages.append(Page(page_session, target_id))
        return pages

    async def remove(self):
        """销毁容器及其所有数据（页面、Cookie、存储）。"""
