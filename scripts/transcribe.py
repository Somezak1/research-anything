#!/usr/bin/env python3
"""fun-asr 音视频转写：直传优先 + 慢链熔断降级，产出全文/SRT/完整JSON。

research-anything Phase 3（视频口播提取）的正式实现。实测依据（2026-07-13，样本与耗时
元数据见调研产物 docs/research/video-to-text-api/test/p0/）：
- 抖音 video_download_url、小红书新鲜 video_url 可免下载直传；坏链 3.6s 返回
  FILE_403_FORBIDDEN；5 天旧链"活着但慢约 50 倍"（6s→313s）——因此直传按 --timeout
  （默认 180s）熔断，自动降级：curl 下载 → 百炼临时通道上传（oss://）→ 重提。
- 105s / 845s / 4226s 素材转写耗时 5.4s / 16.6s / 66s（≈60 倍实时）；直传视频容器与
  抽音轨 mp3 结果等价（相似度 99.3%），无需 ffmpeg 抽音轨。
- 已知坑：带人声演唱的 BGM 段会被转成歌词乱码，下游做笔记时对开头/间奏段留意。
- 临时通道官方限制：单文件≤1GB、凭证接口 100 QPS、48h 时效，勿用于高并发生产。
凭据：环境变量 DASHSCOPE_API_KEY（放 ~/.zshrc）。凭据绝不写进 skill/报告。

用法:
  python3 transcribe.py --url  "<公网媒体直链>"  --out <输出前缀>
  python3 transcribe.py --file <本地音视频文件>  --out <输出前缀>
产出: <out>.txt（全文）/ <out>.srt（句级时间戳）/ <out>.json（完整结果）；
stdout 打一行 JSON 摘要，失败时退出码 2。
"""
import argparse, json, os, subprocess, sys, tempfile, time, urllib.request

BASE = 'https://dashscope.aliyuncs.com/api/v1'


def _key():
    k = os.environ.get('DASHSCOPE_API_KEY')
    if not k:
        raise RuntimeError('缺少环境变量 DASHSCOPE_API_KEY（阿里云百炼 API Key，建议放 ~/.zshrc）')
    return k


def api(path, method='GET', body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Authorization', f'Bearer {_key()}')
    data = None
    if body is not None:
        req.add_header('Content-Type', 'application/json')
        data = json.dumps(body).encode()
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, data, timeout=60) as r:
        return json.load(r)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read().decode('utf-8'))


def download(url, dest, runner=subprocess.run):
    r = runner(['curl', '-L', '-sS', '--fail', '--max-time', '600', '-o', dest, url])
    if r.returncode != 0 or not os.path.exists(dest) or not os.path.getsize(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
        raise RuntimeError(f'下载失败: {url[:80]}')
    return dest


def temp_upload(path, api_fn=api, runner=subprocess.run):
    """百炼临时上传通道 → oss:// 地址（配合 X-DashScope-OssResourceResolve 头使用）。"""
    if not os.path.isfile(path) or not os.path.getsize(path):
        raise RuntimeError(f'待上传文件不存在或为空: {path}')
    if os.path.getsize(path) > 1024 * 1024 * 1024:
        raise RuntimeError('待上传文件超过临时通道 1GB 限制')
    pol = api_fn('/uploads?action=getPolicy&model=fun-asr')['data']
    key = f"{pol['upload_dir']}/{os.path.basename(path)}"
    r = runner(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '-X', 'POST', pol['upload_host'],
                '-F', f"OSSAccessKeyId={pol['oss_access_key_id']}", '-F', f"Signature={pol['signature']}",
                '-F', f"policy={pol['policy']}", '-F', f"key={key}", '-F', 'x-oss-object-acl=private',
                '-F', 'x-oss-forbid-overwrite=true', '-F', 'success_action_status=200', '-F', f'file=@{path}'],
               capture_output=True, text=True)
    if r.stdout != '200':
        raise RuntimeError(f'临时通道上传失败 HTTP {r.stdout}')
    return 'oss://' + key


def submit(url, language, api_fn=api):
    hdr = {'X-DashScope-Async': 'enable'}
    if url.startswith('oss://'):
        hdr['X-DashScope-OssResourceResolve'] = 'enable'
    params = {}
    if language:
        params['language_hints'] = [language]
    # parameters 必带（哪怕空 {}）：官方文档明示新版域名下省略会"提交成功但识别失败"
    body = {'model': 'fun-asr', 'input': {'file_urls': [url]}, 'parameters': params}
    return api_fn('/services/audio/asr/transcription', 'POST', body, hdr)['output']['task_id']


def wait(task_id, timeout, api_fn=api, sleep=time.sleep, interval=3):
    """轮询至终态，至少查一次；超过 timeout 返回 None（慢链熔断，放弃等待）。"""
    t0 = time.monotonic()
    while True:
        sleep(interval)
        st = api_fn(f'/tasks/{task_id}')['output']
        if st['task_status'] in ('SUCCEEDED', 'FAILED', 'UNKNOWN'):
            return st
        if time.monotonic() - t0 >= timeout:
            return None


