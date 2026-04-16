# Mobile UI Agent 实现方案对比

## 方案概述

| 方案 | 类型 | 复杂度 | 适用场景 |
|------|------|--------|----------|
| **ADB** | 命令行工具 | 低 | Android真机/模拟器 |
| **HDC** | 命令行工具 | 低 | 鸿蒙真机/模拟器 |
| **Appium** | 自动化框架 | 高 | 跨平台自动化测试 |

---

## 方案1：ADB (Android Debug Bridge)

### 基本原理
ADB是Android SDK自带的命令行工具，通过USB或网络与Android设备通信，可以直接执行shell命令操控设备。

### 核心命令

```bash
# 设备连接
adb devices                    # 列出连接的设备
adb connect <ip:port>         # 网络连接设备

# 截图
adb shell screencap -p /sdcard/screen.png
adb pull /sdcard/screen.png ./screenshot.png

# 点击操作
adb shell input tap x y        # 点击坐标(x,y)
adb shell input swipe x1 y1 x2 y2 duration  # 滑动

# 输入文字
adb shell input text "hello"   # 输入文本

# 按键操作
adb shell input keyevent KEYCODE_HOME      # 按Home键
adb shell input keyevent KEYCODE_BACK      # 按返回键
adb shell input keyevent 26                # 电源键

# 获取UI信息
adb shell uiautomator dump /sdcard/ui.xml  # 导出UI层次结构
adb pull /sdcard/ui.xml ./ui.xml

# 安装/启动应用
adb install app.apk
adb shell am start -n com.package/.Activity  # 启动应用
adb shell am force-stop com.package         # 停止应用

# 获取设备信息
adb shell getprop ro.product.model          # 设备型号
adb shell dumpsys window displays           # 屏幕分辨率
```

### Python封装示例

```python
# src/openharness/agents/mobile/adb_perception.py
class ADBPerceptionProvider(PerceptionProvider):
    """Mobile perception using ADB."""

    def __init__(self, device_id: str = None):
        self.device_id = device_id  # None表示默认设备
        self.screenshot_dir = "/sdcard"

    async def observe(self) -> Observation:
        # 1. 截图
        timestamp = int(time.time())
        device_flag = f"-s {self.device_id}" if self.device_id else ""

        # 截图到设备
        await self._run_shell(f"adb {device_flag} shell screencap -p {self.screenshot_dir}/screen_{timestamp}.png")
        # 拉到本地
        local_path = f"/tmp/mobile_screenshot_{timestamp}.png"
        await self._run_shell(f"adb {device_flag} pull {self.screenshot_dir}/screen_{timestamp}.png {local_path}")

        # 2. 获取UI层次（可选，但有助于理解界面）
        ui_xml = await self._get_ui_hierarchy(device_flag)

        # 3. 获取设备信息
        device_info = await self._get_device_info(device_flag)

        return Observation(
            text=ui_xml,  # UI层次结构文本
            visual=Image.open(local_path),  # 截图
            metadata={
                "device_id": self.device_id,
                "device_info": device_info,
                "screenshot_path": local_path,
                "timestamp": timestamp
            }
        )

    async def _get_ui_hierarchy(self, device_flag: str) -> str:
        """Get UI hierarchy using uiautomator."""
        try:
            await self._run_shell(f"adb {device_flag} shell uiautomator dump /sdcard/window_dump.xml")
            await self._run_shell(f"adb {device_flag} pull /sdcard/window_dump.xml /tmp/ui.xml")
            with open("/tmp/ui.xml", "r", encoding="utf-8") as f:
                return f.read()
        except:
            return ""  # 如果失败，只用截图

# src/openharness/agents/mobile/adb_action.py
class ADBActionExecutor(ActionExecutor):
    """Mobile action execution using ADB."""

    def __init__(self, device_id: str = None):
        self.device_id = device_id

    async def execute(self, action: Action) -> ActionResult:
        device_flag = f"-s {self.device_id}" if self.device_id else ""

        if action.type == "tap":
            x, y = action.coordinates
            await self._run_shell(f"adb {device_flag} shell input tap {x} {y}")

        elif action.type == "swipe":
            x1, y1, x2, y2 = action.swipe_coords
            duration = action.duration or 300
            await self._run_shell(f"adb {device_flag} shell input swipe {x1} {y1} {x2} {y2} {duration}")

        elif action.type == "input_text":
            text = action.text.replace(" ", "%s")  # ADB需要转义空格
            await self._run_shell(f"adb {device_flag} shell input text '{text}'")

        elif action.type == "keyevent":
            keycode = action.keycode
            await self._run_shell(f"adb {device_flag} shell input keyevent {keycode}")

        elif action.type == "start_app":
            package = action.package
            activity = action.activity
            await self._run_shell(f"adb {device_flag} shell am start -n {package}/{activity}")

        elif action.type == "install_app":
            apk_path = action.apk_path
            await self._run_shell(f"adb {device_flag} install {apk_path}")

        return ActionResult(success=True)
```

