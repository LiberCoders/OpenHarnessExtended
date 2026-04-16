# Browser/Web Agent 实现方案对比

## 方案概述

| 方案 | 开发者 | 协议 | 语言 | 特点 |
|------|--------|------|------|------|
| **Playwright** | Microsoft | CDP + 自定义协议 | Python/JS/Java/C# | 现代、稳定、多浏览器 |
| **Selenium** | 开源社区 | WebDriver | 多语言 | 老牌、生态丰富 |
| **Puppeteer** | Google | Chrome DevTools Protocol | Node.js | Chrome专用、底层控制 |
| **CDP原生** | Google | Chrome DevTools Protocol | 任意 | 最底层、最灵活 |

---

## 方案1：Playwright（推荐）

### 基本原理
Playwright是微软开发的新一代浏览器自动化框架，基于Chrome DevTools Protocol (CDP) 和自定义协议，支持Chromium、Firefox、WebKit。

### 架构优势

```
┌─────────────────────────────────────────┐
│          Your Code (Python)              │
│    playwright.sync_api / async_api       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│      Playwright Driver (Node.js)         │
│    处理多浏览器协议差异，统一API         │
└─────────────┬───────────────────────────┘
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌────────┐ ┌────────┐ ┌────────┐
│Chromium│ │Firefox │ │ WebKit │
│  (CDP) │ │(自定义)│ │(自定义)│
└────────┘ └────────┘ └────────┘
```

### 核心代码示例

```python
from playwright.async_api import async_playwright

class PlaywrightPerceptionProvider(PerceptionProvider):
    """Browser perception using Playwright."""

    def __init__(self, browser_type="chromium", headless=False):
        self.browser_type = browser_type
        self.headless = headless
        self.browser = None
        self.page = None
        self.playwright = None

    async def start(self, start_url=None):
        """Start browser session."""
        self.playwright = await async_playwright().start()

        # 启动浏览器
        browser_class = getattr(self.playwright, self.browser_type)
        self.browser = await browser_class.launch(headless=self.headless)

        # 创建页面
        context = await self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 ..."
        )
        self.page = await context.new_page()

        if start_url:
            await self.page.goto(start_url)

    async def observe(self) -> Observation:
        """Capture browser state."""
        if not self.page:
            raise RuntimeError("Browser not started")

        # 1. 截图（可见区域或完整页面）
        screenshot_bytes = await self.page.screenshot(
            full_page=False,  # 设为True可截全页面
            type="png"
        )

        # 2. 获取页面信息
        url = self.page.url
        title = await self.page.title()

        # 3. 获取可交互元素列表（重要！）
        interactive_elements = await self._get_interactive_elements()

        # 4. 获取页面文本内容（用于LLM理解）
        page_text = await self._get_page_text()

        return Observation(
            text=f"URL: {url}\nTitle: {title}\n\nInteractive elements:\n{interactive_elements}\n\nPage content:\n{page_text[:2000]}",
            visual=Image.open(io.BytesIO(screenshot_bytes)),
            metadata={
                "url": url,
                "title": title,
                "elements": interactive_elements,
                "viewport": await self.page.viewport_size(),
            }
        )

    async def _get_interactive_elements(self) -> list:
        """Get all interactive elements with their properties."""
        elements = await self.page.query_selector_all(
            'button, input, textarea, select, a, [role="button"], [onclick]'
        )

        element_list = []
        for i, elem in enumerate(elements[:50]):  # 限制数量
            info = await elem.evaluate("""
                el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type,
                    text: el.innerText?.substring(0, 100),
                    placeholder: el.placeholder,
                    ariaLabel: el.getAttribute('aria-label'),
                    id: el.id,
                    class: el.className,
                    href: el.href,
                    disabled: el.disabled,
                    boundingBox: el.getBoundingClientRect()
                })
            """)
            if info["boundingBox"]:
                element_list.append({
                    "index": i,
                    "tag": info["tag"],
                    "type": info.get("type"),
                    "text": info.get("text", "")[:50],
                    "location": {
                        "x": info["boundingBox"]["x"],
                        "y": info["boundingBox"]["y"],
                        "width": info["boundingBox"]["width"],
                        "height": info["boundingBox"]["height"],
                    }
                })

        return element_list

    async def _get_page_text(self) -> str:
        """Extract main text content from page."""
        # 获取body文本，但过滤掉脚本/样式
        return await self.page.evaluate("""
            () => {
                const scripts = document.querySelectorAll('script, style, nav, footer');
                scripts.forEach(s => s.remove());
                return document.body.innerText;
            }
        """)

    async def stop(self):
        """Close browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


class PlaywrightActionExecutor(ActionExecutor):
    """Browser action execution using Playwright."""

    def __init__(self, page):
        self.page = page

    async def execute(self, action: Action) -> ActionResult:
        try:
            if action.type == "goto":
                await self.page.goto(action.url, wait_until="networkidle")

            elif action.type == "click":
                if action.selector:
                    # 通过CSS选择器点击
                    await self.page.click(action.selector)
                elif action.coordinates:
                    # 通过坐标点击
                    await self.page.mouse.click(
                        action.coordinates[0],
                        action.coordinates[1]
                    )
                elif action.element_index is not None:
                    # 通过元素索引点击（配合_perception返回的列表）
                    elements = await self.page.query_selector_all(
                        'button, input, textarea, select, a'
                    )
                    if action.element_index < len(elements):
                        await elements[action.element_index].click()

            elif action.type == "type":
                # 清空并输入
                await self.page.fill(action.selector, action.text)
                # 或者逐字输入（更真实）
                # await self.page.type(action.selector, action.text, delay=50)

            elif action.type == "press":
                # 按键（Enter, Tab, Escape等）
                await self.page.press(action.selector or "body", action.key)

            elif action.type == "scroll":
                if action.direction == "down":
                    await self.page.mouse.wheel(0, action.amount or 500)
                elif action.direction == "up":
                    await self.page.mouse.wheel(0, -(action.amount or 500))

            elif action.type == "screenshot":
                # 截图已在perception中处理
                pass

            elif action.type == "wait":
                if action.selector:
                    # 等待元素出现
                    await self.page.wait_for_selector(
                        action.selector,
                        timeout=action.timeout or 5000
                    )
                else:
                    # 固定时间等待
                    await asyncio.sleep(action.duration or 1)

            elif action.type == "extract":
                # 提取数据
                data = await self.page.evaluate(action.javascript)
                return ActionResult(success=True, data=data)

            elif action.type == "back":
                await self.page.go_back()

            elif action.type == "forward":
                await self.page.go_forward()

            elif action.type == "refresh":
                await self.page.reload()

            return ActionResult(success=True)

        except Exception as e:
            return ActionResult(success=False, error=str(e))
```

