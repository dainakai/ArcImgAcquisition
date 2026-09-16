include(FetchContent)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_LIST core,imgproc,imgcodecs,highgui CACHE STRING "" FORCE)
foreach(flag BUILD_TESTS BUILD_PERF_TESTS BUILD_EXAMPLES BUILD_opencv_apps BUILD_JAVA
    BUILD_opencv_python2 BUILD_opencv_python3 BUILD_opencv_java BUILD_DOCS
    WITH_FFMPEG WITH_GSTREAMER WITH_V4L WITH_OPENCL WITH_IPP WITH_ITT WITH_TBB
    WITH_LAPACK WITH_EIGEN WITH_OPENEXR WITH_WEBP WITH_JPEG WITH_PNG WITH_JASPER
    WITH_OPENJPEG WITH_AVIF WITH_PROTOBUF WITH_QUIRC WITH_CUDA WITH_QT
    WITH_GTK_2_X WITH_GTK_GLEXT WITH_1394 WITH_VTK)
  set(${flag} OFF CACHE BOOL "" FORCE)
endforeach()
set(WITH_TIFF ON CACHE BOOL "" FORCE)
set(BUILD_TIFF ON CACHE BOOL "" FORCE)
set(BUILD_ZLIB ON CACHE BOOL "" FORCE)
set(BUILD_WITH_STATIC_CRT ON CACHE BOOL "" FORCE)
if(UNIX AND NOT APPLE)
  set(WITH_GTK ON CACHE BOOL "" FORCE)
endif()
FetchContent_Declare(opencv
  URL https://github.com/opencv/opencv/archive/cbee6841638edb6fbc8110df7cd52bb8e3d66211.tar.gz
  URL_HASH SHA256=0f547a94412162228b1ec5c5d72445fb5789d6d1af0362b9c838fe45383a2107
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE) # OpenCV 4.12.0, immutable commit
FetchContent_GetProperties(opencv)
if(NOT opencv_POPULATED)
  FetchContent_Populate(opencv)
  add_subdirectory("${opencv_SOURCE_DIR}" "${opencv_BINARY_DIR}" EXCLUDE_FROM_ALL)
endif()
set(OpenCV_LIBS opencv_core opencv_imgproc opencv_imgcodecs opencv_highgui)
set(OpenCV_INCLUDE_DIRS "${OPENCV_CONFIG_FILE_INCLUDE_DIR}" "${opencv_BINARY_DIR}" "${opencv_SOURCE_DIR}/include")
foreach(module core imgproc imgcodecs highgui)
  list(APPEND OpenCV_INCLUDE_DIRS "${opencv_SOURCE_DIR}/modules/${module}/include")
endforeach()
# Keep upstream notices with the static OpenCV and TIFF/zlib code in the app.
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/third-party-licenses")
file(COPY "${opencv_SOURCE_DIR}/LICENSE" DESTINATION "${CMAKE_BINARY_DIR}/third-party-licenses/opencv")
file(GLOB_RECURSE notices LIST_DIRECTORIES FALSE
  "${opencv_SOURCE_DIR}/3rdparty/*LICENSE*" "${opencv_SOURCE_DIR}/3rdparty/*COPYING*"
  "${opencv_SOURCE_DIR}/3rdparty/*COPYRIGHT*")
foreach(notice IN LISTS notices)
  file(RELATIVE_PATH relative "${opencv_SOURCE_DIR}" "${notice}")
  get_filename_component(directory "${relative}" DIRECTORY)
  file(COPY "${notice}" DESTINATION "${CMAKE_BINARY_DIR}/third-party-licenses/${directory}")
endforeach()
