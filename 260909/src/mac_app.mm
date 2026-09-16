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
        NSURL* pictures=[[[NSFileManager defaultManager] URLsForDirectory:NSPicturesDirectory
            inDomains:NSUserDomainMask] firstObject];
        if(!pictures) throw std::runtime_error("Cannot locate the user's Pictures folder; use --output DIR");
        return std::string([[[pictures URLByAppendingPathComponent:@"DualHolo"] path] fileSystemRepresentation]);
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
