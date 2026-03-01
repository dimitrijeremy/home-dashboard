import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

MEDIAMTX_HOST = os.getenv("MEDIAMTX_HOST", "mediamtx")
MEDIAMTX_HTTP_PORT = os.getenv("MEDIAMTX_HTTP_PORT", "8888")
BASE_URL = os.getenv("BASE_URL", f"http://{MEDIAMTX_HOST}:{MEDIAMTX_HTTP_PORT}")

@app.route('/api/cameras', methods=['GET'])
def get_cameras():
    data = [
        {
            "id": 1,
            "name": "Front Door",
            "stream_url": f"{BASE_URL}/frontdoor/index.m3u8"
        }
    ]
    return jsonify(data)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
