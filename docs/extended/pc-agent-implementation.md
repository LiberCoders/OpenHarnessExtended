# PC UI Agent 实现方案对比

## 方案概述

| 方案 | 核心库 | 复杂度 | 特点 |
|------|--------|--------|------|
| **MSS + pynput** | mss, pynput | 低 | 轻量级，纯Python，跨平台 |
| **PyAutoGUI** | pyautogui | 低 | 简单易用，功能全面 |
| **Pillow + ctypes** | PIL, ctypes | 中 | 原生Windows API调用 |
| **Playwright/Selenium** | playwright | 中 | 专为浏览器，也可控桌面 |
| **Windows API (pywin32)** | pywin32 | 高 | 功能最强，仅Windows |

---

## 方案1：MSS + pynput（参考实现采用）

### 核心库
- **mss** (Multi-Screen Shot)：超快截图库，支持多显示器
- **pynput**：监听和控制鼠标键盘

### 核心代码

```python
# screenshot.py
import mss
import mss.tools

def capture_screen():
    """Capture primary screen and return PNG bytes."""
    with mss.mss() as sct:
        # sct.monitors[0] - 所有显示器
        # sct.monitors[1] - 主显示器
        screenshot = sct.grab(sct.monitors[1])
        return mss.tools.to_png(screenshot.rgb, screenshot.size)

# action.py
from pynput import mouse, keyboard
from pynput.mouse import Button
from pynput.keyboard import Key

class DesktopController:
    def __init__(self):
        self.mouse = mouse.Controller()
        self.keyboard = keyboard.Controller()
    
    def click(self, x, y, button=Button.left, clicks=1):
        """Click at (x, y)."""
        self.mouse.position = (x, y)
        self.mouse.click(button, clicks)
    
    def move_to(self, x, y, duration=0.5):
        """Smooth move to (x, y)."""
        import time
        start_x, start_y = self.mouse.position
        steps = int(duration * 60)  # 60fps
        
        for i in range(steps + 1):
            t = i / steps
            new_x = start_x + (x - start_x) * t
            new_y = start_y + (y - start_y) * t
            self.mouse.position = (new_x, new_y)
            time.sleep(duration / steps)
    
    def type_text(self, text):
        """Type text."""
        # 方法1：直接输入（可能有输入法问题）
        # self.keyboard.type(text)
        
        # 方法2：剪贴板粘贴（更稳定）
        import subprocess
        import platform
        
        system = platform.system()
        if system == "Darwin":  # macOS
            subprocess.run(["pbcopy"], input=text.encode("utf-8"))
            paste_key = Key.cmd
        elif system == "Windows":
            subprocess.run(["clip"], input=text.encode("utf-16le"))
            paste_key = Key.ctrl
        else:  # Linux
            subprocess.run(["xclip", "-selection", "clipboard"], 
                          input=text.encode("utf-8"))
            paste_key = Key.ctrl
        
        # Ctrl/Cmd + V
        with self.keyboard.pressed(paste_key):
            self.keyboard.press("v")
            self.keyboard.release("v")
    
    def hotkey(self, *keys):
        """Press hotkey combination."""
        # e.g., hotkey(Key.ctrl, "c")
        with self.keyboard.pressed(keys[0]):
            for key in keys[1:]:
                self.keyboard.press(key)
                self.keyboard.release(key)
    
    def scroll(self, dx=0, dy=0):
        """Scroll."""
        self.mouse.scroll(dx, dy)
```

### 优缺点

**优点**：
- ✅ **性能优秀**：mss是C语言优化，截图速度极快（~20ms）
- ✅ **跨平台**：Windows/Mac/Linux都支持
- ✅ **多显示器支持**：自动处理多屏坐标
- ✅ **纯Python**：无需额外依赖，pip安装即可
- ✅ **无侵入性**：不安装驱动，不修改系统

**缺点**：
- ❌ **键盘输入需要窗口聚焦**：pynput的键盘输入会发送到当前焦点窗口（见下方说明）
- ❌ **无法获取UI元素信息**：只能截图，不知道有什么按钮/输入框
- ❌ **坐标硬编码**：不同分辨率需要适配
- ❌ **可能被安全软件拦截**：模拟输入可能被杀毒软件误判

**重要澄清：pynput的窗口聚焦问题**

**误解**："pynput需要窗口在后台"
**实际**：pynput**不**需要窗口在后台，而是键盘输入会进入当前**焦点窗口**