### 优缺点

**优点**：
- ✅ **简单直接**：纯命令行，无额外依赖
- ✅ **轻量级**：无需安装复杂框架
- ✅ **快速响应**：命令执行延迟低（~100ms）
- ✅ **稳定性好**：成熟工具，Bug少
- ✅ **真机/模拟器都支持**：无需Root
- ✅ **跨平台**：Windows/Mac/Linux都支持ADB

**缺点**：
- ❌ **功能有限**：只能模拟基本触控和按键
- ❌ **无应用内信息获取**：无法直接获取APP内部状态（如列表内容）
- ❌ **坐标依赖**：需要基于坐标点击，不同分辨率设备需要适配
- ❌ **无等待机制**：需要自己实现等待元素出现
- ❌ **仅Android**：不直接支持iOS

---

## 方案2：HDC (HarmonyOS Device Connector)

### 基本原理
HDC是鸿蒙系统的设备连接工具，功能类似ADB，但专为HarmonyOS设计。

### 核心命令

```bash
# 设备连接
hdc list targets               # 列出设备
hdc -t <device_id> shell       # 指定设备执行

# 截图
hdc shell snapshot_display -f /data/screen.png
hdc file recv /data/screen.png ./screenshot.png

# 点击操作
hdc shell uinput -T -x 100 -y 200  # 点击坐标(100,200)

# 滑动
hdc shell uinput -S -x 100 -y 200 -X 300 -Y 400 -d 500

# 输入文字
hdc shell uinput -T -x 100 -y 200  # 先点击输入框聚焦
hdc shell uinput -K -c "hello"     # 输入文字

# 按键
hdc shell uinput -K -k 1   # Home键
hdc shell uinput -K -k 2   # Back键

# 获取UI信息
hdc shell uitest dumpLayout    # 导出UI布局

# 安装应用
hdc app install entry.hap

# 启动应用
hdc shell aa start -a EntryAbility -b com.example.app
```

### Python封装

与ADB几乎相同，只是命令前缀从`adb`变成`hdc`，部分命令参数不同。

```python
class HDCPerceptionProvider(PerceptionProvider):
    """鸿蒙设备感知层，类似ADB但使用HDC命令."""

    async def observe(self) -> Observation:
        # 截图
        await self._run_shell(f"hdc -t {self.device_id} shell snapshot_display -f /data/screen.png")
        await self._run_shell(f"hdc -t {self.device_id} file recv /data/screen.png /tmp/screen.png")

        # 获取UI布局
        ui_info = await self._run_shell(f"hdc -t {self.device_id} shell uitest dumpLayout")

        return Observation(
            text=ui_info,
            visual=Image.open("/tmp/screen.png"),
            metadata={"device_type": "harmonyos"}
        )
```

### 优缺点

**优点**：
- ✅ 与ADB类似，简单直接
- ✅ 专为鸿蒙优化
- ✅ 支持鸿蒙特有功能（分布式设备等）

**缺点**：
- ❌ 仅鸿蒙系统
- ❌ 生态相对较新，文档较少
- ❌ 部分命令与ADB不兼容

---

## 方案3：Appium

### 基本原理
Appium是一个开源的跨平台自动化测试框架，基于WebDriver协议（与Selenium同源），通过HTTP协议与Appium Server通信，再由Server转发指令给设备。

### 架构图

```
┌─────────────────┐
│   Your Code     │  Python/Java/JS等
│  (WebDriver)    │
└────────┬────────┘
         │ HTTP/JSON
         ▼
┌─────────────────┐
│  Appium Server  │  Node.js服务
│   (端口4723)     │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────┐
│Android │ │  iOS   │
│Driver  │ │Driver  │
└────────┘ └────────┘
```

### 核心概念

