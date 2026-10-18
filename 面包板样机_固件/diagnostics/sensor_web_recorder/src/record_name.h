#pragma once
#include <cstdio>
#include <cstring>
#include <cstdint>
namespace recorder {
constexpr unsigned kRecordNameSize = 24;
inline unsigned monthDays(unsigned year, unsigned month) {
  static constexpr unsigned days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
  return month >= 1 && month <= 12 ? days[month-1] + (month == 2 && year % 4 == 0) : 0;
}
// Explicit control-device local time YY-MM-DD-HH.mm.ss, never a device wall-clock guess.
inline bool localStamp(const char* value) {
  if (!value || strlen(value) != 17) return false;
  for (unsigned i=0;i<17;i++) {
    char separator = (i==2||i==5||i==8)?'-':(i==11||i==14)?'.':0;
    if (separator ? value[i]!=separator : value[i]<'0'||value[i]>'9') return false;
  }
  auto pair=[value](unsigned at){return unsigned(value[at]-'0')*10+unsigned(value[at+1]-'0');};
  unsigned year=pair(0),month=pair(3),day=pair(6);
  return month>=1 && month<=12 && day>=1 && day<=monthDays(year,month) && pair(9)<24 && pair(12)<60 && pair(15)<60;
}
inline bool minuteLabel(const char* stamp, uint64_t elapsedUs, char* label) {
  if (!localStamp(stamp) || elapsedUs>86400000000ULL) return false;
  auto pair=[stamp](unsigned at){return unsigned(stamp[at]-'0')*10+unsigned(stamp[at+1]-'0');};
  unsigned year=pair(0),month=pair(3),day=pair(6);
  uint64_t seconds=pair(9)*3600+pair(12)*60+pair(15)+elapsedUs/1000000;
  while (seconds>=86400) {
    seconds-=86400;
    if (++day>monthDays(year,month)) {day=1;if(++month>12){month=1;if(++year>99)return false;}}
  }
  snprintf(label,kRecordNameSize,"%02u-%02u-%02u-%02u.%02u",year,month,day,unsigned(seconds/3600),unsigned(seconds/60%60));
  return true;
}
inline bool recordingName(const char* name) {
  if (!name) return false;
  size_t n=strlen(name);
  if (n!=14 && (n<17 || n>19 || name[14]!='-')) return false;
  char stamp[18]; memcpy(stamp,name,14); memcpy(stamp+14,".00",4);
  if (!localStamp(stamp)) return false;
  if (n==14) return true;
  unsigned number=0;
  for (size_t i=15;i<n;i++) {if(name[i]<'0'||name[i]>'9')return false;number=number*10+unsigned(name[i]-'0');}
  if (number<2 || number>9999) return false;
  char canonical[kRecordNameSize]; snprintf(canonical,sizeof(canonical),"%.*s-%02u",14,name,number);
  return !strcmp(canonical,name);
}
enum class NameSlot { Free, Occupied, Error };
template<size_t D,size_t P> bool recordingPaths(const char* drive,const char* name,bool named,char (&directory)[D],char (&path)[P]) {
  int d=snprintf(directory,D,"%s/%s",drive,name);
  int p=named?snprintf(path,P,"%s/%s.BIN",directory,name):snprintf(path,P,"%s/DATA.BIN",directory);
  return d>0&&size_t(d)<D&&p>0&&size_t(p)<P;
}
template<class Inspect> bool chooseRecordingName(const char* stamp, uint64_t elapsedUs, Inspect inspect, char* name) {
  char base[kRecordNameSize]; name[0]=0;
  if (!minuteLabel(stamp,elapsedUs,base)) return false;
  for (unsigned i=1;i<=9999;i++) {
    if(i==1)snprintf(name,kRecordNameSize,"%s",base);
    else snprintf(name,kRecordNameSize,"%.14s-%02u",base,i);
    auto slot=inspect(name);
    if(slot==NameSlot::Free)return true;
    if(slot==NameSlot::Error)break;
  }
  name[0]=0;return false;
}
}
