#pragma once
#include <opencv2/core.hpp>
#include <opencv2/highgui.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace holo {
using Clock = std::chrono::steady_clock;
int64_t nowNs();
std::string timestamp();
std::string csv(const std::string& s);
struct Config {
    std::array<std::string, 2> serial;
    std::string output_dir = "captures", pixel_format = "keep";
    double expected_hz = 10, pair_tolerance_ms = 8, max_clock_uncertainty_ms = 3;
    int capture_queue = 12, writer_queue_mb = 512, opencv_threads = 4, display_width = 1500;
    void read(const std::string& file);
    void validate() const;
    void write(const std::filesystem::path& file) const;
};
struct Frame {
    cv::Mat raw;
    std::string serial, source_format;
    uint64_t id = 0, camera_ns = 0;
    int64_t host_ns = 0, exposure_host_ns = 0;
    double clock_uncertainty_ms = 0;
    double full_scale = 255;
};
using FramePtr = std::shared_ptr<const Frame>;
struct Pair {
    std::array<FramePtr, 2> frames;
    uint64_t sequence = 0;
    double skew_ms = 0;
    size_t bytes() const;
};
using PairPtr = std::shared_ptr<const Pair>;
class Pairer {
public:
    explicit Pairer(const Config& cfg) : tolerance_ns_(cfg.pair_tolerance_ms * 1e6), limit_(cfg.capture_queue) {}
    std::vector<PairPtr> add(int camera, FramePtr frame);
    uint64_t unmatched() const { return unmatched_; }
private:
    std::array<std::deque<FramePtr>, 2> pending_;
    double tolerance_ns_;
    size_t limit_;
    uint64_t sequence_ = 0, unmatched_ = 0;
};
struct Shared {
    std::atomic<bool> stop{false}, recording{false};
    std::array<std::atomic<uint64_t>, 2> received{}, incomplete{}, frame_gaps{}, capture_drops{}, timeouts{};
    std::array<std::atomic<uint64_t>, 2> accepted{}, saved{};
    std::atomic<uint64_t> pairs{0}, unmatched{0}, recordings{0}, writer_waits{0}, pending_frames{0}, queued_bytes{0};
    std::atomic<int64_t> record_started_ns{0};
    std::atomic<double> display_age_ms{0};
    std::mutex mutex;
    std::array<FramePtr, 2> latest;
    std::string status = "Preview only. Click REC or press R to record.", error;
    void fail(const std::string& message);
    void setStatus(const std::string& message);
};
cv::Mat syntheticImage(int index, int camera, cv::Size size = {640, 480});
} // namespace holo
