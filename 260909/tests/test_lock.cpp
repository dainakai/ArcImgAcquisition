#include "camera_lock.hpp"
#include <chrono>
#include <iostream>
#include <stdexcept>
int main() {
    try {
        const auto name="dual_holo_test_"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
        holo::CameraLock first(name),second(name);
        first.acquire();bool rejected=false;
        try {second.acquire();} catch(const std::runtime_error&) {rejected=true;}
        if(!rejected) throw std::runtime_error("Second camera lock was accepted");
        first.release();second.acquire();second.release();first.acquire();
        std::cout<<"Camera lock exclusion and release passed\n";return 0;
    } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
