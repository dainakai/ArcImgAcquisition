#include "core.hpp"
#include "recorder.hpp"
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
using namespace holo;
namespace fs=std::filesystem;
void require(bool x,const std::string& message) {if(!x) throw std::runtime_error(message);}
FramePtr frame(uint64_t id,int64_t ns,int camera=0,bool sixteen=false,cv::Size size={192,192}) {
    auto f=std::make_shared<Frame>();f->id=id;f->exposure_host_ns=ns;f->camera_ns=ns;f->host_ns=ns;
    f->serial="TEST"+std::to_string(camera);f->source_format=sixteen?"Mono16":"Mono8";f->full_scale=sixteen?65535:255;
    f->raw=cv::Mat(size,sixteen?CV_16U:CV_8U);cv::RNG rng(id*17+camera+1);rng.fill(f->raw,cv::RNG::UNIFORM,0,f->full_scale);return f;
}
std::string read(const fs::path& p) {std::ifstream f(p);return std::string((std::istreambuf_iterator<char>(f)),{});}
size_t images(const fs::path& root) {size_t n=0;for(auto& e:fs::recursive_directory_iterator(root)) if(e.path().extension()==".tiff") ++n;return n;}
int main() {
    try {
        cv::setNumThreads(2);Config cfg;cfg.validate();Pairer pairs(cfg);
        require(pairs.add(0,frame(41,100000000)).empty(),"must wait for peer");
        auto match=pairs.add(1,frame(1,101000000,1));require(match.size()==1,"different ID epochs must match by time");
        pairs.add(0,frame(42,200000000));pairs.add(0,frame(43,300000000));
        match=pairs.add(1,frame(3,301000000,1));require(match.size()==1 && match[0]->frames[0]->id==43 && pairs.unmatched()==1,"missing trigger must not shift pairing");
        auto uncertain=std::make_shared<Frame>(*frame(44,400000000));uncertain->clock_uncertainty_ms=6;
        pairs.add(0,uncertain);auto peer=std::make_shared<Frame>(*frame(4,401000000,1));peer->clock_uncertainty_ms=6;
        require(pairs.add(1,peer).empty(),"clock uncertainty prevents ambiguous pairing");
        auto root=fs::temp_directory_path()/("dual_holo_rec_test_"+timestamp());fs::create_directories(root);
        Shared idle;
        {Recorder rec(cfg,idle,root/"idle");for(int n=0;n<5;++n) require(!rec.consume(n%2,frame(n,100,n%2)),"idle gate");rec.close();}
        require(!fs::exists(root/"idle") && idle.saved[0]==0 && idle.saved[1]==0,"no files at all without REC");
        Shared shared;std::map<std::string,FramePtr> originals;
        {
            Recorder rec(cfg,shared,root/"recordings");require(!shared.recording,"startup REC OFF");
            rec.setRecording(true);require(!rec.setRecording(true),"duplicate start is idempotent");
            for(int i=0;i<3;++i) for(int cam=0;cam<2;++cam) if(cam==0 || i<2) {
                auto f=frame(41+i+cam*50,i*100000000,cam);
                originals[f->serial+"_"+std::to_string(f->id)]=f;
                require(rec.consume(cam,f),"all admitted frames including unmatched must save");
            }
            rec.setRecording(false);require(!shared.recording,"stop gate immediate");
            require(!rec.consume(0,frame(900,900)),"no post frames");
            rec.setRecording(true);auto f=frame(100,100,1,true);originals["TEST1_100"]=f;
            rec.consume(1,f);rec.close(); // Shutdown must finish an active recording and drain.
        }
        require(shared.accepted[0]==3 && shared.saved[0]==3 && shared.accepted[1]==3 && shared.saved[1]==3,"all 6 raw frames survive both recordings");
        require(shared.pending_frames==0 && !shared.stop && shared.recordings==2,"clean drain");
        int clips=0;
        for(auto& clip:fs::directory_iterator(root/"recordings")) {
            ++clips;require(fs::exists(clip.path()/"summary.txt")&&!fs::exists(clip.path()/"IN_PROGRESS"),"recording finalized only after successful writes");
            require(read(clip.path()/"summary.txt").find("status=complete")!=std::string::npos,"complete recording summary");
            for(auto& entry:fs::recursive_directory_iterator(clip.path())) if(entry.path().extension()==".tiff") {
                auto dir=entry.path().parent_path().filename().string();auto serial=dir.substr(dir.find('_')+1);
                auto name=entry.path().stem().string();auto id=name.substr(name.find("_id")+3);auto original=originals.at(serial+"_"+id);
                auto saved=cv::imread(entry.path().string(),cv::IMREAD_UNCHANGED);
                require(saved.type()==original->raw.type()&&cv::norm(saved,original->raw,cv::NORM_INF)==0,"raw 8/16-bit TIFF bit exact");
            }
        }
        require(clips==2 && images(root/"recordings")==6,"no pre/post or duplicate images");
        Shared gaps;
        {Recorder rec(cfg,gaps,root/"gaps");rec.setRecording(true);rec.consume(0,frame(10,100));rec.consume(0,frame(12,300));rec.close();}
        auto clip=fs::directory_iterator(root/"gaps")->path();
        require(read(clip/"summary.txt").find("status=capture_gaps")!=std::string::npos,"hardware gaps visibly marked");
        Shared pressure;
        {auto limited=cfg;limited.writer_queue_mb=32;Recorder rec(limited,pressure,root/"pressure");rec.setRecording(true);
         require(rec.consume(0,frame(1,100,0,true,{4096,4096})),"oversized frame waits, never dropped");rec.close();}
        require(pressure.writer_waits==1 && pressure.saved[0]==1 && pressure.pending_frames==0,"backpressure drains without loss");
        Shared concurrent;
        {
            auto limited=cfg;limited.writer_queue_mb=32;Recorder rec(limited,concurrent,root/"concurrent");rec.setRecording(true);
            std::array<std::thread,2> threads;
            for(int cam=0;cam<2;++cam) threads[cam]=std::thread([&,cam]{for(int i=1;i<=8;++i) rec.consume(cam,frame(i,i*100000000,cam,true,{2448,2048}));});
            for(auto& thread:threads) thread.join();rec.setRecording(false);rec.close();
        }
        require(concurrent.saved[0]==8 && concurrent.saved[1]==8 && concurrent.pending_frames==0,"two native-size producer threads save every frame");
        Shared failure;std::ofstream(root/"not_a_directory")<<"test";
        {Recorder rec(cfg,failure,root/"not_a_directory");rec.setRecording(true);rec.consume(0,frame(1,100));rec.close();}
        require(failure.stop && !failure.error.empty() && failure.saved[0]==0,"storage failure is explicit and stops acquisition");
        fs::remove_all(root);
        std::cout<<"All tests passed: pairing, idle zero files, REC boundaries, unmatched frames, repeated REC, shutdown drain, exact 8/16-bit TIFF, gap reporting, non-dropping backpressure, concurrent native frames, disk failure.\n";
        return 0;
    } catch(const std::exception& e) {std::cerr<<"TEST FAILED: "<<e.what()<<'\n';return 1;}
}
