import json
import os
import subprocess
import pytest
import researchctl
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
UNKNOWN = {'task_status': 'UNKNOWN'}


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


def _authorize(tmp_path, max_seconds=3600):
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({
        'asr_authorization': {
            'authorized': True,
            'max_hours': max_seconds / 3600,
            'max_cost_cny': 100,
        }
    }))
    return str(path)


def _safe(tmp_path, **extra):
    return {'manifest': _authorize(tmp_path), 'estimated_seconds': 60, **extra}


def test_direct_success(tmp_path):
    fake = FakeApi({'t1': [OK_STATE]})
    out = str(tmp_path / 'v')
    meta = T.transcribe(url='https://cdn/x.mp4', out=out, api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep, **_safe(tmp_path))
    assert meta['status'] == 'SUCCEEDED' and meta['path_used'] == 'direct'
    assert meta['billed_seconds'] == 41 and meta['sentences'] == 2
    assert (tmp_path / 'v.txt').read_text() == '欢迎使用阿里云。第二句。'
    assert '-->' in (tmp_path / 'v.srt').read_text()
    assert json.loads((tmp_path / 'v.json').read_text())['transcripts']
    assert ((tmp_path / 'v.txt').stat().st_mode & 0o777) == 0o600


def test_parameters_always_present(tmp_path):
    # 官方文档：parameters 省略会"提交成功但识别失败"——必须始终带上（空也要 {}）
    fake = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out=str(tmp_path / 'a'), language=None,
                 api_fn=fake, fetch=lambda u: DATA, sleep=_noop_sleep,
                 **_safe(tmp_path, media_fingerprint='a'))
    body, _ = fake.submits[0]
    assert body['parameters'] == {}
    fake2 = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out=str(tmp_path / 'b'), language='zh',
                 api_fn=fake2, fetch=lambda u: DATA, sleep=_noop_sleep,
                 **_safe(tmp_path, media_fingerprint='b'))
    assert fake2.submits[0][0]['parameters'] == {'language_hints': ['zh']}


def test_fast_fail_falls_back_to_download_upload(tmp_path):
    fake = FakeApi({'t1': [FAIL_403], 't2': [OK_STATE]})
    dl_calls = []
    meta = T.transcribe(url='https://cdn/expired.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep,
                        dl=lambda url, dest: dl_calls.append(url) or dest,
                        up=lambda path, api_fn: 'oss://tmp/expired.mp4', **_safe(tmp_path))
    assert meta['status'] == 'SUCCEEDED' and meta['path_used'] == 'fallback_download_upload'
    assert dl_calls == ['https://cdn/expired.mp4']
    # 降级后走 oss://，必须带资源解析头
    body2, hdr2 = fake.submits[1]
    assert body2['input']['file_urls'][0].startswith('oss://')
    assert hdr2.get('X-DashScope-OssResourceResolve') == 'enable'


def test_slow_link_timeout_never_starts_second_bill(tmp_path):
    # t1 永远 RUNNING：任务未终结，必须停住并保留预留，不能启动 t2。
    fake = FakeApi({'t1': [RUNNING], 't2': [OK_STATE]})
    meta = T.transcribe(url='https://cdn/slow.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep, timeout=0,
                        dl=lambda url, dest: pytest.fail('non-terminal timeout must not download'),
                        up=lambda path, api_fn: pytest.fail('non-terminal timeout must not upload'),
                        **_safe(tmp_path))
    assert meta['status'] == 'FAILED' and meta['code'] == 'TIMEOUT_NON_TERMINAL'
    assert len(fake.submits) == 1
    events = [json.loads(x) for x in (tmp_path / 'asr_ledger.jsonl').read_text().splitlines()]
    assert events[-1]['status'] == 'TIMEOUT_NON_TERMINAL'


def test_unknown_task_status_never_starts_fallback(tmp_path):
    fake = FakeApi({'t1': [UNKNOWN], 't2': [OK_STATE]})
    meta = T.transcribe(url='https://cdn/unknown.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        dl=lambda *_: pytest.fail('unknown task must not fall back'),
                        up=lambda *_: pytest.fail('unknown task must not fall back'),
                        sleep=_noop_sleep, **_safe(tmp_path))
    assert meta['status'] == 'FAILED' and meta['terminal'] is False
    assert len(fake.submits) == 1


