#include "cameras.hpp"
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>

namespace holo {
using namespace Spinnaker;
using namespace Spinnaker::GenApi;
namespace {
std::string value(INodeMap& map,const char* name) { CValuePtr n=map.GetNode(name); return IsReadable(n)?n->ToString().c_str():"unavailable"; }
struct ReleaseImage { ImagePtr image; ~ReleaseImage() { if(image) try {image->Release();} catch(...) {} } };
}
Cameras::Cameras(const Config& cfg,Shared& shared,const std::filesystem::path& session,std::function<void(int,FramePtr)> callback)
    : cfg_(cfg),shared_(shared),session_(session),on_frame_(std::move(callback)) {}
Cameras::~Cameras() { stop(); }
Cameras::ClockMap Cameras::calibrateClock(int index) {
    auto& map=cameras_[index]->GetNodeMap();
    CCommandPtr latch=map.GetNode("TimestampLatch"); CIntegerPtr counter=map.GetNode("TimestampLatchValue");
    if(!IsWritable(latch)||!IsReadable(counter)) throw std::runtime_error("TimestampLatch unavailable: cannot establish reliable frame pairs");
    struct Sample { double camera, host, rtt; }; std::vector<Sample> samples;
    for(int k=0;k<9;++k) {
        auto before=nowNs(); latch->Execute(); auto after=nowNs(); auto ticks=counter->GetValue();
        samples.push_back({static_cast<double>(ticks),before*.5+after*.5,static_cast<double>(after-before)});
        if(k<8) std::this_thread::sleep_for(std::chrono::milliseconds(3));
    }
    // Some cameras expose latch values in ticks and others in nanoseconds.
    // Verify the unit against elapsed host time instead of assuming shared epochs.
    std::vector<double> units{1.0};
    CIntegerPtr increment=map.GetNode("TimestampIncrement");
    if(IsReadable(increment) && increment->GetValue()>0) units.push_back(increment->GetValue());
    CIntegerPtr frequency=map.GetNode("GevTimestampTickFrequency");
    if(IsReadable(frequency) && frequency->GetValue()>0) units.push_back(1e9/frequency->GetValue());
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
        auto lock_path=std::filesystem::temp_directory_path()/("dual_holo_cameras_"+std::to_string(getuid())+".lock");
        lock_fd_=::open(lock_path.c_str(),O_CREAT|O_RDWR,0600);
        if(lock_fd_<0) throw std::runtime_error("Cannot create the camera application lock");
        if(flock(lock_fd_,LOCK_EX|LOCK_NB)!=0) throw std::runtime_error("DualHolo is already using the cameras. Close the other instance first.");
        system_=System::GetInstance(); list_=system_->GetCameras();
        if(list_.GetSize()<2) throw std::runtime_error("Two cameras required; found "+std::to_string(list_.GetSize())+". Close SpinView and check USB access.");
        std::vector<std::pair<std::string,unsigned>> available;
        for(unsigned i=0;i<list_.GetSize();++i) { auto cam=list_.GetByIndex(i); available.emplace_back(value(cam->GetTLDeviceNodeMap(),"DeviceSerialNumber"),i); }
        std::sort(available.begin(),available.end());
        if(list_.GetSize()>2 && (cfg_.serial[0].empty()||cfg_.serial[1].empty())) throw std::runtime_error("More than two cameras: specify both serials");
        for(int i=0;i<2;++i) {
            if(cfg_.serial[i].empty()) cfg_.serial[i]=available[i].first;
            auto found=std::find_if(available.begin(),available.end(),[&](const auto& a){return a.first==cfg_.serial[i];});
            if(found==available.end()) throw std::runtime_error("Camera not found: "+cfg_.serial[i]);
            if(i && cfg_.serial[0]==cfg_.serial[1]) throw std::runtime_error("The same camera was selected twice");
            cameras_[i]=list_.GetByIndex(found->second); cameras_[i]->Init(); initialized_[i]=true;
            auto& map=cameras_[i]->GetNodeMap();
            if(value(map,"TriggerMode")!="On" || value(map,"TriggerSelector")!="FrameStart" || value(map,"TriggerSource")=="Software")
                throw std::runtime_error("Camera "+cfg_.serial[i]+" must already use external FrameStart triggering. This app preserves trigger wiring/settings.");
            if(value(map,"AcquisitionMode")!="Continuous") throw std::runtime_error("AcquisitionMode is not Continuous. Camera settings are read-only in this app.");
            auto fmt=value(map,"PixelFormat");
            if(fmt.rfind("Mono",0)!=0) throw std::runtime_error("Monochrome pixel format required, found "+fmt);
            auto& stream=cameras_[i]->GetTLStreamNodeMap();
            clock_[i]=calibrateClock(i);
            cv::FileStorage fs((session_/("camera"+std::to_string(i)+".yml")).string(),cv::FileStorage::WRITE);
            if(!fs.isOpened()) throw std::runtime_error("Cannot write camera metadata");
            for(const char* name:{"DeviceModelName","DeviceSerialNumber","DeviceFirmwareVersion","Width","Height","ReverseX","ReverseY","OffsetX","OffsetY","PixelFormat","AdcBitDepth","ExposureTime","ExposureAuto","Gain","GainAuto","GammaEnable","Gamma","BlackLevel","AcquisitionMode","AcquisitionFrameRateEnable","AcquisitionFrameRate","TriggerSelector","TriggerMode","TriggerSource","TriggerActivation","TriggerDelay","TimestampIncrement","DeviceLinkThroughputLimit","CounterSelector","CounterEventSource","CounterDuration"}) fs << name << value(map,name);
            fs << "StreamBufferHandlingMode" << value(stream,"StreamBufferHandlingMode")
               << "StreamBufferCountManual" << value(stream,"StreamBufferCountManual")
               << "camera_settings_policy" << "read_only_no_parameter_writes"
               << "pairing_method" << "timestamp_latch_host_mapping_estimate"
               << "latch_unit_ns" << clock_[i].latch_unit_ns << "clock_uncertainty_ms" << clock_[i].uncertainty_ms;
            std::cout << "Camera " << i << " serial=" << cfg_.serial[i] << " " << value(map,"Width") << 'x' << value(map,"Height")
                      << " " << fmt << " trigger=" << value(map,"TriggerSource") << " latch_unit_ns=" << clock_[i].latch_unit_ns
                      << " clock uncertainty=" << clock_[i].uncertainty_ms << " ms" << std::endl;
        }
        for(int i=0;i<2;++i) { cameras_[i]->BeginAcquisition(); acquiring_[i]=true; }
        for(int i=0;i<2;++i) threads_[i]=std::thread(&Cameras::acquire,this,i);
    } catch(...) { stop(); throw; }
}
void Cameras::acquire(int i) {
    uint64_t previous_id=0, previous_camera_ns=0; bool have_previous=false;
    ImageProcessor processor;
    try {
        auto format=value(cameras_[i]->GetNodeMap(),"PixelFormat");
        while(!shared_.stop) {
            if(nowNs()-clock_[i].checked_ns>30'000'000'000LL) clock_[i]=calibrateClock(i);
            ImagePtr image;
            try {image=cameras_[i]->GetNextImage(250);} catch(const Spinnaker::Exception& e) {
                if(e.GetError()==SPINNAKER_ERR_TIMEOUT) {++shared_.timeouts[i]; continue;} throw;
            }
            ReleaseImage guard{image};
            auto host_ns=nowNs(); auto id=image->GetFrameID();
            if(have_previous && id>previous_id+1) shared_.frame_gaps[i]+=id-previous_id-1;
            if(have_previous && id<=previous_id) throw std::runtime_error("Camera frame ID reset; restart acquisition to avoid incorrect pairing");
            previous_id=id; have_previous=true;
            if(image->IsIncomplete()) {++shared_.incomplete[i]; continue;}
            auto frame=std::make_shared<Frame>(); frame->id=id; frame->camera_ns=image->GetTimeStamp();
            if(frame->camera_ns==0 || (previous_camera_ns && frame->camera_ns<=previous_camera_ns)) throw std::runtime_error("Camera timestamp missing/reset");
            previous_camera_ns=frame->camera_ns;
            frame->host_ns=host_ns; frame->exposure_host_ns=static_cast<int64_t>(frame->camera_ns+clock_[i].offset_ns);
            frame->clock_uncertainty_ms=clock_[i].uncertainty_ms;
            double age_ms=(host_ns-frame->exposure_host_ns)/1e6;
            if(age_ms < -cfg_.max_clock_uncertainty_ms || age_ms>2000) throw std::runtime_error("Timestamp mapping invalid or camera transport backlog exceeds 2 seconds");
            frame->serial=cfg_.serial[i]; frame->source_format=format;
            ImagePtr pixels=image;
            if(image->GetPixelFormat()!=PixelFormat_Mono8 && image->GetPixelFormat()!=PixelFormat_Mono16) pixels=processor.Convert(image,PixelFormat_Mono16);
            int depth=pixels->GetPixelFormat()==PixelFormat_Mono8?CV_8UC1:CV_16UC1;
            frame->full_scale=depth==CV_8UC1?255:65535;
            frame->raw=cv::Mat(static_cast<int>(pixels->GetHeight()),static_cast<int>(pixels->GetWidth()),depth,pixels->GetData(),pixels->GetStride()).clone();
            ++shared_.received[i]; on_frame_(i,frame);
        }
    } catch(const std::exception& e) {shared_.fail("Camera "+std::to_string(i)+": "+e.what());}
}
void Cameras::stop() {
    shared_.stop=true;
    for(auto& thread:threads_) if(thread.joinable()) thread.join();
    for(int i=0;i<2;++i) {
        if(acquiring_[i]) {try {cameras_[i]->EndAcquisition();} catch(const std::exception& e) {std::cerr<<e.what()<<std::endl;} acquiring_[i]=false;}
        if(initialized_[i]) {
            try {cameras_[i]->DeInit();} catch(const std::exception& e) {std::cerr<<e.what()<<std::endl;} initialized_[i]=false;
        }
        cameras_[i]=nullptr;
    }
    list_.Clear(); if(system_) {try {system_->ReleaseInstance();} catch(...) {} system_=nullptr;}
    if(lock_fd_>=0) {::close(lock_fd_);lock_fd_=-1;}
}
} // namespace holo
