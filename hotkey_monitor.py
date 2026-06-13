"""
热键监控 + AI 翻译 + 火山引擎朗读 + 歌词字幕渐变
- 监听 Ctrl+0，复制所选内容，识别来源应用
- DeepSeek API 翻译：中文 ↔ 英语
- 火山引擎 TTS 朗读
- 桌面悬浮窗逐词卡拉OK渐变高亮
"""

import atexit
import base64
import configparser
import ctypes
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import uuid
from concurrent.futures import ThreadPoolExecutor

import win32api
import win32clipboard
import win32con
import win32gui
import win32process
import psutil
import requests
import pygame
from openai import OpenAI

# ========== 隐藏控制台窗口（PyInstaller 兼容）==========
if getattr(sys, 'frozen', False):
    try:
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:
        pass

# ========== 剪贴板操作（纯 Win32 API，不依赖外部进程）==========
def _clipboard_set_text(text):
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()

def _clipboard_get_text():
    win32clipboard.OpenClipboard()
    try:
        data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return data
    except:
        return ""
    finally:
        win32clipboard.CloseClipboard()

# ========== 路径兼容（源码 / PyInstaller 打包）==========
def _app_root():
    """返回 exe 所在目录（打包后）或脚本所在目录（源码）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ========== 配置文件加载 ==========
_CONFIG_PATH = os.path.join(_app_root(), "config.ini")

_config = configparser.ConfigParser()
_config.read(_CONFIG_PATH, encoding="utf-8")


def _get_cfg(section, key, fallback=""):
    """从 config.ini 读取配置，不存在则返回 fallback"""
    try:
        return _config.get(section, key)
    except (configparser.NoSectionError, configparser.NoOptionError):
        return fallback


# ========== DeepSeek API ==========
DEEPSEEK_API_KEY = _get_cfg("DEEPSEEK", "api_key", "")
DEEPSEEK_BASE_URL = _get_cfg("DEEPSEEK", "base_url",
                              "https://api.deepseek.com")
DEEPSEEK_MODEL = _get_cfg("DEEPSEEK", "model", "deepseek-v4-pro")

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
executor = ThreadPoolExecutor(max_workers=3)

# ========== 火山引擎 TTS ==========
VOLCANO_TOKEN = _get_cfg("VOLCANO_TTS", "token", "")
VOLCANO_APPID = _get_cfg("VOLCANO_TTS", "appid", "")
VOLCANO_HOST = "openspeech.bytedance.com"
TTS_VOICE = _get_cfg("SETTINGS", "voice", "zh_female_sophie_uranus_bigtts")

pygame.mixer.init()

# ========== 全局热键（Win32 API，无需管理员权限）==========
WM_HOTKEY = 0x0312
_hk_callbacks = {}
_hk_proc = None
_hk_old = ctypes.c_void_p(0)

# ★ 显式声明 CallWindowProcW 的参数类型，避免 64 位下 ctypes 默认推断为 32 位 int
_ctypes_user32 = ctypes.windll.user32
_ctypes_user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p,   # WNDPROC lpPrevWndFunc
    ctypes.c_size_t,   # HWND    hWnd
    ctypes.c_uint,     # UINT    Msg
    ctypes.c_size_t,   # WPARAM  wParam
    ctypes.c_size_t,   # LPARAM  lParam
]
_ctypes_user32.CallWindowProcW.restype = ctypes.c_long


def register_hotkeys(root, items):
    """注册全局热键。items = [(id, modifiers, vk, callback), ...]
    modifiers: win32con.MOD_CONTROL / MOD_ALT / MOD_SHIFT 组合
    """
    global _hk_proc, _hk_old
    hwnd = _ctypes_user32.GetParent(root.winfo_id())
    for hkid, mod, vk, cb in items:
        _hk_callbacks[hkid] = cb
        _ctypes_user32.RegisterHotKey(hwnd, hkid, mod, vk)
    # 子类化窗口过程（只做一次）
    if _hk_proc is None:
        # 窗口过程类型
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_size_t, ctypes.c_uint,
            ctypes.c_size_t, ctypes.c_size_t)
        def _wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY and wparam in _hk_callbacks:
                _hk_callbacks[wparam]()
                return 0
            return _ctypes_user32.CallWindowProcW(_hk_old, hwnd, msg, wparam, lparam)
        _hk_proc = WNDPROC(_wndproc)
        _ctypes_user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        _hk_old = _ctypes_user32.SetWindowLongPtrW(hwnd, -4, _hk_proc)


def unregister_hotkeys(root):
    """注销所有全局热键"""
    hwnd = _ctypes_user32.GetParent(root.winfo_id())
    for hkid in list(_hk_callbacks.keys()):
        try:
            _ctypes_user32.UnregisterHotKey(hwnd, hkid)
        except Exception:
            pass
    _hk_callbacks.clear()

# ============================================================
#  翻译历史记录（持久化到文件，重启不丢失）
# ============================================================
_HISTORY = []
_HISTORY_MAX = 20
_HISTORY_PATH = os.path.join(_app_root(), "translate_history.json")


def _load_history():
    """启动时从文件加载历史记录"""
    global _HISTORY
    try:
        if os.path.exists(_HISTORY_PATH):
            with open(_HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                _HISTORY = data[:_HISTORY_MAX]
    except Exception:
        _HISTORY = []


def _save_history():
    """保存历史记录到文件"""
    try:
        with open(_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(_HISTORY, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _record_history(source: str, spoken: str):
    """记录一条翻译历史"""
    entry = {
        "source": source,
        "text": spoken,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _HISTORY.insert(0, entry)
    if len(_HISTORY) > _HISTORY_MAX:
        _HISTORY.pop()
    _save_history()


# 模块加载时自动恢复历史
_load_history()


def _show_history_window():
    """显示历史翻译记录窗口"""
    win = tk.Toplevel()
    win.title("翻译历史记录")
    win.withdraw()  # 先隐藏，等定位完成再显示
    win.configure(bg=C_BG)
    win.attributes("-topmost", True)

    # 标题栏
    tk.Label(win, text="翻译历史记录",
             font=(FONT_FAMILY, 13, "bold"),
             fg="#cccccc", bg=C_BG, pady=10).pack()

    if not _HISTORY:
        tk.Label(win, text="暂无记录", font=(FONT_FAMILY, 11),
                 fg="#707070", bg=C_BG, pady=40).pack()
    else:
        # 用 Text 做可滚动内容区，每条的译文用 tag 标记为可点击
        text_widget = tk.Text(win, font=(FONT_FAMILY, 10),
                              fg="#cccccc", bg=C_BG,
                              wrap=tk.WORD, relief=tk.FLAT, borderwidth=0,
                              padx=10, pady=6, cursor="arrow",
                              state=tk.DISABLED)
        scrollbar = tk.Scrollbar(win, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=(0, 10))
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 10))

        # tag_name → {source, text}，用于点击回放
        replay_map = {}

        text_widget.config(state=tk.NORMAL)
        for i, entry in enumerate(_HISTORY):
            tag_name = f"entry_{i}"
            replay_map[tag_name] = entry

            # 时间 + 原文
            text_widget.insert(tk.END, f"🕐 {entry['time']}  |  ", "meta")
            text_widget.insert(tk.END, f"{entry['source'][:60]}\n", "meta")
            # 译文 / 朗读文本（可点击回放）
            text_widget.insert(tk.END, f"{entry['text']}\n", (tag_name, "entry"))
            text_widget.insert(tk.END, "\n")

        text_widget.tag_configure("meta", font=(FONT_FAMILY, 9),
                                  foreground="#909090")
        text_widget.tag_configure("entry", font=(FONT_FAMILY, 11),
                                  foreground="#cccccc")

        # 为每条译文创建可点击 tag：点击 → 显示面板 + 朗读
        for tag_name, entry in replay_map.items():
            def make_handler(e=entry, tn=tag_name):
                def _on_click(event=None):
                    win.destroy()
                    show_subtitle_safe(e["source"], e["text"])
                    speak(e["text"])
                    return "break"
                return _on_click
            text_widget.tag_bind(tag_name, "<Button-1>", make_handler())
            # 悬停高亮 + 手型光标
            text_widget.tag_bind(tag_name, "<Enter>",
                lambda e, tn=tag_name, tw=text_widget: (
                    tw.tag_configure(tn, foreground="#ffffff"),
                    tw.config(cursor="hand2")))
            text_widget.tag_bind(tag_name, "<Leave>",
                lambda e, tn=tag_name, tw=text_widget: (
                    tw.tag_configure(tn, foreground="#cccccc"),
                    tw.config(cursor="arrow")))

        text_widget.config(state=tk.DISABLED)

        # 鼠标滚轮
        def _on_mousewheel(event):
            text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
        text_widget.bind("<MouseWheel>", _on_mousewheel)

    # 窗口居中
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    ww, wh = 500, 400
    win.geometry(f"{ww}x{wh}+{(sw - ww) // 2}+{(sh - wh) // 2}")
    win.deiconify()
    win.focus_set()


# ============================================================
#  TTS 缓存 —— 相同文本只合成一次，省 API 费用
# ============================================================
CACHE_DIR = os.path.join(_app_root(), "tts_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(text: str) -> str:
    """文本 → MD5 文件名（不含扩展名）"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _cache_get(text: str):
    """命中返回 (audio_bytes, tokens, timelines, total_ms)，否则返回 None"""
    key = _cache_key(text)
    mp3_path = os.path.join(CACHE_DIR, f"{key}.mp3")
    json_path = os.path.join(CACHE_DIR, f"{key}.json")
    if os.path.exists(mp3_path) and os.path.exists(json_path):
        try:
            with open(mp3_path, "rb") as f:
                audio = f.read()
            with open(json_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            return audio, meta["tokens"], meta["timelines"], meta["total_ms"]
        except Exception:
            # 缓存损坏，清除
            for p in (mp3_path, json_path):
                try:
                    os.remove(p)
                except Exception:
                    pass
    return None


def _cache_put(text: str, audio: bytes, tokens, timelines, total_ms):
    """写入缓存"""
    key = _cache_key(text)
    mp3_path = os.path.join(CACHE_DIR, f"{key}.mp3")
    json_path = os.path.join(CACHE_DIR, f"{key}.json")
    try:
        with open(mp3_path, "wb") as f:
            f.write(audio)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"tokens": tokens, "timelines": timelines,
                        "total_ms": total_ms}, f, ensure_ascii=False)
    except Exception:
        pass


