"""Build an offline Compose release, excluding all local data and credentials."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args,cwd=ROOT,check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='dist/guanxing-offline.zip')
    args=parser.parse_args()
    output=Path(args.output).resolve()
    if output.exists():raise ValueError('Output exists; use a new release name')
    output.parent.mkdir(parents=True,exist_ok=True)
    sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())
    tag=sha[:12]+('-working' if dirty else '')
    backend='appmon-backend:'+tag;frontend='appmon-frontend:'+tag
    run('docker','build','-t',backend,'.')
    run('docker','build','-t',frontend,'frontend')
    storage_images=['oceanbase/oceanbase-ce:4.4.2.1-101000022026050611','chrislusf/seaweedfs:4.52']
    for image in storage_images:run('docker','pull',image)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        stage=Path(temp)
        run('docker','image','save','-o',str(stage/'images.tar'),backend,frontend,*storage_images)
        for name in ('compose.yaml','compose.volume.yaml','compose.storage-network.yaml','config.toml','.env.example'):
            shutil.copyfile(ROOT/name,stage/name)
        shutil.copyfile(ROOT/'scripts/configure_storage.py',stage/'configure_storage.py')
        shutil.copyfile(ROOT/'docs/offline-deployment.md',stage/'README.md')
        (stage/'release.env').write_text(f'APPMON_BACKEND_IMAGE={backend}\nAPPMON_FRONTEND_IMAGE={frontend}\n',encoding='utf-8')
        images=json.loads(subprocess.check_output(['docker','image','inspect',backend,frontend,*storage_images],text=True))
        (stage/'release.json').write_text(json.dumps(dict(source_commit=sha,working_tree_changes=dirty,
            images=[{k:i[k] for k in ('Id','RepoTags','Architecture','Os')} for i in images]),indent=2),encoding='utf-8')
        with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for path in stage.iterdir():archive.write(path,path.name)
    with output.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(checksum+'  '+output.name+'\n',encoding='ascii')
    print(json.dumps(dict(path=str(output),sha256=checksum,bytes=output.stat().st_size),indent=2))


if __name__=='__main__':main()
