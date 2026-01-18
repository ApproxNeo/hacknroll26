from pynput import keyboard
from collections import deque
import threading
import requests
import json
from PIL import Image
import time
import sys
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QPixmap
from PySide6.QtCore import QTimer, Qt
from pathlib import Path
import subprocess
import sys
from pathlib import Path


# Store the last 20 keystrokes (or fewer if less have occurred)
# deque is efficient for adding/removing from both ends (like a queue)
keystroke_history = deque(maxlen=14)

URL = "http://100.79.188.68:5000/"
    
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

# -----------------------------
# Fire-and-forget photo popup
# -----------------------------
def show_photo(image_path: str | Path, duration_ms: int = 5000):
    
    script = f"""
    import sys
    from pathlib import Path
    from PySide6.QtWidgets import QApplication, QLabel
    from PySide6.QtGui import QPixmap
    from PySide6.QtCore import QTimer, Qt

    app = QApplication(sys.argv)
    label = QLabel()
    label.setAlignment(Qt.AlignCenter)
    label.setWindowFlags(Qt.WindowStaysOnTopHint)

    pixmap = QPixmap(str(Path(r'{image_path}').resolve()))
    if pixmap.isNull():
        label.setText('Cannot load image:\\n{image_path}')
        label.resize(400, 200)
    else:
        label.setPixmap(pixmap.scaled(600, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label.resize(600, 400)

    label.show()
    QTimer.singleShot({duration_ms * 1000}, label.close)
    sys.exit(app.exec())
    """

    subprocess.Popen([sys.executable, "-c", script])


def on_press(key):
    # Clean up key representation for storage
    try:
        # Alphanumeric keys
        key_char = key.char
    except AttributeError:
        # Special keys (like Space, Enter, etc.)
        key_char = str(key) # e.g., "Key.space", "Key.enter"

    print(f"Current History ({len(keystroke_history)}): {list(keystroke_history)}")

    if key == keyboard.Key.shift:
        pass
    # Stop listener if Escape key is pressed
    elif key == keyboard.Key.enter:
        id = "".join(keystroke_history)
        data = requests.get(url = f"{URL}{id}")
        
        with open("control_panel_settings.json", 'w') as file:
            out = data.json()
            print(out)
            json.dump(out, file, indent=4) 
        print(id)
        if id == "04508362401090":
        
            show_photo("./assets/classes/warlock.jpg")
        elif id == "04578162401090":
            show_photo("./assets/classes/dm.jpg")
        elif id == "04C08762401090":
            show_photo("./assets/classes/warrior.jpg")
            
        elif id == "04768662401090":
            show_photo("./assets/classes/bard.jpg")
    else:
        keystroke_history.append(key_char)


def record_keystrokes():
    # Start the listener in a separate thread to non-block the main execution
    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    listener.join() # Keep the listener running until it stops

# You can call record_keystrokes() directly, but this structure allows
# for potential future additions like replaying or saving the history.
record_keystrokes()


    
    

