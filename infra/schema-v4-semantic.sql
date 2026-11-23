-- v4 additive semantic schema; caller holds platform migration lock.
-- Does not record wb_schema_version until all v4 module migrations succeed.


CREATE TABLE IF NOT EXISTS wb_semantic_asset_revision (
	workspace_id INTEGER NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	asset_type VARCHAR(24) NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	actor VARCHAR(80) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, asset_id, revision)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_asset_revision_asset_type ON wb_semantic_asset_revision (asset_type);


CREATE TABLE IF NOT EXISTS wb_semantic_governance (
	workspace_id INTEGER NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	dependency_hash VARCHAR(64) NOT NULL,
	workflow_state VARCHAR(24) NOT NULL,
	validation_state VARCHAR(24) NOT NULL,
	record_version INTEGER NOT NULL,
	issue_codes JSON NOT NULL,
	PRIMARY KEY (workspace_id, asset_id),
	FOREIGN KEY(workspace_id, asset_id, revision) REFERENCES wb_semantic_asset_revision (workspace_id, asset_id, revision)
)

;


CREATE TABLE IF NOT EXISTS wb_semantic_revision_ref (
	id SERIAL NOT NULL,
	workspace_id INTEGER NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	slot TEXT NOT NULL,
	target JSON NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(workspace_id, asset_id, revision) REFERENCES wb_semantic_asset_revision (workspace_id, asset_id, revision)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_revision_ref_workspace_id ON wb_semantic_revision_ref (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_verification (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_verification_asset_id ON wb_semantic_verification (asset_id);

CREATE INDEX IF NOT EXISTS ix_wb_semantic_verification_workspace_id ON wb_semantic_verification (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_approval (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_approval_workspace_id ON wb_semantic_approval (workspace_id);

CREATE INDEX IF NOT EXISTS ix_wb_semantic_approval_asset_id ON wb_semantic_approval (asset_id);


CREATE TABLE IF NOT EXISTS wb_semantic_catalog_snapshot (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_catalog_snapshot_workspace_id ON wb_semantic_catalog_snapshot (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_release (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	scope VARCHAR(24) NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_release_workspace_id ON wb_semantic_release (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_index_snapshot (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	release_id VARCHAR(100) NOT NULL,
	release_hash VARCHAR(64) NOT NULL,
	content JSON NOT NULL,
	documents JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_index_snapshot_workspace_id ON wb_semantic_index_snapshot (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_deployment (
	workspace_id INTEGER NOT NULL,
	environment VARCHAR(24) NOT NULL,
	pointer_version INTEGER NOT NULL,
	release_id VARCHAR(100) NOT NULL,
	release_hash VARCHAR(64) NOT NULL,
	index_snapshot_id VARCHAR(100) NOT NULL,
	index_snapshot_hash VARCHAR(64) NOT NULL,
	actor_id INTEGER NOT NULL,
	activated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, environment)
)

;


CREATE TABLE IF NOT EXISTS wb_semantic_outbox (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	event_type VARCHAR(80) NOT NULL,
	content JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_outbox_workspace_id ON wb_semantic_outbox (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_migration_staging (
	workspace_id INTEGER NOT NULL,
	old_uid VARCHAR(250) NOT NULL,
	role VARCHAR(24) NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	migration_status VARCHAR(40) NOT NULL,
	PRIMARY KEY (workspace_id, old_uid)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_semantic_migration_staging_role ON wb_semantic_migration_staging (role);


CREATE TABLE IF NOT EXISTS wb_semantic_execution_contract (
	workspace_id INTEGER NOT NULL,
	binding_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (workspace_id, binding_id, revision)
)

;


CREATE TABLE IF NOT EXISTS wb_semantic_source_snapshot (
	workspace_id INTEGER NOT NULL,
	source_id VARCHAR(200) NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	content JSON NOT NULL,
	PRIMARY KEY (workspace_id, source_id, content_hash)
)

;


CREATE TABLE IF NOT EXISTS wb_semantic_command (
	id VARCHAR(64) NOT NULL,
	workspace_id INTEGER NOT NULL,
	actor_id INTEGER NOT NULL,
	input_hash VARCHAR(64) NOT NULL,
	response JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;


CREATE TABLE IF NOT EXISTS wb_agent_retrieval_bundle (
	id VARCHAR(100) NOT NULL,
	workspace_id INTEGER NOT NULL,
	run_id VARCHAR(100) NOT NULL,
	requirement_id VARCHAR(100) NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
)

;

CREATE INDEX IF NOT EXISTS ix_wb_agent_retrieval_bundle_run_id ON wb_agent_retrieval_bundle (run_id);

CREATE INDEX IF NOT EXISTS ix_wb_agent_retrieval_bundle_workspace_id ON wb_agent_retrieval_bundle (workspace_id);


CREATE TABLE IF NOT EXISTS wb_semantic_role_assignment (
	workspace_id INTEGER NOT NULL,
	user_id INTEGER NOT NULL,
	role VARCHAR(24) NOT NULL,
	asset_id VARCHAR(200) NOT NULL,
	active INTEGER NOT NULL,
	assigned_by INTEGER NOT NULL,
	record_version INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, user_id, role, asset_id)
)

;


CREATE TABLE IF NOT EXISTS wb_semantic_policy (
	workspace_id INTEGER NOT NULL,
	policy_id VARCHAR(200) NOT NULL,
	revision INTEGER NOT NULL,
	content JSON NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	active INTEGER NOT NULL,
	created_by INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (workspace_id, policy_id, revision)
)

;

CREATE OR REPLACE FUNCTION wb_semantic_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Semantic revisions are immutable'; END $$;

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_asset_revision;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_asset_revision FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_approval;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_approval FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_verification;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_verification FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_catalog_snapshot;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_catalog_snapshot FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_source_snapshot;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_source_snapshot FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_release;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_release FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_index_snapshot;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_index_snapshot FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();

DROP TRIGGER IF EXISTS immutable_content ON wb_semantic_execution_contract;

CREATE TRIGGER immutable_content BEFORE UPDATE OR DELETE ON wb_semantic_execution_contract FOR EACH ROW EXECUTE FUNCTION wb_semantic_revision_immutable();
