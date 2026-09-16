#pragma once
#include <cstddef>
#include <cstdint>
#include <string>

namespace holo {
// Minimal dynamic binding to the documented Spinnaker 4 C ABI (64-bit only).
// SDK headers and binaries are not included or redistributed by this project.
// https://softwareservices.flir.com/Spinnaker/latest/api/c.html
class SpinApi {
public:
    using Handle=void*;
    using Error=int;
    using Bool=uint8_t;
    enum PixelFormat : int {Mono8=0,Mono16=1};
    static constexpr Error Timeout=-1011;
    SpinApi();
    ~SpinApi();
    SpinApi(const SpinApi&)=delete;
    SpinApi& operator=(const SpinApi&)=delete;
    void check(Error error,const char* operation) const;
    Handle node(Handle map,const char* name) const;
    bool readable(Handle node) const;
    bool writable(Handle node) const;
    std::string value(Handle map,const char* name) const;
    int64_t integer(Handle node) const;
    std::string libraryPath() const {return path_;}

#define HOLO_SPIN_FUNCTIONS(X) \
    X(ErrorGetLastMessage,(char*,size_t*)) \
    X(SystemGetInstance,(Handle*)) \
    X(SystemReleaseInstance,(Handle)) \
    X(SystemGetCameras,(Handle,Handle)) \
    X(CameraListCreateEmpty,(Handle*)) \
    X(CameraListDestroy,(Handle)) \
    X(CameraListGetSize,(Handle,size_t*)) \
    X(CameraListGet,(Handle,size_t,Handle*)) \
    X(CameraListClear,(Handle)) \
    X(CameraInit,(Handle)) \
    X(CameraDeInit,(Handle)) \
    X(CameraRelease,(Handle)) \
    X(CameraGetNodeMap,(Handle,Handle*)) \
    X(CameraGetTLDeviceNodeMap,(Handle,Handle*)) \
    X(CameraGetTLStreamNodeMap,(Handle,Handle*)) \
    X(CameraBeginAcquisition,(Handle)) \
    X(CameraEndAcquisition,(Handle)) \
    X(CameraGetNextImageEx,(Handle,uint64_t,Handle*)) \
    X(NodeMapGetNode,(Handle,const char*,Handle*)) \
    X(NodeIsReadable,(Handle,Bool*)) \
    X(NodeIsWritable,(Handle,Bool*)) \
    X(NodeToString,(Handle,char*,size_t*)) \
    X(IntegerGetValue,(Handle,int64_t*)) \
    X(CommandExecute,(Handle)) \
    X(ImageRelease,(Handle)) \
    X(ImageCreateEmpty,(Handle*)) \
    X(ImageDestroy,(Handle)) \
    X(ImageGetFrameID,(Handle,uint64_t*)) \
    X(ImageGetTimeStamp,(Handle,uint64_t*)) \
    X(ImageIsIncomplete,(Handle,Bool*)) \
    X(ImageGetPixelFormat,(Handle,PixelFormat*)) \
    X(ImageGetWidth,(Handle,size_t*)) \
    X(ImageGetHeight,(Handle,size_t*)) \
    X(ImageGetStride,(Handle,size_t*)) \
    X(ImageGetData,(Handle,void**)) \
    X(ImageProcessorCreate,(Handle*)) \
    X(ImageProcessorDestroy,(Handle)) \
    X(ImageProcessorConvert,(Handle,Handle,Handle,PixelFormat))
#define HOLO_DECLARE(name,args) Error (*name) args=nullptr;
    HOLO_SPIN_FUNCTIONS(HOLO_DECLARE)
#undef HOLO_DECLARE
private:
    void* library_=nullptr;
    std::string path_;
    void* symbol(const char* name) const;
    void unload() noexcept;
};
}
