"""Import the retained read-only MySQL snapshot into a separate OceanBase DB."""
import argparse
from appmon.mysql_import import Importer

parser=argparse.ArgumentParser()
parser.add_argument('--target',required=True)
parser.add_argument('--source-root',default='data')
parser.add_argument('--state',default='data/mysql-import')
parser.add_argument('--batch-size',type=int,default=2000)
parser.add_argument('--activate',action='store_true')
parser.add_argument('--writers-stopped',action='store_true')
args=parser.parse_args()
if args.activate and not args.writers_stopped:parser.error('--activate requires stopped API and workers')
job=Importer(args.target,args.source_root,args.state,args.batch_size)
if args.activate:
    import json
    print(json.dumps(job.activate()))
else:job.run()