def _cache_stats() -> tuple[int, int]:
    """返回 (文件数, 总大小MB)"""
    files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".mp3")]
    total = sum(os.path.getsize(os.path.join(CACHE_DIR, f)) for f in files)
    return len(files), round(total / 1024 / 1024, 2)


# ============================================================
#  火山引擎 TTS + 词级时间线估算
# ============================================================
def _volcano_tts(text: str, voice: str = None) -> bytes:
    if voice is None:
        voice = TTS_VOICE
    req = {
        "app": {"appid": VOLCANO_APPID, "token": VOLCANO_TOKEN, "cluster": "volcano_tts"},
        "user": {"uid": "default_user"},
        "audio": {
            "voice_type": voice, "encoding": "mp3",
            "speed_ratio": 1.0, "volume_ratio": 1.0, "pitch_ratio": 1.0,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text, "text_type": "plain", "operation": "query",
        },
    }
    resp = requests.post(
        f"https://{VOLCANO_HOST}/api/v1/tts",
        json=req,
        headers={"Authorization": f"Bearer;{VOLCANO_TOKEN}",
                 "Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    result = resp.json()
    if "data" not in result:
        raise Exception(f"TTS failed: {result}")
    return base64.b64decode(result["data"])


def _get_ffprobe_path() -> str:
    """优先使用本地 ffmpeg/ 目录下的 ffprobe，否则走系统 PATH"""
    local = os.path.join(_app_root(),
                         "ffmpeg", "ffprobe.exe")
    if os.path.exists(local):
        return local
    return "ffprobe"


def _get_audio_duration_ms(mp3_bytes: bytes) -> float:
    tmp = os.path.join(tempfile.gettempdir(), f"dur_{uuid.uuid4().hex[:8]}.mp3")
    try:
        with open(tmp, "wb") as f:
            f.write(mp3_bytes)
        ffprobe = _get_ffprobe_path()
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", tmp],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return float(json.loads(r.stdout)["format"]["duration"]) * 1000
    except Exception:
        return 0
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _split_english_words(text: str):
    tokens = re.findall(r"\S+\s*", text)
    if not tokens:
        tokens = [text]
    lengths = [len(re.sub(r"[^a-zA-Z0-9]", "", t)) + 1 for t in tokens]
    return tokens, lengths, sum(lengths)


def _get_word_timeline(text: str, audio_bytes: bytes):
    tokens, lengths, total_len = _split_english_words(text)
    total_dur = _get_audio_duration_ms(audio_bytes)
    if total_dur <= 0:
        total_dur = len(text) * 80
    timelines = []
    elapsed = 0.0
    for l in lengths:
        dur = total_dur * (l / max(total_len, 1))
        timelines.append((elapsed, elapsed + dur))
        elapsed += dur
    return tokens, timelines, total_dur


# ============================================================
#  TTS 播放 + 动画联动（队列 + 缓存）
# ============================================================
_tts_queue = queue.Queue()
_tts_ready = threading.Event()
_playback_lock = threading.Lock()

_last_tts = {"audio": None, "tokens": [], "timelines": [], "total_ms": 0}


def _tts_worker():
    _tts_ready.set()
    while True:
        item = _tts_queue.get()
        if item is None:
            break
        text, voice = item if isinstance(item, tuple) else (item, None)
        try:
            # --- 查缓存 ---
            cached = _cache_get(text)
            if cached:
                audio, tokens, timelines, total_dur = cached
                cache_count, cache_mb = _cache_stats()
                print(f"   Cache hit ({len(text)} chars)  |  "
                      f"cache: {cache_count} files / {cache_mb} MB")
            else:
                print(f"   TTS synthesizing ({len(text)} chars)...")
                audio = _volcano_tts(text, voice)
                tokens, timelines, total_dur = _get_word_timeline(text, audio)
                _cache_put(text, audio, tokens, timelines, total_dur)

            _last_tts["audio"] = audio
            _last_tts["tokens"] = tokens
            _last_tts["timelines"] = timelines
            _last_tts["total_ms"] = total_dur

            tmp = os.path.join(tempfile.gettempdir(),
                               f"tts_{uuid.uuid4().hex[:8]}.mp3")
            with open(tmp, "wb") as f:
                f.write(audio)

            _notify_playback_start(tokens, timelines, total_dur)

            with _playback_lock:
                try:
                    pygame.mixer.music.load(tmp)
                    pygame.mixer.music.play()
                except Exception:
                    pass

            clock = pygame.time.Clock()
            tick = 0
            while pygame.mixer.music.get_busy():
                clock.tick(30)
                tick += 1
                _notify_anim_tick(tick * (1000 / 30))

            time.sleep(0.3)
            _notify_playback_end()

            try:
                os.remove(tmp)
            except Exception:
                pass
        except Exception as e:
            print(f"   TTS error: {e}")
            _notify_playback_end()


_tts_thread = threading.Thread(target=_tts_worker, daemon=True)
_tts_thread.start()
_tts_ready.wait()


def _notify_playback_start(tokens, timelines, total_ms):
    try:
        ov = get_overlay()
        if ov.root:
            ov.root.after(0, lambda: ov.start_playback(tokens, timelines, total_ms))
    except Exception:
        pass


def _notify_anim_tick(elapsed_ms):
    try:
        ov = get_overlay()
        if ov.root:
            ov.root.after(0, lambda: ov.update_highlight(elapsed_ms))
    except Exception:
        pass


def _notify_playback_end():
    try:
        ov = get_overlay()
        if ov.root:
            ov.root.after(0, ov.playback_done)
    except Exception:
        pass


def replay_last():
    if _last_tts["audio"] is None or not _last_tts["tokens"]:
        print("   nothing to replay")
        return
    print("   replaying...")
    _notify_playback_start(_last_tts["tokens"], _last_tts["timelines"],
                           _last_tts["total_ms"])

    def _replay():
        tmp = os.path.join(tempfile.gettempdir(),
                           f"tts_replay_{uuid.uuid4().hex[:8]}.mp3")
        with open(tmp, "wb") as f:
            f.write(_last_tts["audio"])
        try:
            with _playback_lock:
                try:
                    pygame.mixer.music.load(tmp)
                    pygame.mixer.music.play()
                except Exception:
                    pass
            clock = pygame.time.Clock()
            tick = 0
            while pygame.mixer.music.get_busy():
                clock.tick(30)
                tick += 1
                _notify_anim_tick(tick * (1000 / 30))
            time.sleep(0.3)
            _notify_playback_end()
            try:
                os.remove(tmp)
            except Exception:
                pass
        except Exception as e:
            print(f"   replay error: {e}")
            _notify_playback_end()

    threading.Thread(target=_replay, daemon=True).start()


def speak(text: str, voice: str = None):
    if not text or not text.strip():
        return
    speak_text = text if len(text) <= 2000 else text[:2000]
    while not _tts_queue.empty():
        try:
            _tts_queue.get_nowait()
        except queue.Empty:
            break
    _tts_queue.put((speak_text, voice))


# ============================================================
def get_active_app_info():
    try:
        hwnd = win32gui.GetForegroundWindow()
        window_title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            p = psutil.Process(pid)
            process_name = p.name()
            exe_path = p.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            process_name = "unknown"
            exe_path = "unknown"
        return {"window_title": window_title, "process_name": process_name,
                "process_id": pid, "exe_path": exe_path}
    except Exception as e:
        return {"error": str(e)}


def copy_selected_text():
    try:
        _clipboard_set_text("")
        time.sleep(0.05)
        # 使用 win32 keybd_event 模拟 Ctrl+C（无需管理员权限）
        win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
        win32api.keybd_event(ord('C'), 0, 0, 0)
        time.sleep(0.05)
        win32api.keybd_event(ord('C'), 0, win32con.KEYEVENTF_KEYUP, 0)
        win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)
        text = _clipboard_get_text()
        if not text.strip():
            time.sleep(0.15)
            text = _clipboard_get_text()
        return text
    except Exception as e:
        return f"[copy failed: {e}]"


def detect_language(text: str) -> str:
    cn = len(re.findall(r"[一-鿿]", text))
    en = len(re.findall(r"[a-zA-Z]", text))
    total = cn + en
    if total == 0:
        return "other"
    if cn / max(total, 1) > 0.3:
        return "chinese"
    if en / max(total, 1) > 0.3:
        return "english"
    return "other"


# ★ 翻译结果缓存：相同原文 + 相同语言方向 → 跳过 DeepSeek API
_TRANSLATION_CACHE = {}  # key: MD5(lang:text) → translation


def translate_text(text: str, source_lang: str) -> str:
    # 查翻译缓存
    cache_key = hashlib.md5(f"{source_lang}:{text}".encode("utf-8")).hexdigest()
    if cache_key in _TRANSLATION_CACHE:
        print(f"         Translate cache hit ({source_lang})")
        return _TRANSLATION_CACHE[cache_key]

    if source_lang == "chinese":
        sys_p = ("You are a professional translator. Translate the Chinese input "
                 "into natural, idiomatic English. Output ONLY the translation.")
        usr_p = f"Translate this Chinese to English:\n\n{text}"
        target = "EN"
    elif source_lang == "english":
        sys_p = ("你是一个专业的翻译助手。将英文翻译成地道自然的中文。只输出翻译结果。")
        usr_p = f"将以下英文翻译成中文：\n\n{text}"
        target = "CN"
    else:
        sys_p = ("Detect language and translate: Chinese->English, English->Chinese, "
                 "others->both. Output translation only.")
        usr_p = f"Translate:\n\n{text}"
        target = "auto"

    try:
        print(f"         Translating [{target}] ...")
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "system", "content": sys_p},
                      {"role": "user", "content": usr_p}],
            temperature=0.3, max_tokens=4096,
        )
        result = resp.choices[0].message.content.strip()
        _TRANSLATION_CACHE[cache_key] = result
        return result
    except Exception as e:
        return f"[translation failed: {e}]"


