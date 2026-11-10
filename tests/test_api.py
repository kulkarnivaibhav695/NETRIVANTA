import os,tempfile,sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['NETRIVANTA_DATABASE']=os.path.join(tempfile.gettempdir(),'netrivanta-v2-test.db')
os.environ['NETRIVANTA_SECRET_KEY']='test-secret-key-that-is-long-enough-for-hmac'
try:os.remove(os.environ['NETRIVANTA_DATABASE'])
except FileNotFoundError:pass
from fastapi.testclient import TestClient
from app.main import app,hash_password
from app.database import init_db,connect
init_db(); client=TestClient(app)

def create_user(email='volunteer@example.org',role='user'):
    with connect() as db: db.execute('INSERT INTO users(name,email,password_hash,role,consent) VALUES(?,?,?,?,1)',('Test User',email,hash_password('correct-horse-battery-staple'),role))

def test_public_health_and_private_api():
    assert client.get('/api/health').status_code==200
    assert client.get('/api/dashboard/summary').status_code==401
    assert client.get('/api/volunteers').status_code==401
    assert client.get('/api/measurements').status_code==401

def test_signup_login_and_role_access():
    r=client.post('/api/auth/signup',json={'name':'New User','email':'new@example.org','password':'correct-horse-battery-staple','consent':True})
    assert r.status_code==200
    r=client.post('/api/auth/login',json={'email':'new@example.org','password':'correct-horse-battery-staple'})
    assert r.status_code==200 and r.json()['redirect']=='/dashboard'
    assert client.get('/api/dashboard/summary').status_code==200
    assert client.get('/api/admin/summary').status_code==403

def test_agent_registration_submission_and_privacy():
    create_user()
    assert client.post('/api/auth/login',json={'email':'volunteer@example.org','password':'correct-horse-battery-staple'}).status_code==200
    r=client.post('/api/volunteers',json={'city':'Pune','isp':'Pilot ISP','access_medium':'Fiber','wifi_type':'ethernet'})
    assert r.status_code==200,r.text
    key=r.json()['api_key']
    m=client.post('/api/measurements',headers={'X-API-Key':key},json={'latency_ms':22.5,'packet_loss_pct':0,'dns_delay_ms':12,'download_mbps':45,'upload_mbps':8,'jitter_ms':1.2,'target_host':'example.com'})
    assert m.status_code==200,m.text
    assert client.get('/api/measurements').json()[0]['latency_ms']==22.5
    assert client.get('/api/measurements/mine').json()[0]['latency_ms']==22.5
    assert client.get('/api/volunteers').status_code==403

def test_admin_review_and_admin_apis():
    create_user('admin@example.org','admin')
    assert client.post('/api/auth/login',json={'email':'admin@example.org','password':'correct-horse-battery-staple'}).status_code==200
    assert client.get('/api/admin/summary').status_code==200
    assert client.get('/api/admin/users').status_code==200
    assert client.get('/api/admin/data-quality').status_code==200

def test_probe_limits_and_routes():
    assert client.get('/api/probe/download?bytes=1024').status_code==200
    assert client.get('/api/probe/download?bytes=2000000').status_code==422
    upload=client.post('/api/probe/upload',content=b'x'*65536,headers={'Content-Type':'application/octet-stream'})
    assert upload.status_code==200 and upload.json()['received_bytes']==65536
    assert client.get('/measurements').status_code==200

def test_all_admin_routes_require_admin():
    paths=['/api/admin/summary','/api/admin/users','/api/admin/measurements','/api/admin/analytics','/api/admin/agents','/api/admin/data-quality','/api/admin/audit-logs','/api/admin/campaigns','/api/admin/reports.csv','/api/volunteers']
    # Remove the current test client's session before checking the boundary.
    client.post('/api/auth/logout')
    for path in paths:
        assert client.get(path).status_code==401, path

def test_signup_requires_consent_and_does_not_accept_role():
    r=client.post('/api/auth/signup',json={'name':'No Consent','email':'noconsent@example.org','password':'correct-horse-battery-staple','consent':False,'role':'admin'})
    assert r.status_code==422
    r=client.post('/api/auth/signup',json={'name':'Regular Person','email':'regular@example.org','password':'correct-horse-battery-staple','consent':True,'role':'admin'})
    assert r.status_code==200
    client.post('/api/auth/login',json={'email':'regular@example.org','password':'correct-horse-battery-staple'})
    assert client.get('/api/auth/me').json()['role']=='user'


