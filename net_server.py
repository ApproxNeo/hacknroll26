from flask import Flask, jsonify, request
from lightdb import LightDB
from lightdb.models import Model

from typing import List, Dict, Any, Union

# Initialize the database
db = LightDB("db.json")

# Define a User model
class User(Model, table="users"):
    id: str
    settings: Dict[str, Union[str, bool, int, float]]

# Create a Flask application instance
app = Flask(__name__)


# Route to get all items or create a new item
@app.route('/<string:id>', methods=['POST'])
def handle_post(id):
    retrieved_user = User.get(id=id)
    if retrieved_user:
        retrieved_user.settings = request.json
        retrieved_user.save()
        return {"message": "Updated successfully"}
    else:
        user = User.create(id=id)

# Route to get a specific item by ID
@app.route('/<string:id>', methods=['GET'])
def handle_get(id):
    retrieved_user = User.get(id=id)
    
    if len(id) != 14:
        return {"message": "invalid length"}
    
    try:
        num = int(id)
    except:
        return {"message": "invalid id"}
    
    if retrieved_user:
        return jsonify(retrieved_user.settings)
    else:
        user = User.create(
            id=id,
            settings={
                "speed": 5,
                "visible": True,
                "running": True,
                "click_through": True,
                "right_to_left": False,
                "projectile_color": "#6e6e6e6"
            }
            )
    return {"message": "Created successfully"}
    

# Run the application
if __name__ == '__main__':
    # Running on port 5000 by default, with debug mode enabled for development
    app.run(host="0.0.0.0", debug=True)