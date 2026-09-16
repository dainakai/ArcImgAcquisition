#include "camera_lock.hpp"
#include <filesystem>
#include <stdexcept>
#include <utility>
#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

namespace holo {
CameraLock::CameraLock(std::string name):name_(std::move(name)) {}
CameraLock::~CameraLock() {release();}
void CameraLock::acquire() {
#ifdef _WIN32
    if(handle_) return;
    // A named kernel object survives until the last handle closes, including
    // process crashes. Global prevents a second Windows login opening cameras.
    HANDLE candidate=CreateMutexA(nullptr,FALSE,("Global\\"+name_).c_str());
    const DWORD error=GetLastError();
    if(!candidate) throw std::runtime_error("Cannot create camera lock (Windows error "+std::to_string(error)+")");
    if(error==ERROR_ALREADY_EXISTS) {
        CloseHandle(candidate);
        throw std::runtime_error("DualHolo is already using the cameras. Close the other instance first.");
    }
    handle_=candidate;
#else
    if(fd_>=0) return;
    // Retain the original POSIX lock name for compatibility with existing apps.
    auto path=std::filesystem::temp_directory_path()/(name_+"_"+std::to_string(getuid())+".lock");
    const int candidate=::open(path.c_str(),O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if(candidate<0) throw std::runtime_error("Cannot create the camera application lock");
    if(flock(candidate,LOCK_EX|LOCK_NB)!=0) {
        ::close(candidate);
        throw std::runtime_error("DualHolo is already using the cameras. Close the other instance first.");
    }
    fd_=candidate;
#endif
}
void CameraLock::release() noexcept {
#ifdef _WIN32
    if(handle_) {CloseHandle(static_cast<HANDLE>(handle_));handle_=nullptr;}
#else
    if(fd_>=0) {::close(fd_);fd_=-1;}
#endif
}
}
