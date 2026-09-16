#include "mac_app.hpp"
#import <AppKit/AppKit.h>
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
        return std::string([[[bundle URLByDeletingLastPathComponent] path] fileSystemRepresentation]);
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
