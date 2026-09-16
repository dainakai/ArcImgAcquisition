// Test-only runtime loader: no camera enumeration or initialization.
#include "spin_api.hpp"
#include <cstdlib>
#include <iostream>
#include <stdexcept>

int main(int argc,char** argv) {
    try {
        // Set inside the process so this test does not depend on inheritance
        // of the OS-managed ProgramFiles environment variable.
        if(argc!=2 || _putenv_s("ProgramFiles",argv[1])!=0)
            throw std::runtime_error("Cannot set the fake ProgramFiles root");
        holo::SpinApi api;
        std::cout<<"Spinnaker C runtime: "<<api.libraryPath()<<'\n';
        return 0;
    } catch(const std::exception& e) {
        std::cerr<<e.what()<<'\n';
        return 1;
    }
}
