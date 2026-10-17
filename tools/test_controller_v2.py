"""Run actual v2 file/router sources against a temporary host filesystem adapter.
This exercises job/state/error paths, not ESP32 timing or real FatFS/media behavior.
Requires g++, libcjson-dev and libssl-dev. No serial or hardware operations.
"""
from pathlib import Path
import tempfile
import subprocess
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'面包板样机_固件/diagnostics/sensor_web_recorder/src'
HEADERS={
'cJSON.h':'#include <cjson/cJSON.h>\n',
'esp_timer.h':'#include <cstdint>\nextern int64_t hostTime; inline int64_t esp_timer_get_time(){return hostTime;}\n',
'esp_random.h':'#include <cstdint>\ninline uint32_t esp_random(){static uint32_t n=1;return n++;}\n',
'freertos/FreeRTOS.h':'#pragma once\n#include <mutex>\n#include <cstdint>\nusing StaticSemaphore_t=std::mutex;using SemaphoreHandle_t=std::mutex*;\n#define portMAX_DELAY 0\ninline void vTaskDelay(int){}\n',
'freertos/semphr.h':'#include "FreeRTOS.h"\ninline auto xSemaphoreCreateMutexStatic(StaticSemaphore_t* s){return s;}\ninline void xSemaphoreTake(SemaphoreHandle_t s,int){s->lock();}\ninline void xSemaphoreGive(SemaphoreHandle_t s){s->unlock();}\n',
'mbedtls/sha256.h':"""#pragma once
#include <openssl/sha.h>
using mbedtls_sha256_context=SHA256_CTX;
inline void mbedtls_sha256_init(SHA256_CTX*){}
inline void mbedtls_sha256_free(SHA256_CTX*){}
inline int mbedtls_sha256_starts(SHA256_CTX* c,int){return SHA256_Init(c);}
inline int mbedtls_sha256_update(SHA256_CTX* c,const unsigned char* b,size_t n){return SHA256_Update(c,b,n);}
inline int mbedtls_sha256_finish(SHA256_CTX* c,unsigned char* b){return SHA256_Final(b,c);}
""",
'mbedtls/base64.h':"""#pragma once
#include <openssl/evp.h>
inline int mbedtls_base64_encode(unsigned char* out,size_t cap,size_t* size,const unsigned char* in,size_t n){if(cap<4*((n+2)/3)+1)return -1;*size=EVP_EncodeBlock(out,in,n);return 0;}
inline int mbedtls_base64_decode(unsigned char* out,size_t cap,size_t* size,const unsigned char* in,size_t n){if(n%4||cap<n/4*3)return -1;int got=EVP_DecodeBlock(out,in,n);if(got<0)return -1;if(n&&in[n-1]=='=')got--;if(n>1&&in[n-2]=='=')got--;*size=got;return 0;}
""",
'ff.h':"""#pragma once
#include <cstdio>
#include <vector>
#include <filesystem>
using UINT=unsigned;using DWORD=uint32_t;
enum {FR_OK=0,FR_NO_FILE=4,FR_ERR=1,FA_READ=1,FA_WRITE=2,FA_CREATE_NEW=4,AM_DIR=16};
struct FIL{FILE* f=nullptr;};struct FF_DIR{std::vector<std::filesystem::directory_entry> entries;size_t at=0;};
struct FILINFO{uint64_t fsize=0;unsigned fattrib=0;char fname[256]={};};struct FATFS{unsigned csize=8;};
int f_open(FIL*,const char*,unsigned);int f_close(FIL*);int f_sync(FIL*);int f_read(FIL*,void*,UINT,UINT*);int f_write(FIL*,const void*,UINT,UINT*);
int f_stat(const char*,FILINFO*);int f_opendir(FF_DIR*,const char*);int f_readdir(FF_DIR*,FILINFO*);int f_closedir(FF_DIR*);
int f_mkdir(const char*);int f_unlink(const char*);int f_rename(const char*,const char*);uint64_t f_size(FIL*);int f_getfree(const char*,DWORD*,FATFS**);
"""
}
def run():
    with tempfile.TemporaryDirectory(prefix='controller-v2-host-') as folder:
        out=Path(folder);include=out/'include'
        for name,text in HEADERS.items():
            target=include/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
        common=['g++','-std=c++17','-Wall','-Wextra','-Werror','-Wno-deprecated-declarations','-I'+str(SRC),'-I'+str(include)]
        subprocess.run(common+[str(ROOT/'tests/controller_v2_test.cpp'),'-o',str(out/'model')],check=True)
        subprocess.run([str(out/'model')],check=True)
        subprocess.run(common+[str(ROOT/'tests/controller_files_host.cpp'),str(SRC/'files_v2.cpp'),str(SRC/'device_v2.cpp'),'-lcrypto','-lcjson','-o',str(out/'files')],check=True)
        card=out/'temporary-card';card.mkdir()
        subprocess.run([str(out/'files'),str(card)],check=True)
if __name__=='__main__':run()
