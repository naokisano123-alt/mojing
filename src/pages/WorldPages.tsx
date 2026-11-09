import { useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowUpRight, Plus, Upload, FileText, X, ArrowRight, BookOpen } from 'lucide-react';
import type { World } from '../model';
import type { WorldSummary } from '../api/types';
import { clock } from '../model';
import { WorldTransferImport } from '../components/documents/WorldTransferImport';
import { Back, Landscape, PageTitle, Tag } from '../components/UI';
export function Home({worlds,enter,create,remoteWorlds,remoteStatus,remoteError,onRetryRemote,onEnterRemote,onCreateRemote}:{
  worlds:World[];enter:(id:string)=>void;create:()=>void;
  remoteWorlds:WorldSummary[];
  remoteStatus:'loading'|'ready'|'error';
  remoteError:string;
  onRetryRemote:()=>void;
  onEnterRemote:(world:WorldSummary)=>void;
  onCreateRemote:(name:string,description:string)=>Promise<void>;
}){
  const [creating,setCreating]=useState(false);
  const [name,setName]=useState('');
  const [desc,setDesc]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function submit(e:FormEvent){
    e.preventDefault();
    if(!name.trim()){setError('请为世界资料库取一个名字');return}
    setBusy(true);setError('');
    try{await onCreateRemote(name.trim(),desc.trim());setCreating(false);setName('');setDesc('')}
    catch(err){setError(err instanceof Error?err.message:'创建失败，请确认后端服务已启动。')}
    finally{setBusy(false)}
  }
  return <><PageTitle eyebrow="YOUR WORLDS" title="你的世界，未完待续。" description="在文字里启程，去经历另一种人生。" action={<button className="primary desktop-create" onClick={()=>setCreating(true)} disabled={remoteStatus!=='ready'}><Plus size={18}/>新建世界</button>}/>
    <div className="section-label"><span>世界资料库 <small>{String(remoteWorlds.length).padStart(2,'0')}</small></span><span className="muted text-xs">AI 冒险 · 服务端存档</span></div>
    {remoteStatus==='loading'&&<p className="muted text-xs">正在连接资料库服务…</p>}
    {remoteStatus==='error'&&<p role="alert" className="error">{remoteError} <button type="button" className="text-button" onClick={onRetryRemote}>重新连接</button></p>}
    {remoteStatus==='ready'&&<WorldTransferImport onImported={onEnterRemote} />}
    {remoteStatus==='ready'&&<div className="world-grid">
      {remoteWorlds.map((w,i)=><article className="world-card" key={w.id}><button className="world-cover" onClick={()=>onEnterRemote(w)} aria-label={`查看${w.name}的世界资料库`}><Landscape night={false}/><span className="cover-tag">世界资料库</span><span className="cover-number">LORE / {String(i+1).padStart(2,'0')}</span></button><div className="world-body"><div className="flex items-center justify-between gap-2"><h2>{w.name}</h2><Tag>世界资料库 · 已就绪</Tag></div><p className="muted description">{w.description||'尚未填写世界介绍。'}</p><div className="world-progress"><span className="status-dot"/>已导入 {w.document_count} 个文档 · 可开启冒险</div><button className="continue-button" onClick={()=>onEnterRemote(w)}>进入世界<ArrowRight size={18}/></button></div></article>)}
      <button className="new-world-card" onClick={()=>setCreating(true)}><span className="new-world-icon"><Plus size={25}/></span><h3>新建世界资料库</h3><p>导入 TXT / Markdown，保存原文与章节来源</p><span className="text-button">创建资料库世界 <ArrowUpRight size={15}/></span></button>
    </div>}
    {remoteStatus==='ready'&&remoteWorlds.length===0&&<p className="muted text-xs">还没有资料库世界：先新建一个，再导入你的设定集。</p>}
    <div className="section-label"><span>我的世界 <small>{String(worlds.length).padStart(2,'0')}</small></span><span className="muted text-xs">演示世界 · 本地进度</span></div>
    <div className="world-grid">
      {worlds.map((w,i)=>{const b=w.branches.find(b=>b.id===w.active)!;return <article className="world-card" key={w.id}><button className="world-cover" onClick={()=>enter(w.id)} aria-label={`进入${w.name}`}><Landscape night={w.kind==='night'}/><span className="cover-tag">{w.kind==='night'?'奇幻 · 探索':w.kind==='harbor'?'奇幻 · 日常':'自定义世界'}</span><span className="cover-number">WORLD / {String(i+1).padStart(2,'0')}</span></button><div className="world-body"><div className="flex items-center justify-between gap-2"><h2>{w.name}</h2><Tag>M0 · 演示世界</Tag></div><p className="muted description">{w.description}</p><div className="world-progress"><span className="status-dot"/>{b.location}<span>·</span>{clock(b.time).split(' · ')[0]}</div><button className="continue-button" onClick={()=>enter(w.id)}>{b.turns.length>1?'继续冒险':'开启旅程'}<ArrowRight size={18}/></button></div></article>})}
      <button className="new-world-card" onClick={create}><span className="new-world-icon"><Plus size={25}/></span><h3>让想象有一个归处</h3><p>写下设定，开启属于你的世界</p><span className="text-button">创建新世界 <ArrowUpRight size={15}/></span></button>
    </div>
    <div className="home-note"><BookOpen size={20}/><div><h3>故事的下一页，由你书写。</h3><p>不必成为英雄。你可以探索、谋生、结识某个人，或只是看看今晚的月亮。</p></div></div>
    <p className="footnote">M0 演示为本地模拟内容 · 资料库世界可接入模型，旅程进度保存在服务端</p>
    {creating&&<div className="modal-backdrop" onClick={()=>{if(!busy)setCreating(false)}}><section role="dialog" aria-modal="true" aria-label="新建世界资料库" className="modal" onClick={e=>e.stopPropagation()}>
      <div className="flex items-center justify-between gap-3"><h2>新建世界资料库</h2><button type="button" className="icon-button" aria-label="关闭" onClick={()=>setCreating(false)} disabled={busy}><X size={20}/></button></div>
      <form onSubmit={submit}>
        <label htmlFor="lore-name">世界名称 <span className="required">*</span></label>
        <input id="lore-name" maxLength={40} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：潮汐档案馆" required/>
        <div className="field-meta">保存在服务端，与本地演示进度分开<span>{name.length}/40</span></div>
        <label htmlFor="lore-desc">世界介绍</label>
        <textarea id="lore-desc" maxLength={600} rows={3} value={desc} onChange={e=>setDesc(e.target.value)} placeholder="一句话描述这个世界"/>
        {error&&<p role="alert" className="error">{error}</p>}
        <p className="muted text-xs leading-relaxed">创建后可直接开始旅程，也可导入设定供模型参考。</p>
        <div className="flex gap-2"><button className="primary full" type="submit" disabled={busy}>{busy?'创建中…':'创建世界资料库'}</button><button className="secondary" type="button" onClick={()=>setCreating(false)} disabled={busy}>取消</button></div>
      </form>
    </section></div>}
  </>}
