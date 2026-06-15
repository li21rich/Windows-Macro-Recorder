import os
import struct
import threading
import time
import ctypes
import json
from ctypes import wintypes

import psutil
from pynput import keyboard, mouse


# =============================================================================
#  Process / timer setup
# =============================================================================
psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
ctypes.windll.winmm.timeBeginPeriod(1)

_qpc_freq = ctypes.c_longlong()
ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(_qpc_freq))
_QPC_FREQ: float = float(_qpc_freq.value)

def qpc_time() -> float:
    """High-resolution timestamp in seconds."""
    val = ctypes.c_longlong()
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(val))
    return val.value / _QPC_FREQ


# =============================================================================
#  Win32 structures — input injection
# =============================================================================
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _fields_ = [("type", ctypes.c_ulong), ("un", _INPUT)]

INPUT_MOUSE        = 0
MOUSEEVENTF_MOVE   = 0x0001
MOUSEEVENTF_WHEEL  = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000


# =============================================================================
#  Win32 structures — raw input
# =============================================================================
WM_INPUT        = 0x00FF
RID_INPUT       = 0x10000003
RIDEV_INPUTSINK = 0x00000100

class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType",  wintypes.DWORD),
        ("dwSize",  wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam",  wintypes.WPARAM),
    ]

class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags",            wintypes.USHORT),
        ("ulButtons",          ctypes.c_ulong),
        ("ulRawButtons",       ctypes.c_ulong),
        ("lLastX",             ctypes.c_long),
        ("lLastY",             ctypes.c_long),
        ("ulExtraInformation", ctypes.c_ulong),
    ]

class RAWINPUT(ctypes.Structure):
    class _RAWINPUT(ctypes.Union):
        _fields_ = [("mouse", RAWMOUSE)]
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT)]

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage",     wintypes.USHORT),
        ("dwFlags",     wintypes.DWORD),
        ("hwndTarget",  wintypes.HWND),
    ]

HCURSOR = wintypes.HANDLE
HBRUSH  = wintypes.HANDLE
LRESULT = wintypes.LPARAM
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style",         wintypes.UINT),      ("lpfnWndProc",   WNDPROC),
        ("cbClsExtra",    ctypes.c_int),        ("cbWndExtra",    ctypes.c_int),
        ("hInstance",     wintypes.HINSTANCE),  ("hIcon",         wintypes.HICON),
        ("hCursor",       HCURSOR),             ("hbrBackground", HBRUSH),
        ("lpszMenuName",  wintypes.LPCWSTR),    ("lpszClassName", wintypes.LPCWSTR),
    ]

u32 = ctypes.windll.user32
u32.RegisterClassW.argtypes  = [ctypes.POINTER(WNDCLASSW)]
u32.RegisterClassW.restype   = wintypes.ATOM
u32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
u32.CreateWindowExW.restype  = wintypes.HWND
u32.DefWindowProcW.argtypes  = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
u32.DefWindowProcW.restype   = LRESULT


# =============================================================================
#  Config
# =============================================================================
SAVE_DIR     = "macros"
MAPPING_FILE = os.path.join(SAVE_DIR, "hotkey_map.json")
RECORD_KEY   = 'ctrl i'   # Swapped to Ctrl+I
TEST_KEY     = 'ctrl b'   # Global key combo to test unsaved macro
os.makedirs(SAVE_DIR, exist_ok=True)


# =============================================================================
#  Binary file format
#  struct '<d B i i 64s'>  — 81 bytes per event
# =============================================================================
_FMT   = '<dBii64s'
_CHUNK = struct.calcsize(_FMT)

BUTTON_MAP     = {'Button.left': 1, 'Button.right': 2, 'Button.middle': 3}
BUTTON_MAP_INV = {v: k for k, v in BUTTON_MAP.items()}
ACTION_MAP     = {'press': 1, 'release': 2}
ACTION_MAP_INV = {v: k for k, v in ACTION_MAP.items()}

