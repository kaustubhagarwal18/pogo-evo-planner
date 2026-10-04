.PHONY: install install-ocr test test-fast lint gamemaster demo mock

# full game data once `make gamemaster` has fetched it, else the bundled sample
SPECIES := $(if $(wildcard data/latest.json),--species data/latest.json)

install:            ## planner + scanner, Tesseract fallback OCR
	pip install -e '.[dev]'

install-ocr:        ## add RapidOCR + RapidFuzz (recommended)
	pip install -e '.[ocr,dev]'

test:               ## full suite incl. end-to-end OCR on synthetic screens (~30 s)
	python -m unittest discover -s tests -t . -v

test-fast:          ## skip the slow OCR end-to-end test
	SKIP_SLOW=1 python -m unittest discover -s tests -t .

lint:
	ruff check pogo_evo_planner tests

gamemaster:         ## download the latest datamined game master to data/latest.json
	./scripts/fetch_gamemaster.sh

demo:               ## plan from the bundled example inventory
	pogo-evo-planner plan examples/inventory_sample.json $(SPECIES)

mock:               ## render synthetic screens + recording into mock/ and scan them
	python tests/mockscreens.py mock
	pogo-evo-planner scan mock/bag.png mock/recording.mp4 -o mock/inventory.json --plan $(SPECIES)
