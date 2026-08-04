import os
import sys
import time
import webbrowser
import threading

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# If frozen with PyInstaller, adjust resource path
if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS
    os.chdir(bundle_dir)
    import backend
    backend.PUBLIC_DIR = os.path.join(bundle_dir, "public")
    backend.DB_PATH = os.path.join(os.path.dirname(sys.executable), "power_logs.db")
else:
    import backend

def open_browser():
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:5000")

if __name__ == '__main__':
    # Launch browser automatically in separate thread
    threading.Thread(target=open_browser, daemon=True).start()
    
    # Run server
    backend.main()