def test_ist_session_window_boundaries():
    from app.main import measurement_session
    ist=timezone(timedelta(hours=5,minutes=30))
    assert measurement_session(datetime(2026,11,10,7,0,tzinfo=ist))=='morning'
    assert measurement_session(datetime(2026,11,10,9,59,tzinfo=ist))=='morning'
    assert measurement_session(datetime(2026,11,10,10,0,tzinfo=ist)) is None
    assert measurement_session(datetime(2026,11,10,13,0,tzinfo=ist))=='afternoon'
    assert measurement_session(datetime(2026,11,10,16,0,tzinfo=ist)) is None
    assert measurement_session(datetime(2026,11,10,19,0,tzinfo=ist))=='evening'
    assert measurement_session(datetime(2026,11,10,22,0,tzinfo=ist)) is None

def test_browser_measurement_session_and_daily_analysis(monkeypatch):
    create_user('browser@example.org')
    login=client.post('/api/auth/login',json={'email':'browser@example.org','password':'correct-horse-battery-staple'})
    assert login.status_code==200
    endpoint=client.post('/api/volunteers',json={'city':'Pune','isp':'Pilot ISP','access_medium':'Fiber','wifi_type':'ethernet'})
    assert endpoint.status_code==200,endpoint.text
    endpoint_id=endpoint.json()['id']
    from app import main as main_module
    ist=timezone(timedelta(hours=5,minutes=30))
    monkeypatch.setattr(main_module,'current_ist',lambda:datetime(2026,11,10,14,0,tzinfo=ist))
    payload={'endpoint_id':endpoint_id,'latency_ms':23.5,'jitter_ms':1.2,'download_mbps':80.0,'upload_mbps':12.0}
    saved=client.post('/api/browser/measurements',json=payload)
    assert saved.status_code==200,saved.text
    assert saved.json()['session_name']=='afternoon'
    duplicate=client.post('/api/browser/measurements',json=payload)
    assert duplicate.status_code==409
    history=client.get('/api/measurements/mine').json()
    assert history[0]['measurement_method']=='browser_http'
    assert history[0]['session_date']=='2026-11-10'
    daily=client.get('/api/measurements/daily-averages').json()
    assert daily[0]['date']=='2026-11-10' and daily[0]['latency_ms']==23.5
    analysis=client.get('/api/measurements/analysis?days=3').json()
    assert analysis['days_available']==1 and analysis['period_averages']['download_mbps']==80.0
    monkeypatch.setattr(main_module,'current_ist',lambda:datetime(2026,11,10,11,0,tzinfo=ist))
    outside=client.post('/api/browser/measurements',json=payload)
    assert outside.status_code==403

def test_browser_measurement_cannot_use_another_users_endpoint(monkeypatch):
    create_user('owner@example.org')
    client.post('/api/auth/login',json={'email':'owner@example.org','password':'correct-horse-battery-staple'})
    endpoint=client.post('/api/volunteers',json={'city':'Pune','isp':'Pilot ISP','access_medium':'Fiber'})
    endpoint_id=endpoint.json()['id']
    create_user('intruder@example.org')
    client.post('/api/auth/login',json={'email':'intruder@example.org','password':'correct-horse-battery-staple'})
    from app import main as main_module
    ist=timezone(timedelta(hours=5,minutes=30))
    monkeypatch.setattr(main_module,'current_ist',lambda:datetime(2026,11,10,14,0,tzinfo=ist))
    response=client.post('/api/browser/measurements',json={'endpoint_id':endpoint_id,'latency_ms':10})
    assert response.status_code==404


def test_postgres_adapter_converts_placeholders_and_insert_ids():
    from app.database import PgConnection
    class FakeCursor:
        def __init__(self): self.sql=None; self.params=None; self.rowcount=1
        def execute(self,sql,params): self.sql=sql; self.params=params
        def fetchone(self): return {'id': 41}
        def fetchall(self): return []
    class FakeConnection:
        def __init__(self): self.last=None
        def cursor(self): self.last=FakeCursor(); return self.last
    fake=FakeConnection(); pg=PgConnection(fake)
    inserted=pg.execute('INSERT INTO users(name,email) VALUES(?,?)',('A','a@example.org'))
    assert inserted.lastrowid==41
    assert fake.last.sql=='INSERT INTO users(name,email) VALUES(%s,%s) RETURNING id'
    pg.execute('INSERT OR IGNORE INTO campaign_members(campaign_id,user_id) VALUES(?,?)',(1,2))
    assert fake.last.sql=='INSERT INTO campaign_members(campaign_id,user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING'


