/* Browser checks against a local mock only. No serial, WiFi device or TF access.
   Run with Node and Playwright available in NODE_PATH. Screenshots are labeled simulated. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'local-data', 'validation', 'f-range');
fs.mkdirSync(output, {recursive:true});
const boot='abcdefabcdefabcdefabcdefabcdefab';
let s={boot,phase:'idle',ready:true,connected:true,ip:'192.168.1.23',card_serial:'10044BA5',card_bytes:'7948206080',space_valid:true,free_bytes:'6144000000',
  run:'',seconds:0,written:0,elapsed_us:0,errors:0,queue:0,fifo_high:0,queue_high:0,write_us:0,sync_us:0,free_heap:150000,
  ina_valid:false,ina_us:0,bus_mv:0,current_ua:0,power_uw:0,detail:'软件模拟状态；非实机数据',sha256:'',preview_seq:0,preview_us:0,preview:[]};
let posts=[], unavailable=false, requests=0, outstanding=0,maxOutstanding=0,delayAck=false;
let trendUnavailable=false,trendRun=null,trendBoot=null;
const html=fs.readFileSync(path.join(root,'面包板样机_固件/diagnostics/sensor_web_recorder/src/index.html'),'utf8').replace('/*CONTROLLER_V2*/', ['sha256.js','controller_v2.js'].map(n=>fs.readFileSync(path.join(root,'面包板样机_固件/diagnostics/sensor_web_recorder/src',n),'utf8')).join('\n')+'\nv2Bootstrap();').replace('{{KEY}}',boot);
const server=http.createServer(async(req,res)=>{
  res.setHeader('Cache-Control','no-store');
  if(req.url==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
  if(req.url==='/api/status'){
    requests++;outstanding++;maxOutstanding=Math.max(maxOutstanding,outstanding);
    await new Promise(r=>setTimeout(r,30));outstanding--;
    res.statusCode=unavailable?503:200;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(s));return;
  }
  if(req.url==='/api/trend'){
    res.statusCode=trendUnavailable?503:200;res.setHeader('Content-Type','application/json');
    const run=trendRun===null?s.run:trendRun;
    res.end(JSON.stringify({boot:trendBoot===null?s.boot:trendBoot,run,range_g:s.range_g,scale_micro_g:s.scale_micro_g,sample_rate:26667,window_samples:5333,
      points:run?Array.from({length:150},(_,i)=>[(i+1)*5333,12000+i*10,14000+i*8,16000+i*6,i===40?2:0]):[]}));return;
  }
  if(req.url==='/api/v2/capabilities'){res.end('{"version":2}');return;}
  if(req.url?.startsWith('/api/v2/session/')&&!['/api/v2/session/start','/api/v2/session/stop','/api/v2/session/takeover'].includes(req.url)){req.resume();res.end('{"accepted":true}');return;}
  if(req.method==='POST'){
    let body='';for await(const data of req)body+=data;
    posts.push({url:req.url,key:req.headers['x-record-key'],body});
    assert.equal(req.headers['x-record-key'],boot);
    if(['/api/v2/session/start','/api/v2/session/takeover'].includes(req.url)){

      const r=JSON.parse(body);assert.equal(r.card_serial,s.card_serial);assert.equal(r.card_bytes,s.card_bytes);
      assert.match(r.run,/^[0-9A-F]{8}$/);s={...s,run:r.run,seconds:r.seconds,range_g:r.range_g,scale_micro_g:({2:61,8:244,16:488})[r.range_g],phase:'queued',owner:r.client};
    }
    if(delayAck)await new Promise(r=>setTimeout(r,11000));
    res.setHeader('Content-Type','application/json');res.end('{"accepted":true}');return;
  }
  res.statusCode=404;res.end();
});

async function main(){
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const context=await browser.newContext();await context.routeWebSocket('**/api/v2/live?**',ws=>ws.onMessage(()=>{}));
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));let checks=[];
 try{
 await page.goto('http://127.0.0.1:'+server.address().port);
 await page.waitForFunction(()=>!$('start').disabled);
 assert.deepEqual(await page.locator('#range option').evaluateAll(o=>o.map(x=>x.value)),['2','8','16']);checks.push('three options');
 for(const g of [2,8,16]){
  s={...s,phase:'idle',run:'',owner:'',preview:[],preview_seq:0};
  await page.waitForFunction(()=>!$('range').disabled);await page.locator('#range').selectOption(String(g));
  await page.locator('#start').click();await page.waitForFunction(()=>snapshot.phase==='queued');
  assert.equal(JSON.parse(posts.at(-1).body).range_g,g);assert(await page.locator('#range').isDisabled());
  s.phase='recording';s.preview=[[1000,2000,3000]];s.preview_seq++;
  await page.waitForFunction(g=>latestRange.g===g&&shownRaw.length>0,g);
  assert.equal(await page.evaluate(()=>shownRange.scale),({2:61,8:244,16:488})[g]);
  assert.match(await page.locator('#rawScale').innerText(),new RegExp(String(g*1000)));
  await page.waitForFunction(()=>latestTrend.length>0);checks.push('start '+g+'g payload/busy/scale/trend');
 }
 s={...s,phase:'passed',owner:''};await page.waitForFunction(()=>!$('range').disabled);
 await page.locator('#range').selectOption('8');s={...s,phase:'recording',owner:'other-client',run:'DEADBEEF'};
 await page.waitForFunction(()=>$('start').textContent==='接管并开始');await page.locator('#start').click();await page.locator('#takeoverContinue').click();
 await page.waitForFunction(()=>snapshot.owner===v2Client);assert.equal(posts.at(-1).url,'/api/v2/session/takeover');
 const r=JSON.parse(posts.at(-1).body);assert.equal(r.range_g,8);assert.equal(r.expected_run,'DEADBEEF');checks.push('takeover preserves selected range');
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'v2-range-browser.json'),JSON.stringify({checks,errors,posts:posts.map(x=>({url:x.url,range_g:JSON.parse(x.body).range_g}))},null,2));console.log('PASS '+checks.join('; '));
 }finally{await browser.close();server.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;server.close();});
