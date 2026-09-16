#pragma once
#include "core.hpp"
#include <Spinnaker.h>
#include <SpinGenApi/SpinnakerGenApi.h>
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
    Spinnaker::SystemPtr system_;
    Spinnaker::CameraList list_;
    std::array<Spinnaker::CameraPtr,2> cameras_;
    std::array<bool,2> initialized_{}, acquiring_{};
    int lock_fd_ = -1;
    std::array<ClockMap,2> clock_;
    std::array<std::thread,2> threads_;
    ClockMap calibrateClock(int index);
    void acquire(int index);
};
} // namespace holo
