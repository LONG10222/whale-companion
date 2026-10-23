'use strict';
const assert=require('node:assert/strict');
const {createController}=require('../qq_social_controller.js');
const fs=require('node:fs'),vm=require('node:vm');
function fake() {
  let now=1000, calls=[], pending=null, failConfirm=false;
  const api=async(path,body)=>{
    calls.push({path,body});
    if(path==='/local/session')return {connected:true};
    if(path==='/qq/social/status')return {enabled:true};
    if(path==='/qq/status')return {connected:true,account_matches:true,enabled:true,login_id:'12345678'};
    if(path==='/qq/social/config')return {enabled:body.enabled};
    if(path==='/qq/social/requests')return {requests:[]};
    if(path==='/qq/social/prepare') {
      if(pending)return pending;
      return {nonce:'x'.repeat(40),expires:now+120,preview:{bot:'12345678',...body.operation,audience_label:'仅自己可见'}};
    }
    if(path==='/qq/social/confirm') {if(failConfirm)throw Error('network');return {state:'confirmed'};}
    throw Error('unexpected path');
  };
  return {api,calls,manager:createController(api,()=>now),setTime:v=>now=v,
    defer:p=>pending=p,fail:()=>failConfirm=true};
}
const op={kind:'qzone_text',content:'test',audience:'self'};
(async()=>{
  let f=fake(),c=f.manager;
  await c.prepare(op); assert.equal(f.calls.length,0,'starts disconnected');
  await c.connect(); await c.configure(false); await c.prepare(op);
  assert.equal(f.calls.filter(x=>x.path.endsWith('/prepare')).length,0,'disabled cannot prepare');
  await c.configure(true); await c.prepare(op);
  assert.equal(f.calls.filter(x=>x.path.endsWith('/confirm')).length,0,'preview never sends');
  await c.confirm(false); assert.ok(c.state.preview,'unchecked cannot send');
  await Promise.all([c.confirm(true),c.confirm(true)]);
  assert.equal(f.calls.filter(x=>x.path.endsWith('/confirm')).length,1,'double-click single submit');
  assert.equal(c.state.preview,null); assert.equal(c.state.phase,'confirmed');
  assert.equal(c.state.lastAttempt.content,'test','exact content retained without nonce');
  await c.prepare(op); f.setTime(1121); await c.confirm(true);
  assert.equal(f.calls.filter(x=>x.path.endsWith('/confirm')).length,1,'expired nonce not sent');
  f=fake();c=f.manager;await c.connect();
  let resolve; f.defer(new Promise(r=>resolve=r)); const preparing=c.prepare(op);
  c.invalidate('edited');resolve({nonce:'x'.repeat(40),expires:1120,preview:op});await preparing;
  assert.equal(c.state.preview,null,'late preview after edit is discarded');
  f=fake();c=f.manager;await c.connect();await c.prepare(op);c.invalidate('changed audience');
  await c.confirm(true);assert.equal(f.calls.filter(x=>x.path.endsWith('/confirm')).length,0);
  await c.prepare(op);await c.connect();assert.equal(c.state.preview,null,'new session invalidates nonce');
  await c.prepare(op);f.fail();await c.confirm(true);await c.confirm(true);await c.prepare(op);
  assert.equal(f.calls.filter(x=>x.path.endsWith('/confirm')).length,1,'unknown never retries');
  assert.equal(c.state.uncertain,true);assert.ok(c.state.notice.includes('人工核对'));
  c.invalidate('edited');assert.ok(c.state.notice.includes('人工核对'),'unknown warning cannot be erased by editing');
  c.afterManualCheck();assert.equal(c.state.lastAttempt,null);
  const failing=createController(async()=>{throw Error('offline')});
  await failing.connect();assert.equal(failing.state.connected,false);assert.equal(failing.state.enabled,false);
  console.log('Controller offline safety assertions passed');

  // Offline DOM smoke test: real scripts + a minimal DOM, zero browser/QQ networking.
  class Element {
    constructor(id='') {this.id=id;this.children=[];this.listeners={};this.dataset={};this.value='';this.checked=false;this.disabled=false;this.hidden=false;this.textContent='';}
    addEventListener(type,fn){this.listeners[type]=fn;}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=nodes;}
  }
  const html=fs.readFileSync('qq_social.html','utf8');
  const elements={};for(const [,id] of html.matchAll(/id="([^"]+)"/g))elements[id]=new Element(id);
  let domCalls=[];
  const document={getElementById:id=>elements[id],createElement:()=>new Element(),
    querySelectorAll:()=>[],addEventListener:()=>{},hidden:false};
  const response=data=>({ok:true,status:200,json:async()=>data});
  const dangerous='<img src=x onerror=alert(1)>';
  const fetch=async(path,options)=>{
    domCalls.push({path,body:options.body&&JSON.parse(options.body)});
    if(path==='/local/session')return response({});
    if(path==='/qq/social/status')return response({enabled:true});
    if(path==='/qq/status')return response({connected:true,account_matches:true,enabled:true,login_id:'12345678'});
    if(path==='/qq/social/requests')return response({requests:[{id:'safe-id',user_id:'87654321',comment:dangerous}]});
    if(path==='/qq/social/prepare')return response({nonce:'secret-nonce',expires:Date.now()/1000+120,
      preview:{kind:'qzone_text',bot:'12345678',content:dangerous,audience:'self',audience_label:'仅自己可见'}});
    if(path==='/qq/social/confirm')throw Error('network uncertain');
    throw Error('unexpected '+path);
  };
  const window={fetch,addEventListener:()=>{}};
  const ctx=vm.createContext({document,window,WhaleSocial:{createController},AbortController,
    setTimeout,clearTimeout,setInterval:()=>{},Date,console});
  const app=fs.readFileSync('qq_social_app.js','utf8');
  assert.ok(!app.includes('innerHTML'),'untrusted text must not enter HTML');
  assert.ok(!/localStorage|sessionStorage/.test(app),'no persistent browser storage');
  vm.runInContext(app,ctx);
  assert.equal(domCalls.length,0,'page load causes no network or authentication');
  elements.connectSession.listeners.click();await new Promise(r=>setImmediate(r));
  await elements.loadRequests.listeners.click();
  const card=elements.requestList.children[0];
  assert.equal(card.children[1].textContent,dangerous,'request comment remains literal text');
  elements.postContent.value=dangerous;elements.audience.value='self';
  elements.postForm.listeners.submit({preventDefault(){}});await new Promise(r=>setImmediate(r));
  assert.equal(elements.reviewContent.textContent,dangerous,'preview remains literal text');
  assert.equal(elements.confirmAction.disabled,true,'explicit checkbox initially required');
  elements.reviewChecked.checked=true;elements.reviewChecked.listeners.change();
  elements.confirmAction.listeners.click();elements.confirmAction.listeners.click();
  await new Promise(r=>setImmediate(r));
  assert.equal(domCalls.filter(x=>x.path==='/qq/social/confirm').length,1);
  assert.ok(elements.notice.textContent.includes('人工核对'));
  assert.equal(elements.confirmAction.hidden,true,'consumed confirmation cannot be clicked again');
  console.log('DOM offline safety assertions passed (not a browser visual test)');
})().catch(e=>{console.error(e);process.exitCode=1});
