PYTHON ?= python3
export PYTHONPATH = src

.PHONY: test test-mode check demo

test:
	$(PYTHON) -m pytest

test-mode:
	$(PYTHON) -m desk_assistant --test

demo:
	$(PYTHON) -m desk_assistant --test --script --screenshot-dir /tmp/desk-assistant-demo

check:
	$(PYTHON) -m desk_assistant --check
