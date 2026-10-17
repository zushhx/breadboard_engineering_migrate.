#pragma once
#include <string>
namespace devicev2 {
inline bool validPath(const std::string& path,bool rootAllowed=false) {
  if(path=="/") return rootAllowed;
  if(path.empty()||path[0]!='/'||path.size()>240||path.back()=='/') return false;
  size_t start=1;
  for(size_t i=1;i<=path.size();i++) {
    if(i==path.size()||path[i]=='/') {
      auto part=path.substr(start,i-start);
      if(part.empty()||part=="."||part==".."||part.back()=='.'||part.back()==' ') return false;
      start=i+1;
    } else if(static_cast<unsigned char>(path[i])<32||path[i]=='\\'||path[i]==':'||path[i]=='"'||path[i]=='*'||path[i]=='?'||path[i]=='<'||path[i]=='>'||path[i]=='|') return false;
  }
  return true;
}
inline bool nestedCopy(const std::string& src,const std::string& dst) {
  return dst==src||(dst.size()>src.size()&&dst.compare(0,src.size()+1,src+"/")==0);
}
}
