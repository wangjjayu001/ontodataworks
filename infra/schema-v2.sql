--
-- PostgreSQL database dump
--

\restrict KHq4TvdjVAfa2hlcXEPSn1LSKgKqA9g8D6HJAuerCpYKTLljgKE9Ii27Q9KI88h

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: wb_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_audit (
    id integer NOT NULL,
    workspace_id integer,
    actor character varying(80) NOT NULL,
    action character varying(100) NOT NULL,
    detail text NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_audit_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_audit_id_seq OWNED BY public.wb_audit.id;


--
-- Name: wb_catalog_asset; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_catalog_asset (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    datasource_id integer NOT NULL,
    namespace character varying(100) NOT NULL,
    table_name character varying(150) NOT NULL,
    name character varying(150) NOT NULL,
    description text NOT NULL,
    layer character varying(10) NOT NULL,
    domain_id integer,
    columns json NOT NULL,
    row_estimate integer,
    fingerprint character varying(64) NOT NULL,
    state character varying(20) NOT NULL,
    collected_at timestamp with time zone NOT NULL
);


--
-- Name: wb_catalog_asset_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_catalog_asset_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_catalog_asset_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_catalog_asset_id_seq OWNED BY public.wb_catalog_asset.id;


--
-- Name: wb_datasource; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_datasource (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    name character varying(100) NOT NULL,
    host character varying(255) NOT NULL,
    port integer NOT NULL,
    database character varying(100) NOT NULL,
    username character varying(100) NOT NULL,
    secret text NOT NULL,
    schemas json NOT NULL,
    last_tested_at timestamp with time zone,
    last_collected_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_datasource_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_datasource_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_datasource_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_datasource_id_seq OWNED BY public.wb_datasource.id;


--
-- Name: wb_lineage_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_lineage_evidence (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    query_id integer NOT NULL,
    asset_id integer NOT NULL,
    source character varying(250) NOT NULL,
    target character varying(100) NOT NULL,
    evidence json NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_lineage_evidence_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_lineage_evidence_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_lineage_evidence_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_lineage_evidence_id_seq OWNED BY public.wb_lineage_evidence.id;


--
-- Name: wb_membership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_membership (
    id integer NOT NULL,
    user_id integer NOT NULL,
    workspace_id integer NOT NULL
);


--
-- Name: wb_membership_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_membership_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_membership_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_membership_id_seq OWNED BY public.wb_membership.id;


--
-- Name: wb_modeling_document; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_modeling_document (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    kind character varying(24) NOT NULL,
    code character varying(100) NOT NULL,
    name character varying(150) NOT NULL,
    description text NOT NULL,
    domain_id integer,
    layer character varying(10) NOT NULL,
    status character varying(20) NOT NULL,
    spec json NOT NULL,
    revision integer NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: wb_modeling_document_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_modeling_document_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_modeling_document_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_modeling_document_id_seq OWNED BY public.wb_modeling_document.id;


--
-- Name: wb_modeling_revision; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_modeling_revision (
    id integer NOT NULL,
    resource_id integer NOT NULL,
    revision integer NOT NULL,
    snapshot json NOT NULL,
    actor character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_modeling_revision_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_modeling_revision_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_modeling_revision_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_modeling_revision_id_seq OWNED BY public.wb_modeling_revision.id;


--
-- Name: wb_query_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_query_run (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    datasource_id integer NOT NULL,
    actor character varying(80) NOT NULL,
    sql text NOT NULL,
    status character varying(20) NOT NULL,
    row_count integer NOT NULL,
    duration_ms integer NOT NULL,
    error text NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_query_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_query_run_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_query_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_query_run_id_seq OWNED BY public.wb_query_run.id;


--
-- Name: wb_schema_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_schema_version (
    version integer NOT NULL,
    installed_at timestamp with time zone NOT NULL
);


--
-- Name: wb_schema_version_version_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_schema_version_version_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_schema_version_version_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_schema_version_version_seq OWNED BY public.wb_schema_version.version;


--
-- Name: wb_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_session (
    token_hash character varying(64) NOT NULL,
    user_id integer NOT NULL,
    csrf character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: wb_transform_edge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_transform_edge (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    run_id integer NOT NULL,
    source_asset_id integer NOT NULL,
    target_asset_id integer NOT NULL
);


--
-- Name: wb_transform_edge_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_transform_edge_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_transform_edge_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_transform_edge_id_seq OWNED BY public.wb_transform_edge.id;


--
-- Name: wb_transform_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_transform_run (
    id integer NOT NULL,
    workspace_id integer NOT NULL,
    datasource_id integer NOT NULL,
    target_asset_id integer NOT NULL,
    run_key character varying(150) NOT NULL,
    kind character varying(40) NOT NULL,
    status character varying(40) NOT NULL,
    select_sql text NOT NULL,
    evidence json NOT NULL,
    actor character varying(80) NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_transform_run_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_transform_run_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_transform_run_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_transform_run_id_seq OWNED BY public.wb_transform_run.id;


--
-- Name: wb_user; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_user (
    id integer NOT NULL,
    username character varying(80) NOT NULL,
    display_name character varying(100) NOT NULL,
    password_hash text NOT NULL,
    role character varying(20) NOT NULL,
    enabled integer NOT NULL
);


--
-- Name: wb_user_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_user_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_user_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_user_id_seq OWNED BY public.wb_user.id;


--
-- Name: wb_workspace; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wb_workspace (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    description text NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: wb_workspace_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wb_workspace_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: wb_workspace_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wb_workspace_id_seq OWNED BY public.wb_workspace.id;


--
-- Name: wb_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_audit ALTER COLUMN id SET DEFAULT nextval('public.wb_audit_id_seq'::regclass);


--
-- Name: wb_catalog_asset id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset ALTER COLUMN id SET DEFAULT nextval('public.wb_catalog_asset_id_seq'::regclass);


--
-- Name: wb_datasource id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_datasource ALTER COLUMN id SET DEFAULT nextval('public.wb_datasource_id_seq'::regclass);


--
-- Name: wb_lineage_evidence id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_lineage_evidence ALTER COLUMN id SET DEFAULT nextval('public.wb_lineage_evidence_id_seq'::regclass);


--
-- Name: wb_membership id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_membership ALTER COLUMN id SET DEFAULT nextval('public.wb_membership_id_seq'::regclass);


--
-- Name: wb_modeling_document id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_document ALTER COLUMN id SET DEFAULT nextval('public.wb_modeling_document_id_seq'::regclass);


--
-- Name: wb_modeling_revision id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_revision ALTER COLUMN id SET DEFAULT nextval('public.wb_modeling_revision_id_seq'::regclass);


--
-- Name: wb_query_run id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_query_run ALTER COLUMN id SET DEFAULT nextval('public.wb_query_run_id_seq'::regclass);


--
-- Name: wb_schema_version version; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_schema_version ALTER COLUMN version SET DEFAULT nextval('public.wb_schema_version_version_seq'::regclass);


--
-- Name: wb_transform_edge id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge ALTER COLUMN id SET DEFAULT nextval('public.wb_transform_edge_id_seq'::regclass);


--
-- Name: wb_transform_run id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run ALTER COLUMN id SET DEFAULT nextval('public.wb_transform_run_id_seq'::regclass);


--
-- Name: wb_user id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_user ALTER COLUMN id SET DEFAULT nextval('public.wb_user_id_seq'::regclass);


--
-- Name: wb_workspace id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_workspace ALTER COLUMN id SET DEFAULT nextval('public.wb_workspace_id_seq'::regclass);


--
-- Name: wb_audit wb_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_audit
    ADD CONSTRAINT wb_audit_pkey PRIMARY KEY (id);


--
-- Name: wb_catalog_asset wb_catalog_asset_datasource_id_namespace_table_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset
    ADD CONSTRAINT wb_catalog_asset_datasource_id_namespace_table_name_key UNIQUE (datasource_id, namespace, table_name);


--
-- Name: wb_catalog_asset wb_catalog_asset_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset
    ADD CONSTRAINT wb_catalog_asset_pkey PRIMARY KEY (id);


--
-- Name: wb_datasource wb_datasource_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_datasource
    ADD CONSTRAINT wb_datasource_pkey PRIMARY KEY (id);


--
-- Name: wb_lineage_evidence wb_lineage_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_lineage_evidence
    ADD CONSTRAINT wb_lineage_evidence_pkey PRIMARY KEY (id);


--
-- Name: wb_membership wb_membership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_membership
    ADD CONSTRAINT wb_membership_pkey PRIMARY KEY (id);


--
-- Name: wb_membership wb_membership_user_id_workspace_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_membership
    ADD CONSTRAINT wb_membership_user_id_workspace_id_key UNIQUE (user_id, workspace_id);


--
-- Name: wb_modeling_document wb_modeling_document_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_document
    ADD CONSTRAINT wb_modeling_document_pkey PRIMARY KEY (id);


--
-- Name: wb_modeling_document wb_modeling_document_workspace_id_kind_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_document
    ADD CONSTRAINT wb_modeling_document_workspace_id_kind_code_key UNIQUE (workspace_id, kind, code);


--
-- Name: wb_modeling_revision wb_modeling_revision_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_revision
    ADD CONSTRAINT wb_modeling_revision_pkey PRIMARY KEY (id);


--
-- Name: wb_query_run wb_query_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_query_run
    ADD CONSTRAINT wb_query_run_pkey PRIMARY KEY (id);


--
-- Name: wb_schema_version wb_schema_version_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_schema_version
    ADD CONSTRAINT wb_schema_version_pkey PRIMARY KEY (version);


--
-- Name: wb_session wb_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_session
    ADD CONSTRAINT wb_session_pkey PRIMARY KEY (token_hash);


--
-- Name: wb_transform_edge wb_transform_edge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge
    ADD CONSTRAINT wb_transform_edge_pkey PRIMARY KEY (id);


--
-- Name: wb_transform_run wb_transform_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run
    ADD CONSTRAINT wb_transform_run_pkey PRIMARY KEY (id);


--
-- Name: wb_transform_run wb_transform_run_workspace_id_run_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run
    ADD CONSTRAINT wb_transform_run_workspace_id_run_key_key UNIQUE (workspace_id, run_key);


--
-- Name: wb_user wb_user_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_user
    ADD CONSTRAINT wb_user_pkey PRIMARY KEY (id);


--
-- Name: wb_user wb_user_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_user
    ADD CONSTRAINT wb_user_username_key UNIQUE (username);


--
-- Name: wb_workspace wb_workspace_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_workspace
    ADD CONSTRAINT wb_workspace_pkey PRIMARY KEY (id);


--
-- Name: wb_catalog_asset wb_catalog_asset_datasource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset
    ADD CONSTRAINT wb_catalog_asset_datasource_id_fkey FOREIGN KEY (datasource_id) REFERENCES public.wb_datasource(id);


--
-- Name: wb_catalog_asset wb_catalog_asset_domain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset
    ADD CONSTRAINT wb_catalog_asset_domain_id_fkey FOREIGN KEY (domain_id) REFERENCES public.wb_modeling_document(id);


--
-- Name: wb_catalog_asset wb_catalog_asset_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_catalog_asset
    ADD CONSTRAINT wb_catalog_asset_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_datasource wb_datasource_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_datasource
    ADD CONSTRAINT wb_datasource_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_lineage_evidence wb_lineage_evidence_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_lineage_evidence
    ADD CONSTRAINT wb_lineage_evidence_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.wb_catalog_asset(id);


--
-- Name: wb_lineage_evidence wb_lineage_evidence_query_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_lineage_evidence
    ADD CONSTRAINT wb_lineage_evidence_query_id_fkey FOREIGN KEY (query_id) REFERENCES public.wb_query_run(id);


--
-- Name: wb_lineage_evidence wb_lineage_evidence_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_lineage_evidence
    ADD CONSTRAINT wb_lineage_evidence_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_membership wb_membership_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_membership
    ADD CONSTRAINT wb_membership_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.wb_user(id);


--
-- Name: wb_membership wb_membership_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_membership
    ADD CONSTRAINT wb_membership_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_modeling_document wb_modeling_document_domain_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_document
    ADD CONSTRAINT wb_modeling_document_domain_id_fkey FOREIGN KEY (domain_id) REFERENCES public.wb_modeling_document(id);


--
-- Name: wb_modeling_document wb_modeling_document_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_document
    ADD CONSTRAINT wb_modeling_document_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_modeling_revision wb_modeling_revision_resource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_modeling_revision
    ADD CONSTRAINT wb_modeling_revision_resource_id_fkey FOREIGN KEY (resource_id) REFERENCES public.wb_modeling_document(id) ON DELETE CASCADE;


--
-- Name: wb_query_run wb_query_run_datasource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_query_run
    ADD CONSTRAINT wb_query_run_datasource_id_fkey FOREIGN KEY (datasource_id) REFERENCES public.wb_datasource(id);


--
-- Name: wb_query_run wb_query_run_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_query_run
    ADD CONSTRAINT wb_query_run_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_session wb_session_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_session
    ADD CONSTRAINT wb_session_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.wb_user(id);


--
-- Name: wb_transform_edge wb_transform_edge_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge
    ADD CONSTRAINT wb_transform_edge_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.wb_transform_run(id);


--
-- Name: wb_transform_edge wb_transform_edge_source_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge
    ADD CONSTRAINT wb_transform_edge_source_asset_id_fkey FOREIGN KEY (source_asset_id) REFERENCES public.wb_catalog_asset(id);


--
-- Name: wb_transform_edge wb_transform_edge_target_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge
    ADD CONSTRAINT wb_transform_edge_target_asset_id_fkey FOREIGN KEY (target_asset_id) REFERENCES public.wb_catalog_asset(id);


--
-- Name: wb_transform_edge wb_transform_edge_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_edge
    ADD CONSTRAINT wb_transform_edge_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- Name: wb_transform_run wb_transform_run_datasource_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run
    ADD CONSTRAINT wb_transform_run_datasource_id_fkey FOREIGN KEY (datasource_id) REFERENCES public.wb_datasource(id);


--
-- Name: wb_transform_run wb_transform_run_target_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run
    ADD CONSTRAINT wb_transform_run_target_asset_id_fkey FOREIGN KEY (target_asset_id) REFERENCES public.wb_catalog_asset(id);


--
-- Name: wb_transform_run wb_transform_run_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wb_transform_run
    ADD CONSTRAINT wb_transform_run_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.wb_workspace(id);


--
-- PostgreSQL database dump complete
--

\unrestrict KHq4TvdjVAfa2hlcXEPSn1LSKgKqA9g8D6HJAuerCpYKTLljgKE9Ii27Q9KI88h