"""Structured diagnostics without request headers, bodies, credentials or locals."""
import re
import traceback

ALLOWED = {'error_type','cause_type','stage','method','host','status_code','timeout_seconds',
           'retry_after_seconds','duration_seconds','stack','note'}


def clean(value,limit=8000):
    value=re.sub(r'https?://\S+','[URL已隐藏]',str(value))
    value=re.sub(r'(?i)\bBearer\s+[^\s,;\"\']+','Bearer [已隐藏]',value)
    value=re.sub(r'(?i)\b(password|token|secret|cookie|authorization)[\"\']?\s*[:=]\s*[\"\']?[^\s,;\"\']+',r'\1=[已隐藏]',value)
    return value[:limit]


def sanitize(details):
    if not isinstance(details,dict):return None
    return {key:(value if isinstance(value,(int,float,bool)) else clean(value))
            for key,value in details.items() if key in ALLOWED and value is not None}


def diagnostics(error,**extra):
    cause=error.__cause__
    # Frame locations and source lines are useful; locals and exception repr are not logged.
    stack=''.join(traceback.format_list(traceback.extract_tb((cause or error).__traceback__)))
    return sanitize({'error_type':type(error).__name__,'cause_type':type(cause).__name__ if cause else None,
                     'stack':stack,**(getattr(error,'details',None) or {}),**extra})
