/* Browser checks against a local mock only. No serial, WiFi device or TF access.
   Run with Node and Playwright available in NODE_PATH. Screenshots are labeled simulated. */
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'local-data', 'validation', 'sensor-web-ui');
fs.mkdirSync(output, {recursive:true});
const boot='abcdefabcdefabcdefabcdefabcdefab';
let s={boot,phase:'idle',ready:true,connected:true,ip:'192.168.1.23',card_serial:'10044BA5',card_bytes:'7948206080',space_valid:true,free_bytes:'6144000000',
  run:'',seconds:0,written:0,elapsed_us:0,errors:0,queue:0,fifo_high:0,queue_high:0,write_us:0,sync_us:0,free_heap:150000,
  ina_valid:false,ina_us:0,bus_mv:0,current_ua:0,power_uw:0,detail:'软件模拟状态；非实机数据',sha256:'',preview_seq:0,preview_us:0,preview:[]};
let posts=[], unavailable=false, requests=0, outstanding=0,maxOutstanding=0,delayAck=false;
let trendUnavailable=false,trendRun=null,trendBoot=null,trendRange=null,trendScale=null,trendDelay=0;
const html=fs.readFileSync(path.join(root,'面包板样机_固件/diagnostics/sensor_web_recorder/src/index.html'),'utf8').replace('{{KEY}}',boot);
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
    const response={boot:trendBoot===null?s.boot:trendBoot,run,sample_rate:26667,window_samples:5333,
      range_g:trendRange===null?s.range_g:trendRange,scale_micro_g:trendScale===null?s.scale_micro_g:trendScale,
      points:run?Array.from({length:150},(_,i)=>[(i+1)*5333,12000+i*10,14000+i*8,16000+i*6,i===40?2:0]):[]};
    if(trendDelay)await new Promise(r=>setTimeout(r,trendDelay));
    res.end(JSON.stringify(response));return;
  }
  if(req.method==='POST'){
    let body='';for await(const data of req)body+=data;
    posts.push({url:req.url,key:req.headers['x-record-key'],body});
    assert.equal(req.headers['x-record-key'],boot);
    if(req.url==='/api/start'){
      assert(!['queued','recording'].includes(s.phase));
      const r=JSON.parse(body);assert.equal(r.card_serial,s.card_serial);assert.equal(r.card_bytes,s.card_bytes);
      assert.match(r.run,/^[0-9A-F]{8}$/);assert([2,8,16].includes(r.range_g));
      s={...s,run:r.run,seconds:r.seconds,range_g:r.range_g,scale_micro_g:({2:61,8:244,16:488})[r.range_g],phase:'queued'};
    }
    if(delayAck)await new Promise(r=>setTimeout(r,3000));
    res.setHeader('Content-Type','application/json');res.end('{"accepted":true}');return;
  }
  res.statusCode=404;res.end();
});
async function main(){
  await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const address='http://127.0.0.1:'+server.address().port;
  const browser=await chromium.launch({headless:true,channel:process.env.WEB_TEST_BROWSER||'msedge'});
  const context=await browser.newContext({viewport:{width:1360,height:1080}});
  let maxPageStatus=0;
  await context.exposeBinding('reportStatusConcurrency',(_source,count)=>{maxPageStatus=Math.max(maxPageStatus,count);});
  await context.addInitScript(()=>{
    const original=window.fetch;let inFlight=0;
    window.fetch=async function(input,...args){
      const status=String(input)==='/api/status';
      if(status){inFlight++;window.reportStatusConcurrency(inFlight);}
      try{return await original.call(this,input,...args);}finally{if(status)inFlight--;}
    };
  });
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let assertions=0;const check=async(label,fn)=>{await fn();assertions++;console.log('PASS: '+label);};
  try{
    await page.goto(address);
    await check('startup only queries; no auto record; invalid INA blank',async()=>{
      await page.waitForFunction(()=>!document.getElementById('start').disabled);
      assert.equal(posts.length,0);assert.equal(await page.locator('#voltage').innerText(),'—');
      assert.deepEqual(await page.locator('#range option').evaluateAll(options=>options.map(o=>o.value)),['2','8','16']);
      assert.equal(await page.locator('#range').inputValue(),'2');
      assert.equal(await page.locator('label[for="range"]').count(),1);
    });
    await check('readable errors decode every flag, combinations and unknown bits without false healthy state',async()=>{
      assert.match(await page.locator('#errors').innerText(),/待机/);
      const labels=['传感器通信异常','传感器 FIFO 溢出','采样帧标识异常','采集缓冲或完整性异常','供电监测读取异常','TF 写入或同步异常','采集配置异常','停止采集异常','采样率超出允许范围','内存异常'];
      for(let bit=0;bit<labels.length;bit++){
        s.errors=1<<bit;
        await page.waitForFunction(label=>document.getElementById('errors').textContent.includes(label),labels[bit]);
        assert.equal(await page.locator('#errorCode').textContent(),'0x'+s.errors.toString(16).toUpperCase());
      }
      s.errors=2|32|1024;
      await page.waitForFunction(()=>document.getElementById('errors').textContent.includes('3 项'));
      assert.equal(await page.locator('#errorItems li').count(),3);
      assert.match(await page.locator('#errorItems').innerText(),/未知错误：0x400/);
      await page.locator('.metrics').screenshot({path:path.join(output,'errors-simulated.png')});
      s.errors=0;s.phase='failed';s.detail='模块准备失败（模拟）';
      await page.waitForFunction(()=>document.getElementById('errors').textContent.includes('本轮失败'));
      assert.equal(await page.locator('#errorItems li').count(),0);
      assert.match(await page.locator('#errorHint').innerText(),/模块准备失败/);
      s.phase='rejected';await page.waitForFunction(()=>document.getElementById('errors').textContent.includes('请求未执行'));
      s.phase='idle';await page.waitForFunction(()=>document.getElementById('errors').textContent.includes('待机'));
    });
    await check('invalid duration rejects locally',async()=>{
      await page.locator('#seconds').fill('301');await page.locator('#start').click();assert.equal(posts.length,0);
    });
    await check('duration shortcuts only set input; no POST',async()=>{
      for(const value of ['30','60','90']){
        await page.locator('[data-seconds="'+value+'"]').click();
        assert.equal(await page.locator('#seconds').inputValue(),value);assert.equal(posts.length,0);
      }
    });
    await check('double click creates one request and reserves run',async()=>{
      await page.locator('#seconds').fill('300');
      await page.evaluate(()=>{document.getElementById('start').click();document.getElementById('start').click();});
      await page.waitForFunction(()=>document.getElementById('phase').textContent==='请求已排队');
      assert.equal(posts.length,1);assert.equal(await page.locator('#start').isDisabled(),true);
    });
    s={...s,phase:'recording',written:2630000,elapsed_us:100000000,queue:4,write_us:155434,sync_us:111534,
      ina_valid:true,ina_us:100000000,bus_mv:3290,current_ua:750,power_uw:2500,preview_seq:1,preview_us:99997000,
      preview:Array.from({length:272},(_,i)=>[Math.round(Math.sin(i/7)*5000),Math.round(Math.cos(i/9)*3000),16384+Math.round(Math.sin(i/3)*1500)])};
    await page.waitForFunction(()=>document.getElementById('phase').textContent==='正在录制');
    await check('recording shows actual server state and preview',async()=>{
      assert.equal(await page.locator('#voltage').innerText(),'3.290');
      assert.match(await page.locator('#previewLabel').innerText(),/源片段 272 点/);
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(await page.locator('#stop').isEnabled(),true);
    });
    await check('continuous trend and near-full-scale flag; duration locked while busy',async()=>{
      await page.waitForFunction(()=>document.getElementById('trendLabel').textContent.includes('150 个完整窗口'));
      assert.match(await page.locator('#trendLabel').innerText(),/近满量程 2/);
      assert(await page.locator('[data-seconds="30"]').isDisabled());
      assert.equal(await page.locator('#scenario').count(),0);
      assert.equal(await page.locator('.prototype').count(),0);
    });
    await check('fixed common scales survive amplitude changes and all time modes',async()=>{
      for(const span of ['1','5','10']){
        await page.locator('[data-span="'+span+'"]').click();
        assert.deepEqual(await page.evaluate(()=>[views.get($('wave')).min,views.get($('wave')).max,views.get($('trend')).min,views.get($('trend')).max]),[-2000,2000,0,2000]);
      }
      await page.evaluate(()=>{shownRaw=[[1,2,3],[32767,-32768,1000]];paint();});
      assert.deepEqual(await page.evaluate(()=>[views.get($('wave')).min,views.get($('wave')).max]),[-2000,2000]);
      await page.locator('[data-span="1"]').click();
    });
    await check('hover shows actual sample values; pin, keyboard and Escape work',async()=>{
      await page.locator('#wave').scrollIntoViewIfNeeded();
      const box=await page.locator('#wave').boundingBox();
      await page.mouse.move(box.x+100,box.y+80);
      await page.waitForFunction(()=>!$('wave').parentElement.querySelector('.chart-tip').hidden);
      assert.match(await page.locator('.raw-plot .chart-tip').innerText(),/原始样本/);
      await page.locator('#wave').click({position:{x:150,y:80}});
      assert.equal(await page.evaluate(()=>cursor.pinned),true);
      await page.locator('#wave').press('End');
      assert.equal(await page.evaluate(()=>cursor.fraction),1);
      const t=await page.locator('.raw-plot .chart-tip').boundingBox(),b=await page.locator('.raw-plot').boundingBox();
      assert(t.x>=b.x&&t.x+t.width<=b.x+b.width+1);
      await page.locator('#wave').press('Escape');assert(await page.locator('.raw-plot .chart-tip').isHidden());
    });
    await check('1/5/10ms use real consecutive samples with correct counts and actual tooltip values',async()=>{
      await page.locator('#pause').click();
      await page.evaluate(()=>{shownRaw=latestRaw;paint();});
      for(const [span,count] of [[1,27],[5,134],[10,267]]){
        await page.locator('[data-span="'+span+'"]').click();
        assert.match(await page.locator('#previewLabel').innerText(),new RegExp('^'+count+' 点 / '+span+' ms'));
        assert.equal(await page.evaluate(()=>views.get($('wave')).end),span);
        await page.locator('#wave').focus();await page.locator('#wave').press('End');
        const expected=await page.evaluate(n=>(shownRaw[n-1][0]*.061).toFixed(2),count);
        const tip=await page.locator('.raw-plot .chart-tip').innerText();assert.match(tip,/原始样本/);assert.deepEqual(tip.split('\n')[1].trim().split(/\s+/),['X',expected,'mg']);assert(!tip.includes('区间'));
      }
      await page.evaluate(()=>{shownRaw=shownRaw.slice(0,96);paint();});
      assert.match(await page.locator('#previewLabel').innerText(),/^96 点 \/ 10 ms.*96 点 \/ 3.56 ms/);
      assert.equal(await page.evaluate(()=>views.get($('wave')).pts.length),96);
      await page.locator('#pause').click();await page.locator('[data-span="1"]').click();
    });
    await check('remaining capacity uses valid filesystem bytes; zero differs from unavailable and legacy',async()=>{
      await page.waitForFunction(()=>$('remaining').textContent==='6.144 GB');
      for(const [valid,bytes,expected] of [[true,'0','0.000 GB'],[false,'0','—'],[true,'9999999999','—'],[true,'-1','—'],[true,'not-a-number','—'],[undefined,undefined,'—'],[true,'6144000000','6.144 GB']]){
        s.space_valid=valid;s.free_bytes=bytes;await page.waitForFunction(v=>snapshot.space_valid===v[0]&&snapshot.free_bytes===v[1],[valid,bytes]);await page.waitForFunction(v=>$('remaining').textContent===v,expected);
      }
    });
    await check('touch tap pins a readable cursor without blocking page scrolling',async()=>{
      const touch=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
      const tp=await touch.newPage();
      // Separate browser client: do not count its requests as overlap in the main client's serial polling check.
      await tp.route('**/api/status',r=>r.fulfill({contentType:'application/json',body:JSON.stringify(s)}));
      await tp.goto(address);await tp.waitForFunction(()=>shownRaw.length===272);
      await tp.locator('#wave').tap({position:{x:130,y:90}});
      assert.equal(await tp.evaluate(()=>cursor.pinned),true);
      assert(await tp.locator('.raw-plot .chart-tip').isVisible());
      assert.equal(await tp.locator('#wave').evaluate(e=>getComputedStyle(e).touchAction),'auto');await touch.close();
    });
    await check('pause freezes plots; raw zoom only changes view; resume gets newest sample',async()=>{
      await page.locator('#pause').click();
      const old=await page.locator('#previewLabel').innerText();
      s.preview_seq++;s.preview_us+=1000000;
      await new Promise(r=>setTimeout(r,500));assert.equal(await page.locator('#previewLabel').innerText(),old);
      await page.locator('[data-span="1"]').click();
      assert.equal(await page.locator('[data-span="1"]').getAttribute('aria-pressed'),'true');
      await page.locator('#pause').click();
      await page.waitForFunction(v=>document.getElementById('previewLabel').textContent!==v,old);
    });
    await check('trend failure does not disable recording controls and recovers',async()=>{
      trendUnavailable=true;await page.waitForFunction(()=>document.getElementById('trendLabel').textContent.includes('趋势更新中断'));
      assert(await page.locator('#stop').isEnabled());trendUnavailable=false;
      await page.waitForFunction(()=>document.getElementById('trendLabel').textContent.includes('150 个完整窗口'));
    });
    await page.evaluate(()=>{const banner=document.createElement('div');banner.textContent='软件模拟页面 · 非实机数据';banner.style='background:#efc589;color:#201b13;padding:8px;text-align:center;font-weight:bold';document.body.prepend(banner);});
    await page.screenshot({path:path.join(output,'desktop-simulated.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await check('375/768/1024/1440 layouts fit and each plot axis has >=220px',async()=>{
      for(const width of [375,768,1024,1440]){
        await page.setViewportSize({width,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
        assert(await page.locator('#plot').evaluate(e=>(e.getBoundingClientRect().height-94)/3>=220));
      }
      await page.setViewportSize({width:390,height:844});
    });
    await page.screenshot({path:path.join(output,'mobile-simulated.png'),fullPage:true});
    await page.setViewportSize({width:1360,height:1080});
    await check('network loss disables control and reconnect never replays start',async()=>{
      unavailable=true;await page.waitForFunction(()=>document.getElementById('connection').textContent.includes('连接中断'));
      assert.equal(await page.locator('#stop').isDisabled(),true);assert.equal(posts.length,1);assert.match(await page.locator('#errors').innerText(),/连接中断/);
      unavailable=false;await page.waitForFunction(()=>!document.getElementById('stop').disabled);assert.equal(posts.length,1);
    });
    await check('stop is a separate empty request',async()=>{
      await page.locator('#stop').click();await page.waitForFunction(()=>document.getElementById('message').textContent.includes('请求已接收'));
      assert.equal(posts.length,2);assert.equal(posts[1].url,'/api/stop');assert.equal(posts[1].body,'');
    });
    await check('closing and verify are not presented as passed',async()=>{
      s.phase='verifying';await page.waitForFunction(()=>document.getElementById('phase').textContent==='完整回读校验');
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(await page.locator('#stop').isDisabled(),true);
    });
    await check('only confirmed result enables next run; stale INA labeled',async()=>{
      s.phase='passed';s.sha256='a'.repeat(64);await page.waitForFunction(()=>!document.getElementById('start').disabled);
      assert.match(await page.locator('#inaState').innerText(),/最后快照/);
      await page.locator('details').filter({has:page.locator('#sha')}).locator('summary').click();
      assert.match(await page.locator('#sha').innerText(),/a{64}/);
    });
    await check('refresh reconnect queries without replay',async()=>{
      await page.reload();await page.waitForFunction(()=>!document.getElementById('start').disabled);assert.equal(posts.length,2);
    });
    await check('lost POST acknowledgement never auto-retries',async()=>{
      delayAck=true;await page.locator('#start').click();
      await page.waitForFunction(()=>document.getElementById('message').textContent.includes('结果未确认'));
      assert.equal(posts.length,3);await new Promise(r=>setTimeout(r,500));assert.equal(posts.length,3);delayAck=false;
    });
    await check('failure locks new recording',async()=>{
      s.phase='failed';await page.waitForFunction(()=>document.getElementById('phase').textContent.includes('失败'));
      assert.equal(await page.locator('#start').isDisabled(),true);
    });
    await check('reboot invalidates old page control session',async()=>{
      s.phase='idle';s.boot='f'.repeat(32);await page.waitForFunction(()=>document.getElementById('bootWarning').style.display==='block');
      assert.equal(await page.locator('#start').isDisabled(),true);assert.equal(posts.length,3);
      assert.equal(await page.evaluate(()=>shownTrend.length),0);assert.match(await page.locator('#errors').innerText(),/设备已重启/);
    });
    await check('old run/boot trend cannot populate a new empty run; missing endpoint remains optional',async()=>{
      trendRun='DEADBEEF';trendBoot=boot;s.run='CAFEBABE';s.preview=[];s.preview_seq=0;
      await page.waitForFunction(()=>lastIdentity.endsWith('/CAFEBABE'));
      await new Promise(r=>setTimeout(r,1200));assert.equal(await page.evaluate(()=>shownTrend.length),0);
      trendUnavailable=true;await new Promise(r=>setTimeout(r,1200));assert(await page.locator('#start').isDisabled());
    });
    await check('reduced motion initially freezes drawings without stopping queries',async()=>{
      await page.emulateMedia({reducedMotion:'reduce'});await page.reload();
      await page.waitForFunction(()=>document.getElementById('pause').getAttribute('aria-pressed')==='true');
      assert.equal(posts.length,3);
    });
    await check('three selectable ranges submit and scale raw/trend views and tooltips correctly',async()=>{
      trendUnavailable=false;trendRun=null;trendBoot=null;s={...s,boot,phase:'idle',run:'',range_g:2,scale_micro_g:61,preview:[],preview_seq:0};
      await page.emulateMedia({reducedMotion:'no-preference'});await page.reload();
      await page.waitForFunction(()=>!$('start').disabled);
      for(const [range,scale] of [[2,61],[8,244],[16,488]]){
        await page.waitForFunction(()=>!$('start').disabled);
        const before=posts.length;
        await page.locator('#range').selectOption(String(range));await page.locator('#start').click();
        await page.waitForFunction(()=>snapshot.phase==='queued');
        assert.equal(posts.length,before+1);assert.equal(JSON.parse(posts.at(-1).body).range_g,range);
        for(const phase of ['queued','preparing','recording','draining','closing','verifying']){
          s.phase=phase;await page.waitForFunction(p=>snapshot.phase===p,phase);
          assert(await page.locator('#range').isDisabled());
        }
        s.phase='recording';s.preview_seq++;s.preview=Array.from({length:272},()=>[1000,-1000,2000]);
        await page.waitForFunction(r=>shownRaw.length===272&&shownRange.g===r,range);
        assert.deepEqual(await page.evaluate(()=>[views.get($('wave')).min,views.get($('wave')).max,views.get($('trend')).max]),[-range*1000,range*1000,range*1000]);
        assert.match(await page.locator('#rangeState').innerText(),new RegExp('±'+range+' g'));
        assert.match(await page.locator('#rawScale').innerText(),new RegExp('±'+range*1000+' mg'));
        await page.locator('#wave').focus();await page.locator('#wave').press('Home');
        assert.deepEqual((await page.locator('.raw-plot .chart-tip').innerText()).split('\n')[1].trim().split(/\s+/),['X',scale.toFixed(2),'mg']);
        await page.waitForFunction(()=>shownTrend.length===150);
        await page.locator('#trend').focus();await page.locator('#trend').press('Home');
        assert.deepEqual((await page.locator('#plot .chart-tip').innerText()).split('\n')[1].trim().split(/\s+/),['X','12.00','mg']);
        s.phase='passed';await page.waitForFunction(()=>!$('range').disabled);
      }
    });
    await check('changing next range leaves last data, axes and paused tooltip scale unchanged',async()=>{
      await page.locator('#pause').click();const old=await page.evaluate(()=>({range:shownRange.g,scale:shownRange.scale,first:shownRaw[0]}));
      await page.locator('#range').selectOption('2');await page.locator('[data-span="5"]').click();
      assert.equal(await page.evaluate(()=>shownRange.g),old.range);
      assert.equal(await page.evaluate(()=>views.get($('wave')).max),old.range*1000);
      await page.locator('#wave').focus();await page.locator('#wave').press('Home');
      assert.match(await page.locator('.raw-plot .chart-tip').innerText(),/488\.00/);
      s.preview_seq++;s.preview=Array.from({length:272},()=>[2000,-1000,2000]);
      await page.waitForFunction(()=>latestRaw[0][0]===2000);
      assert.equal(await page.evaluate(()=>shownRaw[0][0]),old.first[0]);
      await page.locator('#pause').click();await page.waitForFunction(()=>shownRaw[0][0]===2000);
      assert.equal(await page.evaluate(()=>shownRange.g),16);
    });
    await check('unsupported selection sends no request; malformed status freezes valid scale and locks control',async()=>{
      const count=posts.length;
      await page.evaluate(()=>{const o=document.createElement('option');o.value='4';$('range').append(o);$('range').value='4';$('start').click();});
      assert.equal(posts.length,count);assert.match(await page.locator('#message').innerText(),/请选择/);
      await page.evaluate(()=>$('range').querySelector('option[value="4"]').remove());await page.locator('#range').selectOption('2');
      for(const [range,scale] of [[4,122],[16,61],[16,undefined],[undefined,488]]){
        s.range_g=range;s.scale_micro_g=scale;
        await page.waitForFunction(()=>!online);
        assert(await page.locator('#range').isDisabled());assert.equal(await page.evaluate(()=>shownRange.g),16);
      }
      s.range_g=16;s.scale_micro_g=488;await page.waitForFunction(()=>online);
      s.range_g=8;s.scale_micro_g=244;await page.waitForFunction(()=>!online);
      assert.equal(await page.evaluate(()=>shownRange.g),16);
      s.range_g=16;s.scale_micro_g=488;await page.waitForFunction(()=>online);
    });
    await check('wrong-range and old-run trends never replace current run history',async()=>{
      const old=await page.evaluate(()=>JSON.stringify(latestTrend));trendRange=2;trendScale=61;
      await page.waitForFunction(()=>trendError.length>0);
      assert.equal(await page.evaluate(()=>JSON.stringify(latestTrend)),old);
      trendRange=null;trendScale=null;await page.waitForFunction(()=>!trendError);
      await page.locator('#pause').click();
      trendRun=s.run;trendDelay=600;await page.evaluate(()=>{nextTrend=0;});
      await new Promise(r=>setTimeout(r,150));
      s={...s,run:'FACE1234',range_g:8,scale_micro_g:244,preview:[],preview_seq:0};
      await page.waitForFunction(()=>lastIdentity.endsWith('/FACE1234'));
      assert.equal(await page.evaluate(()=>shownRaw.length),0);
      assert.equal(await page.evaluate(()=>shownRange.g),8);
      trendDelay=0;trendRun='DEADBEEF';await new Promise(r=>setTimeout(r,1200));
      assert.equal(await page.evaluate(()=>shownTrend.length),0);trendRun=null;
      assert.equal(await page.evaluate(()=>paused),true);await page.locator('#pause').click();
      await page.waitForFunction(()=>shownTrend.length===150);
    });
    await check('range locks while POST pending, disconnected and after reboot',async()=>{
      s.phase='passed';await page.waitForFunction(()=>!$('start').disabled);await page.locator('#range').selectOption('8');
      delayAck=true;await page.locator('#start').click();assert(await page.locator('#range').isDisabled());
      await page.waitForFunction(()=>!pending);delayAck=false;s.phase='passed';await page.waitForFunction(()=>!$('range').disabled);
      unavailable=true;await page.waitForFunction(()=>!online);assert(await page.locator('#range').isDisabled());
      unavailable=false;await page.waitForFunction(()=>online);s.boot='e'.repeat(32);
      await page.waitForFunction(()=>stale);assert(await page.locator('#range').isDisabled());
    });
    assert.equal(maxPageStatus,1);assert.deepEqual(errors,[]);
    const report={simulated_only:true,hardware_accessed:false,browser:await browser.version(),playwright:require('playwright/package.json').version,
      assertions,posts:posts.length,status_requests:requests,max_parallel_status:maxPageStatus,max_server_status_across_reloads:maxOutstanding,page_errors:errors,
      screenshots:['desktop-simulated.png','mobile-simulated.png']};
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));
  }finally{await browser.close();server.close();}
}
main().catch(e=>{console.error(e);server.close();process.exitCode=1;});
