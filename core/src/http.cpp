#include "ncm/http.hpp"

#include <windows.h>
#include <winhttp.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <vector>

namespace ncm {
namespace {

std::wstring to_wide(const std::string& value) {
    if (value.empty()) {
        return {};
    }
    const int len = MultiByteToWideChar(CP_UTF8, 0, value.c_str(),
                                        static_cast<int>(value.size()), nullptr, 0);
    if (len <= 0) {
        return {};
    }
    std::wstring out(static_cast<std::size_t>(len), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()),
                        out.data(), len);
    return out;
}

std::string to_utf8(const std::wstring& value) {
    if (value.empty()) {
        return {};
    }
    const int len = WideCharToMultiByte(CP_UTF8, 0, value.c_str(),
                                        static_cast<int>(value.size()), nullptr, 0,
                                        nullptr, nullptr);
    if (len <= 0) {
        return {};
    }
    std::string out(static_cast<std::size_t>(len), '\0');
    WideCharToMultiByte(CP_UTF8, 0, value.c_str(), static_cast<int>(value.size()),
                        out.data(), len, nullptr, nullptr);
    return out;
}

std::string trim(const std::string& value) {
    const std::size_t begin = value.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
        return {};
    }
    const std::size_t end = value.find_last_not_of(" \t\r\n");
    return value.substr(begin, end - begin + 1);
}

std::string lower(std::string value) {
    for (char& c : value) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c - 'A' + 'a');
        }
    }
    return value;
}

struct UrlParts {
    bool secure = true;
    std::wstring host;
    INTERNET_PORT port = INTERNET_DEFAULT_HTTPS_PORT;
    std::wstring path;
};

UrlParts parse_url(const std::string& url) {
    UrlParts parts;
    std::string rest;
    if (url.rfind("https://", 0) == 0) {
        parts.secure = true;
        rest = url.substr(8);
    } else if (url.rfind("http://", 0) == 0) {
        parts.secure = false;
        rest = url.substr(7);
    } else {
        throw std::runtime_error("unsupported url scheme: " + url);
    }

    const std::size_t slash = rest.find('/');
    std::string host_port = slash == std::string::npos ? rest : rest.substr(0, slash);
    const std::string path = slash == std::string::npos ? "/" : rest.substr(slash);

    const std::size_t colon = host_port.rfind(':');
    if (colon != std::string::npos) {
        parts.port = static_cast<INTERNET_PORT>(
            std::atoi(host_port.substr(colon + 1).c_str()));
        host_port = host_port.substr(0, colon);
    } else {
        parts.port = parts.secure ? INTERNET_DEFAULT_HTTPS_PORT : INTERNET_DEFAULT_HTTP_PORT;
    }

    parts.host = to_wide(host_port);
    parts.path = to_wide(path);
    return parts;
}

class WinHandle {
public:
    WinHandle() = default;
    explicit WinHandle(HINTERNET handle) : handle_(handle) {}
    ~WinHandle() {
        if (handle_) {
            WinHttpCloseHandle(handle_);
        }
    }
    WinHandle(const WinHandle&) = delete;
    WinHandle& operator=(const WinHandle&) = delete;
    WinHandle(WinHandle&& other) noexcept : handle_(other.handle_) {
        other.handle_ = nullptr;
    }
    WinHandle& operator=(WinHandle&& other) noexcept {
        if (this != &other) {
            if (handle_) {
                WinHttpCloseHandle(handle_);
            }
            handle_ = other.handle_;
            other.handle_ = nullptr;
        }
        return *this;
    }
    HINTERNET get() const { return handle_; }
    explicit operator bool() const { return handle_ != nullptr; }

private:
    HINTERNET handle_ = nullptr;
};

std::string last_error_message(const char* what) {
    return std::string(what) + " failed (error " + std::to_string(GetLastError()) + ")";
}

void collect_headers(HINTERNET request, HttpResponse& response) {
    DWORD size = 0;
    WinHttpQueryHeaders(request, WINHTTP_QUERY_RAW_HEADERS_CRLF,
                        WINHTTP_HEADER_NAME_BY_INDEX, WINHTTP_NO_OUTPUT_BUFFER,
                        &size, WINHTTP_NO_HEADER_INDEX);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || size == 0) {
        return;
    }
    std::wstring raw(size / sizeof(wchar_t), L'\0');
    if (!WinHttpQueryHeaders(request, WINHTTP_QUERY_RAW_HEADERS_CRLF,
                             WINHTTP_HEADER_NAME_BY_INDEX, raw.data(), &size,
                             WINHTTP_NO_HEADER_INDEX)) {
        return;
    }
    const std::string text = to_utf8(raw.c_str());
    std::size_t pos = 0;
    bool first = true;
    while (pos < text.size()) {
        std::size_t end = text.find("\r\n", pos);
        if (end == std::string::npos) {
            break;
        }
        const std::string line = text.substr(pos, end - pos);
        pos = end + 2;
        if (first) {
            first = false;
            continue;
        }
        if (line.empty()) {
            break;
        }
        const std::size_t colon = line.find(':');
        if (colon == std::string::npos) {
            continue;
        }
        response.headers.emplace_back(lower(trim(line.substr(0, colon))),
                                      trim(line.substr(colon + 1)));
    }
}

