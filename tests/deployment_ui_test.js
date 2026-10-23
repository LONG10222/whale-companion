'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../deployment.js'), 'utf8');
function harness(reply) {
  const elements = {};
  for (const id of ['key','saveAgree','saveKey','keyNotice','inspect','preview','agree','connect','notice','manage']) elements[id] = {value:'',checked:false,disabled:false,hidden:true,textContent:''};
  const listeners = {}, calls = [];
  const document = {hidden:false,getElementById:id=>elements[id],addEventListener:(k,f)=>listeners[k]=f};
  const window = {addEventListener:(k,f)=>listeners[k]=f,fetch:async (url, options)=>{
    calls.push({url,options});return reply(url,options);
  }};
  vm.runInNewContext(source, {window,document,Date,setInterval:()=>0});
  return {elements,listeners,calls,document};
}
const ok = data => ({ok:true,json:async()=>data});
const preview = {nonce:'test-only',preview:{bot_id:'123456',allowed_private_ids:['654321']},expires_in:120};
const normal = url => ok(url==='/deployment/prepare' ? preview : url==='/deployment/status' ? {phase:'paused'} : {message:'connected but paused'});
(async () => {
  const h=harness(normal);assert.equal(h.calls.length,0);
  await h.elements.inspect.onclick();h.elements.agree.checked=true;h.elements.agree.onchange();
  await h.elements.connect.onclick();await h.elements.connect.onclick();
  assert.equal(h.calls.filter(c=>c.url==='/deployment/connect').length,1);
  assert.equal(h.elements.manage.hidden,false);
  assert.equal(h.elements.preview.textContent.includes('123456'),true);
  assert.equal(h.elements.preview.textContent.includes('test-only'),false);
  const fail=harness(url=>url==='/deployment/connect'?{ok:false}:normal(url));
  await fail.elements.inspect.onclick();fail.elements.agree.checked=true;await fail.elements.connect.onclick();await fail.elements.connect.onclick();
  assert.equal(fail.calls.filter(c=>c.url==='/deployment/connect').length,1);
  assert.equal(fail.calls.filter(c=>c.url==='/local/session').length,1);
  assert.match(fail.elements.notice.textContent,/不确定/);
  let resolve;
  const late=harness(url=>url==='/deployment/prepare'?new Promise(r=>resolve=r):normal(url));
  const task=late.elements.inspect.onclick();
  while(!resolve) await Promise.resolve();
  late.document.hidden=true;late.listeners.visibilitychange();resolve(ok(preview));await task;
  assert.equal(late.elements.agree.disabled,true);assert.equal(late.elements.connect.disabled,true);
  const reused=harness(url=>url==='/deployment/status'?ok({phase:'connected_requested'}):normal(url));
  await reused.elements.inspect.onclick();assert.equal(reused.calls.some(c=>c.url==='/deployment/prepare'),false);assert.equal(reused.elements.manage.hidden,false);
  const key=harness(normal);key.elements.key.value='unit-key';await key.elements.saveKey.onclick();assert.equal(key.calls.length,0);
  key.elements.saveAgree.checked=true;await key.elements.saveKey.onclick();
  assert.equal(key.calls.filter(c=>c.url==='/agent/config').length,1);
  assert.equal(JSON.parse(key.calls.find(c=>c.url==='/agent/config').options.body).api_key,'unit-key');
  assert.equal(key.elements.key.value,'');assert.equal(key.elements.saveAgree.checked,false);
  console.log('Deployment DOM mock checks passed (17 assertions; no browser or QQ).');
})().catch(error=>{console.error(error);process.exitCode=1;});