### 高级功能：更智能的Perception

```python
class SmartPlaywrightPerception(PlaywrightPerceptionProvider):
    """Enhanced perception with accessibility and visual understanding."""

    async def observe(self) -> Observation:
        base_obs = await super().observe()

        # 额外获取：Accessibility Tree
        a11y_tree = await self._get_accessibility_tree()

        # 额外获取：页面性能信息（加载时间、资源数）
        metrics = await self._get_performance_metrics()

        # 检测：是否有弹窗/对话框
        dialogs = await self._detect_dialogs()

        base_obs.metadata.update({
            "accessibility_tree": a11y_tree,
            "performance": metrics,
            "dialogs": dialogs,
        })

        return base_obs

    async def _get_accessibility_tree(self):
        """Get accessibility tree for better understanding."""
        # Playwright支持获取accessibility snapshot
        return await self.page.accessibility.snapshot()
```

### 优缺点

**优点**：
- ✅ **多浏览器支持**：Chromium、Firefox、WebKit
- ✅ **现代API**：自动等待、智能重试、网络拦截
- ✅ **性能优秀**：基于CDP，速度快
- ✅ **稳定性好**：微软维护，更新及时
- ✅ **丰富的元素信息**：可以轻松获取所有按钮、输入框、链接
- ✅ **网络监控**：可以拦截/修改请求
- ✅ **移动端模拟**：支持模拟手机浏览器

**缺点**：
- ❌ **需要安装浏览器**：首次使用需要下载浏览器（~100MB）
- ❌ **Node.js依赖**：底层驱动是Node.js，需要安装
- ❌ **资源占用**：每个浏览器实例占用内存
- ❌ **学习曲线**：API比Selenium新，文档相对少

---

## 方案2：Selenium

### 基本原理
Selenium是老牌浏览器自动化框架，基于WebDriver协议，支持几乎所有浏览器。

### 核心代码示例

