#include <Spinnaker.h>
#include <SpinGenApi/SpinnakerGenApi.h>
#include <iostream>
#include <vector>
using namespace Spinnaker;
using namespace Spinnaker::GenApi;
int main() {
    auto system = System::GetInstance();
    auto cameras = system->GetCameras();
    std::cout << "Cameras: " << cameras.GetSize() << std::endl;
    int result = cameras.GetSize() == 0 ? 1 : 0;
    for (unsigned i = 0; i < cameras.GetSize(); ++i) {
        CameraPtr camera = cameras.GetByIndex(i);
        bool initialized = false;
        try {
            camera->Init(); initialized = true;
            auto& map = camera->GetNodeMap();
            std::cout << "Camera " << i << std::endl;
            for (const char* name : {"DeviceModelName", "DeviceSerialNumber", "Width", "Height", "PixelFormat", "AdcBitDepth", "ExposureTime", "ExposureAuto", "Gain", "GainAuto", "GammaEnable", "AcquisitionMode", "AcquisitionFrameRateEnable", "AcquisitionFrameRate", "TriggerSelector", "TriggerMode", "TriggerSource", "TriggerActivation", "TriggerDelay", "TimestampIncrement", "TimestampLatchValue", "DeviceLinkThroughputLimit"}) {
                CValuePtr node = map.GetNode(name);
                if (IsReadable(node)) std::cout << "  " << name << " = " << node->ToString() << std::endl;
            }
            for (const char* name : {"TimestampLatch", "TimestampReset"}) {
                CCommandPtr node = map.GetNode(name);
                std::cout << "  " << name << " writable = " << IsWritable(node) << std::endl;
            }
        } catch (const std::exception& e) { std::cerr << e.what() << std::endl; result = 1; }
        if (initialized) try { camera->DeInit(); } catch (...) { result = 1; }
        camera = nullptr;
    }
    cameras.Clear(); system->ReleaseInstance();
    return result;
}
