from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from local_tts_renderer import cli_entry, cli_render_flow, scheduler_args
from local_tts_renderer.cli_resume import ResumeCheckpointError
from local_tts_renderer.scheduler_jobs import build_jobs, build_worker_command
from test_epub_logical_chapters import make_epub


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    model = tmp_path/'model.onnx'; model.write_bytes(b'test model')
    voices = tmp_path/'voices.bin'; voices.write_bytes(b'test voices')
    monkeypatch.setattr(cli_entry, 'ensure_model_files', lambda _: (model,voices))
    monkeypatch.setattr(cli_entry, 'configure_runtime_temp_dir', lambda **kw: tmp_path)
    monkeypatch.setattr(cli_entry, 'enable_windows_espeak_fallback', lambda: None)
    monkeypatch.setattr(cli_entry, 'configure_onnx_provider', lambda **kw: 'CPUExecutionProvider')
    session = SimpleNamespace(get_providers=lambda: ['CPUExecutionProvider'])
    monkeypatch.setattr(cli_entry, 'get_kokoro_class', lambda: lambda *args: SimpleNamespace(sess=session))
    monkeypatch.setattr(cli_entry, 'get_onnxruntime', lambda: SimpleNamespace(get_available_providers=lambda: ['CPUExecutionProvider']))
    synthesized = []
    def fake_audio(**kw):
        synthesized.append(kw['text'])
        return [np.zeros(2400,dtype=np.float32)],24000
    monkeypatch.setattr(cli_render_flow,'CREATE_AUDIO_WITH_RETRY',fake_audio)
    return synthesized


def source_and_args(tmp_path, monkeypatch):
    source=tmp_path/'book.epub'
    doc=make_epub(source,{'all.xhtml':'<h1>Chapter</h1>'+''.join(f'<p>Paragraph {i} begins. Another complete sentence follows.</p>' for i in range(15))})
    monkeypatch.setattr(sys,'argv',['batch','--input',str(source),'--out',str(tmp_path/'out'),
                                  '--job-max-chars','190','--warmup-text','','--heartbeat-seconds','0',
                                  '--max-chars','40','--max-phoneme-chars','40','--max-part-minutes','10'])
    args=scheduler_args.parse_args()
    return source,doc,args


def execute(job, source, args, cache, monkeypatch):
    command=build_worker_command(Path(sys.executable),Path('unused'),args,source,job,40,cache)
    monkeypatch.setattr(sys,'argv',['render',*command[4:]])
    return cli_entry.main()


def test_segment_render_out_of_order_then_skip_complete(tmp_path, monkeypatch, runtime):
    source,doc,args=source_and_args(tmp_path,monkeypatch)
    jobs,_,cache=build_jobs([source],Path(args.output_dir),False,job_max_chars=190,max_chars=40,max_phoneme_chars=40)
    assert len(jobs)>2 and len(doc.chapters)==1
    for job in reversed(jobs):
        assert execute(job,source,args,cache[source],monkeypatch)==0
    texts=[]
    artifact_paths=[]
    for job in jobs:
        manifest=json.loads((Path(args.output_dir)/job.output_subdir/(job.output_name+'.json')).read_text())
        assert {chunk['chapter'] for chunk in manifest['chunks']}=={'Chapter'}
        texts.extend(chunk['text'] for chunk in manifest['chunks'])
        artifact_paths.extend(part['mp3_path'] for part in manifest['parts'])
    assert ' '.join(' '.join(texts).split())==' '.join(doc.chapters[0].text.split())
    assert len(set(artifact_paths))==len(artifact_paths)
    assert all(Path(path).stat().st_size>0 for path in artifact_paths)
    again,skipped,_=build_jobs([source],Path(args.output_dir),False,job_max_chars=190,max_chars=40,max_phoneme_chars=40)
    assert again==[] and len(skipped)==len(jobs)


def test_segment_interrupt_keeps_other_outputs_and_resumes_exactly(tmp_path, monkeypatch, runtime):
    source,doc,args=source_and_args(tmp_path,monkeypatch)
    output=Path(args.output_dir)
    jobs,_,cache=build_jobs([source],output,False,job_max_chars=190,max_chars=40,max_phoneme_chars=40)
    assert execute(jobs[1],source,args,cache[source],monkeypatch)==0
    sibling=output/jobs[1].output_subdir/(jobs[1].output_name+'.json')
    sibling_bytes=sibling.read_bytes()
    original_write=cli_render_flow.OutputPartWriter.write_audio
    def interrupt_after_chunk(self,audio):
        original_write(self,audio)
        cli_render_flow.request_termination()
    monkeypatch.setattr(cli_render_flow.OutputPartWriter,'write_audio',interrupt_after_chunk)
    before=len(runtime)
    with pytest.raises(KeyboardInterrupt):
        execute(jobs[0],source,args,cache[source],monkeypatch)
    checkpoint=output/jobs[0].output_subdir/(jobs[0].output_name+'.resume.json')
    assert json.loads(checkpoint.read_text())['completed_chunks']==1
    assert sibling.read_bytes()==sibling_bytes
    pending,skipped,_=build_jobs([source],output,False,job_max_chars=190,max_chars=40,max_phoneme_chars=40)
    resumed=next(job for job in pending if job.segment_index==1)
    assert (resumed.text_start,resumed.text_end,resumed.render_max_chars)==(jobs[0].text_start,jobs[0].text_end,40)
    assert len(skipped)==1
    checkpoint_bytes=checkpoint.read_bytes()
    with pytest.raises(ResumeCheckpointError,match='changed'):
        execute(replace(resumed,text_start=resumed.text_start+1),source,args,cache[source],monkeypatch)
    assert checkpoint.read_bytes()==checkpoint_bytes
    monkeypatch.setattr(cli_render_flow.OutputPartWriter,'write_audio',original_write)
    assert execute(resumed,source,args,cache[source],monkeypatch)==0
    assert not checkpoint.exists()
    selected=doc.chapters[0].text[jobs[0].text_start:jobs[0].text_end]
    assert ' '.join(' '.join(runtime[before:]).split())==' '.join(selected.split())
    assert sibling.read_bytes()==sibling_bytes
