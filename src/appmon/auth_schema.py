"""Persistent accounts, grants and revocable browser sessions."""

TABLES = {
    'auth_settings': '''id INT PRIMARY KEY, registration_enabled BOOLEAN NOT NULL DEFAULT TRUE''',
    'auth_users': '''id BIGINT PRIMARY KEY AUTO_INCREMENT,
        username VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
        password_hash VARCHAR(256) NOT NULL, recovery_hash CHAR(64) NOT NULL UNIQUE,
        role VARCHAR(16) NOT NULL, member_until DOUBLE NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at DOUBLE NOT NULL''',
    'auth_sessions': '''token_hash CHAR(64) PRIMARY KEY, user_id BIGINT NOT NULL,
        expires_at DOUBLE NOT NULL, INDEX(user_id), INDEX(expires_at)''',
    'auth_roles': '''role VARCHAR(16) PRIMARY KEY, permissions TEXT NOT NULL''',
    'auth_overrides': '''user_id BIGINT PRIMARY KEY, permissions TEXT NOT NULL''',
    'auth_limits': '''bucket CHAR(64) PRIMARY KEY, attempts INT NOT NULL, expires_at DOUBLE NOT NULL,
        INDEX(expires_at)''',
    'auth_audit': '''id BIGINT PRIMARY KEY AUTO_INCREMENT, actor_id BIGINT NULL,
        target_id BIGINT NULL, action VARCHAR(64) NOT NULL, created_at DOUBLE NOT NULL''',
}
