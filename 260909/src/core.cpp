#include "core.hpp"
#include <algorithm>
#include <cmath>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>

namespace holo {
int64_t nowNs() { return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count(); }
std::string timestamp() {
    auto now = std::chrono::system_clock::now(); auto t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    std::ostringstream s;
    s << std::put_time(&tm, "%Y%m%d_%H%M%S") << '_' << std::setw(6) << std::setfill('0')
      << std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count() % 1000000;
    return s.str();
}
std::string csv(const std::string& s) { std::string r = "\""; for(char c : s) { if(c=='\"') r += '"'; r += c; } return r + '"'; }
void Config::read(const std::string& file) {
    cv::FileStorage fs(file, cv::FileStorage::READ);
    if (!fs.isOpened()) throw std::runtime_error("Cannot open config: " + file);
    if (!fs["serial0"].empty()) fs["serial0"] >> serial[0];
    if (!fs["serial1"].empty()) fs["serial1"] >> serial[1];
#define READ(x) if (!fs[#x].empty()) fs[#x] >> x
    READ(output_dir); READ(pixel_format); READ(expected_hz); READ(pair_tolerance_ms);
    READ(max_clock_uncertainty_ms); READ(capture_queue); READ(writer_queue_mb);
    READ(opencv_threads); READ(display_width);
#undef READ
    validate();
}
void Config::validate() const {
    if (!std::isfinite(expected_hz) || expected_hz <= 0 || !std::isfinite(pair_tolerance_ms) ||
        pair_tolerance_ms <= 0 || pair_tolerance_ms >= 500.0 / expected_hz ||
        !std::isfinite(max_clock_uncertainty_ms) || max_clock_uncertainty_ms <= 0 ||
        capture_queue < 2 || writer_queue_mb < 32 || opencv_threads < 1 || display_width < 960 ||
        pixel_format != "keep" ||
        (!serial[0].empty() && serial[0] == serial[1])) throw std::runtime_error("Invalid configuration; see config.yml / README.md");
}
void Config::write(const std::filesystem::path& file) const {
    cv::FileStorage fs(file.string(), cv::FileStorage::WRITE);
    if (!fs.isOpened()) throw std::runtime_error("Cannot write configuration");
    fs << "serial0" << serial[0] << "serial1" << serial[1];
#define WRITE(x) fs << #x << x
    WRITE(output_dir); WRITE(pixel_format); WRITE(expected_hz); WRITE(pair_tolerance_ms);
    WRITE(max_clock_uncertainty_ms); WRITE(capture_queue); WRITE(writer_queue_mb);
    WRITE(opencv_threads); WRITE(display_width);
#undef WRITE
}
size_t Pair::bytes() const { size_t n = 0; for (auto& f : frames) n += f->raw.total() * f->raw.elemSize(); return n; }
std::vector<PairPtr> Pairer::add(int camera, FramePtr frame) {
    auto& queue = pending_.at(camera);
    // A timestamp moving backwards invalidates pending data for that camera.
    if (!queue.empty() && frame->exposure_host_ns <= queue.back()->exposure_host_ns) { unmatched_ += queue.size(); queue.clear(); }
    queue.push_back(std::move(frame));
    if (queue.size() > limit_) { queue.pop_front(); ++unmatched_; }
    std::vector<PairPtr> result;
    while (!pending_[0].empty() && !pending_[1].empty()) {
        auto a = pending_[0].front(), b = pending_[1].front();
        double delta = static_cast<double>(a->exposure_host_ns - b->exposure_host_ns);
        double uncertainty = (a->clock_uncertainty_ms + b->clock_uncertainty_ms) * 1e6;
        if (std::abs(delta) + uncertainty <= tolerance_ns_) {
            auto p = std::make_shared<Pair>(); p->frames = {a,b}; p->sequence = ++sequence_; p->skew_ms = delta / 1e6;
            result.push_back(p); pending_[0].pop_front(); pending_[1].pop_front();
        } else { pending_[delta < 0 ? 0 : 1].pop_front(); ++unmatched_; }
    }
    return result;
}
void Shared::fail(const std::string& message) { std::lock_guard<std::mutex> lock(mutex); if (error.empty()) error = message; std::cerr << message << std::endl; stop = true; }
void Shared::setStatus(const std::string& message) { std::lock_guard<std::mutex> lock(mutex); status = message; std::cout << message << std::endl; }
cv::Mat syntheticImage(int index, int camera, cv::Size size) {
    cv::Mat f(size, CV_32F);
    // A synthetic stress input, not a physical propagation model.
    double gain = 1.0 + .25 * std::sin(index * 1.3);
    bool particle = index >= 35 && index % 90 >= 35 && index % 90 <= 37;
    for (int y=0; y<f.rows; ++y) for (int x=0; x<f.cols; ++x) {
        double base = 120 + 15 * std::cos(x * .013) + 8 * std::sin(y * .027) + 3 * std::sin(x*.14+y*.07);
        double r2 = std::pow(x-f.cols*.5-camera*3, 2) + std::pow(y-f.rows*.5, 2);
        double ring = particle ? 30 * std::cos(r2 / (65.0+camera*20)) * std::exp(-r2/1800) : 0;
        f.at<float>(y,x) = static_cast<float>(gain * (base + ring));
    }
    cv::RNG rng(static_cast<uint64_t>(index*17+camera+1)); cv::Mat noise(size,CV_32F);
    rng.fill(noise,cv::RNG::NORMAL,0,.8); f += noise;
    cv::Mat raw; f.convertTo(raw,CV_8U); return raw;
}
} // namespace holo
