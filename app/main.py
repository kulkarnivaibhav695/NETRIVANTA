import os, io, hmac, hashlib, secrets, base64, json
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Query, Depends, Request, Response, Body
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, EmailStr, ConfigDict
from .database import connect, init_db
from .analysis import grouped_comparison, METRICS

COOKIE='netrivanta_session'; SESSION_DAYS=7

def _b64(data:bytes)->str: return base64.urlsafe_b64encode(data).decode().rstrip('=')
def _unb64(s:str)->bytes: return base64.urlsafe_b64decode(s+'='*(-len(s)%4))
def _secret()->bytes:
    value=os.getenv('NETRIVANTA_SECRET_KEY','')
    if len(value)<32:
        if os.getenv('APP_ENV','').lower()=='production':
            raise RuntimeError('NETRIVANTA_SECRET_KEY must be configured with at least 32 characters in production')
        # Development fallback only; production must set a persistent secret.
        value='development-only-change-this-secret-key-before-deployment'
    return value.encode()
def hash_password(password:str)->str:
    salt=secrets.token_bytes(16); digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,310000)
    return f'pbkdf2_sha256$310000${_b64(salt)}${_b64(digest)}'
def verify_password(password:str,stored:str)->bool:
    try:
        scheme,rounds,salt,digest=stored.split('$'); actual=hashlib.pbkdf2_hmac('sha256',password.encode(),_unb64(salt),int(rounds))
        return scheme=='pbkdf2_sha256' and hmac.compare_digest(actual,_unb64(digest))
    except Exception: return False
def make_session(user_id:int,role:str)->str:
    payload=_b64(json.dumps({'uid':user_id,'role':role,'exp':int((datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)).timestamp())},separators=(',',':')).encode())
    sig=_b64(hmac.new(_secret(),payload.encode(),hashlib.sha256).digest())
    return payload+'.'+sig
def read_session(token:str):
    try:
        payload,sig=token.split('.',1); expected=_b64(hmac.new(_secret(),payload.encode(),hashlib.sha256).digest())
        if not hmac.compare_digest(sig,expected): return None
        data=json.loads(_unb64(payload));
        if data['exp']<int(datetime.now(timezone.utc).timestamp()): return None
        return data
    except Exception: return None

def current_user(request:Request):
    data=read_session(request.cookies.get(COOKIE,''))
    if not data: raise HTTPException(401,'Please log in to continue')
    with connect() as db: row=db.execute('SELECT id,name,email,role,organization,city,state,country,active FROM users WHERE id=?',(data['uid'],)).fetchone()
    if not row or not row['active']: raise HTTPException(401,'Account is inactive')
    return dict(row)
def admin_user(user=Depends(current_user)):
    if user['role']!='admin': raise HTTPException(403,'Administrator access required')
    return user
def audit(db,admin,action,target='',details=''):
    db.execute('INSERT INTO audit_logs(admin_user_id,action,target,details) VALUES(?,?,?,?)',(admin['id'],action,str(target),str(details)[:1000]))

@asynccontextmanager
async def lifespan(_app):
    if os.getenv('APP_ENV','').lower() == 'production':
        _secret()
    init_db()
    # Bootstrap admin only when explicitly configured and no admin exists.
    email=os.getenv('NETRIVANTA_ADMIN_EMAIL','').strip().lower(); password=os.getenv('NETRIVANTA_ADMIN_PASSWORD','')
    if email and password:
        with connect() as db:
            exists=db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
            if not exists:
                db.execute('INSERT OR IGNORE INTO users(name,email,password_hash,role,consent) VALUES(?,?,?,?,1)',(os.getenv('NETRIVANTA_ADMIN_NAME','NETRIVANTA Administrator'),email,hash_password(password),'admin'))
    yield

app=FastAPI(title='NETRIVANTA',version='3.0.0',description='Internet quality measurement and comparison research platform',lifespan=lifespan)
app.mount('/static',StaticFiles(directory='app/static'),name='static')

