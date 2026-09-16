#include "camera_lock.hpp"
#include "spin_api.hpp"
#include <iostream>
#include <string>

int main(int argc,char** argv) {
    try {
        if(argc>2 || (argc==2 && std::string(argv[1])!="--runtime-only")) {
            std::cerr<<"Usage: inspect_cameras [--runtime-only]\n";return 1;
        }
        holo::CameraLock lock;
        if(argc==1) lock.acquire();
        holo::SpinApi api;
        std::cout<<"Spinnaker C runtime: "<<api.libraryPath()<<'\n';
        // Resolve symbols only: do not enumerate, initialize, or open cameras.
        if(argc==2) return 0;
        holo::SpinApi::Handle system=nullptr,list=nullptr,cam=nullptr;bool initialized=false;
        auto cleanup=[&] {
            if(cam) {if(initialized) api.CameraDeInit(cam);api.CameraRelease(cam);}
            if(list) {api.CameraListClear(list);api.CameraListDestroy(list);}
            if(system) api.SystemReleaseInstance(system);
        };
        try {
            api.check(api.SystemGetInstance(&system),"SystemGetInstance");
            api.check(api.CameraListCreateEmpty(&list),"CameraListCreateEmpty");
            api.check(api.SystemGetCameras(system,list),"SystemGetCameras");
            size_t count=0;api.check(api.CameraListGetSize(list,&count),"CameraListGetSize");
            std::cout<<"Cameras: "<<count<<'\n';
            for(size_t i=0;i<count;++i) {
                api.check(api.CameraListGet(list,i,&cam),"CameraListGet");
                api.check(api.CameraInit(cam),"CameraInit");initialized=true;
                holo::SpinApi::Handle map=nullptr;api.check(api.CameraGetNodeMap(cam,&map),"CameraGetNodeMap");
                std::cout<<"Camera "<<i<<'\n';
                for(const char* name:{"DeviceModelName","DeviceSerialNumber","Width","Height","PixelFormat","ReverseX","ReverseY","ExposureTime","Gain","TriggerSelector","TriggerMode","TriggerSource","TriggerDelay","AcquisitionMode"})
                    std::cout<<"  "<<name<<" = "<<api.value(map,name)<<'\n';
                api.check(api.CameraDeInit(cam),"CameraDeInit");initialized=false;
                api.check(api.CameraRelease(cam),"CameraRelease");cam=nullptr;
            }
            cleanup();return count?0:1;
        } catch(...) {cleanup();throw;}
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
