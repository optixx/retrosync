env-boostrap:
	rm -rvf .venv
	uv venv

pip-install:
	uv pip install -r requirements.txt


all: env-boostrap pip-install

sync:
	python3 app.py