TYPE_MOVE    = 0
TYPE_CLICK   = 1
TYPE_PRESS   = 2
TYPE_RELEASE = 3
TYPE_SCROLL  = 4


# =============================================================================
#  MacroEngine — encapsulates all state and logic
# =============================================================================
class MacroEngine:
    def __init__(self):
        self.is_recording    = False
        self._current_macro: list = []
        self._rec_start_qpc: float = 0.0

        self._stop_event      = threading.Event()
        self._playback_lock   = threading.Lock()
        self._playback_thread = None

        # State tracking for clean hotkey evaluation
        self._ctrl_pressed = False
        parts = [p.strip().lower() for p in RECORD_KEY.split()]
        self._req_ctrl = 'ctrl' in parts
        self._req_char = next((p for p in parts if p != 'ctrl'), None)
        
        self._req_test_char = TEST_KEY.split()[-1].lower()

        # hotkey_map: { key_char: filepath }
        self.hotkey_map: dict[str, str] = {}
        self._load_hotkey_map()

        self.on_state_change = None

        self._wnd_proc_holder = None
        threading.Thread(target=self._raw_input_loop, daemon=True).start()

        self._kb_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release)
        self._ms_listener = mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll)
        self._kb_listener.start()
        self._ms_listener.start()

    # ── Hotkey map persistence ────────────────────────────────────────────────
    def _load_hotkey_map(self):
        if os.path.exists(MAPPING_FILE):
            try:
                with open(MAPPING_FILE) as f:
                    self.hotkey_map = json.load(f)
            except Exception:
                self.hotkey_map = {}

    def save_hotkey_map(self):
        with open(MAPPING_FILE, 'w') as f:
            json.dump(self.hotkey_map, f, indent=2)

    def assign_hotkey(self, key: str, filepath: str):
        self.hotkey_map.pop(key, None)
        for k, v in list(self.hotkey_map.items()):
            if v == filepath:
                del self.hotkey_map[k]
        self.hotkey_map[key] = filepath
        self.save_hotkey_map()

    def clear_hotkey(self, filepath: str):
        for k, v in list(self.hotkey_map.items()):
            if v == filepath:
                del self.hotkey_map[k]
        self.save_hotkey_map()

    def hotkey_for(self, filepath: str) -> str:
        for k, v in self.hotkey_map.items():
            if v == filepath:
                return k
        return ""

    # ── Recording ─────────────────────────────────────────────────────────────
    def start_recording(self):
        if self.is_playing():
            return
        self._current_macro = []
        self._rec_start_qpc = qpc_time()
        self.is_recording   = True
        self._notify('record_start')

    def stop_recording(self) -> int:
        self.is_recording = False
        self._notify('record_stop')
        return len(self._current_macro)

    def save_current(self) -> str:
        fname = self._get_next_filename()
        save_macro_binary(fname, self._current_macro)
        self._notify('save')
        return fname

    def has_unsaved(self) -> bool:
        return bool(self._current_macro)

    # ── Playback ──────────────────────────────────────────────────────────────
    def is_playing(self) -> bool:
        with self._playback_lock:
            return (self._playback_thread is not None
                    and self._playback_thread.is_alive())

    def play(self, filepath: str):
        if self.is_playing() or self.is_recording:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._play_worker,
                             args=(load_macro_binary(filepath),), daemon=True)
        with self._playback_lock:
            self._playback_thread = t
        t.start()

    def play_temporary(self):
        """Plays the current macro sitting in RAM buffer."""
        if self.is_playing() or self.is_recording or not self._current_macro:
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._play_worker,
                             args=(self._current_macro.copy(),), daemon=True)
        with self._playback_lock:
            self._playback_thread = t
        t.start()

    def stop_playback(self):
        self._stop_event.set()

    def _play_worker(self, data: list):
        _set_thread_critical()
        self._notify('play_start')

        kb          = keyboard.Controller()
        ms          = mouse.Controller()
        active_keys: set = set()
        start_play  = time.time()

        try:
            for event in data:
                if self._stop_event.is_set():
                    break
                if not self._precise_wait(start_play + event['time']):
                    break

                etype = event['type']
                if etype == 'move':
                    hardware_move_relative(event['dx'], event['dy'])
                elif etype == 'scroll':
                    hardware_scroll(event['dx'], event['dy'])
                elif etype == 'mouse_click':
                    btn_name = event['button'].replace("Button.", "")
                    if hasattr(mouse.Button, btn_name):
                        btn = getattr(mouse.Button, btn_name)
                        if event['action'] == 'press':
                            ms.press(btn)
                        else:
                            ms.release(btn)
                elif etype in ('press', 'release'):
                    key = clean_key(event['key'])
                    if key is None:
                        continue
                    try:
                        if etype == 'press':
                            kb.press(key)
                            active_keys.add(key)
                        else:
                            kb.release(key)
                            active_keys.discard(key)
                    except Exception:
                        pass
        finally:
            for key in active_keys:
                try:
                    kb.release(key)
                except Exception:
                    pass
            with self._playback_lock:
                self._playback_thread = None
            self._notify('play_stop')

    def _precise_wait(self, target: float) -> bool:
        while True:
            remaining = target - time.time()
            if remaining <= 0:
                return True
            if self._stop_event.is_set():
                return False
            if remaining > 0.1:
                time.sleep(remaining - 0.05)

    # ── File helpers ──────────────────────────────────────────────────────────
    def _get_next_filename(self) -> str:
        i = 0
        while os.path.exists(os.path.join(SAVE_DIR, f"macro_{i}.bin")):
            i += 1
        return os.path.join(SAVE_DIR, f"macro_{i}.bin")

    def get_latest_file(self) -> str | None:
        files = list_macros()
        return max(files, key=os.path.getctime) if files else None

    def delete_macro(self, filepath: str):
        self.clear_hotkey(filepath)
        os.remove(filepath)

    # ── Input listeners ───────────────────────────────────────────────────────
    def _on_scroll(self, x, y, dx, dy):
        if self.is_recording:
            self._current_macro.append({
                'type': 'scroll',
                'dx':   int(dx),
                'dy':   int(dy),
                'time': qpc_time() - self._rec_start_qpc,
            })

    def _on_click(self, x, y, button, pressed):
        if self.is_recording:
            self._current_macro.append({
                'type':   'mouse_click',
                'button': str(button),
                'action': 'press' if pressed else 'release',
                'time':   qpc_time() - self._rec_start_qpc,
            })

    def _on_press(self, key):
        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = True

        # Ctrl+C — always quit
        try:
            if key.vk == 3 or (self._ctrl_pressed and getattr(key, 'char', None) == '\x03'):
                os._exit(0)
        except AttributeError:
            pass

        char = getattr(key, 'char', None)
        if char and ord(char) < 32:
            try:
                char = chr(ord(char) + 96)
            except Exception:
                pass

        key_name = char or getattr(key, 'name', None)
        if key_name:
            key_name = key_name.lower()

        # Check if remapped combo matches
        if self._ctrl_pressed:
            if key_name == self._req_char:
                if not self.is_recording:
                    self.start_recording()
                else:
                    self.stop_recording()
                return
            if key_name == self._req_test_char:
                if self.is_playing():
                    self.stop_playback()
                else:
                    self.play_temporary()
                return

        # Mapped playback hotkeys
        if char and char in self.hotkey_map and not self.is_recording:
            fp = self.hotkey_map[char]
            if os.path.exists(fp):
                if self.is_playing():
                    self.stop_playback()
                else:
                    self.play(fp)
            return

        # Record event
        if self.is_recording:
            self._current_macro.append({
                'type': 'press',
                'key':  str(key),
                'time': qpc_time() - self._rec_start_qpc,
            })

    def _on_release(self, key):
        char = getattr(key, 'char', None)
        if char and ord(char) < 32:
            try:
                char = chr(ord(char) + 96)
            except Exception:
                pass

        key_name = char or getattr(key, 'name', None)
        if key_name:
            key_name = key_name.lower()

        is_record_hotkey = (self._req_ctrl == self._ctrl_pressed) and (key_name == self._req_char)
        is_test_hotkey = self._ctrl_pressed and (key_name == self._req_test_char)

        if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_pressed = False

        if self.is_recording:
            if is_record_hotkey or is_test_hotkey:
                return
            if char and char in self.hotkey_map:
                return
            self._current_macro.append({
                'type': 'release',
                'key':  str(key),
                'time': qpc_time() - self._rec_start_qpc,
            })

    # ── Raw input hidden window ───────────────────────────────────────────────
    def _raw_input_loop(self):
        hwnd = self._create_hidden_window()
        rid  = RAWINPUTDEVICE()
        rid.usUsagePage = 0x01
        rid.usUsage     = 0x02
        rid.dwFlags     = RIDEV_INPUTSINK
        rid.hwndTarget  = hwnd
        if not u32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(rid)):
            print("CRITICAL: Failed to register raw input devices.")
            return
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def _create_hidden_window(self):
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT and self.is_recording:
                raw    = RAWINPUT()
                dwSize = wintypes.DWORD(ctypes.sizeof(RAWINPUT))
                u32.GetRawInputData(
                    ctypes.cast(lparam, wintypes.HANDLE),
                    RID_INPUT,
                    ctypes.byref(raw),
                    ctypes.byref(dwSize),
                    ctypes.sizeof(RAWINPUTHEADER),
                )
                if raw.header.dwType == 0:
                    dx = raw.data.mouse.lLastX
                    dy = raw.data.mouse.lLastY
                    if raw.data.mouse.usFlags == 0 and (dx or dy):
                        self._current_macro.append({
                            'type': 'move',
                            'dx':   int(dx),
                            'dy':   int(dy),
                            'time': qpc_time() - self._rec_start_qpc,
                        })
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_holder = WNDPROC(wnd_proc)
        cls_name = "MacroRawInputWindow"
        wcls = WNDCLASSW()
        wcls.lpfnWndProc   = self._wnd_proc_holder
        wcls.lpszClassName = cls_name
        wcls.hInstance     = ctypes.windll.kernel32.GetModuleHandleW(None)
        u32.RegisterClassW(ctypes.byref(wcls))
        return u32.CreateWindowExW(
            0, cls_name, "RawInputHost", 0,
            0, 0, 0, 0, 0, 0, wcls.hInstance, None,
        )

    def _notify(self, event: str):
        if self.on_state_change:
            self.on_state_change(event)


