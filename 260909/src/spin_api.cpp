#include "spin_api.hpp"
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
#include <system_error>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace holo {
SpinApi::SpinApi() {
    static_assert(sizeof(void*)==8 && sizeof(int)==4,"Spinnaker runtime binding requires a 64-bit build");
    std::vector<std::filesystem::path> paths;
    const char* explicit_path=std::getenv("SPINNAKER_C_LIBRARY");
    if(explicit_path && *explicit_path) {
        if(!std::filesystem::path(explicit_path).is_absolute())
            throw std::runtime_error("SPINNAKER_C_LIBRARY must be an absolute library path");
        paths.emplace_back(explicit_path);
    } else {
#ifdef _WIN32
        // Search SDK install locations, not the current working directory.
        const char* program_files=std::getenv("ProgramFiles");
        auto base=std::filesystem::path(program_files?program_files:"C:/Program Files");
        for(const auto& vendor:{"Teledyne/Spinnaker","Teledyne Spinnaker","FLIR Systems/Spinnaker"})
            for(const auto& dir:{"bin64/vs2015","bin64/vs2022","bin64"})
                for(const auto& file:{"SpinnakerC_v140.dll","SpinnakerC.dll","Spinnaker_C_v140.dll","Spinnaker_C.dll"})
                    paths.push_back(base/vendor/dir/file);
#elif defined(__APPLE__)
        paths.emplace_back("/Applications/Spinnaker/lib/libSpinnaker_C.dylib");
        paths.emplace_back("/usr/local/lib/libSpinnaker_C.dylib");
#else
        paths.emplace_back("/opt/spinnaker/lib/libSpinnaker_C.so");
        paths.emplace_back("/opt/spinnaker/lib/libSpinnaker_C.so.4");
        paths.emplace_back("/usr/lib/libSpinnaker_C.so");
        paths.emplace_back("/usr/lib/x86_64-linux-gnu/libSpinnaker_C.so");
        paths.emplace_back("/usr/local/lib/libSpinnaker_C.so");
        paths.emplace_back("libSpinnaker_C.so");
        paths.emplace_back("libSpinnaker_C.so.4");
#endif
    }
    std::string errors;
    for(const auto& path:paths) {
#ifdef _WIN32
        library_=LoadLibraryExW(path.c_str(),nullptr,LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR|LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
        if(!library_) {
            const auto error=GetLastError();
            errors+=path.string()+": Windows error "+std::to_string(error)+" ("+
                std::system_category().message(static_cast<int>(error))+")\n";
        }
#else
        library_=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL);
        if(!library_) {const char* err=dlerror();errors+=path.string()+": "+(err?err:"load failed")+"\n";}
#endif
        if(library_) {path_=path.string();break;}
    }
    if(!library_) throw std::runtime_error("Spinnaker C runtime not found. Install the 64-bit Spinnaker SDK/runtime and camera drivers, or set SPINNAKER_C_LIBRARY to its full library path. Use --simulate without hardware.\n"+errors);
    try {
#define HOLO_LOAD(name,args) name=reinterpret_cast<decltype(name)>(symbol("spin" #name));
        HOLO_SPIN_FUNCTIONS(HOLO_LOAD)
#undef HOLO_LOAD
    } catch(...) {unload();throw;}
}
SpinApi::~SpinApi() {unload();}
void SpinApi::unload() noexcept {
    if(!library_) return;
#ifdef _WIN32
    FreeLibrary(static_cast<HMODULE>(library_));
#else
    dlclose(library_);
#endif
    library_=nullptr;
}
void* SpinApi::symbol(const char* name) const {
#ifdef _WIN32
    auto result=reinterpret_cast<void*>(GetProcAddress(static_cast<HMODULE>(library_),name));
#else
    auto result=dlsym(library_,name);
#endif
    if(!result) throw std::runtime_error(path_+" is missing "+name+"; install Spinnaker 4.x with C API support");
    return result;
}
void SpinApi::check(Error error,const char* operation) const {
    if(!error) return;
    char message[2048]{};size_t length=sizeof(message);
    // The SDK's last error is shared across threads; retain operation and code.
    ErrorGetLastMessage(message,&length);message[sizeof(message)-1]='\0';
    throw std::runtime_error(std::string(operation)+" failed ("+std::to_string(error)+"): "+message);
}
SpinApi::Handle SpinApi::node(Handle map,const char* name) const {
    Handle result=nullptr;
    if(NodeMapGetNode(map,name,&result)!=0) return nullptr;
    return result;
}
bool SpinApi::readable(Handle n) const {Bool result=0;return n && NodeIsReadable(n,&result)==0 && result;}
bool SpinApi::writable(Handle n) const {Bool result=0;return n && NodeIsWritable(n,&result)==0 && result;}
std::string SpinApi::value(Handle map,const char* name) const {
    auto n=node(map,name);if(!readable(n)) return "unavailable";
    char buffer[4096]{};size_t length=sizeof(buffer);
    check(NodeToString(n,buffer,&length),name);buffer[sizeof(buffer)-1]='\0';return buffer;
}
int64_t SpinApi::integer(Handle n) const {int64_t v=0;check(IntegerGetValue(n,&v),"IntegerGetValue");return v;}
}
