#pragma once
#include <string>

namespace holo {
// Same lock for acquisition and inspection. Never enumerate before acquiring it.
class CameraLock {
public:
    explicit CameraLock(std::string name="dual_holo_cameras");
    ~CameraLock();
    CameraLock(const CameraLock&)=delete;
    CameraLock& operator=(const CameraLock&)=delete;
    void acquire();
    void release() noexcept;
private:
    std::string name_;
#ifdef _WIN32
    void* handle_=nullptr;
#else
    int fd_=-1;
#endif
};
}