export function CreateWorld({cancel,submit}:{cancel:()=>void;submit:(name:string,description:string,documents:string[])=>void}){const [name,setName]=useState('');const [description,setDescription]=useState('');const [docs,setDocs]=useState<string[]>([]);const [error,setError]=useState('');return <div className="narrow"><Back onClick={cancel} label="我的世界"/><PageTitle eyebrow="A NEW BEGINNING" title="从一个念头，开始。" description="为你的世界取个名字，故事将在这里生长。"/><form onSubmit={e=>{e.preventDefault();if(!name.trim()){setError('请为世界取一个名字');return}submit(name.trim(),description.trim()||'一个等待你探索的新世界。',docs)}}><section className="panel form-panel"><label htmlFor="world-name">世界名称 <span className="required">*</span></label><input id="world-name" maxLength={40} value={name} onChange={e=>setName(e.target.value)} placeholder="例如：雾港来信" required/><div className="field-meta">一个让你想走进去的名字<span>{name.length}/40</span></div><label htmlFor="world-desc">世界介绍</label><textarea id="world-desc" maxLength={600} rows={4} value={description} onChange={e=>setDescription(e.target.value)} placeholder="这是一个怎样的世界？有哪些值得讲述的人和事……"/><label>世界观文档 <Tag>模拟导入</Tag></label><label className="upload-zone"><Upload size={26}/><strong>选择你的世界观资料</strong><span>PDF / Word / TXT / Markdown</span><input type="file" multiple accept=".pdf,.docx,.txt,.md" onChange={e=>{const files=Array.from(e.target.files||[]);if(files.some(f=>!/\.(pdf|docx|txt|md)$/i.test(f.name))){setError('请选择 PDF、DOCX、TXT 或 MD 文件');return}setDocs([...new Set([...docs,...files.map(f=>f.name)])]);setError('');e.target.value=''}}/></label><p className="muted text-xs leading-relaxed">此阶段仅记录文件名，不读取、上传或解析内容。新世界会使用统一的“雾港”演示开局。</p>{docs.map(d=><div key={d} className="file-row"><FileText size={17}/><span>{d}</span><button type="button" className="icon-button" aria-label={`移除${d}`} onClick={()=>setDocs(docs.filter(f=>f!==d))}><X size={16}/></button></div>)}</section>{error&&<p role="alert" className="error">{error}</p>}<button className="primary full" type="submit">创建世界，开始冒险<ArrowRight size={18}/></button><p className="footnote">无需配置模型或填写 API Key</p></form></div>}
