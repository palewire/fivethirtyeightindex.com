# Mark all the commands that don't have a target
.PHONY: help test lint fix format install type-check build clean build-docs serve-docs site-data site-install site-dev site-build
.DEFAULT_GOAL := help

#
# Colors
#

# Define ANSI color codes
RESET_COLOR   = \033[m

BLUE       = \033[1;34m
YELLOW     = \033[1;33m
GREEN      = \033[1;32m
RED        = \033[1;31m
BLACK      = \033[1;30m
MAGENTA    = \033[1;35m
CYAN       = \033[1;36m
WHITE      = \033[1;37m

DBLUE      = \033[0;34m
DYELLOW    = \033[0;33m
DGREEN     = \033[0;32m
DRED       = \033[0;31m
DBLACK     = \033[0;30m
DMAGENTA   = \033[0;35m
DCYAN      = \033[0;36m
DWHITE     = \033[0;37m

BG_WHITE   = \033[47m
BG_RED     = \033[41m
BG_GREEN   = \033[42m
BG_YELLOW  = \033[43m
BG_BLUE    = \033[44m
BG_MAGENTA = \033[45m
BG_CYAN    = \033[46m

# Name some of the colors
COM_COLOR   = $(DBLUE)
OBJ_COLOR   = $(DCYAN)
OK_COLOR    = $(DGREEN)
ERROR_COLOR = $(DRED)
WARN_COLOR  = $(DYELLOW)
NO_COLOR    = $(RESET_COLOR)

OK_STRING    = "[OK]"
ERROR_STRING = "[ERROR]"
WARN_STRING  = "[WARNING]"

define banner
    @echo "  $(WHITE)__________$(RESET_COLOR)"
    @echo "$(WHITE) |$(DWHITE) PALEWIRE $(RESET_COLOR)$(WHITE)|$(RESET_COLOR)"
    @echo "$(WHITE) |&&& ======|$(RESET_COLOR)"
    @echo "$(WHITE) |=== ======|$(RESET_COLOR)  $(DWHITE)This is a $(RESET_COLOR)$(DBLACK)$(BG_WHITE)palewire$(RESET_COLOR)$(DWHITE) automation$(RESET_COLOR)"
    @echo "$(WHITE) |=== == %%%|$(RESET_COLOR)"
    @echo "$(WHITE) |[_] ======|$(RESET_COLOR)  $(1)"
    @echo "$(WHITE) |=== ===!##|$(RESET_COLOR)"
    @echo "$(WHITE) |__________|$(RESET_COLOR)"
    @echo ""
endef

#
# Python helpers
#

UV := uv run
PYTHON := python -W ignore -m

#
# Commands
#

help: ## Show this help. Example: make help
	@egrep -h '\s##\s' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies with uv
	$(call banner,  ⚙️  Installing dependencies ⚙️ )
	uv sync --all-extras

test: ## Run tests with coverage
	$(call banner,  🧪 Running tests 🧪)
	uv run pytest --cov -sv

lint: ## Check code with ruff
	$(call banner,  🛡️  Linting code 🛡️ )
	uv run ruff check

format: ## Format code with ruff
	$(call banner,  🎨 Formatting code 🎨 )
	uv run ruff format

fix: ## Auto-fix linting issues
	$(call banner,  🔧 Auto-fixing issues 🔧)
	@uv run ruff check --fix

type-check: ## Verify static typing with ty
	$(call banner,  🔍 Verifying static typing 🔍)
	uv run ty check

build: ## Build distribution packages
	$(call banner,  📦 Building distribution packages 📦)
	uv build --sdist --wheel

clean: ## Remove build artifacts
	$(call banner,  🧹 Cleaning build artifacts 🧹)
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +

build-docs: ## Build the docs
	$(call banner,  📚 Building docs 📚)
	@rm -rf _build/
	@rm -rf docs/_build
	@cd docs && $(UV) make html

serve-docs: ## Test the site
	$(call banner,  🧪 Serving test site 🧪)
	@rm -rf _build/
	@rm -rf docs/_build
	@cd docs && $(UV) make livehtml

site-data: ## Build web/static/data/articles.json from curated.csv + enriched.csv
	$(call banner,  📦 Building site data 📦)
	uv run fakethirtyeight build-site-data

site-install: ## Install web/ npm dependencies
	$(call banner,  ⚙️  Installing web dependencies ⚙️ )
	cd web && npm install

site-dev: ## Run the SvelteKit dev server (npm run dev)
	$(call banner,  🌐 Running SvelteKit dev server 🌐)
	cd web && npm run dev

site-build: ## Build the static site (npm run build)
	$(call banner,  🏗️  Building static site 🏗️ )
	cd web && npm run build
