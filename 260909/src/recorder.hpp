#pragma once
#include "core.hpp"
#include <fstream>
namespace holo {
// All raw frames are admitted at this gate, independently of timestamp pairing.
// FIFO storage never drops an admitted frame to keep up with the preview.
class Recorder {
public:
    Recorder(const Config&, Shared&, std::filesystem::path session);
    ~Recorder();
    bool setRecording(bool enabled, const std::string& reason="button");
    bool consume(int camera, FramePtr frame);
    void close();
private:
    enum class Kind { Start, Frame, Finish };
    struct Job {
        Kind kind=Kind::Frame;
        std::string recording, summary;
        FramePtr frame;
        int camera=0;
        uint64_t index=0;
        int64_t admitted_ns=0;
        size_t bytes=0;
        std::array<uint64_t,2> expected{};
    };
    Config cfg_; Shared& shared_; std::filesystem::path session_;
    std::mutex mutex_; std::condition_variable cv_; std::deque<Job> jobs_;
    size_t queued_bytes_=0;
    bool closed_=false, failed_=false;
    std::string active_;
    std::array<uint64_t,2> admitted_{}, last_id_{}, id_gaps_{}, initial_gaps_{}, initial_incomplete_{};
    int64_t started_ns_=0;
    std::thread thread_;
    void finishLocked(const std::string& reason);
    void run();
};
} // namespace holo
