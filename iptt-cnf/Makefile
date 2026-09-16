.PHONY: up down migrate test lint etl verify fmt

up:          ## start postgres + api locally
	docker compose up --build -d

down:
	docker compose down

migrate:
	cd backend && alembic upgrade head

test:
	cd backend && python -m pytest tests/ -q

web-build:
	cd frontend && npm run build

web-check:
	cd frontend && npx tsc --noEmit

test-all:    ## everything; needs an empty migrated database, no fixture data
	cd backend && IPTT_TEST_DB_READY=1 python -m pytest tests/ -q

lint:
	cd backend && ruff check app tests etl

fmt:
	cd backend && ruff format app tests etl

etl:         ## SQLITE=/path/to/iptt.db make etl
	cd backend && python -m etl.migrate_from_sqlite --sqlite $(SQLITE) --force

verify:
	cd backend && python -m etl.verify_migration --sqlite $(SQLITE)