class SignupIn(BaseModel):
    name:str=Field(min_length=2,max_length=100); email:EmailStr; password:str=Field(min_length=10,max_length=128)
    organization:str=''; city:str=''; state:str=''; country:str='India'; consent:bool
class LoginIn(BaseModel): email:EmailStr; password:str
class VolunteerIn(BaseModel):
    name:str=Field(min_length=2,max_length=100); email:EmailStr; city:str=Field(min_length=2,max_length=100)
    site_label:str=''; isp:str=Field(min_length=2,max_length=100); access_medium:str
    medium_evidence:str=''; medium_status:str='self_reported'; wifi_type:str='unknown'; device_label:str=''
class MeasurementIn(BaseModel):
    model_config=ConfigDict(extra='ignore')
    measured_at:Optional[str]=None; latency_ms:Optional[float]=Field(default=None,ge=0,le=60000)
    packet_loss_pct:Optional[float]=Field(default=None,ge=0,le=100); dns_delay_ms:Optional[float]=Field(default=None,ge=0,le=60000)
    download_mbps:Optional[float]=Field(default=None,ge=0,le=10000); upload_mbps:Optional[float]=Field(default=None,ge=0,le=10000)
    jitter_ms:Optional[float]=Field(default=None,ge=0,le=60000); loaded_jitter_ms:Optional[float]=Field(default=None,ge=0,le=60000)
    target_host:str=''; wifi_signal_dbm:Optional[float]=None; connection_interface:str=''; test_status:str='complete'; error_detail:str=''
class BrowserMeasurementIn(BaseModel):
    endpoint_id: int = Field(ge=1)
    latency_ms: float = Field(ge=0, le=60000)
    jitter_ms: Optional[float] = Field(default=None, ge=0, le=60000)
    download_mbps: Optional[float] = Field(default=None, ge=0, le=10000)
    upload_mbps: Optional[float] = Field(default=None, ge=0, le=10000)
    target_host: str = 'netrivanta-controlled-http-probe'
    packet_loss_pct: Optional[float] = Field(default=None, ge=0, le=100)
    dns_delay_ms: Optional[float] = Field(default=None, ge=0, le=60000)


def current_ist():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def measurement_session(at):
    minute = at.hour * 60 + at.minute
    if 7 * 60 <= minute < 10 * 60:
        return 'morning'
    if 13 * 60 <= minute < 16 * 60:
        return 'afternoon'
    if 19 * 60 <= minute < 22 * 60:
        return 'evening'
    return None


class CampaignIn(BaseModel):
    title:str=Field(min_length=3,max_length=150); description:str=''; start_date:str=''; end_date:str=''; target_medium:str='All'; target_participants:int=Field(default=0,ge=0); status:str='draft'
class ReviewIn(BaseModel): status:str
class UserRoleIn(BaseModel): role:str
class AgentIn(BaseModel): city:str; isp:str; access_medium:str; site_label:str=''; wifi_type:str='unknown'; device_label:str=''; medium_evidence:str=''

@app.get('/',response_class=HTMLResponse)
def home():
    with open('app/static/index.html',encoding='utf-8') as f:return f.read()
@app.get('/api/health')
def health():
    try:
        with connect() as db: db.execute('SELECT 1').fetchone()
    except Exception as exc:
        raise HTTPException(503, 'Database unavailable') from exc
    return {'status':'ok','service':'NETRIVANTA','version':'3.0.0','database':'connected','utc':datetime.now(timezone.utc).isoformat()}

@app.post('/api/auth/signup')
def signup(data:SignupIn):
    if not data.consent: raise HTTPException(422,'Consent is required to participate')
    with connect() as db:
        try: cur=db.execute('INSERT INTO users(name,email,password_hash,organization,city,state,country,consent) VALUES(?,?,?,?,?,?,?,1)',(data.name,data.email.lower(),hash_password(data.password),data.organization,data.city,data.state,data.country))
        except Exception as e:
            if 'UNIQUE' in str(e):raise HTTPException(409,'An account with this email already exists')
            raise
        return {'id':cur.lastrowid,'message':'Account created. Please log in.'}
