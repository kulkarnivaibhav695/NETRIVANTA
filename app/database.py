import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.getenv('NETRIVANTA_DATABASE_URL') or os.getenv('DATABASE_URL') or os.getenv('NETRIVANTA_DATABASE', './netrivanta.db')
USE_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))
DB_PATH = DATABASE_URL

SQLITE_SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', organization TEXT DEFAULT '',
 city TEXT DEFAULT '', state TEXT DEFAULT '', country TEXT DEFAULT '', consent INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS volunteers (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, city TEXT NOT NULL, site_label TEXT NOT NULL DEFAULT '',
 isp TEXT NOT NULL, access_medium TEXT NOT NULL, medium_evidence TEXT NOT NULL DEFAULT '',
 medium_status TEXT NOT NULL DEFAULT 'self_reported', wifi_type TEXT NOT NULL DEFAULT 'unknown',
 device_label TEXT NOT NULL DEFAULT '', api_key TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS measurements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, volunteer_id INTEGER NOT NULL REFERENCES volunteers(id) ON DELETE CASCADE,
 measured_at TEXT NOT NULL, latency_ms REAL, packet_loss_pct REAL, dns_delay_ms REAL, download_mbps REAL,
 upload_mbps REAL, jitter_ms REAL, loaded_jitter_ms REAL, target_host TEXT NOT NULL DEFAULT '',
 wifi_signal_dbm REAL, connection_interface TEXT NOT NULL DEFAULT '', test_status TEXT NOT NULL DEFAULT 'complete',
 error_detail TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'pending',
 session_date TEXT, session_name TEXT, timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
 measurement_method TEXT NOT NULL DEFAULT 'python_agent'
);
CREATE TABLE IF NOT EXISTS campaigns (
 id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '', target_medium TEXT NOT NULL DEFAULT 'All',
 target_participants INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS campaign_members (
 campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(campaign_id,user_id)
);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', read_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
 details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_measurements_volunteer_date ON measurements(volunteer_id,measured_at);
'''

POSTGRES_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', organization TEXT DEFAULT '',
 city TEXT DEFAULT '', state TEXT DEFAULT '', country TEXT DEFAULT '', consent INTEGER NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
CREATE TABLE IF NOT EXISTS volunteers (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
 name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, city TEXT NOT NULL, site_label TEXT NOT NULL DEFAULT '',
 isp TEXT NOT NULL, access_medium TEXT NOT NULL, medium_evidence TEXT NOT NULL DEFAULT '',
 medium_status TEXT NOT NULL DEFAULT 'self_reported', wifi_type TEXT NOT NULL DEFAULT 'unknown',
 device_label TEXT NOT NULL DEFAULT '', api_key TEXT NOT NULL UNIQUE,
 created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS measurements (
 id BIGSERIAL PRIMARY KEY, volunteer_id BIGINT NOT NULL REFERENCES volunteers(id) ON DELETE CASCADE,
 measured_at TEXT NOT NULL, latency_ms DOUBLE PRECISION, packet_loss_pct DOUBLE PRECISION, dns_delay_ms DOUBLE PRECISION,
 download_mbps DOUBLE PRECISION, upload_mbps DOUBLE PRECISION, jitter_ms DOUBLE PRECISION, loaded_jitter_ms DOUBLE PRECISION,
 target_host TEXT NOT NULL DEFAULT '', wifi_signal_dbm DOUBLE PRECISION, connection_interface TEXT NOT NULL DEFAULT '',
 test_status TEXT NOT NULL DEFAULT 'complete', error_detail TEXT NOT NULL DEFAULT '', review_status TEXT NOT NULL DEFAULT 'pending',
 session_date TEXT, session_name TEXT, timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
 measurement_method TEXT NOT NULL DEFAULT 'python_agent'
);
CREATE TABLE IF NOT EXISTS campaigns (
 id BIGSERIAL PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL DEFAULT '',
 end_date TEXT NOT NULL DEFAULT '', target_medium TEXT NOT NULL DEFAULT 'All', target_participants INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
CREATE TABLE IF NOT EXISTS campaign_members (
 campaign_id BIGINT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
 user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, joined_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
 PRIMARY KEY(campaign_id,user_id)
);
CREATE TABLE IF NOT EXISTS notifications (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', read_at TEXT, created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id BIGSERIAL PRIMARY KEY, admin_user_id BIGINT, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
 details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
);
'''

class PgCursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self.rowcount = cursor.rowcount
    def fetchone(self): return self._cursor.fetchone()
    def fetchall(self): return self._cursor.fetchall()

class PgConnection:
    def __init__(self, conn): self._conn = conn
    def execute(self, sql, params=()):
        statement = sql.strip()
        ignore = bool(re.match(r'INSERT\s+OR\s+IGNORE\s+INTO\s+', statement, re.I))
        if ignore:
            statement = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO\s+', 'INSERT INTO ', statement, count=1, flags=re.I)
            statement = statement.rstrip().rstrip(';') + ' ON CONFLICT DO NOTHING'
        needs_id = bool(re.match(r'INSERT\s+INTO\s+(users|volunteers|measurements|campaigns)\s*(?:\(|\s)', statement, re.I)) and 'RETURNING' not in statement.upper()
        if needs_id: statement = statement.rstrip().rstrip(';') + ' RETURNING id'
        statement = statement.replace('?', '%s')
        cur = self._conn.cursor()
        cur.execute(statement, tuple(params or ()))
        lastrowid = None
        if needs_id:
            row = cur.fetchone()
            if row: lastrowid = row['id']
        return PgCursor(cur, lastrowid)
    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self): self._conn.close()

