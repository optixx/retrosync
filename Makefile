homebrew:
	brew install uv
	brew install direnv

bootstrap:
	test -d .venv && rm -rvf .venv
	uv venv
	direnv reload

pip:
	uv pip install -r requirements.txt

install: homebrew bootstrap pip

run-tests:
	pytest test/commandline.py -rP
