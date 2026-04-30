import asyncio
import random
import time
import base64
from .session import CDPSession


class HumanBehavior:
    """拟人操作间隔控制，enabled=False 时所有延迟为 0"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    async def before_mouse_move(self):
        """鼠标开始移动前（视线定位目标）"""
        if self.enabled:
            await asyncio.sleep(random.uniform(0.1, 0.4))

    async def mouse_move_duration(self):
        """模拟鼠标移动耗时"""
        if self.enabled:
            await asyncio.sleep(random.uniform(0.2, 0.6))

    async def after_mouse_down(self):
        """mousePressed 到 mouseReleased 之间（按住时长）"""
        if self.enabled:
            await asyncio.sleep(random.uniform(0.05, 0.15))

    async def after_click(self):
        """点击后等待页面响应"""
        if self.enabled:
            await asyncio.sleep(random.uniform(0.1, 0.5))

    async def before_type(self):
        """开始输入前的思考停顿"""
        if self.enabled:
            await asyncio.sleep(random.uniform(0.3, 0.8))

    async def between_keys(self):
        """击键间隔，10% 概率较长停顿模拟思考"""
        if self.enabled:
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.3, 0.6))
            else:
                await asyncio.sleep(random.uniform(0.08, 0.2))


class Page:
    def __init__(self, session: CDPSession, target_id: str):
        self._s = session
        self.target_id = target_id
        self._attached_iframes = {}
        self._auto_attach_ready = False
        self.human = HumanBehavior()

    async def _ensure_auto_attach(self):
        """页面级 auto-attach，只初始化一次，所有 get_frame 共享"""
        if self._auto_attach_ready:
            return
        async def _on_attached(params):
            info = params.get("targetInfo", {})
            if info.get("type") == "iframe":
                sid = params.get("sessionId", "")
                if sid:
                    self._attached_iframes[sid] = info
        self._s.on("Target.attachedToTarget", _on_attached)
        await self._s.send("Target.setAutoAttach", {
            "autoAttach": True,
            "waitForDebuggerOnStart": False,
            "flatten": True,
        })
        self._auto_attach_ready = True

    async def goto(self, url: str, timeout: float = 30):
        """导航到 URL，等待页面 readyState 为 complete。

        Args:
            url: 目标地址
            timeout: 超时秒数，默认 30
        """
        await self._s.send("Page.navigate", {"url": url})
        self._isolated_ctx = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = await self._s.send("Runtime.evaluate", {
                "expression": "document.readyState", "returnByValue": True, "silent": True,
            })
            if r.get("result", {}).get("value") == "complete":
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"goto timeout: {url}")

    async def wait_for_navigation(self, timeout: float = 30):
        fut = asyncio.get_event_loop().create_future()
        def _on_load(params):
            if not fut.done():
                fut.set_result(True)
        self._s.on("Page.loadEventFired", _on_load)
        try:
            await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError("wait_for_navigation timeout")
        finally:
            self._s.off("Page.loadEventFired", _on_load)
            self._isolated_ctx = None

    async def _ensure_isolated_world(self):
        if getattr(self, '_isolated_ctx', None):
            return self._isolated_ctx
        tree = await self._s.send("Page.getFrameTree", {})
        frame_id = tree["frameTree"]["frame"]["id"]
        world = await self._s.send("Page.createIsolatedWorld", {
            "frameId": frame_id, "worldName": "__cdp_util__"
        })
        self._isolated_ctx = world["executionContextId"]
        return self._isolated_ctx

    async def evaluate(self, js: str):
        ctx = await self._ensure_isolated_world()
        r = await self._s.send("Runtime.evaluate", {
            "expression": js, "contextId": ctx,
            "returnByValue": True, "silent": True,
        })
        return r.get("result", {}).get("value")

    async def wait_for_text(self, text: str, timeout: float = 120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.evaluate(f"document.body.innerText.includes({repr(text)})"):
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"wait_for_text timeout: {text}")

    async def wait_for_selector(self, selector: str, state: str = "visible", timeout: float = 120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if state == "attached":
                js = f"!!document.querySelector({repr(selector)})"
            elif state == "visible":
                js = f"(function(){{var el=document.querySelector({repr(selector)});return !!(el&&el.offsetWidth>0&&el.offsetHeight>0&&getComputedStyle(el).display!=='none');}})()"
            else:  # hidden / detached
                js = f"!document.querySelector({repr(selector)})"
            if await self.evaluate(js):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"wait_for_selector timeout: {selector}")

    async def wait_for(self, selector: str, timeout: float = 120):
        return await self.wait_for_selector(selector, timeout=timeout)

    async def query_selector(self, selector: str) -> "ElementHandle | None":
        """查找第一个匹配的元素，未找到返回 None。

        Args:
            selector: CSS 选择器
        """
        try:
            doc = await self._s.send("DOM.getDocument", {"depth": 0})
            r = await self._s.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"], "selector": selector})
            nid = r.get("nodeId", 0)
            if not nid:
                return None
            r2 = await self._s.send("DOM.describeNode", {"nodeId": nid})
            return ElementHandle(self._s, r2["node"]["backendNodeId"])
        finally:
            await self._disable_dom()

    async def query_selector_all(self, selector: str) -> "list[ElementHandle]":
        await self._ensure_dom()
        try:
            doc = await self._s.send("DOM.getDocument", {"depth": 0})
            r = await self._s.send("DOM.querySelectorAll", {"nodeId": doc["root"]["nodeId"], "selector": selector})
            result = []
            for nid in r.get("nodeIds", []):
                r2 = await self._s.send("DOM.describeNode", {"nodeId": nid})
                result.append(ElementHandle(self._s, r2["node"]["backendNodeId"]))
            return result
        finally:
            await self._disable_dom()

    async def click(self, selector: str, timeout: float = 120):
        """模拟真人鼠标点击元素（含移动轨迹和随机延迟）。

        Args:
            selector: CSS 选择器
            timeout: 等待元素出现的超时秒数
        """
        while time.time() < deadline:
            rect = await self.evaluate(f"(function(){{var el=document.querySelector({repr(selector)});if(!el)return null;var r=el.getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}};}})() ")
            if rect:
                await self.human.before_mouse_move()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"], "button": "none", "clickCount": 0})
                await self.human.mouse_move_duration()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_mouse_down()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_click()
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"click timeout: {selector}")

    async def click_by_text(self, text: str, timeout: float = 120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = await self.evaluate(
                f"(function(){{var el=document.evaluate(\"//button[normalize-space()='{text}']|//*[normalize-space()='{text}']\",document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;if(!el)return null;var r=el.getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}};}})() "
            )
            if rect:
                await self.human.before_mouse_move()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"], "button": "none", "clickCount": 0})
                await self.human.mouse_move_duration()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_mouse_down()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_click()
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"click_by_text timeout: {text}")

    async def type(self, selector: str, text: str):
        """点击输入框后逐字符模拟键盘输入（含随机击键间隔）。

        Args:
            selector: CSS 选择器
            text: 要输入的文本
        """
        await self.click(selector)
        await self.human.before_type()
        for ch in text:
            if ch.isascii() and ch.isprintable():
                await self._s.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch, "unmodifiedText": ch, "windowsVirtualKeyCode": ord(ch)})
                await self._s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch, "windowsVirtualKeyCode": ord(ch)})
            else:
                await self._s.send("Input.insertText", {"text": ch})
            await self.human.between_keys()

    async def fill(self, selector: str, text: str):
        await self.evaluate(f"""
            (function(sel,val){{
                var el=document.querySelector(sel);
                var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                setter.call(el,val);
                el.dispatchEvent(new Event('input',{{bubbles:true}}));
                el.dispatchEvent(new Event('change',{{bubbles:true}}));
            }})({repr(selector)},{repr(text)})
        """)

    async def select(self, selector: str, value: str):
        await self.evaluate(f"""
            (function(sel,val){{
                var el=document.querySelector(sel);
                if(!el) return;
                el.value=val;
                el.dispatchEvent(new Event('change',{{bubbles:true}}));
            }})({repr(selector)},{repr(value)})
        """)

    async def screenshot(self, path: str):
        r = await self._s.send("Page.captureScreenshot", {"format": "jpeg", "quality": 80})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)

    async def add_init_script(self, js: str):
        await self._s.send("Page.addScriptToEvaluateOnNewDocument", {"source": js})

    async def on_response(self, url_pattern: str, callback):
        """监听匹配 url_pattern 的网络响应，自动解析 JSON 并回调。

        Args:
            url_pattern: URL 通配符，如 "*api.example.com/data*"
            callback: async def callback(data: dict, url: str)
        """

        if not hasattr(self, '_net_handlers'):
            self._net_handlers = {}
            await self._s.send("Network.enable", {})

            async def on_response_received(params):
                url = params.get("response", {}).get("url", "")
                request_id = params.get("requestId")
                for pattern, cb in self._net_handlers.items():
                    if fnmatch.fnmatch(url, pattern):
                        asyncio.create_task(_get_body(request_id, url, cb))
                        break

            async def _get_body(request_id, url, cb):
                try:
                    await asyncio.sleep(0.1)
                    r = await self._s.send("Network.getResponseBody", {"requestId": request_id})
                    raw = base64.b64decode(r["body"]) if r.get("base64Encoded") else r.get("body", "").encode()
                    try:
                        raw = gzip.decompress(raw)
                    except Exception:
                        pass
                    try:
                        await cb(_json.loads(raw.decode("utf-8", errors="replace")), url)
                    except _json.JSONDecodeError:
                        pass
                except Exception as e:
                    print(f"[Network] error {e}")

            self._s.on("Network.responseReceived", on_response_received)

        self._net_handlers[url_pattern] = callback

    async def watch_new_targets(self, url_pattern: str, callback):
        import fnmatch, json as _json, base64, gzip

        async def _setup_network(send_fn):
            await send_fn("Network.enable", {})

            async def on_resp(params):
                url = params.get("response", {}).get("url", "")
                rid = params.get("requestId")
                if not fnmatch.fnmatch(url, url_pattern):
                    return
                asyncio.create_task(_fetch_body(send_fn, rid, url))

            return on_resp

        async def _fetch_body(send_fn, rid, url):
            try:
                for _ in range(10):
                    try:
                        r = await send_fn("Network.getResponseBody", {"requestId": rid})
                        break
                    except Exception as e:
                        if "No data" in str(e) or "No resource" in str(e):
                            await asyncio.sleep(0.3)
                        else:
                            raise
                else:
                    return
                raw = base64.b64decode(r["body"]) if r.get("base64Encoded") else r.get("body", "").encode()
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
                try:
                    await callback(_json.loads(raw.decode("utf-8", errors="replace")), url)
                except _json.JSONDecodeError:
                    pass
            except Exception as e:
                print(f"[watch error] {e}")

        handler = await _setup_network(self._s.send)
        self._s.on("Network.responseReceived", handler)

        async def on_attached(params):
            info = params.get("targetInfo", {})
            if info.get("type") != "page":
                return
            sid = params.get("sessionId")
            if not sid or sid in getattr(self._s._parent, "_sessions", {}):
                return
            try:
                from .session import CDPSession
                import json as _j
                sub = CDPSession.__new__(CDPSession)
                sub._ws_url = None
                sub._ws = self._s._parent._ws
                sub._call_id = 0
                sub._pending = {}
                sub._listeners = {}
                sub._recv_task = None
                sub._sessions = {}
                sub._session_id = sid
                sub._parent = self._s._parent
                self._s._parent._sessions = getattr(self._s._parent, "_sessions", {})
                self._s._parent._sessions[sid] = sub

                async def send_sub(method, params=None, _sid=sid):
                    sub._call_id += 1
                    cid = sub._call_id
                    msg = {"id": cid, "method": method, "params": params or {}, "sessionId": _sid}
                    fut = asyncio.get_event_loop().create_future()
                    sub._pending[cid] = fut
                    await self._s._parent._ws.send(_j.dumps(msg))
                    return await fut
                sub.send = send_sub

                h = await _setup_network(sub.send)
                sub.on("Network.responseReceived", h)
                await sub.send("Runtime.runIfWaitingForDebugger")
                # print(f"[watch] target {info.get('targetId')} 已启用 Network 监听")
            except Exception as e:
                print(f"[watch attach error] {e}")

        self._s._parent.on("Target.attachedToTarget", on_attached)
        await self._s._parent.send("Target.setAutoAttach", {
            "autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True
        })

    async def get_frame(self, url_pattern: str = None, name_pattern: str = None, blank: bool = False, timeout: float = 120) -> "Frame":
        import fnmatch, json as _j
        deadline = time.time() + timeout

        while time.time() < deadline:
            # 方法1: Page.getFrameTree（同域 / about:blank）
            try:
                tree = await self._s.send("Page.getFrameTree", {})
                def find_frame(node):
                    f = node.get("frame", {})
                    url = f.get("url", "")
                    name = f.get("name", "")
                    if blank and url == "about:blank":
                        return f.get("id")
                    if url_pattern and fnmatch.fnmatch(url, url_pattern):
                        return f.get("id")
                    if name_pattern and fnmatch.fnmatch(name, name_pattern):
                        return f.get("id")
                    for child in node.get("childFrames", []):
                        r = find_frame(child)
                        if r:
                            return r
                frame_id = find_frame(tree.get("frameTree", {}))
                if frame_id:
                    world = await self._s.send("Page.createIsolatedWorld", {"frameId": frame_id})
                    return Frame(self._s, world["executionContextId"], self)
            except Exception:
                pass

            # 方法2: 页面级持久 auto-attach（跨域 iframe）
            try:
                await self._ensure_auto_attach()
            except Exception:
                pass

            for sid, info in self._attached_iframes.items():
                turl = info.get("url", "")
                if (url_pattern and fnmatch.fnmatch(turl, url_pattern)) or \
                   (name_pattern and fnmatch.fnmatch(turl, name_pattern)):
                    if sid in getattr(self._s._parent, "_sessions", {}):
                        continue
                    sub = CDPSession.__new__(CDPSession)
                    sub._ws = self._s._parent._ws
                    sub._call_id = 0
                    sub._pending = {}
                    sub._listeners = {}
                    sub._recv_task = None
                    sub._sessions = {}
                    sub._session_id = sid
                    sub._parent = self._s._parent
                    self._s._parent._sessions = getattr(self._s._parent, "_sessions", {})
                    self._s._parent._sessions[sid] = sub

                    async def send_sub(method, params=None, _sid=sid, _sub=sub):
                        _sub._call_id += 1
                        cid = _sub._call_id
                        msg = {"id": cid, "method": method, "params": params or {}, "sessionId": _sid}
                        fut = asyncio.get_event_loop().create_future()
                        _sub._pending[cid] = fut
                        await self._s._parent._ws.send(_j.dumps(msg))
                        return await fut
                    sub.send = send_sub
                    return Frame(sub, None, self)

            await asyncio.sleep(0.5)

        raise TimeoutError(f"frame not found: url={url_pattern} name={name_pattern}")

    async def _attach_target(self, target_id: str):
        """
        Attach 到指定 CDP target，返回该 target 的 send 函数。
        子 session 注册到 root_s._sessions，确保 _recv_loop 能路由响应。
        """
        import json as _json
        root_s = self._s
        while hasattr(root_s, '_parent') and root_s._parent is not None:
            root_s = root_s._parent

        r = await root_s.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        sid = r["sessionId"]

        _sub = CDPSession.__new__(CDPSession)
        _sub._ws = root_s._ws
        _sub._call_id = 0
        _sub._pending = {}
        _sub._listeners = {}
        _sub._recv_task = None
        _sub._sessions = {}
        _sub._session_id = sid
        _sub._parent = root_s
        root_s._sessions = getattr(root_s, "_sessions", {})
        root_s._sessions[sid] = _sub

        async def _send(method, params=None, _s=_sub, _id=sid):
            _s._call_id += 1
            cid = _s._call_id
            msg = {"id": cid, "method": method, "params": params or {}, "sessionId": _id}
            fut = asyncio.get_event_loop().create_future()
            _s._pending[cid] = fut
            await root_s._ws.send(_json.dumps(msg))
            return await fut

        _sub.send = _send
        return _sub

    async def _get_iframe_offset(self, target_id: str) -> tuple:
        """
        通过 target_id 前8位匹配主页面 DOM 里的 iframe 节点，
        返回 iframe 左上角在主页面视口的坐标 (x, y)。
        原理：Cloudflare target ID 前8位 == 主页面 iframe 节点的 frameId 前8位。
        """
        try:
            await self._s.send("DOM.enable", {})
            main_doc = await self._s.send("DOM.getDocument", {"depth": -1, "pierce": True})

            def _find_all_iframes(node):
                results = []
                if node.get("nodeName") == "IFRAME":
                    results.append((node.get("backendNodeId"), node.get("frameId", "")))
                for c in node.get("children", []):
                    results.extend(_find_all_iframes(c))
                for sr in node.get("shadowRoots", []):
                    results.extend(_find_all_iframes(sr))
                return results

            all_iframes = _find_all_iframes(main_doc["root"])
            iframe_bid = next(
                (bid for bid, fid in all_iframes
                 if fid.upper().startswith(target_id[:8].upper())),
                None
            )
            if iframe_bid:
                iq = await self._s.send("DOM.getContentQuads", {"backendNodeId": iframe_bid})
                pts = iq.get("quads", [])
                if pts and pts[0]:
                    return pts[0][0], pts[0][1]
        except Exception as e:
            print(f"[get_iframe_offset] {e}")
        return 0, 0

    async def get_shadow_iframe_element(self, target_url_pattern: str, target_selector: str, timeout: float = 30) -> "ShadowElement":
        """
        在匹配 target_url_pattern 的跨域 iframe target 内，
        穿透 closed shadow root 找到 target_selector 元素，返回 ShadowElement。

        用法示例：
            el = await page.get_shadow_iframe_element(
                "*challenges.cloudflare.com*",
                "input[type=checkbox]"
            )
            await el.click()
        """
        import fnmatch
        parent_s = self._s._parent if hasattr(self._s, '_parent') else self._s
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                r = await parent_s.send("Target.getTargets", {})
                target_ids = [
                    t["targetId"] for t in r.get("targetInfos", [])
                    if fnmatch.fnmatch(t.get("url", ""), target_url_pattern)
                ]
            except Exception:
                target_ids = []

            for target_id in target_ids:
                try:
                    sub = await self._attach_target(target_id)
                    await sub.send("DOM.enable", {})
                    doc = await sub.send("DOM.getDocument", {"depth": -1, "pierce": True})
                    results = []
                    self._walk_pierce(doc["root"], target_selector, results)
                    if results:
                        ox, oy = await self._get_iframe_offset(target_id)
                        return ShadowElement(sub, results[0], self._s, ox, oy)
                except Exception as e:
                    print(f"[get_shadow_iframe_element] target {target_id}: {e}")

            await asyncio.sleep(0.5)

        raise TimeoutError(f"get_shadow_iframe_element: '{target_selector}' not found in '{target_url_pattern}'")

    async def click_cf_turnstile(self, timeout: float = 30):
        """
        点击 Cloudflare Turnstile 验证框。

        DOM 结构（三层嵌套）：
          主页面
          └── DIV (shadow host)
              └── shadow-root [closed]
                  └── IFRAME (跨域 challenges.cloudflare.com)
                      └── shadow-root [closed]
                          └── INPUT[type=checkbox]  ← 目标

        等价于：
            el = await page.get_shadow_iframe_element("*challenges.cloudflare.com*", "input[type=checkbox]")
            await el.click()
        """
        el = await self.get_shadow_iframe_element(
            "*challenges.cloudflare.com*", "input[type=checkbox]", timeout=timeout
        )
        await el.click()

    # ── Closed Shadow Root 支持 ──────────────────────────────────────────────
    # 原理：CDP DOM.describeNode(pierce=True) 是浏览器内部特权 API，
    # 可穿透 attachShadow({mode:'closed'}) 的 JS 访问限制，
    # 普通 document.querySelector 对 closed shadow root 无效。

    async def _ensure_dom(self):
        if not getattr(self, '_dom_enabled', False):
            await self._s.send("DOM.enable", {})
            self._dom_enabled = True

    async def _disable_dom(self):
        if getattr(self, '_dom_enabled', False):
            await self._s.send("DOM.disable", {})
            self._dom_enabled = False

    def _css_match(self, node: dict, selector: str) -> bool:
        """简单 CSS selector 匹配：支持 tag、#id、.class、[attr=val] 及组合"""
        import re
        tag_m = re.match(r'^([a-zA-Z][a-zA-Z0-9-]*)', selector)
        required_tag = tag_m.group(1).upper() if tag_m else None
        ids = re.findall(r'#([^#.\[:\s]+)', selector)
        classes = re.findall(r'\.([^#.\[:\s]+)', selector)
        attrs = re.findall(r'\[([^=\]]+)(?:=([^\]]+))?\]', selector)

        node_tag = node.get("nodeName", "")
        if required_tag and node_tag != required_tag:
            return False
        node_attrs = node.get("attributes", [])
        attr_map = {node_attrs[i]: node_attrs[i+1] for i in range(0, len(node_attrs)-1, 2)}
        if ids and attr_map.get("id") not in ids:
            return False
        if classes:
            node_cls = set(attr_map.get("class", "").split())
            if not all(c in node_cls for c in classes):
                return False
        for k, v in attrs:
            v = v.strip('"\'') if v else None
            if v is None:
                if k not in attr_map:
                    return False
            elif attr_map.get(k) != v:
                return False
        return True

    def _walk_pierce(self, node: dict, selector: str, results: list):
        """递归遍历 CDP 节点树（含 shadowRoots），收集匹配 selector 的 backendNodeId"""
        if self._css_match(node, selector):
            bid = node.get("backendNodeId")
            if bid:
                results.append(bid)
        for child in node.get("children", []):
            self._walk_pierce(child, selector, results)
        for sr in node.get("shadowRoots", []):
            # shadowRoots 里的节点同样递归，closed/open 均可见
            self._walk_pierce(sr, selector, results)

    async def query_shadow(self, selector: str) -> int | None:
        """
        穿透所有 closed/open shadow root 查找第一个匹配 selector 的元素。
        返回 backendNodeId（可传给 DOM.getContentQuads / DOM.resolveNode 等）。
        """
        await self._ensure_dom()
        doc = await self._s.send("DOM.getDocument", {"depth": -1, "pierce": True})
        results = []
        self._walk_pierce(doc["root"], selector, results)
        return results[0] if results else None

    async def query_shadow_all(self, selector: str) -> list[int]:
        """穿透所有 shadow root，返回全部匹配元素的 backendNodeId 列表"""
        await self._ensure_dom()
        doc = await self._s.send("DOM.getDocument", {"depth": -1, "pierce": True})
        results = []
        self._walk_pierce(doc["root"], selector, results)
        return results

    async def click_shadow(self, selector: str, timeout: float = 30):
        """穿透 closed shadow root 点击元素（通过 DOM.getContentQuads 获取坐标）"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            bid = await self.query_shadow(selector)
            if bid:
                quads = await self._s.send("DOM.getContentQuads", {"backendNodeId": bid})
                pts = quads.get("quads", [])
                if pts and pts[0]:
                    q = pts[0]
                    x = (q[0] + q[2] + q[4] + q[6]) / 4
                    y = (q[1] + q[3] + q[5] + q[7]) / 4
                    await self.human.before_mouse_move()
                    await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0})
                    await self.human.mouse_move_duration()
                    await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
                    await self.human.after_mouse_down()
                    await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
                    await self.human.after_click()
                    return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"click_shadow timeout: {selector}")

    async def fill_shadow(self, selector: str, text: str, timeout: float = 30):
        """穿透 closed shadow root 填充 input 值"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            bid = await self.query_shadow(selector)
            if bid:
                # 先 focus，再通过 Runtime 操作 resolved 对象
                r = await self._s.send("DOM.resolveNode", {"backendNodeId": bid})
                obj_id = r["object"]["objectId"]
                await self._s.send("Runtime.callFunctionOn", {
                    "objectId": obj_id,
                    "functionDeclaration": """function(val) {
                        var setter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value').set;
                        setter.call(this, val);
                        this.dispatchEvent(new Event('input', {bubbles:true}));
                        this.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    "arguments": [{"value": text}],
                    "returnByValue": True,
                })
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"fill_shadow timeout: {selector}")

    async def get_text_shadow(self, selector: str) -> str | None:
        """穿透 closed shadow root 获取元素 innerText"""
        bid = await self.query_shadow(selector)
        if not bid:
            return None
        r = await self._s.send("DOM.resolveNode", {"backendNodeId": bid})
        obj_id = r["object"]["objectId"]
        res = await self._s.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": "function() { return this.innerText; }",
            "returnByValue": True,
        })
        return res.get("result", {}).get("value")

    async def wait_for_shadow(self, selector: str, timeout: float = 30):
        """等待 closed shadow root 内的元素出现"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self.query_shadow(selector):
                return
            await asyncio.sleep(0.3)
        raise TimeoutError(f"wait_for_shadow timeout: {selector}")

    # ── End Closed Shadow Root ───────────────────────────────────────────────

    async def set_cookies(self, cookies):
        if isinstance(cookies, str):
            import json as _json
            cookies = _json.loads(cookies)
        await self._s.send("Network.enable", {})
        await self._s.send("Network.setCookies", {"cookies": cookies})

    async def wait_for_url(self, url_pattern: str, timeout: float = 120):
        import fnmatch
        fut = asyncio.get_event_loop().create_future()
        def _on_nav(params):
            url = params.get("frame", {}).get("url", "")
            if fnmatch.fnmatch(url, url_pattern) and not fut.done():
                fut.set_result(url)
        self._s.on("Page.frameNavigated", _on_nav)
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"wait_for_url timeout: {url_pattern}")
        finally:
            self._s.off("Page.frameNavigated", _on_nav)

    async def url(self) -> str:
        r = await self._s.send("Target.getTargetInfo", {"targetId": self.target_id})
        return r.get("targetInfo", {}).get("url", "")

    async def runtime_disable(self):
        await self._s.send("Runtime.disable", {})

    async def stop_intercept(self):
        await self._s.send("Fetch.disable")

    async def close(self):
        await self._s.send("Target.closeTarget", {"targetId": self.target_id})


class ElementHandle:
    def __init__(self, session: "CDPSession", bid: int):
        self._s = session
        self._bid = bid

    async def _resolve(self) -> str:
        r = await self._s.send("DOM.resolveNode", {"backendNodeId": self._bid})
        return r["object"]["objectId"]

    async def click(self):
        quads = await self._s.send("DOM.getContentQuads", {"backendNodeId": self._bid})
        pts = quads.get("quads", [])
        if not pts or not pts[0]:
            raise RuntimeError("ElementHandle.click: cannot get quads")
        q = pts[0]
        x = (q[0] + q[2] + q[4] + q[6]) / 4
        y = (q[1] + q[3] + q[5] + q[7]) / 4
        await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none", "clickCount": 0})
        await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
        await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})

    async def get_attribute(self, name: str) -> str | None:
        r = await self._s.send("DOM.getAttributes", {"nodeId": await self._get_node_id()})
        attrs = r.get("attributes", [])
        attr_map = {attrs[i]: attrs[i+1] for i in range(0, len(attrs)-1, 2)}
        return attr_map.get(name)

    async def _get_node_id(self) -> int:
        r = await self._s.send("DOM.describeNode", {"backendNodeId": self._bid})
        return r["node"]["nodeId"]

    async def text_content(self) -> str | None:
        obj_id = await self._resolve()
        r = await self._s.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": "function(){return this.textContent;}",
            "returnByValue": True,
        })
        return r.get("result", {}).get("value")

    async def inner_text(self) -> str | None:
        obj_id = await self._resolve()
        r = await self._s.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": "function(){return this.innerText;}",
            "returnByValue": True,
        })
        return r.get("result", {}).get("value")

    async def fill(self, text: str):
        obj_id = await self._resolve()
        await self._s.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": "function(v){var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;s.call(this,v);this.dispatchEvent(new Event('input',{bubbles:true}));this.dispatchEvent(new Event('change',{bubbles:true}));}",
            "arguments": [{"value": text}],
            "returnByValue": True,
        })

    async def is_visible(self) -> bool:
        quads = await self._s.send("DOM.getContentQuads", {"backendNodeId": self._bid})
        pts = quads.get("quads", [])
        return bool(pts and pts[0])


