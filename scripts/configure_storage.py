"""Generate private local credentials/configuration; never print secrets."""
import json
import os
from pathlib import Path
import secrets


def main():
    root=Path.cwd();path=root/'.env'
    values={}
    if path.exists():
        values=dict(line.split('=',1) for line in path.read_text(encoding='utf8').splitlines() if '=' in line and not line.startswith('#'))
    values.setdefault('OCEANBASE_HOST','127.0.0.1');values.setdefault('OCEANBASE_PORT','2881')
    values.setdefault('OCEANBASE_USER','root@appmon');values['OCEANBASE_DATABASE']=os.environ.get('OCEANBASE_DATABASE',values.get('OCEANBASE_DATABASE','appstore_applications_monitoring'))
    values.setdefault('S3_ENDPOINT','http://127.0.0.1:8333');values.setdefault('S3_PUBLIC_ENDPOINT','http://127.0.0.1:8333');values.setdefault('S3_BUCKET','appmon')
    for key in ('OCEANBASE_PASSWORD','OCEANBASE_SYS_PASSWORD','S3_ACCESS_KEY','S3_SECRET_KEY'):
        if not values.get(key):values[key]=secrets.token_hex(24)
    values['OCEANBASE_ROOT_PASSWORD']=values['OCEANBASE_PASSWORD']
    path.write_text(''.join(k+'='+v+'\n' for k,v in values.items()),encoding='utf8')
    folder=root/'data/runtime';folder.mkdir(parents=True,exist_ok=True)
    (folder/'s3.json').write_text(json.dumps({'identities':[{'name':'appmon','credentials':[{'accessKey':values['S3_ACCESS_KEY'],'secretKey':values['S3_SECRET_KEY']}],'actions':['Admin','Read','Write','List','Tagging']}]}),encoding='utf8')
    # The mysql filer driver speaks the protocol to OceanBase; no MySQL server.
    (folder/'filer.toml').write_text('[leveldb2]\nenabled=false\n[mysql]\nenabled=true\nhostname="oceanbase"\nport=2881\nusername="root@appmon"\npassword='+json.dumps(values['OCEANBASE_PASSWORD'])+'\ndatabase="seaweedfs"\nconnection_max_idle=4\nconnection_max_open=16\n',encoding='utf8')
    print('Private OceanBase and SeaweedFS configuration written to .env and data/runtime.')


if __name__=='__main__':main()
