"""Send real Ctrl+C only to our newly created child console, never the user's console."""
import ctypes, sys, time
k = ctypes.WinDLL('kernel32', use_last_error=True)
k.FreeConsole()
if not k.AttachConsole(int(sys.argv[1])):
    raise ctypes.WinError(ctypes.get_last_error())
if not k.SetConsoleCtrlHandler(None, True):
    raise ctypes.WinError(ctypes.get_last_error())
if not k.GenerateConsoleCtrlEvent(0, 0):
    raise ctypes.WinError(ctypes.get_last_error())
print('Sent CTRL_C_EVENT to isolated test console for PID', sys.argv[1], flush=True)
time.sleep(.3)
k.FreeConsole()
