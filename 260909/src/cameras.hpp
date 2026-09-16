#pragma once
#include "core.hpp"
#include "camera_lock.hpp"
#include "spin_api.hpp"
namespace holo {
class Cameras {
public:
    Cameras(const Config& config, Shared& shared, const std::filesystem::path& session,
            std::function<void(int, FramePtr)> on_frame);
    ~Cameras();
    void start();
    void stop();
private:
    struct ClockMap { double offset_ns=0, uncertainty_ms=0, latch_unit_ns=1; int64_t checked_ns=0; };
    Config cfg_;
    Shared& shared_;
    std::filesystem::path session_;
    std::function<void(int,FramePtr)> on_frame_;
    CameraLock lock_;
    std::unique_ptr<SpinApi> api_;
    SpinApi::Handle system_=nullptr, list_=nullptr;
    std::array<SpinApi::Handle,2> cameras_{};
    std::array<SpinApi::Handle,2> maps_{};
    std::array<bool,2> initialized_{}, acquiring_{};
    std::array<ClockMap,2> clock_;
    std::array<std::thread,2> threads_;
    ClockMap calibrateClock(int index);
    void acquire(int index);
};
} // namespace holo