def pick(st):
    """返回 (ok, payload)：ok=True 时 payload 含 transcription_url/billed_seconds，否则为错误码。"""
    if not st or st.get('task_status') != 'SUCCEEDED':
        return False, (st or {}).get('code') or 'TIMEOUT'
    res = (st.get('results') or [{}])[0]
    sub = res.get('subtask_status') or res.get('output', {}).get('subtask_status')
    if sub == 'FAILED':
        return False, res.get('code') or res.get('output', {}).get('code') or 'SUBTASK_FAILED'
    turl = res.get('transcription_url') or res.get('output', {}).get('transcription_url')
    if not turl:
        return False, 'NO_TRANSCRIPTION_URL'
    return True, {'transcription_url': turl, 'billed_seconds': (res.get('usage') or {}).get('duration')}


def srt_ts(ms):
    h, r = divmod(int(ms), 3600000)
    m, r = divmod(r, 60000)
    s, x = divmod(r, 1000)
    return f'{h:02}:{m:02}:{s:02},{x:03}'


def build_srt(sentences):
    blocks = [f"{i}\n{srt_ts(s['begin_time'])} --> {srt_ts(s['end_time'])}\n{s['text']}\n"
              for i, s in enumerate(sentences, 1)]
    return '\n'.join(blocks)


def _run_once(url, language, timeout, api_fn, sleep):
    tid = submit(url, language, api_fn)
    ok, payload = pick(wait(tid, timeout, api_fn, sleep))
    return ok, payload, tid


def ledger_append(path, meta):
    """费用台账：每次转写调用追加一行（含 billed_seconds），供 Stage 2 对账 manifest 的 asr_authorization。"""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), **meta}, ensure_ascii=False) + '\n')
    except OSError as exc:
        print(f'警告: ASR 台账写入失败（转写产物不受影响）: {exc}', file=sys.stderr)


def transcribe(url=None, file=None, out='out', language='zh', timeout=180, ledger=None,
               api_fn=api, fetch=fetch_json, dl=download, up=temp_upload, sleep=time.sleep):
    t0 = time.monotonic()
    if url:
        ok, payload, tid = _run_once(url, language, timeout, api_fn, sleep)
        path_used = 'direct'
        if not ok:  # 快速失败码或慢链超时 → 下载-上传降级
            tmp = os.path.join(tempfile.gettempdir(), f'transcribe_dl_{os.getpid()}{os.path.splitext(url.split("?")[0])[1] or ".mp4"}')
            oss = up(dl(url, tmp), api_fn)
            ok, payload, tid = _run_once(oss, language, timeout, api_fn, sleep)
            path_used = 'fallback_download_upload'
    else:
        oss = up(file, api_fn)
        ok, payload, tid = _run_once(oss, language, timeout, api_fn, sleep)
        path_used = 'upload'
    meta = {'source': url or file, 'path_used': path_used, 'task_id': tid,
            'wall_seconds': round(time.monotonic() - t0, 1)}
    if not ok:
        meta.update(status='FAILED', code=payload)
    else:
        data = fetch(payload['transcription_url'])  # 结果 URL 仅 24h 有效，立即取回落盘
        tr = data['transcripts'][0]
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out + '.txt', 'w', encoding='utf-8') as f:
            f.write(tr['text'])
        with open(out + '.srt', 'w', encoding='utf-8') as f:
            f.write(build_srt(tr['sentences']))
        with open(out + '.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        meta.update(status='SUCCEEDED', billed_seconds=payload.get('billed_seconds'),
                    sentences=len(tr['sentences']), chars=len(tr['text']),
                    txt=out + '.txt', srt=out + '.srt')
    ledger_append(ledger or os.path.join(os.path.dirname(os.path.abspath(out)), 'asr_ledger.jsonl'), meta)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--url', help='公网媒体直链（http/https）——直传优先，失败自动降级下载-上传')
    g.add_argument('--file', help='本地音视频文件——走百炼临时通道上传')
    ap.add_argument('--out', required=True, help='输出前缀，产出 <out>.txt / <out>.srt / <out>.json')
    ap.add_argument('--language', default='zh', help="语种提示（默认 zh；语种混杂/不确定传空串关闭）")
    ap.add_argument('--timeout', type=int, default=180, help='单次轮询熔断秒数（实测正常任务 5–70s）')
    ap.add_argument('--ledger', default=None, help='费用台账 JSONL 路径（默认 <out 所在目录>/asr_ledger.jsonl，每次调用追加一行含 billed_seconds）')
    a = ap.parse_args()
    try:
        meta = transcribe(url=a.url, file=a.file, out=a.out, language=a.language or None, timeout=a.timeout, ledger=a.ledger)
    except Exception as exc:
        meta = {'source': a.url or a.file, 'status': 'FAILED',
                'code': type(exc).__name__, 'error': str(exc)}
    print(json.dumps(meta, ensure_ascii=False))
    if meta['status'] != 'SUCCEEDED':
        sys.exit(2)


if __name__ == '__main__':
    main()