**具体情况**：
1. **鼠标控制**：可以在屏幕任何位置工作，与窗口焦点无关
   - `mouse.position = (x, y)` 移动鼠标到坐标
   - `mouse.click()` 在该位置点击，会自动聚焦该位置的窗口
   
2. **键盘输入**：会发送到当前具有键盘焦点的窗口
   - 如果用户手动切换到了浏览器，键盘输入就会进入浏览器
   - 这不是pynput的限制，而是操作系统的设计

**解决方案**：

```python
# 方案1：先点击目标位置，确保窗口获得焦点，再输入
class PynputActionExecutor:
    async def click_and_type(self, x, y, text):
        # 第一步：点击目标位置（会自动聚焦该窗口）
        self.mouse.position = (x, y)
        self.mouse.click(Button.left)
        time.sleep(0.2)  # 等待窗口聚焦
        
        # 第二步：现在可以安全输入
        self.keyboard.type(text)

# 方案2：使用平台特定API强制聚焦窗口（Windows示例）
import win32gui
import win32con

def focus_window(window_title):
    """Bring window to foreground."""
    hwnd = win32gui.FindWindow(None, window_title)
    if hwnd:
        win32gui.SetForegroundWindow(hwnd)
        return True
    return False

# 使用
focus_window("记事本")
time.sleep(0.3)  # 等待聚焦
keyboard.type("Hello")  # 现在输入会进入记事本

# 方案3：点击任务栏图标（通用方法）
def click_taskbar_icon(app_name):
    """Click taskbar icon to focus app."""
    # 需要截图识别任务栏图标位置，或已知坐标
    # 然后点击该位置
    pass
```

**总结**：
- 鼠标点击可以在任何位置工作
- 键盘输入前需要确保目标窗口有焦点
- 最简单的方法：先点击目标窗口的输入框，再输入
- 这不是严重限制，只是需要正确的操作顺序

---

## 方案2：PyAutoGUI

### 核心库
- **pyautogui**：一站式GUI自动化，截图+控制一体化

### 核心代码

```python
import pyautogui

# 截图
screenshot = pyautogui.screenshot()  # PIL Image
screenshot.save("screen.png")

# 获取屏幕尺寸
width, height = pyautogui.size()

# 点击
pyautogui.click(x=100, y=200)
pyautogui.click(x=100, y=200, clicks=2)  # 双击
pyautogui.rightClick(x=100, y=200)

# 输入
pyautogui.typewrite("Hello World", interval=0.1)  # 逐个输入
pyautogui.write("Hello World")  # 快速输入

# 热键
pyautogui.hotkey("ctrl", "c")
pyautogui.hotkey("ctrl", "shift", "esc")  # 打开任务管理器

# 移动
pyautogui.moveTo(x=100, y=200, duration=0.5)
pyautogui.dragTo(x=300, y=400, duration=0.5)

# 滚动
pyautogui.scroll(500)   # 向上
pyautogui.scroll(-500)  # 向下

# 图像识别定位（高级功能）
button_location = pyautogui.locateOnScreen("button.png")
if button_location:
    center = pyautogui.center(button_location)
    pyautogui.click(center)
```

### 优缺点

**优点**：
- ✅ **API简单**：一行代码完成截图或点击
- ✅ **图像识别**：可以基于图片定位元素
- ✅ **防故障**：鼠标移到屏幕角落可紧急停止
- ✅ **文档丰富**：有大量教程和示例

**缺点**：
- ❌ **性能一般**：截图速度比mss慢（~100ms）
- ❌ **图像识别慢**：模板匹配效率低
- ❌ **依赖Pillow**：需要PIL库
- ❌ **同样无法获取UI元素**：只能截图或图像匹配

---

## 方案3：Windows原生API（pywin32）

### 核心库
- **pywin32**：调用Windows原生API
- **仅Windows**

### 核心代码