def test_sqlite_upgrade_adds_session_columns_to_existing_measurements(tmp_path,monkeypatch):
    import sqlite3
    from app import database
    old_db=tmp_path/'old_netrivanta.db'
    conn=sqlite3.connect(old_db)
    conn.executescript("""
    CREATE TABLE users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'user',organization TEXT DEFAULT '',city TEXT DEFAULT '',state TEXT DEFAULT '',country TEXT DEFAULT '',consent INTEGER NOT NULL DEFAULT 0,active INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE volunteers(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,city TEXT NOT NULL,site_label TEXT NOT NULL DEFAULT '',isp TEXT NOT NULL,access_medium TEXT NOT NULL,medium_evidence TEXT NOT NULL DEFAULT '',medium_status TEXT NOT NULL DEFAULT 'self_reported',wifi_type TEXT NOT NULL DEFAULT 'unknown',device_label TEXT NOT NULL DEFAULT '',api_key TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE measurements(id INTEGER PRIMARY KEY AUTOINCREMENT,volunteer_id INTEGER NOT NULL,measured_at TEXT NOT NULL,latency_ms REAL,packet_loss_pct REAL,dns_delay_ms REAL,download_mbps REAL,upload_mbps REAL,jitter_ms REAL,loaded_jitter_ms REAL,target_host TEXT NOT NULL DEFAULT '',wifi_signal_dbm REAL,connection_interface TEXT NOT NULL DEFAULT '',test_status TEXT NOT NULL DEFAULT 'complete',error_detail TEXT NOT NULL DEFAULT '');
    """)
    conn.commit();conn.close()
    monkeypatch.setattr(database,'USE_POSTGRES',False)
    monkeypatch.setattr(database,'DB_PATH',str(old_db))
    database.init_db()
    with database.connect() as db:
        columns={r['name'] for r in db.execute('PRAGMA table_info(measurements)')}
        assert {'review_status','session_date','session_name','timezone','measurement_method'} <= columns
        indexes={r['name'] for r in db.execute('PRAGMA index_list(measurements)')}
        assert 'idx_measurement_successful_session' in indexes

def test_all_frontend_routes_serve_the_application():
    paths=['/login','/signup','/forgot-password','/admin/login','/dashboard','/comparison','/measurements','/volunteers','/agent','/my-measurements','/campaigns','/account','/notifications','/help','/admin/dashboard','/admin/volunteers','/admin/measurements','/admin/verification','/admin/campaigns','/admin/analytics','/admin/users','/admin/agents','/admin/data-quality','/admin/reports','/admin/audit-logs','/admin/settings']
    for path in paths:
        response=client.get(path)
        assert response.status_code==200,path
        assert 'NETRIVANTA' in response.text


def test_application_startup_bootstraps_admin_and_health_checks_database(tmp_path,monkeypatch):
    from app import database
    monkeypatch.setattr(database,'USE_POSTGRES',False)
    monkeypatch.setattr(database,'DB_PATH',str(tmp_path/'lifespan.db'))
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('NETRIVANTA_SECRET_KEY','startup-test-secret-at-least-32-characters')
    monkeypatch.setenv('NETRIVANTA_ADMIN_NAME','NETRIVANTA QA Admin')
    monkeypatch.setenv('NETRIVANTA_ADMIN_EMAIL','bootstrap-admin@example.org')
    monkeypatch.setenv('NETRIVANTA_ADMIN_PASSWORD','bootstrap-admin-password-123')
    with TestClient(app) as startup_client:
        health=startup_client.get('/api/health')
        assert health.status_code==200 and health.json()['database']=='connected'
        login=startup_client.post('/api/auth/login',json={'email':'bootstrap-admin@example.org','password':'bootstrap-admin-password-123'})
        assert login.status_code==200 and login.json()['role']=='admin'

def test_password_change():
    create_user('password@example.org')
    client.post('/api/auth/login',json={'email':'password@example.org','password':'correct-horse-battery-staple'})
    r=client.patch('/api/auth/password',json={'current_password':'correct-horse-battery-staple','new_password':'new-correct-horse-battery-staple'})
    assert r.status_code==200
    client.post('/api/auth/logout')
    assert client.post('/api/auth/login',json={'email':'password@example.org','password':'new-correct-horse-battery-staple'}).status_code==200
