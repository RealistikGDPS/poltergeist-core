#!/usr/bin/make
lint:
	uv run pre-commit run --all-files
