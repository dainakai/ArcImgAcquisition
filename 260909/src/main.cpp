#include "cameras.hpp"
#include "recorder.hpp"
#include <csignal>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace {
constexpr const char* WINDOW="Dual Hologram Viewer";
volatile std::sig_atomic_t interrupted=0;
void signalHandler(int) {interrupted=1;}
void help() {
    std::cout<<"Dual Hologram Viewer (C++ / Spinnaker)\n"
             <<"  --config FILE       default: config.yml beside the application\n"
             <<"  --simulate          synthetic two-camera input; no hardware\n"
             <<"  --headless          no display\n"
             <<"  --seconds N         stop after N seconds (0: unlimited)\n"
             <<"  --output DIR        override capture root\n"
             <<"  --record-at N       SIMULATION ONLY: test a REC start after N seconds\n"
             <<"  --record-for N      SIMULATION ONLY: stop the test recording after N seconds\n"
             <<"  --help              this message\n"
             <<"Click REC / STOP REC, or press R to start/stop recording.\n"
             <<"N: display contrast only. Q / Esc: quit after queued images are saved.\n"
             <<"Always starts with REC OFF. No object detection or automatic image saving.\n";
}
std::string number(double v,int precision=1) {std::ostringstream s;s<<std::fixed<<std::setprecision(precision)<<v;return s.str();}
cv::Mat panel(const holo::FramePtr& frame,int width,bool normalize) {
    if(!frame) {cv::Mat blank(640,width,CV_8UC3,cv::Scalar(18,18,18));cv::putText(blank,"Waiting for hardware triggers...",{20,80},cv::FONT_HERSHEY_SIMPLEX,.6,{220,220,220},1,cv::LINE_AA);return blank;}
    cv::Mat scaled,gray,image;
    double scale=width/static_cast<double>(frame->raw.cols);
    cv::resize(frame->raw,scaled,{width,std::max(1,static_cast<int>(frame->raw.rows*scale))},0,0,cv::INTER_AREA);
    if(normalize) {double lo=0,hi=0;cv::minMaxLoc(scaled,&lo,&hi);scaled.convertTo(gray,CV_8U,255/std::max(1.0,hi-lo),-lo*255/std::max(1.0,hi-lo));}
    else scaled.convertTo(gray,CV_8U,255/frame->full_scale);
    cv::cvtColor(gray,image,cv::COLOR_GRAY2BGR);
    cv::copyMakeBorder(image,image,72,0,0,0,cv::BORDER_CONSTANT,cv::Scalar(22,22,22));
    cv::putText(image,frame->serial+" | "+frame->source_format+" | frame "+std::to_string(frame->id),{12,24},cv::FONT_HERSHEY_SIMPLEX,.55,{230,230,230},1,cv::LINE_AA);
    cv::putText(image,"Received "+number((holo::nowNs()-frame->host_ns)/1e6)+" ms ago",{12,51},cv::FONT_HERSHEY_SIMPLEX,.45,{190,210,190},1,cv::LINE_AA);return image;
}
struct Controls {
    cv::Rect record_button;
    bool toggle=false;
    static void mouse(int event,int x,int y,int,void* context) {
        auto& c=*static_cast<Controls*>(context);
        if(event==cv::EVENT_LBUTTONUP && c.record_button.contains(cv::Point(x,y))) c.toggle=true;
    }
};
}
int main(int argc,char** argv) {
    using namespace holo;
    try {
        Config cfg;bool simulate=false,headless=false;
        double seconds=0,record_at=-1,record_for=-1;
        std::string config=std::filesystem::exists("config.yml")?"config.yml":"";
        if(config.empty()) {
            auto parent=std::filesystem::absolute(argv[0]).parent_path();
            for(int level=0;level<5;++level,parent=parent.parent_path()) if(std::filesystem::exists(parent/"config.yml")) {config=(parent/"config.yml").string();break;}
        }
        for(int i=1;i<argc;++i) if(std::string(argv[i])=="--config") {if(i+1>=argc) throw std::runtime_error("--config requires a path");config=argv[++i];}
        if(!config.empty()) {
            cfg.read(config);
            if(std::filesystem::path(cfg.output_dir).is_relative()) cfg.output_dir=(std::filesystem::absolute(config).parent_path()/cfg.output_dir).string();
        }
        for(int i=1;i<argc;++i) {
            std::string arg=argv[i];auto value=[&](){if(i+1>=argc) throw std::runtime_error("Missing value for "+arg);return std::string(argv[++i]);};
            if(arg=="--config") value();else if(arg=="--simulate") simulate=true;else if(arg=="--headless") headless=true;
            else if(arg=="--seconds") seconds=std::stod(value());else if(arg=="--record-at") record_at=std::stod(value());
            else if(arg=="--record-for") record_for=std::stod(value());else if(arg=="--output") cfg.output_dir=value();
            else if(arg=="--help") {help();return 0;}else throw std::runtime_error("Unknown argument: "+arg);
        }
        cfg.validate();
        if(!std::isfinite(seconds)||seconds<0||!std::isfinite(record_at)||!std::isfinite(record_for)||record_at < -1||record_for < -1)
            throw std::runtime_error("Invalid runtime");
        if((record_at>=0 || record_for>=0) && !simulate) throw std::runtime_error("Scheduled recording flags are for --simulate tests only; use the REC button for cameras");
        if(record_for>=0 && record_at<0) throw std::runtime_error("--record-for requires --record-at");
        cv::setNumThreads(cfg.opencv_threads);std::signal(SIGINT,signalHandler);std::signal(SIGTERM,signalHandler);
        auto session=std::filesystem::absolute(cfg.output_dir)/("session_"+timestamp()+(simulate?"_SIMULATION":""));
        if(!std::filesystem::create_directories(session)) throw std::runtime_error("Session directory already exists");
        cfg.write(session/"config.yml");std::cout<<"Session: "<<session<<std::endl;
        Shared shared;Pairer pairer(cfg);std::mutex pair_mutex;Recorder recorder(cfg,shared,session);
        auto on_frame=[&](int i,FramePtr frame) {
            {std::lock_guard<std::mutex> lock(shared.mutex);shared.latest[i]=frame;}
            // Recording is independent of pairing and preview refresh rate.
            recorder.consume(i,frame);
            std::lock_guard<std::mutex> lock(pair_mutex);
            shared.pairs+=pairer.add(i,std::move(frame)).size();shared.unmatched=pairer.unmatched();
        };
        std::unique_ptr<Cameras> cameras;std::thread simulator;bool gui_open=false;Controls controls;
        try {
            if(!headless) {
                cv::namedWindow(WINDOW,cv::WINDOW_NORMAL);cv::resizeWindow(WINDOW,cfg.display_width,850);gui_open=true;
                cv::setMouseCallback(WINDOW,Controls::mouse,&controls);
            }
            if(simulate) {
                simulator=std::thread([&] {try {
                    auto next=Clock::now();int64_t first=nowNs();
                    for(int index=0;!shared.stop;++index) {
                        auto exposure=first+static_cast<int64_t>(index*1e9/cfg.expected_hz);
                        for(int i=0;i<2;++i) {
                            auto frame=std::make_shared<Frame>();frame->raw=syntheticImage(index,i);frame->serial="SIM"+std::to_string(i);
                            frame->id=index+1;frame->camera_ns=exposure+i*1000000000LL;frame->exposure_host_ns=exposure;
                            frame->host_ns=nowNs();frame->source_format="Mono8";++shared.received[i];on_frame(i,frame);
                        }
                        next+=std::chrono::nanoseconds(static_cast<int64_t>(1e9/cfg.expected_hz));std::this_thread::sleep_until(next);
                    }
                } catch(const std::exception& e) {shared.fail(e.what());}});
            } else {cameras=std::make_unique<Cameras>(cfg,shared,session,on_frame);cameras->start();}
            auto start=nowNs(),last_report=start,last_draw=int64_t{0};bool normalized=false,test_started=false,test_stopped=false;
            std::array<uint64_t,2> last_draw_id{},last_report_count{};
            while(!shared.stop && !interrupted) {
                auto now=nowNs();double elapsed=(now-start)/1e9;
                if(seconds>0 && elapsed>=seconds) break;
                if(record_at>=0 && elapsed>=record_at && !test_started) {recorder.setRecording(true,"simulation_test");test_started=true;}
                if(test_started && record_for>=0 && elapsed>=record_at+record_for && !test_stopped) {recorder.setRecording(false,"simulation_test");test_stopped=true;}
                if(now-last_report>=2'000'000'000LL) {
                    double duration=(now-last_report)/1e9;
                    std::cout<<"received="<<shared.received[0]<<','<<shared.received[1]<<" fps="
                             <<number((shared.received[0]-last_report_count[0])/duration)<<','<<number((shared.received[1]-last_report_count[1])/duration)
                             <<" pairs="<<shared.pairs<<" unmatched="<<shared.unmatched<<" gaps="<<shared.frame_gaps[0]<<','<<shared.frame_gaps[1]
                             <<" REC="<<shared.recording<<" accepted="<<shared.accepted[0]<<','<<shared.accepted[1]
                             <<" saved="<<shared.saved[0]<<','<<shared.saved[1]<<" pending="<<shared.pending_frames
                             <<" writer_waits="<<shared.writer_waits<<std::endl;
                    for(int i=0;i<2;++i) last_report_count[i]=shared.received[i];last_report=now;
                }
                if(!headless) {
                    std::array<FramePtr,2> frames;
                    {std::lock_guard<std::mutex> lock(shared.mutex);frames=shared.latest;}
                    bool changed=false;for(int i=0;i<2;++i) if(frames[i] && frames[i]->id!=last_draw_id[i]) changed=true;
                    if(changed || now-last_draw>250'000'000LL) {
                        std::array<cv::Mat,2> images;
                        for(int i=0;i<2;++i) {images[i]=panel(frames[i],cfg.display_width/2,normalized);if(frames[i]) last_draw_id[i]=frames[i]->id;}
                        int height=std::max(images[0].rows,images[1].rows);
                        for(auto& im:images) cv::copyMakeBorder(im,im,0,height-im.rows,0,0,cv::BORDER_CONSTANT);
                        cv::Mat canvas;cv::hconcat(images[0],images[1],canvas);cv::copyMakeBorder(canvas,canvas,0,142,0,0,cv::BORDER_CONSTANT,cv::Scalar(25,25,25));
                        const bool rec=shared.recording;
                        controls.record_button={16,height+12,190,54};
                        cv::rectangle(canvas,controls.record_button,rec?cv::Scalar(35,35,185):cv::Scalar(60,60,60),cv::FILLED);
                        cv::circle(canvas,{39,height+39},8,rec?cv::Scalar(255,255,255):cv::Scalar(50,50,240),cv::FILLED);
                        cv::putText(canvas,rec?"STOP REC":"REC",{60,height+46},cv::FONT_HERSHEY_SIMPLEX,.65,{255,255,255},2,cv::LINE_AA);
                        std::string state=rec?"RECORDING  "+number((now-shared.record_started_ns)/1e9)+" s":shared.pending_frames?"REC OFF - finishing queued writes":"REC OFF - preview only";
                        cv::putText(canvas,state+(simulate?" | SIMULATION":""),{226,height+30},cv::FONT_HERSHEY_SIMPLEX,.55,rec?cv::Scalar(100,140,255):cv::Scalar(220,220,220),1,cv::LINE_AA);
                        cv::putText(canvas,"R: Rec start/stop | N: display contrast | Q / Esc: Quit",{226,height+57},cv::FONT_HERSHEY_SIMPLEX,.47,{235,235,235},1,cv::LINE_AA);
                        std::string counts="Saved cam0 / cam1: "+std::to_string(shared.saved[0])+" / "+std::to_string(shared.saved[1])+" | Pending: "+std::to_string(shared.pending_frames)+" | Queue: "+number(shared.queued_bytes/1048576.)+" MB";
                        cv::putText(canvas,counts,{16,height+91},cv::FONT_HERSHEY_SIMPLEX,.48,{210,220,220},1,cv::LINE_AA);
                        const bool issue=shared.writer_waits>0 || shared.frame_gaps[0]>0 || shared.frame_gaps[1]>0 || shared.incomplete[0]>0 || shared.incomplete[1]>0;
                        std::string metrics="Pairs: "+std::to_string(shared.pairs)+" | Unmatched: "+std::to_string(shared.unmatched)+" | Gaps: "+std::to_string(shared.frame_gaps[0])+" / "+std::to_string(shared.frame_gaps[1])+" | Incomplete: "+std::to_string(shared.incomplete[0])+" / "+std::to_string(shared.incomplete[1])+" | Disk waits: "+std::to_string(shared.writer_waits);
                        cv::putText(canvas,metrics,{16,height+119},cv::FONT_HERSHEY_SIMPLEX,.45,issue?cv::Scalar(80,180,255):cv::Scalar(180,180,180),1,cv::LINE_AA);
                        cv::imshow(WINDOW,canvas);last_draw=now;
                        if(frames[0]&&frames[1]) shared.display_age_ms=(nowNs()-std::min(frames[0]->host_ns,frames[1]->host_ns))/1e6;
                    }
                    int key=cv::waitKey(1)&0xff;
                    if(key=='q'||key=='Q'||key==27) break;
                    if(key=='r'||key=='R'||controls.toggle) {controls.toggle=false;recorder.setRecording(!shared.recording);last_draw=0;}
                    if(key=='n'||key=='N') {normalized=!normalized;last_draw=0;}
                    if(cv::getWindowProperty(WINDOW,cv::WND_PROP_VISIBLE)<1) break;
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
        } catch(const std::exception& e) {shared.fail(e.what());}
        recorder.setRecording(false,"shutdown");shared.stop=true;
        if(simulator.joinable()) simulator.join();if(cameras) cameras->stop();
        recorder.close();if(gui_open) cv::destroyAllWindows();
        cv::FileStorage stats((session/"run_summary.yml").string(),cv::FileStorage::WRITE);
        stats<<"simulation"<<static_cast<int>(simulate)<<"pairs"<<static_cast<double>(shared.pairs)<<"unmatched"<<static_cast<double>(shared.unmatched)
             <<"recordings"<<static_cast<double>(shared.recordings)<<"writer_waits"<<static_cast<double>(shared.writer_waits)
             <<"pending_frames"<<static_cast<double>(shared.pending_frames)<<"last_display_receive_age_ms"<<shared.display_age_ms.load();
        for(int i=0;i<2;++i) stats<<("received_cam"+std::to_string(i))<<static_cast<double>(shared.received[i])
            <<("accepted_cam"+std::to_string(i))<<static_cast<double>(shared.accepted[i])<<("saved_cam"+std::to_string(i))<<static_cast<double>(shared.saved[i])
            <<("incomplete_cam"+std::to_string(i))<<static_cast<double>(shared.incomplete[i])<<("frame_gaps_cam"+std::to_string(i))<<static_cast<double>(shared.frame_gaps[i]);
        {std::lock_guard<std::mutex> lock(shared.mutex);stats<<"error"<<shared.error;std::cout<<"Finished: "<<session<<std::endl;if(!shared.error.empty()) {std::cerr<<shared.error<<std::endl;return 1;}}
        return 0;
    } catch(const std::exception& e) {std::cerr<<"Error: "<<e.what()<<std::endl;return 1;}
}
