import json
import os
import subprocess
import transcribe as T

SENTS = [{'begin_time': 0, 'end_time': 1440, 'text': '欢迎使用阿里云。'},
         {'begin_time': 90061000, 'end_time': 90062500, 'text': '第二句。'}]
DATA = {'transcripts': [{'text': '欢迎使用阿里云。第二句。', 'sentences': SENTS}]}
OK_STATE = {'task_status': 'SUCCEEDED',
            'results': [{'transcription_url': 'http://x/r.json', 'subtask_status': 'SUCCEEDED',
                         'usage': {'duration': 41}}]}
FAIL_403 = {'task_status': 'SUCCEEDED',
            'results': [{'subtask_status': 'FAILED', 'code': 'FILE_403_FORBIDDEN'}]}
RUNNING = {'task_status': 'RUNNING'}


def test_srt_ts():
    assert T.srt_ts(0) == '00:00:00,000'
    assert T.srt_ts(1440) == '00:00:01,440'
    assert T.srt_ts(90061000) == '25:01:01,000'  # 超长音频的小时位不溢出


def test_build_srt():
    s = T.build_srt(SENTS)
    assert s.startswith('1\n00:00:00,000 --> 00:00:01,440\n欢迎使用阿里云。\n')
    assert '\n2\n25:01:01,000 --> 25:01:02,500\n第二句。' in s


class FakeApi:
    """按任务 id 回放状态序列；记录所有提交，供断言请求体/请求头。"""
    def __init__(self, states_per_task):
        self.states = states_per_task  # {'t1': [state, ...], 't2': [...]}
        self.submits = []              # [(body, headers)]

    def __call__(self, path, method='GET', body=None, headers=None):
        if path.startswith('/services/audio/asr/transcription'):
            self.submits.append((body, headers))
            return {'output': {'task_id': f't{len(self.submits)}', 'task_status': 'PENDING'}}
        if path.startswith('/tasks/'):
            seq = self.states[path.split('/')[-1]]
            return {'output': seq.pop(0) if len(seq) > 1 else seq[0]}
        raise AssertionError(f'意外调用 {path}')


def _noop_sleep(_):
    pass


def test_direct_success(tmp_path):
    fake = FakeApi({'t1': [OK_STATE]})
    out = str(tmp_path / 'v')
    meta = T.transcribe(url='https://cdn/x.mp4', out=out, api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep)
    assert meta['status'] == 'SUCCEEDED' and meta['path_used'] == 'direct'
    assert meta['billed_seconds'] == 41 and meta['sentences'] == 2
    assert (tmp_path / 'v.txt').read_text() == '欢迎使用阿里云。第二句。'
    assert '-->' in (tmp_path / 'v.srt').read_text()
    assert json.loads((tmp_path / 'v.json').read_text())['transcripts']


def test_parameters_always_present():
    # 官方文档：parameters 省略会"提交成功但识别失败"——必须始终带上（空也要 {}）
    fake = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out='/tmp/_tp', language=None,
                 api_fn=fake, fetch=lambda u: DATA, sleep=_noop_sleep)
    body, _ = fake.submits[0]
    assert body['parameters'] == {}
    fake2 = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out='/tmp/_tp', language='zh',
                 api_fn=fake2, fetch=lambda u: DATA, sleep=_noop_sleep)
    assert fake2.submits[0][0]['parameters'] == {'language_hints': ['zh']}


def test_fast_fail_falls_back_to_download_upload(tmp_path):
    fake = FakeApi({'t1': [FAIL_403], 't2': [OK_STATE]})
    dl_calls = []
    meta = T.transcribe(url='https://cdn/expired.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep,
                        dl=lambda url, dest: dl_calls.append(url) or dest,
                        up=lambda path, api_fn: 'oss://tmp/expired.mp4')
    assert meta['status'] == 'SUCCEEDED' and meta['path_used'] == 'fallback_download_upload'
    assert dl_calls == ['https://cdn/expired.mp4']
    # 降级后走 oss://，必须带资源解析头
    body2, hdr2 = fake.submits[1]
    assert body2['input']['file_urls'][0].startswith('oss://')
    assert hdr2.get('X-DashScope-OssResourceResolve') == 'enable'


