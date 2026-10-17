#pragma once

#include <string>

#include "ncm/http.hpp"

namespace ncm {

class MusicJxClient {
public:
    MusicJxClient();

    void set_timeout(int seconds);

    std::string search(const std::string& input, const std::string& filter,
                       const std::string& type, int page);

    static const char* base_url();

private:
    Http http_;
    int timeout_seconds_ = 60;
};

}
