import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import './layout.css';

const paths = {
  play: 'm9 5 11 7-11 7Z',
  upload: 'M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5',
  search: 'm21 21-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4',
  clock: 'M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  check: 'm5 12 4 4L19 6', file: 'M14 2H5v20h14V7Zm0 0v6h5M8 12h8m-8 4h6',
  loop: 'M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5',
  arrow: 'M5 12h14m-5-5 5 5-5 5', close: 'm6 6 12 12M6 18 18 6',
  layers: 'm12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5',
  tool: 'm14 6 4 4m-3-7a6 6 0 0 0-7 7L2 16v6h6l6-6a6 6 0 0 0 7-7l-5 5-6-6Z',
};
function Icon({ name, size = 18, ...props }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}><path d={paths[name] || paths.file} /></svg>;
}
function stamp(seconds = 0) {
  const value = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}
async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('；') : '请求失败，请重试');
  return data;
}
function HighlightedSummary({ chapter, onAnchor }) {
  const anchors = [...chapter.anchors].sort((first, second) => second.label.length - first.label.length);
  const pieces = [];
  let cursor = 0;
  while (cursor < chapter.summary.length) {
    let chosen = null;
    let next = chapter.summary.length;
    for (const anchor of anchors) {
      const index = chapter.summary.indexOf(anchor.label, cursor);
      if (index >= 0 && index < next) { next = index; chosen = anchor; }
    }
    if (!chosen) { pieces.push(chapter.summary.slice(cursor)); break; }
    if (next > cursor) pieces.push(chapter.summary.slice(cursor, next));
    pieces.push(<button className="anchor" key={`${next}-${chosen.label}`} onClick={() => onAnchor(chosen)} title="查看原文并跳转">{chosen.label}<span>↗</span></button>);
    cursor = next + chosen.label.length;
  }
  return <p className="chapter-summary">{pieces}</p>;
}