```python
import win32gui
import win32con
import win32api
from PIL import ImageGrab

def capture_screen_win32():
    """Windows native screenshot."""
    # 获取主显示器DC
    hwnd = win32gui.GetDesktopWindow()
    hwndDC = win32gui.GetWindowDC(hwnd)
    
    # 获取屏幕尺寸
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top
    
    # 创建内存DC
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    
    # 创建位图
    saveBitMap = win32ui.CreateBitmap()
    saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
    saveDC.SelectObject(saveBitMap)
    
    # 截图
    saveDC.BitBlt((0, 0), (width, height), mfcDC, (left, top), win32con.SRCCOPY)
    
    # 转换为PIL Image
    bmpinfo = saveBitMap.GetInfo()
    bmpstr = saveBitMap.GetBitmapBits(True)
    im = Image.frombuffer(
        "RGB",
        (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
        bmpstr, "raw", "BGRX", 0, 1
    )
    
    # 清理
    win32gui.DeleteObject(saveBitMap.GetHandle())
    saveDC.DeleteDC()
    mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)
    
    return im

# 模拟输入
import win32api
import win32con

def click_win32(x, y):
    """Windows native click."""
    # 移动
    win32api.SetCursorPos((x, y))
    # 点击
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)

def send_keys_win32(text):
    """Send keystrokes using Windows API."""
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    shell.SendKeys(text)
```

### 优缺点

**优点**：
- ✅ **功能最强**：可以操作窗口、获取UI信息、发送消息
- ✅ **可以获取UI元素**：通过Accessibility API获取按钮/输入框
- ✅ **性能好**：原生API调用
- ✅ **后台操作**：可以向非活动窗口发送消息

**缺点**：
- ❌ **仅Windows**：不跨平台
- ❌ **复杂度高**：API繁琐，学习曲线陡
- ❌ **依赖安装**：需要安装pywin32
- ❌ **可能被杀毒软件拦截**：底层API调用

---

## 方案4：Playwright（浏览器为主）

### 核心库
- **playwright**：微软开源的浏览器自动化框架

### 适用场景
主要用于浏览器自动化，但也可以扩展到桌面（有限）。

```python
from playwright.sync_api import sync_playwright

# 主要用于浏览器
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://example.com")
    screenshot = page.screenshot()
    page.click("button#submit")
```

### 桌面应用支持（有限）
Playwright主要用于浏览器，对桌面应用支持有限，不推荐作为通用PC UI方案。

---

## 对比总结

| 维度 | MSS+pynput | PyAutoGUI | Windows API | Playwright |
|------|------------|-----------|-------------|------------|
| **复杂度** | ⭐ 低 | ⭐ 低 | ⭐⭐⭐ 高 | ⭐⭐ 中 |
| **性能** | ⚡ 快 | 🐢 一般 | ⚡ 快 | ⚡ 快 |
| **跨平台** | ✅ Win/Mac/Linux | ✅ Win/Mac/Linux | ❌ 仅Windows | ✅ Win/Mac/Linux |
| **API简洁** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ | ⭐⭐ |
| **UI元素获取** | ❌ 无 | ⚠️ 图像匹配 | ✅ Accessibility | ❌ 无 |
| **多显示器** | ✅ 自动支持 | ⚠️ 需手动处理 | ⚠️ 需手动处理 | N/A |
| **依赖** | 轻量 | 中等 | 重 | 重 |
| **维护成本** | 低 | 低 | 高 | 中 |

---

## 推荐方案

### 第一阶段：MSS + pynput（参考实现）

**推荐理由**：
1. **与参考实现一致**：你看到的代码就是这个方案，已经验证可行
2. **轻量高效**：mss截图速度极快，适合实时Loop
3. **跨平台**：一套代码支持Windows/Mac/Linux
4. **足够使用**：截图+坐标点击+视觉模型，能完成大部分任务

**技术组合**：
```
感知层：mss (截图) + OCR/视觉模型 (理解界面)
行动层：pynput (鼠标/键盘控制)
```

### 第二阶段（可选）：增强UI感知

如果需要更精确的UI元素定位，可以添加：

**方案A：Windows Accessibility API（仅Windows）**
```python
# 获取UI元素信息
import comtypes.client
from comtypes.gen import Accessibility as IA2

# 获取当前窗口的所有按钮/输入框
# 结合截图坐标定位
```

**方案B：OCR + 视觉模型**
```python
# 截图后使用OCR识别文字
# 使用GPT-4V等视觉模型识别可点击元素
# 无需原生API，纯AI方案
```

**方案C：混合方案**
- 主要用MSS+pynput（跨平台）
- Windows平台可选增强：用pywin32获取UI元素辅助

---

## 实现代码结构

