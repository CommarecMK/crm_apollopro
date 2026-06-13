"""
run.py — vstupní bod aplikace.
Lokálně:  python run.py
Railway/gunicorn: gunicorn run:app
"""
import os
from app import create_app

app = create_app()

if __name__ == "__main__":
    # debug se zapne jen lokálně přes FLASK_DEBUG=1, nikdy v produkci
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