```python
# Appium基本使用示例
from appium import webdriver
from appium.options.android import UiAutomator2Options

# 配置Desired Capabilities
caps = {
    "platformName": "Android",
    "appium:platformVersion": "12",
    "appium:deviceName": "Pixel 5",
    "appium:appPackage": "com.example.app",
    "appium:appActivity": ".MainActivity",
    "appium:automationName": "UiAutomator2",  # 或Espresso
    "appium:noReset": False,
}

# 创建Driver（连接到Appium Server）
driver = webdriver.Remote(
    command_executor="http://localhost:4723",
    options=UiAutomator2Options().load_capabilities(caps)
)

# 截图
screenshot = driver.get_screenshot_as_png()

# 查找元素（多种方式）
element = driver.find_element("id", "com.example.app:id/button")
element = driver.find_element("xpath", "//android.widget.Button[@text='Click']")
element = driver.find_element("accessibility id", "submit_button")
element = driver.find_element("class name", "android.widget.EditText")

# 操作元素
element.click()
element.send_keys("Hello World")
element.clear()

# 手势操作
driver.tap([(100, 200)])  # 点击坐标
driver.swipe(100, 500, 100, 100, 500)  # 滑动

# 等待元素（显式等待）
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

wait = WebDriverWait(driver, 10)
element = wait.until(EC.presence_of_element_located(("id", "loading_complete")))

# 获取设备信息
print(driver.device_time)
print(driver.current_package)
print(driver.current_activity)

# 关闭
driver.quit()
```

### Python封装示例（适配我们的Agent框架）

```python
# src/openharness/agents/mobile/appium_perception.py
class AppiumPerceptionProvider(PerceptionProvider):
    """Mobile perception using Appium."""

    def __init__(self, device_config: dict):
        self.device_config = device_config
        self.driver = None

    async def connect(self):
        """Connect to Appium Server."""
        from appium import webdriver

        caps = {
            "platformName": self.device_config.get("platform", "Android"),
            "appium:deviceName": self.device_config.get("device_name"),
            "appium:appPackage": self.device_config.get("app_package"),
            "appium:appActivity": self.device_config.get("app_activity"),
            "appium:automationName": "UiAutomator2",
            "appium:noReset": True,
        }

        self.driver = webdriver.Remote(
            command_executor=self.device_config.get("appium_server", "http://localhost:4723"),
            desired_capabilities=caps
        )

    async def observe(self) -> Observation:
        if not self.driver:
            await self.connect()

        # 1. 截图
        screenshot_png = self.driver.get_screenshot_as_png()
        screenshot = Image.open(io.BytesIO(screenshot_png))

        # 2. 获取当前页面信息
        page_source = self.driver.page_source  # XML格式的UI层次

        # 3. 获取当前应用信息
        current_app = {
            "package": self.driver.current_package,
            "activity": self.driver.current_activity,
        }

        # 4. 获取所有可交互元素（帮助模型理解界面）
        elements = self._get_interactive_elements()

        return Observation(
            text=f"Current app: {current_app}\nElements: {elements}",
            visual=screenshot,
            metadata={
                "page_source": page_source,
                "current_app": current_app,
                "elements": elements,
            }
        )

    def _get_interactive_elements(self) -> list:
        """Get all interactive elements with their properties."""
        elements = []
        try:
            # 查找按钮、输入框等可交互元素
            clickable = self.driver.find_elements("xpath", "//*[@clickable='true']")
            for elem in clickable:
                elements.append({
                    "type": elem.tag_name,
                    "text": elem.text,
                    "location": elem.location,
                    "size": elem.size,
                })
        except:
            pass
        return elements

# src/openharness/agents/mobile/appium_action.py
class AppiumActionExecutor(ActionExecutor):
    """Mobile action execution using Appium."""

    def __init__(self, driver):
        self.driver = driver

    async def execute(self, action: Action) -> ActionResult:
        try:
            if action.type == "tap_by_coordinates":
                x, y = action.coordinates
                self.driver.tap([(x, y)])

            elif action.type == "tap_by_element":
                # 通过元素ID/描述点击（更智能）
                elem = self._find_element(action.element_desc)
                if elem:
                    elem.click()
                else:
                    return ActionResult(success=False, error="Element not found")

            elif action.type == "input_text":
                if action.element_desc:
                    elem = self._find_element(action.element_desc)
                    elem.send_keys(action.text)
                else:
                    # 当前聚焦元素直接输入
                    from appium.webdriver.common.mobileby import MobileBy
                    active = self.driver.switch_to.active_element
                    active.send_keys(action.text)

            elif action.type == "swipe":
                x1, y1, x2, y2 = action.swipe_coords
                duration = action.duration or 500
                self.driver.swipe(x1, y1, x2, y2, duration)

            elif action.type == "keyevent":
                # Appium支持按键
                self.driver.press_keycode(action.keycode)

            elif action.type == "wait_for_element":
                from selenium.webdriver.support.ui import WebDriverWait
                from selenium.webdriver.support import expected_conditions as EC

                wait = WebDriverWait(self.driver, action.timeout or 10)
                elem = wait.until(EC.presence_of_element_located(
                    (action.locator_type, action.locator_value)
                ))

            return ActionResult(success=True)

        except Exception as e:
            return ActionResult(success=False, error=str(e))

    def _find_element(self, desc: dict):
        """Find element by various strategies."""
        # 尝试多种查找策略
        strategies = [
            ("id", desc.get("id")),
            ("xpath", desc.get("xpath")),
            ("accessibility id", desc.get("accessibility_id")),
            ("text", f"//*[@text='{desc.get('text')}']"),
        ]

        for strategy, value in strategies:
            if value:
                try:
                    return self.driver.find_element(strategy, value)
                except:
                    continue
        return None
```

