"""Same-origin console and HTTP API; a database connection per request."""
import csv
import io
import json
import os
import re
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
from . import task_logs, locales, scoped_views, app_icons
from . import charts, app_collection
from . import growth, keyword_discovery, keyword_reports
from . import auth
from . import global_charts
from . import data_sources
from . import apple_popularity
from . import app_discovery, app_metrics, availability
from . import app_groups
from .stores import FetchError
from datetime import date, datetime, timezone
from . import keywords, intelligence as intel, analytics, alerts, exports


def create_app(cfg):
    lineage_cache = {}
    lineage_lock = threading.Lock()
    pool_cache = {}
    pool_lock = threading.Lock()
    category_cache = {}
    pipeline_cache = {}
    pipeline_lock = threading.Lock()
    @asynccontextmanager
    async def lifespan(app):
        db = connect(cfg)
        try:app_groups.bootstrap(db)
        finally:db.close()
        yield

    app = FastAPI(title="衡枢·观星 API", version="0.4.0", lifespan=lifespan,
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
            body_limit = 64 * 1024 * 1024 if request.url.path == '/api/data-sources/marketdata/datasets' else 5_000_000
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > body_limit:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
            # Read with a cap even for chunked requests.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > body_limit:
                    return JSONResponse({"detail": "Payload too large"}, status_code=413)
            request._body = bytes(body)
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
    def permission_guard(request: Request, db=DB):
        auth.authorize(db, request)

    app.include_router(auth.router(database))
    api = APIRouter(prefix="/api", dependencies=[Depends(permission_guard)])

    @api.post('/icons/resolve')
    def resolve_icons(value:dict=Body(...),db=DB):
        values=value.get('items');force=value.get('force',False)
        if not isinstance(values,list) or not 1<=len(values)<=50 or type(force) is not bool:raise ValueError('每次最多请求50个应用图标')
        rows=[]
        for item in values:
            try:
                if not isinstance(item,dict):raise ValueError('应用标识无效')
                rows.append(app_icons.request_icon(db,cfg,item,force))
            except ValueError as exc:rows.append({'error':str(exc),'pending':False})
        return {'items':rows}

    @api.get('/icons/{identifier}')
    def icon_file(identifier:int,db=DB):
        row=db.execute('SELECT * FROM app_icons WHERE id=%s',(identifier,)).fetchone()
        path=app_icons.cached_path(cfg,row) if row else None
        if path is None:raise HTTPException(404,'图标正在采集或尚不可用')
        return FileResponse(path,media_type=row['mime'])

    @api.get('/media/{identifier}')
    def media_file(identifier:int,db=DB):
        row=db.execute('SELECT * FROM app_media_assets WHERE id=%s',(identifier,)).fetchone()
        path=app_icons.cached_path(cfg,row) if row else None
        if path is None:raise HTTPException(404,'图片正在采集或尚不可用')
        return FileResponse(path,media_type=row['mime'])

    @api.get("/openapi.json")
    def schema():
        return app.openapi()

    @api.get("/overview")
    def overview(store:str="all",db=DB):
        return scoped_views.overview(db,store)

    @api.get("/pool")
    def pool(store:str="all",db=DB):
        # Exact counts, cached with an explicit observation time; never use
        # information_schema's approximate table_rows as a product metric.
        with pool_lock:
            if not pool_cache or time.time() - pool_cache["observed_at"] > 300:
                rows = db.execute("SELECT store,count(*) count FROM apps GROUP BY store").fetchall()
                pool_cache.update(items=rows, total=sum(r["count"] for r in rows), observed_at=time.time())
        selected=locales.stores(store)
        rows=[r for r in pool_cache["items"] if r["store"] in selected]
        return pool_cache|dict(items=rows,total=sum(r["count"] for r in rows))

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

    @api.get("/apps/filter-options")
    def app_filter_options(field:str,q:str=Query("",max_length=255),store:str="",country:str="us",language:str="en",db=DB):
        return scoped_views.filter_options(db,field,store,country,language,q)

    @api.get("/apps")
    def apps(q: str = Query("", max_length=255), store: str = "", country: str = "us", language: str = "en", category: str = "",
             page:int|None=Query(None,ge=1,le=1000000),q_match: str = "auto", developer_match: str = "exact", developer: str = "", state: str = "", after_store: str = "", after_id: str = "", limit: int = Query(50, ge=1, le=200),
             after_google:str="",after_apple:str="",google_done:bool=False,apple_done:bool=False,
             min_rating: float | None = Query(None, ge=0, le=5), min_ratings: int | None = Query(None,ge=0),
             pricing: str = "", updated_since: str = "", released_since: str = "", db=DB):
        return scoped_views.apps(db,store=store,country=country,language=language,q=q,page=page,q_match=q_match,category=category,developer=developer,developer_match=developer_match,state=state,limit=limit,
            after_store=after_store,after_id=after_id,after_google=after_google,after_apple=after_apple,google_done=google_done,apple_done=apple_done,
            min_rating=min_rating,min_ratings=min_ratings,pricing=pricing,updated_since=updated_since,released_since=released_since)

    @api.post('/discovery/search')
    def search_store(value:dict=Body(...),db=DB):
        return app_discovery.search(db,cfg,value)

    @api.get('/availability')
    def listing_availability(store:str,country:str='us',language:str='en',days:int=30,db=DB):
        return availability.report(db,dict(store=store,country=country,language=language,days=days))

    @api.get('/availability/watched')
    def watched_availability(store:str='all',country:str='',group_name:str='',q:str='',offset:int=0,limit:int=100,language:str='',enabled:str='',state:str='',db=DB):
        return availability.watched(db,store,country,group_name,q,offset,limit,language,enabled,state)

    @api.get('/groups')
    def groups(db=DB):return {'items':app_groups.list_groups(db)}

    @api.post('/groups')
    def create_group(value:dict=Body(...),db=DB):return app_groups.create(db,value)

    @api.put('/groups/{identifier}')
    def update_group(identifier:int,value:dict=Body(...),db=DB):return app_groups.update(db,identifier,value)

    @api.post('/groups/{identifier}/members')
    def add_group_members(identifier:int,value:dict=Body(...),db=DB):return app_groups.add_members(db,identifier,value)

    @api.post('/groups/{identifier}/members/remove')
    def remove_group_members(identifier:int,value:dict=Body(...),db=DB):return app_groups.remove_members(db,identifier,value)

    @api.get('/groups/{identifier}/dashboard')
    def group_dashboard(identifier:int,store:str='all',country:str='',state:str='',q:str='',days:int=30,offset:int=0,limit:int=50,classification:str='',db=DB):
        return app_groups.dashboard(db,cfg,identifier,store,country,state,q,days,offset,limit,classification)

    @api.put('/groups/{identifier}/members/{member_id}/adjustment')
    def adjust_group_member(identifier:int,member_id:int,request:Request,value:dict=Body(...),db=DB):
        return app_groups.adjust_member(db,identifier,member_id,value,request.state.user['username'])

    @api.get('/groups/{identifier}/members/{member_id}')
    def group_member(identifier:int,member_id:int,days:int=30,db=DB):return app_groups.member_detail(db,identifier,member_id,days,cfg)

    @api.get('/groups/{identifier}/members/{member_id}/rank-history')
    def group_rank_history(identifier:int,member_id:int,kind:str,target_id:int,days:int=30,db=DB):
        return app_groups.rank_history(db,identifier,member_id,kind,target_id,days)

    @api.get('/groups/{identifier}/members/{member_id}/report')
    def group_reference_report(identifier:int,member_id:int,report_id:int,sheet:str='ASO关键词',offset:int=0,db=DB):
        return app_groups.reference_report(db,identifier,member_id,report_id,sheet,offset)

    @api.post('/groups/{identifier}/collect')
    def collect_group(identifier:int,value:dict=Body(...),db=DB):return app_groups.collect(db,identifier,value)

    @api.post('/groups/{identifier}/monitoring/control')
    def control_group(identifier:int,value:dict=Body(...),db=DB):return app_groups.control_monitoring(db,identifier,value)

    @api.post('/groups/{identifier}/metrics')
    def import_group_metrics(identifier:int,value:dict=Body(...),db=DB):return app_groups.import_metrics(db,identifier,value)

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
    def chart_view(store:str,country:str='us',language:str='en',collection:str='top-free',category:str='',as_of:float|None=None,compare_days:int=0,db=DB):
        m=intel.market(dict(store=store,country=country,language=language),require_app=False)
        if category:catalog.category_aliases(store,category)
        return charts.view(db,m['store'],m['country'],m['language'],collection,category,as_of=as_of,compare_days=compare_days)

    @api.get('/charts/detail')
    def chart_detail(store:str,country:str='us',language:str='en',collection:str='top-free',category:str='',as_of:float|None=None,
                     q:str=Query('',max_length=255),offset:int=0,limit:int=50,db=DB):
        m=intel.market(dict(store=store,country=country,language=language),require_app=False)
        if category:catalog.category_aliases(store,category)
        return charts.detail(db,m['store'],m['country'],m['language'],collection,category,as_of,q,offset,limit,cfg=cfg)

    @api.get('/discovery/growth')
    def growth_view(store:str,country:str='us',language:str='en',days:int=7,metric:str='ratings',as_of:float|None=None,
                    category:str='',q:str=Query('',max_length=255),pricing:str='',monetization:str='',new_days:int=0,db=DB):
        m=intel.market(dict(store=store,country=country,language=language),require_app=False)
        return growth.report(db,m['store'],m['country'],m['language'],days,metric,as_of,category,q,pricing,monetization,new_days)

    @api.get('/charts/global')
    def global_board(store:str,countries:str='',collections:str='top-free,top-paid,top-grossing',category:str='',as_of:float|None=None,depth:int=10,offset:int=0,db=DB):
        return global_charts.report(db,store,countries,collections,category,as_of,depth,offset)

    @api.post('/charts/global/preview')
    def global_preview(value:dict=Body(...)):return global_charts.preview(value)

    @api.get('/data-sources')
    def source_settings(db=DB):return data_sources.status(db)

    @api.put('/data-sources/community')
    def source_save(value:dict=Body(...),db=DB):return data_sources.save(db,cfg,value)

    @api.post('/data-sources/community/authorize')
    def source_authorize(db=DB):return data_sources.authorize(db,cfg)

    @api.put('/data-sources/directory')
    def directory_save(value:dict=Body(...),db=DB):return data_sources.save(db,cfg,value,'directory')

    @api.post('/data-sources/directory/authorize')
    def directory_authorize(db=DB):return data_sources.authorize(db,cfg,'directory')

    @api.get('/data-sources/community/datasets')
    def source_datasets(db=DB):return data_sources.dataset_catalog(db,cfg)

    from . import marketdata_source as marketdata

    @api.put('/data-sources/marketdata')
    def marketdata_save(value:dict=Body(...),db=DB):return data_sources.save(db,cfg,value,'marketdata')

    @api.post('/data-sources/marketdata/authorize')
    def marketdata_authorize(db=DB):return data_sources.authorize(db,cfg,'marketdata')

    @api.get('/data-sources/marketdata/collection')
    def marketdata_report(db=DB):return marketdata.report(db)

    @api.post('/data-sources/marketdata/collection')
    def marketdata_plan(value:dict=Body(...),db=DB):
        try:return marketdata.create_run(db,value)
        except RuntimeError:raise ValueError('正在建立采集计划，请稍后刷新') from None

    @api.post('/data-sources/marketdata/collection/{identifier}/control')
    def marketdata_control(identifier:int,value:dict=Body(...),db=DB):
        try:return marketdata.control(db,identifier,value.get('action'))
        except RuntimeError:raise ValueError('当前请求正在归档，请稍后重试操作') from None

    @api.post('/data-sources/marketdata/datasets')
    def marketdata_import(value:dict=Body(...),db=DB):return marketdata.import_csv(db,cfg,value)

    @api.get('/data-sources/marketdata/artifacts/{identifier}')
    def marketdata_download(identifier:int,db=DB):
        from fastapi.responses import FileResponse
        path=marketdata.artifact_path(db,cfg,identifier)
        return FileResponse(path,filename=path.name,media_type='application/gzip')

    @api.post('/data-sources/app-metrics')
    def source_metrics(value:dict=Body(...),db=DB):return data_sources.app_metrics(db,cfg,value.get('store'),value.get('app_id'),value.get('provider','auto'))

    @api.get('/charts/global/app-summary')
    def global_app_summary(store:str,app_id:str,countries:str='',collections:str='top-free,top-paid,top-grossing',category:str='',as_of:float|None=None,depth:int=10,db=DB):
        return global_charts.app_summary(db,store,app_id,countries,collections,category,as_of,depth)

    @api.post('/charts/global/plan')
    def global_plan(value:dict=Body(...),db=DB):return global_charts.create_plan(db,value)

    @api.get('/discovery/keywords/sources')
    def keyword_sources():return keyword_discovery.source_catalog()

    @api.get('/discovery/keywords/overview')
    def keyword_monitoring_overview(db=DB):return keyword_discovery.monitoring_overview(db)

    @api.get('/discovery/keywords/coverage')
    def keyword_coverage(store:str,country:str='us',language:str='en',app_id:str='',app_offset:int=0,keyword_offset:int=0,app_keyword_offset:int=0,q:str=Query('',max_length=255),limit:int=100,db=DB):
        m=intel.market(dict(store=store,country=country,language=language,app_id=app_id),require_app=bool(app_id))
        return keyword_discovery.coverage(db,m,app_id,app_offset,keyword_offset,app_keyword_offset,q,limit)

    @api.get('/discovery/keywords/markets')
    def keyword_markets(store:str,app_id:str='',db=DB):
        if store not in ('google','apple'):raise ValueError('请选择一个商店')
        if app_id and not valid_id(store,app_id):raise ValueError('应用ID无效')
        return keyword_discovery.market_matrix(db,store,app_id)

    @api.get('/discovery/keywords/candidates')
    def keyword_candidates(store:str,country:str='us',language:str='en',q:str=Query('',max_length=255),offset:int=0,db=DB):
        m=intel.market(dict(store=store,country=country,language=language),require_app=False)
        return keyword_discovery.candidates(db,m,q,offset)

    @api.post('/discovery/keywords/candidates')
    def candidate_import(value:dict=Body(...),db=DB):return keyword_discovery.import_candidates(db,value)

    @api.get('/discovery/keywords/aso-library')
    def aso_library(store:str,country:str='us',language:str='en',app_id:str='',q:str=Query('',max_length=255),offset:int=0,db=DB):
        m=intel.market(dict(store=store,country=country,language=language,app_id=app_id),require_app=bool(app_id))
        return keyword_reports.library(db,m,q,offset)

    @api.get('/discovery/keywords/candidates/{identifier}/sources')
    def candidate_source_list(identifier:int,db=DB):return keyword_discovery.candidate_sources(db,identifier)

    @api.post('/discovery/keywords/frequency')
    def frequency_import(value:dict=Body(...),db=DB):return keyword_discovery.import_frequency(db,value)

    @api.post('/discovery/keywords/track')
    def candidate_track(value:dict=Body(...),db=DB):return keyword_discovery.track(db,value)

    @api.get('/discovery/keywords/signals')
    def keyword_signals(store:str,country:str='us',language:str='en',run_id:int|None=None,db=DB):
        return keyword_discovery.signals(db,intel.market(dict(store=store,country=country,language=language),require_app=False),run_id)

    @api.post('/discovery/keywords/signals')
    def signal_import(value:dict=Body(...),db=DB):return keyword_discovery.import_signals(db,value)

    @api.post('/discovery/keywords/signals/{identifier}/candidates')
    def signal_candidates(identifier:int,db=DB):return keyword_discovery.promote_signals(db,identifier)

    @api.get('/discovery/keywords/apple-popularity')
    def popularity_configuration():return apple_popularity.configuration()

    @api.post('/discovery/keywords/apple-popularity')
    def popularity_fetch(value:dict=Body(...),db=DB):
        from .source_tasks import enqueue
        return enqueue(db,'aso_popularity',value,request_id=value.get('request_id'))

    @api.post('/discovery/keywords/apple-popularity/import')
    def popularity_import(value:dict=Body(...),db=DB):
        from .source_tasks import enqueue
        return enqueue(db,'aso_import',value,request_id=value.get('request_id'))

    @api.post('/discovery/reports/preview')
    def report_preview(value:dict=Body(...)):return keyword_reports.preview(value)

    @api.post('/discovery/reports/import')
    def report_import(value:dict=Body(...),db=DB):
        from .source_tasks import enqueue
        return enqueue(db,'aso_report',value,request_id=value.get('request_id'))

    @api.get('/discovery/keywords/tasks/{identifier}')
    def source_task_result(identifier:int,db=DB):
        from .source_tasks import receipt
        return receipt(db,identifier)

    @api.get('/research/collection/source-tasks/{identifier}')
    def collection_source_result(identifier:int,db=DB):
        from .source_tasks import receipt
        return receipt(db,identifier)

    @api.get('/research/collection/top50')
    def top50_progress(db=DB):
        from .chart_enrichment import report
        return report(db)

    @api.get('/discovery/reports')
    def imported_reports(store:str,country:str='us',language:str='en',app_id:str='',db=DB):
        return keyword_reports.reports(db,intel.market(dict(store=store,country=country,language=language,app_id=app_id),require_app=bool(app_id)))

    @api.get('/discovery/report-summary')
    def keyword_report_summary(store:str,country:str='us',language:str='en',db=DB):
        return keyword_reports.summary(db,intel.market(dict(store=store,country=country,language=language),require_app=False))

    @api.get('/discovery/reports/{identifier}')
    def report_rows(identifier:int,sheet:str='ASO关键词',q:str=Query('',max_length=255),offset:int=0,db=DB):
        return keyword_reports.report_rows(db,identifier,sheet,q,offset)

    @api.get('/discovery/reports/{identifier}/rejections')
    def report_rejections(identifier:int,offset:int=0,db=DB):return keyword_reports.rejections(db,identifier,offset)

    @api.post('/discovery/reports/promote')
    def report_promote(value:dict=Body(...),db=DB):return keyword_reports.promote(db,value)

    @api.get("/watchlist")
    def watchlist(store:str="all",country:str="",language:str="",db=DB):
        scope,args=locales.where(store,country,language,'w')
        return db.execute('''SELECT w.*,a.name,j.due,j.failures,j.last_error,l.state,l.last_success FROM watchlist w JOIN apps a USING(store,app_id) LEFT JOIN jobs j USING(store,app_id,country,language) LEFT JOIN listings l USING(store,app_id,country,language) WHERE '''+scope+' ORDER BY w.id DESC LIMIT 2000',args).fetchall()

    @api.post("/watchlist")
    def add_watch(value: dict = Body(...), db=DB):
        return intel.watch(db, value)

    @api.get("/targets")
    def targets(kind:str="",store:str="all",country:str="",language:str="",db=DB):
        scope,args=locales.where(store,country,language)
        if kind:scope+=' AND kind=%s';args.append(kind)
        rows=db.execute('SELECT * FROM observation_targets WHERE '+scope+' ORDER BY id DESC LIMIT 2000',args).fetchall()
        return [{k:v for k,v in row.items() if k!='token'} for row in rows]

    @api.post("/targets")
    def add_target(value: dict = Body(...), db=DB):
        return intel.add_observation_target(db, value)

    @api.get("/targets/{target_id}")
    def target(target_id: int, run_id: int | None = None, db=DB):
        return intel.observation_report(db, target_id, run_id)

    @api.get("/keywords")
    def keyword_list(store:str="all",country:str="",language:str="",db=DB):
        scope,args=locales.where(store,country,language)
        rows=db.execute('SELECT * FROM keyword_targets WHERE '+scope+' ORDER BY id DESC LIMIT 2000',args).fetchall()
        return [{k:v for k,v in row.items() if k!='token'} for row in rows]

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
    def market_summary(store:str="all",country:str="us",language:str="en",db=DB):
        return scoped_views.market_summary(db,store,country,language)

    @api.get("/alerts")
    def alert_list(unread:bool=False,store:str="all",db=DB):
        selected=locales.stores(store);marks=','.join('%s' for _ in selected)
        return {'rules':db.execute('SELECT * FROM alert_rules ORDER BY id').fetchall(),
                'items':db.execute("SELECT * FROM alerts WHERE JSON_UNQUOTE(JSON_EXTRACT(payload,'$.store')) IN ("+marks+')'+(' AND acknowledged=0' if unread else '')+' ORDER BY id DESC LIMIT 500',selected).fetchall()}

    @api.post("/alerts/rules")
    def alert_rule(value: dict = Body(...), db=DB):
        return alerts.add_rule(db, value)

    @api.post("/alerts/{identifier}/acknowledge")
    def acknowledge(identifier: int, db=DB):
        db.execute("UPDATE alerts SET acknowledged=1 WHERE id=%s", (identifier,))
        return {"id": identifier}

    @api.get("/daily")
    def daily(store:str="all",db=DB):
        return scoped_views.daily(db,store)

    @api.get("/exports")
    def export_list(store:str='all',db=DB):
        selected=locales.stores(store)
        where=" WHERE JSON_UNQUOTE(JSON_EXTRACT(filters,'$.store'))=%s" if len(selected)==1 else ''
        return db.execute("SELECT id,kind,filters,status,created_at,finished_at,rows_written,error FROM exports"+where+" ORDER BY created_at DESC LIMIT 100",selected if where else None).fetchall()

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
    def ads(store:str="all",db=DB):
        scope,args=locales.where(store)
        return {'observations':db.execute('SELECT * FROM ad_observations WHERE '+scope+' ORDER BY observed_at DESC LIMIT 500',args).fetchall(),
                'reports':db.execute('SELECT * FROM ad_reports ORDER BY report_date DESC,id DESC LIMIT 500').fetchall() if 'apple' in locales.stores(store) else []}

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
                     group_name: str='', days: int=Query(30,ge=1,le=365), day:str='', offset:int=Query(0,ge=0), limit:int=Query(50,ge=1,le=200), db=DB):
        return scoped_views.events(db,dict(store=store,country=country,language=language,app_id=app_id,kind=kind,field=field,group_name=group_name,days=days,day=day,offset=offset,limit=limit))

    @api.post('/research/annotations')
    def annotate(value:dict=Body(...),db=DB):return insights.add_annotation(db,value)

    @api.get('/research/markets/{store}/{app_id}')
    def market_matrix(store:str,app_id:str,db=DB):return insights.app_markets(db,store,app_id)

    @api.get('/research/developers')
    def developer_list(store:str,q:str='',db=DB):return [row for selected in locales.stores(store) for row in insights.developers(db,selected,q)]

    @api.get('/research/developer-apps')
    def developer_detail(store:str,name:str,developer_id:str='',db=DB):return insights.developer_apps(db,store,name,developer_id)

    @api.get('/research/aso')
    def aso_workspace(store:str,app_id:str,country:str='us',language:str='en',db=DB):
        return aso.workspace(db,dict(store=store,app_id=app_id,country=country,language=language))

    @api.get('/research/aso/keywords')
    def aso_report_keywords(store:str,app_id:str,country:str='us',language:str='en',q:str=Query('',max_length=255),offset:int=0,db=DB):
        m=intel.market(dict(store=store,app_id=app_id,country=country,language=language))
        return keyword_reports.library(db,m,q,offset)

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
    def review_records(store:str,country:str='us',language:str='en',app_id:str='',q:str='',version:str='',reply_status:str='',
                     rating:int|None=Query(None,ge=1,le=5),since:float|None=Query(None,ge=0),offset:int=Query(0,ge=0,le=100000),db=DB):
        return scoped_views.reviews(db,dict(store=store,country=country,language=language,app_id=app_id,q=q,version=version,reply_status=reply_status,rating=rating,since=since,offset=offset))

    @api.post('/research/reviews/history')
    def review_versions(value:dict=Body(...),db=DB):return review_desk.review_history(db,value)

    @api.get('/research/comparisons')
    def sets(store:str="all",db=DB):
        scope,args=locales.where(store)
        rows=db.execute('SELECT * FROM comparison_sets WHERE '+scope+' ORDER BY updated_at DESC LIMIT 100',args).fetchall()
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
    def collection_activity(store:str='all',db=DB):
        from .dispatcher import status
        selected=locales.stores(store);marks=','.join('%s' for _ in selected)
        source_scope=' OR '.join("JSON_CONTAINS(JSON_EXTRACT(actions,'$[*].value.store'),JSON_QUOTE(%s))" for _ in selected)
        return {'plans':db.execute("SELECT id,name,status,completed,total,error,created_at FROM collection_plans WHERE (JSON_EXTRACT(specification,'$.input_source') IS NOT NULL AND ("+source_scope+")) OR COALESCE(JSON_UNQUOTE(JSON_EXTRACT(specification,'$.store')),JSON_UNQUOTE(JSON_EXTRACT(specification,'$.app_request.store'))) IN ("+marks+') ORDER BY id DESC LIMIT 100',(*selected,*selected)).fetchall(),
                'heartbeat':db.execute("SELECT next_at FROM schedules WHERE name='console-heartbeat'").fetchone(), 'dispatcher':status(db)}

    @api.get('/research/collection/lineage')
    def collection_lineage_report(store:str='all',hours:int=24,refresh:bool=False,db=DB):
        from .collection_lineage import report
        if store not in ('all','google','apple') or hours not in (1,24,168):
            raise ValueError('请选择有效商店和统计窗口')
        key=(store,hours)
        with lineage_lock:
            cached=lineage_cache.get(key)
            if cached and not refresh and time.monotonic()-cached[0]<60:
                return cached[1]
            result=report(db,cfg,store,hours)
            lineage_cache[key]=(time.monotonic(),result)
        return result

    @api.get('/research/plans/{identifier}/collection')
    def plan_collection_results(identifier:int,db=DB):
        return app_collection.report(db,identifier,allow_plan=True)

    @api.post('/research/collection/enqueue')
    def enqueue_collection(value:dict=Body(...),db=DB):
        return collection.enqueue(db,value)

    @api.get('/research/collection/revision')
    def collection_revision(db=DB):
        from .collection_batch import revision
        return {'revision':revision(db)}

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
    def collection_pipelines(counts:bool=True,db=DB):
        rows=pipelines.report(db,cfg,counts=False)
        if counts:
            pool=rows[0]
            key=(tuple(pool['countries']),pool['language'],pool['enabled'])
            with pipeline_lock:
                cached=pipeline_cache.get(key)
                if not cached or time.time()-cached['observed_at']>=60:
                    cached=pipelines.pool_summary(db,pool)
                    pipeline_cache.clear()
                    pipeline_cache[key]=cached
            pool.update(cached)
        return rows

    @api.get('/research/collection/limits')
    def collection_limit_settings(db=DB):
        from .collection_limits import settings
        return settings(db,cfg)

    @api.put('/research/collection/limits')
    def save_collection_limits(value:dict=Body(...),db=DB):
        from .collection_limits import save
        return save(db,cfg,value)

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