class ShadowElement:
    """
    跨域 iframe 内元素的句柄，封装 backendNodeId + iframe 偏移坐标。
    通过 page.get_shadow_iframe_element() 获取。
    """
    def __init__(self, sub, bid: int, page_session, offset_x: float = 0, offset_y: float = 0):
        self._sub = sub          # 该 iframe target 的 CDPSession
        self._bid = bid          # 元素的 backendNodeId
        self._page_s = page_session  # 主页面 session，用于发送鼠标事件
        self._ox = offset_x      # iframe 在主页面视口的 x 偏移
        self._oy = offset_y      # iframe 在主页面视口的 y 偏移

    async def click(self):
        """模拟鼠标点击（坐标 = iframe 内部坐标 + iframe 偏移）"""
        quads = await self._sub.send("DOM.getContentQuads", {"backendNodeId": self._bid})
        pts = quads.get("quads", [])
        print(f"[ShadowElement.click] bid={self._bid} quads={quads}")
        if not pts or not pts[0]:
            raise RuntimeError("ShadowElement.click: cannot get quads")
        q = pts[0]
        x = (q[0] + q[2] + q[4] + q[6]) / 4 + self._ox
        y = (q[1] + q[3] + q[5] + q[7]) / 4 + self._oy
        for etype in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self._page_s.send("Input.dispatchMouseEvent", {
                "type": etype, "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })

    async def get_text(self) -> str | None:
        """获取元素 innerText"""
        r = await self._sub.send("DOM.resolveNode", {"backendNodeId": self._bid})
        obj_id = r["object"]["objectId"]
        res = await self._sub.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": "function() { return this.innerText; }",
            "returnByValue": True,
        })
        return res.get("result", {}).get("value")

    async def get_attribute(self, name: str) -> str | None:
        """获取元素属性值"""
        r = await self._sub.send("DOM.resolveNode", {"backendNodeId": self._bid})
        obj_id = r["object"]["objectId"]
        res = await self._sub.send("Runtime.callFunctionOn", {
            "objectId": obj_id,
            "functionDeclaration": f"function() {{ return this.getAttribute({repr(name)}); }}",
            "returnByValue": True,
        })
        return res.get("result", {}).get("value")

    async def is_visible(self) -> bool:
        """判断元素是否可见"""
        quads = await self._sub.send("DOM.getContentQuads", {"backendNodeId": self._bid})
        pts = quads.get("quads", [])
        return bool(pts and pts[0])


