// Small platform layer so the parser builds on Linux, macOS and Windows (MSVC or MinGW).
#pragma once
#ifdef _WIN32
#ifndef _USE_MATH_DEFINES
#define _USE_MATH_DEFINES  // M_PI from <cmath>
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#include <cstddef>
#include <cstdio>
#include <ctime>
#include <string>

// A whole file mapped read-only into memory (mmap; a file mapping on Windows).
class MappedFile {
 public:
  MappedFile() = default;
  MappedFile(const MappedFile &) = delete;
  MappedFile &operator=(const MappedFile &) = delete;
  ~MappedFile() { close(); }

  // false with a message in error when the file cannot be opened, is empty or cannot be mapped
  bool open(const std::string &path, std::string &error) {
#ifdef _WIN32
    file_ = CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                        FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    LARGE_INTEGER sz;
    if (file_ == INVALID_HANDLE_VALUE || !GetFileSizeEx(file_, &sz)) { error = "Cannot open DATA file"; return false; }
    if (sz.QuadPart == 0) { error = "Empty DATA file"; return false; }
    size_ = static_cast<size_t>(sz.QuadPart);
    mapping_ = CreateFileMappingA(file_, nullptr, PAGE_READONLY, 0, 0, nullptr);
    data_ = mapping_ ? static_cast<const char *>(MapViewOfFile(mapping_, FILE_MAP_READ, 0, 0, 0)) : nullptr;
    if (!data_) { error = "Cannot map DATA file"; return false; }
#else
    fd_ = ::open(path.c_str(), O_RDONLY);
    struct stat st;
    if (fd_ < 0 || fstat(fd_, &st) != 0) { error = "Cannot open DATA file"; return false; }
    if (st.st_size == 0) { error = "Empty DATA file"; return false; }
    size_ = static_cast<size_t>(st.st_size);
#ifdef MAP_POPULATE
    void *map = mmap(nullptr, size_, PROT_READ, MAP_PRIVATE | MAP_POPULATE, fd_, 0);
    if (map == MAP_FAILED) { map = mmap(nullptr, size_, PROT_READ, MAP_PRIVATE, fd_, 0); }
#else
    void *map = mmap(nullptr, size_, PROT_READ, MAP_PRIVATE, fd_, 0);
#endif
    if (map == MAP_FAILED) { error = "mmap failed"; return false; }
    madvise(map, size_, MADV_SEQUENTIAL);
    data_ = static_cast<const char *>(map);
#endif
    return true;
  }

  void close() {
#ifdef _WIN32
    if (data_) { UnmapViewOfFile(data_); }
    if (mapping_) { CloseHandle(mapping_); }
    if (file_ != INVALID_HANDLE_VALUE) { CloseHandle(file_); }
    mapping_ = nullptr;
    file_ = INVALID_HANDLE_VALUE;
#else
    if (data_) { munmap(const_cast<char *>(data_), size_); }
    if (fd_ >= 0) { ::close(fd_); }
    fd_ = -1;
#endif
    data_ = nullptr;
    size_ = 0;
  }

  const char *data() const { return data_; }
  size_t size() const { return size_; }

 private:
  const char *data_ = nullptr;
  size_t size_ = 0;
#ifdef _WIN32
  HANDLE file_ = INVALID_HANDLE_VALUE, mapping_ = nullptr;
#else
  int fd_ = -1;
#endif
};

// gmtime_r, also on Windows
inline void utc_tm(time_t sec, struct tm &out) {
#ifdef _WIN32
  gmtime_s(&out, &sec);
#else
  gmtime_r(&sec, &out);
#endif
}

// true when the file exists and can be opened for reading (access(R_OK), also on Windows)
inline bool readable(const std::string &path) {
  FILE *f = std::fopen(path.c_str(), "rb");
  if (!f) { return false; }
  std::fclose(f);
  return true;
}

// stdin in binary mode (Windows would otherwise translate line endings of the piped .data)
inline void binary_stdin() {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
#endif
}
