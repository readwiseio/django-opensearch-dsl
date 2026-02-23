# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-opensearch-dsl is a Django wrapper around opensearch-py that maps Django models to OpenSearch documents for automatic indexing and search. Fork maintained by Readwise.

## Commands

### Testing
```bash
# Run all tests (requires running OpenSearch - see Docker section)
DJANGO_SETTINGS_MODULE=tests.project.settings python3 -m django test tests.tests

# Run a single test class/method
DJANGO_SETTINGS_MODULE=tests.project.settings python3 -m django test tests.tests.test_documents.DocumentTestCase.test_get_indexing_queryset

# Run with tox (matrix: Python 3.9-3.13 × Django 4.2-5.2 × OpenSearch 1-3)
tox -e py312-django52-opensearch30
```

### Linting & Formatting
```bash
black -l 120 django_opensearch_dsl/ tests/
isort django_opensearch_dsl/ tests/
pycodestyle django_opensearch_dsl tests
pydocstyle --count django_opensearch_dsl/ tests/
mypy django_opensearch_dsl --disallow-untyped-def
bandit --ini=setup.cfg -ll

# Or run all checks at once:
./bin/pre_commit.sh
```

### OpenSearch Docker Containers
```bash
docker-compose up -d opensearch_test_30_0 opensearch_test_30_1  # v3 on ports 9230-9231
docker-compose up -d opensearch_test_20_0 opensearch_test_20_1  # v2 on ports 9220-9221
docker-compose up -d opensearch_test_10_0 opensearch_test_10_1  # v1 on ports 9210-9211
```

### Build
```bash
pip install build && python -m build
```

## Architecture

**Registration flow:** `@registry.register_document` on a Document subclass → registry maps Django model fields to OpenSearch field types → signals connect model changes to index updates.

**Key components:**
- **`documents.py`** — `Document` base class. Nested `Django` class specifies model/fields, nested `Index` class configures the OpenSearch index. `prepare()` converts model instances to docs, `update()` does bulk indexing, `get_indexing_queryset()` handles chunked iteration with keyset pagination.
- **`registries.py`** — `DocumentRegistry` maintains model→document→index mappings. Handles cascade updates for related models.
- **`fields.py`** — `DODField` base with types like `ObjectField`, `NestedField`, `ListField`. Auto-mapping from Django model fields via `model_field_class_to_field_class` dict in documents.py.
- **`signals.py`** — `RealTimeSignalProcessor` (sync) and `CelerySignalProcessor` (async) handle post_save/pre_delete/m2m_changed.
- **`aliases.py`** — Stateless utility functions for alias-based zero-downtime index deployments. Versioned physical indices (e.g. `products_20260223143052`) sit behind an alias (`products`); atomic switchover via `activate_alias()`.
- **`management/commands/opensearch.py`** — `index` subcommands: `create`, `delete`, `rebuild`, `update`, `activate`, `cleanup`. `document` subcommands: `index`, `delete`, `update`. Supports `--using`, `--filter`, `--parallel`, `--new-version`, `--keep`.

**Test app:** `tests/django_dummy_app/` has models (Continent, Country, Event) and documents used by tests in `tests/tests/`.

## Code Style

- Line length: 120 characters
- Formatting: black + isort
- Docstrings: numpy convention (pydocstyle)
- Type checking: mypy with `--disallow-untyped-def`

## Gotchas

- Most unit tests (`test_aliases`, `test_fields`, `test_indices`, `test_registries`) run without OpenSearch. Tests in `test_documents` and `test_signals` require a running OpenSearch instance (see Docker section).
- Linting tools (`black`, `isort`, `pycodestyle`, etc.) are not installed in the venv — use `tox` or install them separately.
- The `index create` command always creates a new versioned physical index. If an alias already exists it is left unchanged; use `index activate` to switch it.
