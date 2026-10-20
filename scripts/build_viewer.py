#!/usr/bin/env python3
"""Build a fully offline, escaped HTML viewer from the database JSON export."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Loom · 笔记与想法数据库</title>
<style>
:root{--ink:#192e42;--muted:#697784;--line:#dbe3e8;--paper:#f5f7f9;--orange:#b95428;--blue:#244e73}
*{box-sizing:border-box}body{margin:0;color:var(--ink);background:var(--paper);font:15px/1.7 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{background:var(--ink);color:#fff;padding:34px max(6vw,24px) 40px}.brand{letter-spacing:5px;font-size:13px;color:#e8b390}h1{font-size:32px;margin:8px 0 6px;letter-spacing:1px}header p{color:#ccdae4;margin:0;max-width:830px}
main{max-width:1360px;margin:auto;padding:24px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:22px}.stat{background:white;border:1px solid var(--line);border-radius:12px;padding:16px 22px}.stat b{display:block;font-size:27px;line-height:1.3}.stat span{color:var(--muted);font-size:13px}.notice{background:#fff5e9;border-left:4px solid #cb7a39;padding:12px 16px;margin:0 0 20px;font-size:13px}
.toolbar{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:12px}label{display:block;font-size:13px;color:var(--muted)}input,select{width:100%;font:inherit;border:1px solid #b9c7d1;border-radius:7px;background:white;color:var(--ink);padding:9px 12px;margin-top:5px}input:focus,select:focus{outline:2px solid #749dbc;outline-offset:2px}
.results{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:18px 0 12px;font-size:13px;color:var(--muted)}button{font:inherit;color:var(--blue);background:white;border:1px solid var(--line);border-radius:7px;cursor:pointer;padding:6px 12px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}article{background:white;border:1px solid var(--line);border-radius:12px;padding:21px 23px;min-width:0}article h2{font-size:18px;line-height:1.45;margin:8px 0 9px;overflow-wrap:anywhere}.meta{font-size:12px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}.badge{color:#a6502b;background:#fff1e8;border-radius:4px;padding:1px 7px;white-space:nowrap}
blockquote{margin:14px 0 12px;padding:10px 14px;background:#f3f6f8;border-left:3px solid #7f9cb2;font-size:15px;overflow-wrap:anywhere;white-space:pre-wrap}.small{color:var(--muted);font-size:12px}.summary{font-size:14px;margin:5px 0 12px}.tags{display:flex;flex-wrap:wrap;gap:6px}.tag{font-size:12px;border:1px solid #e0e6eb;border-radius:16px;padding:1px 9px;color:#47647c}
.bottom{border-top:1px solid #edf0f3;margin-top:16px;padding-top:12px;display:flex;justify-content:space-between;gap:10px;font-size:12px}a{color:var(--blue);text-underline-offset:3px}details{font-size:12px;margin-top:12px;color:var(--muted)}details p{margin:6px 0;overflow-wrap:anywhere}summary{cursor:pointer}.empty{text-align:center;padding:50px;color:var(--muted);background:white;border-radius:12px}
footer{padding:30px 0 12px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);margin-top:24px}footer a{margin-right:14px}#status{font-weight:600}noscript{display:block;padding:30px}
@media(max-width:850px){.toolbar{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}header{padding:28px 24px}h1{font-size:27px}}
@media(max-width:500px){main{padding:16px}.stats{grid-template-columns:1fr 1fr}.toolbar{grid-template-columns:1fr}article{padding:17px}.meta,.bottom{flex-wrap:wrap}}
</style></head><body>
<header><div class="brand">LOOM / RESEARCH COLLECTION</div><h1>让每条想法，都有出处。</h1>
<p>笔记与想法样本库 · 原文短摘录、中文整理与核查记录。为 Loom 的摘要、标签和检索实验准备可检查的数据。</p></header>
<main>
<section class="stats" aria-label="数据概况"><div class="stat"><b id="count"></b><span>公开笔记短摘录</span></div><div class="stat"><b id="authors"></b><span>独立作者</span></div><div class="stat"><b id="langs"></b><span>中文 / 英文</span></div><div class="stat"><b id="pending"></b><span>标注待人工复核</span></div></section>
<p class="notice">这是公开笔记的短摘录样本。中文整理为 AI 辅助，未作为人工标准答案；未运行 Loom 的 GLM 管线。许可未知的条目不视为可自由再利用，全部条目未获训练授权。</p>
<section class="toolbar" aria-label="检索与筛选">
<label>关键词检索<input id="query" type="search" placeholder="搜索标题、原文、中文整理或标签…" autocomplete="off"></label>
<label>作者<select id="author"><option value="">全部作者</option></select></label>
<label>语言<select id="language"><option value="">全部语言</option><option value="zh">中文</option><option value="en">英文</option></select></label>
<label>分类<select id="category"><option value="">全部分类</option><option value="idea">想法</option><option value="note">笔记</option><option value="question">问题</option><option value="event">事件</option></select></label>
</section>
<div class="results"><span id="status" role="status" aria-live="polite"></span><button id="reset" type="button">重置筛选</button></div>
<section id="cards" class="grid" aria-label="笔记列表"></section>
<footer><p id="stamp"></p><p>本页离线可用，关键词匹配不代表语义检索。原文以 JSON 为准；CSV 对公式前缀做安全处理。</p>
<a href="notes.json">JSON 数据</a><a href="notes.csv">CSV 表格</a><a href="notes.md">Markdown 清单</a><a href="../docs/高校参考.md">高校参考</a><a href="../README.md">使用说明</a></footer>
</main><noscript>请启用 JavaScript 查看筛选页面，或直接打开旁边的 notes.csv / notes.md。</noscript>
<script id="dataset" type="application/json">__DATA__</script>
<script>
'use strict';
const payload=JSON.parse(document.getElementById('dataset').textContent);
const notes=payload.notes;
const $=id=>document.getElementById(id);
const categories={idea:'想法',note:'笔记',question:'问题',event:'事件'};
const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const link=(url,label)=>{const a=el('a',label);try{const u=new URL(url);if(['https:','http:'].includes(u.protocol)){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer'}}catch{}return a};
$('count').textContent=notes.length;
$('authors').textContent=new Set(notes.map(n=>n.author)).size;
$('langs').textContent=`${notes.filter(n=>n.language==='zh').length} / ${notes.filter(n=>n.language==='en').length}`;
$('pending').textContent=notes.filter(n=>(n.annotation||{}).review_status==='pending').length;
for(const author of [...new Set(notes.map(n=>n.author))].sort()){const o=el('option',author);o.value=author;$('author').append(o)}
$('stamp').textContent=`最近采集：${notes.map(n=>n.retrieved_on).sort().at(-1)||'—'} · 本周推进至 2026-10-25`;
function render(){
const q=$('query').value.trim().toLocaleLowerCase(),author=$('author').value,language=$('language').value,category=$('category').value;
const matched=notes.filter(n=>{const a=n.annotation||{};return (!author||n.author===author)&&(!language||n.language===language)&&(!category||(a.category||n.category)===category)&&(!q||[n.title,n.original_excerpt,a.summary_zh||n.summary_zh,n.author,...n.tags].join(' ').toLocaleLowerCase().includes(q))});
$('status').textContent=`显示 ${matched.length} / ${notes.length} 条 · 可展开核查记录`;
const root=$('cards');root.replaceChildren();
if(!matched.length){const empty=el('div','未找到匹配记录。试试其他关键词或重置筛选。','empty');root.append(empty);return}
for(const n of matched){
const a=n.annotation||{},card=el('article');
const meta=el('div',undefined,'meta');meta.append(el('span',`${n.id} · ${n.author} · ${n.language==='zh'?'中文':'EN'}`),el('span',categories[a.category||n.category]||'未分类','badge'));card.append(meta,el('h2',n.title));
card.append(el('div','来源原文 · 短摘录','small'),el('blockquote',n.original_excerpt));
card.append(el('div','中文整理 · AI 辅助 / '+(a.review_status==='verified'?'已人工复核':'待人工复核'),'small'),el('p',a.summary_zh||n.summary_zh,'summary'));
const tags=el('div',undefined,'tags');n.tags.forEach(t=>tags.append(el('span',t,'tag')));card.append(tags);
const details=el('details');details.append(el('summary','查看位置、许可与采集依据'));
details.append(el('p','原文定位：'+n.excerpt_locator),el('p','采集日期：'+n.retrieved_on),el('p','核查记录：'+n.evidence_note),el('p',`数据分组：${n.split} · GLM 管线：${a.pipeline_status||'not_run'} · 训练授权：未授予`),el('p','原文 SHA-256：'+n.original_sha256));
if(n.license_url)details.append(link(n.license_url,'查看许可条款'));card.append(details);
const bottom=el('div',undefined,'bottom');bottom.append(link(n.url,'打开原始来源 ↗'),el('span',n.license_status==='open'?n.license_name:'再利用许可未核实'));card.append(bottom);root.append(card);
}}
['query','author','language','category'].forEach(id=>$(id).addEventListener('input',render));
$('reset').addEventListener('click',()=>{['query','author','language','category'].forEach(id=>$(id).value='');render()});
render();
</script></body></html>
"""


def build():
    src = ROOT / "exports" / "notes.json"
    data = json.loads(src.read_text(encoding="utf-8-sig"))
    if not isinstance(data.get("notes"), list) or not data["notes"]:
        raise ValueError("JSON must contain non-empty notes")
    # Escape script-closing markup without changing the parsed JSON values.
    encoded = json.dumps(data, ensure_ascii=False).replace("&", "\\u0026")
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e")
    encoded = encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    out = ROOT / "exports" / "浏览数据库.html"
    out.write_text(TEMPLATE.replace("__DATA__", encoded), encoding="utf-8")
    print(json.dumps({"output": str(out), "notes": len(data["notes"])}, ensure_ascii=False))


if __name__ == "__main__":
    build()
