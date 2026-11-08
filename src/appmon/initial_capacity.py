"""Honest lower bounds for a cold start; never count partial baselines as full data."""
import math,time,shutil
from .source_registry import collector_config
from .collection_limits import settings

def report(db,cfg,hours=24):
    effective=collector_config(db,cfg,'pool');limits=settings(db,cfg);seconds=hours*3600
    inventory=db.execute('SELECT store,COUNT(*) apps FROM apps GROUP BY store').fetchall()
    capacities={'apple':effective.get('apple_lookup_batch_size',50),'google':effective.get('google_rpc_batch_size',20)}
    requests={row['store']:math.ceil(row['apps']/capacities[row['store']]) for row in inventory}
    count=sum(requests.values());maximum=seconds/limits['global_interval_seconds']
    if limits['daily_request_limit']:maximum=min(maximum,limits['daily_request_limit']*hours/24)
    bounds=[count*limits['global_interval_seconds']]+[requests.get(store,0)*limits[store+'_interval_seconds'] for store in capacities]
    if limits['daily_request_limit']:bounds.append(count/limits['daily_request_limit']*86400)
    samples={};estimated=0;complete_sample=True
    for row in inventory:
        sample=db.execute("SELECT COUNT(*) n,AVG(bytes) bytes FROM (SELECT COALESCE(JSON_EXTRACT(payload,'$.__appmon_content_v1__.bytes'),OCTET_LENGTH(payload)) bytes FROM snapshots WHERE store=%s LIMIT 200) sample",(row['store'],)).fetchone()
        samples[row['store']]={'rows':sample['n'],'mean_payload_bytes':int(sample['bytes'] or 0)}
        if not sample['n']:complete_sample=False
        estimated+=int(sample['bytes'] or 0)*row['apps']
    return {'observed_at':time.time(),'target_hours':hours,'inventory':inventory,'batch_sizes':capacities,
        'detail_requests_lower_bound':count,'detail_hours_lower_bound':round(max(bounds,default=0)/3600,2),
        'configured_window_requests_maximum':int(maximum),'request_capacity_gap':max(0,count-int(maximum)),
        'free_gib':round(shutil.disk_usage(cfg['data_dir']).free/1024**3,2),'minimum_free_gib':cfg.get('min_free_gb',15),
        'payload_samples':samples,'estimated_payload_gib':round(estimated/1024**3,2) if complete_sample and inventory else None,
        'storage_note':'按现有成功详情样本估算单份结构化载荷，不含索引、历史副本和图片；样本不代表全部应用。',
        'full_scope_24h_ready':False,
        'scope_note':'仅计算全库每个应用一个市场、一次详情的冷启动理论下限；未包含全球市场展开、评论、关键词、素材和失败补采。',
        'blockers':['免费批量包只提供部分历史字段，不能作为完整实时详情完成凭据','全球市场、全历史评论与商业数据尚无已验证的完整免费覆盖'],
        'capacity_note':'批量结果按全部成功且满批估算；公开接口允许量及实际数据缺失会使耗时增加。'}