### 优缺点

**优点**：
- ✅ **跨平台**：同时支持Android和iOS
- ✅ **元素定位丰富**：支持ID、XPath、Accessibility ID、Class Name等
- ✅ **智能等待**：原生支持显式/隐式等待元素出现
- ✅ **应用内信息丰富**：可以获取元素文本、属性、状态
- ✅ **生态成熟**：有大量文档和社区支持
- ✅ **与测试框架集成**：易于接入CI/CD流程

**缺点**：
- ❌ **架构复杂**：需要额外启动Appium Server（Node.js服务）
- ❌ **配置复杂**：需要配置Desired Capabilities
- ❌ **启动慢**：建立连接需要几秒时间
- ❌ **资源占用**：Appium Server持续占用内存
- ❌ **依赖稳定性**：Server崩溃会导致所有连接断开
- ❌ **学习曲线**：需要理解WebDriver概念

---

## 对比总结

| 维度 | ADB | HDC | Appium |
|------|-----|-----|--------|
| **复杂度** | ⭐ 低 | ⭐ 低 | ⭐⭐⭐ 高 |
| **启动速度** | ⚡ 快（毫秒） | ⚡ 快 | 🐢 慢（秒级） |
| **配置难度** | 几乎无 | 几乎无 | 需要配置Server |
| **元素定位** | ❌ 仅坐标 | ❌ 仅坐标 | ✅ 丰富的定位方式 |
| **智能等待** | ❌ 需自实现 | ❌ 需自实现 | ✅ 原生支持 |
| **跨平台** | Android | HarmonyOS | Android+iOS |
| **真机支持** | ✅ | ✅ | ✅ |
| **模拟器支持** | ✅ | ✅ | ✅ |
| **应用内信息** | 有限 | 有限 | ✅ 丰富 |
| **稳定性** | ⭐⭐⭐ 高 | ⭐⭐⭐ 高 | ⭐⭐ 中 |
| **资源占用** | 低 | 低 | 高（需Server） |
| **维护成本** | 低 | 低 | 中 |

---

## 推荐方案

### 场景1：快速原型/轻量级控制 → **ADB/HDC**

**推荐理由**：
- 与我们的Agent Loop架构完美契合（简单、低延迟）
- 无需额外基础设施
- 开发和调试成本低

### 场景2：复杂自动化测试/多设备管理 → **Appium**

**推荐理由**：
- 需要跨平台（同时测Android和iOS）
- 需要精确的元素定位和等待机制
- 有专门的测试团队维护基础设施

### 建议的实现策略

**阶段1**：先用ADB/HDC实现基础功能
- 截图 + 坐标点击足以完成大部分任务
- 可以通过OCR+视觉模型弥补元素定位的不足

**阶段2**：根据需求考虑Appium
- 如果确实需要频繁操作特定APP的特定元素
- 如果需要同时支持iOS

**混合方案**（推荐）：
```python
# 主要用ADB实现
# 对于需要精确定位元素的场景，可以结合UI Automator Viewer分析后点击
# 或者使用Appium作为可选的辅助方式

class HybridMobilePerception(PerceptionProvider):
    """Hybrid: Primary ADB + Optional Appium for complex cases."""

    def __init__(self, device_id, use_appium_for_elements=False):
        self.adb = ADBPerceptionProvider(device_id)
        self.appium = None
        self.use_appium = use_appium_for_elements

    async def observe(self) -> Observation:
        # 总是用ADB截图（快）
        obs = await self.adb.observe()

        # 如果需要元素信息，可选Appium
        if self.use_appium:
            # ...获取元素信息
            pass

        return obs
```

---

## 鸿蒙支持计划

由于鸿蒙市场份额增长，建议同时支持HDC：

```python
class MobilePerceptionFactory:
    """Factory to create perception provider based on device type."""

    @staticmethod
    def create(device_type: str, device_id: str) -> PerceptionProvider:
        if device_type == "android":
            return ADBPerceptionProvider(device_id)
        elif device_type == "harmonyos":
            return HDCPerceptionProvider(device_id)
        else:
            raise ValueError(f"Unsupported device type: {device_type}")
```

这样我们的Mobile UI Agent可以同时支持Android和鸿蒙设备。