```python
# src/openharness/agents/pc/mss_pynput_perception.py
class MSSPerceptionProvider(PerceptionProvider):
    """PC perception using MSS for screenshot."""

    def __init__(self, monitor_index=1):  # 1=primary, 0=all
        self.monitor_index = monitor_index
        self.scale_factor = self._calculate_scale()

    async def observe(self) -> Observation:
        # 截图
        with mss.mss() as sct:
            screenshot = sct.grab(sct.monitors[self.monitor_index])
            png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)

        return Observation(
            text="",  # 视觉模型自己理解
            visual=Image.open(io.BytesIO(png_bytes)),
            metadata={
                "screen_size": (screenshot.width, screenshot.height),
                "scale_factor": self.scale_factor,
            }
        )

    def _calculate_scale(self):
        """Calculate scale factor for coordinate conversion."""
        with mss.mss() as sct:
            monitor = sct.monitors[self.monitor_index]
            # 如果模型输出基于1920x1080，需要缩放
            return {
                "x": monitor["width"] / 1920,
                "y": monitor["height"] / 1080,
            }

# src/openharness/agents/pc/pynput_action.py
class PynputActionExecutor(ActionExecutor):
    """PC action execution using pynput."""

    def __init__(self, scale_factor=None):
        self.mouse = mouse.Controller()
        self.keyboard = keyboard.Controller()
        self.scale = scale_factor or {"x": 1, "y": 1}

    async def execute(self, action: Action) -> ActionResult:
        try:
            if action.type == "click":
                x = int(action.coordinates[0] * self.scale["x"])
                y = int(action.coordinates[1] * self.scale["y"])
                button = Button.left if action.button == "left" else Button.right
                self.mouse.position = (x, y)
                self.mouse.click(button, action.clicks or 1)

            elif action.type == "type":
                await self._type_via_clipboard(action.text)

            elif action.type == "hotkey":
                await self._press_hotkey(action.keys)

            elif action.type == "move":
                x = int(action.coordinates[0] * self.scale["x"])
                y = int(action.coordinates[1] * self.scale["y"])
                await self._smooth_move(x, y, action.duration or 0.5)

            return ActionResult(success=True)

        except Exception as e:
            return ActionResult(success=False, error=str(e))

    async def _type_via_clipboard(self, text: str):
        """Type text via clipboard (most reliable)."""
        # 保存原剪贴板内容
        import pyperclip
        original = pyperclip.paste()

        # 设置新内容并粘贴
        pyperclip.copy(text)
        with self.keyboard.pressed(Key.ctrl):
            self.keyboard.press("v")
            self.keyboard.release("v")

        # 恢复（可选）
        # pyperclip.copy(original)
```

---

## 与参考实现的对比

| 方面 | 参考实现 | 建议实现 |
|------|----------|----------|
| 截图 | ✅ mss | ✅ 相同 |
| 控制 | ✅ pynput | ✅ 相同 |
| 坐标缩放 | ✅ 支持 | ✅ 相同 |
| 平滑移动 | ✅ 实现 | ✅ 复用 |
| 剪贴板输入 | ✅ 实现 | ✅ 复用 |
| 跨平台 | ✅ 支持 | ✅ 相同 |
| 应用启动 | ✅ 实现 | ✅ 复用 |
| **Loop集成** | ❌ 无 | ✅ 适配UnifiedAgentLoop |
| **感知抽象** | ❌ 无 | ✅ PerceptionProvider |
| **行动抽象** | ❌ 无 | ✅ ActionExecutor |

**结论**：参考实现的底层技术（mss+pynput）非常合适，只需要包装成我们的Agent Loop架构即可。

---

## 实施建议

1. **直接复用参考实现的核心逻辑**：
   - `computer_action_executor.py` 中的 `_mouse_move`, `_do_click`, `_type_text` 等方法
   - `computer_use_util.py` 中的 `screenshot_to_bytes`

2. **适配到我们的架构**：
   - 包装成 `MSSPerceptionProvider`
   - 包装成 `PynputActionExecutor`
   - 集成到 `UnifiedAgentLoop`

3. **坐标系统一**：
   - 模型输出基于1920x1080标准分辨率
   - 实际执行时按屏幕比例缩放
   - 参考实现中的 `_xy` 方法已处理

4. **跨平台测试**：
   - Windows：主要目标
   - macOS：使用 `pbcopy` 和 AppleScript
   - Linux：使用 `xclip`
   - 参考实现已包含这些处理