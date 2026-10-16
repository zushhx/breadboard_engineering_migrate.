/* Actual-device UI check. Parent and child both validate the specific human approval. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const [url,folder]=process.argv.slice(2);
if(!/^http:\/\/\d+\.\d+\.\d+\.\d+\/$/.test(url)||!process.env.BREADBOARD_APPROVAL_PATH||!process.env.BREADBOARD_PYTHON)throw Error('requires gated actual-device trial');
execFileSync(process.env.BREADBOARD_PYTHON,[path.join(__dirname,'ui_live_trial.py'),'--validate-approval','--approval-record',process.env.BREADBOARD_APPROVAL_PATH],{stdio:'pipe'});
const events=[];let browser;const delay=ms=>new Promise(r=>setTimeout(r,ms));
function emit(type,data){const entry={time:new Date().toISOString(),type,...data};events.push(entry);console.log(JSON.stringify(entry));}
(async()=>{
 try{
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--no-proxy-server']});
  const context=await browser.newContext({viewport:{width:1360,height:1080}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(url,{waitUntil:'domcontentloaded',timeout:15000});
  await page.waitForFunction(()=>snapshot&&online&&snapshot.ready&&!document.getElementById('start').disabled,null,{timeout:15000});
  assert.equal(await page.locator('#trend-title').innerText(),'振动强度趋势');
  assert.equal(await page.locator('#scenario').count(),0);
  await page.locator('[data-seconds="30"]').click();assert.equal(await page.locator('#seconds').inputValue(),'30');
  const initial=await page.evaluate(()=>snapshot);assert.equal(initial.phase,'idle');
  assert.equal(initial.space_valid,true);assert(Number(initial.free_bytes)>0);
  assert.equal(initial.card_serial,'10044BA5');assert.equal(initial.card_bytes,'7948206080');
  await page.locator('#start').click();
  await page.waitForFunction(()=>snapshot&&['recording','failed','rejected'].includes(snapshot.phase),null,{timeout:20000});
  let current=await page.evaluate(()=>snapshot);assert.equal(current.phase,'recording');
  const run=current.run,boot=current.boot;emit('started',{run,boot,seconds:30});
  const deadline=Date.now()+150000;let screenshot=false,liveChecks=false,next=0;
  while(Date.now()<deadline){
   current=await page.evaluate(()=>snapshot);assert.equal(current.run,run);assert.equal(current.boot,boot);
   assert(!['failed','rejected'].includes(current.phase),current.detail);
   if(Date.now()>=next){emit('state',current);next=Date.now()+5000;}
   const live=await page.evaluate(()=>({raw:latestRaw.length,windows:latestTrend.length,ina:snapshot.ina_valid,stamp:snapshot.ina_us}));
    if(current.phase==='recording'&&live.raw===272&&live.windows>=20&&live.ina&&live.stamp>0){
    liveChecks=true;
    if(!screenshot){await page.screenshot({path:path.join(folder,'actual-recording.png'),fullPage:true});screenshot=true;emit('live_signals',live);}
   }
   if(current.phase==='passed')break;
   await delay(200);
  }
  assert.equal(current.phase,'passed');assert.equal(current.errors,0);assert(liveChecks,'no simultaneous raw/RMS/INA live evidence');
  assert(current.elapsed_us>=29000000&&current.elapsed_us<=32000000);
  assert(/^[a-f0-9]{64}$/.test(current.sha256));assert(current.written>30*25333);
  const trend=await page.evaluate(async()=>{const r=await fetch('/api/trend',{cache:'no-store'});if(!r.ok)throw Error('trend HTTP');return r.json();});
  assert.equal(trend.boot,boot);assert.equal(trend.run,run);assert.equal(trend.sample_rate,26667);assert.equal(trend.window_samples,5333);
  assert(trend.points.length>=140&&trend.points.length<=150);
  trend.points.forEach((p,i)=>{assert.equal(p.length,5);assert(p.every(v=>Number.isSafeInteger(v)&&v>=0));if(i)assert.equal(p[0]-trend.points[i-1][0],5333);});
  assert(trend.points.at(-1)[0]<=current.written);assert(current.written-trend.points.at(-1)[0]<5333);
  const after=await page.evaluate(async()=>{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('status HTTP');return r.json();});
  assert.equal(after.boot,boot);assert.equal(after.run,run);assert.equal(after.phase,'passed');
  assert.equal(after.space_valid,true);assert(Number(after.free_bytes)>=0&&Number(after.free_bytes)<Number(initial.free_bytes));
  const capacityView=await page.evaluate(()=>({text:$('remaining').textContent,bytes:snapshot.free_bytes,valid:snapshot.space_valid}));
  assert.equal(capacityView.valid,true);assert.equal(capacityView.text,(Number(capacityView.bytes)/1e9).toFixed(3)+' GB');
  for(const span of ['1','5','10']){
    await page.locator('[data-span="'+span+'"]').click();
    assert.deepEqual(await page.evaluate(()=>[views.get($('wave')).min,views.get($('wave')).max,views.get($('trend')).min,views.get($('trend')).max]),[-2000,2000,0,2000]);
    await page.locator('#wave').focus();await page.locator('#wave').press('End');
    assert(await page.locator('.raw-plot .chart-tip').isVisible());
    assert.match(await page.locator('.raw-plot .chart-tip').innerText(),/原始样本/);
    await page.locator('#wave').press('Escape');
  }
  assert.deepEqual(errors,[]);await page.screenshot({path:path.join(folder,'actual-passed.png'),fullPage:true});
  const result={passed:true,hardware_accessed:true,simulated:false,url,run,final:current,trend,free_before:initial.free_bytes,free_after:after.free_bytes,live_signals_verified:liveChecks,page_errors:errors,browser:await browser.version(),events};
  fs.writeFileSync(path.join(folder,'result.json'),JSON.stringify(result,null,2));emit('passed',{run,samples:current.written,windows:trend.points.length,sha256:current.sha256});
 }finally{if(browser)await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
