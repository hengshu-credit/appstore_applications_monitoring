"""Fetch bounded public samples without accounts, cookies, tokens, or paid APIs."""
import argparse
import json
import time
from appmon.public_sources import collect

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--app',action='append',default=[],help='store:app_id; repeat to collect multiple apps')
parser.add_argument('--country',action='append',default=[],help='Google WEB search RSS country; not app-store hot searches')
parser.add_argument('--output',default='data/public-sources')
parser.add_argument('--proxy',default=None)
parser.add_argument('--config',default='config.toml')
args=parser.parse_args();summary=[]
from appmon.config import load
from appmon.source_profiles import profile
cfg=load(args.config)
for kind,key in [('app',x) for x in args.app]+[('country',x) for x in args.country]:
    try:
        options=dict(zip(('store','app_id'),key.split(':',1))) if kind=='app' else {'country':key}
        result=collect(args.output,proxy=args.proxy,origin=profile(cfg,'community' if kind=='app' else 'web_feed')['origin'],**options)
        summary.append({'source':key,'ok':True,'scope':result['scope'],'rows':len(result.get('items',[])),
                        'metrics':result.get('values_display')})
    except Exception as error:summary.append({'source':key,'ok':False,'error':str(error)})
    time.sleep(1)
print(json.dumps(summary,ensure_ascii=False,indent=2))
