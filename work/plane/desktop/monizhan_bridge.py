import base64
import binascii
import json
import os
import sys
import shutil
from pathlib import Path
import subprocess

LIMIT = 12 * 1024 * 1024


def execute(args, data=None, timeout=20):
    try:
        result = subprocess.run(args, input=data, capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError('读取或计算超时，请重试。') from None
    if result.returncode:
        lines = result.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise ValueError(lines[-1][:250] if lines else '读取失败，请重试。')
    return result.stdout


def mumu_image(cfg, requested):
    candidates = [cfg.get('adb'), os.getenv('MUMU_ADB'), shutil.which('adb')]
    for var in ('ProgramFiles','ProgramFiles(x86)'):
        base = Path(os.environ.get(var, '.'))
        candidates.extend(base / name / 'nx_main/adb.exe' for name in ('Netease/MuMuPlayer-12.0','MuMu/MuMu Player 12','MuMuPlayer-12.0'))
    adb = next((Path(p) for p in candidates if p and Path(p).is_file() and Path(p).with_name('MuMuManager.exe').is_file()),None)
    if adb is None:
        raise ValueError('请启动 MuMu；若安装在自定义目录，请将环境变量 MUMU_ADB 设为 nx_main 下的 adb.exe 路径。')
    manager = adb.with_name('MuMuManager.exe')
    raw = json.loads(execute([manager,'info','-v','all']))
    devices = [r for r in ([raw] if 'index' in raw else raw.values()) if r.get('is_android_started') and r.get('adb_port')]
    if not devices:
        raise ValueError('请启动 MuMu 中的安卓实例，并打开模拟战对阵页面。')
    if requested is None and len(devices)>1:
        return {'status':'choose_device','devices':[{'id':str(r['index']),'name':r['name']} for r in devices]}
    row = next((r for r in devices if requested is None or str(r['index'])==str(requested)), None)
    if row is None:
        raise ValueError('所选 MuMu 实例已关闭。')
    serial = '127.0.0.1:'+str(int(row['adb_port']))
    execute([adb,'connect',serial])
    data = execute([adb,'-s',serial,'exec-out','screencap','-p'])
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('未能读取 MuMu 屏幕。')
    return data


def predict(request):
    settings = Path(__file__).resolve().parents[3] / 'data' / 'monizhan-local.json'
    cfg = json.loads(settings.read_text(encoding='utf-8-sig')) if settings.is_file() else {}
    worker = settings.parents[1] / 'work/monizhan/local_battle.py'
    python = sys.executable
    source = request.get('source')
    if source in ('forms','manual'):
        payload = json.dumps({'action':source,'positions':request.get('positions')}).encode()
        return json.loads(execute([python,'-B','-X','utf8',worker.with_name('desktop_activity.py')],payload,180))
    if source == 'mumu':
        data = mumu_image(cfg,request.get('device'))
        if isinstance(data,dict): return data
    elif source == 'clipboard':
        data = execute([python,'-B','-X','utf8',Path(__file__).with_name('clipboard_image.py')])
    elif request.get('path'):
        path = Path(request['path'])
        if path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
            raise ValueError('请选择 PNG、JPEG 或 WebP 截图。')
        with path.open('rb') as f:
            data = f.read(LIMIT+1)
    else:
        encoded = request.get('image_base64','')
        if not isinstance(encoded,str) or len(encoded)>LIMIT*4//3+4: raise ValueError('图片超过 12 MB。')
        try: data=base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error): raise ValueError('无法读取图片。') from None
    if not 0<len(data)<=LIMIT: raise ValueError('请导入一张大小不超过 12 MB 的截图。')
    result=json.loads(execute([python,'-B','-X','utf8',worker],data,180))
    return {k:result[k] for k in ('status','slots','issues','summary','blocked_heroes') if k in result}
