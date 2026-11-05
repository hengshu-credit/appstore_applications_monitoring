TABLES={'source_accounts': '''provider VARCHAR(32) PRIMARY KEY, secret LONGTEXT NOT NULL,
    status VARCHAR(32) NOT NULL, checked_at DOUBLE NULL, retry_after DOUBLE NOT NULL DEFAULT 0,
    updated_at DOUBLE NOT NULL'''}

TABLES.update({
    'appfigures_products': '''run_id BIGINT NOT NULL, product_id VARCHAR(20) NOT NULL,
        PRIMARY KEY(run_id,product_id)''',
    'appfigures_blocks': '''run_id BIGINT NOT NULL, kind VARCHAR(32) NOT NULL, error TEXT NOT NULL,
        PRIMARY KEY(run_id,kind)''',
    'appfigures_runs': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, settings LONGTEXT NOT NULL,
        status VARCHAR(32) NOT NULL, created_at DOUBLE NOT NULL, stop_at DOUBLE NOT NULL,
        last_chart_day CHAR(10), last_error TEXT''',
    'appfigures_jobs': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, run_id BIGINT NOT NULL,
        fingerprint CHAR(64) NOT NULL, kind VARCHAR(32) NOT NULL, path TEXT NOT NULL,
        params LONGTEXT NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0, due DOUBLE NOT NULL DEFAULT 0,
        artifact_id BIGINT, error TEXT, UNIQUE KEY af_job(run_id,fingerprint),
        INDEX af_due(run_id,status,due,id)''',
    'appfigures_artifacts': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, sha256 CHAR(64) NOT NULL,
        path TEXT NOT NULL, kind VARCHAR(32) NOT NULL, provenance LONGTEXT NOT NULL,
        records BIGINT NOT NULL, created_at DOUBLE NOT NULL''',
    'appfigures_budget': '''day CHAR(10) PRIMARY KEY, used INT NOT NULL DEFAULT 0,
        source_limit INT NOT NULL DEFAULT 1000, next_at DOUBLE NOT NULL DEFAULT 0''',
})
