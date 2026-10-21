#pragma once
#include <cstdint>
#include <cstddef>
namespace devicev2 {
constexpr size_t frameLimit=4096;
inline uint32_t crc32(const uint8_t* data,size_t length,uint32_t seed=0xffffffff) {
  uint32_t crc=seed;
  for(size_t i=0;i<length;i++) {crc^=data[i]; for(unsigned j=0;j<8;j++) crc=(crc>>1)^(0xedb88320u&-(crc&1));}
  return crc;
}
inline uint32_t get32(const uint8_t* p) {return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
inline void put32(uint8_t* p,uint32_t value) {for(unsigned i=0;i<4;i++) p[i]=(value>>(i*8))&255;}
inline void frameHeader(uint8_t* header,uint8_t kind,uint32_t id,const uint8_t* data,size_t length) {
  header[0]='B';header[1]='R';header[2]='C';header[3]='2';header[4]=2;header[5]=kind;header[6]=length&255;header[7]=length>>8;
  put32(header+8,id); put32(header+12,~crc32(data,length,crc32(header+4,8)));
}
inline bool validFrame(const uint8_t* h,const uint8_t* body) {
  size_t length=h[6]|(size_t(h[7])<<8);
  return h[0]=='B'&&h[1]=='R'&&h[2]=='C'&&h[3]=='2'&&h[4]==2&&length<=frameLimit&&get32(h+12)==~crc32(body,length,crc32(h+4,8));
}
}