@app.post('/api/auth/login')
def login(data:LoginIn,response:Response):
    with connect() as db: row=db.execute('SELECT * FROM users WHERE email=?',(data.email.lower(),)).fetchone()
    if not row or not verify_password(data.password,row['password_hash']) or not row['active']:raise HTTPException(401,'Invalid email or password')
    response.set_cookie(COOKIE,make_session(row['id'],row['role']),httponly=True,secure=os.getenv('COOKIE_SECURE','false').lower()=='true',samesite='lax',max_age=SESSION_DAYS*86400,path='/')
    return {'id':row['id'],'name':row['name'],'role':row['role'],'redirect':'/admin/dashboard' if row['role']=='admin' else '/dashboard'}
@app.post('/api/auth/logout')
def logout(response:Response):response.delete_cookie(COOKIE,path='/');return {'message':'Logged out'}
@app.get('/api/auth/me')
def me(user=Depends(current_user)):return user

@app.post('/api/volunteers')
def create_volunteer(data:AgentIn,user=Depends(current_user)):
    allowed={'Fiber','Cellular','Fixed Wireless','Satellite','Other'}
    if data.access_medium not in allowed:raise HTTPException(422,'Unsupported access medium')
    key=secrets.token_urlsafe(36)
    with connect() as db:
        try:cur=db.execute('INSERT INTO volunteers(user_id,name,email,city,site_label,isp,access_medium,medium_evidence,medium_status,wifi_type,device_label,api_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(user['id'],user['name'],f"endpoint-{user['id']}-{secrets.token_hex(6)}@endpoint.netrivanta.invalid",data.city,data.site_label,data.isp,data.access_medium,data.medium_evidence,'self_reported',data.wifi_type,data.device_label,key))
        except Exception as e:
            if 'UNIQUE' in str(e):raise HTTPException(409,'This account already has a registered endpoint')
            raise
        db.execute('INSERT INTO notifications(user_id,title,body) VALUES(?,?,?)',(user['id'],'Measurement endpoint registered','Your endpoint is ready. Open Measurement Agent during an IST session to start a browser measurement.'))
        return {'id':cur.lastrowid,'api_key':key,'message':'Endpoint registered. Save this key; it is displayed only once.'}
@app.get('/api/volunteers')
def volunteers(user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT v.id,v.user_id,v.name,v.email,v.city,v.site_label,v.isp,v.access_medium,v.medium_status,v.wifi_type,v.device_label,v.created_at,v.active,COUNT(m.id) measurement_count FROM volunteers v LEFT JOIN measurements m ON m.volunteer_id=v.id GROUP BY v.id ORDER BY v.created_at DESC').fetchall()]
@app.get('/api/volunteers/overview')
def volunteer_overview(user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT access_medium,COUNT(*) volunteer_count,SUM(active) active_count FROM volunteers GROUP BY access_medium ORDER BY access_medium').fetchall()]
@app.get('/api/volunteers/mine')
def my_volunteers(user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT id,city,site_label,isp,access_medium,medium_status,wifi_type,device_label,created_at,active,(SELECT COUNT(*) FROM measurements m WHERE m.volunteer_id=v.id) measurement_count FROM volunteers v WHERE user_id=?',(user['id'],)).fetchall()]
@app.patch('/api/volunteers/{volunteer_id}/medium-review')
def review_medium(volunteer_id:int,data:ReviewIn,user=Depends(admin_user)):
    if data.status not in {'verified','self_reported','rejected'}:raise HTTPException(422,'Invalid review status')
    with connect() as db:
        cur=db.execute('UPDATE volunteers SET medium_status=? WHERE id=?',(data.status,volunteer_id))
        if not cur.rowcount:raise HTTPException(404,'Volunteer not found')
        audit(db,user,'medium_review',volunteer_id,data.status)
    return {'volunteer_id':volunteer_id,'medium_status':data.status}
@app.patch('/api/admin/agents/{volunteer_id}/active')
def set_agent_active(volunteer_id:int,active:bool,user=Depends(admin_user)):
    with connect() as db:
        cur=db.execute('UPDATE volunteers SET active=? WHERE id=?',(int(active),volunteer_id))
        if not cur.rowcount: raise HTTPException(404,'Agent not found')
        audit(db,user,'agent_active',volunteer_id,active)
    return {'id':volunteer_id,'active':active}
@app.patch('/api/admin/volunteers/{volunteer_id}/active')
def set_volunteer_active(volunteer_id:int,active:bool,user=Depends(admin_user)):
    with connect() as db:
        cur=db.execute('UPDATE volunteers SET active=? WHERE id=?',(int(active),volunteer_id))
        if not cur.rowcount:raise HTTPException(404,'Volunteer not found')
        audit(db,user,'volunteer_active',volunteer_id,active)
    return {'id':volunteer_id,'active':active}

@app.post('/api/measurements')
def submit_measurement(data:MeasurementIn,x_api_key:str=Header(default='')):
    if not x_api_key:raise HTTPException(401,'Missing X-API-Key')
    with connect() as db:
        v=db.execute('SELECT id FROM volunteers WHERE api_key=? AND active=1',(x_api_key,)).fetchone()
        if not v:raise HTTPException(401,'Invalid or inactive API key')
        at=data.measured_at or datetime.now(timezone.utc).isoformat(); vals=[getattr(data,k) for k in METRICS]
        cur=db.execute('INSERT INTO measurements(volunteer_id,measured_at,latency_ms,packet_loss_pct,dns_delay_ms,download_mbps,upload_mbps,jitter_ms,loaded_jitter_ms,target_host,wifi_signal_dbm,connection_interface,test_status,error_detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(v['id'],at,*vals,data.target_host,data.wifi_signal_dbm,data.connection_interface,data.test_status,data.error_detail))
        return {'id':cur.lastrowid,'volunteer_id':v['id'],'measured_at':at,'status':'stored'}
@app.post('/api/browser/measurements')
def submit_browser_measurement(data: BrowserMeasurementIn, user=Depends(current_user)):
    local_now = current_ist()
    session_name = measurement_session(local_now)
    if session_name is None:
        raise HTTPException(403, 'Measurements are available only during the scheduled sessions: 07:00–10:00, 13:00–16:00 and 19:00–22:00 IST.')
    session_date = local_now.date().isoformat()
    with connect() as db:
        endpoint = db.execute('SELECT id FROM volunteers WHERE id=? AND user_id=? AND active=1', (data.endpoint_id, user['id'])).fetchone()
        if not endpoint:
            raise HTTPException(404, 'Active measurement endpoint not found for this account.')
        existing = db.execute("SELECT id FROM measurements WHERE volunteer_id=? AND session_date=? AND session_name=? AND test_status='complete' LIMIT 1", (data.endpoint_id, session_date, session_name)).fetchone()
        if existing:
            raise HTTPException(409, 'A successful measurement is already recorded for this session.')
        measured_at = datetime.now(timezone.utc).isoformat()
        try:
            cur = db.execute('''INSERT INTO measurements
                (volunteer_id,measured_at,latency_ms,packet_loss_pct,dns_delay_ms,download_mbps,upload_mbps,jitter_ms,target_host,
                 connection_interface,test_status,error_detail,session_date,session_name,timezone,measurement_method)
                VALUES(?,?,?,?,?,?,?,?,?,'browser','complete','',?,?, 'Asia/Kolkata','browser_http')''',
                (data.endpoint_id, measured_at, data.latency_ms, data.packet_loss_pct, data.dns_delay_ms,
                 data.download_mbps, data.upload_mbps, data.jitter_ms, data.target_host[:200], session_date, session_name))
        except Exception as exc:
            if 'UNIQUE' in str(exc).upper():
                raise HTTPException(409, 'A successful measurement is already recorded for this session.')
            raise
        return {'id': cur.lastrowid, 'endpoint_id': data.endpoint_id, 'measured_at': measured_at,
                'session_date': session_date, 'session_name': session_name, 'timezone': 'Asia/Kolkata',
                'measurement_method': 'browser_http', 'packet_loss_pct': data.packet_loss_pct, 'dns_delay_ms': data.dns_delay_ms, 'status': 'stored'}


@app.get('/api/measurements')
def measurements(limit:int=Query(200,ge=1,le=5000),user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute("SELECT m.id,m.measured_at,m.latency_ms,m.packet_loss_pct,m.dns_delay_ms,m.download_mbps,m.upload_mbps,m.jitter_ms,m.loaded_jitter_ms,m.test_status,m.review_status,v.access_medium,v.medium_status,v.city,v.isp FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id ORDER BY measured_at DESC LIMIT ?",(limit,)).fetchall()]
@app.get('/api/measurements/mine')
def own_measurements(limit:int=Query(500,ge=1,le=5000),user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT m.*,v.access_medium,v.isp,v.city FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id WHERE v.user_id=? ORDER BY m.measured_at DESC LIMIT ?',(user['id'],limit)).fetchall()]
@app.get('/api/measurements/daily-averages')
def daily_averages(limit_days: int = Query(30, ge=1, le=365), user=Depends(current_user)):
    metrics = ('latency_ms','packet_loss_pct','dns_delay_ms','download_mbps','upload_mbps','jitter_ms','loaded_jitter_ms')
    with connect() as db:
        rows = db.execute('''SELECT m.* FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id
            WHERE v.user_id=? AND m.test_status='complete' AND m.session_date IS NOT NULL
            ORDER BY m.session_date DESC''', (user['id'],)).fetchall()
    grouped = {}
    for row in rows:
        item = grouped.setdefault(row['session_date'], {'date': row['session_date'], 'sessions': [], 'session_count': 0, **{m: [] for m in metrics}})
        item['sessions'].append(row['session_name'])
        item['session_count'] += 1
        for metric in metrics:
            if row[metric] is not None:
                item[metric].append(float(row[metric]))
    result=[]
    for day in sorted(grouped, reverse=True)[:limit_days]:
        item=grouped[day]
        result.append({'date': day, 'sessions': sorted(set(item['sessions'])), 'session_count': item['session_count'],
                       **{m: round(sum(item[m])/len(item[m]), 3) if item[m] else None for m in metrics}})
    return result


@app.get('/api/measurements/analysis')
def measurement_analysis(days: int = Query(3, ge=2, le=30), user=Depends(current_user)):
    daily = daily_averages(limit_days=days, user=user)
    metrics = ('latency_ms','packet_loss_pct','dns_delay_ms','download_mbps','upload_mbps','jitter_ms','loaded_jitter_ms')
    return {'days_requested': days, 'days_available': len(daily), 'daily_averages': daily,
            'period_averages': {m: (round(sum(d[m] for d in daily if d[m] is not None) / len([d for d in daily if d[m] is not None]), 3)
                                   if any(d[m] is not None for d in daily) else None) for m in metrics},
            'averaging_note': 'Each metric is averaged independently across available daily averages. Missing sessions are not treated as zero.'}


@app.get('/api/dashboard/summary')
def summary(user=Depends(current_user)):
    with connect() as db:
        v=db.execute('SELECT COUNT(*) n FROM volunteers WHERE active=1').fetchone()['n'];m=db.execute('SELECT COUNT(*) n FROM measurements').fetchone()['n']
        media=db.execute('SELECT COUNT(DISTINCT access_medium) n FROM volunteers WHERE active=1').fetchone()['n'];days=db.execute('SELECT MIN(substr(measured_at,1,10)) first_day,MAX(substr(measured_at,1,10)) last_day FROM measurements').fetchone()
        by=db.execute('SELECT access_medium,COUNT(*) n FROM volunteers WHERE active=1 GROUP BY access_medium').fetchall()
        return {'volunteers':v,'measurements':m,'access_media':media,'first_measurement_day':days['first_day'],'last_measurement_day':days['last_day'],'by_medium':[dict(r) for r in by]}
def global_comparison_rows():
    """Return the platform-wide verified dataset for administrator research analytics."""
    with connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT m.*,v.id volunteer_id,v.city,v.site_label,v.isp,v.access_medium,v.wifi_type,v.medium_status "
            "FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id "
            "WHERE m.test_status='complete' AND v.medium_status='verified'"
        ).fetchall()]

@app.get('/api/dashboard/comparison')
def comparison(user=Depends(current_user)):
    # The participant workspace is private: aggregate only endpoints owned by
    # the authenticated account. Keep the same grouping dimensions and metrics.
    with connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT m.*,v.id volunteer_id,v.city,v.site_label,v.isp,v.access_medium,v.wifi_type,v.medium_status "
            "FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id "
            "WHERE m.test_status='complete' AND v.medium_status='verified' AND v.user_id=?",
            (user['id'],)
        ).fetchall()]
    return grouped_comparison(rows)

@app.get('/api/profile')
def profile(user=Depends(current_user)):return user
@app.patch('/api/profile')
def update_profile(payload:dict,user=Depends(current_user)):
    allowed={'name','organization','city','state','country'};fields={k:str(v)[:150] for k,v in payload.items() if k in allowed}
    if not fields:return user
    with connect() as db:db.execute('UPDATE users SET '+','.join(f'{k}=?' for k in fields)+' WHERE id=?',(*fields.values(),user['id']))
    return {'message':'Profile updated'}
@app.patch('/api/auth/password')
def change_password(payload:dict,user=Depends(current_user)):
    current=str(payload.get('current_password','')); new=str(payload.get('new_password',''))
    if len(new)<10 or len(new)>128: raise HTTPException(422,'New password must be 10–128 characters')
    with connect() as db:
        row=db.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],)).fetchone()
        if not row or not verify_password(current,row['password_hash']): raise HTTPException(400,'Current password is incorrect')
        db.execute('UPDATE users SET password_hash=? WHERE id=?',(hash_password(new),user['id']))
    return {'message':'Password updated'}

