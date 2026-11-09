"""Private, immutable S3 objects. Compression always preserves original bytes."""
import hashlib
import os
import re
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import zstandard


class ObjectStore:
    def __init__(self, namespace=None):
        self.bucket = os.environ.get('S3_BUCKET', 'appmon')
        self.prefix = (namespace or os.environ.get('OCEANBASE_DATABASE', 'appstore_applications_monitoring')) + '/'
        self.endpoint = os.environ.get('S3_ENDPOINT', 'http://127.0.0.1:8333')
        self.public_endpoint = os.environ.get('S3_PUBLIC_ENDPOINT', self.endpoint)
        args = dict(region_name=os.environ.get('S3_REGION','us-east-1'),
                    aws_access_key_id=os.environ.get('S3_ACCESS_KEY'),
                    aws_secret_access_key=os.environ.get('S3_SECRET_KEY'),
                    config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                                  retries={'max_attempts':3,'mode':'standard'},connect_timeout=10,read_timeout=120))
        if not args['aws_access_key_id'] or not args['aws_secret_access_key']:
            raise ValueError('S3_ACCESS_KEY and S3_SECRET_KEY are required')
        self.client = boto3.client('s3',endpoint_url=self.endpoint,**args)
        self.public = boto3.client('s3',endpoint_url=self.public_endpoint,**args)

    def initialize(self):
        try:self.client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if str(exc.response['Error']['Code']) not in ('404','NoSuchBucket','NotFound'):raise
            self.client.create_bucket(Bucket=self.bucket)

    def key(self, ref):
        if not isinstance(ref,str) or not re.fullmatch(r'[a-z][a-z0-9_-]*/[0-9a-f]{2}/[0-9a-f]{64}\.[a-z0-9]+',ref):
            raise ValueError('Invalid object reference')
        return self.prefix+ref

    def put(self, raw, *, kind='content', mime='application/octet-stream', compress=False, extension='bin'):
        raw=bytes(raw);digest=hashlib.sha256(raw).hexdigest()
        body=zstandard.ZstdCompressor(level=6).compress(raw) if compress else raw
        codec='zstd' if compress and len(body)<len(raw) else 'identity'
        if codec=='identity':body=raw
        ref=f'{kind}/{digest[:2]}/{digest}.{extension}'
        key=self.key(ref)
        # Equal original content always has the same key. PUT is atomic, so a
        # concurrent writer cannot publish a partial object or a partial reference.
        expected={'sha256':digest,'raw-bytes':str(len(raw)),'codec':codec,'original-mime':mime}
        valid=False
        try:
            head=self.client.head_object(Bucket=self.bucket,Key=key)
            valid=head.get('Metadata')==expected and head.get('ETag','').strip('"')==hashlib.md5(body).hexdigest()
        except ClientError as exc:
            if str(exc.response['Error']['Code']) not in ('404','NoSuchKey','NotFound'):raise
        if not valid:
            self.client.put_object(Bucket=self.bucket,Key=key,Body=body,
                ContentType=mime if codec=='identity' else 'application/zstd',
                Metadata=expected)
        return ref

    def exists(self, ref):
        try:self.client.head_object(Bucket=self.bucket,Key=self.key(ref));return True
        except ClientError as exc:
            if str(exc.response['Error']['Code']) in ('404','NoSuchKey','NotFound'):return False
            raise

    def put_file(self, path, *, kind='exports', extension='jsonl', mime='application/x-ndjson'):
        digest=hashlib.sha256()
        with open(path,'rb') as source:
            for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
        sha=digest.hexdigest();ref=f'{kind}/{sha[:2]}/{sha}.{extension}'
        if not self.exists(ref):
            self.client.upload_file(str(path),self.bucket,self.key(ref),ExtraArgs={
                'ContentType':mime,'Metadata':{'sha256':sha,'raw-bytes':str(os.path.getsize(path)),
                                             'codec':'identity','original-mime':mime}})
        return ref

    def get(self, ref, *, max_bytes=64*1024*1024):
        response=self.client.get_object(Bucket=self.bucket,Key=self.key(ref))
        try:
            meta=response['Metadata'];size=int(meta['raw-bytes'])
            if size<0 or size>max_bytes:raise ValueError('Object exceeds read limit')
            body=response['Body'].read(max_bytes+1)
            if len(body)>max_bytes:raise ValueError('Object exceeds read limit')
            if meta['codec']=='zstd':
                raw=zstandard.ZstdDecompressor().decompress(body,max_output_size=max(size,1),allow_extra_data=False)
            elif meta['codec']=='identity':raw=body
            else:raise ValueError('Unknown object codec')
            expected=ref.rsplit('/',1)[1].split('.')[0]
            if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=expected or meta['sha256']!=expected:
                raise ValueError('Object checksum mismatch')
            return raw
        finally:response['Body'].close()

    def url(self, ref, *, expires=300):
        info=self.client.head_object(Bucket=self.bucket,Key=self.key(ref))
        if info['Metadata'].get('codec')!='identity':raise ValueError('Compressed objects require server decoding')
        return self.public.generate_presigned_url('get_object',Params={'Bucket':self.bucket,'Key':self.key(ref)},ExpiresIn=expires)

    def verify(self, ref):
        """Validate arbitrarily large backup objects without loading them in RAM."""
        response=self.client.get_object(Bucket=self.bucket,Key=self.key(ref))
        body=response['Body'];reader=None
        try:
            meta=response['Metadata'];expected_size=int(meta['raw-bytes'])
            if expected_size<0:raise ValueError('Invalid object size')
            if meta['codec']=='zstd':reader=zstandard.ZstdDecompressor().stream_reader(body,closefd=False)
            elif meta['codec']=='identity':reader=body
            else:raise ValueError('Unknown object codec')
            checksum=hashlib.sha256();count=0
            for chunk in iter(lambda:reader.read(1024*1024),b''):
                count+=len(chunk)
                if count>expected_size:raise ValueError('Object size mismatch')
                checksum.update(chunk)
            expected=ref.rsplit('/',1)[1].split('.')[0]
            if count!=expected_size or checksum.hexdigest()!=expected or meta['sha256']!=expected:
                raise ValueError('Object checksum mismatch')
            return count
        finally:
            if reader is not None and reader is not body:reader.close()
            body.close()

    def inventory(self):
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=self.prefix):
            for item in page.get('Contents',[]):yield item['Key'][len(self.prefix):]


@lru_cache(maxsize=64)
def store(namespace=None):
    return ObjectStore(namespace)


def for_config(cfg):
    return store(cfg['oceanbase']['database'])
