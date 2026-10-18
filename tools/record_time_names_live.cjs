/* Two explicitly authorized 3s board recordings. Only the approved parent starts this. */
const fs=require('fs'),path=require('path'),assert=require('assert/strict'),crypto=require('crypto');
const {chromium}=require('playwright');
const [base,folder]=process.argv.slice(2);
if(!/^http:\/\/127\.0\.0\.1:\d+\/$/.test(base)||!process.env.BREADBOARD_V2_APPROVAL)throw Error('Approved local USB host required');
let browser,context;const records=[],events=[];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function emit(type,data={}){const value={at:new Date().toISOString(),type,...data};events.push(value);console.log(JSON.stringify(value));}
async function main(){
 browser=await chromium.launch({headless:true,channel:'msedge',args:['--no-proxy-server']});
 context=await browser.newContext({viewport:{width:1360,height:1050}});
 const p=await context.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto(base,{waitUntil:'domcontentloaded',timeout:15000});await p.waitForFunction(()=>v2Ready&&snapshot?.ready,{},{timeout:15000});
 const initial=await p.evaluate(()=>snapshot);
 assert(['idle','passed'].includes(initial.phase));assert.equal(initial.errors,0);assert.equal(initial.card_serial,'10044BA5');
 assert.equal((await p.evaluate(()=>v2Api('capabilities'))).record_time_names,true);
 // Leave enough of the real local minute for both starts. Never fake controller time.
 while(new Date().getSeconds()>35)await sleep(400);
 for(let i=0;i<2;i++){
  await p.locator('#seconds').fill('3');const requested=new Date();
  await p.locator('#start').click();
  await p.waitForFunction(old=>snapshot&&snapshot.run!==old&&['recording','passed','failed','rejected'].includes(snapshot.phase),i?records[i-1].run:initial.run,{timeout:20000});
  await p.waitForFunction(()=>['passed','failed','rejected'].includes(snapshot.phase),{},{timeout:30000});
  const s=await p.evaluate(()=>snapshot);assert.equal(s.phase,'passed',s.detail);assert.equal(s.errors,0);assert.equal(s.boot,initial.boot);assert.equal(s.seconds,3);
  assert(s.elapsed_us>2500000&&s.elapsed_us<4500000);assert.match(s.sha256,/^[0-9a-f]{64}$/);
  assert.match(s.record_name,/^\d{2}-\d{2}-\d{2}-\d{2}\.\d{2}(?:-\d{2,4})?$/);
  assert.equal(s.record_path,'/'+s.record_name+'/'+s.record_name+'.BIN');
  const expected=await p.evaluate(ms=>v2LocalTime(new Date(ms)).slice(0,14),requested.getTime());
  assert.equal(s.record_name.slice(0,14),expected);
  records.push({run:s.run,name:s.record_name,path:s.record_path,seconds:s.seconds,elapsed_us:s.elapsed_us,samples:s.written,sha256:s.sha256,fifo_high:s.fifo_high,queue_high:s.queue_high});emit('record_passed',records[i]);
 }
 assert.equal(records[0].name.length,14,'First time directory was already occupied; do not overwrite or silently expand trial');
 assert.equal(records[1].name,records[0].name+'-02');assert.notEqual(records[0].run,records[1].run);
 for(const record of records){
  const response=await context.request.get(base+'api/v2/download?path='+encodeURIComponent(record.path),{timeout:60000});
  assert.equal(response.status(),200);const bytes=await response.body();
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),record.sha256);
  assert(response.headers()['content-disposition'].includes(encodeURIComponent(record.name+'.BIN')));
  fs.writeFileSync(path.join(folder,record.name+'.BIN'),bytes,{flag:'wx'});record.download_bytes=bytes.length;record.download_sha256=record.sha256;emit('complete_pc_readback',record);
 }
 assert.deepEqual(errors,[]);
 fs.writeFileSync(path.join(folder,'browser-result.json'),JSON.stringify({passed:true,actual_device:true,boot:initial.boot,records,page_errors:errors,network_changed:false,tf_deleted:false},null,2)+'\n');
}
main().catch(e=>{console.error(e);fs.writeFileSync(path.join(folder,'browser-error.json'),JSON.stringify({passed:false,error:e.stack},null,2));process.exitCode=1;})
.finally(async()=>{fs.writeFileSync(path.join(folder,'browser-events.json'),JSON.stringify(events,null,2)+'\n');if(context)await context.close();if(browser)await browser.close();});
