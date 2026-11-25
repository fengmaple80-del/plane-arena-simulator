"""Stage a verified GitHub portable release alongside the running application."""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path, PureWindowsPath
from urllib.request import Request, urlopen, getproxies, build_opener, ProxyHandler, install_opener
from zipfile import ZipFile

REPO = 'fengmaple80-del/plane-arena-simulator'

# A standalone NO_PROXY variable masks the Windows proxy in urllib's defaults.
if sys.platform == 'win32' and not any(getproxies().get(k) for k in ('http','https')):
    from urllib.request import getproxies_registry
    system_proxies = getproxies_registry()
    if system_proxies:
        install_opener(build_opener(ProxyHandler(system_proxies)))

def version(tag):
    if not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+', tag):
        raise ValueError('版本号格式异常')
    return tuple(map(int, tag.lstrip('v').split('.')))

def fetch(url):
    accept = 'application/octet-stream' if '/releases/assets/' in url else 'application/vnd.github+json'
    return urlopen(Request(url, headers={'User-Agent':'plane-arena-updater','Accept':accept}), timeout=60)

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
        asset_id = int(asset['id'])
        url = f'https://api.github.com/repos/{REPO}/releases/assets/{asset_id}?download={int(time.time())}'
        # Resolve the signed release URL once. Ranged transfers can retry a single part.
        head = Request(url, headers={'Accept':'application/octet-stream','Range':'bytes=0-0','User-Agent':'plane-arena-updater'})
        with urlopen(head,timeout=30) as response:
            signed_url = response.geturl()
        from concurrent.futures import ThreadPoolExecutor
        size=asset['size']
        part_size=2*1024**2
        def download_part(start):
            end=min(start+part_size,size)-1
            part=temp/f'{start}.part'
            for attempt in range(3):
                try:
                    request=Request(signed_url,headers={'Range':f'bytes={start}-{end}','User-Agent':'plane-arena-updater'})
                    with urlopen(request,timeout=25) as response:
                        if response.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                            raise ValueError('下载服务返回了错误的文件范围')
                        data=response.read(end-start+2)
                    if len(data)!=end-start+1:
                        raise ValueError('更新包分段下载不完整')
                    part.write_bytes(data)
                    return part
                except OSError:
                    if attempt==2:raise
        with ThreadPoolExecutor(max_workers=4) as pool,archive.open('xb') as out:
            for part in pool.map(download_part,range(0,size,part_size)):
                data=part.read_bytes();digest.update(data);out.write(data);part.unlink()
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
