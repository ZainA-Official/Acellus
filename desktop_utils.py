import sys
import ctypes

def ensure_default_desktop():
    """
    On Windows, when running in certain environments (like background agents
    or custom desktops), the thread might not be attached to the interactive
    "Default" desktop where the GUI lives. This function ensures the current
    thread is attached to the "Default" desktop so that GDI captures (BitBlt)
    and input injections (pyautogui) work correctly.
    """
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            thread_id = kernel32.GetCurrentThreadId()
            h_current = user32.GetThreadDesktop(thread_id)
            if h_current:
                buf = ctypes.create_unicode_buffer(256)
                needed = ctypes.c_ulong()
                if user32.GetUserObjectInformationW(h_current, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
                    if buf.value != "Default":
                        h_default = user32.OpenDesktopW("Default", 0, False, 0x01ff) # DESKTOP_ALL_ACCESS = 0x01ff
                        if h_default:
                            user32.SetThreadDesktop(h_default)
                            user32.CloseDesktop(h_default)
        except Exception:
            pass
