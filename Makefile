PY ?= python3
.PHONY: test starter full repro dictionary clean
test:       ; $(PY) -m pytest
starter:    ; $(PY) -m energygen generate --preset starter --out data/starter
full:       ; $(PY) -m energygen generate --preset full --formats csv --out data/full
repro:      ; $(PY) -m energygen repro-check --preset full
dictionary: ; $(PY) -m energygen dictionary --out docs/DATA_DICTIONARY.md
clean:      ; rm -rf out .pytest_cache