class Frame:
    def __init__(self, session: CDPSession, context_id: int, page: "Page" = None):
        self._s = session
        self._ctx = context_id
        self._page = page
        self.human = page.human if page else HumanBehavior()

    async def evaluate(self, js: str):
        params = {"expression": js, "returnByValue": True, "silent": True}
        if self._ctx is not None:
            params["contextId"] = self._ctx
        r = await self._s.send("Runtime.evaluate", params)
        return r.get("result", {}).get("value")

    async def wait_for_selector(self, selector: str, state: str = "visible", timeout: float = 30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if state == "attached":
                js = f"!!document.querySelector({repr(selector)})"
            elif state == "visible":
                js = f"(function(){{var el=document.querySelector({repr(selector)});return !!(el&&el.offsetWidth>0&&el.offsetHeight>0&&getComputedStyle(el).display!=='none');}})()"
            else:  # hidden / detached
                js = f"!document.querySelector({repr(selector)})"
            if await self.evaluate(js):
                return
            await asyncio.sleep(0.1)
        raise TimeoutError(f"wait_for_selector timeout: {selector}")

    async def wait_for(self, selector: str, timeout: float = 30):
        return await self.wait_for_selector(selector, timeout=timeout)

    async def click(self, selector: str, timeout: float = 30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = await self.evaluate(f"(function(){{var el=document.querySelector({repr(selector)});if(!el)return null;var r=el.getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}};}})() ")
            if rect:
                await self.human.before_mouse_move()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"], "button": "none", "clickCount": 0})
                await self.human.mouse_move_duration()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_mouse_down()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_click()
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"click timeout: {selector}")

    async def click_by_text(self, text: str, timeout: float = 30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rect = await self.evaluate(
                f"(function(){{var el=document.evaluate(\"//button[normalize-space()='{text}']|//*[normalize-space()='{text}']\",document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;if(!el)return null;var r=el.getBoundingClientRect();return {{x:r.left+r.width/2,y:r.top+r.height/2}};}})() "
            )
            if rect:
                await self.human.before_mouse_move()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": rect["x"], "y": rect["y"], "button": "none", "clickCount": 0})
                await self.human.mouse_move_duration()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_mouse_down()
                await self._s.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
                await self.human.after_click()
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"click_by_text timeout: {text}")

    async def type(self, selector: str, text: str):
        await self.click(selector)
        await self.human.before_type()
        for ch in text:
            if ch.isascii() and ch.isprintable():
                await self._s.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch, "key": ch, "unmodifiedText": ch, "windowsVirtualKeyCode": ord(ch)})
                await self._s.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch, "windowsVirtualKeyCode": ord(ch)})
            else:
                await self._s.send("Input.insertText", {"text": ch})
            await self.human.between_keys()

    async def fill(self, selector: str, text: str):
        await self.evaluate(f"""
            (function(sel,val){{
                var el=document.querySelector(sel);
                var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                setter.call(el,val);
                el.dispatchEvent(new Event('input',{{bubbles:true}}));
                el.dispatchEvent(new Event('change',{{bubbles:true}}));
            }})({repr(selector)},{repr(text)})
        """)

    async def select(self, selector: str, value: str):
        await self.evaluate(f"""
            (function(sel,val){{
                var el=document.querySelector(sel);
                if(!el) return;
                el.value=val;
                el.dispatchEvent(new Event('change',{{bubbles:true}}));
            }})({repr(selector)},{repr(value)})
        """)

    async def click_mouse(self, selector: str, timeout: float = 30, page: "Page" = None, iframe_selector: str = None):
        deadline = time.time() + timeout
        # 获取 iframe 在主页面的偏移（用于跨域 iframe 坐标修正）
        offset_x, offset_y = 0, 0
        if page and iframe_selector:
            off = await page.evaluate(f"""
                (function(){{
                    var el=document.querySelector({repr(iframe_selector)});
                    if(!el) {{
                        var all=document.querySelectorAll('*');
                        for(var e of all){{
                            if(e.shadowRoot){{
                                var f=e.shadowRoot.querySelector('iframe');
                                if(f){{ var r=f.getBoundingClientRect(); return {{x:r.left,y:r.top}}; }}
                            }}
                        }}
                    }} else {{
                        var r=el.getBoundingClientRect(); return {{x:r.left,y:r.top}};
                    }}
                    return {{x:0,y:0}};
                }})()
            """)
            if off:
                offset_x, offset_y = off["x"], off["y"]
        while time.time() < deadline:
            rect = await self.evaluate(f"""
                (function(){{
                    var el=document.querySelector({repr(selector)});
                    if(!el) return null;
                    var r=el.getBoundingClientRect();
                    return {{x:r.left+r.width/2, y:r.top+r.height/2}};
                }})()
            """)
            if rect:
                x, y = rect["x"] + offset_x, rect["y"] + offset_y
                for etype in ("mouseMoved", "mousePressed", "mouseReleased"):
                    await self._s.send("Input.dispatchMouseEvent", {
                        "type": etype, "x": x, "y": y,
                        "button": "left", "clickCount": 1,
                    })
                return
            await asyncio.sleep(0.5)
        raise TimeoutError(f"click_mouse timeout: {selector}")

    async def get_frame(self, url_pattern: str = None, name_pattern: str = None, timeout: float = 120) -> "Frame":
        import fnmatch, json as _j

        if not self._page:
            raise RuntimeError("Frame.get_frame requires page reference")

        pg = self._page
        ps = pg._s
        deadline = time.time() + timeout

        while time.time() < deadline:
            # 方法1: 页面级 frame tree（同域 iframe）
            try:
                tree = await ps.send("Page.getFrameTree", {})
                def find_frame(node):
                    f = node.get("frame", {})
                    url = f.get("url", "")
                    name = f.get("name", "")
                    if url_pattern and fnmatch.fnmatch(url, url_pattern):
                        return f.get("id")
                    if name_pattern and fnmatch.fnmatch(name, name_pattern):
                        return f.get("id")
                    for child in node.get("childFrames", []):
                        r = find_frame(child)
                        if r:
                            return r
                frame_id = find_frame(tree.get("frameTree", {}))
                if frame_id:
                    world = await ps.send("Page.createIsolatedWorld", {"frameId": frame_id})
                    return Frame(ps, world["executionContextId"], pg)
            except Exception:
                pass

            # 方法2: 页面级持久 auto-attach（跨域 iframe）
            try:
                await pg._ensure_auto_attach()
            except Exception:
                pass

            for sid, info in pg._attached_iframes.items():
                turl = info.get("url", "")
                if (url_pattern and fnmatch.fnmatch(turl, url_pattern)) or \
                   (name_pattern and fnmatch.fnmatch(turl, name_pattern)):
                    if sid in getattr(ps._parent, "_sessions", {}):
                        continue
                    sub = CDPSession.__new__(CDPSession)
                    sub._ws = ps._parent._ws
                    sub._call_id = 0
                    sub._pending = {}
                    sub._listeners = {}
                    sub._recv_task = None
                    sub._sessions = {}
                    sub._session_id = sid
                    sub._parent = ps._parent
                    ps._parent._sessions = getattr(ps._parent, "_sessions", {})
                    ps._parent._sessions[sid] = sub

                    async def send_sub(method, params=None, _sid=sid, _sub=sub):
                        _sub._call_id += 1
                        cid = _sub._call_id
                        msg = {"id": cid, "method": method, "params": params or {}, "sessionId": _sid}
                        fut = asyncio.get_event_loop().create_future()
                        _sub._pending[cid] = fut
                        await ps._parent._ws.send(_j.dumps(msg))
                        return await fut
                    sub.send = send_sub
                    return Frame(sub, None, pg)

            await asyncio.sleep(0.5)

        raise TimeoutError(f"frame not found: url={url_pattern} name={name_pattern}")
