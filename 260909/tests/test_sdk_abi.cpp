// Optional compile-time verification against a separately installed vendor SDK.
#include "spin_api.hpp"
#include <SpinnakerC.h>
#include <SpinnakerGenApiC.h>
#include <type_traits>
template<class T> struct ABI {using type=std::conditional_t<std::is_enum_v<T>,int,T>;};
template<class T> struct ABI<T*> {using type=typename ABI<T>::type*;};
template<class R,class... Args> struct ABI<R(*)(Args...)> {using type=typename ABI<R>::type(*)(typename ABI<Args>::type...);};
#define CHECK_ABI(name,args) static_assert(std::is_same_v<ABI<decltype(holo::SpinApi::name)>::type,ABI<decltype(&spin##name)>::type>,#name " ABI mismatch");
HOLO_SPIN_FUNCTIONS(CHECK_ABI)
#undef CHECK_ABI
static_assert(sizeof(spinError)==sizeof(int));
static_assert(sizeof(spinPixelFormatEnums)==sizeof(holo::SpinApi::PixelFormat));
static_assert(static_cast<int>(PixelFormat_Mono8)==static_cast<int>(holo::SpinApi::Mono8));
static_assert(static_cast<int>(PixelFormat_Mono16)==static_cast<int>(holo::SpinApi::Mono16));
static_assert(SPINNAKER_ERR_TIMEOUT==holo::SpinApi::Timeout);
int main() {return 0;}
