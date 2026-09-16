#include "mac_app.hpp"
#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <memory>
#include <stdexcept>

namespace holo {
std::string macBundledConfig() {
    @autoreleasepool {
        NSString* path=[[NSBundle mainBundle] pathForResource:@"config" ofType:@"yml"];
        return path ? std::string([path fileSystemRepresentation]) : std::string{};
    }
}
std::string macCaptureRoot() {
    @autoreleasepool {
        NSURL* bundle=[[NSBundle mainBundle] bundleURL];
        if(!bundle || ![[bundle pathExtension] isEqualToString:@"app"])
            throw std::runtime_error("Cannot locate DualHolo.app; use --output DIR");
        // NSBundle points into a read-only AppTranslocation mount for downloaded
        // apps. Ask macOS for the original URL, not the mount's parent directory.
        // This Security SPI also returns the input URL for ordinary launches.
        // Load at runtime because it is not declared in the public SDK headers.
        // Apple: Security/OSX/libsecurity_translocate/lib/SecTranslocate.h
        std::unique_ptr<void,decltype(&dlclose)> security(
            dlopen("/System/Library/Frameworks/Security.framework/Security",RTLD_LAZY|RTLD_LOCAL),&dlclose);
        using OriginalPath=CFURLRef(*)(CFURLRef,CFErrorRef*);
        auto originalPath=security ? reinterpret_cast<OriginalPath>(
            dlsym(security.get(),"SecTranslocateCreateOriginalPathForURL")) : nullptr;
        if(!originalPath) throw std::runtime_error("macOS could not resolve the original DualHolo.app location");
        CFErrorRef error=nullptr;
        using CFHandle=std::unique_ptr<const void,decltype(&CFRelease)>;
        CFHandle original(originalPath((CFURLRef)bundle,&error),&CFRelease);
        CFHandle failure(error,&CFRelease);
        if(!original) {
            const char* reason=error ? [[(NSError*)error localizedDescription] UTF8String] : "unknown error";
            throw std::runtime_error(std::string("Cannot find the original DualHolo.app location: ")+(reason?reason:"unknown error"));
        }
        return std::string([[[(NSURL*)original.get() URLByDeletingLastPathComponent] path] fileSystemRepresentation]);
    }
}
void macShowError(const std::string& message) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        [NSApp activateIgnoringOtherApps:YES];
        NSAlert* alert=[[NSAlert alloc] init];
        [alert setAlertStyle:NSAlertStyleCritical];
        [alert setMessageText:@"DualHolo could not continue"];
        [alert setInformativeText:[NSString stringWithUTF8String:message.c_str()]];
        [alert addButtonWithTitle:@"OK"];
        [alert runModal];
        [alert release];
    }
}
}
