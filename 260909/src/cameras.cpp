#include "cameras.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace holo {
namespace {
struct ImageGuard {
    SpinApi& api; SpinApi::Handle image=nullptr; bool owned=false;
    ~ImageGuard() {if(image) {if(owned) api.ImageDestroy(image);else api.ImageRelease(image);}}
};
struct ProcessorGuard {
    SpinApi& api; SpinApi::Handle handle=nullptr;
    ~ProcessorGuard() {if(handle) api.ImageProcessorDestroy(handle);}
};
}
Cameras::Cameras(const Config& cfg,Shared& shared,const std::filesystem::path& session,std::function<void(int,FramePtr)> callback)
    : cfg_(cfg),shared_(shared),session_(session),on_frame_(std::move(callback)) {}
Cameras::~Cameras() { stop(); }
Cameras::ClockMap Cameras::calibrateClock(int index) {
    auto& api=*api_;auto map=maps_[index];
    auto latch=api.node(map,"TimestampLatch"), counter=api.node(map,"TimestampLatchValue");
    if(!api.writable(latch)||!api.readable(counter)) throw std::runtime_error("TimestampLatch unavailable: cannot establish reliable frame pairs");
    struct Sample { double camera, host, rtt; }; std::vector<Sample> samples;
    for(int k=0;k<9;++k) {
        auto before=nowNs(); api.check(api.CommandExecute(latch),"TimestampLatch"); auto after=nowNs(); auto ticks=api.integer(counter);
        samples.push_back({static_cast<double>(ticks),before*.5+after*.5,static_cast<double>(after-before)});
        if(k<8) std::this_thread::sleep_for(std::chrono::milliseconds(3));
    }
    // Some cameras expose latch values in ticks and others in nanoseconds.
    // Verify the unit against elapsed host time instead of assuming shared epochs.
    std::vector<double> units{1.0};
    auto increment=api.node(map,"TimestampIncrement");
    if(api.readable(increment) && api.integer(increment)>0) units.push_back(api.integer(increment));
    auto frequency=api.node(map,"GevTimestampTickFrequency");
    if(api.readable(frequency) && api.integer(frequency)>0) units.push_back(1e9/api.integer(frequency));
    double ticks=samples.back().camera-samples.front().camera, elapsed=samples.back().host-samples.front().host;
    double best_error=std::numeric_limits<double>::max(), unit=1;
    for(double u:units) { double error=std::abs(ticks*u/elapsed-1); if(error<best_error) {best_error=error;unit=u;} }
    if(ticks<=0 || best_error>.12) throw std::runtime_error("Camera timestamp rate could not be verified");
    auto best=std::min_element(samples.begin(),samples.end(),[](const Sample& a,const Sample& b){return a.rtt<b.rtt;});
    ClockMap result; result.offset_ns=best->host-best->camera*unit;
    result.uncertainty_ms=best->rtt*.5/1e6 + .02; result.checked_ns=nowNs(); result.latch_unit_ns=unit;
    if(result.uncertainty_ms>cfg_.max_clock_uncertainty_ms) throw std::runtime_error("Camera clock calibration too uncertain for pairing");
    return result;
}
void Cameras::start() {
    try {
        // Lock before even enumerating devices. A second process must never open
        // the same USB stream and interfere with the first process's acquisition.
        lock_.acquire();
        api_=std::make_unique<SpinApi>();auto& api=*api_;
        api.check(api.SystemGetInstance(&system_),"SystemGetInstance");
        api.check(api.CameraListCreateEmpty(&list_),"CameraListCreateEmpty");
        api.check(api.SystemGetCameras(system_,list_),"SystemGetCameras");
        size_t count=0;api.check(api.CameraListGetSize(list_,&count),"CameraListGetSize");
        if(count<2) throw std::runtime_error("Two cameras required; found "+std::to_string(count)+". Close SpinView and check USB access.");
        std::vector<std::pair<std::string,size_t>> available;
        for(size_t i=0;i<count;++i) {
            SpinApi::Handle cam=nullptr,map=nullptr;
            api.check(api.CameraListGet(list_,i,&cam),"CameraListGet");
            try {
                api.check(api.CameraGetTLDeviceNodeMap(cam,&map),"CameraGetTLDeviceNodeMap");
                available.emplace_back(api.value(map,"DeviceSerialNumber"),i);
            } catch(...) {api.CameraRelease(cam);throw;}
            api.check(api.CameraRelease(cam),"CameraRelease");
        }
        std::sort(available.begin(),available.end());
        if(count>2 && (cfg_.serial[0].empty()||cfg_.serial[1].empty())) throw std::runtime_error("More than two cameras: specify both serials");
        for(int i=0;i<2;++i) {
            if(cfg_.serial[i].empty()) cfg_.serial[i]=available[i].first;
            auto found=std::find_if(available.begin(),available.end(),[&](const auto& a){return a.first==cfg_.serial[i];});
            if(found==available.end()) throw std::runtime_error("Camera not found: "+cfg_.serial[i]);
            if(i && cfg_.serial[0]==cfg_.serial[1]) throw std::runtime_error("The same camera was selected twice");
            api.check(api.CameraListGet(list_,found->second,&cameras_[i]),"CameraListGet");
            api.check(api.CameraInit(cameras_[i]),"CameraInit");initialized_[i]=true;
            api.check(api.CameraGetNodeMap(cameras_[i],&maps_[i]),"CameraGetNodeMap");auto map=maps_[i];
            if(api.value(map,"TriggerMode")!="On" || api.value(map,"TriggerSelector")!="FrameStart" || api.value(map,"TriggerSource")=="Software")
                throw std::runtime_error("Camera "+cfg_.serial[i]+" must already use external FrameStart triggering. This app preserves trigger wiring/settings.");
            if(api.value(map,"AcquisitionMode")!="Continuous") throw std::runtime_error("AcquisitionMode is not Continuous. Camera settings are read-only in this app.");
            auto fmt=api.value(map,"PixelFormat");
            if(fmt.rfind("Mono",0)!=0) throw std::runtime_error("Monochrome pixel format required, found "+fmt);
            SpinApi::Handle stream=nullptr;api.check(api.CameraGetTLStreamNodeMap(cameras_[i],&stream),"CameraGetTLStreamNodeMap");
            clock_[i]=calibrateClock(i);
            cv::FileStorage fs((session_/("camera"+std::to_string(i)+".yml")).string(),cv::FileStorage::WRITE);
            if(!fs.isOpened()) throw std::runtime_error("Cannot write camera metadata");
            for(const char* name:{"DeviceModelName","DeviceSerialNumber","DeviceFirmwareVersion","Width","Height","ReverseX","ReverseY","OffsetX","OffsetY","PixelFormat","AdcBitDepth","ExposureTime","ExposureAuto","Gain","GainAuto","GammaEnable","Gamma","BlackLevel","AcquisitionMode","AcquisitionFrameRateEnable","AcquisitionFrameRate","TriggerSelector","TriggerMode","TriggerSource","TriggerActivation","TriggerDelay","TimestampIncrement","DeviceLinkThroughputLimit","CounterSelector","CounterEventSource","CounterDuration"}) fs << name << api.value(map,name);
            fs << "StreamBufferHandlingMode" << api.value(stream,"StreamBufferHandlingMode")
               << "StreamBufferCountManual" << api.value(stream,"StreamBufferCountManual")
               << "camera_settings_policy" << "read_only_no_parameter_writes"
               << "pairing_method" << "timestamp_latch_host_mapping_estimate"
               << "latch_unit_ns" << clock_[i].latch_unit_ns << "clock_uncertainty_ms" << clock_[i].uncertainty_ms;
            std::cout << "Camera " << i << " serial=" << cfg_.serial[i] << " " << api.value(map,"Width") << 'x' << api.value(map,"Height")
                      << " " << fmt << " trigger=" << api.value(map,"TriggerSource") << " latch_unit_ns=" << clock_[i].latch_unit_ns
                      << " clock uncertainty=" << clock_[i].uncertainty_ms << " ms" << std::endl;
        }
        for(int i=0;i<2;++i) { api.check(api.CameraBeginAcquisition(cameras_[i]),"CameraBeginAcquisition"); acquiring_[i]=true; }
        for(int i=0;i<2;++i) threads_[i]=std::thread(&Cameras::acquire,this,i);
    } catch(...) { stop(); throw; }
}
void Cameras::acquire(int i) {
    uint64_t previous_id=0, previous_camera_ns=0; bool have_previous=false;
    auto& api=*api_;ProcessorGuard processor{api};
    try {
        api.check(api.ImageProcessorCreate(&processor.handle),"ImageProcessorCreate");
        auto format=api.value(maps_[i],"PixelFormat");
        while(!shared_.stop) {
            if(nowNs()-clock_[i].checked_ns>30'000'000'000LL) clock_[i]=calibrateClock(i);
            ImageGuard image{api};
            auto error=api.CameraGetNextImageEx(cameras_[i],250,&image.image);
            if(error==SpinApi::Timeout) {++shared_.timeouts[i];continue;}
            api.check(error,"CameraGetNextImageEx");
            auto host_ns=nowNs();uint64_t id=0;
            api.check(api.ImageGetFrameID(image.image,&id),"ImageGetFrameID");
            if(have_previous && id>previous_id+1) shared_.frame_gaps[i]+=id-previous_id-1;
            if(have_previous && id<=previous_id) throw std::runtime_error("Camera frame ID reset; restart acquisition to avoid incorrect pairing");
            previous_id=id; have_previous=true;
            SpinApi::Bool incomplete=0;api.check(api.ImageIsIncomplete(image.image,&incomplete),"ImageIsIncomplete");
            if(incomplete) {++shared_.incomplete[i]; continue;}
            auto frame=std::make_shared<Frame>(); frame->id=id; api.check(api.ImageGetTimeStamp(image.image,&frame->camera_ns),"ImageGetTimeStamp");
            if(frame->camera_ns==0 || (previous_camera_ns && frame->camera_ns<=previous_camera_ns)) throw std::runtime_error("Camera timestamp missing/reset");
            previous_camera_ns=frame->camera_ns;
            frame->host_ns=host_ns; frame->exposure_host_ns=static_cast<int64_t>(frame->camera_ns+clock_[i].offset_ns);
            frame->clock_uncertainty_ms=clock_[i].uncertainty_ms;
            double age_ms=(host_ns-frame->exposure_host_ns)/1e6;
            if(age_ms < -cfg_.max_clock_uncertainty_ms || age_ms>2000) throw std::runtime_error("Timestamp mapping invalid or camera transport backlog exceeds 2 seconds");
            frame->serial=cfg_.serial[i]; frame->source_format=format;
            auto pixels=image.image;ImageGuard converted{api,nullptr,true};SpinApi::PixelFormat pixel_format{};
            api.check(api.ImageGetPixelFormat(pixels,&pixel_format),"ImageGetPixelFormat");
            if(pixel_format!=SpinApi::Mono8 && pixel_format!=SpinApi::Mono16) {
                api.check(api.ImageCreateEmpty(&converted.image),"ImageCreateEmpty");
                api.check(api.ImageProcessorConvert(processor.handle,image.image,converted.image,SpinApi::Mono16),"ImageProcessorConvert");
                pixels=converted.image;pixel_format=SpinApi::Mono16;
            }
            int depth=pixel_format==SpinApi::Mono8?CV_8UC1:CV_16UC1;
            frame->full_scale=depth==CV_8UC1?255:65535;
            size_t height=0,width=0,stride=0;void* data=nullptr;
            api.check(api.ImageGetHeight(pixels,&height),"ImageGetHeight");
            api.check(api.ImageGetWidth(pixels,&width),"ImageGetWidth");
            api.check(api.ImageGetStride(pixels,&stride),"ImageGetStride");
            api.check(api.ImageGetData(pixels,&data),"ImageGetData");
            if(!data || height==0 || width==0 || height>static_cast<size_t>(std::numeric_limits<int>::max()) ||
               width>static_cast<size_t>(std::numeric_limits<int>::max())) throw std::runtime_error("Invalid image dimensions/data");
            frame->raw=cv::Mat(static_cast<int>(height),static_cast<int>(width),depth,data,stride).clone();
            ++shared_.received[i]; on_frame_(i,frame);
        }
    } catch(const std::exception& e) {shared_.fail("Camera "+std::to_string(i)+": "+e.what());}
}
void Cameras::stop() {
    shared_.stop=true;
    for(auto& thread:threads_) if(thread.joinable()) thread.join();
    if(api_) {
        auto& api=*api_;
        auto cleanup=[&](SpinApi::Error error,const char* op) {
            if(error) {try {api.check(error,op);} catch(const std::exception& e) {shared_.fail(e.what());}}
        };
        for(int i=0;i<2;++i) {
            if(acquiring_[i]) cleanup(api.CameraEndAcquisition(cameras_[i]),"CameraEndAcquisition");
            acquiring_[i]=false;
            if(initialized_[i]) cleanup(api.CameraDeInit(cameras_[i]),"CameraDeInit");
            initialized_[i]=false;
            if(cameras_[i]) cleanup(api.CameraRelease(cameras_[i]),"CameraRelease");
            cameras_[i]=nullptr;maps_[i]=nullptr;
        }
        if(list_) {cleanup(api.CameraListClear(list_),"CameraListClear");cleanup(api.CameraListDestroy(list_),"CameraListDestroy");list_=nullptr;}
        if(system_) {cleanup(api.SystemReleaseInstance(system_),"SystemReleaseInstance");system_=nullptr;}
        api_.reset();
    }
    lock_.release();
}
} // namespace holo
