import asyncio
import json
import websockets


class CDPSession:
    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._ws = None
        self._call_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._listeners: dict[str, list] = {}
        self._recv_task = None

    async def connect(self):
        self._ws = await websockets.connect(self._ws_url, max_size=None)
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def disconnect(self):
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    async def send(self, method: str, params: dict = None) -> dict:
        self._call_id += 1
        cid = self._call_id
        msg = {"id": cid, "method": method, "params": params or {}}
        fut = asyncio.get_event_loop().create_future()
        self._pending[cid] = fut
        await self._ws.send(json.dumps(msg))
        return await fut

    def on(self, event: str, callback):
        self._listeners.setdefault(event, []).append(callback)

    def off(self, event: str, callback):
        if event in self._listeners:
            self._listeners[event].remove(callback)

    async def _recv_loop(self):
        async for raw in self._ws:
            msg = json.loads(raw)
            sid = msg.get("sessionId")
            if sid:
                sub = getattr(self, "_sessions", {}).get(sid)
                if sub:
                    asyncio.create_task(sub._dispatch(msg))
                continue
            if "id" in msg:
                fut = self._pending.pop(msg["id"], None)
                if fut and not fut.done():
                    if "error" in msg:
                        fut.set_exception(Exception(msg["error"]["message"]))
                    else:
                        fut.set_result(msg.get("result", {}))
            elif "method" in msg:
                for cb in self._listeners.get(msg["method"], []):
                    asyncio.create_task(cb(msg.get("params", {})))

    async def _dispatch(self, msg: dict):
        if "id" in msg:
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(Exception(msg["error"]["message"]))
                else:
                    fut.set_result(msg.get("result", {}))
        elif "method" in msg:
            for cb in self._listeners.get(msg["method"], []):
                asyncio.create_task(cb(msg.get("params", {})))
