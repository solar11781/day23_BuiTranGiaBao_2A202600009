.PHONY: install test lint typecheck run-all run-scenarios run-extended run-sqlite verify-extensions export-diagram streamlit-ui grade-local clean

install:
	pip install -e '.[dev]'

# One command for the complete lab run: baseline metrics, all extension evidence,
# Mermaid diagram, and one consolidated report at reports/lab_report.md.
run-all:
	python -m langgraph_agent_lab.cli run-all

test:
	pytest

lint:
	ruff check src tests

typecheck:
	mypy src

run-scenarios:
	python -m langgraph_agent_lab.cli run-scenarios --config configs/lab.yaml --output outputs/metrics.json

run-extended:
	python -m langgraph_agent_lab.cli run-scenarios --config configs/lab_extended.yaml --output outputs/metrics_extended.json

run-sqlite:
	python -m langgraph_agent_lab.cli run-scenarios --config configs/lab_sqlite.yaml --output outputs/metrics_sqlite.json

verify-extensions:
	python -m langgraph_agent_lab.cli verify-extensions

export-diagram:
	python -m langgraph_agent_lab.cli export-diagram --output outputs/graph.mmd

streamlit-ui:
	LANGGRAPH_INTERRUPT=true streamlit run streamlit_app.py

grade-local:
	python -m langgraph_agent_lab.cli validate-metrics --metrics outputs/metrics.json

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov dist build *.egg-info outputs/*.json outputs/*.mmd outputs/*.sqlite outputs/*.sqlite-wal outputs/*.sqlite-shm reports/lab_report.md