# =============================================================================
#  Module-level helpers
# =============================================================================
def hardware_move_relative(dx: int, dy: int) -> None:
    ii_ = INPUT._INPUT()
    ii_.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, None)
    cmd = INPUT(INPUT_MOUSE, ii_)
    u32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))


def hardware_scroll(dx: int, dy: int) -> None:
    WHEEL_DELTA = 120
    if dy:
        ii_ = INPUT._INPUT()
        ii_.mi = MOUSEINPUT(0, 0, dy * WHEEL_DELTA, MOUSEEVENTF_WHEEL, 0, None)
        cmd = INPUT(INPUT_MOUSE, ii_)
        u32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))
    if dx:
        ii_ = INPUT._INPUT()
        ii_.mi = MOUSEINPUT(0, 0, dx * WHEEL_DELTA, MOUSEEVENTF_HWHEEL, 0, None)
        cmd = INPUT(INPUT_MOUSE, ii_)
        u32.SendInput(1, ctypes.pointer(cmd), ctypes.sizeof(cmd))


def save_macro_binary(filename: str, macro_data: list) -> None:
    with open(filename, 'wb') as f:
        for e in macro_data:
            etype = e['type']
            if etype == 'move':
                t_id, d1, d2, kb = TYPE_MOVE,    e['dx'],  e['dy'],  b''
            elif etype == 'mouse_click':
                t_id = TYPE_CLICK
                d1   = BUTTON_MAP.get(e['button'], 0)
                d2   = ACTION_MAP.get(e['action'], 0)
                kb   = b''
            elif etype == 'press':
                t_id, d1, d2, kb = TYPE_PRESS,   0, 0, e['key'].encode('utf-8')[:64]
            elif etype == 'release':
                t_id, d1, d2, kb = TYPE_RELEASE, 0, 0, e['key'].encode('utf-8')[:64]
            elif etype == 'scroll':
                t_id, d1, d2, kb = TYPE_SCROLL,  e['dx'],  e['dy'],  b''
            else:
                continue
            f.write(struct.pack(_FMT, e['time'], t_id, d1, d2,
                                kb.ljust(64, b'\x00')))


