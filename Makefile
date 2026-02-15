.PHONY: setup run dev prod clean

# First-time setup
setup:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt
	mkdir -p instance uploads

# Development server (debug mode)
dev:
	./venv/bin/python run.py

# Production server (gunicorn)
prod:
	./venv/bin/gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"

# Clean database (careful!)
clean:
	rm -f instance/baseball.db