# ============================================================
#  圆角按钮 Canvas 组件
# ============================================================
FONT_FAMILY = "Microsoft YaHei UI"
C_BG = "#1e1e1e"  # 中性深灰（之前 #1e1e2e 偏蓝）


class RoundedButton(tk.Canvas):
    """简洁药丸按钮：纯色填充，无边线，hover 变亮"""

    def __init__(self, parent, text="Replay", command=None, **kw):
        self._btn_w = 62
        self._btn_h = 24
        super().__init__(parent, width=self._btn_w, height=self._btn_h,
                         bg=C_BG, highlightthickness=0, cursor="hand2", **kw)
        self._text = text
        self._command = command
        self._hovered = False
        self._active = False
        self.after(1, self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _draw(self):
        self.delete("all")
        w, h = self._btn_w, self._btn_h
        r = h // 2

        if self._hovered:
            fill, text_fill = "#2a5a2a", "#FFFFFF"
        elif self._active:
            fill, text_fill = "#1e3a1e", "#4cff8d"
        else:
            fill, text_fill = "#141f14", "#4cff8d"

        # solid pill — no outline, just fill
        d = 2 * r
        self.create_oval(0, 0, d, h, fill=fill, outline="")
        self.create_oval(w - d, 0, w, h, fill=fill, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=fill, outline="")

        self.create_text(w // 2, h // 2, text=self._text,
                         fill=text_fill,
                         font=(FONT_FAMILY, 10, "bold"))

    def _on_enter(self, e):
        self._hovered = True
        self._draw()

    def _on_leave(self, e):
        self._hovered = False
        self._draw()

    def _on_click(self, e):
        if self._command:
            self._command()

    def set_active(self, active: bool):
        self._active = active
        self._draw()


# ============================================================
#  歌词卡拉OK 悬浮窗
# ============================================================
C_DIM_TEXT    = "#505050"
C_ACTIVE_TEXT = "#FFFFFF"
C_DONE_TEXT   = "#4cff8d"
C_GLOW_BG     = "#1a3a1a"


class SubtitleOverlay:
    """
    桌面歌词悬浮窗：
    - 第1行：原文（暗灰）
    - 第2行：译文（灰色）              【重放按钮】
    - 第3行：卡拉OK逐词渐变色（灰 -> 白 -> 绿）
    - 鼠标悬停 = 暂停淡出，离开后重新3s倒计时
    """

    def __init__(self):
        self._tokens = []
        self._timelines = []
        self._total_ms = 0
        self._current_idx = -1
        self._playing = False
        self._fade_job = None
        self._alpha = 1.0
        self._fading = False
        self._hovered = False
        self._pinned = False
        self._karaoke_text = ""  # 缓存卡拉OK文本，供复制按钮使用
        self._lock = threading.RLock()
        self._create_window()

    # ---- 窗口骨架 ----
    def _create_window(self):
        self.root = tk.Tk()
        self.root.title("trans-subtitle")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 1.0)
        self.root.configure(bg=C_BG)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self._win_w = min(920, sw - 40)
        self._win_h = 220  # 增加高度以容纳译文行
        x = (sw - self._win_w) // 2
        y = int(sh * 0.65)
        self.root.geometry(f"{self._win_w}x{self._win_h}+{x}+{y}")
        self.root.attributes("-alpha", 0.0)  # 初始化期间透明，避免闪屏

        # -- row 1: 左上角图钉 + 原文 + 右上角 replay，同一行 --
        row1 = tk.Frame(self.root, bg=C_BG, height=48)
        row1.pack(fill=tk.X, padx=14, pady=(12, 0))
        row1.pack_propagate(False)

        # 图钉按钮（左上角）
        self.btn_pin = tk.Label(
            row1, text="  📌  ",
            font=(FONT_FAMILY, 11), fg="#505050", bg=C_BG,
            cursor="hand2",
        )
        self.btn_pin.pack(side=tk.LEFT, pady=(2, 0))
        self.btn_pin.bind("<ButtonRelease-1>", self._on_pin_toggle)

        # 标签可用宽度 = 窗口宽 - 左右 padding - 按钮宽 - 图钉宽 - 间距
        self._label_wrap = self._win_w - 28 - 70 - 54 - 8

        self.label_src = tk.Label(
            row1, text="",
            font=(FONT_FAMILY, 12), fg="#b0b0b0", bg=C_BG,
            anchor="w", justify="left",
            wraplength=self._label_wrap,
        )
        self.label_src.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=(2, 0))

        self.btn_replay = RoundedButton(
            row1, text="Replay",
            command=self._on_replay,
        )
        self.btn_replay.pack(side=tk.RIGHT)

        # -- row 1.5: 译文 --
        self.label_trans = tk.Label(
            self.root, text="  译文将显示在这里",
            font=(FONT_FAMILY, 14), fg="#505050", bg=C_BG,
            anchor="w", justify="left",
            wraplength=self._label_wrap,
        )
        self.label_trans.pack(fill=tk.X, padx=14, pady=(4, 0))

        # -- row 2: 卡拉OK歌词 + 复制按钮 --
        row2 = tk.Frame(self.root, bg=C_BG)
        row2.pack(fill=tk.BOTH, expand=True, padx=14, pady=(5, 10))

        self.lyric_text = tk.Text(
            row2,
            font=(FONT_FAMILY, 20, "bold"),
            fg=C_DIM_TEXT, bg=C_BG,
            wrap=tk.WORD, relief=tk.FLAT, borderwidth=0,
            height=3, width=64, padx=24, pady=4,
            state=tk.DISABLED,
        )
        self.lyric_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lyric_text.tag_configure("dim", foreground=C_DIM_TEXT)
        self.lyric_text.tag_configure("active", foreground=C_ACTIVE_TEXT,
                                       background=C_GLOW_BG)
        self.lyric_text.tag_configure("done", foreground=C_DONE_TEXT)
        self.lyric_text.tag_configure("placeholder", foreground="#404040",
                                       font=(FONT_FAMILY, 16))

        # 初始水印占位
        self.lyric_text.config(state=tk.NORMAL)
        self.lyric_text.insert("1.0", "暂无翻译内容哦，请先 Ctrl + 0 使用翻译", "placeholder")
        self.lyric_text.config(state=tk.DISABLED)

        # 复制按钮（卡拉OK右侧）
        self.btn_copy = tk.Label(
            row2, text=" 📋 ",
            font=(FONT_FAMILY, 11), fg="#505050", bg=C_BG,
            cursor="hand2",
        )
        self.btn_copy.pack(side=tk.RIGHT, anchor="n", pady=(4, 0))
        self.btn_copy.bind("<ButtonRelease-1>", self._on_copy)
        self.btn_copy.bind("<Enter>", lambda e: self.btn_copy.config(fg="#4cff8d"))
        self.btn_copy.bind("<Leave>", lambda e: self.btn_copy.config(fg="#505050"))

        # -- 鼠标事件 --
        # 拖拽区域：仅顶部原文行（不含按钮，避免冲突）
        drag_w = [row1, self.label_src, self.label_trans]
        # 悬停区域：整个窗口（鼠标悬停暂停淡出）
        hover_w = [self.root, row1, row2,
                   self.label_src, self.label_trans, self.lyric_text,
                   self.btn_pin, self.btn_replay, self.btn_copy]
        for w in drag_w:
            w.bind("<ButtonPress-1>", self._on_drag_start)
            w.bind("<B1-Motion>", self._on_drag_move)
            w.bind("<ButtonRelease-1>", self._on_drag_stop)
        for w in hover_w:
            w.bind("<Enter>", self._on_mouse_enter)
            w.bind("<Leave>", self._on_mouse_leave)

        # ★ 所有 widget 创建完成后，先做初始布局再隐藏
        #    避免 withdrawn 状态下 widget 尺寸为 0，导致后续 deiconify 渲染空白
        self.root.update()
        self._apply_rounded_corners()
        self.root.withdraw()

    def _apply_rounded_corners(self):
        """Windows 11 原生圆角。Win10 静默忽略。"""
        try:
            # Tk 窗口在 Windows 上有一个 wrapper 顶层窗口；DWM 属性需要设在它上面
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            DWMWA = 33   # DWMWA_WINDOW_CORNER_PREFERENCE
            ROUND = 2    # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), DWMWA,
                ctypes.byref(ctypes.c_int(ROUND)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            pass

    # ---- 公开接口 ----
    def show(self, source_text: str, translated_text: str = ""):
        """显示原文 + 译文，清空歌词区。"""
        s = source_text[:150] + "..." if len(source_text) > 150 else source_text
        self.label_src.config(text=f"  {s}")
        # 显示译文
        t = translated_text[:200] + "..." if len(translated_text) > 200 else translated_text
        self.label_trans.config(text=f"  {t}" if t else "  (翻译中...)", fg="#b0b0b0")
        self._reset_lyric_area()
        self.root.update_idletasks()
        self.root.update()  # ★ 确保 label 文本变更立即生效
        self._show_window()

    def _reset_lyric_area(self):
        self.lyric_text.config(state=tk.NORMAL)
        self.lyric_text.delete("1.0", tk.END)
        # 水印占位文字
        self.lyric_text.insert("1.0", "暂无翻译内容哦，请先 Ctrl + 0 使用翻译", "placeholder")
        self.lyric_text.tag_configure("placeholder", foreground="#404040",
                                       font=(FONT_FAMILY, 16))
        self.lyric_text.config(state=tk.DISABLED)
        self._tokens, self._timelines = [], []
        self._current_idx = -1
        self._playing = False
        self._karaoke_text = ""
        self.btn_replay.set_active(False)

    def _show_window(self):
        self._alpha = 1.0
        self.root.attributes("-alpha", 1.0)
        self.root.deiconify()
        self.root.lift()
        self.root.update_idletasks()
        self.root.update()
        # ★ 强制 Windows 重绘整个窗口（解决 overrideredirect 窗口渲染空白问题）
        try:
            hwnd = self.root.winfo_id()
            ctypes.windll.user32.RedrawWindow(
                ctypes.c_void_p(hwnd), None, 0,
                0x0001 | 0x0002 | 0x0400 | 0x0080
                # RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_ALLCHILDREN
            )
        except Exception:
            pass
        # 第二次 update 确保 RedrawWindow 触发的绘制消息被处理
        self.root.update()
        self._cancel_fade()

    def _on_replay(self):
        # 正在朗读时不响应 replay，避免重叠播放
        if self._playing or pygame.mixer.music.get_busy():
            return
        self._cancel_fade()
        self._show_window()
        threading.Thread(target=replay_last, daemon=True).start()

    def _on_copy(self, event=None):
        """复制卡拉OK文本到剪贴板，带瞬间绿色反馈。"""
        if not self._karaoke_text:
            return
        _clipboard_set_text(self._karaoke_text)
        self.btn_copy.config(fg="#4cff8d")
        self.root.after(800, lambda: self.btn_copy.config(fg="#505050"))

    # ---- 播放驱动 ----
    def start_playback(self, tokens, timelines, total_ms):
        self._tokens = tokens
        self._timelines = timelines
        self._total_ms = total_ms
        self._current_idx = -1
        self._playing = True
        self._karaoke_text = "".join(tokens)  # 供复制按钮使用

        self.lyric_text.config(state=tk.NORMAL)
        self.lyric_text.delete("1.0", tk.END)
        for i, tok in enumerate(tokens):
            tag = f"w{i}"
            self.lyric_text.tag_configure(tag, foreground=C_DIM_TEXT, background="")
            self.lyric_text.insert(tk.END, tok, tag)
        self.lyric_text.config(state=tk.DISABLED)
        self._show_window()

    def update_highlight(self, elapsed_ms: float):
        if not self._playing or not self._timelines:
            return
        new_idx = -1
        for i, (s, e) in enumerate(self._timelines):
            if elapsed_ms >= s:
                new_idx = i
            else:
                break
        if new_idx == self._current_idx:
            return

        self.lyric_text.config(state=tk.NORMAL)
        for i in range(max(0, self._current_idx + 1), new_idx + 1):
            tag = f"w{i}"
            if i < new_idx:
                self.lyric_text.tag_configure(tag, foreground=C_DONE_TEXT, background="")
            else:
                self.lyric_text.tag_configure(tag, foreground=C_ACTIVE_TEXT,
                                               background=C_GLOW_BG)
        if 0 <= self._current_idx < new_idx:
            self.lyric_text.tag_configure(f"w{self._current_idx}",
                                           foreground=C_DONE_TEXT, background="")
        self._current_idx = new_idx
        self.lyric_text.config(state=tk.DISABLED)

    def playback_done(self):
        self._playing = False
        self.lyric_text.config(state=tk.NORMAL)
        for i in range(len(self._tokens)):
            self.lyric_text.tag_configure(f"w{i}", foreground=C_DONE_TEXT, background="")
        self.lyric_text.config(state=tk.DISABLED)
        self.btn_replay.set_active(True)
        if not self._hovered and not self._pinned:
            self._start_fade()

    # ---- 图钉锁定 ----
    def _on_pin_toggle(self, event=None):
        self._pinned = not self._pinned
        if self._pinned:
            self.btn_pin.config(fg="#4cff8d")
            self._cancel_fade()
        else:
            self.btn_pin.config(fg="#505050")
            if not self._playing and not self._hovered:
                self._start_fade()

    # ---- 鼠标悬停 ----
    def _on_mouse_enter(self, event):
        self._hovered = True
        self._cancel_fade()
        self._alpha = 1.0

    def _on_mouse_leave(self, event):
        self._hovered = False
        if not self._playing and not self._pinned:
            self._start_fade()

    # ---- 淡出 ----
    def _cancel_fade(self):
        with self._lock:
            self._fading = False
            if self._fade_job:
                try:
                    self.root.after_cancel(self._fade_job)
                except Exception:
                    pass
                self._fade_job = None

    def _start_fade(self):
        self._cancel_fade()
        with self._lock:
            self._fading = True
        self._fade_job = self.root.after(3000, self._do_fade_step)

    def _do_fade_step(self):
        with self._lock:
            if not self._fading:
                return
            self._alpha -= 0.06
            if self._alpha <= 0.02:
                self._fading = False
                self.hide()
                return
        try:
            self.root.attributes("-alpha", self._alpha)
        except Exception:
            return
        self._fade_job = self.root.after(50, self._do_fade_step)

    def hide(self):
        global _overlay_visible
        _overlay_visible = False
        self._cancel_fade()
        try:
            self.root.withdraw()
        except Exception:
            pass

    def destroy(self):
        self._cancel_fade()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---- 拖拽 ----
    def _on_drag_start(self, event):
        self._drag_x, self._drag_y = event.x_root, event.y_root
        self.root.config(cursor="fleur")

    def _on_drag_move(self, event):
        dx = event.x_root - self._drag_x
        dy = event.y_root - self._drag_y
        self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")
        self._drag_x, self._drag_y = event.x_root, event.y_root

    def _on_drag_stop(self, event):
        self.root.config(cursor="arrow")


# ============================================================
#  悬浮球 —— 始终显示，点击切换翻译窗口显隐
# ============================================================
class FloatingBall:
    """桌面悬浮球：圆角药丸形可拖拽，左键切换翻译窗口，右键菜单隐藏到任务栏。"""

    # 浅白配色
    BALL_FILL   = "#e8e8e8"
    BALL_TEXT   = "#555555"
    BALL_HOVER  = "#ffffff"
    BALL_H_TEXT = "#333333"

    def __init__(self, toggle_callback):
        self._toggle = toggle_callback
        self._drag_origin = (0, 0)
        self._hidden = False
        self._taskbar_win = None
        self._create()

    # ── 悬浮球本体 ──
    def _create(self):
        self.ball = tk.Toplevel()
        self.ball.title("float-ball")
        self.ball.overrideredirect(True)
        self.ball.attributes("-topmost", True)
        self.ball.attributes("-alpha", 1.0)

        sw = self.ball.winfo_screenwidth()
        self._bw, self._bh = 90, 32
        x = sw - self._bw - 24
        y = 24
        self.ball.geometry(f"{self._bw}x{self._bh}+{x}+{y}")

        self.canvas = tk.Canvas(self.ball, width=self._bw, height=self._bh,
                                bg="#010101", highlightthickness=0,
                                cursor="hand2")
        self.canvas.pack()

        self._draw(self.BALL_FILL, self.BALL_TEXT)

        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Enter>", lambda e: self._draw(self.BALL_HOVER, self.BALL_H_TEXT))
        self.canvas.bind("<Leave>", lambda e: self._draw(self.BALL_FILL, self.BALL_TEXT))

        try:
            self.ball.wm_attributes("-transparentcolor", "#010101")
        except Exception:
            pass

    def _draw(self, fill, text_color):
        w, h = self._bw, self._bh
        r = h // 2  # 圆角半径 = 半高 → 完美药丸形
        d = 2 * r
        self.canvas.delete("all")
        # 药丸形：左半圆 + 矩形 + 右半圆
        self.canvas.create_oval(0, 0, d, h, fill=fill, outline="")
        self.canvas.create_oval(w - d, 0, w, h, fill=fill, outline="")
        self.canvas.create_rectangle(r, 0, w - r, h, fill=fill, outline="")
        # 文字居中
        self.canvas.create_text(w // 2, h // 2, text="Translate",
                                fill=text_color,
                                font=(FONT_FAMILY, 10, "bold"))

    # ── 事件 ──
    def _on_release(self, event):
        ox, oy = self._drag_origin
        if abs(event.x_root - ox) < 4 and abs(event.y_root - oy) < 4:
            self._toggle()

    def _on_drag_start(self, event):
        self._dx = event.x_root - self.ball.winfo_x()
        self._dy = event.y_root - self.ball.winfo_y()
        self._drag_origin = (event.x_root, event.y_root)

    def _on_drag_move(self, event):
        x = event.x_root - self._dx
        y = event.y_root - self._dy
        self.ball.geometry(f"+{x}+{y}")

    # ── 右键菜单 ──
    def _on_right_click(self, event):
        self._show_context_menu(event.x_root, event.y_root)

    def _show_context_menu(self, x, y):
        """自定义现代风格右键菜单（替代系统原生 Menu）"""
        menu = tk.Toplevel(self.ball)
        menu.overrideredirect(True)
        menu.attributes("-topmost", True)
        menu.configure(bg="#e8e8e8")

        items = [
            ("历史记录",   _show_history_window),
            ("隐藏悬浮球", self._hide_to_taskbar),
            ("退出程序",   _on_quit),
        ]

        for i, (label, cmd) in enumerate(items):
            if i > 0:
                tk.Frame(menu, height=1, bg="#cccccc").pack(fill=tk.X, padx=8)
            btn = tk.Label(
                menu, text=label,
                font=(FONT_FAMILY, 10), fg="#333333", bg="#e8e8e8",
                padx=16, pady=6, anchor="w", cursor="hand2",
            )
            btn.pack(fill=tk.X)
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg="#d0d0d0"))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg="#e8e8e8"))

            def make_handler(c):
                return lambda e: (menu.destroy(), c())[1]
            btn.bind("<ButtonRelease-1>", make_handler(cmd))

        # 定位
        menu.update_idletasks()
        mw = menu.winfo_reqwidth()
        mh = menu.winfo_reqheight()
        sw = menu.winfo_screenwidth()
        sh = menu.winfo_screenheight()
        if x + mw > sw:
            x = sw - mw - 4
        if y + mh > sh:
            y = sh - mh - 4
        menu.geometry(f"{mw}x{mh}+{x}+{y}")

        # 失焦自动关闭
        menu.bind("<FocusOut>", lambda e: menu.after(50, menu.destroy))
        menu.focus_set()

    def _hide_to_taskbar(self):
        """隐藏悬浮球，在任务栏留一个恢复入口。"""
        self._hidden = True
        self.ball.withdraw()

        # 创建带任务栏图标的小窗口
        tw = tk.Toplevel()
        tw.title("🌐 翻译助手 — 双击恢复悬浮球")
        tw.geometry("300x1+-300+0")  # 放在屏幕外，只留任务栏图标
        tw.resizable(False, False)
        tw.attributes("-topmost", False)
        tw.protocol("WM_DELETE_WINDOW", lambda: self._restore_from_taskbar())
        tw.bind("<FocusIn>", lambda e: self._restore_from_taskbar())
        self._taskbar_win = tw

    def _restore_from_taskbar(self):
        """从任务栏恢复悬浮球。"""
        self._hidden = False
        self.ball.deiconify()
        self.ball.attributes("-alpha", 1.0)
        if self._taskbar_win:
            try:
                self._taskbar_win.destroy()
            except Exception:
                pass
            self._taskbar_win = None

    # ── 生命周期 ──
    def show(self):
        try:
            self.ball.deiconify()
        except Exception:
            pass

    def destroy(self):
        if self._taskbar_win:
            try:
                self._taskbar_win.destroy()
            except Exception:
                pass
        try:
            self.ball.destroy()
        except Exception:
            pass


