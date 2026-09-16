#include "windows_app.hpp"
#include <windows.h>
#include <shlobj.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace holo {
namespace {
std::filesystem::path userFolder(REFKNOWNFOLDERID id) {
    PWSTR value=nullptr;
    const HRESULT result=SHGetKnownFolderPath(id,0,nullptr,&value);
    if(FAILED(result)) throw std::runtime_error("Cannot locate the Windows user folder; use --output DIR");
    std::filesystem::path path(value);
    CoTaskMemFree(value);
    return path;
}
std::wstring fromAnsi(const std::string& text) {
    const int length=MultiByteToWideChar(CP_ACP,0,text.data(),static_cast<int>(text.size()),nullptr,0);
    std::wstring result(length,L'\0');
    if(length) MultiByteToWideChar(CP_ACP,0,text.data(),static_cast<int>(text.size()),result.data(),length);
    return result;
}
std::string utf8(const std::wstring& text) {
    const int length=WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),nullptr,0,nullptr,nullptr);
    std::string result(length,'\0');
    if(length) WideCharToMultiByte(CP_UTF8,0,text.data(),static_cast<int>(text.size()),result.data(),length,nullptr,nullptr);
    return result;
}
}
std::string windowsCaptureRoot() {return (userFolder(FOLDERID_Pictures)/"DualHolo").string();}
void windowsReportError(const std::string& message,bool show_dialog) {
    std::wstring log_notice;
    try {
        const auto directory=userFolder(FOLDERID_LocalAppData)/"DualHolo"/"logs";
        std::filesystem::create_directories(directory);
        const auto file=directory/("startup-error-"+std::to_string(GetCurrentProcessId())+"-"+
                                   std::to_string(GetTickCount64())+".txt");
        std::ofstream log(file,std::ios::binary);
        log.exceptions(std::ios::failbit|std::ios::badbit);
        wchar_t executable[32768]{};
        GetModuleFileNameW(nullptr,executable,32768);
        log<<"\xEF\xBB\xBF"<<"DualHolo "<<HOLO_VERSION<<" (Windows x64)\n"
           <<"Executable: "<<utf8(executable)<<"\n"
           <<"Working directory: "<<utf8(std::filesystem::current_path().wstring())<<"\n\n"
           <<utf8(fromAnsi(message))<<"\n";
        log.close();
        log_notice=L"\n\nDetails saved to:\n"+file.wstring();
        std::cerr<<"Error log: "<<file<<std::endl;
    } catch(const std::exception& e) {
        std::cerr<<"Could not save error log: "<<e.what()<<std::endl;
        log_notice=L"\n\nCould not save an error log. Start with Run.cmd to keep the error visible.";
    }
    if(show_dialog) {
        // A console launched by Explorer disappears on exit; retain the actual
        // error in a native dialog. Full DLL search details stay in the log.
        const auto first_line=message.substr(0,message.find('\n'));
        const std::wstring body=fromAnsi(first_line)+log_notice+
            L"\n\nFor a camera-free GUI check, open Simulate.cmd.\n"
            L"Real cameras require the Spinnaker C runtime and drivers.";
        MessageBoxW(nullptr,body.c_str(),L"DualHolo - Startup error",MB_OK|MB_ICONERROR|MB_SETFOREGROUND);
    }
}
}
