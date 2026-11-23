# OntoDataWorks

**A DataWorks-inspired data platform with an ontology-driven semantic data query agent.**

OntoDataWorks connects ingestion, warehouse modeling, SQL development, scheduling, quality, catalog/lineage and data services with governed semantic assets, versioned metric bindings, retrieval and controlled NL2SQL execution.

Built with React 18, FastAPI and PostgreSQL. “DataWorks” describes the capability model; Alibaba Cloud DataWorks APIs, MaxCompute and OpenDataWorks MCP are not connected in this implementation. Exact/BM25/graph retrieval is implemented; vector retrieval is currently disabled. Model execution uses a local Codex bridge for public/synthetic validation, and production Agent execution remains disabled.

- [Deployment README (Chinese)](README.md)
- [Features and use cases](docs/features/README.md)
- [Semantic assets and Agent](docs/features/semantic-agent.md)
- [Capability mapping](docs/features/dataworks-capabilities.md)
- [Architecture](docs/architecture/README.md)
- [Module and PR maintenance](docs/maintenance/modules.md)
- [Contributing](CONTRIBUTING.md)

The deployment guide covers a fresh database, local secrets, Windows Docker Desktop and an optional model runtime. No data warehouse, credentials or live acceptance payloads are included.

The source is publicly visible under the existing [license notice](LICENSE); an open-source redistribution license has not yet been selected. Third-party licenses remain unchanged.
