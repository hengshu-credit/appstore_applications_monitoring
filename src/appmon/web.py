"""Same-origin console and HTTP API; a database connection per request."""
import csv
import io
import json
import os
import re
import secrets
import hashlib
import hmac
import time
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Query, Body
from fastapi.responses import FileResponse, JSONResponse
import pymysql

from .db import connect
from .ingest import valid_id
from . import catalog
from . import aso, review_desk, insights, collection
from . import collection_tasks, pipelines
from . import task_logs
from . import charts, app_collection
from . import app_discovery, app_metrics, availability
from .stores import FetchError
from datetime import date, datetime, timezone
from . import keywords, intelligence as intel, analytics, alerts, exports


def create_app(cfg):
    token = os.environ.get("APPMON_API_TOKEN", "")
    session_key = secrets.token_bytes(32)
    pool_cache = {}
    pool_lock = threading.Lock()
    category_cache = {}
    def signed_session(stamp):
        return hmac.new(session_key, stamp.encode(), hashlib.sha256).hexdigest()

    def valid_session(value):
        try:
            stamp, signature = value.split('.', 1)
            return 0 <= time.time() - int(stamp) < 8 * 3600 and secrets.compare_digest(signature, signed_session(stamp))
        except (ValueError, AttributeError):
            return False
    @asynccontextmanager
    async def lifespan(app):
        db = connect(cfg)
        db.close()
        yield

    app = FastAPI(title="App Observatory API", version="0.4.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guards(request, call_next):
        host = request.headers.get("host", "")
        configured = os.environ.get("APPMON_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(",")
        if urlsplit("http://" + host).hostname not in configured:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).netloc != host or urlsplit(origin).scheme not in ("http", "https")):
            return JSONResponse({"detail": "Cross-origin requests are disabled"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are disabled"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 5_000_000:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
            # Read with a cap even for chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 5_000_000:
                    return JSONResponse({"detail": "Payload too large"}, status_code=413)
            request._body = bytes(body)
        if request.url.path.startswith("/api/"):
            if token:
                supplied = request.headers.get("authorization", "")
                if not secrets.compare_digest(supplied, "Bearer " + token) and not valid_session(request.cookies.get("appmon_session", "")):
                    return JSONResponse({"detail": "请提供访问令牌"}, status_code=401)
            elif request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
                return JSONResponse({"detail": "A token is required for remote access"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' https: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FetchError)
    async def store_error(request, exc):
        return JSONResponse({"detail": "商店请求失败，未将失败判定为下架：" + str(exc)}, status_code=502)

    @app.exception_handler(pymysql.MySQLError)
    async def database_error(request, exc):
        return JSONResponse({"detail": "数据库查询失败或超时，请缩小筛选范围或查看服务日志"}, status_code=503)

    def database():
        db = connect(cfg, initialize=False)
        try:
            yield db
        finally:
            db.close()

    DB = Depends(database)
    api = APIRouter(prefix="/api")

    @api.post("/session")
    def browser_session(request: Request):
        stamp = str(int(time.time()))
        response = JSONResponse({"authenticated": True})
        response.set_cookie("appmon_session", stamp + '.' + signed_session(stamp), httponly=True,
                            samesite="strict", secure=request.url.scheme == "https", max_age=8 * 3600)
        return response

    @api.get("/openapi.json")
    def schema():
        return app.openapi()

    @api.get("/overview")
    def overview(db=DB):
        now = time.time()
        counts = {table: db.execute(f"SELECT count(*) n FROM {table}").fetchone()["n"] for table in
                  ("watchlist", "keyword_targets", "observation_targets", "reviews")}
        counts["unread_alerts"] = db.execute("SELECT count(*) n FROM alerts WHERE acknowledged=0").fetchone()["n"]
        return {"counts": counts, "at": now,
                "freshness": db.execute("SELECT store,country,state,count(*) count,max(last_success) last_success FROM listings GROUP BY store,country,state").fetchall(),
                "recent_events": db.execute("SELECT e.*,a.name FROM events e LEFT JOIN apps a USING(store,app_id) ORDER BY e.id DESC LIMIT 20").fetchall(),
                "worker": db.execute("SELECT next_at FROM schedules WHERE name='console-heartbeat'").fetchone(),
                "failures": {table: db.execute(f"SELECT id,last_error,failures FROM {table} WHERE failures>0 ORDER BY id DESC LIMIT 20").fetchall() for table in ("keyword_targets", "observation_targets")}}

    @api.get("/pool")
    def pool(db=DB):
        # Exact counts, cached with an explicit observation time; never use
        # information_schema's approximate table_rows as a product metric.
        with pool_lock:
            if not pool_cache or time.time() - pool_cache["observed_at"] > 300:
                rows = db.execute("SELECT store,count(*) count FROM apps GROUP BY store").fetchall()
                pool_cache.update(items=rows, total=sum(r["count"] for r in rows), observed_at=time.time())
        return pool_cache

    @api.get('/catalog')
    def store_catalog():
        return catalog.public_catalog()

    @api.get('/categories/counts')
    def category_counts(store: str, db=DB):
        with pool_lock:
            cached = category_cache.get(store)
            if not cached or time.time() - cached['observed_at'] > 300:
                cached = catalog.category_counts(db, store) | {'observed_at': time.time()}
                category_cache[store] = cached
            return cached

    @api.get("/apps")
    def apps(q: str = Query("", max_length=255), store: str = "", country: str = "us", language: str = "en", category: str = "",
             developer: str = "", state: str = "", after_store: str = "", after_id: str = "", limit: int = Query(50, ge=1, le=200),
             min_rating: float | None = Query(None, ge=0, le=5), min_ratings: int | None = Query(None,ge=0),
             pricing: str = '', updated_since: str = '', released_since: str = '', db=DB):
        clauses, args = [], [country, language]
        for key, value in (("a.store", store), ("l.state", state)):
            if value:
                clauses.append(f"{key}=%s")
                args.append(value)
        if category:
            aliases = catalog.category_aliases(store, category)
            placeholders = ','.join('%s' for _ in aliases)
            clauses.append(f"(a.category IS NULL OR a.category NOT IN ({placeholders}))" if category=='__unknown__' else f"a.category IN ({placeholders})")
            args.extend(aliases)
        if q and store in ("google", "apple") and valid_id(store, q):
            clauses.append("a.app_id=%s")
            args.append(q)
        elif q:
            clauses.append("(a.app_id=%s OR a.name LIKE %s)")
            args.extend([q, '%' + q.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'])
            clauses[-1] = "(a.app_id=%s OR a.name COLLATE utf8mb4_0900_ai_ci LIKE %s ESCAPE '!')"
        if developer:
            clauses.append("JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.developer'))=%s")
            args.append(developer)
        for field, minimum in (('rating',min_rating),('ratings',min_ratings)):
            if minimum is not None:
                clauses.append(f"JSON_TYPE(JSON_EXTRACT(l.payload,'$.{field}')) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.{field}')) AS DECIMAL(20,6))>=%s")
                args.append(minimum)
        if pricing:
            if pricing not in ('free','paid'):raise ValueError('售价筛选无效')
            clauses.append("JSON_TYPE(JSON_EXTRACT(l.payload,'$.price')) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE(JSON_EXTRACT(l.payload,'$.price')) AS DECIMAL(20,6))"+('=0' if pricing=='free' else '>0'))
        for field, minimum in (('updated',updated_since),('released',released_since)):
            if minimum:
                day=date.fromisoformat(minimum)
                epoch=datetime.combine(day,datetime.min.time(),tzinfo=timezone.utc).timestamp()
                path=f"JSON_EXTRACT(l.payload,'$.{field}')"
                clauses.append(f"((JSON_TYPE({path}) IN ('INTEGER','DOUBLE') AND CAST(JSON_UNQUOTE({path}) AS DECIMAL(20,6))>=%s) OR (JSON_UNQUOTE({path}) REGEXP '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' AND LEFT(JSON_UNQUOTE({path}),10)>=%s))")
                args.extend((epoch,minimum))
        if after_store or after_id:
            clauses.append("(a.store,a.app_id)>(%s,%s)")
            args.extend([after_store, after_id])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = db.execute('''SELECT /*+ MAX_EXECUTION_TIME(15000) */ a.*,l.state,l.last_success,l.checked_at,l.payload
            FROM apps a LEFT JOIN listings l ON a.store=l.store AND a.app_id=l.app_id AND l.country=%s AND l.language=%s'''
                          + where + " ORDER BY a.store,a.app_id LIMIT %s", (*args, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        for row in rows:
            row["payload"] = json.loads(row["payload"]) if row["payload"] else None
            row['category_id'] = catalog.category_id(row['store'], row['category'])
            row['category_name'] = catalog.BY_ID.get(row['store'], {}).get(row['category_id'], {}).get('name')
        return {"items": rows, "next": {"after_store": rows[-1]["store"], "after_id": rows[-1]["app_id"]} if more else None,
                "country": country, "language": language}

    @api.post('/discovery/search')
    def search_store(value:dict=Body(...),db=DB):
        return app_discovery.search(db,cfg,value)

    @api.get('/availability')
    def listing_availability(store:str,country:str='us',language:str='en',days:int=30,db=DB):
        return availability.report(db,dict(store=store,country=country,language=language,days=days))

    @api.post('/discovery/import')
    def import_store_app(value:dict=Body(...),db=DB):
        return app_discovery.import_app(db,cfg,value)

    @api.get('/apps/{store}/{app_id}/metrics')
    def app_monitoring(store:str,app_id:str,country:str='us',language:str='en',days:int=30,db=DB):
        return app_metrics.report(db,dict(store=store,app_id=app_id,country=country,language=language,days=days))

    @api.get("/apps/{store}/{app_id}")
    def app_detail(store: str, app_id: str, country: str = "us", language: str = "en", db=DB):
        normalized=intel.market(dict(store=store, app_id=app_id, country=country, language=language))
        country,language=normalized['country'],normalized['language']
        base = db.execute("SELECT * FROM apps WHERE store=%s AND app_id=%s", (store, app_id)).fetchone()
        if not base:
            raise HTTPException(404, "应用尚未进入应用池")
        base['category_id'] = catalog.category_id(store, base['category'])
        base['category_name'] = catalog.BY_ID[store].get(base['category_id'], {}).get('name')
        key = (store, app_id, country, language)
        listing = db.execute("SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s", key).fetchone()
        snapshots = db.execute("SELECT * FROM snapshots WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY id DESC LIMIT 100", key).fetchall()
        for row in [*snapshots, *([listing] if listing else [])]:
            row["payload"] = json.loads(row["payload"]) if row["payload"] else None
        return {"app": base, "listing": listing, "snapshots": snapshots,
                "chart_rankings":charts.app_rankings(db,store,app_id,country,language,['']+([base['category_id']] if base['category_id'] else [])),
                "collection_request":app_collection.latest(db,dict(store=store,app_id=app_id,country=country,language=language)),
                "events": db.execute("SELECT * FROM events WHERE store=%s AND app_id=%s AND country=%s AND language=%s ORDER BY id DESC LIMIT 100", key).fetchall(),
                "keywords": keywords.app_coverage(db, store, app_id, country, language),
                "job": db.execute("SELECT due,failures,last_error FROM jobs WHERE store=%s AND app_id=%s AND country=%s AND language=%s", key).fetchone()}

    @api.post('/apps/{store}/{app_id}/collect')
    def collect_app(store:str,app_id:str,value:dict=Body(...),db=DB):
        return app_collection.collect(db,value|{'store':store,'app_id':app_id})

    @api.get('/apps/{store}/{app_id}/collection')
    def app_collection_status(store:str,app_id:str,country:str='us',language:str='en',db=DB):
        m=intel.market(dict(store=store,app_id=app_id,country=country,language=language))
        return app_collection.latest(db,m)

    @api.get('/charts/view')
    def chart_view(store:str,country:str='us',language:str='en',collection:str='top-free',category:str='',db=DB):
        m=intel.market(dict(store=store,country=country,language=language),require_app=False)
        if category:catalog.category_aliases(store,category)
        return charts.view(db,m['store'],m['country'],m['language'],collection,category)

    @api.get("/watchlist")
    def watchlist(db=DB):
        return db.execute('''SELECT w.*,a.name,j.due,j.failures,j.last_error,l.state,l.last_success FROM watchlist w
            JOIN apps a USING(store,app_id) LEFT JOIN jobs j USING(store,app_id,country,language)
            LEFT JOIN listings l USING(store,app_id,country,language) ORDER BY w.id DESC LIMIT 2000''').fetchall()

    @api.post("/watchlist")
    def add_watch(value: dict = Body(...), db=DB):
        return intel.watch(db, value)

    @api.get("/targets")
    def targets(kind: str = "", db=DB):
        rows = db.execute("SELECT * FROM observation_targets" + (" WHERE kind=%s" if kind else "") + " ORDER BY id DESC LIMIT 2000", (kind,) if kind else None).fetchall()
        return [{k: v for k, v in r.items() if k != "token"} for r in rows]

    @api.post("/targets")
    def add_target(value: dict = Body(...), db=DB):
        return intel.add_observation_target(db, value)

    @api.get("/targets/{target_id}")
    def target(target_id: int, run_id: int | None = None, db=DB):
        return intel.observation_report(db, target_id, run_id)

    @api.get("/keywords")
    def keyword_list(db=DB):
        return [{k: v for k, v in row.items() if k != "token"} for row in db.execute("SELECT * FROM keyword_targets ORDER BY id DESC LIMIT 2000").fetchall()]

    @api.post("/keywords")
    def add_keywords(values: list[dict] = Body(...), db=DB):
        if len(values) > 2000:
            raise ValueError("At most 2000 keywords per import")
        return keywords.add_targets(db, values)

    @api.get("/keywords/{target_id}")
    def keyword(target_id: int, db=DB):
        return keywords.ranks(db, target_id)

    @api.get("/keywords/{target_id}/history")
    def keyword_history(target_id: int, app_id: str | None = None, db=DB):
        return keywords.history(db, target_id, app_id, limit=200)

    @api.post("/control/{resource}/{identifier}")
    def control(resource: str, identifier: int, value: dict = Body(...), db=DB):
        if resource in collection_tasks.TABLES:
            return collection_tasks.control_tasks(db, {'tasks':[{'resource':resource,'id':identifier}], 'action':value.get('action')})
        table = {"watchlist": "watchlist", "targets": "observation_targets", "keywords": "keyword_targets", "rules": "alert_rules"}.get(resource)
        if not table or not db.execute(f"SELECT id FROM {table} WHERE id=%s", (identifier,)).fetchone():
            raise HTTPException(404, "任务不存在")
        action = value.get("action")
        if action in ("pause", "resume"):
            extra = ",token=NULL,lease_until=0" if resource in ("targets", "keywords") else ""
            db.execute(f"UPDATE {table} SET enabled=%s{extra} WHERE id=%s", (action == "resume", identifier))
        elif action == "refresh" and resource != "rules":
            if resource == "watchlist":
                db.execute("UPDATE jobs j JOIN watchlist w USING(store,app_id,country,language) SET j.due=0 WHERE w.id=%s AND j.lease_until<=%s", (identifier, time.time()))
            else:
                db.execute(f"UPDATE {table} SET due=0 WHERE id=%s AND lease_until<=%s", (identifier, time.time()))
        else:
            raise ValueError("Invalid action")
        return {"id": identifier, "action": action}

    @api.post("/compare")
    def compare(values: list[dict] = Body(...), db=DB):
        return intel.compare_apps(db, values)

    @api.post("/suggestions")
    def suggest(value: dict = Body(...), db=DB):
        return analytics.suggestions(db, value)

    @api.get("/reviews")
    def reviews(store: str = "", app_id: str = "", country: str = "", language: str = "", rating: int | None = Query(None, ge=1, le=5),
                offset: int = Query(0, ge=0, le=100000), db=DB):
        filters = {k: v for k, v in dict(store=store, app_id=app_id, country=country, language=language, rating=rating).items() if v}
        where = " WHERE " + " AND ".join(f"{k}=%s" for k in filters) if filters else ""
        rows = db.execute("SELECT * FROM reviews" + where + " ORDER BY last_seen DESC,review_id LIMIT 100 OFFSET %s", (*filters.values(), offset)).fetchall()
        return {"items": rows, "summary": analytics.review_summary(db, filters), "next_offset": offset + 100 if len(rows) == 100 else None}

    @api.get("/market")
    def market_summary(country: str = "us", language: str = "en", db=DB):
        sql = " FROM listings WHERE payload IS NOT NULL AND country=%s AND language=%s"
        args = (country, language)
        return {"categories": db.execute("SELECT store,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.category')) label,count(*) count" + sql + " GROUP BY store,label ORDER BY count DESC LIMIT 100", args).fetchall(),
                "developers": db.execute("SELECT store,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.developer')) label,count(*) count" + sql + " GROUP BY store,label ORDER BY count DESC LIMIT 100", args).fetchall(),
                "activity": db.execute("SELECT kind,FLOOR(observed_at/86400)*86400 day,count(*) count FROM events WHERE country=%s AND language=%s AND observed_at>=%s GROUP BY kind,day ORDER BY day", (*args, time.time() - 30*86400)).fetchall(),
                "scope": "仅统计本系统已观察的地区应用；首次发现不等于新上架，不代表全球市场份额"}

    @api.get("/alerts")
    def alert_list(unread: bool = False, db=DB):
        return {"rules": db.execute("SELECT * FROM alert_rules ORDER BY id").fetchall(),
                "items": db.execute("SELECT * FROM alerts" + (" WHERE acknowledged=0" if unread else "") + " ORDER BY id DESC LIMIT 500").fetchall()}

    @api.post("/alerts/rules")
    def alert_rule(value: dict = Body(...), db=DB):
        return alerts.add_rule(db, value)

    @api.post("/alerts/{identifier}/acknowledge")
    def acknowledge(identifier: int, db=DB):
        db.execute("UPDATE alerts SET acknowledged=1 WHERE id=%s", (identifier,))
        return {"id": identifier}

    @api.get("/daily")
    def daily(db=DB):
        return alerts.daily_report(db)

    @api.get("/exports")
    def export_list(db=DB):
        return db.execute("SELECT id,kind,filters,status,created_at,finished_at,rows_written,error FROM exports ORDER BY created_at DESC LIMIT 100").fetchall()

    @api.post("/exports")
    def export_create(value: dict = Body(...), db=DB):
        return exports.request_export(db, value.get("kind"), value.get("filters", {}))

    @api.get("/exports/{identifier}/download")
    def export_download(identifier: str, db=DB):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise HTTPException(404)
        row = db.execute("SELECT status FROM exports WHERE id=%s", (identifier,)).fetchone()
        if not row or row["status"] != "ready":
            raise HTTPException(409, "导出尚未就绪")
        return FileResponse(cfg["data_dir"] / "exports" / f"{identifier}.jsonl", filename=f"{identifier}.jsonl", media_type="application/x-ndjson")

    @api.get("/ads")
    def ads(db=DB):
        return {"observations": db.execute("SELECT * FROM ad_observations ORDER BY observed_at DESC LIMIT 500").fetchall(),
                "reports": db.execute("SELECT * FROM ad_reports ORDER BY report_date DESC,id DESC LIMIT 500").fetchall()}

    @api.post("/ads/{kind}/import")
    def ad_import(kind: str, value: dict = Body(...), db=DB):
        rows = value.get("rows")
        if rows is None and isinstance(value.get("csv"), str):
            rows = list(csv.DictReader(io.StringIO(value["csv"].lstrip('\ufeff'))))
            if kind == "observations":
                for row in rows:
                    row["observed_at"] = float(row["observed_at"])
        if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(r, dict) for r in rows):
            raise ValueError("Expected at most 5000 records")
        if kind == "reports":
            return analytics.import_ad_reports(db, rows)
        if kind == "observations":
            return analytics.import_ad_observations(db, rows)
        raise ValueError("Unknown ad import kind")

    @api.get('/research/events')
    def event_center(store: str, country: str='us', language: str='en', app_id: str='', kind: str='', field: str='',
                     group_name: str='', days: int=Query(30,ge=1,le=365), db=DB):
        return insights.event_feed(db,dict(store=store,country=country,language=language,app_id=app_id,kind=kind,field=field,group_name=group_name,days=days))

    @api.post('/research/annotations')
    def annotate(value:dict=Body(...),db=DB):return insights.add_annotation(db,value)

    @api.get('/research/markets/{store}/{app_id}')
    def market_matrix(store:str,app_id:str,db=DB):return insights.app_markets(db,store,app_id)

    @api.get('/research/developers')
    def developer_list(store:str,q:str='',db=DB):return insights.developers(db,store,q)

    @api.get('/research/developer-apps')
    def developer_detail(store:str,name:str,developer_id:str='',db=DB):return insights.developer_apps(db,store,name,developer_id)

    @api.get('/research/aso')
    def aso_workspace(store:str,app_id:str,country:str='us',language:str='en',db=DB):
        return aso.workspace(db,dict(store=store,app_id=app_id,country=country,language=language))

    @api.post('/research/aso/check')
    def aso_check(value:dict=Body(...)):
        words=value.get('keywords',[])
        if not isinstance(words,list) or len(words)>500 or any(not isinstance(w,str) or len(w)>255 for w in words):raise ValueError('核查词库无效')
        return aso.check_metadata(value.get('store'),value.get('metadata'),words)

    @api.post('/research/aso/drafts')
    def draft_save(value:dict=Body(...),db=DB):return aso.save_draft(db,value)

    @api.get('/research/aso/drafts/{identifier}')
    def draft_get(identifier:int,db=DB):return aso.draft_detail(db,identifier)

    @api.get('/research/reviews')
    def review_inbox(store:str,country:str='us',language:str='en',app_id:str='',status:str='',q:str='',version:str='',label:str='',reply_status:str='',
                     rating:int|None=Query(None,ge=1,le=5),since:float|None=Query(None,ge=0),offset:int=Query(0,ge=0,le=100000),db=DB):
        return review_desk.desk(db,dict(store=store,country=country,language=language,app_id=app_id,status=status,q=q,version=version,label=label,reply_status=reply_status,rating=rating,since=since,offset=offset))

    @api.post('/research/reviews/workflow')
    def review_save(value:dict=Body(...),db=DB):return review_desk.save_case(db,value)

    @api.post('/research/reviews/history')
    def review_versions(value:dict=Body(...),db=DB):return review_desk.review_history(db,value)

    @api.get('/research/comparisons')
    def sets(db=DB):
        rows=db.execute('SELECT * FROM comparison_sets ORDER BY updated_at DESC LIMIT 100').fetchall()
        for row in rows:row['members']=json.loads(row['members'])
        return rows

    @api.post('/research/comparisons')
    def save_set(value:dict=Body(...),db=DB):return insights.save_comparison(db,value)

    @api.get('/research/comparisons/{identifier}')
    def get_set(identifier:int,db=DB):return insights.comparison_detail(db,identifier)

    @api.get('/research/collection')
    def data_center(db=DB):return collection.quality(db)

    @api.get('/research/collection/tasks')
    def collection_registry(kind:str='',store:str='',country:str='',language:str='',state:str='',q:str='',
                            page:int=Query(1,ge=1,le=100000),limit:int=Query(50,ge=1,le=200),db=DB):
        return collection_tasks.list_tasks(db,kind=kind,store=store,country=country,language=language,state=state,q=q,page=page,limit=limit)

    @api.get('/research/collection/activity')
    def collection_activity(db=DB):
        return {'plans':db.execute('SELECT id,name,status,completed,total,error,created_at FROM collection_plans ORDER BY id DESC LIMIT 100').fetchall(),
                'heartbeat':db.execute("SELECT next_at FROM schedules WHERE name='console-heartbeat'").fetchone()}

    @api.get('/research/collection/logs/{resource}/{identifier}')
    def collection_task_logs(resource:str,identifier:str,after_id:int=Query(0,ge=0),before_id:int=Query(0,ge=0),
                             limit:int=Query(200,ge=1,le=500),db=DB):
        return task_logs.read_logs(db,resource,identifier,after_id,before_id,limit)

    @api.post('/research/collection/tasks/batch')
    def batch_collection(value:dict=Body(...),db=DB):return collection_tasks.control_tasks(db,value)

    @api.put('/research/collection/tasks/{resource}/{identifier}')
    def update_collection_task(resource:str,identifier:int,value:dict=Body(...),db=DB):
        return collection_tasks.edit_task(db,resource,identifier,value)

    @api.get('/research/collection/pipelines')
    def collection_pipelines(db=DB):return pipelines.report(db,cfg)

    @api.put('/research/collection/pipelines/{name}')
    def save_pipeline(name:str,value:dict=Body(...),db=DB):return pipelines.save(db,cfg,name,value)

    @api.post('/research/plans/preview')
    def preview_collection(value:dict=Body(...)):return collection.preview_plan(value)

    @api.post('/research/plans')
    def save_plan(value:dict=Body(...),db=DB):return collection.create_plan(db,value)

    @api.post('/research/plans/{identifier}/retry')
    def retry_plan(identifier:int,db=DB):
        row=db.execute('SELECT status FROM collection_plans WHERE id=%s',(identifier,)).fetchone()
        if not row or row['status']!='error':raise ValueError('只有失败计划可以重试')
        with db:
            db.execute("UPDATE collection_plans SET status='queued',error=NULL,updated_at=%s WHERE id=%s",(time.time(),identifier))
            task_logs.emit(db,'plans',identifier,'retry','已安排从未完成的配置步骤继续重试')
        return {'id':identifier,'status':'queued'}

    app.include_router(api)
    @app.get("/health")
    def health(db=DB):
        db.execute("SELECT 1")
        return {"status": "ok", "version": "0.4.0"}
    return app
