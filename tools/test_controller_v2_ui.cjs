/* Local simulated servers only: no hardware, serial, Wi-Fi changes, or card access. */
const fs=require('fs'),path=require('path'),http=require('http'),crypto=require('crypto'),assert=require('assert/strict');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),src=path.join(root,'面包板样机_固件/diagnostics/sensor_web_recorder/src');
const SHA=require(path.join(src,'sha256.js'));
for(const n of [0,1,3,55,56,63,64,65,2048,65537,1000000]){
 const data=crypto.randomBytes(n),hash=new SHA;
 for(let i=0;i<n;i+=37)hash.update(data.subarray(i,i+37));
 assert.equal(hash.hex(),crypto.createHash('sha256').update(data).digest('hex'));
}
const boot='abcdefabcdefabcdefabcdefabcdefab',out=path.join(root,'local-data/validation/controller-v2-ui');fs.mkdirSync(out,{recursive:true});
const html=fs.readFileSync(path.join(src,'index.html'),'utf8').replace('/*CONTROLLER_V2*/',fs.readFileSync(path.join(src,'sha256.js'),'utf8')+'\n'+fs.readFileSync(path.join(src,'controller_v2.js'),'utf8')+'\nv2Bootstrap();').replace('{{KEY}}',boot);
let s={boot,phase:'idle',ready:true,connected:true,ip:'192.168.1.23',card_serial:'10044BA5',card_bytes:'7948206080',space_valid:true,free_bytes:'6144000000',run:'',owner:'',pending_owner:'',files_busy:false,seconds:0,written:0,elapsed_us:0,errors:0,queue:0,fifo_high:0,queue_high:0,write_us:0,sync_us:0,free_heap:150000,ina_valid:false,ina_us:0,bus_mv:0,current_ua:0,power_uw:0,detail:'离线模拟，非实机数据',sha256:'',preview_seq:0,preview_us:0,preview:[]};
let posts=[],fileState={},upload=[],confirmedDigest='';
const server=http.createServer(async(req,res)=>{
 res.setHeader('Content-Type','application/json; charset=utf-8');
 const url=new URL(req.url,'http://localhost');
 if(url.pathname==='/'){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
 if(url.pathname==='/api/status'){res.end(JSON.stringify(s));return;}
 if(url.pathname==='/api/trend'){res.end(JSON.stringify({boot,run:s.run,sample_rate:26667,window_samples:5333,points:[]}));return;}
 if(url.pathname==='/api/v2/capabilities'){res.end('{"version":2}');return;}
 if(url.pathname==='/api/v2/network/state'){res.end(JSON.stringify({sta_enabled:true,ap_enabled:true,connected:true,ssid:'测试网络',ap_ssid:'Breadboard-Test',ap_password:'password1234',sta_ip:'192.168.1.23',ap_ip:'192.168.4.1'}));return;}
 if(url.pathname==='/api/v2/files/state'){res.end(JSON.stringify(fileState));return;}
 if(url.pathname==='/api/v2/download'){res.setHeader('Content-Type','application/octet-stream');res.end('mock waveform');return;}
 if(req.method==='POST'){
  let raw='';for await(const bytes of req)raw+=bytes;const a=JSON.parse(raw);posts.push({path:url.pathname,a});
  assert.equal(a.boot,boot);
  const action=url.pathname.split('/').pop();
  if(action==='start'){const name=a.local_time.slice(0,14);s={...s,owner:a.client,run:a.run,seconds:a.seconds,phase:'recording',record_name:name,record_path:'/'+name+'/'+name+'.BIN'};}
  if(action==='takeover'){assert.equal(a.expected_run,s.run);s={...s,pending_owner:a.client,phase:'draining'};}
  if(action==='stop')s.phase='passed';
  if(url.pathname.includes('/files/')){
   let result={accepted:true};
   if(action==='list')result={entries:[{name:'测试.bin',directory:false,bytes:9},{name:'<img onerror=alert(1)>',directory:false,bytes:0},{name:'子目录',directory:true,bytes:0}],next:3,more:false};
   if(action==='measure')result={files:1,bytes:9,revision:5};
   if(action==='begin_upload'){upload=[];result={transfer:'transfer12345678'};}
   if(action==='chunk'){assert.equal(a.offset,Buffer.concat(upload).length);upload.push(Buffer.from(a.data,'base64'));}
   if(action==='finish'){confirmedDigest=crypto.createHash('sha256').update(Buffer.concat(upload)).digest('hex');assert.equal(a.sha256,confirmedDigest);}
   fileState={job:a.job,running:false,bytes:0,code:200,result};
  }
  res.end('{"accepted":true}');return;
 }
 res.statusCode=404;res.end('{}');
});
async function main(){
 await new Promise(r=>server.listen(0,'127.0.0.1',r));const base='http://127.0.0.1:'+server.address().port;
 const browser=await chromium.launch({headless:true,channel:'msedge'});const context=await browser.newContext({viewport:{width:1360,height:1050}});
 // Chromium handles native WS pings in production; this only makes a stable logical control channel for UI tests.
 await context.routeWebSocket('**/api/v2/live?**',ws=>ws.onMessage(()=>{}));
 const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 let count=11;const check=async(name,fn)=>{await fn();count++;console.log('PASS '+name);};
 try{
  await p.goto(base);await p.waitForFunction(()=>!document.getElementById('start').disabled);
  await check('no automatic acquisition',async()=>assert.equal(posts.filter(x=>x.path.endsWith('/start')).length,0));
  await check('start binds independent session and duration',async()=>{await p.locator('#seconds').fill('60');await p.locator('#start').click();await p.waitForFunction(()=>snapshot?.phase==='recording');assert.equal(s.seconds,60);assert.match(s.owner,/^[0-9a-f]{32}$/);});
  await check('start sends local calendar time and shows actual TF path',async()=>{const a=posts.findLast(x=>x.path.endsWith('/start')).a;assert.match(a.local_time,/^\d{2}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2}$/);assert.match(await p.locator('#recordFile').innerText(),new RegExp(s.record_name.replaceAll('.','\\.')));});
  await check('naming uses local date components and rejects out-of-range years',async()=>{
    assert.equal(await p.evaluate(()=>v2LocalTime(new Date(2026,9,18,15,46,32))),'26-10-18-15.46.32');
    assert.equal(await p.evaluate(()=>v2LocalTime(new Date(2024,1,29,0,0,0))),'24-02-29-00.00.00');
    assert(await p.evaluate(()=>{try{v2LocalTime(new Date(2100,0,1));return false;}catch{return true;}}));
  });
  const oldOwner=s.owner,oldRun=s.run;const q=await context.newPage();q.on('pageerror',e=>errors.push(e.message));await q.goto(base);await q.waitForFunction(()=>!document.getElementById('start').disabled);
  await check('other page cancel preserves first recording',async()=>{await q.locator('#start').click();await q.locator('#takeoverCancel').click();assert.equal(s.owner,oldOwner);assert.equal(s.phase,'recording');assert.equal(posts.filter(x=>x.path.endsWith('/takeover')).length,0);});
  await check('continue explicitly targets observed run and new run',async()=>{await q.locator('#seconds').fill('90');await q.locator('#start').click();await q.locator('#takeoverContinue').click();await q.waitForFunction(()=>snapshot?.phase==='draining');const a=posts.findLast(x=>x.path.endsWith('/takeover')).a;assert.equal(a.expected_run,oldRun);assert.notEqual(a.run,oldRun);assert.equal(a.seconds,90);assert.notEqual(a.client,oldOwner);});
  await check('takeover submits its own local timestamp',async()=>assert.match(posts.findLast(x=>x.path.endsWith('/takeover')).a.local_time,/^\d{2}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2}$/));
  await check('closing observer releases only its own session',async()=>{const id=await q.evaluate(()=>v2Client);await q.close();await new Promise(r=>setTimeout(r,150));assert(posts.some(x=>x.path.endsWith('/release')&&x.a.client===id));});
  s={...s,phase:'passed',pending_owner:'',owner:''};
  await p.locator('[data-panel=network]').click();await p.waitForFunction(()=>document.getElementById('wifiSsid').value==='测试网络');
  await check('network displays shareable URLs and masks secrets',async()=>{assert.match(await p.locator('#deviceUrls').innerText(),/192.168.1.23/);assert.equal(await p.locator('#apPassword').getAttribute('type'),'password');assert.equal(await p.locator('#wifiPassword').inputValue(),'');});
  await check('network save targets controller configuration',async()=>{await p.locator('#saveNetwork').click();await p.waitForFunction(()=>document.getElementById('networkMessage').textContent.includes('已保存'));assert.equal(posts.findLast(x=>x.path.endsWith('/save')).a.ap_ssid,'Breadboard-Test');});
  await p.locator('[data-panel=files]').click();await p.waitForFunction(()=>document.querySelectorAll('#fileRows tr').length===3);
  await check('TF names are plain text, Chinese paths retained',async()=>{assert.equal(await p.locator('#fileRows img').count(),0);assert.match(await p.locator('#fileRows').innerText(),/测试.bin/);});
  await p.locator('#fileRows tr').first().click();
  await check('delete cancellation never dispatches delete',async()=>{p.once('dialog',d=>d.dismiss());await p.locator('#deleteFile').click();await p.waitForFunction(()=>document.getElementById('fileMessage').textContent.includes('已取消删除'));assert.equal(posts.filter(x=>x.path.endsWith('/delete')).length,0);});
  await check('confirmed deletion carries measured revision',async()=>{p.once('dialog',d=>d.accept());await p.locator('#deleteFile').click();await p.waitForFunction(()=>document.getElementById('fileMessage').textContent.includes('删除已完成'));assert.equal(posts.findLast(x=>x.path.endsWith('/delete')).a.revision,5);});
  await check('upload chunks and SHA match standard implementation',async()=>{const bytes=crypto.randomBytes(5001);await p.locator('#uploadPicker').setInputFiles({name:'测试上传.bin',mimeType:'application/octet-stream',buffer:bytes});await p.waitForFunction(()=>document.getElementById('fileMessage').textContent.includes('上传已逐文件'));assert.deepEqual(Buffer.concat(upload),bytes);assert.equal(confirmedDigest,crypto.createHash('sha256').update(bytes).digest('hex'));});
  await check('download default retains timestamp and collision suffix',async()=>{await p.evaluate(()=>v2Selected={name:'26-10-18-15.46-02.BIN',path:'/26-10-18-15.46-02/26-10-18-15.46-02.BIN',directory:false});const event=p.waitForEvent('download');await p.locator('#downloadFile').click();const download=await event;assert.equal(download.suggestedFilename(),'26-10-18-15.46-02.BIN');await download.cancel();});
  await check('TF and acquisition controls exclude busy recording',async()=>{s.phase='recording';await p.waitForFunction(()=>document.getElementById('deleteFile').disabled);assert(await p.locator('#uploadFiles').isDisabled());});
  s.phase='passed';await p.waitForFunction(()=>!document.getElementById('uploadFiles').disabled);
  await p.screenshot({path:path.join(out,'web-tf-simulated.png'),fullPage:true});
  await p.setViewportSize({width:390,height:844});await p.screenshot({path:path.join(out,'web-mobile-simulated.png'),fullPage:true});
  await check('query entry loads network after control socket opens',async()=>{const n=await context.newPage();await n.goto(base+'/?panel=network');await n.waitForFunction(()=>document.getElementById('wifiSsid').value==='测试网络');await n.close();});
  await check('file polling observes completion before transfer waits and yields while busy',async()=>{
   const results=await p.evaluate(async()=>{
    const originalApi=v2Api,originalTimeout=window.setTimeout;const results=[];
    try{
     for(const action of ['chunk','read','copy'])for(const busyCount of [0,2]){
      const events=[];let job,busy=busyCount;
      v2Api=async(path,args)=>{if(args){events.push('post');job=args.job;return {job};}events.push('get');return {job,running:busy-->0,bytes:0,code:200,result:{ok:true}};};
      window.setTimeout=(fn,ms,...args)=>{if(ms===20||ms===180){events.push('wait:'+ms);return originalTimeout(fn,0,...args);}return originalTimeout(fn,ms,...args);};
      await v2FileOp(action);results.push({action,busyCount,events});
     }
     const events=[];v2Api=async(path,args)=>{events.push(args?'post':'get');return args?{job:args.job}:{job:'changed',running:true};};
     window.setTimeout=(fn,ms,...args)=>{if(ms===20||ms===180)events.push('wait:'+ms);return originalTimeout(fn,0,...args);};
     let rejected=false;try{await v2FileOp('read');}catch{rejected=true;}results.push({wrongJob:true,rejected,events});return results;
    }finally{v2Api=originalApi;window.setTimeout=originalTimeout;}
   });
   for(const r of results){if(r.wrongJob){assert(r.rejected);assert.deepEqual(r.events,['post','get']);continue;}
    const expected=['post'];if(r.action==='copy')expected.push('wait:180');expected.push('get');for(let i=0;i<r.busyCount;i++)expected.push(r.action==='copy'?'wait:180':'wait:20','get');assert.deepEqual(r.events,expected);
   }
  });
  await check('manual file cancel retains explicit global primitive',async()=>{
   await p.locator('#cancelFile').click();await p.waitForFunction(()=>document.getElementById('fileMessage').textContent.includes('已请求取消'));
   const a=posts.findLast(x=>x.path==='/api/v2/files/cancel').a;assert(!Object.hasOwn(a,'expected_job'));assert.equal(a.transfer,'');
  });
  for(const uncertain of [false,true])await check('pagehide binds last file job and transfer'+(uncertain?' after uncertain POST':''),async()=>{
   const n=await context.newPage();await n.goto(base);await n.waitForFunction(()=>v2Ready);
   const beginJob=await n.evaluate(async()=>{const begin=await v2FileOp('begin_upload',{path:'/beacon.bin',bytes:1});v2Transfer=begin.transfer;return v2LastFileJob;});
   if(uncertain){
    await n.route('**/api/v2/files/chunk',route=>route.abort());
    await n.evaluate(async()=>{try{await v2FileOp('chunk',{transfer:v2Transfer,offset:0,data:'YQ=='});}catch{}});
   }
   const owned=await n.evaluate(()=>({job:v2LastFileJob,transfer:v2Transfer}));assert.match(owned.job,/^[0-9a-f]{32}$/);if(uncertain)assert.notEqual(owned.job,beginJob);
   const before=posts.filter(x=>x.path==='/api/v2/files/cancel').length;
   await n.evaluate(()=>window.dispatchEvent(new Event('pagehide')));
   for(let i=0;i<40&&posts.filter(x=>x.path==='/api/v2/files/cancel').length===before;i++)await new Promise(r=>setTimeout(r,25));
   const a=posts.findLast(x=>x.path==='/api/v2/files/cancel').a;assert.equal(a.expected_job,owned.job);assert.equal(a.transfer,owned.transfer);assert.notEqual(a.job,owned.job);await n.close();
  });
  assert.deepEqual(errors,[]);console.log('RESULT '+count+' checks; local mocks only');
 }finally{await context.close();await browser.close();server.close();}
}
main().catch(e=>{console.error(e);server.close();process.exitCode=1;});