# ============================================================
_overlay_lock = threading.Lock()
_overlay = None


def get_overlay() -> SubtitleOverlay:
    global _overlay
    with _overlay_lock:
        if _overlay is None:
            _overlay = SubtitleOverlay()
        return _overlay


def show_subtitle_safe(source: str, translated: str):
    try:
        ov = get_overlay()
        if ov.root:
            ov.root.after(0, lambda: ov.show(source, translated))
    except Exception:
        pass


_overlay_visible = False


def toggle_overlay():
    """悬浮球点击：切换翻译窗口显隐。"""
    global _overlay_visible
    ov = get_overlay()
    if ov.root is None:
        return
    if _overlay_visible:
        ov.hide()
        _overlay_visible = False
    else:
        # 恢复显示
        ov.root.attributes("-alpha", 1.0)
        ov.root.deiconify()
        ov.root.lift()
        ov.root.update_idletasks()
        ov.root.update()
        try:
            hwnd = ov.root.winfo_id()
            ctypes.windll.user32.RedrawWindow(
                ctypes.c_void_p(hwnd), None, 0,
                0x0001 | 0x0002 | 0x0400 | 0x0080
                # RDW_INVALIDATE | RDW_ERASE | RDW_UPDATENOW | RDW_ALLCHILDREN
            )
        except Exception:
            pass
        ov.root.update()
        ov._alpha = 1.0
        ov._cancel_fade()
        _overlay_visible = True


