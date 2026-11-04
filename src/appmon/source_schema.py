TABLES={'source_accounts': '''provider VARCHAR(32) PRIMARY KEY, secret LONGTEXT NOT NULL,
    status VARCHAR(32) NOT NULL, checked_at DOUBLE NULL, retry_after DOUBLE NOT NULL DEFAULT 0,
    updated_at DOUBLE NOT NULL'''}