def load_macro_binary(filename: str) -> list:
    data      = []
    if not os.path.exists(filename):
        return data
    file_size = os.path.getsize(filename)
    remainder = file_size % _CHUNK
    if remainder:
        print(f"WARNING: {filename} has {remainder} trailing corrupt byte(s).")
    with open(filename, 'rb') as f:
        for idx in range(file_size // _CHUNK):
            chunk = f.read(_CHUNK)
            if len(chunk) < _CHUNK:
                break
            try:
                t_val, type_id, d1, d2, kb = struct.unpack(_FMT, chunk)
            except struct.error as exc:
                print(f"WARNING: chunk {idx} unpack failed ({exc})")
                continue
            key_str = kb.rstrip(b'\x00').decode('utf-8', errors='replace')
            if   type_id == TYPE_MOVE:
                data.append({'time': t_val, 'type': 'move', 'dx': d1, 'dy': d2})
            elif type_id == TYPE_CLICK:
                data.append({'time': t_val, 'type': 'mouse_click',
                             'button': BUTTON_MAP_INV.get(d1, f'Button.{d1}'),
                             'action': ACTION_MAP_INV.get(d2, 'press')})
            elif type_id == TYPE_PRESS:
                data.append({'time': t_val, 'type': 'press',   'key': key_str})
            elif type_id == TYPE_RELEASE:
                data.append({'time': t_val, 'type': 'release', 'key': key_str})
            elif type_id == TYPE_SCROLL:
                data.append({'time': t_val, 'type': 'scroll',  'dx': d1, 'dy': d2})
            else:
                print(f"WARNING: unknown type {type_id} at chunk {idx}")
    return data


def clean_key(raw: str):
    s = raw.strip("'")
    if s.startswith("Key."):
        return getattr(keyboard.Key, s[4:], None)
    if len(s) == 1 and 1 <= ord(s) <= 26:
        return keyboard.KeyCode.from_char(chr(ord(s) + 96))
    if s.startswith(('\\x', '\\X')):
        try:
            hex_val = int(s[2:], 16)
            if 1 <= hex_val <= 26:
                return keyboard.KeyCode.from_char(chr(hex_val + 96))
        except ValueError:
            pass
        return None
    if len(s) == 1:
        return keyboard.KeyCode.from_char(s)
    return None


def list_macros() -> list[str]:
    return sorted([
        os.path.join(SAVE_DIR, f)
        for f in os.listdir(SAVE_DIR)
        if f.startswith("macro_") and f.endswith(".bin")
    ], key=os.path.getctime)


def macro_duration(filepath: str) -> float:
    try:
        data = load_macro_binary(filepath)
        return data[-1]['time'] if data else 0.0
    except Exception:
        return 0.0


def _set_thread_critical() -> None:
    ctypes.windll.kernel32.SetThreadPriority(
        ctypes.windll.kernel32.GetCurrentThread(), 15)