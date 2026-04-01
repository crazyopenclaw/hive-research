-- ResearchSquid Neo4j Schema — run once at startup

-- Uniqueness constraints
CREATE CONSTRAINT finding_id_unique IF NOT EXISTS FOR (f:Finding) REQUIRE f.id IS UNIQUE;
CREATE CONSTRAINT source_id_unique IF NOT EXISTS FOR (s:Source) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT experiment_id_unique IF NOT EXISTS FOR (e:Experiment) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT experiment_run_id_unique IF NOT EXISTS FOR (r:ExperimentRun) REQUIRE r.id IS UNIQUE;
CREATE CONSTRAINT agent_id_unique IF NOT EXISTS FOR (a:Agent) REQUIRE a.id IS UNIQUE;
CREATE CONSTRAINT cluster_id_unique IF NOT EXISTS FOR (c:Cluster) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE;

-- Indexes for common queries
CREATE INDEX finding_status_idx IF NOT EXISTS FOR (f:Finding) ON (f.status);
CREATE INDEX finding_session_idx IF NOT EXISTS FOR (f:Finding) ON (f.session_id);
CREATE INDEX finding_agent_idx IF NOT EXISTS FOR (f:Finding) ON (f.agent_id);
CREATE INDEX finding_cluster_idx IF NOT EXISTS FOR (f:Finding) ON (f.cluster_id);
CREATE INDEX finding_evidence_type_idx IF NOT EXISTS FOR (f:Finding) ON (f.evidence_type);
CREATE INDEX experiment_status_idx IF NOT EXISTS FOR (e:Experiment) ON (e.status);
CREATE INDEX experiment_session_idx IF NOT EXISTS FOR (e:Experiment) ON (e.session_id);
CREATE INDEX experiment_run_status_idx IF NOT EXISTS FOR (r:ExperimentRun) ON (r.status);
CREATE INDEX agent_session_idx IF NOT EXISTS FOR (a:Agent) ON (a.session_id);
CREATE INDEX source_tier_idx IF NOT EXISTS FOR (s:Source) ON (s.tier);
