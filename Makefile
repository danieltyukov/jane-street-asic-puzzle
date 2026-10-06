# Jane Street ASIC puzzle: reproduce the whole solve with `make all`.

PYTHON  ?= python3
VENV    ?= .venv
BIN     := $(VENV)/bin
# Deliberately not called PDK_ROOT: many machines export that for other PDKs,
# and this project keeps its own copy next to the code.
SKY130_ROOT ?= $(CURDIR)/pdk
# open_pdks build of SKY130 that ciel can download; the cell library has not
# changed between recent builds, any of them works
PDK_VERSION ?= 0fe599b2afb6708d281543108caf8310912f54af

.PHONY: help setup puzzle pdk all figures test clean

help:
	@echo "make setup    create $(VENV) and install the package"
	@echo "make puzzle   fetch the puzzle files (git submodule)"
	@echo "make pdk      download the SKY130 HD standard cell library into ./pdk"
	@echo "make all      run every step and write build/results.json"
	@echo "make figures  redraw docs/img"
	@echo "make test     run the test suite"

$(BIN)/asicre:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e '.[dev]'

setup: $(BIN)/asicre

puzzle:
	git submodule update --init

pdk: setup
	$(BIN)/ciel enable --pdk-family sky130 --pdk-root $(SKY130_ROOT) \
		--include-libraries sky130_fd_sc_hd $(PDK_VERSION)

all: setup
	$(BIN)/asicre all

figures: setup
	$(BIN)/asicre figures

test: setup
	$(BIN)/pytest -q

clean:
	rm -rf build .pytest_cache