std::string read_body(HINTERNET request) {
    std::string body;
    for (;;) {
        DWORD available = 0;
        if (!WinHttpQueryDataAvailable(request, &available)) {
            throw std::runtime_error(last_error_message("WinHttpQueryDataAvailable"));
        }
        if (available == 0) {
            break;
        }
        std::string chunk(available, '\0');
        DWORD read = 0;
        if (!WinHttpReadData(request, chunk.data(), available, &read)) {
            throw std::runtime_error(last_error_message("WinHttpReadData"));
        }
        chunk.resize(read);
        body += chunk;
    }
    return body;
}

struct RequestContext {
    WinHandle connection;
    WinHandle request;
};

RequestContext open_request(HINTERNET session, const std::string& method,
                            const std::string& url, int timeout_seconds) {
    const UrlParts parts = parse_url(url);
    RequestContext context;
    context.connection = WinHandle(WinHttpConnect(session, parts.host.c_str(),
                                                  parts.port, 0));
    if (!context.connection) {
        throw std::runtime_error(last_error_message("WinHttpConnect"));
    }
    context.request = WinHandle(WinHttpOpenRequest(
        context.connection.get(), to_wide(method).c_str(), parts.path.c_str(),
        nullptr, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
        parts.secure ? WINHTTP_FLAG_SECURE : 0));
    if (!context.request) {
        throw std::runtime_error(last_error_message("WinHttpOpenRequest"));
    }

    WinHttpSetTimeouts(context.request.get(), 10000, 10000,
                       timeout_seconds * 1000, timeout_seconds * 1000);

    DWORD decompression = WINHTTP_DECOMPRESSION_FLAG_ALL;
    WinHttpSetOption(context.request.get(), WINHTTP_OPTION_DECOMPRESSION,
                     &decompression, sizeof(decompression));
    return context;
}

void add_headers(HINTERNET request, const Http::Headers& headers) {
    for (const auto& header : headers) {
        if (header.first.empty() || header.second.empty()) {
            continue;
        }
        const std::wstring line = to_wide(header.first + ": " + header.second);
        WinHttpAddRequestHeaders(request, line.c_str(), static_cast<DWORD>(-1),
                                 WINHTTP_ADDREQ_FLAG_ADD | WINHTTP_ADDREQ_FLAG_REPLACE);
    }
}

void send_request(HINTERNET request, const std::string& body) {
    const DWORD length = static_cast<DWORD>(body.size());
    if (!WinHttpSendRequest(request, WINHTTP_NO_ADDITIONAL_HEADERS, 0,
                            body.empty() ? WINHTTP_NO_REQUEST_DATA
                                         : const_cast<char*>(body.data()),
                            length, length, 0)) {
        throw std::runtime_error(last_error_message("WinHttpSendRequest"));
    }
    if (!WinHttpReceiveResponse(request, nullptr)) {
        throw std::runtime_error(last_error_message("WinHttpReceiveResponse"));
    }
}

std::string final_url(HINTERNET request) {
    DWORD size = 0;
    WinHttpQueryOption(request, WINHTTP_OPTION_URL, nullptr, &size);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || size == 0) {
        return {};
    }
    std::wstring buffer(size / sizeof(wchar_t), L'\0');
    if (!WinHttpQueryOption(request, WINHTTP_OPTION_URL, buffer.data(), &size)) {
        return {};
    }
    return to_utf8(buffer.c_str());
}

}

Http::Http() {
    session_ = WinHttpOpen(L"ncm_core/1.0", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY,
                           WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session_) {
        throw std::runtime_error("WinHttpOpen failed");
    }
    apply_timeouts();
}

Http::~Http() {
    if (session_) {
        WinHttpCloseHandle(static_cast<HINTERNET>(session_));
    }
}

void Http::set_timeout(int seconds) {
    timeout_seconds_ = seconds > 0 ? seconds : 30;
    apply_timeouts();
}

void Http::set_user_agent(const std::string& ua) {
    user_agent_ = ua;
}

void Http::apply_timeouts() {
    WinHttpSetTimeouts(static_cast<HINTERNET>(session_), 10000, 10000,
                       timeout_seconds_ * 1000, timeout_seconds_ * 1000);
}

