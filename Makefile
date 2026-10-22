.PHONY: install dev test evaluate
install:
	python -m pip install -e '.[dev]'
dev:
	uvicorn cryptoproof.api:app --reload --host 127.0.0.1 --port 8000
test:
	pytest -q
evaluate:
	python -m scripts.evaluate
