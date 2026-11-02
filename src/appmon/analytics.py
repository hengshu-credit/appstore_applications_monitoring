"""Explainable metadata checks, observed market summaries and imported ad facts."""
import csv
import hashlib
import io
import json
import math
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from .db import dump
from .intelligence import market, text
from .keywords import app_coverage


def suggestions(db, value):
    m = market(value)
    listing = db.execute("SELECT * FROM listings WHERE store=%s AND app_id=%s AND country=%s AND language=%s",
                         (m["store"], m["app_id"], m["country"], m["language"])).fetchone()
    if not listing or not listing["payload"]:
        return {"actions": [], "reason": "需要先采集该地区应用详情"}
    p = json.loads(listing["payload"])
    actions = []
    for field, label in (("name", "标题"), ("description", "描述"), ("icon", "图标"), ("screenshots", "截图")):
        if not p.get(field):
            actions.append({"kind": "missing_metadata", "title": f"核查{label}", "evidence": f"最近快照的 {field} 字段为空，可能是来源未返回", "observed_at": listing["last_success"]})
    words = app_coverage(db, m["store"], m["app_id"], m["country"], m["language"])["coverage"]
    for row in words:
        if row["stale"]:
            actions.append({"kind": "stale_keyword", "title": f"更新关键词：{row['target']['keyword']}", "evidence": "最近成功采集已过期或最近尝试失败", "target_id": row["target"]["id"]})
        elif row["change"] is not None and row["change"] <= -5:
            actions.append({"kind": "rank_drop", "title": f"复查关键词：{row['target']['keyword']}", "evidence": f"同口径观察从 {row['previous_position']} 降至 {row['position']}", "target_id": row["target"]["id"]})
    # Candidate dictionary, deliberately no fabricated popularity/difficulty score.
    corpus = " ".join(str(p.get(k) or "") for k in ("name", "summary", "description"))
    candidates = list(dict.fromkeys(w.lower() for w in re.findall(r"[^\W\d_]{3,30}", corpus, flags=re.UNICODE)))[:80]
    return {"actions": actions, "metadata_candidates": candidates,
            "source": "stored_metadata_and_observed_ranks", "observed_at": listing["last_success"],
            "note": "候选词来自已采集元数据，不代表搜索热度或流量预测。"}


def review_summary(db, filters):
    where, args = [], []
    for key in ("store", "app_id", "country", "language"):
        if filters.get(key):
            where.append(f"{key}=%s")
            args.append(filters[key])
    sql = " WHERE " + " AND ".join(where) if where else ""
    distribution = db.execute("SELECT rating,count(*) count FROM reviews" + sql + " GROUP BY rating ORDER BY rating", args).fetchall()
    # Rules are transparent, editable in source, and may overlap. No sentiment model.
    topics = {"稳定性": ["crash", "bug", "闪退", "崩溃"], "广告": ["advert", "ads", "广告"],
              "付费": ["payment", "subscription", "price", "收费", "订阅"], "登录": ["login", "sign in", "登录"]}
    sample = db.execute("SELECT content FROM reviews" + sql + " ORDER BY last_seen DESC LIMIT 5000", args).fetchall()
    counts = {topic: sum(any(word in row["content"].lower() for word in terms) for row in sample) for topic, terms in topics.items()}
    return {"ratings": distribution, "topics": counts, "topic_rules": topics, "topic_sample_size": len(sample),
            "scope": "已采集评论；主题统计最多最近5000条，可重叠，不代表全部用户"}


def import_ad_observations(db, rows):
    prepared = []
    for row in rows:
        m = market(row | {"language": row.get("language", "en")})
        source, keyword = text(row.get("source"), 255), text(row.get("keyword"), 255)
        url = row.get("evidence_url", "")
        if not isinstance(url, str) or len(url) > 2000 or urlsplit(url).scheme not in ("https", "http") or not urlsplit(url).hostname or urlsplit(url).username:
            raise ValueError("evidence_url must be an HTTP(S) evidence link without credentials")
        at = row.get("observed_at")
        if isinstance(at, bool) or not isinstance(at, (float, int)) or not math.isfinite(at) or not 0 < at <= time.time() + 300:
            raise ValueError("observed_at must be a valid past UTC epoch")
        p = m | dict(source=source, keyword=keyword, evidence_url=url, observed_at=at, placement=text(row.get("placement", "search_ad")))
        digest = hashlib.sha256(dump(p).encode()).hexdigest()
        prepared.append((digest, m["store"], m["app_id"], m["country"], keyword, at, source, url, p["placement"], dump(p)))
    with db:
        added = db.executemany('''INSERT INTO ad_observations(fingerprint,store,app_id,country,keyword,observed_at,source,evidence_url,placement,payload)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE fingerprint=ad_observations.fingerprint''', prepared).rowcount if prepared else 0
    return {"received": len(rows), "added": added}


def import_ad_reports(db, rows):
    prepared = []
    for row in rows:
        required = {"app_id", "country", "report_date", "campaign", "keyword", "impressions", "taps", "installs", "spend", "currency", "source"}
        if not required <= row.keys():
            raise ValueError("Missing report fields: " + ', '.join(sorted(required - row.keys())))
        m = market(dict(store="apple", app_id=row.get("app_id"), country=row.get("country"), language="en"))
        day = date.fromisoformat(row["report_date"]).isoformat()
        numbers = []
        for key in ("impressions", "taps", "installs"):
            value = row.get(key)
            if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,15}", str(value)):
                raise ValueError(f"Invalid {key}")
            numbers.append(int(value))
        try:
            spend = Decimal(str(row["spend"]))
        except (InvalidOperation, KeyError) as exc:
            raise ValueError("Invalid spend") from exc
        if not spend.is_finite() or not 0 <= spend < Decimal('1000000000000') or spend.as_tuple().exponent < -6:
            raise ValueError("spend must be nonnegative with at most six decimals")
        currency = row.get("currency", "")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("currency must be an ISO currency code")
        campaign, keyword, source = [text(row.get(k), 255) for k in ("campaign", "keyword", "source")]
        # One daily aggregate per source/campaign/keyword/market. Reimports replace, never double count.
        digest = hashlib.sha256(dump([day, m["app_id"], m["country"], campaign, keyword, currency, source]).encode()).hexdigest()
        prepared.append((digest, day, m["app_id"], m["country"], campaign, keyword, *numbers, spend, currency, source, time.time()))
    with db:
        if prepared:
            db.executemany('''INSERT INTO ad_reports(fingerprint,report_date,app_id,country,campaign,keyword,impressions,taps,installs,spend,currency,source,imported_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) AS incoming ON DUPLICATE KEY UPDATE
                impressions=incoming.impressions,taps=incoming.taps,installs=incoming.installs,spend=incoming.spend,imported_at=incoming.imported_at''', prepared)
    return {"received": len(rows), "processed": len(prepared)}