# ============================================================
def on_hotkey():
    print("\n" + "=" * 60)
    print("Ctrl+0 triggered")

    info = get_active_app_info()
    print(f"window: {info.get('window_title', '?')}")
    print(f"process: {info.get('process_name', '?')}")

    selected = copy_selected_text()
    if not selected.strip():
        print("nothing selected")
        print("=" * 60)
        return

    disp = selected[:200] + "..." if len(selected) > 200 else selected
    print(f"selected ({len(selected)} chars):\n   {disp}")

    def do_translate():
        lang = detect_language(selected)
        print(f"\nlang: {lang}")
        translation = translate_text(selected, lang)
        print(f"\ntranslation:\n   {translation}")
        print("=" * 60)

        if translation and not translation.startswith("[translation failed"):
            if lang == "chinese":
                show_subtitle_safe(selected, translation)
                print("speaking EN translation (karaoke)...")
                speak(translation)
                _record_history(selected, translation)
            elif lang == "english":
                show_subtitle_safe(selected, translation)
                print("speaking EN original (karaoke)...")
                speak(selected)
                _record_history(selected, selected)
            else:
                show_subtitle_safe(selected, translation)
                print("speaking...")
                speak(translation)
                _record_history(selected, translation)
        print("done\n")

    executor.submit(do_translate)
    print("=" * 60)


