.PHONY: help setup install test clean run

help: ## Show this help message
	@echo "Image Squisher - Makefile Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

setup: ## Run automated setup (creates venv and installs dependencies)
	@chmod +x setup.sh
	@./setup.sh

install: ## Install Python dependencies (assumes venv is activated)
	pip install --upgrade pip
	pip install -r requirements.txt

test: ## Run a test on a sample folder (requires venv activation)
	@echo "Note: Create a test folder with sample images first"
	@echo "Usage: source venv/bin/activate && python main.py /path/to/test/images"

clean: ## Remove virtual environment and log files
	rm -rf venv/
	rm -f image-squisher.log
	rm -f *.tmp.jxl *.tmp.webp
	@echo "Cleaned up virtual environment and temporary files"

run: ## Run the script (requires venv activation and folder argument)
	@if [ -z "$(FOLDER)" ]; then \
		echo "Usage: make run FOLDER=/path/to/images"; \
		exit 1; \
	fi
	source venv/bin/activate && python main.py "$(FOLDER)"

