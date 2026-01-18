from pynput import keyboard
from collections import deque
import threading
import requests
import json
from PIL import Image
import time

# Store the last 20 keystrokes (or fewer if less have occurred)
# deque is efficient for adding/removing from both ends (like a queue)
keystroke_history = deque(maxlen=14)

URL = "http://100.79.188.68:5000/"

def show_photo(path):
    img = Image.open('path')
    img.show()
    time.sleep(5)
    img.close()


def on_press(key):
    # Clean up key representation for storage
    try:
        # Alphanumeric keys
        key_char = key.char
    except AttributeError:
        # Special keys (like Space, Enter, etc.)
        key_char = str(key) # e.g., "Key.space", "Key.enter"

    print(f"Current History ({len(keystroke_history)}): {list(keystroke_history)}")

    # Stop listener if Escape key is pressed
    if key == keyboard.Key.enter:
        id = "".join(keystroke_history)
        data = requests.get(url = f"{URL}{id}")
        
        with open("control_panel_settings.json", 'w') as file:
            print(data.json())
            json.dump(data.json(), file, indent=4) 
            
        if data['id'] == "04768662401090":
            show_photo("./assets/classes/warlock.jpg")
        elif data['id'] == "04578162401090":
            show_photo("./assets/classes/dm.jpg")
        elif data['id'] == "placeholder":
            show_photo("./assets/classes/warrior.jpg")
        elif data['id'] == "placeholder":
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


    
    