HttpResponse Http::request(const std::string& method, const std::string& url,
                           const Headers& headers, const std::string& body) {
    RequestContext context = open_request(static_cast<HINTERNET>(session_), method,
                                          url, timeout_seconds_);
    if (!user_agent_.empty()) {
        const std::wstring line = to_wide("User-Agent: " + user_agent_);
        WinHttpAddRequestHeaders(context.request.get(), line.c_str(),
                                 static_cast<DWORD>(-1), WINHTTP_ADDREQ_FLAG_ADD);
    }
    add_headers(context.request.get(), headers);
    send_request(context.request.get(), body);

    HttpResponse response;
    DWORD status = 0;
    DWORD size = sizeof(status);
    WinHttpQueryHeaders(context.request.get(),
                        WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                        WINHTTP_HEADER_NAME_BY_INDEX, &status, &size,
                        WINHTTP_NO_HEADER_INDEX);
    response.status = static_cast<int>(status);
    response.final_url = final_url(context.request.get());
    collect_headers(context.request.get(), response);
    response.body = read_body(context.request.get());
    return response;
}

bool Http::download(const std::string& url, const std::string& file_path,
                    const Headers& headers, const ProgressFn& progress,
                    std::string& error, bool resume) {
    const std::filesystem::path target(to_wide(file_path));
    try {
        std::uint64_t existing = 0;
        if (resume) {
            std::error_code size_error;
            if (std::filesystem::is_regular_file(target, size_error)) {
                existing = static_cast<std::uint64_t>(
                    std::filesystem::file_size(target, size_error));
            }
        }

        RequestContext context = open_request(static_cast<HINTERNET>(session_), "GET",
                                              url, timeout_seconds_);
        if (!user_agent_.empty()) {
            const std::wstring line = to_wide("User-Agent: " + user_agent_);
            WinHttpAddRequestHeaders(context.request.get(), line.c_str(),
                                     static_cast<DWORD>(-1), WINHTTP_ADDREQ_FLAG_ADD);
        }
        if (existing > 0) {
            Headers ranged = headers;
            ranged.emplace_back("Range", "bytes=" + std::to_string(existing) + "-");
            add_headers(context.request.get(), ranged);
        } else {
            add_headers(context.request.get(), headers);
        }
        send_request(context.request.get(), {});

        DWORD status = 0;
        DWORD size = sizeof(status);
        WinHttpQueryHeaders(context.request.get(),
                            WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
                            WINHTTP_HEADER_NAME_BY_INDEX, &status, &size,
                            WINHTTP_NO_HEADER_INDEX);
        const bool partial = (status == 206);
        if (status != 200 && status != 206) {
            error = "download failed with HTTP status " + std::to_string(status);
            return false;
        }

        std::uint64_t total = 0;
        DWORD content_length = 0;
        DWORD length_size = sizeof(content_length);
        if (WinHttpQueryHeaders(context.request.get(),
                                WINHTTP_QUERY_CONTENT_LENGTH | WINHTTP_QUERY_FLAG_NUMBER,
                                WINHTTP_HEADER_NAME_BY_INDEX, &content_length,
                                &length_size, WINHTTP_NO_HEADER_INDEX)) {
            total = partial ? existing + content_length : content_length;
        }

        std::ofstream out(target, std::ios::binary |
                                     (partial ? std::ios::app : std::ios::trunc));
        if (!out) {
            error = "cannot open output file: " + file_path;
            return false;
        }

        std::uint64_t received = partial ? existing : 0;
        if (progress && !progress(received, total)) {
            error = "download aborted";
            out.close();
            return false;
        }

        for (;;) {
            DWORD available = 0;
            if (!WinHttpQueryDataAvailable(context.request.get(), &available)) {
                error = last_error_message("WinHttpQueryDataAvailable");
                out.close();
                return false;
            }
            if (available == 0) {
                break;
            }
            std::string chunk(available, '\0');
            DWORD read = 0;
            if (!WinHttpReadData(context.request.get(), chunk.data(), available, &read)) {
                error = last_error_message("WinHttpReadData");
                out.close();
                return false;
            }
            out.write(chunk.data(), static_cast<std::streamsize>(read));
            received += read;
            if (progress && !progress(received, total)) {
                error = "download aborted";
                out.close();
                return false;
            }
        }
        out.close();
        return true;
    } catch (const std::exception& ex) {
        error = ex.what();
        return false;
    }
}

std::string Http::resolve_redirect(const std::string& url) {
    try {
        const HttpResponse response = request("GET", url, {}, {});
        if (!response.final_url.empty()) {
            return response.final_url;
        }
    } catch (const std::exception&) {
    }
    return {};
}

}