_shutdown_done = False
_ball = None


def _cleanup():
    """释放所有资源：keyboard hook、pygame、TTS 线程、线程池、tk 窗口。"""
    global _shutdown_done, _ball
    if _shutdown_done:
        return
    _shutdown_done = True

    print("\ncleaning up...")

    # 1. 解除全局热键
    try:
        overlay = get_overlay()
        if overlay and overlay.root:
            unregister_hotkeys(overlay.root)
    except Exception:
        pass

    # 2. 停止 TTS 后台线程
    try:
        _tts_queue.put(None)
    except Exception:
        pass

    # 3. 关闭线程池
    try:
        executor.shutdown(wait=False)
    except Exception:
        pass

    # 4. 停止 pygame 混音
    try:
        pygame.mixer.music.stop()
        pygame.mixer.quit()
    except Exception:
        pass

    # 5. 销毁悬浮球
    if _ball:
        try:
            _ball.destroy()
        except Exception:
            pass

    # 6. 销毁翻译窗口
    try:
        overlay = get_overlay()
        overlay.destroy()
    except Exception:
        pass

    print("bye")


def _on_quit():
    """Ctrl+Shift+Q 或 Ctrl+C 退出入口。"""
    _cleanup()
    # 强制退出（处理挂起的线程/事件循环）
    os._exit(0)