def test_slow_link_times_out_then_falls_back(tmp_path):
    # t1 永远 RUNNING（慢链），timeout=0 → 至少查一次后熔断 → 降级成功
    fake = FakeApi({'t1': [RUNNING], 't2': [OK_STATE]})
    meta = T.transcribe(url='https://cdn/slow.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep, timeout=0,
                        dl=lambda url, dest: dest, up=lambda path, api_fn: 'oss://tmp/slow.mp4')
    assert meta['status'] == 'SUCCEEDED' and meta['path_used'] == 'fallback_download_upload'


def test_ledger_appended_on_success_and_failure(tmp_path):
    # 成功：默认台账落在 out 所在目录，记录 billed_seconds 供对账 asr_authorization
    fake = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                 fetch=lambda u: DATA, sleep=_noop_sleep)
    lines = (tmp_path / 'asr_ledger.jsonl').read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec['status'] == 'SUCCEEDED' and rec['billed_seconds'] == 41 and rec['ts']
    # 失败：同样追加（billed 无值），指定 --ledger 路径生效
    fake2 = FakeApi({'t1': [FAIL_403]})
    ledger2 = tmp_path / 'custom_ledger.jsonl'
    T.transcribe(file='/tmp/local.mp4', out=str(tmp_path / 'v2'), api_fn=fake2,
                 fetch=lambda u: DATA, sleep=_noop_sleep,
                 up=lambda path, api_fn: 'oss://tmp/local.mp4', ledger=str(ledger2))
    rec2 = json.loads(ledger2.read_text().strip())
    assert rec2['status'] == 'FAILED' and rec2['code'] == 'FILE_403_FORBIDDEN'


def test_file_upload_failure_no_fallback_loop(tmp_path):
    fake = FakeApi({'t1': [FAIL_403]})
    meta = T.transcribe(file='/tmp/local.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep,
                        up=lambda path, api_fn: 'oss://tmp/local.mp4')
    assert meta['status'] == 'FAILED' and meta['code'] == 'FILE_403_FORBIDDEN'
    assert len(fake.submits) == 1  # 本地文件路径不再降级，避免死循环


def test_pick_handles_nested_result_shape():
    # 真实返回里 transcription_url 可能嵌在 results[0].output 下（2026-07-13 实测形态）
    nested = {'task_status': 'SUCCEEDED',
              'results': [{'output': {'transcription_url': 'http://x/r.json',
                                      'subtask_status': 'SUCCEEDED'}, 'usage': {'duration': 9}}]}
    ok, payload = T.pick(nested)
    assert ok and payload['transcription_url'] == 'http://x/r.json' and payload['billed_seconds'] == 9


def test_pick_timeout_and_task_failed():
    assert T.pick(None) == (False, 'TIMEOUT')
    ok, code = T.pick({'task_status': 'FAILED', 'code': 'FILE_403_FORBIDDEN'})
    assert not ok and code == 'FILE_403_FORBIDDEN'


def test_download_rejects_http_failure(tmp_path):
    dest = tmp_path / 'bad.mp4'
    dest.write_text('error page')

    def runner(_cmd):
        return subprocess.CompletedProcess(_cmd, 22)

    try:
        T.download('https://cdn/missing.mp4', str(dest), runner=runner)
        assert False, '应拒绝 HTTP 失败'
    except RuntimeError:
        pass
    assert not dest.exists()


def test_temp_upload_rejects_oversize(monkeypatch, tmp_path):
    path = tmp_path / 'huge.mp4'
    path.write_bytes(b'x')
    monkeypatch.setattr(os.path, 'getsize', lambda _p: 1024 * 1024 * 1024 + 1)
    try:
        T.temp_upload(str(path), api_fn=lambda *_a, **_k: {})
        assert False, '应拒绝超过 1GB 的文件'
    except RuntimeError as exc:
        assert '1GB' in str(exc)