def test_rerun_resumes_timed_out_task_without_resubmitting(tmp_path):
    fake = FakeApi({'t1': [RUNNING, OK_STATE]})
    common = dict(url='https://cdn/resume.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                  fetch=lambda u: DATA, sleep=_noop_sleep, timeout=0, **_safe(tmp_path))
    first = T.transcribe(**common)
    second = T.transcribe(**common)
    assert first['code'] == 'TIMEOUT_NON_TERMINAL'
    assert second['status'] == 'SUCCEEDED'
    assert len(fake.submits) == 1


def test_ledger_appended_on_success_and_failure(tmp_path):
    # 成功：默认台账落在 out 所在目录，记录 billed_seconds 供对账 asr_authorization
    fake = FakeApi({'t1': [OK_STATE]})
    T.transcribe(url='https://cdn/x.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                 fetch=lambda u: DATA, sleep=_noop_sleep, **_safe(tmp_path))
    lines = (tmp_path / 'asr_ledger.jsonl').read_text().strip().splitlines()
    assert len(lines) >= 4  # reserve, submit, terminal, output
    rec = next(row for row in reversed([json.loads(line) for line in lines])
               if row.get('event') == 'TERMINAL')
    assert rec['status'] == 'SUCCEEDED' and rec['billed_seconds'] == 41 and rec['ts']
    # 失败：同样追加（billed 无值），指定 --ledger 路径生效
    fake2 = FakeApi({'t1': [FAIL_403]})
    ledger2 = tmp_path / 'custom_ledger.jsonl'
    T.transcribe(file='/tmp/local.mp4', out=str(tmp_path / 'v2'), api_fn=fake2,
                 fetch=lambda u: DATA, sleep=_noop_sleep,
                 up=lambda path, api_fn: 'oss://tmp/local.mp4', ledger=str(ledger2),
                 **_safe(tmp_path, media_fingerprint='local-v2'))
    rec2 = json.loads(ledger2.read_text().strip().splitlines()[-1])
    assert rec2['status'] == 'FAILED' and rec2['code'] == 'FILE_403_FORBIDDEN'


def test_file_upload_failure_no_fallback_loop(tmp_path):
    fake = FakeApi({'t1': [FAIL_403]})
    meta = T.transcribe(file='/tmp/local.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                        fetch=lambda u: DATA, sleep=_noop_sleep,
                        up=lambda path, api_fn: 'oss://tmp/local.mp4',
                        **_safe(tmp_path, media_fingerprint='local'))
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


def test_signed_url_query_is_idempotent(tmp_path):
    fake = FakeApi({'t1': [OK_STATE]})
    common = dict(api_fn=fake, fetch=lambda u: DATA, sleep=_noop_sleep,
                  manifest=_authorize(tmp_path), estimated_seconds=60)
    first = T.transcribe(url='https://cdn.example/video/abc.mp4?sig=old',
                         out=str(tmp_path / 'first'), **common)
    second = T.transcribe(url='https://cdn.example/video/abc.mp4?sig=new',
                          out=str(tmp_path / 'second'), **common)
    assert first['status'] == second['status'] == 'SUCCEEDED'
    assert second['reused'] is True
    assert len(fake.submits) == 1


def test_budget_exceed_rejected_before_submit(tmp_path):
    fake = FakeApi({'t1': [OK_STATE]})
    with pytest.raises(T.BudgetError):
        T.transcribe(url='https://cdn.example/v.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                     fetch=lambda u: DATA, sleep=_noop_sleep,
                     manifest=_authorize(tmp_path, max_seconds=10), estimated_seconds=11)
    assert fake.submits == []


def test_unauthorized_manifest_rejected_before_submit(tmp_path):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'asr_authorization': {
        'authorized': False, 'max_hours': 0, 'max_cost_cny': 0}}))
    fake = FakeApi({'t1': [OK_STATE]})
    with pytest.raises(T.AuthorizationError):
        T.transcribe(url='https://cdn.example/v.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                     manifest=str(manifest), estimated_seconds=10)
    assert fake.submits == []


def test_ledger_failure_is_fail_closed(monkeypatch, tmp_path):
    fake = FakeApi({'t1': [OK_STATE]})
    def fail_write(_path, _row):
        raise T.LedgerError('disk full')
    monkeypatch.setattr(T, '_append_unlocked', fail_write)
    with pytest.raises(T.LedgerError):
        T.transcribe(url='https://cdn.example/v.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                     manifest=_authorize(tmp_path), estimated_seconds=10)
    assert fake.submits == []


def test_fallback_temp_file_is_removed(tmp_path):
    fake = FakeApi({'t1': [FAIL_403], 't2': [OK_STATE]})
    seen = []
    def fake_download(_url, dest):
        seen.append(dest)
        with open(dest, 'wb') as f:
            f.write(b'media')
        return dest
    T.transcribe(url='https://cdn.example/expired.mp4', out=str(tmp_path / 'v'), api_fn=fake,
                 fetch=lambda u: DATA, sleep=_noop_sleep, dl=fake_download,
                 up=lambda path, api_fn: 'oss://tmp/media', **_safe(tmp_path))
    assert seen and not os.path.exists(seen[0])


def _v3_state(tmp_path):
    db = tmp_path / "research.db"
    researchctl.init_database(db, {
        "run_id": "run-asr", "objective": "transcribe evidence", "profile": "technical",
    })
    authorization = researchctl.record_event(db, {
        "event_type": "user.asr-authorization", "actor": "user",
        "verbatim": "Authorize at most 100 seconds and 10 CNY of ASR.",
    })
    researchctl.authorize_budget(db, {
        "asr_seconds_limit": 100, "asr_cost_limit": 10,
        "user_authorization_event_id": authorization["seq"],
    })
    scope = researchctl.record_event(db, {
        "event_type": "user.search-scope-approval", "actor": "user",
        "verbatim": "Approve the eight-entry transcription research scope.",
    })
    researchctl.set_plan(db, {
        "plan_version": 3, "profile": "technical", "risk_overlays": [],
        "dimensions": ["quality", "cost"],
        "source_requirements": ["official", "independent-test"],
        "estimates": {"p50_minutes": 5, "p90_minutes": 15,
                      "basis": ["test fixture declared caps"]},
        "budgets": {"wall_minutes": 30, "asr_seconds": 100,
                    "asr_cost_cny": 10, "account_actions": False},
        "scope_approval_event_id": scope["seq"],
        "budget_authorization_event_id": authorization["seq"],
        "account_authorization_event_id": None,
        "channels": [{
            "name": name, "signals": ["transcript"],
            "probe": {"queries": ["transcription evidence"], "limit_per_query": 1},
        } for name in sorted(researchctl.KNOWN_CHANNELS)],
        "deepening": [],
    })
    finding = researchctl.upsert_finding(db, {
        "channel": "youtube", "source_url": "https://example.test/watch/1",
        "media_url": "https://cdn.example/video/1.mp4?sig=first",
        "title": "Video", "headline": "Evidence video", "note": "Full speech is required.",
    })
    return db, finding


def test_v3_budget_broker_reserves_settles_and_deduplicates(tmp_path):
    db, finding = _v3_state(tmp_path)
    fake = FakeApi({'t1': [OK_STATE]})
    common = dict(
        api_fn=fake, fetch=lambda _url: DATA, sleep=_noop_sleep,
        db=str(db), finding_id=finding["id"], estimated_seconds=60,
    )
    first = T.transcribe(url='https://cdn.example/video/1.mp4?sig=old',
                         out=str(tmp_path / 'first'), **common)
    second = T.transcribe(url='https://cdn.example/video/1.mp4?sig=rotated',
                          out=str(tmp_path / 'second'), **common)
    assert first['status'] == second['status'] == 'SUCCEEDED'
    assert second['reused'] is True and len(fake.submits) == 1
    budget = researchctl.status(db)['gate']['budget']
    assert budget['asr_seconds_spent'] == 41
    assert budget['asr_seconds_reserved'] == 0
    assert researchctl.status(db)['counts']['attempts'] == 1


def test_v3_timeout_holds_canonical_reservation(tmp_path):
    db, finding = _v3_state(tmp_path)
    fake = FakeApi({'t1': [RUNNING]})
    meta = T.transcribe(
        url='https://cdn.example/video/1.mp4', out=str(tmp_path / 'timeout'),
        api_fn=fake, sleep=_noop_sleep, timeout=0, db=str(db),
        finding_id=finding['id'], estimated_seconds=60,
    )
    assert meta['code'] == 'TIMEOUT_NON_TERMINAL'
    state = researchctl.status(db)
    assert state['gate']['checks']['budget_sane'] is False
    assert state['gate']['budget']['asr_seconds_reserved'] == 60