function App() {
  const [health, setHealth] = useState(null);
  const [video, setVideo] = useState(null);
  const [library, setLibrary] = useState([]);
  const [task, setTask] = useState(null);
  const [starting, setStarting] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState(null);
  const [searching, setSearching] = useState(false);
  const [tab, setTab] = useState('summary');
  const [evidence, setEvidence] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [modal, setModal] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadFile, setUploadFile] = useState(null);
  const [subtitleFile, setSubtitleFile] = useState(null);
  const [toast, setToast] = useState('');
  const player = useRef(null);
  const subtitleInput = useRef(null);
  const selectedId = useRef(null);
  const searchVersion = useRef(0);
  const busy = starting || ['queued', 'running'].includes(task?.status);
  const events = task?.events || [];
  const failedChecks = events.filter(item => item.kind === 'validation' && item.data?.passed === false).length;
  const actions = events.filter(item => item.kind === 'action').length;
  const summary = video?.summary;
  const workflowIndex = !video ? 0 : !video.segments.length ? 1 : !summary ? 2 : 3;
  const transcribing = busy && task?.mode === 'transcribe';
  const retrieving = busy && task?.mode === 'search';
  const progressEvent = [...events].reverse().find(item => ['progress', 'decision', 'done', 'error'].includes(item.kind));

  useEffect(() => {
    api('/api/health').then(setHealth).catch(error => setToast(error.message));
    api('/api/videos').then(setLibrary).catch(error => setToast(error.message));
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timeout = setTimeout(() => setToast(''), 6500);
    return () => clearTimeout(timeout);
  }, [toast]);
  useEffect(() => {
    if (!task || !['queued', 'running'].includes(task.status)) return;
    let stopped = false;
    let timer;
    const taskId = task.id;
    const videoId = selectedId.current;
    async function poll() {
      try {
        const next = await api(`/api/tasks/${taskId}`);
        if (stopped || selectedId.current !== videoId) return;
        setTask(next);
        if (['completed', 'failed'].includes(next.status)) {
          const updated = await api(`/api/videos/${videoId}`);
          if (!stopped && selectedId.current === videoId) {
            setVideo(updated);
            if (next.mode === 'search') {
              setSearching(false);
              setSearch(next.status === 'completed' ? next.result : null);
            }
            if (next.status === 'failed') setToast(next.error);
          }
        } else timer = setTimeout(poll, 500);
      } catch (error) {
        if (!stopped) { setToast(error.message); timer = setTimeout(poll, 1800); }
      }
    }
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [task?.id]);

  async function selectVideo(next) {
    selectedId.current = next.id;
    searchVersion.current += 1;
    const latest = next.latest_search && (!next.latest_task || next.latest_search.created > next.latest_task.created) ? next.latest_search : next.latest_task;
    const searchResult = next.latest_search?.status === 'completed' ? next.latest_search.result : null;
    setVideo(next); setTask(latest || null);
    setSearch(searchResult); setQuery(searchResult?.query || '');
    setSearching(latest?.mode === 'search' && ['queued', 'running'].includes(latest.status));
    setEvidence(null); setCurrentTime(0); setTab('summary');
    setLibrary(await api('/api/videos'));
  }
  async function switchVideo(id) {
    try { await selectVideo(await api(`/api/videos/${id}`)); }
    catch (error) { setToast(error.message); }
  }
  function seek(seconds) {
    if (!player.current) return;
    const target = Math.max(0, Math.min(seconds, video.duration));
    player.current.currentTime = target;
    setCurrentTime(target);
    player.current.play().catch(error => { if (error.name !== 'AbortError') setToast('已定位，点击播放器即可播放。'); });
  }
  function openAnchor(anchor) { setEvidence(anchor); seek(anchor.start); }
  async function runAgent() {
    if (!video || busy) return;
    setStarting(true); setEvidence(null); setExpanded(true);
    const id = video.id;
    try {
      const response = await api(`/api/videos/${id}/analyze`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({})});
      const next = await api(`/api/tasks/${response.task_id}`);
      if (selectedId.current === id) {
        setTask(next);
        if (['completed', 'failed'].includes(next.status)) {
          const updated = await api(`/api/videos/${id}`);
          if (selectedId.current === id) setVideo(updated);
          if (next.error) setToast(next.error);
        }
      }
    } catch (error) { setToast(error.message); }
    finally { setStarting(false); }
  }
  async function prepareTranscript(id = video?.id) {
    if (!id) return;
    setStarting(true); setExpanded(true);
    try {
      const response = await api(`/api/videos/${id}/transcribe`, {method: 'POST'});
      const next = await api(`/api/tasks/${response.task_id}`);
      if (selectedId.current === id) {
        setTask(next);
        if (['completed', 'failed'].includes(next.status)) {
          const updated = await api(`/api/videos/${id}`);
          if (selectedId.current === id) setVideo(updated);
          if (next.error) setToast(next.error);
        }
      }
    } catch (error) { setToast(error.message); }
    finally { setStarting(false); }
  }
  async function performSearch(value = query) {
    if (!value.trim() || !video || busy || !health?.llm?.configured) return;
    const version = ++searchVersion.current;
    const id = video.id;
    setQuery(value); setSearching(true); setStarting(true); setSearch(null); setExpanded(true);
    try {
      const response = await api(`/api/videos/${id}/search`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query: value.trim()})});
      const next = await api(`/api/tasks/${response.task_id}`);
      if (version === searchVersion.current && id === selectedId.current) {
        setTask(next);
        if (['completed', 'failed'].includes(next.status)) {
          setSearching(false);
          if (next.status === 'completed') setSearch(next.result);
          else setToast(next.error);
        }
      }
    } catch (error) {
      if (version === searchVersion.current) { setSearching(false); setToast(error.message); }
    } finally { setStarting(false); }
  }
  async function upload(event) {
    event.preventDefault();
    if (!uploadFile) return;
    setUploading(true);
    const form = new FormData(); form.append('video', uploadFile);
    if (subtitleFile) form.append('subtitles', subtitleFile);
    try {
      const next = await api('/api/videos', {method: 'POST', body: form});
      await selectVideo(next); setModal(false); setUploadFile(null); setSubtitleFile(null);
      if (next.segments.length) setToast('视频和字幕已导入，可以检索或生成总结。');
      else { setToast('视频已上传，正在自动转写；完成后可生成总结和智能检索。'); await prepareTranscript(next.id); }
    } catch (error) { setToast(error.message); }
    finally { setUploading(false); }
  }
  async function importSubtitles(event) {
    const file = event.target.files?.[0];
    if (!file || !video) return;
    const id = video.id;
    const form = new FormData(); form.append('subtitles', file);
    try {
      const next = await api(`/api/videos/${id}/subtitles`, {method: 'POST', body: form});
      if (selectedId.current === id) { await selectVideo(next); setToast('字幕已更新，请重新生成总结。'); }
    } catch (error) { setToast(error.message); }
    event.target.value = '';
  }

  return <div className="app-shell">
    <aside className="rail"><a className="brand-symbol" href="#" aria-label="Frame 首页">f<span>.</span></a><div className="rail-divider" /><button className="rail-button selected" aria-label="视频工作台"><Icon name="layers" size={22} /></button><button className="rail-button" onClick={() => document.getElementById('agent-trace').scrollIntoView({behavior: 'smooth'})} aria-label="Agent 执行记录"><Icon name="loop" size={22} /></button><span className="rail-bottom">LAB B</span></aside>
    <main>
      <header className="topbar"><div className="wordmark">frame<span> / 视频内容分析 Agent</span></div><div className="topbar-right"><span className="availability"><i />本地工作台</span><span className="avatar">B</span></div></header>
      <div className="workspace">
        <section className="page-heading"><h1>视频工作台</h1>{library.length > 0 && <div className="history-picker"><Icon name="clock" size={16} /><select aria-label="切换已上传视频" value={video?.id || ''} onChange={event => event.target.value && switchVideo(event.target.value)} disabled={busy}><option value="" disabled>打开历史视频</option>{library.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>}</section>
        <nav className="workflow-steps" aria-label="处理流程">{['上传视频', '自动语音转写', 'Agent 总结与校验', '检索与点击回看'].map((label, index) => <div className={`workflow-step ${index < workflowIndex ? 'complete' : index === workflowIndex ? 'current' : ''}`} aria-current={index === workflowIndex ? 'step' : undefined} key={label}><span className="step-number">{index < workflowIndex ? <Icon name="check" size={15} /> : String(index + 1).padStart(2, '0')}</span><span className="step-label">{label}</span></div>)}</nav>
        <div className="studio-grid">
          <section className="left-column">
            <div className={`card video-card ${!video ? 'awaiting-video' : ''}`}>
              <div className="card-heading"><span className="section-title"><span className="small-icon"><Icon name="play" size={15} /></span>视频工作区</span><button className="button dark import-button" onClick={() => setModal(true)} disabled={busy}><Icon name="upload" size={15} />导入视频</button></div>
              <div className="player-wrap">{video ? <video key={video.id} ref={player} src={video.media_url} controls preload="metadata" onTimeUpdate={event => setCurrentTime(event.target.currentTime)} onError={() => setToast('浏览器无法播放此编码，建议使用 H.264 编码的 MP4。')} /> : <div className="video-placeholder"><div className="upload-illustration"><Icon name="upload" size={28} /></div><h3>从上传一段视频开始</h3><p>自动识别语音，保留时间戳与原文证据</p><button className="button primary" onClick={() => setModal(true)}>选择本地视频<Icon name="arrow" size={15} /></button></div>}</div>
              {video && <div className={`processing-banner ${busy ? 'active' : ''}`} role="status"><Icon name={busy ? 'loop' : video.segments.length ? 'check' : 'file'} className={busy ? 'spinning' : ''} size={16} /><div><strong>{busy ? progressEvent?.title || '正在准备任务' : video.segments.length ? `转写就绪 · ${video.segments.length} 个片段` : '等待语音转写'}</strong><span>{busy ? progressEvent?.detail : video.segments.length ? '可立即智能检索，点击右侧按钮生成总结。' : '点击开始转写，或导入已有字幕。'}</span></div>{!busy && !video.segments.length && <button className="button secondary" onClick={() => prepareTranscript()}>开始转写</button>}</div>}
              <div className="video-meta"><div><span className="tiny-label">CURRENT VIDEO</span><h2>{video?.name || '等待导入视频'}</h2></div><span className="source-badge">{video?.source === 'subtitle' ? '已导入字幕' : video?.source === 'asr' ? '语音识别转写' : '等待转写'}</span></div>
              <div className="video-footer"><span><Icon name="clock" size={14} />{stamp(currentTime)} / {stamp(video?.duration)}</span><button className="text-button" onClick={() => subtitleInput.current.click()} disabled={!video || busy}><Icon name="file" size={14} />导入字幕</button><input className="hidden-input" type="file" ref={subtitleInput} accept=".srt,.vtt" onChange={importSubtitles} aria-label="导入字幕" /></div>
            </div>
            <div className="card search-card">
              <div className="card-heading"><span className="section-title"><Icon name="search" />智能检索</span><span className="pill purple">语义理解</span></div>
              <form className="search-form" onSubmit={event => {event.preventDefault(); performSearch();}}><Icon name="search" /><input aria-label="检索问题" placeholder="描述想找的内容，例如：老师如何解释模型的输入和输出？" value={query} onChange={event => setQuery(event.target.value)} maxLength={500} /><button type="submit" disabled={busy || searching || !health?.llm?.configured || !video?.segments?.length || !query.trim()}>{searching ? '检索中' : '检索'}<Icon name="arrow" size={15} /></button></form>
              {searching && <div className="search-progress" role="status"><Icon name="loop" size={15} className="spinning" /><span>{retrieving ? progressEvent?.title || '正在理解问题并寻找相关内容' : '正在提交检索任务'}<small>根据内容含义检索并核对原文依据</small></span></div>}
              {search && <div className="search-results"><div className="search-answer"><Icon name="spark" size={15} /><p>{search.answer}</p></div><div className="results-count">相关片段 <strong>{search.matches.length}</strong> 处<span>已核对原文依据</span></div>{search.matches.map((match, index) => <article className="semantic-result" key={`${match.start}-${index}`}><button className="search-result" onClick={() => seek(match.start)}><span className="timestamp">{stamp(match.start)}<Icon name="play" size={11} /></span><span className="result-copy"><strong>{match.title}</strong><span className="relevance-reason">{match.reason}</span></span><Icon name="arrow" size={15} /></button><details className="search-evidence"><summary>查看原文依据</summary>{match.evidence.map(segment => <button key={segment.segment_id} onClick={() => seek(segment.start)}><span>{stamp(segment.start)}</span>{segment.text}</button>)}</details></article>)}</div>}

            </div>
          </section>
          <section className="card analysis-card">
            <div className="card-heading"><span className="section-title"><span className="small-icon purple"><Icon name="spark" size={16} /></span>Agent 内容分析</span><span className={`pill ${busy ? 'working' : 'purple'}`}>{busy ? '执行中' : summary ? '已校验' : '待分析'}</span></div>
            <div className="agent-settings"><div className="model-label"><Icon name="spark" size={15} /><span>{health?.llm?.configured ? health.llm.model : '模型尚未配置'}</span></div><button className="button primary" onClick={runAgent} disabled={!health?.llm?.configured || !video?.segments?.length || busy}><Icon name={busy ? 'loop' : 'spark'} size={16} className={busy ? 'spinning' : ''} />{busy ? transcribing ? '转写中' : retrieving ? '检索中' : '分析中' : summary ? '重新生成总结' : '生成总结'}</button></div>
            <div className="tabs" role="tablist"><button role="tab" aria-selected={tab === 'summary'} onClick={() => setTab('summary')} className={tab === 'summary' ? 'active' : ''}>章节总结{summary && <span>{summary.chapters.length}</span>}</button><button role="tab" aria-selected={tab === 'transcript'} onClick={() => setTab('transcript')} className={tab === 'transcript' ? 'active' : ''}>原始转写<span>{video?.segments?.length || 0}</span></button></div>
            <div className="analysis-content">{tab === 'summary' ? summary ? <><div className="overview"><div className="tiny-label"><Icon name="spark" size={13} />内容概览 <span>模型总结</span></div><p>{summary.overview}</p></div><div className="chapter-list">{summary.chapters.map((chapter, index) => <article className={`chapter ${currentTime >= chapter.start && currentTime < chapter.end ? 'current' : ''}`} key={index}><div className="chapter-top"><span className="chapter-number">{String(index + 1).padStart(2, '0')}</span><button className="timestamp" onClick={() => seek(chapter.start)}>{stamp(chapter.start)}<Icon name="play" size={11} /></button><h3>{chapter.title}</h3></div><HighlightedSummary chapter={chapter} onAnchor={openAnchor} /><div className="chapter-source"><Icon name="check" size={12} />{chapter.segment_ids.length} 个原文片段 · 关键词可点击回看</div></article>)}</div><div className="validation-note"><Icon name="check" size={15} />{summary.validation}</div></> : <div className="empty summary-empty"><div className="empty-orbit"><Icon name="spark" size={32} /></div><h3>{transcribing ? '正在将语音转换为文字' : '等待内容分析'}</h3><p>{!video ? '先上传视频，系统会自动识别语音。' : transcribing ? '转写完成后，即可生成总结或智能检索。' : '转写已就绪，点击上方按钮生成总结。'}</p></div> : <div className="transcript-list">{video?.segments?.length ? video.segments.map(segment => <button className={`transcript-row ${currentTime >= segment.start && currentTime < segment.end ? 'current' : ''}`} key={segment.segment_id} onClick={() => seek(segment.start)}><span className="timestamp">{stamp(segment.start)}</span><span>{segment.text}<small>{segment.segment_id}</small></span></button>) : <div className="empty">尚无转写，请导入字幕或运行语音识别。</div>}</div>}</div>
            {evidence && <div className="evidence-box"><div><span><Icon name="file" size={14} />「{evidence.label}」的原文依据</span><button className="icon-button" aria-label="关闭原文依据" onClick={() => setEvidence(null)}><Icon name="close" size={14} /></button></div>{evidence.evidence.map(segment => <button key={segment.segment_id} onClick={() => seek(segment.start)}><span>{stamp(segment.start)} · {segment.segment_id}</span>{segment.text}</button>)}</div>}
          </section>
        </div>
        <section className="card trace-card" id="agent-trace">
          <div className="trace-heading"><div><div className="section-title"><span className="small-icon purple"><Icon name="loop" size={16} /></span>Agent 执行轨迹<span className="pill neutral">ReAct + Tools + Validation</span></div><p>工具调用、校验反馈、修正动作，每一步都可查看。</p></div><div className="trace-stats"><div><strong>{String(actions).padStart(2, '0')}</strong><span>工具调用</span></div><div><strong className={failedChecks ? 'amber-text' : ''}>{String(failedChecks).padStart(2, '0')}</strong><span>校验反馈</span></div><span className={`status-dot ${busy ? 'running' : task?.status || ''}`}>{busy ? '执行中' : task?.status === 'completed' ? '已完成' : task?.status === 'failed' ? '未完成' : '等待任务'}</span></div></div>
          <div className="react-flow">{[['spark', '判断与规划', 'Reason'], ['tool', '调用工具', 'Act'], ['file', '观察结果', 'Observe'], ['check', '校验与修正', 'Validate']].map(([icon, title, english], index) => <React.Fragment key={title}><div className={busy ? 'live' : task?.status === 'completed' ? 'finished' : ''}><span className="flow-icon"><Icon name={icon} size={17} /></span><span>{title}<small>{english}</small></span></div>{index < 3 && <Icon name="arrow" size={17} />}</React.Fragment>)}</div>
          {events.length ? <><div className="trace-list">{(expanded ? events : events.slice(-5)).map(event => <div className={`trace-row ${event.kind}`} key={event.index}><span className="trace-index">{String(event.index).padStart(2, '0')}</span><span className="event-kind">{{decision:'决策',action:'调用',observation:'返回',validation:'校验',progress:'进度',done:'完成',error:'异常'}[event.kind]}</span><div><strong>{event.title}</strong>{event.kind !== 'observation' && event.detail && <p>{event.detail}</p>}{event.data && <details><summary>查看{event.kind === 'action' ? '参数' : '数据'}</summary><pre>{JSON.stringify(event.data, null, 2)}</pre></details>}</div><span className="event-time">{new Date(event.time * 1000).toLocaleTimeString('zh-CN', {hour12:false})}</span></div>)}</div>{events.length > 5 && <button className="expand-trace" onClick={() => setExpanded(!expanded)}>{expanded ? '收起执行记录 ↑' : `展开全部 ${events.length} 条记录 ↓`}</button>}</> : null}
          {task?.error && <div className="task-error">{task.error} 已有转写仍可检索。</div>}
        </section>
      </div>
    </main>
    {modal && <div className="modal-backdrop" onClick={() => !uploading && setModal(false)}><section className="modal" role="dialog" aria-modal="true" aria-labelledby="upload-title" onClick={event => event.stopPropagation()}><div className="modal-heading"><div><span className="eyebrow">YOUR NEXT VIDEO</span><h2 id="upload-title">导入视频，开始分析</h2></div><button className="icon-button" onClick={() => setModal(false)} disabled={uploading} aria-label="关闭上传窗口"><Icon name="close" /></button></div><form onSubmit={upload}><label className="upload-zone"><Icon name="upload" size={30} /><strong>{uploadFile?.name || '选择本地视频文件'}</strong><span>支持 MP4 / WebM / MOV / M4V / MKV</span><input type="file" accept=".mp4,.webm,.mov,.m4v,.mkv" required onChange={event => setUploadFile(event.target.files?.[0])} disabled={uploading} aria-label="选择视频文件" /></label><details className="optional-subtitles"><summary>已有字幕？可选导入 SRT / VTT</summary><label className="subtitle-label">配套字幕 <span>可选 · SRT / VTT · UTF-8</span><input type="file" accept=".srt,.vtt" onChange={event => setSubtitleFile(event.target.files?.[0])} disabled={uploading} aria-label="选择配套字幕" /></label></details><p className="upload-note">{health?.asr_ready ? '语音模型已就绪。上传后自动在本机转写，无需提供字幕。' : health?.asr_installed ? '上传后自动转写。首次使用需要下载语音模型，建议演示前预先准备。' : '尚未安装语音识别组件，请先准备本地模型，或导入配套字幕。'}</p><button className="button primary wide" disabled={uploading || !uploadFile} type="submit">{uploading ? '正在上传与检查视频…' : subtitleFile ? '导入工作区' : '上传并自动转写'}<Icon name="arrow" size={16} /></button></form></section></div>}
    {toast && <div className="toast" role="status">{toast}<button onClick={() => setToast('')} aria-label="关闭提示"><Icon name="close" size={15} /></button></div>}
  </div>;
}
createRoot(document.getElementById('root')).render(<App />);
