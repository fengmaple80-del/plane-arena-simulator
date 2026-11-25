"""Stage a verified GitHub portable release alongside the running application."""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path, PureWindowsPath
from urllib.request import Request, urlopen
from zipfile import ZipFile

REPO = 'fengmaple80-del/plane-arena-simulator'

def version(tag):
    if not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+', tag):
        raise ValueError('版本号格式异常')
    return tuple(map(int, tag.lstrip('v').split('.')))

def fetch(url):
    return urlopen(Request(url, headers={'User-Agent':'plane-arena-updater'}), timeout=40)

def latest(current):
    with fetch(f'https://api.github.com/repos/{REPO}/releases/latest') as stream:
        info=json.load(stream)
    tag=info['tag_name']
    newer=version(tag)>version(current) and not (info['draft'] or info['prerelease'])
    result=dict(current=current, latest=tag, available=newer)
    if not newer:
        return result, None
    name=f'plane-arena-{tag}-windows-x64.zip'
    for asset in info['assets']:
        if (asset['name']==name and asset['state']=='uploaded' and asset['browser_download_url']==f'https://github.com/{REPO}/releases/download/{tag}/{name}'
                and re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or '') and 0<asset['size']<=1024**3):
            return result,asset
    raise ValueError('该版本的 Windows 包尚未就绪，请稍后重试')

def install(asset, tag, base):
    updates=base/'updates'
    updates.mkdir(exist_ok=True)
    if updates.is_symlink() or updates.is_junction():
        raise ValueError('更新目录不可用')
    # Unique directories keep failed downloads and concurrent clicks away from the running copy.
    with tempfile.TemporaryDirectory(prefix='download-',dir=updates) as scratch:
        temp=Path(scratch)
        archive=temp/'package.zip'
        digest=hashlib.sha256()
        with fetch(asset['browser_download_url']) as response, archive.open('xb') as out:
            while data:=response.read(1024**2):
                out.write(data);digest.update(data)
                if out.tell()>asset['size']:
                    raise ValueError('下载大小与发布记录不符')
        if archive.stat().st_size!=asset['size'] or 'sha256:'+digest.hexdigest()!=asset['digest']:
            raise ValueError('下载文件校验失败')
        payload=temp/'app'
        with ZipFile(archive) as z:
            if sum(i.file_size for i in z.infolist())>3*1024**3:
                raise ValueError('更新包解压大小异常')
            for item in z.infolist():
                name=item.filename.rstrip('/')
                parts=name.split('/')
                if '\\' in name or ':' in name or any(p in ('','.','..') or p.endswith((' ','.')) or PureWindowsPath(p).is_reserved() for p in parts):
                    raise ValueError('更新包文件路径异常')
                path=payload.joinpath(*parts)
                if item.is_dir():
                    path.mkdir(parents=True,exist_ok=True)
                else:
                    path.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(item) as source, path.open('xb') as out:
                        shutil.copyfileobj(source,out)
        for rel in ('位面对战台.exe','runtime/python/python.exe','work/plane/desktop/plane_bridge.py','updater/update.py'):
            if not (payload/rel).is_file():raise ValueError('更新包缺少 '+rel)
        # Publish only fully extracted directories. Never overlay the active copy.
        dest=updates/(tag+'-'+temp.name.removeprefix('download-'))
        os.rename(payload,dest)
        return dest.name

if __name__=='__main__':
    try:
        action,current,base=sys.argv[1:]
        result,asset=latest(current)
        if action=='install' and asset:
            result['folder']=install(asset,result['latest'],Path(base))
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps({'error':str(error)}));sys.exit(1)
