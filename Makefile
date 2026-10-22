PY ?= .venv/bin/python
PORT ?= 3000

.PHONY: help install data pipeline all report app test clean

help:
	@echo "install   create .venv and install dependencies"
	@echo "data      generate the synthetic raw exports (~60k customers)"
	@echo "pipeline  rebuild the DuckDB warehouse from raw data"
	@echo "all       data + pipeline"
	@echo "report    render the static analyst report to reports/"
	@echo "app       run the Streamlit dashboard on port $(PORT)"
	@echo "test      run the test suite"
	@echo "clean     remove the warehouse and generated data"

install:
	uv venv --python 3.12 .venv
	uv pip install -r requirements.txt

data:
	PYTHONPATH=src $(PY) -m analytics.generate_data

pipeline:
	PYTHONPATH=src $(PY) -m analytics.pipeline --reset

all: data pipeline report

report:
	PYTHONPATH=src $(PY) -m analytics.report

app:
	$(PY) -m streamlit run app/dashboard.py --server.port $(PORT) --server.address 0.0.0.0

test:
	$(PY) -m pytest

clean:
	rm -rf data
