from .db import APP_KEY,REGION

TABLES={
    'app_media_assets':'''id BIGINT AUTO_INCREMENT PRIMARY KEY,store VARCHAR(16) CHARACTER SET ascii NOT NULL,
        url_hash CHAR(64) CHARACTER SET ascii NOT NULL UNIQUE,source_url TEXT NOT NULL,label VARCHAR(64) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,manual_paused BOOLEAN NOT NULL DEFAULT FALSE,refresh_hours DOUBLE NOT NULL DEFAULT 168,due DOUBLE NOT NULL DEFAULT 0,
        lease_until DOUBLE NOT NULL DEFAULT 0,token CHAR(32),failures INT NOT NULL DEFAULT 0,last_error TEXT,
        file_path TEXT,mime VARCHAR(32),checked_at DOUBLE,INDEX media_queue(enabled,due,lease_until)''',
    'app_media_refs':f'''{APP_KEY},{REGION},kind VARCHAR(32) NOT NULL,position INT NOT NULL,asset_id BIGINT NOT NULL,
        PRIMARY KEY(store,app_id,country,language,kind,position),INDEX media_reference(asset_id,store,app_id)''',
    'immutable_sources':'''source_key CHAR(64) CHARACTER SET ascii PRIMARY KEY,content LONGTEXT NOT NULL,
        sha256 CHAR(64) CHARACTER SET ascii NOT NULL,fetched_at DOUBLE NOT NULL''',
    'icon_downloads':'''url_hash CHAR(64) CHARACTER SET ascii PRIMARY KEY,source_url TEXT NOT NULL,
        file_path TEXT NOT NULL,mime VARCHAR(32) NOT NULL,fetched_at DOUBLE NOT NULL''',
    'app_icons':f'''id BIGINT AUTO_INCREMENT PRIMARY KEY, {APP_KEY}, {REGION}, name TEXT,
        source_url TEXT,source_updated DOUBLE,file_path TEXT,mime VARCHAR(32),checked_at DOUBLE,
        requested BOOLEAN NOT NULL DEFAULT FALSE,enabled BOOLEAN NOT NULL DEFAULT TRUE,
        status VARCHAR(16) NOT NULL DEFAULT 'available',refresh_hours DOUBLE NOT NULL DEFAULT 168,
        due DOUBLE NOT NULL DEFAULT 0,lease_until DOUBLE NOT NULL DEFAULT 0,token CHAR(32),
        failures INT NOT NULL DEFAULT 0,last_error TEXT,
        UNIQUE KEY icon_identity(store,app_id,country,language),INDEX icon_queue(requested,enabled,due,lease_until),
        INDEX icon_source_queue(requested,enabled,source_url(191),due,lease_until)''',
}
