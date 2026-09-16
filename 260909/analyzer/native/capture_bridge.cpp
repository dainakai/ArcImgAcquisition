// Small C ABI shared with the Python/Qt GUI. No Python callbacks on SDK threads.
// Camera settings, timestamp pairing and the interprocess lock are DualHolo's.
#include "cameras.hpp"
#include "recorder.hpp"
#include <cstring>
#include <limits>
#include <stdexcept>

#ifdef _WIN32
#define API extern "C" __declspec(dllexport)
#else
#define API extern "C" __attribute__((visibility("default")))
#endif

namespace {
void copyText(char* dst, size_t size, const std::string& value) {
    if(size) {std::strncpy(dst,value.c_str(),size-1);dst[size-1]=0;}
}
struct Capture {
    holo::Config cfg;
    holo::Shared shared;
    std::unique_ptr<holo::Recorder> recorder;
    std::unique_ptr<holo::Pairer> pairer;
    std::unique_ptr<holo::Cameras> cameras;
    std::thread simulator;
    std::mutex pair_mutex;
    holo::PairPtr latest;
    ~Capture() {
        shared.stop=true;
        if(cameras) cameras->stop();
        if(simulator.joinable()) simulator.join();
        if(recorder) recorder->close();
    }
    void receive(int i, holo::FramePtr frame) {
        recorder->consume(i,frame);
        std::lock_guard<std::mutex> guard(pair_mutex);
        for(auto& pair:pairer->add(i,frame)) {latest=pair;++shared.pairs;}
        shared.unmatched=pairer->unmatched();
    }
};
}
struct HoloFrameView {
    const void* data;
    int width, height, bits;
    uint64_t id, camera_ns;
    int64_t host_ns, exposure_ns;
    double uncertainty_ms;
    char serial[64];
};
struct HoloStatus {
    uint64_t received[2], saved[2], gaps[2], incomplete[2], pending, waits;
    int recording, stopped;
};

API void* holo_open(const char* serial0, const char* serial1, const char* session,
                    double tolerance_ms, double max_uncertainty_ms, int simulate,
                    char* error, size_t error_size) {
    try {
        auto c=std::make_unique<Capture>();
        cv::setNumThreads(1);
        c->cfg.serial={serial0,serial1};c->cfg.pair_tolerance_ms=tolerance_ms;
        c->cfg.max_clock_uncertainty_ms=max_uncertainty_ms;c->cfg.opencv_threads=1;
        c->cfg.validate();
        const auto path=std::filesystem::u8path(session);
        std::filesystem::create_directories(path);
        c->recorder=std::make_unique<holo::Recorder>(c->cfg,c->shared,path);
        c->pairer=std::make_unique<holo::Pairer>(c->cfg);
        if(simulate) {
            auto* ptr=c.get();
            c->simulator=std::thread([ptr] {
                try {
                    auto next=holo::Clock::now();int index=0;
                    while(!ptr->shared.stop) {
                        const auto stamp=holo::nowNs();
                        for(int i=0;i<2;++i) {
                            auto f=std::make_shared<holo::Frame>();
                            f->raw=holo::syntheticImage(index,i);f->id=static_cast<uint64_t>(index+1);
                            f->serial=ptr->cfg.serial[i];f->source_format="Mono8";
                            f->host_ns=stamp;f->camera_ns=stamp;f->exposure_host_ns=stamp;
                            ++ptr->shared.received[i];ptr->receive(i,f);
                        }
                        ++index;next+=std::chrono::milliseconds(100);std::this_thread::sleep_until(next);
                    }
                } catch(const std::exception& e) {ptr->shared.fail(e.what());}
            });
        } else {
            auto* ptr=c.get();
            c->cameras=std::make_unique<holo::Cameras>(c->cfg,c->shared,path,
                [ptr](int i,holo::FramePtr f){ptr->receive(i,std::move(f));});
            c->cameras->start();
        }
        return c.release();
    } catch(const std::exception& e) {copyText(error,error_size,e.what());return nullptr;}
}
API void holo_close(void* capture) {delete static_cast<Capture*>(capture);}
// Returned snapshot owns both frames until holo_release. The GUI copies the
// original pixels, never pointers into the SDK's recycled image buffers.
API void* holo_snapshot(void* capture, uint64_t after, uint64_t* sequence, double* age_ms) {
    auto& c=*static_cast<Capture*>(capture);
    std::lock_guard<std::mutex> guard(c.pair_mutex);
    if(!c.latest || c.latest->sequence+1<=after) return nullptr;
    *sequence=c.latest->sequence+1;
    *age_ms=(holo::nowNs()-std::min(c.latest->frames[0]->host_ns,c.latest->frames[1]->host_ns))/1e6;
    return new holo::PairPtr(c.latest);
}
API int holo_frame(void* snapshot, int camera, HoloFrameView* out) {
    if(camera<0 || camera>1 || !out) return 0;
    const auto& f=*(*static_cast<holo::PairPtr*>(snapshot))->frames[camera];
    out->data=f.raw.data;out->width=f.raw.cols;out->height=f.raw.rows;
    out->bits=static_cast<int>(f.raw.elemSize()*8);out->id=f.id;out->camera_ns=f.camera_ns;
    out->host_ns=f.host_ns;out->exposure_ns=f.exposure_host_ns;
    out->uncertainty_ms=f.clock_uncertainty_ms;copyText(out->serial,sizeof(out->serial),f.serial);
    return 1;
}
API void holo_release(void* snapshot) {delete static_cast<holo::PairPtr*>(snapshot);}
API int holo_record(void* capture, int enabled) {
    return static_cast<Capture*>(capture)->recorder->setRecording(enabled!=0)?1:0;
}
API void holo_status(void* capture, HoloStatus* out, char* error, size_t size) {
    auto& s=static_cast<Capture*>(capture)->shared;
    for(int i=0;i<2;++i) {
        out->received[i]=s.received[i];out->saved[i]=s.saved[i];out->gaps[i]=s.frame_gaps[i];
        out->incomplete[i]=s.incomplete[i];
    }
    out->pending=s.pending_frames;out->waits=s.writer_waits;
    out->recording=s.recording;out->stopped=s.stop;
    std::lock_guard<std::mutex> guard(s.mutex);copyText(error,size,s.error);
}