@contextmanager
def connect():
    if USE_POSTGRES:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise RuntimeError('PostgreSQL support requires psycopg2-binary. Install requirements.txt.') from exc
        dsn = DATABASE_URL
        if 'sslmode=' not in dsn:
            dsn += ('&' if '?' in dsn else '?') + 'sslmode=require'
        conn = psycopg2.connect(dsn, connect_timeout=15, cursor_factory=RealDictCursor)
        wrapped = PgConnection(conn)
        try:
            yield wrapped
            wrapped.commit()
        except Exception:
            wrapped.rollback()
            raise
        finally:
            wrapped.close()
        return
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('PRAGMA busy_timeout=30000')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    with connect() as db:
        if USE_POSTGRES:
            for statement in POSTGRES_SCHEMA.split(';'):
                if statement.strip(): db.execute(statement)
            db.execute('CREATE INDEX IF NOT EXISTS idx_measurements_volunteer_date ON measurements(volunteer_id,measured_at)')
            db.execute('ALTER TABLE volunteers ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users(id) ON DELETE SET NULL')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT \'pending\'')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS session_date TEXT')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS session_name TEXT')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT \'Asia/Kolkata\'')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS measurement_method TEXT NOT NULL DEFAULT \'python_agent\'')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS packet_loss_pct DOUBLE PRECISION')
            db.execute('ALTER TABLE measurements ADD COLUMN IF NOT EXISTS dns_delay_ms DOUBLE PRECISION')
            db.execute('CREATE INDEX IF NOT EXISTS idx_volunteers_user ON volunteers(user_id)')
        else:
            db.executescript(SQLITE_SCHEMA)
            vcols = {r['name'] for r in db.execute('PRAGMA table_info(volunteers)')}
            if 'user_id' not in vcols:
                db.execute('ALTER TABLE volunteers ADD COLUMN user_id INTEGER REFERENCES users(id)')
            db.execute('CREATE INDEX IF NOT EXISTS idx_volunteers_user ON volunteers(user_id)')
            mcols = {r['name'] for r in db.execute('PRAGMA table_info(measurements)')}
            migrations = {
                'review_status': "TEXT NOT NULL DEFAULT 'pending'", 'session_date': 'TEXT', 'session_name': 'TEXT',
                'timezone': "TEXT NOT NULL DEFAULT 'Asia/Kolkata'", 'measurement_method': "TEXT NOT NULL DEFAULT 'python_agent'",
                'packet_loss_pct': 'REAL', 'dns_delay_ms': 'REAL',
            }
            for column, definition in migrations.items():
                if column not in mcols: db.execute(f'ALTER TABLE measurements ADD COLUMN {column} {definition}')
        db.execute('CREATE INDEX IF NOT EXISTS idx_measurements_session ON measurements(session_date,session_name,volunteer_id)')
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_measurement_successful_session ON measurements(volunteer_id,session_date,session_name) WHERE session_date IS NOT NULL AND session_name IS NOT NULL AND test_status='complete'")
