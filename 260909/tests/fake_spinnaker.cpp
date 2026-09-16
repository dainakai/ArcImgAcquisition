// Test-only C API runtime. No SDK, hardware, parameter setters or transport.
#include "spin_api.hpp"
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <map>
#include <string>
#include <thread>
#include <vector>
using Api=holo::SpinApi;
using Handle=Api::Handle;
using Bool=Api::Bool;
using PixelFormat=Api::PixelFormat;
#ifdef _WIN32
#define EXPORT extern "C" __declspec(dllexport) int
#else
#define EXPORT extern "C" __attribute__((visibility("default"))) int
#endif
struct Camera;
struct Node {Camera* camera;std::string name;};
struct Camera {
    int index=0,refs=0,frames=0;bool initialized=false,acquiring=false;
    int64_t latch=0;
    std::map<std::string,Node> nodes;
};
Camera cameras[2];
std::atomic<int> images{0},processors{0},systems{0},lists{0};
int mode() {const char* v=std::getenv("HOLO_FAKE_FORMAT");return v?std::atoi(v):0;}
int64_t now(Camera* cam) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()+cam->index*1000000000LL;
}
struct Image {int format=0;uint64_t id=0,stamp=0;bool incomplete=false;std::vector<uint16_t> data=std::vector<uint16_t>(24);};
int stringOut(const std::string& value,char* out,size_t* length) {
    if(*length<value.size()+1) {*length=value.size()+1;return -1016;}
    std::memcpy(out,value.c_str(),value.size()+1);*length=value.size()+1;return 0;
}
EXPORT spinErrorGetLastMessage(char* out,size_t* length) {return stringOut("fake runtime error",out,length);}
EXPORT spinSystemGetInstance(Handle* out) {*out=cameras;++systems;for(int i=0;i<2;++i) cameras[i].index=i;return 0;}
EXPORT spinSystemReleaseInstance(Handle) {--systems;return 0;}
EXPORT spinSystemGetCameras(Handle,Handle) {return 0;}
EXPORT spinCameraListCreateEmpty(Handle* out) {*out=cameras;++lists;return 0;}
EXPORT spinCameraListDestroy(Handle) {--lists;return 0;}
EXPORT spinCameraListGetSize(Handle,size_t* out) {*out=2;return 0;}
EXPORT spinCameraListGet(Handle,size_t i,Handle* out) {if(i>=2) return -1;*out=&cameras[i];++cameras[i].refs;return 0;}
EXPORT spinCameraListClear(Handle) {return 0;}
EXPORT spinCameraInit(Handle h) {
    auto c=static_cast<Camera*>(h);
    if(std::getenv("HOLO_FAKE_INIT_FAILURE") && c->index==1) return -1001;
    c->initialized=true;return 0;
}
EXPORT spinCameraDeInit(Handle h) {auto c=static_cast<Camera*>(h);if(c->acquiring) return -1;c->initialized=false;return 0;}
EXPORT spinCameraRelease(Handle h) {--static_cast<Camera*>(h)->refs;return 0;}
EXPORT spinCameraGetNodeMap(Handle h,Handle* out) {*out=h;return 0;}
EXPORT spinCameraGetTLDeviceNodeMap(Handle h,Handle* out) {*out=h;return 0;}
EXPORT spinCameraGetTLStreamNodeMap(Handle h,Handle* out) {*out=h;return 0;}
EXPORT spinCameraBeginAcquisition(Handle h) {auto c=static_cast<Camera*>(h);c->acquiring=true;return 0;}
EXPORT spinCameraEndAcquisition(Handle h) {static_cast<Camera*>(h)->acquiring=false;return 0;}
EXPORT spinCameraGetNextImageEx(Handle h,uint64_t,Handle* out) {
    auto c=static_cast<Camera*>(h);
    if(c->frames>=4) {std::this_thread::sleep_for(std::chrono::milliseconds(2));return Api::Timeout;}
    auto image=new Image;image->id=++c->frames;image->stamp=static_cast<uint64_t>(now(c));image->format=mode();
    image->incomplete=image->id==4;
    for(int y=0;y<3;++y) for(int x=0;x<4;++x) {
        if(mode()==0) reinterpret_cast<uint8_t*>(image->data.data())[y*16+x]=static_cast<uint8_t>(10*y+x);
        else image->data[y*8+x]=static_cast<uint16_t>(1000*y+x);
    }
    *out=image;++images;return 0;
}
EXPORT spinNodeMapGetNode(Handle h,const char* name,Handle* out) {
    auto c=static_cast<Camera*>(h);auto result=c->nodes.emplace(name,Node{c,name});*out=&result.first->second;return 0;
}
EXPORT spinNodeIsReadable(Handle h,Bool* out) {*out=h?1:0;return 0;}
EXPORT spinNodeIsWritable(Handle h,Bool* out) {*out=static_cast<Node*>(h)->name=="TimestampLatch";return 0;}
EXPORT spinNodeToString(Handle h,char* out,size_t* length) {
    auto n=static_cast<Node*>(h);std::string value="unavailable";
    if(n->name=="DeviceSerialNumber") value=n->camera->index==0?"26259157":"26259158";
    if(n->name=="TriggerMode") value="On";
    if(n->name=="TriggerSelector") value="FrameStart";
    if(n->name=="TriggerSource") value="Line3";
    if(n->name=="AcquisitionMode") value="Continuous";
    if(n->name=="PixelFormat") value=mode()==0?"Mono8":mode()==1?"Mono16":"Mono12p";
    if(n->name=="Width") value="4";
    if(n->name=="Height") value="3";
    return stringOut(value,out,length);
}
EXPORT spinIntegerGetValue(Handle h,int64_t* out) {
    auto n=static_cast<Node*>(h);
    *out=n->name=="TimestampLatchValue"?n->camera->latch:n->name=="TimestampIncrement"?1:1000000000;
    return 0;
}
EXPORT spinCommandExecute(Handle h) {
    auto n=static_cast<Node*>(h);if(n->name!="TimestampLatch") return -1;n->camera->latch=now(n->camera);return 0;
}
EXPORT spinImageRelease(Handle h) {delete static_cast<Image*>(h);--images;return 0;}
EXPORT spinImageCreateEmpty(Handle* out) {*out=new Image;++images;return 0;}
EXPORT spinImageDestroy(Handle h) {return spinImageRelease(h);}
EXPORT spinImageGetFrameID(Handle h,uint64_t* out) {*out=static_cast<Image*>(h)->id;return 0;}
EXPORT spinImageGetTimeStamp(Handle h,uint64_t* out) {*out=static_cast<Image*>(h)->stamp;return 0;}
EXPORT spinImageIsIncomplete(Handle h,Bool* out) {*out=static_cast<Image*>(h)->incomplete;return 0;}
EXPORT spinImageGetPixelFormat(Handle h,PixelFormat* out) {*out=static_cast<PixelFormat>(static_cast<Image*>(h)->format);return 0;}
EXPORT spinImageGetWidth(Handle,size_t* out) {*out=4;return 0;}
EXPORT spinImageGetHeight(Handle,size_t* out) {*out=3;return 0;}
EXPORT spinImageGetStride(Handle,size_t* out) {*out=16;return 0;}
EXPORT spinImageGetData(Handle h,void** out) {*out=static_cast<Image*>(h)->data.data();return 0;}
EXPORT spinImageProcessorCreate(Handle* out) {*out=&processors;++processors;return 0;}
EXPORT spinImageProcessorDestroy(Handle) {--processors;return 0;}
EXPORT spinImageProcessorConvert(Handle,Handle src,Handle dest,PixelFormat format) {
    *static_cast<Image*>(dest)=*static_cast<Image*>(src);static_cast<Image*>(dest)->format=format;return 0;
}
EXPORT fakeVerifyShutdown() {
    if(images || processors || systems || lists) return 1;
    for(auto& c:cameras) if(c.refs || c.initialized || c.acquiring) return 2;
    return 0;
}
