.PHONY: setup check doctor
setup:
	bash scripts/setup.sh
check:
	bash scripts/check.sh
doctor:
	.venv/bin/judicial-kg doctor