```python
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

class SeleniumPerceptionProvider(PerceptionProvider):
    """Browser perception using Selenium."""

    def __init__(self, browser="chrome"):
        self.browser_type = browser
        self.driver = None

    async def start(self, start_url=None):
        """Start browser."""
        if self.browser_type == "chrome":
            options = webdriver.ChromeOptions()
            options.add_argument("--window-size=1920,1080")
            self.driver = webdriver.Chrome(options=options)
        elif self.browser_type == "firefox":
            self.driver = webdriver.Firefox()

        if start_url:
            self.driver.get(start_url)

    async def observe(self) -> Observation:
        """Capture browser state."""
        # 截图
        screenshot_png = self.driver.get_screenshot_as_png()

        # 页面信息
        url = self.driver.current_url
        title = self.driver.title

        # 获取元素（Selenium方式）
        elements = self.driver.find_elements(
            By.CSS_SELECTOR,
            "button, input, textarea, select, a"
        )

        element_list = []
        for elem in elements[:50]:
            try:
                element_list.append({
                    "tag": elem.tag_name,
                    "text": elem.text[:50],
                    "location": elem.location,
                    "size": elem.size,
                    "enabled": elem.is_enabled(),
                    "displayed": elem.is_displayed(),
                })
            except:
                pass

        return Observation(
            text=f"URL: {url}\nTitle: {title}\nElements: {len(element_list)}",
            visual=Image.open(io.BytesIO(screenshot_png)),
            metadata={
                "url": url,
                "title": title,
                "elements": element_list,
            }
        )

    async def stop(self):
        self.driver.quit()
```

### 优缺点

**优点**：
- ✅ **生态最丰富**：10+年历史，教程、问答最多
- ✅ **多语言支持**：Python、Java、C#、JS、Ruby
- ✅ **浏览器支持最全**：几乎所有浏览器
- ✅ **Grid分布式**：支持大规模分布式测试

**缺点**：
- ❌ **API较老**：需要手动处理等待，容易出问题
- ❌ **性能一般**：比Playwright慢
- ❌ **配置复杂**：WebDriver版本需要与浏览器版本匹配
- ❌ **维护成本高**：更新不如Playwright及时

---

## 方案3：Puppeteer

### 基本原理
Puppeteer是Google开发的Node.js库，直接基于Chrome DevTools Protocol，提供最底层的Chrome控制。

### 特点
- **仅支持Chrome/Chromium**
- **Node.js专属**
- **最底层控制**：可以操作Chrome的任何功能

**在我们的场景**：如果需要Python环境，可以使用Pyppeteer（Puppeteer的Python移植版），但维护不如Playwright好。

**推荐度**：如果使用Node.js，Puppeteer很好；如果使用Python，Playwright更好。

---

## 方案4：原生CDP

### 基本原理
直接使用Chrome DevTools Protocol，最底层、最灵活。

```python
import requests
import websocket

# 启动Chrome时开启远程调试
# chrome --remote-debugging-port=9222

# 连接到Chrome
response = requests.get("http://localhost:9222/json/list")
pages = response.json()

# 通过WebSocket发送CDP命令
ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"])
ws.send(json.dumps({
    "id": 1,
    "method": "Page.captureScreenshot",
    "params": {"format": "png"}
}))
result = ws.recv()
```

**推荐度**：除非需要极高自定义，否则使用Playwright更省事。

---

## 对比总结

| 维度 | Playwright | Selenium | Puppeteer | CDP原生 |
|------|------------|----------|-----------|---------|
| **性能** | ⚡ 快 | 🐢 一般 | ⚡ 快 | ⚡ 最快 |
| **浏览器支持** | Chromium/Firefox/WebKit | 几乎所有 | 仅Chrome | 仅Chrome |
| **Python支持** | ⭐⭐⭐ 完美 | ⭐⭐⭐ 完美 | ⭐⭐ Pyppeteer | ⭐⭐ 需封装 |
| **API现代性** | ⭐⭐⭐ 最现代 | ⭐⭐ 较老 | ⭐⭐⭐ 现代 | ⭐ 原始 |
| **元素信息** | ⭐⭐⭐ 丰富 | ⭐⭐ 一般 | ⭐⭐⭐ 丰富 | ⭐⭐ 需处理 |
| **自动等待** | ✅ 原生 | ❌ 手动 | ✅ 原生 | ❌ 手动 |
| **生态** | ⭐⭐ 增长快 | ⭐⭐⭐ 最大 | ⭐⭐ 较大 | ⭐ 小 |
| **安装复杂度** | ⭐⭐ 中等 | ⭐⭐⭐ 复杂（driver管理） | ⭐⭐ 中等 | ⭐ 简单 |
| **维护状态** | ⭐⭐⭐ 活跃 | ⭐⭐ 活跃 | ⭐⭐⭐ 活跃 | ⭐ 底层 |

