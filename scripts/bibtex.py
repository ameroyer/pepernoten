# Usage:
#   uv run scripts/bibtex.py generate <arxiv_id>
#   uv run scripts/bibtex.py generate <arxiv_id> --update_note --bib_file=refs.bib
#   uv run scripts/bibtex.py batch 2405.12345 2406.67890 --bib_file=refs.bib
#   uv run scripts/bibtex.py clear_cache [<arxiv_id>]

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fire
from bibtex import generate, batch, clear_cache

if __name__ == "__main__":
    fire.Fire({"generate": generate, "batch": batch, "clear_cache": clear_cache})
