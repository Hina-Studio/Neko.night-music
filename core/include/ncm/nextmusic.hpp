#pragma once

#include <string>

#include "ncm/http.hpp"

namespace ncm {

class NextMusicClient {
public:
    NextMusicClient();

    void set_timeout(int seconds);

    std::string request(const std::string& path, const std::string& payload_json);
    std::string get_ip(bool refresh = false);

    std::string search(const std::string& keyword, int limit, int offset);
    std::string song_info(const std::string& id);
    std::string song_url(const std::string& id, const std::string& level,
                         const std::string& immerse_type = "");
    std::string song_lyric(const std::string& id);
    std::string album(const std::string& id);
    std::string playlist_trackall(const std::string& id);
    std::string artist_songs(const std::string& id, int limit, int offset);
    std::string toplist();

    static const char* base_url();

private:
    Http http_;
    std::string cached_ip_;
    int timeout_seconds_ = 30;
};

}