---

## 推荐方案：Playwright

### 核心理由

1. **与我们的架构完美契合**：
   - 支持async/await，适合Agent Loop
   - 可以轻松包装成PerceptionProvider和ActionExecutor
   - 丰富的元素信息，无需额外OCR

2. **技术领先**：
   - 微软官方维护，更新及时
   - 基于CDP，性能优秀
   - 自动等待，减少错误

3. **适合AI Agent场景**：
   - 可以获取完整的可交互元素列表（带坐标、文本、类型）
   - LLM可以直接理解页面结构
   - 支持截图+DOM树双通道感知

### 实现代码结构

```python
# src/openharness/agents/browser/playwright_perception.py
class PlaywrightPerceptionProvider(PerceptionProvider):
    """Browser perception using Playwright."""
    # ... 见上文完整代码 ...

# src/openharness/agents/browser/playwright_action.py
class PlaywrightActionExecutor(ActionExecutor):
    """Browser action execution using Playwright."""
    # ... 见上文完整代码 ...

# src/openharness/agents/browser/browser_agent_factory.py
class BrowserAgentFactory:
    @staticmethod
    def create(headless=False, browser="chromium"):
        perception = PlaywrightPerceptionProvider(
            browser_type=browser,
            headless=headless
        )
        # 需要初始化后才能获得page，所以稍微复杂一点
        return perception  # 需要特殊处理启动流程
```

### 与PC/Mobile Agent的区别

| 方面 | PC Agent | Mobile Agent | Browser Agent |
|------|----------|--------------|---------------|
| 感知来源 | 屏幕截图 | 设备截图 | 页面DOM + 截图 |
| 元素信息 | 截图后AI识别 | 有限 | **内置丰富API** |
| 坐标系统 | 屏幕绝对坐标 | 屏幕绝对坐标 | 视口相对坐标 |
| 点击方式 | 鼠标模拟 | adb input | Playwright API |
| 输入方式 | 键盘模拟 | adb input | fill/type API |

**Browser Agent的优势**：
- 不需要AI识别界面元素，直接获取按钮/输入框列表
- 操作更精准（基于选择器而非坐标）
- 可以等待页面加载完成再执行下一步

---

## 示例：完整的Browser Agent Loop

```python
# 用户任务："在京东搜索iPhone，找到价格最低的商品"

# Agent执行流程：

# Turn 1: 感知
obs = await perception.observe()
# 返回：
# - 截图（空白页或导航页）
# - 可交互元素（地址栏、收藏夹等）

# Turn 1: 思考（LLM）
thought = {
    "action": "goto",
    "url": "https://www.jd.com"
}

# Turn 1: 行动
await action.execute(thought)
# 浏览器打开京东

# Turn 2: 感知
obs = await perception.observe()
# 返回：
# - 截图（京东首页）
# - 元素列表：[{"index": 0, "tag": "input", "id": "key", "text": ""}, 
#              {"index": 1, "tag": "button", "text": "搜索"}]

# Turn 2: 思考（LLM）
thought = {
    "action": "type",
    "selector": "#key",
    "text": "iPhone"
}

# Turn 2: 行动
await action.execute(thought)

# Turn 3: 思考
thought = {
    "action": "click",
    "selector": "button.button"
}

# ...继续直到找到最低价商品...
```

---

## 实施建议

### 阶段1：基础Browser Agent
1. 实现PlaywrightPerceptionProvider（截图+元素列表）
2. 实现PlaywrightActionExecutor（goto, click, type, scroll）
3. 测试简单任务：打开网页、搜索、点击链接

### 阶段2：增强功能
1. 添加多Tab支持
2. 添加Cookie/Session管理（保持登录状态）
3. 添加下载文件处理
4. 添加JavaScript执行（处理复杂交互）

### 阶段3：高级功能（可选）
1. 网络请求拦截（修改请求/响应）
2. 移动端浏览器模拟
3. 并行多浏览器实例

### 与现有架构集成
```python
# Browser Agent作为spawn_agent的一种类型
spawn_agent(
    type="browser",
    task="在京东搜索iPhone并找到最低价",
    config={
        "headless": False,  # 可见浏览器窗口
        "browser": "chromium"
    }
)
```