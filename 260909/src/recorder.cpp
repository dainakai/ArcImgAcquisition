#include "recorder.hpp"
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace holo {
namespace fs=std::filesystem;
namespace {
void textFile(const fs::path& path,const std::string& text) {
    std::ofstream out;out.exceptions(std::ios::badbit|std::ios::failbit);out.open(path);out<<text;out.close();
}
std::string frameName(uint64_t index,uint64_t id) {
    std::ostringstream s;s<<"frame_"<<std::setw(6)<<std::setfill('0')<<index<<"_id"<<id<<".tiff";return s.str();
}
}
Recorder::Recorder(const Config& cfg,Shared& shared,fs::path session)
    : cfg_(cfg),shared_(shared),session_(std::move(session)),thread_(&Recorder::run,this) {}
Recorder::~Recorder() {close();}
bool Recorder::setRecording(bool enabled,const std::string& reason) {
    std::lock_guard<std::mutex> lock(mutex_);
    if(closed_ || failed_ || (enabled && shared_.stop)) return false;
    if(enabled==!active_.empty()) return false;
    if(!enabled) {finishLocked(reason);return true;}
    active_="recording_"+timestamp();admitted_={};last_id_={};id_gaps_={};started_ns_=nowNs();
    for(int i=0;i<2;++i) {initial_gaps_[i]=shared_.frame_gaps[i];initial_incomplete_[i]=shared_.incomplete[i];}
    Job job;job.kind=Kind::Start;job.recording=active_;
    job.summary="status=recording\nstarted_steady_ns="+std::to_string(started_ns_)+
        "\ngate=per-camera callback admission between REC start and stop; no pre/post frames\n";
    jobs_.push_back(std::move(job));++shared_.recordings;shared_.record_started_ns=started_ns_;
    shared_.recording=true;shared_.setStatus("Recording: "+active_);cv_.notify_all();return true;
}
void Recorder::finishLocked(const std::string& reason) {
    if(active_.empty()) return;
    auto stopped=nowNs();std::ostringstream s;
    s<<"end_reason="<<reason<<"\nstarted_steady_ns="<<started_ns_<<"\nstopped_steady_ns="<<stopped
     <<"\nduration_seconds="<<std::setprecision(12)<<(stopped-started_ns_)/1e9<<'\n';
    bool gaps=false;
    for(int i=0;i<2;++i) {
        auto transport=shared_.frame_gaps[i]-initial_gaps_[i],incomplete=shared_.incomplete[i]-initial_incomplete_[i];
        s<<"accepted_cam"<<i<<'='<<admitted_[i]<<"\nframe_id_gaps_cam"<<i<<'='<<id_gaps_[i]
         <<"\ntransport_gaps_cam"<<i<<'='<<transport<<"\nincomplete_cam"<<i<<'='<<incomplete<<'\n';
        gaps |= id_gaps_[i]>0 || transport>0 || incomplete>0;
    }
    s<<"status="<<(gaps?"capture_gaps":(admitted_[0]+admitted_[1]==0?"empty":"complete"))
     <<"\nimages=all_admitted_raw_frames_including_unpaired\n";
    Job job;job.kind=Kind::Finish;job.recording=active_;job.summary=s.str();job.expected=admitted_;
    jobs_.push_back(std::move(job));active_.clear();shared_.recording=false;
    shared_.setStatus("Rec stopped. Finishing queued writes; preview continues.");cv_.notify_all();
}
bool Recorder::consume(int camera,FramePtr frame) {
    if(camera<0 || camera>1 || !frame || frame->raw.empty()) throw std::invalid_argument("Invalid recording frame");
    std::unique_lock<std::mutex> lock(mutex_);
    if(active_.empty() || closed_ || failed_) return false;
    Job job;job.recording=active_;job.camera=camera;job.frame=std::move(frame);job.admitted_ns=nowNs();
    job.index=admitted_[camera]++;job.bytes=job.frame->raw.total()*job.frame->raw.elemSize();
    if(last_id_[camera] && job.frame->id!=last_id_[camera]+1)
        id_gaps_[camera]+=job.frame->id>last_id_[camera]?job.frame->id-last_id_[camera]-1:1;
    last_id_[camera]=job.frame->id;
    queued_bytes_+=job.bytes;shared_.queued_bytes=queued_bytes_;++shared_.pending_frames;++shared_.accepted[camera];
    jobs_.push_back(std::move(job));cv_.notify_all();
    const size_t limit=static_cast<size_t>(cfg_.writer_queue_mb)*1024*1024;
    if(queued_bytes_>=limit) {
        ++shared_.writer_waits;
        // Already admitted: keep this frame. Pause the producer until disk catches
        // up. The condition wait releases the gate, so STOP stays responsive.
        // Memory is bounded by the limit plus at most one frame per producer.
        cv_.wait(lock,[&]{return failed_ || closed_ || queued_bytes_<limit;});
    }
    return !failed_;
}
void Recorder::close() {
    {std::lock_guard<std::mutex> lock(mutex_);
     if(!closed_) {if(!failed_) finishLocked("shutdown");closed_=true;cv_.notify_all();}}
    if(thread_.joinable()) thread_.join();
}
void Recorder::run() {
    try {
        std::ofstream manifest;manifest.exceptions(std::ios::badbit|std::ios::failbit);
        std::array<uint64_t,2> saved{};
        for(;;) {
            Job job;
            {std::unique_lock<std::mutex> lock(mutex_);cv_.wait(lock,[&]{return closed_ || !jobs_.empty();});
             if(jobs_.empty()) break;job=std::move(jobs_.front());jobs_.pop_front();}
            auto dir=session_/job.recording;
            if(job.kind==Kind::Start) {
                if(!fs::create_directories(dir)) throw std::runtime_error("Refusing to overwrite recording: "+dir.string());
                textFile(dir/"IN_PROGRESS",job.summary);saved={};
                manifest.open(dir/"frames.csv");
                manifest<<"file,camera,index,serial,frame_id,camera_ns,host_received_ns,estimated_exposure_host_ns,clock_uncertainty_ms,source_format,stored_bits,full_scale,record_admitted_ns\n";
            } else if(job.kind==Kind::Finish) {
                manifest.close();
                if(saved!=job.expected) throw std::runtime_error("Recording frame count mismatch");
                textFile(dir/"summary.txt",job.summary+"saved_cam0="+std::to_string(saved[0])+"\nsaved_cam1="+std::to_string(saved[1])+"\n");
                fs::remove(dir/"IN_PROGRESS");
            } else {
                if(fs::space(dir).available<job.bytes+64*1024*1024) throw std::runtime_error("Disk nearly full");
                const auto& f=*job.frame;
                auto relative=fs::path("cam"+std::to_string(job.camera)+"_"+f.serial)/frameName(job.index,f.id);
                auto path=dir/relative;fs::create_directories(path.parent_path());
                auto temporary=path.parent_path()/(path.stem().string()+".partial.tiff");
                if(fs::exists(path)||fs::exists(temporary)) throw std::runtime_error("Refusing to overwrite raw image");
                if(!cv::imwrite(temporary.string(),f.raw,{cv::IMWRITE_TIFF_COMPRESSION,1})) throw std::runtime_error("TIFF write failed");
                fs::rename(temporary,path);
                manifest<<std::setprecision(17)<<csv(relative.generic_string())<<','<<job.camera<<','<<job.index<<','<<csv(f.serial)
                        <<','<<f.id<<','<<f.camera_ns<<','<<f.host_ns<<','<<f.exposure_host_ns<<','<<f.clock_uncertainty_ms
                        <<','<<csv(f.source_format)<<','<<f.raw.elemSize()*8<<','<<f.full_scale<<','<<job.admitted_ns<<'\n';
                manifest.flush();++saved[job.camera];++shared_.saved[job.camera];--shared_.pending_frames;
                {std::lock_guard<std::mutex> lock(mutex_);queued_bytes_-=job.bytes;shared_.queued_bytes=queued_bytes_;cv_.notify_all();}
            }
        }
    } catch(const std::exception& e) {
        {std::lock_guard<std::mutex> lock(mutex_);failed_=true;closed_=true;active_.clear();shared_.recording=false;
         jobs_.clear();queued_bytes_=0;shared_.queued_bytes=0;cv_.notify_all();}
        // Pending/accepted-minus-saved counters intentionally remain visible.
        // IN_PROGRESS is kept: failed recordings must never appear complete.
        shared_.fail(std::string("Storage error: ")+e.what());
    }
}
} // namespace holo
