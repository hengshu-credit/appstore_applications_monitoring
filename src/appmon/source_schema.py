TABLES={'source_accounts': '''provider VARCHAR(32) PRIMARY KEY, secret LONGTEXT NOT NULL,
    status VARCHAR(32) NOT NULL, checked_at DOUBLE NULL, retry_after DOUBLE NOT NULL DEFAULT 0,
    updated_at DOUBLE NOT NULL'''}

TABLES['data_source_configs']='''name VARCHAR(32) PRIMARY KEY, settings LONGTEXT NOT NULL,
    updated_at DOUBLE NOT NULL'''
TABLES['source_bulk_imports']='''digest CHAR(64) CHARACTER SET ascii PRIMARY KEY,dataset VARCHAR(32) NOT NULL,
    file_name VARCHAR(128) NOT NULL,status VARCHAR(32) NOT NULL,processed BIGINT NOT NULL DEFAULT 0,
    valid BIGINT NOT NULL DEFAULT 0,rejected BIGINT NOT NULL DEFAULT 0,created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL,error TEXT'''
TABLES['public_app_baselines']='''dataset VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    store VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,app_id VARCHAR(512) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload LONGTEXT NOT NULL,source_observed_at DOUBLE,imported_at DOUBLE NOT NULL,
    file_digest CHAR(64) CHARACTER SET ascii NOT NULL,PRIMARY KEY(dataset,store,app_id)'''

TABLES.update({
    'marketdata_products': '''run_id BIGINT NOT NULL, product_id VARCHAR(20) NOT NULL,
        PRIMARY KEY(run_id,product_id)''',
    'marketdata_blocks': '''run_id BIGINT NOT NULL, kind VARCHAR(32) NOT NULL, error TEXT NOT NULL,
        PRIMARY KEY(run_id,kind)''',
    'marketdata_runs': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, settings LONGTEXT NOT NULL,
        status VARCHAR(32) NOT NULL, created_at DOUBLE NOT NULL, stop_at DOUBLE NOT NULL,
        last_chart_day CHAR(10), last_error TEXT''',
    'marketdata_jobs': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, run_id BIGINT NOT NULL,
        fingerprint CHAR(64) NOT NULL, kind VARCHAR(32) NOT NULL, path TEXT NOT NULL,
        params LONGTEXT NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'pending',
        attempts INT NOT NULL DEFAULT 0, due DOUBLE NOT NULL DEFAULT 0,
        artifact_id BIGINT, error TEXT, UNIQUE KEY af_job(run_id,fingerprint),
        INDEX af_due(run_id,status,due,id)''',
    'marketdata_artifacts': '''id BIGINT AUTO_INCREMENT PRIMARY KEY, sha256 CHAR(64) NOT NULL,
        path TEXT NOT NULL, kind VARCHAR(32) NOT NULL, provenance LONGTEXT NOT NULL,
        records BIGINT NOT NULL, created_at DOUBLE NOT NULL''',
    'marketdata_budget': '''day CHAR(10) PRIMARY KEY, used INT NOT NULL DEFAULT 0,
        source_limit INT NOT NULL DEFAULT 1000, next_at DOUBLE NOT NULL DEFAULT 0''',
})