# 注册退出时的兜底清理
atexit.register(_cleanup)

# 在 Windows 上注册 SIGINT 处理
signal.signal(signal.SIGINT, lambda s, f: _on_quit())


def main():
    # ★ 单实例：强制杀死旧进程，启动新实例
    current_pid = os.getpid()
    if getattr(sys, 'frozen', False):
        target_name = os.path.basename(sys.executable)  # translate.exe
    else:
        target_name = "hotkey_monitor.py"

    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            if proc.info['pid'] == current_pid:
                continue
            cmd = " ".join(proc.info['cmdline'] or [])
            if target_name in cmd:
                proc.kill()
                print(f"killed old instance (pid={proc.info['pid']})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    cache_count, cache_mb = _cache_stats()
    print("=" * 60)
    print("  Hotkey + Translate + Karaoke Subtitle")
    print("  Ctrl+0 -> copy + translate + speak + karaoke overlay")
    print("  Ctrl+Shift+Q -> quit")
    if cache_count > 0:
        print(f"  TTS cache: {cache_count} files, {cache_mb} MB")
    print("  点击悬浮球 → 显示/隐藏翻译窗口")
    print("=" * 60)

    global _ball
    overlay = get_overlay()
    _ball = FloatingBall(toggle_callback=toggle_overlay)

    # 注册全局热键（使用 Win32 API，无需管理员权限）
    register_hotkeys(overlay.root, [
        (1, win32con.MOD_CONTROL, ord('0'), on_hotkey),
        (2, win32con.MOD_CONTROL | win32con.MOD_SHIFT, ord('Q'), _on_quit),
    ])

    # 当 hotkey 触发翻译时，自动显示 overlay
    # （覆写 show() 以同步 _overlay_visible 状态）
    original_show = overlay.show

    def show_and_track(*args, **kwargs):
        global _overlay_visible
        _overlay_visible = True
        original_show(*args, **kwargs)

    overlay.show = show_and_track

    overlay.root.mainloop()


if __name__ == "__main__":
    main()
