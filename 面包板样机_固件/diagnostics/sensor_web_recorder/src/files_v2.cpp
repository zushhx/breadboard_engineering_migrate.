#include "device_v2.h"
#include "mutex_lock.h"
#include "file_policy.h"
#include "session_model.h"
#include "recorder.h"
#include <new>
#include "ff.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include <atomic>
#include <cstring>
#include <strings.h>
namespace devicev2 {
bool mountStorage();
void writeGate(bool enabled);
void updateSpace();
static SemaphoreHandle_t mutex;

static bool queued=false,running=false;
static std::string command,action,result="{\"idle\":true}";
static int resultCode=200;
static char job[33]={},transfer[17]={};
static std::atomic<bool> cancel(false);
static uint64_t progressBytes=0,transferSize=0,transferOffset=0;
static uint32_t revision=0;
static int64_t transferSeen=0;
static std::atomic<bool> transferOpen(false);
static bool writing=false;
static std::string transferTemp,transferFinal;
static mbedtls_sha256_context transferHash;
struct Workspace { FIL file,other; FF_DIR directories[9]; FILINFO infos[9]; uint8_t block[2048],verifyBlock[2048]; };
static_assert(sizeof(Workspace)<=32768,"File workspace must fit within the idle acquisition pool");
static Workspace* work;
// Resolve existing parents through FatFS so long/short-name aliases cannot bypass the descendant guard.
static std::string canonical(const char* drive,const std::string& path) {
  std::string resolved; size_t from=1;
  while(from<path.size()) {
    size_t to=path.find('/',from); if(to==std::string::npos)to=path.size();
    std::string probe=std::string(drive)+resolved+"/"+path.substr(from,to-from);
    FILINFO& info=work->infos[0]; if(f_stat(probe.c_str(),&info)!=FR_OK||!info.fname[0])return "";
    resolved+="/";resolved+=info.fname;from=to+1;
  }
  return resolved.empty()?"/":resolved;
}
static std::string hashFinish(mbedtls_sha256_context* ctx) {
  uint8_t digest[32]; mbedtls_sha256_finish(ctx,digest); char value[65];
  for(unsigned i=0;i<32;i++) {snprintf(value+i*2,3,"%02x",digest[i]);}
  return value;
}
static std::string hashFile(const std::string& path) {
  if(f_open(&work->other,path.c_str(),FA_READ)!=FR_OK) return "";
  mbedtls_sha256_context hash; mbedtls_sha256_init(&hash); mbedtls_sha256_starts(&hash,0);
  bool ok=true; UINT n=0;
  do { if(f_read(&work->other,work->verifyBlock,sizeof(work->verifyBlock),&n)!=FR_OK) {ok=false;break;} if(cancel.load()) {ok=false;break;} if(n) {mbedtls_sha256_update(&hash,work->verifyBlock,n);vTaskDelay(1);} } while(n);
  if(f_close(&work->other)!=FR_OK) ok=false;
  auto digest=hashFinish(&hash); mbedtls_sha256_free(&hash); return ok?digest:"";
}
static bool copyFile(const std::string& from,const std::string& to) {
  if(f_open(&work->file,from.c_str(),FA_READ)!=FR_OK) return false;
  if(f_open(&work->other,to.c_str(),FA_WRITE|FA_CREATE_NEW)!=FR_OK) {f_close(&work->file);return false;}
  mbedtls_sha256_context hash; mbedtls_sha256_init(&hash); mbedtls_sha256_starts(&hash,0);
  bool ok=true; UINT count=0,written=0;
  do {
    if(cancel.load()||f_read(&work->file,work->block,sizeof(work->block),&count)!=FR_OK) {ok=false;break;}
    if(count) {
      if(f_write(&work->other,work->block,count,&written)!=FR_OK||written!=count) {ok=false;break;}
      mbedtls_sha256_update(&hash,work->block,count); {MutexLock lock(mutex); progressBytes+=count;} vTaskDelay(1);
    }
  } while(count);
  if(f_sync(&work->other)!=FR_OK) ok=false;
  if(f_close(&work->file)!=FR_OK) ok=false;
  if(f_close(&work->other)!=FR_OK) ok=false;
  auto source=hashFinish(&hash); mbedtls_sha256_free(&hash);
  return ok&&hashFile(to)==source;
}
static bool tree(const std::string& from,const std::string& to,unsigned depth,int operation,uint64_t& bytes,unsigned& count) {
  if(depth>8||cancel.load()) return false;
  FILINFO& info=work->infos[depth];
  if(f_stat(from.c_str(),&info)!=FR_OK) return false;
  bool directory=info.fattrib&AM_DIR;
  if(!directory) {
    bytes+=info.fsize; ++count;
    if(operation==0) return true;
    if(operation==1) return copyFile(from,to);
    return f_unlink(from.c_str())==FR_OK;
  }
  if(operation==1&&f_mkdir(to.c_str())!=FR_OK) return false;
  FF_DIR& dir=work->directories[depth];
  if(f_opendir(&dir,from.c_str())!=FR_OK) return false;
  bool ok=true;
  while(true) {
    if(f_readdir(&dir,&info)!=FR_OK) {ok=false;break;}
    if(!info.fname[0]) break;
    if(!strcmp(info.fname,".")||!strcmp(info.fname,"..")) continue;
    std::string name=info.fname;
    if(!tree(from+"/"+name,to+"/"+name,depth+1,operation,bytes,count)) {ok=false;break;}
  }
  if(f_closedir(&dir)!=FR_OK) ok=false;
  if(ok&&operation==2) ok=f_unlink(from.c_str())==FR_OK;
  return ok;
}
static Reply finishTransfer(bool commit,const char* expected) {
  bool wasWriting=writing,ok=true;
  if(wasWriting&&f_sync(&work->file)!=FR_OK) ok=false;
  if(f_close(&work->file)!=FR_OK) ok=false;
  auto digest=hashFinish(&transferHash); mbedtls_sha256_free(&transferHash); transferOpen=false;
  if(commit&&wasWriting) {
    ok=ok&&transferOffset==transferSize&&strlen(expected)==64&&!strcasecmp(digest.c_str(),expected);
    if(ok) ok=hashFile(transferTemp)==digest;
    FILINFO target; if(f_stat(transferFinal.c_str(),&target)!=FR_NO_FILE) ok=false;
    if(ok) ok=f_rename(transferTemp.c_str(),transferFinal.c_str())==FR_OK;
    if(ok) ++revision;
  }
  writeGate(false);
  if(!commit) return error(409,"传输已取消，未完成的临时文件保留");
  if(!ok) return error(422,"文件关闭或完整性校验失败，临时文件保留");
  auto out=cJSON_CreateObject(); cJSON_AddStringToObject(out,"sha256",digest.c_str()); cJSON_AddNumberToObject(out,"bytes",transferOffset);
  Reply reply; reply.body=json(out); return reply;
}
static Reply execute(const char* drive,const std::string& op,cJSON* a) {
  std::string path=text(a,"path"),destination=text(a,"destination");
  auto physical=[drive](const std::string& p){return std::string(drive)+p;};
  if(op=="cancel") return transferOpen?finishTransfer(false,""):error(409,"没有传输任务");
  if(op=="chunk"||op=="finish"||op=="read") {
    if(!transferOpen||strcmp(text(a,"transfer"),transfer)) return error(409,"传输会话已变化");
    transferSeen=esp_timer_get_time();
    if(op=="finish") return finishTransfer(true,text(a,"sha256"));
    if(op=="chunk") {
      if(!writing) return error(409,"当前传输不是上传");
      auto offset=cJSON_GetObjectItem(a,"offset"); if(!cJSON_IsNumber(offset)||offset->valuedouble<0||offset->valuedouble>4294967295.0||offset->valuedouble!=static_cast<double>(transferOffset)) return error(409,"分块顺序已变化，不自动重放");
      const char* data=text(a,"data"); size_t n=0;
      if(strlen(data)>2800||mbedtls_base64_decode(work->block,sizeof(work->block),&n,(const uint8_t*)data,strlen(data))!=0||!n||transferOffset+n>transferSize) return error(400,"分块数据无效");
      UINT done=0; if(f_write(&work->file,work->block,n,&done)!=FR_OK||done!=n) return finishTransfer(false,"");
      mbedtls_sha256_update(&transferHash,work->block,n); transferOffset+=n; {MutexLock lock(mutex); progressBytes=transferOffset;}
      Reply reply; reply.body="{\"offset\":"+std::to_string(transferOffset)+"}"; return reply;
    }
    if(writing) return error(409,"当前传输不是下载");
    UINT n=0; if(f_read(&work->file,work->block,sizeof(work->block),&n)!=FR_OK) return finishTransfer(false,"");
    if(n) mbedtls_sha256_update(&transferHash,work->block,n);
    transferOffset+=n; {MutexLock lock(mutex); progressBytes=transferOffset;}
    char encoded[2800]={}; size_t length=0; mbedtls_base64_encode((uint8_t*)encoded,sizeof(encoded),&length,work->block,n);
    auto out=cJSON_CreateObject(); cJSON_AddStringToObject(out,"data",encoded); cJSON_AddBoolToObject(out,"done",n==0);
    cJSON_AddNumberToObject(out,"offset",transferOffset);
    if(n==0) {
      auto end=finishTransfer(true,""); auto meta=cJSON_Parse(end.body.c_str()); cJSON_AddItemToObject(out,"verified",meta);
      if(end.code!=200) {cJSON_Delete(out);return end;}
    }
    Reply reply; reply.body=json(out); return reply;
  }
  if(!validPath(path,op=="list")) return error(400,"TF路径无效");
  if(!mountStorage()) return error(503,"TF挂载失败");
  std::string src=physical(path);
  if(op=="list") {
    int skip=0; auto v=cJSON_GetObjectItem(a,"offset"); if(v&&!integer(a,"offset",0,100000,skip)) return error(400,"目录偏移无效");
    FF_DIR& dir=work->directories[0]; FILINFO& info=work->infos[0]; if(f_opendir(&dir,src.c_str())!=FR_OK) return error(404,"目录不存在");
    auto out=cJSON_CreateObject(),entries=cJSON_CreateArray(); unsigned index=0,shown=0; bool more=false,ok=true;
    while(true) {
      if(f_readdir(&dir,&info)!=FR_OK) {ok=false;break;} if(!info.fname[0]) break;
      if(!strcmp(info.fname,".")||!strcmp(info.fname,"..")) continue;
      if(index++<static_cast<unsigned>(skip)) continue;
      if(shown==16) {more=true;break;}
      auto e=cJSON_CreateObject(); cJSON_AddStringToObject(e,"name",info.fname); cJSON_AddBoolToObject(e,"directory",info.fattrib&AM_DIR);
      cJSON_AddNumberToObject(e,"bytes",info.fsize); cJSON_AddItemToArray(entries,e); ++shown;
    }
    if(f_closedir(&dir)!=FR_OK) ok=false;
    if(!ok) {cJSON_Delete(out);cJSON_Delete(entries);return error(500,"目录读取失败");}
    cJSON_AddItemToObject(out,"entries",entries); cJSON_AddBoolToObject(out,"more",more); cJSON_AddNumberToObject(out,"next",skip+shown);
    Reply reply; reply.body=json(out); return reply;
  }
  FILINFO& info=work->infos[0];
  if(op=="measure") {
    uint64_t bytes=0; unsigned count=0; if(!tree(src,"",0,0,bytes,count)) return error(500,"目录统计失败或超过8层");
    auto out=cJSON_CreateObject(); cJSON_AddNumberToObject(out,"bytes",bytes); cJSON_AddNumberToObject(out,"files",count); cJSON_AddNumberToObject(out,"revision",revision);
    Reply reply; reply.body=json(out); return reply;
  }
  // Failed writes can still leave partial entries, so confirmations must be invalidated before attempting mutations.
  if(op!="begin_read"&&op!="delete") ++revision;
  writeGate(op!="begin_read");
  if(op=="mkdir") {if(f_mkdir(src.c_str())!=FR_OK) return error(409,"新建失败，目录可能已存在"); ++revision;}
  else if(op=="rename"||op=="copy") {
    if(!validPath(destination)||nestedCopy(path,destination)) return error(400,"目标路径无效或在源目录内部");
    auto sourceName=canonical(drive,path); auto split=destination.find_last_of('/');
    auto parentName=canonical(drive,split?destination.substr(0,split):"/");
    if(sourceName.empty()||parentName.empty()||nestedCopy(sourceName,(parentName=="/"?"":parentName)+destination.substr(split))) return error(400,"目标父目录无效或通过别名位于源目录内部");
    std::string dst=physical(destination); if(f_stat(dst.c_str(),&info)!=FR_NO_FILE) return error(409,"目标已存在，不覆盖文件");
    if(op=="rename") {if(f_rename(src.c_str(),dst.c_str())!=FR_OK) return error(500,"重命名失败");}
    else {
      std::string temporary=dst+".copy-"+std::string(job,8)+".partial"; uint64_t bytes=0; unsigned count=0;
      if(!tree(src,temporary,0,1,bytes,count)||f_rename(temporary.c_str(),dst.c_str())!=FR_OK) return error(500,"复制失败或已取消，未完成副本保留");
    }
    ++revision;
  } else if(op=="delete") {
    int expected=0; if(!cJSON_IsTrue(cJSON_GetObjectItem(a,"confirmed"))||!integer(a,"revision",0,2147483647,expected)||static_cast<uint32_t>(expected)!=revision) return error(409,"未确认删除，或文件列表已变化");
    uint64_t bytes=0; unsigned count=0; bool ok=tree(src,"",0,2,bytes,count); ++revision;
    if(!ok) return error(500,"删除未完全完成，请刷新检查剩余文件");
  } else if(op=="begin_upload"||op=="begin_read") {
    writing=op=="begin_upload";
    if(writing&&f_stat(src.c_str(),&info)!=FR_NO_FILE) return error(409,"目标已存在，不覆盖文件");
    transferFinal=src; transferTemp=src+".upload-"+std::string(job,8)+".partial";
    transferSize=0; transferOffset=0;
    if(writing) {
      auto size=cJSON_GetObjectItem(a,"bytes");
      if(!cJSON_IsNumber(size)||size->valuedouble<0||size->valuedouble>4294967295.0||size->valuedouble!=static_cast<double>(static_cast<uint64_t>(size->valuedouble))) return error(400,"FAT32单文件上限4GiB减1字节");
      transferSize=static_cast<uint64_t>(size->valuedouble);
      DWORD freeClusters=0; FATFS* fs=nullptr;
      if(f_getfree(drive,&freeClusters,&fs)!=FR_OK||!fs||uint64_t(freeClusters)*fs->csize*512<transferSize+2097152) return error(507,"TF剩余空间不足");
      if(f_open(&work->file,transferTemp.c_str(),FA_WRITE|FA_CREATE_NEW)!=FR_OK) return error(500,"无法创建上传临时文件");
    } else {
      writeGate(false); if(f_open(&work->file,src.c_str(),FA_READ)!=FR_OK) return error(404,"文件不存在");
      transferSize=f_size(&work->file);
    }
    {MutexLock lock(mutex);
      snprintf(transfer,sizeof(transfer),"%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random());
      transferOpen=true;
    }
    transferSeen=esp_timer_get_time(); mbedtls_sha256_init(&transferHash); mbedtls_sha256_starts(&transferHash,0);
    auto out=cJSON_CreateObject(); cJSON_AddStringToObject(out,"transfer",transfer); cJSON_AddNumberToObject(out,"bytes",transferSize);
    Reply reply; reply.body=json(out); return reply;
  } else return error(404,"文件操作不存在");
  Reply reply; reply.body="{\"accepted\":true}"; return reply;
}
Reply filesRequest(const char* method,const char* op,cJSON* a) {
  if(!mutex) return error(503,"文件服务未初始化");
  if(!strcmp(op,"state")&&!strcmp(method,"GET")) {
    MutexLock lock(mutex); auto out=cJSON_CreateObject(); cJSON_AddStringToObject(out,"job",job); cJSON_AddBoolToObject(out,"running",running||queued);
    cJSON_AddNumberToObject(out,"bytes",progressBytes); cJSON_AddNumberToObject(out,"code",resultCode);
    cJSON_AddItemToObject(out,"result",cJSON_Parse(result.c_str())); Reply r; r.body=json(out); return r;
  }
  if(strcmp(method,"POST")) return error(405,"文件操作需POST");
  MutexLock lock(mutex);
  if(!strcmp(op,"cancel")) {
    auto requestJob=cJSON_GetObjectItem(a,"job");
    if(requestJob&&(!cJSON_IsString(requestJob)||!control::Sessions::valid(requestJob->valuestring)))
      return error(400,"文件任务编号无效");
    auto expected=cJSON_GetObjectItem(a,"expected_job");
    if(expected) {
      if(!cJSON_IsString(expected)||!control::Sessions::valid(expected->valuestring))
        return error(400,"取消目标任务编号无效");
      if(strcmp(expected->valuestring,job)) return error(409,"文件任务已变化，未取消");
    }
    auto transferValue=cJSON_GetObjectItem(a,"transfer");
    if(transferValue&&!cJSON_IsString(transferValue)) return error(400,"取消传输编号无效");
    const char* ownedTransfer=text(a,"transfer");
    if(ownedTransfer[0]&&(!transferOpen||strcmp(ownedTransfer,transfer)))
      return error(409,"传输会话已变化，未取消");
    if(!running&&!queued&&!control::Sessions::valid(text(a,"job")))
      return error(400,"文件任务编号无效");
    // Validate ownership and set the cancellation flag in one critical section.
    // Missing expected_job/transfer retains the explicit global cancel primitive.
    cancel.store(true);
    if(running||queued) {Reply r; r.code=202; r.body="{\"accepted\":true}"; return r;}
  }
  if(running||queued) return error(409,"有文件任务正在执行");
  const char* id=text(a,"job");
  if(!control::Sessions::valid(id)) return error(400,"文件任务编号无效");
  if(transferOpen) {
    if(strcmp(op,"cancel")&&strcmp(text(a,"transfer"),transfer)) return error(409,"另一个文件传输正在进行");
    if(strcmp(op,"chunk")&&strcmp(op,"finish")&&strcmp(op,"read")&&strcmp(op,"cancel")) return error(409,"请先完成或取消当前传输");
  } else if(!control::reserveFiles()) return error(409,"正在采集、保存或交接，请稍后操作文件");
  if(!transferOpen) transfer[0]=0; // A completed transfer cannot own a new job.
  snprintf(job,sizeof(job),"%s",id); command=json(cJSON_Duplicate(a,true)); action=op;
  cancel.store(!strcmp(op,"cancel")); queued=true; progressBytes=transferOffset; resultCode=202; result="{}";
  Reply r; r.code=202; r.body="{\"job\":\""+std::string(job)+"\"}"; return r;
}
void initFiles() { static StaticSemaphore_t storage; mutex=xSemaphoreCreateMutexStatic(&storage); }
void pollStorage(const char* drive) {
  if(!mutex) return;
  std::string body,op;
  {MutexLock lock(mutex);
    if(transferOpen&&!running&&!queued&&esp_timer_get_time()-transferSeen>60000000) {body="{}";op="cancel";running=true;}
    else if(queued) {body=command;op=action;queued=false;running=true;}
    else return;
  }
  Reply reply;
  void* memory=recorder::idleWorkspace(sizeof(Workspace));
  if(!memory) reply=error(503,"空闲采集池不可借用，未执行文件操作");
  else {
    if(!transferOpen) work=new(memory) Workspace{};
    auto args=cJSON_Parse(body.c_str()); reply=execute(drive,op,args); cJSON_Delete(args);
  }
  if(!transferOpen) {writeGate(false); updateSpace(); control::releaseFiles();}
  {MutexLock lock(mutex); result=reply.body; resultCode=reply.code; running=false;}
}
}