@app.get('/api/notifications')
def notifications(user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100',(user['id'],)).fetchall()]
@app.post('/api/notifications/{nid}/read')
def read_notification(nid:int,user=Depends(current_user)):
    with connect() as db:db.execute('UPDATE notifications SET read_at=? WHERE id=? AND user_id=?',(datetime.now(timezone.utc).isoformat(),nid,user['id']))
    return {'ok':True}
@app.get('/api/campaigns')
def campaigns(user=Depends(current_user)):
    with connect() as db:return [dict(r) for r in db.execute("SELECT c.*,(SELECT COUNT(*) FROM campaign_members cm WHERE cm.campaign_id=c.id) participants,(SELECT COUNT(*) FROM campaign_members cm WHERE cm.campaign_id=c.id AND cm.user_id=?) joined FROM campaigns c WHERE c.status='active' ORDER BY c.created_at DESC",(user['id'],)).fetchall()]
@app.post('/api/campaigns/{cid}/join')
def join_campaign(cid:int,user=Depends(current_user)):
    with connect() as db:
        if not db.execute("SELECT id FROM campaigns WHERE id=? AND status='active'",(cid,)).fetchone():raise HTTPException(404,'Active campaign not found')
        db.execute('INSERT OR IGNORE INTO campaign_members(campaign_id,user_id) VALUES(?,?)',(cid,user['id']))
        db.execute('INSERT INTO notifications(user_id,title,body) VALUES(?,?,?)',(user['id'],'Campaign joined','You joined a NETRIVANTA research campaign.'))
    return {'joined':True}

# Administrator API namespace
@app.get('/api/admin/summary')
def admin_summary(user=Depends(admin_user)):
    with connect() as db:
        def count(sql):return db.execute(sql).fetchone()['n']
        return {'users':count('SELECT COUNT(*) n FROM users'),'volunteers':count('SELECT COUNT(*) n FROM volunteers'),'active_agents':count('SELECT COUNT(*) n FROM volunteers WHERE active=1'),'measurements':count('SELECT COUNT(*) n FROM measurements'),'today':db.execute('SELECT COUNT(*) n FROM measurements WHERE substr(measured_at,1,10)=?',(datetime.now(timezone.utc).date().isoformat(),)).fetchone()['n'],'failed':count("SELECT COUNT(*) n FROM measurements WHERE test_status!='complete'"),'pending_verification':count("SELECT COUNT(*) n FROM volunteers WHERE medium_status='self_reported'")}
@app.get('/api/admin/users')
def admin_users(user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT id,name,email,role,organization,city,active,created_at FROM users ORDER BY created_at DESC').fetchall()]
@app.patch('/api/admin/users/{uid}/role')
def admin_role(uid:int,data:UserRoleIn,user=Depends(admin_user)):
    if data.role not in {'user','admin'}:raise HTTPException(422,'Role must be user or admin')
    if uid==user['id'] and data.role!='admin':raise HTTPException(400,'You cannot remove your own admin role')
    with connect() as db:
        cur=db.execute('UPDATE users SET role=? WHERE id=?',(data.role,uid))
        if not cur.rowcount:raise HTTPException(404,'User not found')
        audit(db,user,'role_change',uid,data.role)
    return {'id':uid,'role':data.role}
@app.patch('/api/admin/users/{uid}/active')
def admin_user_active(uid:int,active:bool,user=Depends(admin_user)):
    if uid==user['id'] and not active:raise HTTPException(400,'You cannot deactivate your own account')
    with connect() as db:
        cur=db.execute('UPDATE users SET active=? WHERE id=?',(int(active),uid))
        if not cur.rowcount:raise HTTPException(404,'User not found')
        audit(db,user,'user_active',uid,active)
    return {'id':uid,'active':active}
@app.get('/api/admin/measurements')
def admin_measurements(limit:int=Query(1000,ge=1,le=5000),user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT m.*,v.name,v.email,v.city,v.isp,v.access_medium,v.medium_status FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id ORDER BY measured_at DESC LIMIT ?',(limit,)).fetchall()]
@app.patch('/api/admin/measurements/{mid}/review')
def admin_measurement_review(mid:int,status:str,user=Depends(admin_user)):
    if status not in {'verified','rejected','pending'}:raise HTTPException(422,'Invalid review status')
    with connect() as db:
        cur=db.execute('UPDATE measurements SET review_status=? WHERE id=?',(status,mid))
        if not cur.rowcount:raise HTTPException(404,'Measurement not found')
        audit(db,user,'measurement_review',mid,status)
    return {'id':mid,'review_status':status}
@app.get('/api/admin/analytics')
def admin_analytics(user=Depends(admin_user)):return grouped_comparison(global_comparison_rows())
@app.get('/api/admin/agents')
def admin_agents(user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute("SELECT v.id,v.name,v.email,v.city,v.isp,v.access_medium,v.medium_status,v.active,v.created_at,MAX(m.measured_at) last_seen,COUNT(m.id) measurement_count FROM volunteers v LEFT JOIN measurements m ON m.volunteer_id=v.id GROUP BY v.id ORDER BY last_seen DESC").fetchall()]
@app.get('/api/admin/data-quality')
def data_quality(user=Depends(admin_user)):
    with connect() as db:
        return {'duplicate_timestamps':db.execute('SELECT COUNT(*) n FROM (SELECT volunteer_id,measured_at,COUNT(*) c FROM measurements GROUP BY volunteer_id,measured_at HAVING COUNT(*)>1) AS duplicate_groups').fetchone()['n'],'missing_latency':db.execute('SELECT COUNT(*) n FROM measurements WHERE latency_ms IS NULL').fetchone()['n'],'partial_or_failed':db.execute("SELECT COUNT(*) n FROM measurements WHERE test_status!='complete'").fetchone()['n'],'unreviewed':db.execute("SELECT COUNT(*) n FROM measurements WHERE review_status='pending'").fetchone()['n']}
@app.get('/api/admin/audit-logs')
def audit_logs(user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT a.*,u.email admin_email FROM audit_logs a LEFT JOIN users u ON u.id=a.admin_user_id ORDER BY a.created_at DESC LIMIT 500').fetchall()]
@app.get('/api/admin/campaigns')
def admin_campaigns(user=Depends(admin_user)):
    with connect() as db:return [dict(r) for r in db.execute('SELECT * FROM campaigns ORDER BY created_at DESC').fetchall()]
@app.post('/api/admin/campaigns')
def create_campaign(data:CampaignIn,user=Depends(admin_user)):
    if data.status not in {'draft','active','paused','completed'}:raise HTTPException(422,'Invalid campaign status')
    with connect() as db:
        cur=db.execute('INSERT INTO campaigns(title,description,start_date,end_date,target_medium,target_participants,status) VALUES(?,?,?,?,?,?,?)',(data.title,data.description,data.start_date,data.end_date,data.target_medium,data.target_participants,data.status));audit(db,user,'campaign_created',cur.lastrowid,data.title)
        if data.status=='active':
            db.execute('INSERT INTO notifications(user_id,title,body) SELECT id,?,? FROM users WHERE active=1',('New research campaign',data.title))
        return {'id':cur.lastrowid,'message':'Campaign created'}
@app.patch('/api/admin/campaigns/{cid}')
def update_campaign(cid:int,data:CampaignIn,user=Depends(admin_user)):
    with connect() as db:
        cur=db.execute('UPDATE campaigns SET title=?,description=?,start_date=?,end_date=?,target_medium=?,target_participants=?,status=? WHERE id=?',(data.title,data.description,data.start_date,data.end_date,data.target_medium,data.target_participants,data.status,cid))
        if not cur.rowcount:raise HTTPException(404,'Campaign not found')
        audit(db,user,'campaign_updated',cid,data.status)
    return {'id':cid,'updated':True}
@app.get('/api/admin/reports.csv')
def report_csv(user=Depends(admin_user)):
    import csv
    output=io.StringIO();writer=csv.writer(output);writer.writerow(['measurement_id','volunteer_id','measured_at','session_date','session_name','timezone','measurement_method','access_medium','isp','city','latency_ms','packet_loss_pct','dns_delay_ms','download_mbps','upload_mbps','jitter_ms','status'])
    with connect() as db:
        for r in db.execute('SELECT m.*,v.id volunteer_id,v.access_medium,v.isp,v.city FROM measurements m JOIN volunteers v ON v.id=m.volunteer_id ORDER BY m.measured_at DESC'):writer.writerow([r[k] for k in ['id','volunteer_id','measured_at','session_date','session_name','timezone','measurement_method','access_medium','isp','city','latency_ms','packet_loss_pct','dns_delay_ms','download_mbps','upload_mbps','jitter_ms','test_status']])
    return StreamingResponse(iter([output.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename=netrivanta-measurements.csv'})
@app.get('/api/probe/health')
def probe_health():return {'ok':True,'service':'netrivanta-controlled-probe'}
@app.get('/api/probe/download')
def probe_download(bytes:int=Query(262144,ge=1024,le=1048576)):
    payload=b'N'*bytes;return StreamingResponse(io.BytesIO(payload),media_type='application/octet-stream',headers={'Content-Length':str(len(payload)),'Cache-Control':'no-store'})
@app.post('/api/probe/upload')
def probe_upload(payload:bytes=Body(..., media_type='application/octet-stream')):
    if len(payload)>1048576:raise HTTPException(413,'Probe upload is limited to 1 MiB')
    return {'received_bytes':len(payload)}

@app.get('/{path:path}',response_class=HTMLResponse)
def frontend_routes(path:str):
    if path.startswith('api/') or path.startswith('static/'): raise HTTPException(404,'Not found')
    with open('app/static/index.html',encoding='utf-8') as f:return f.read()
