import requests
import json

URL = "http://localhost:5000/"

while True:
    i = input()
    
    data = requests.get(url = f"{URL}{i}")
    
    with open("control_panel_settings.json", 'w') as file:
        print(data.json())
        json.dump(data.json(), file, indent=4) 
    
    

