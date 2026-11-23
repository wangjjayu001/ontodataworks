
CREATE TABLE wb_audit (
	id SERIAL NOT NULL, 
	workspace_id INTEGER, 
	actor VARCHAR(80) NOT NULL, 
	action VARCHAR(100) NOT NULL, 
	detail TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE wb_schema_version (
	version SERIAL NOT NULL, 
	installed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (version)
)

;


CREATE TABLE wb_user (
	id SERIAL NOT NULL, 
	username VARCHAR(80) NOT NULL, 
	display_name VARCHAR(100) NOT NULL, 
	password_hash TEXT NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	enabled INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
)

;


CREATE TABLE wb_workspace (
	id SERIAL NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE wb_datasource (
	id SERIAL NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	host VARCHAR(255) NOT NULL, 
	port INTEGER NOT NULL, 
	database VARCHAR(100) NOT NULL, 
	username VARCHAR(100) NOT NULL, 
	secret TEXT NOT NULL, 
	schemas JSON NOT NULL, 
	last_tested_at TIMESTAMP WITH TIME ZONE, 
	last_collected_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id)
)

;


CREATE TABLE wb_membership (
	id SERIAL NOT NULL, 
	user_id INTEGER NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, workspace_id), 
	FOREIGN KEY(user_id) REFERENCES wb_user (id), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id)
)

;


CREATE TABLE wb_modeling_document (
	id SERIAL NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	kind VARCHAR(24) NOT NULL, 
	code VARCHAR(100) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	description TEXT NOT NULL, 
	domain_id INTEGER, 
	layer VARCHAR(10) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	spec JSON NOT NULL, 
	revision INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (workspace_id, kind, code), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id), 
	FOREIGN KEY(domain_id) REFERENCES wb_modeling_document (id)
)

;


CREATE TABLE wb_session (
	token_hash VARCHAR(64) NOT NULL, 
	user_id INTEGER NOT NULL, 
	csrf VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (token_hash), 
	FOREIGN KEY(user_id) REFERENCES wb_user (id)
)

;


CREATE TABLE wb_catalog_asset (
	id SERIAL NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	datasource_id INTEGER NOT NULL, 
	namespace VARCHAR(100) NOT NULL, 
	table_name VARCHAR(150) NOT NULL, 
	name VARCHAR(150) NOT NULL, 
	description TEXT NOT NULL, 
	layer VARCHAR(10) NOT NULL, 
	domain_id INTEGER, 
	columns JSON NOT NULL, 
	row_estimate INTEGER, 
	fingerprint VARCHAR(64) NOT NULL, 
	state VARCHAR(20) NOT NULL, 
	collected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (datasource_id, namespace, table_name), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id), 
	FOREIGN KEY(datasource_id) REFERENCES wb_datasource (id), 
	FOREIGN KEY(domain_id) REFERENCES wb_modeling_document (id)
)

;


CREATE TABLE wb_modeling_revision (
	id SERIAL NOT NULL, 
	resource_id INTEGER NOT NULL, 
	revision INTEGER NOT NULL, 
	snapshot JSON NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(resource_id) REFERENCES wb_modeling_document (id) ON DELETE CASCADE
)

;


CREATE TABLE wb_query_run (
	id SERIAL NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	datasource_id INTEGER NOT NULL, 
	actor VARCHAR(80) NOT NULL, 
	sql TEXT NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	row_count INTEGER NOT NULL, 
	duration_ms INTEGER NOT NULL, 
	error TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id), 
	FOREIGN KEY(datasource_id) REFERENCES wb_datasource (id)
)

;


CREATE TABLE wb_lineage_evidence (
	id SERIAL NOT NULL, 
	workspace_id INTEGER NOT NULL, 
	query_id INTEGER NOT NULL, 
	asset_id INTEGER NOT NULL, 
	source VARCHAR(250) NOT NULL, 
	target VARCHAR(100) NOT NULL, 
	evidence JSON NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workspace_id) REFERENCES wb_workspace (id), 
	FOREIGN KEY(query_id) REFERENCES wb_query_run (id), 
	FOREIGN KEY(asset_id) REFERENCES wb_catalog_asset (id)
)

;
