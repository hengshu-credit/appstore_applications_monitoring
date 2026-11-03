from .db import APP_KEY,REGION

TABLES={
    'app_icons':f'''id BIGINT AUTO_INCREMENT PRIMARY KEY, {APP_KEY}, {REGION}, name TEXT,
        source_url TEXT,source_updated DOUBLE,file_path TEXT,mime VARCHAR(32),checked_at DOUBLE,
        requested BOOLEAN NOT NULL DEFAULT FALSE,enabled BOOLEAN NOT NULL DEFAULT TRUE,
        status VARCHAR(16) NOT NULL DEFAULT 'available',refresh_hours DOUBLE NOT NULL DEFAULT 168,
        due DOUBLE NOT NULL DEFAULT 0,lease_until DOUBLE NOT NULL DEFAULT 0,token CHAR(32),
        failures INT NOT NULL DEFAULT 0,last_error TEXT,
        UNIQUE KEY icon_identity(store,app_id,country,language),INDEX icon_queue(requested,enabled,due,lease_until)''',
}
