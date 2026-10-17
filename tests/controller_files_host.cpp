// Actual firmware file queue/operations with host adapters. This is not a real-card acceptance test.
#include "device_v2.h"
#include "ff.h"
#include <cassert>
#include <cstring>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
namespace fs=std::filesystem;
int64_t hostTime=1;
static bool reserved=false,recording=false,writable=false,failSync=false;
static unsigned releases=0;
int f_open(FIL* f,const char* p,unsigned flags){if(flags&FA_WRITE){assert(writable);if(fs::exists(p))return FR_ERR;}f->f=fopen(p,flags&FA_WRITE?"wb":"rb");return f->f?FR_OK:FR_ERR;}
int f_close(FIL* f){int rc=fclose(f->f);f->f=nullptr;return rc?FR_ERR:FR_OK;}
int f_sync(FIL* f){return failSync||fflush(f->f)?FR_ERR:FR_OK;}
int f_read(FIL* f,void* b,UINT n,UINT* got){*got=fread(b,1,n,f->f);return ferror(f->f)?FR_ERR:FR_OK;}
int f_write(FIL* f,const void* b,UINT n,UINT* got){assert(writable);*got=fwrite(b,1,n,f->f);return *got==n?FR_OK:FR_ERR;}
int f_stat(const char* p,FILINFO* info){std::error_code ec;if(!fs::exists(p,ec))return FR_NO_FILE;snprintf(info->fname,sizeof(info->fname),"%s",fs::path(p).filename().c_str());info->fattrib=fs::is_directory(p)?AM_DIR:0;info->fsize=info->fattrib?0:fs::file_size(p);return FR_OK;}
int f_opendir(FF_DIR* d,const char* p){std::error_code ec;if(!fs::is_directory(p,ec))return FR_NO_FILE;d->entries.clear();d->at=0;for(auto e:fs::directory_iterator(p))d->entries.push_back(e);return FR_OK;}
int f_readdir(FF_DIR* d,FILINFO* info){if(d->at>=d->entries.size()){info->fname[0]=0;return FR_OK;}auto e=d->entries[d->at++];auto name=e.path().filename().string();snprintf(info->fname,sizeof(info->fname),"%s",name.c_str());return f_stat(e.path().c_str(),info);}
int f_closedir(FF_DIR* d){d->entries.clear();return FR_OK;}
int f_mkdir(const char* p){assert(writable);std::error_code ec;return fs::create_directory(p,ec)?FR_OK:FR_ERR;}
int f_unlink(const char* p){assert(writable);std::error_code ec;return fs::remove(p,ec)?FR_OK:FR_ERR;}
int f_rename(const char* a,const char* b){assert(writable);if(fs::exists(b))return FR_ERR;std::error_code ec;fs::rename(a,b,ec);return ec?FR_ERR:FR_OK;}
uint64_t f_size(FIL* f){auto at=ftell(f->f);fseek(f->f,0,SEEK_END);auto n=ftell(f->f);fseek(f->f,at,SEEK_SET);return n;}
int f_getfree(const char*,DWORD* clusters,FATFS** fs){static FATFS card;*fs=&card;*clusters=1000000;return FR_OK;}
namespace recorder {void* idleWorkspace(size_t n){alignas(16) static unsigned char pool[32768];assert(reserved&&!recording);return n<=sizeof(pool)?pool:nullptr;}}
namespace control {
const char* sessionKey(){return "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";}
std::string statusJson(){return "{}";}std::string trendJson(){return "{}";}
devicev2::Reply sessionRequest(const char*,cJSON*){return {};}
bool busyNow(){return reserved||recording;}bool reserveFiles(){if(busyNow())return false;reserved=true;return true;}
void releaseFiles(){assert(reserved);reserved=false;++releases;}
void networkAddress(bool,const char*){}
}
namespace devicev2 {bool mountStorage(){return true;}void writeGate(bool b){writable=b;}void updateSpace(){assert(reserved);}
Reply wifiRequest(const char*,const char*,cJSON*){return {};}}
static std::string card;
static unsigned seq=1,checks=0;
static void check(bool ok,const char* name){if(!ok){std::cerr<<"FAIL "<<name<<"\n";abort();}++checks;std::cout<<"PASS "<<name<<"\n";}
static devicev2::Reply request(const std::string& op,const std::string& properties){
  char id[33];snprintf(id,sizeof(id),"%032x",seq++);
  auto body="{\"boot\":\""+std::string(control::sessionKey())+"\",\"job\":\""+id+"\""+properties+"}";
  return devicev2::dispatch("POST",("/api/v2/files/"+op).c_str(),body.c_str(),"");
}
static devicev2::Reply job(const std::string& op,const std::string& props=""){
  auto accept=request(op,props);if(accept.code!=202)return accept;
  devicev2::pollStorage(card.c_str());auto state=devicev2::filesRequest("GET","state",nullptr);
  auto parsed=cJSON_Parse(state.body.c_str());devicev2::Reply result;
  result.code=cJSON_GetObjectItem(parsed,"code")->valueint;result.body=devicev2::json(cJSON_Duplicate(cJSON_GetObjectItem(parsed,"result"),true));cJSON_Delete(parsed);return result;
}
static std::string prop(const devicev2::Reply& r,const char* key){auto j=cJSON_Parse(r.body.c_str());std::string v=devicev2::text(j,key);cJSON_Delete(j);return v;}
static std::string contents(const fs::path& p){std::ifstream file(p,std::ios::binary);return {std::istreambuf_iterator<char>(file),{}};}
int main(int argc,char** argv){
 assert(argc==2);card=argv[1];devicev2::initialize();
 auto denied=devicev2::dispatch("POST","/api/v2/files/mkdir","{}","bad");check(denied.code==403&&!reserved,"invalid boot rejected before storage");
 recording=true;check(request("list",",\"path\":\"/\"").code==409&&!reserved,"acquisition blocks all file operations");recording=false;
 check(job("mkdir",",\"path\":\"/测试\"").code==200&&fs::is_directory(card+"/测试"),"create Chinese folder");
 check(job("delete",",\"path\":\"/\",\"confirmed\":true,\"revision\":1").code==400,"root deletion rejected");
 auto begin=job("begin_upload",",\"path\":\"/测试/abc.bin\",\"bytes\":3");auto transfer=prop(begin,"transfer");
 check(begin.code==200&&reserved&&!control::reserveFiles(),"transfer retains acquisition exclusion");
 std::string token=",\"transfer\":\""+transfer+"\"";
 check(job("chunk",token+",\"offset\":1,\"data\":\"YWJj\"").code==409,"out-of-order chunk rejected without replay");
 check(job("chunk",token+",\"offset\":0,\"data\":\"YWJj\"").code==200,"ordered chunk writes");
 unsigned previous=releases;
 check(job("finish",token+",\"sha256\":\"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\"").code==200&&releases==previous+1&&!reserved,"verified upload releases storage exactly once");
 check(contents(card+"/测试/abc.bin")=="abc","published bytes match source");
 check(job("begin_upload",",\"path\":\"/测试/abc.bin\",\"bytes\":3").code==409&&contents(card+"/测试/abc.bin")=="abc","upload never overwrites existing file");
 check(job("rename",",\"path\":\"/测试/abc.bin\",\"destination\":\"/测试/重命名.bin\"").code==200,"rename Chinese filename");
 check(job("copy",",\"path\":\"/测试\",\"destination\":\"/测试/inside\"").code==400,"recursive copy into own tree rejected");
 check(job("copy",",\"path\":\"/测试\",\"destination\":\"/副本\"").code==200&&contents(card+"/副本/重命名.bin")=="abc","directory copy commits verified destination");
 check(job("copy",",\"path\":\"/测试\",\"destination\":\"/副本\"").code==409,"copy never overwrites existing directory");
 begin=job("begin_read",",\"path\":\"/副本/重命名.bin\"");transfer=prop(begin,"transfer");token=",\"transfer\":\""+transfer+"\"";
 check(prop(job("read",token),"data")=="YWJj"&&reserved,"download reads bounded chunk while holding exclusion");
 check(job("read",token).code==200&&!reserved,"download EOF finalizes and releases");
 begin=job("begin_upload",",\"path\":\"/bad.bin\",\"bytes\":3");transfer=prop(begin,"transfer");token=",\"transfer\":\""+transfer+"\"";job("chunk",token+",\"offset\":0,\"data\":\"YWJj\"");
 check(job("finish",token+",\"sha256\":\"wrong\"").code==422&&!fs::exists(card+"/bad.bin")&&!reserved,"digest mismatch leaves partial and does not publish");
 begin=job("begin_upload",",\"path\":\"/sync.bin\",\"bytes\":0");token=",\"transfer\":\""+prop(begin,"transfer")+"\"";failSync=true;
 check(job("finish",token+",\"sha256\":\"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\"").code==422&&!fs::exists(card+"/sync.bin"),"sync failure prevents publish");failSync=false;
 begin=job("begin_upload",",\"path\":\"/cancel.bin\",\"bytes\":1");token=",\"transfer\":\""+prop(begin,"transfer")+"\"";
 check(job("cancel",token).code==409&&!reserved&&!fs::exists(card+"/cancel.bin"),"cancel closes transfer and retains only partial");
 begin=job("begin_upload",",\"path\":\"/timeout.bin\",\"bytes\":1");hostTime+=60000001;devicev2::pollStorage(card.c_str());check(!reserved&&!writable,"abandoned transfer expires and disables writes");
 auto measurement=job("measure",",\"path\":\"/副本\"");auto parsed=cJSON_Parse(measurement.body.c_str());int revision=cJSON_GetObjectItem(parsed,"revision")->valueint;cJSON_Delete(parsed);
 check(job("delete",",\"path\":\"/副本\",\"confirmed\":true,\"revision\":0").code==409&&fs::exists(card+"/副本"),"stale deletion confirmation rejected");
 check(job("delete",",\"path\":\"/副本\",\"confirmed\":true,\"revision\":"+std::to_string(revision)).code==200&&!fs::exists(card+"/副本"),"confirmed recursive delete removes only target");
 check(!writable&&!reserved&&fs::exists(card+"/测试/重命名.bin"),"all jobs leave gate closed and preserve other files");
 std::cout<<"RESULT "<<checks<<" file/router checks; temporary host filesystem, not actual FatFS or card\n";
}
