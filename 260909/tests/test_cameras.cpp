#include "cameras.hpp"
#include "recorder.hpp"
#include <cstdlib>
#include <iostream>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif
int main() {
    namespace fs=std::filesystem;
    fs::path root=fs::temp_directory_path()/("dual_holo_backend_"+holo::timestamp());
    try {
        // Keep the fake library loaded until lifecycle counters are checked.
        const auto path=std::getenv("SPINNAKER_C_LIBRARY");
#ifdef _WIN32
        auto library=LoadLibraryA(path);
        auto verify=reinterpret_cast<int(*)()>(GetProcAddress(library,"fakeVerifyShutdown"));
#else
        auto library=dlopen(path,RTLD_NOW|RTLD_LOCAL);
        auto verify=reinterpret_cast<int(*)()>(dlsym(library,"fakeVerifyShutdown"));
#endif
        if(!verify) throw std::runtime_error("Fake runtime did not load");
        holo::Config cfg;holo::Shared shared;fs::create_directories(root);
        std::atomic<int> frames{0};
        const int mode=std::atoi(std::getenv("HOLO_FAKE_FORMAT"));
        bool failed=false;
        {
            holo::Recorder recorder(cfg,shared,root);recorder.setRecording(true,"test");
            holo::Cameras cameras(cfg,shared,root,[&](int cam,holo::FramePtr frame) {
                const auto& raw=frame->raw;
                if(raw.rows!=3 || raw.cols!=4 || raw.type()!=(mode==0?CV_8UC1:CV_16UC1))
                    throw std::runtime_error("Wrong raw image dimensions or type");
                for(int y=0;y<3;++y) for(int x=0;x<4;++x) {
                    int value=mode==0?raw.at<uint8_t>(y,x):raw.at<uint16_t>(y,x);
                    if(value!=(mode==0?10*y+x:1000*y+x)) throw std::runtime_error("Stride/format/orientation mismatch");
                }
                recorder.consume(cam,frame);++frames;
            });
            try {cameras.start();} catch(const std::exception&) {failed=true;}
            auto deadline=holo::Clock::now()+std::chrono::seconds(3);
            while(!failed && !shared.stop && (frames<6 || shared.incomplete[0]+shared.incomplete[1]<2) && holo::Clock::now()<deadline)
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            cameras.stop();recorder.close();
        }
        if(std::getenv("HOLO_FAKE_INIT_FAILURE")) {
            if(!failed || frames!=0) throw std::runtime_error("Initialization failure did not stop safely");
        } else if(failed || frames!=6 || shared.saved[0]!=3 || shared.saved[1]!=3 || !shared.error.empty()) {
            throw std::runtime_error("Fake camera acquisition/recording failed: "+shared.error);
        }
        if(verify()!=0) throw std::runtime_error("Camera, image or processor handle leaked");
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
        fs::remove_all(root);std::cout<<"Camera runtime acquisition and cleanup passed\n";return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';fs::remove_all(root);return 1;}
}
